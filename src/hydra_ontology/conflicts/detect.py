"""Conflict detection: turns per-document status claims into first-class
Claim nodes, then flags genuine contradictions between sources instead of
silently picking a winner.

Pipeline:
  1. Every document with a status_claim (see ingest/extract.py) gets a
     (:Claim {value, note, source, doc_id, bucket})-[:ABOUT]->(entity) node.
     Nothing is overwritten -- an entity can end up with many Claims.
  2. Claims about the same entity are bucketed heuristically into
     resolved / unresolved / unclear.
  3. When two claims from DIFFERENT sources land in different non-unclear
     buckets, Gemini is asked to confirm it's a real contradiction (not just
     normal status progression) and gives a one-line reason. Confirmed pairs
     get a (:Claim)-[:CONTRADICTS {reason}]->(:Claim) edge.
"""
from __future__ import annotations

import itertools

from ..db import run_query, stable_id, upsert_edge
from ..llm_client import generate_json
from ..ingest.extract import ExtractionResult
from ..ingest.load_dataset import DocumentRecord
from ..ingest.write_graph import GraphBatch

_RESOLVED_KEYWORDS = ("done", "resolved", "closed", "fixed", "merged", "completed", "shipped")
_UNRESOLVED_KEYWORDS = (
    "open", "in_progress", "in progress", "blocked", "broken", "reopened",
    "still broken", "failing", "wip", "pending", "unresolved",
)


def _bucket(value: str) -> str:
    v = value.lower()
    if any(k in v for k in _RESOLVED_KEYWORDS):
        return "resolved"
    if any(k in v for k in _UNRESOLVED_KEYWORDS):
        return "unresolved"
    return "unclear"


def write_status_claims(
    batch: GraphBatch, doc: DocumentRecord, extraction: ExtractionResult, entity_label: str, entity_id: int
) -> None:
    if extraction.status_claim is None:
        return
    # Deterministic from (source, doc_id, entity) -- a document makes at most
    # one status claim about its primary entity -- so re-ingesting the same
    # document doesn't create a duplicate Claim node each run.
    claim_uid = f"claim:{doc['source']}:{doc['doc_id']}:{entity_id}"
    claim_id = stable_id("claim", claim_uid)
    batch.add_node(
        claim_id,
        "Claim",
        {
            "uid": claim_uid,
            "type": "status",
            "value": extraction.status_claim.value,
            "note": extraction.status_claim.note,
            "source": doc["source"],
            "doc_id": doc["doc_id"],
            "bucket": _bucket(extraction.status_claim.value),
        },
    )
    batch.add_edge("ABOUT", "Claim", claim_id, entity_label, entity_id, stable_id("rel", "ABOUT", claim_id, entity_id))


CONFIRM_PROMPT = """Two different internal sources made status claims about \
the same work item. Decide if these are a genuine CONTRADICTION (both can't \
be true at the same time) or just normal progression / compatible framing. \
Return ONLY JSON: {{"contradicts": true|false, "reason": "<one short sentence>"}}

Claim A (from {a_source}): "{a_value}" -- context: {a_note}
Claim B (from {b_source}): "{b_value}" -- context: {b_note}
"""


def detect_contradictions(verbose: bool = True) -> int:
    rows = run_query(
        """
        MATCH (c:Claim)-[:ABOUT]->(e)
        RETURN c.uid AS uid, c.value AS value, c.note AS note, c.source AS source,
               c.bucket AS bucket, e.key AS entity_key
        """
    )
    by_entity: dict[str, list[dict]] = {}
    for row in rows:
        by_entity.setdefault(row["entity_key"], []).append(row)

    written = 0
    for entity_key, claims in by_entity.items():
        if len(claims) < 2:
            continue
        for a, b in itertools.combinations(claims, 2):
            if a["source"] == b["source"]:
                continue
            if a["bucket"] == "unclear" or b["bucket"] == "unclear":
                continue
            if a["bucket"] == b["bucket"]:
                continue
            try:
                raw = generate_json(
                    CONFIRM_PROMPT.format(
                        a_source=a["source"], a_value=a["value"], a_note=a["note"] or "n/a",
                        b_source=b["source"], b_value=b["value"], b_note=b["note"] or "n/a",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                if verbose:
                    print(f"[conflicts] judge failed for {entity_key}: {exc}")
                continue
            if not raw.get("contradicts"):
                continue
            a_id, b_id = stable_id("claim", a["uid"]), stable_id("claim", b["uid"])
            upsert_edge(
                "CONTRADICTS", "Claim", a_id, "Claim", b_id,
                stable_id("rel", "CONTRADICTS", a_id, b_id),
                {"reason": raw.get("reason", "")},
            )
            written += 1
            if verbose:
                print(f"[conflicts] {entity_key}: {a['source']}='{a['value']}' vs {b['source']}='{b['value']}' -> CONTRADICTS")
    return written
