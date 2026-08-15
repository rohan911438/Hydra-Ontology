"""Loads the sampled JSONL documents produced by scripts/sample_dataset.py."""
from __future__ import annotations

import json
import pathlib
from typing import Iterator, TypedDict

ROOT = pathlib.Path(__file__).resolve().parents[3]
SAMPLE_DIR = ROOT / "data" / "sample"

SOURCES = ("github", "jira", "slack")


class DocumentRecord(TypedDict):
    source: str
    doc_id: str
    slug: str
    title: str
    body: str
    path: str
    natural_key: str | None


def load_documents(source: str) -> list[DocumentRecord]:
    path = SAMPLE_DIR / f"{source}.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run scripts/sample_dataset.py first "
            "(after scripts/download_dataset.py)."
        )
    docs = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def iter_all_documents() -> Iterator[DocumentRecord]:
    for source in SOURCES:
        yield from load_documents(source)
