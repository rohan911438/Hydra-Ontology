"""Builds a coherent, manageable subset of the raw GitHub/Jira/Slack slices.

Rather than sampling randomly, this prioritizes documents that genuinely
cross-reference each other (a Jira ticket key or GitHub PR number mentioned
in a Slack thread, etc.) so the resulting subset actually supports multi-hop
questions across sources, then fills up to the target count per source with
the remaining documents (sorted by doc_id for determinism).

Output: data/sample/{github,jira,slack}.jsonl, one JSON object per line:
{"source", "doc_id", "title", "body", "path"}

Usage: python scripts/sample_dataset.py [--github N] [--jira N] [--slack N]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "unz"
SAMPLE_DIR = ROOT / "data" / "sample"

TICKET_KEY_RE = re.compile(r"\b([A-Z]{2,10}-\d{3,7})\b")
PR_NUM_RE = re.compile(r"\bpr-?(\d{2,7})\b", re.IGNORECASE)
FILENAME_RE = re.compile(r"^dsid_([0-9a-f]{32})__(.+)\.txt$")


def load_source(source: str) -> list[dict]:
    src_dir = RAW_DIR / source / source
    docs = []
    for path in sorted(src_dir.glob("*.txt")):
        m = FILENAME_RE.match(path.name)
        if not m:
            continue
        doc_id, slug = m.groups()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        title = lines[0].strip() if lines else slug
        # The ticket key / PR number lives in the filename slug, not
        # necessarily in the document body -- extracted once here so
        # downstream code doesn't have to rely on an LLM inventing one.
        if source == "jira":
            m2 = TICKET_KEY_RE.search(slug)
            natural_key = m2.group(1) if m2 else None
        elif source == "github":
            m2 = PR_NUM_RE.search(slug)
            natural_key = f"PR-{m2.group(1)}" if m2 else None
        else:
            natural_key = None
        docs.append(
            {
                "source": source,
                "doc_id": doc_id,
                "slug": slug,
                "title": title,
                "body": text,
                "path": str(path.relative_to(ROOT)),
                "natural_key": natural_key,
            }
        )
    return docs


def primary_ticket_key(doc: dict) -> str | None:
    m = TICKET_KEY_RE.search(doc["slug"])
    return m.group(1) if m else None


def primary_pr_number(doc: dict) -> str | None:
    m = PR_NUM_RE.search(doc["slug"])
    return m.group(1) if m else None


def select(source_docs: dict[str, list[dict]], targets: dict[str, int]) -> dict[str, list[dict]]:
    jira_by_key = {primary_ticket_key(d): d for d in source_docs["jira"] if primary_ticket_key(d)}
    github_by_pr = {primary_pr_number(d): d for d in source_docs["github"] if primary_pr_number(d)}

    # Which slack docs mention a jira key or PR number we actually have?
    linked_slack, linked_jira_keys, linked_pr_nums = [], set(), set()
    for doc in source_docs["slack"]:
        keys = set(TICKET_KEY_RE.findall(doc["body"])) & jira_by_key.keys()
        prs = set(PR_NUM_RE.findall(doc["body"])) & github_by_pr.keys()
        if keys or prs:
            linked_slack.append(doc)
            linked_jira_keys |= keys
            linked_pr_nums |= prs

    selected = {"github": [], "jira": [], "slack": []}
    seen = {"github": set(), "jira": set(), "slack": set()}

    def add(source: str, doc: dict, cap: int) -> None:
        if doc["doc_id"] in seen[source] or len(selected[source]) >= cap:
            return
        seen[source].add(doc["doc_id"])
        selected[source].append(doc)

    # Seed with cross-referenced documents first (the coherent core).
    for key in linked_jira_keys:
        add("jira", jira_by_key[key], targets["jira"])
    for pr in linked_pr_nums:
        add("github", github_by_pr[pr], targets["github"])
    for doc in linked_slack:
        add("slack", doc, targets["slack"])

    # Fill remaining budget deterministically.
    for source in ("github", "jira", "slack"):
        for doc in source_docs[source]:
            add(source, doc, targets[source])

    return selected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", type=int, default=150)
    ap.add_argument("--jira", type=int, default=150)
    ap.add_argument("--slack", type=int, default=250)
    args = ap.parse_args()

    targets = {"github": args.github, "jira": args.jira, "slack": args.slack}
    source_docs = {s: load_source(s) for s in ("github", "jira", "slack")}
    for s, docs in source_docs.items():
        print(f"[{s}] loaded {len(docs)} raw docs")

    selected = select(source_docs, targets)

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    for source, docs in selected.items():
        out_path = SAMPLE_DIR / f"{source}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for doc in docs:
                f.write(json.dumps(doc) + "\n")
        print(f"[{source}] wrote {len(docs)} docs -> {out_path}")


if __name__ == "__main__":
    main()
