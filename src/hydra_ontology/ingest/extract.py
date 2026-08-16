"""LLM-based entity/relationship extraction from a single raw document.

Every document (a GitHub PR thread, a Jira ticket thread, a Slack thread) is
sent to Gemini once, asking it to identify:
  - the primary entity the document is about (a PR, a Ticket, or a Slack
    thread/Message)
  - the people involved and how (author, assignee, reviewer, reporter, ...)
  - any other tickets/PRs it references (cross-source links)
  - any status claim it makes about the primary entity ("Done", "still
    broken", etc.) -- this is the raw material conflict detection works on

Results are validated with pydantic and cached to data/extracted/<source>.jsonl
so re-running the pipeline doesn't require new LLM calls unless --reextract
is passed.
"""
from __future__ import annotations

import json
import pathlib
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..llm_client import generate_json
from .load_dataset import DocumentRecord

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXTRACTED_DIR = ROOT / "data" / "extracted"

RelType = Literal[
    "AUTHORED",
    "ASSIGNED_TO",
    "REVIEWED_BY",
    "RESOLVES",
    "BLOCKED_BY",
    "REPORTED",
    "PARTICIPATED_IN",
    "MENTIONS",
]


class PersonMention(BaseModel):
    name: str
    role_hint: str | None = None


class RelMention(BaseModel):
    type: RelType
    person: str | None = None  # set for person-involving rel types
    ref: str | None = None  # ticket key / "PR-<n>" for entity-to-entity rel types


class StatusClaim(BaseModel):
    value: str
    note: str | None = None
    # Which entity this claim is about: a ticket key / "PR-<n>" if the doc
    # is asserting a status about something it REFERENCES (e.g. a Slack
    # thread saying a ticket is still broken), or null to mean the primary
    # entity itself. Without this, a Slack thread's claim only ever attaches
    # to its own Message node -- which is never the SAME entity a Jira/GitHub
    # doc's claim attaches to, making cross-source conflicts structurally
    # impossible to detect (confirmed live: 0/543 claims shared an entity
    # across sources before this field existed).
    about_ref: str | None = None


class PrimaryEntity(BaseModel):
    type: Literal["PullRequest", "Ticket", "Message"]
    key: str | None = None  # ticket key, "PR-<n>", or null for Message
    title: str
    repo: str | None = None


class ExtractionResult(BaseModel):
    primary_entity: PrimaryEntity
    people: list[PersonMention] = []
    relationships: list[RelMention] = []
    status_claim: StatusClaim | None = None
    mentioned_refs: list[str] = []


PROMPT_TEMPLATE = """You are extracting structured facts from one internal \
enterprise document (source: {source}) for a knowledge graph. Read the \
document and return ONLY a JSON object matching this shape (no prose, no \
markdown fences):

{{
  "primary_entity": {{
    "type": "PullRequest" | "Ticket" | "Message",
    "key": "<ticket key like SUP-4127, or PR-<number> for a PR, or null for a Slack thread>",
    "title": "<short title>",
    "repo": "<repo name if this is a PR/github doc, else null>"
  }},
  "people": [{{"name": "<person's name or handle as written>", "role_hint": "<e.g. author, reviewer, support, eng, oncall, reporter, or null>"}}],
  "relationships": [
    {{"type": "AUTHORED", "person": "<name>"}},
    {{"type": "ASSIGNED_TO", "person": "<name>"}},
    {{"type": "REVIEWED_BY", "person": "<name>"}},
    {{"type": "REPORTED", "person": "<name>"}},
    {{"type": "PARTICIPATED_IN", "person": "<name>"}},
    {{"type": "RESOLVES", "ref": "<ticket key or PR-<number> this document resolves/fixes>"}},
    {{"type": "BLOCKED_BY", "ref": "<ticket key or PR-<number> blocking this>"}},
    {{"type": "MENTIONS", "ref": "<any other ticket key or PR-<number> referenced>"}}
  ],
  "status_claim": {{"value": "<e.g. done, resolved, in_progress, open, blocked, still broken, or null if the doc makes no status assertion>", "note": "<short quote or paraphrase backing this, or null>", "about_ref": "<ticket key or PR-<number> this status is about, if it's about something the doc REFERENCES rather than the doc's own primary_entity -- e.g. a Slack thread saying 'SUP-4127 is still broken' -- else null>"}},
  "mentioned_refs": ["<every distinct ticket key or PR-<number> mentioned anywhere in the doc, deduplicated>"]
}}

Rules:
- Only include relationships you can actually support from the text.
- Use people's names/handles exactly as written in the document (do not normalize casing or expand nicknames).
- PR numbers must be written as "PR-<number>" (e.g. "PR-99501"). Ticket keys keep their natural form (e.g. "SUP-4127").
- If the document is a Slack thread, its primary_entity.key is null and type is "Message".
- If nothing supports a status_claim, set it to null.
- status_claim.about_ref matters most for Slack threads: if the discussion asserts a status about a specific ticket/PR it references (not just the thread itself), set about_ref to that ticket key / PR-<number> so the claim attaches to the right entity. Leave it null when the claim is genuinely about the document's own primary entity (typical for Jira/GitHub docs).

Document title: {title}
Document body:
{body}
"""


def build_prompt(doc: DocumentRecord) -> str:
    body = doc["body"]
    if len(body) > 6000:
        body = body[:6000] + "\n...[truncated]"
    return PROMPT_TEMPLATE.format(
        source=doc["source"], title=doc["title"], body=body
    )


def extract_document(doc: DocumentRecord) -> ExtractionResult:
    raw = generate_json(build_prompt(doc))
    return ExtractionResult.model_validate(raw)


def _cache_path(source: str) -> pathlib.Path:
    return EXTRACTED_DIR / f"{source}.jsonl"


def load_cached(source: str) -> dict[str, ExtractionResult]:
    path = _cache_path(source)
    if not path.exists():
        return {}
    cached = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            try:
                cached[row["doc_id"]] = ExtractionResult.model_validate(row["extraction"])
            except ValidationError:
                continue
    return cached


def append_cache(source: str, doc_id: str, result: ExtractionResult) -> None:
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(source)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": doc_id, "extraction": result.model_dump()}) + "\n")


def extract_source(
    source: str, docs: list[DocumentRecord], *, reextract: bool = False, verbose: bool = True
) -> dict[str, ExtractionResult]:
    """Returns {doc_id: ExtractionResult} for every doc, using the cache unless
    reextract=True, and appending newly-extracted results to the cache."""
    cached = {} if reextract else load_cached(source)
    if reextract:
        # Start a fresh cache file for this source.
        EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(source).write_text("", encoding="utf-8")

    results: dict[str, ExtractionResult] = {}
    for i, doc in enumerate(docs, start=1):
        doc_id = doc["doc_id"]
        if doc_id in cached:
            results[doc_id] = cached[doc_id]
            continue
        try:
            result = extract_document(doc)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[extract:{source}] {doc_id} failed: {exc}")
            continue
        results[doc_id] = result
        append_cache(source, doc_id, result)
        if verbose and i % 25 == 0:
            print(f"[extract:{source}] {i}/{len(docs)} done")
    return results
