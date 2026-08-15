"""Heuristic blocking: cheaply narrows all cross-source Person pairs down to
a small set of plausible "might be the same identity" candidates before
spending an LLM call on each one.

Signals used (no ML, just stdlib):
  - normalized string similarity (difflib) between names
  - prefix/substring relationship (e.g. "sam" vs "Samira Patel")
  - shared token (e.g. a shared last name, or a shared first name)
  - co-occurrence: do the two raw Person nodes touch the same or
    directly-linked entities (e.g. both connected to the same Ticket/PR
    thread, or to two entities that reference each other), independent of
    name similarity. Computed via directed single-type queries only --
    HydraDB's Cypher subset forbids undirected and unbounded/multi-type
    relationship patterns.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from ..db import run_query

_NORM_RE = re.compile(r"[^a-z0-9\s]")


def _normalize(name: str) -> str:
    return _NORM_RE.sub("", name.lower()).strip()


@dataclass
class PersonNode:
    source: str
    source_id: str
    name: str
    role_hint: str | None


@dataclass
class Candidate:
    a: PersonNode
    b: PersonNode
    name_score: float
    cooccurs: bool

    @property
    def heuristic_score(self) -> float:
        return min(1.0, self.name_score + (0.2 if self.cooccurs else 0.0))


def fetch_people() -> list[PersonNode]:
    rows = run_query(
        "MATCH (p:Person) RETURN p.source AS source, p.source_id AS source_id, "
        "p.name AS name, p.role_hint AS role_hint"
    )
    return [PersonNode(**row) for row in rows]


def _name_score(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    prefix_bonus = 0.0
    if na.startswith(nb) or nb.startswith(na):
        prefix_bonus = 0.25
    tokens_a, tokens_b = set(na.split()), set(nb.split())
    token_overlap = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    return min(1.0, max(ratio, token_overlap) + prefix_bonus)


_PERSON_OUT_TYPES = ("AUTHORED", "REPORTED", "PARTICIPATED_IN")
_PERSON_IN_TYPES = ("ASSIGNED_TO", "REVIEWED_BY")
_ENTITY_ENTITY_TYPES = ("MENTIONS", "RESOLVES", "BLOCKED_BY")


def _person_entity_links() -> dict[tuple[str, str], set[str]]:
    """One directed, single-type MATCH per relevant relationship type (HydraDB's
    Cypher subset forbids undirected and multi-type patterns), unioned in
    Python. Returns {(source, source_id): {entity_key, ...}}."""
    links: dict[tuple[str, str], set[str]] = {}

    def add(rows: list[dict], key_field: str) -> None:
        for row in rows:
            if row.get(key_field) is None:
                continue
            links.setdefault((row["source"], row["source_id"]), set()).add(row[key_field])

    for rel in _PERSON_OUT_TYPES:
        add(
            run_query(
                f"MATCH (p:Person)-[:{rel}]->(e) RETURN p.source AS source, p.source_id AS source_id, e.key AS ekey"
            ),
            "ekey",
        )
    for rel in _PERSON_IN_TYPES:
        add(
            run_query(
                f"MATCH (e)-[:{rel}]->(p:Person) RETURN p.source AS source, p.source_id AS source_id, e.key AS ekey"
            ),
            "ekey",
        )
    return links


def _entity_adjacency() -> dict[str, set[str]]:
    """Direct (1-hop, directed) entity-to-entity links via MENTIONS/RESOLVES/
    BLOCKED_BY, treated as undirected adjacency for the co-occurrence heuristic."""
    adj: dict[str, set[str]] = {}
    for rel in _ENTITY_ENTITY_TYPES:
        rows = run_query(f"MATCH (a)-[:{rel}]->(b) RETURN a.key AS a_key, b.key AS b_key")
        for row in rows:
            ak, bk = row.get("a_key"), row.get("b_key")
            if ak is None or bk is None:
                continue
            adj.setdefault(ak, set()).add(bk)
            adj.setdefault(bk, set()).add(ak)
    return adj


def _build_cooccurrence_index() -> dict[tuple[str, str], set[str]]:
    """{(source, source_id): set of entity keys this person is within 1 hop
    of (direct), expanded one more hop through entity-entity links} -- a
    bounded, all-directed-queries proxy for "sit near each other in the
    graph" that stays inside HydraDB's supported Cypher subset."""
    person_links = _person_entity_links()
    entity_adj = _entity_adjacency()

    expanded: dict[tuple[str, str], set[str]] = {}
    for person_key, entity_keys in person_links.items():
        extra: set[str] = set()
        for ek in entity_keys:
            extra |= entity_adj.get(ek, set())
        expanded[person_key] = entity_keys | extra
    return expanded


def generate_candidates(min_name_score: float = 0.45) -> list[Candidate]:
    people = fetch_people()
    by_source: dict[str, list[PersonNode]] = {}
    for p in people:
        by_source.setdefault(p.source, []).append(p)

    sources = list(by_source)
    prelim: list[tuple[PersonNode, PersonNode, float]] = []
    for i, src_a in enumerate(sources):
        for src_b in sources[i + 1 :]:
            for a in by_source[src_a]:
                for b in by_source[src_b]:
                    score = _name_score(a.name, b.name)
                    if score >= min_name_score:
                        prelim.append((a, b, score))

    cooc_index = _build_cooccurrence_index()

    def cooccurs(a: PersonNode, b: PersonNode) -> bool:
        ea = cooc_index.get((a.source, a.source_id), set())
        eb = cooc_index.get((b.source, b.source_id), set())
        return bool(ea & eb)

    candidates = [
        Candidate(a=a, b=b, name_score=score, cooccurs=cooccurs(a, b))
        for a, b, score in prelim
    ]
    candidates.sort(key=lambda c: c.heuristic_score, reverse=True)
    return candidates
