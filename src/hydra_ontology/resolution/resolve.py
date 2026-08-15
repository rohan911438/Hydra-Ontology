"""LLM-assisted entity resolution.

For every heuristic candidate pair (see candidates.py), asks Gemini to judge
whether the two raw per-source Person nodes are the same real person, with a
confidence in [0,1] and a short reason. Then:

  - confidence >= HIGH_CONFIDENCE: the pair is unioned into a cluster. Every
    cluster of size >= 2 gets ONE canonical :Person{canonical:true} node,
    linked to each raw node via MERGED_FROM {confidence, evidence, method}.
  - LOW_CONFIDENCE <= confidence < HIGH_CONFIDENCE: written as a
    SUGGESTED_SAME_AS edge directly between the two raw nodes. Never merged.
  - confidence < LOW_CONFIDENCE: dropped, no edge at all.

This keeps every merge decision inspectable as graph data instead of a
silent, invisible dedup step.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from ..db import stable_id, upsert_edge, upsert_node
from ..llm_client import generate_json
from .candidates import Candidate, generate_candidates

HIGH_CONFIDENCE = 0.75
LOW_CONFIDENCE = 0.4

ROOT = pathlib.Path(__file__).resolve().parents[3]
CACHE_PATH = ROOT / "data" / "extracted" / "resolution_decisions.jsonl"

JUDGE_PROMPT = """You are deciding whether two identities mentioned in \
different internal systems refer to the SAME real person. Return ONLY JSON: \
{{"same_person": true|false, "confidence": <0.0-1.0>, "reason": "<one short sentence>"}}

Identity A: name="{a_name}" role_hint="{a_role}" source={a_source}
Identity B: name="{b_name}" role_hint="{b_role}" source={b_source}
They {cooc_clause} appear near each other in the same discussion/ticket thread.

Judge based on name similarity, plausibility of nicknames/initials, and role \
consistency. A short first name like "sam" matching a full name like "Samira \
Patel" is only a match if there's other supporting evidence (co-occurrence, \
matching role) -- otherwise keep confidence low/moderate, do not assume.
"""


@dataclass
class Decision:
    candidate: Candidate
    same_person: bool
    confidence: float
    reason: str


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def _node_key(source: str, source_id: str) -> str:
    return f"{source}::{source_id}"


def _person_node_id(source: str, source_id: str) -> int:
    return stable_id("person", source, source_id)


def judge_candidate(c: Candidate) -> Decision:
    cooc_clause = "do" if c.cooccurs else "do not"
    prompt = JUDGE_PROMPT.format(
        a_name=c.a.name,
        a_role=c.a.role_hint or "unknown",
        a_source=c.a.source,
        b_name=c.b.name,
        b_role=c.b.role_hint or "unknown",
        b_source=c.b.source,
        cooc_clause=cooc_clause,
    )
    raw = generate_json(prompt)
    return Decision(
        candidate=c,
        same_person=bool(raw.get("same_person")),
        confidence=float(raw.get("confidence", 0.0)),
        reason=str(raw.get("reason", "")),
    )


def _load_cache() -> dict[tuple, Decision]:
    if not CACHE_PATH.exists():
        return {}
    cache = {}
    with CACHE_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = tuple(row["pair_key"])
            cache[key] = row
    return cache


def _append_cache(pair_key: tuple, decision: Decision) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "pair_key": list(pair_key),
                    "same_person": decision.same_person,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "name_score": decision.candidate.name_score,
                    "cooccurs": decision.candidate.cooccurs,
                }
            )
            + "\n"
        )


def run_resolution(*, reresolve: bool = False, verbose: bool = True) -> list[Decision]:
    candidates = generate_candidates()
    cache = {} if reresolve else _load_cache()
    if reresolve:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text("", encoding="utf-8")

    decisions: list[Decision] = []
    for i, c in enumerate(candidates, start=1):
        pair_key = tuple(sorted([_node_key(c.a.source, c.a.source_id), _node_key(c.b.source, c.b.source_id)]))
        if pair_key in cache:
            row = cache[pair_key]
            decisions.append(
                Decision(candidate=c, same_person=row["same_person"], confidence=row["confidence"], reason=row["reason"])
            )
            continue
        try:
            decision = judge_candidate(c)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[resolve] pair {pair_key} failed: {exc}")
            continue
        decisions.append(decision)
        _append_cache(pair_key, decision)
        if verbose and i % 10 == 0:
            print(f"[resolve] {i}/{len(candidates)} judged")

    _write_decisions(decisions)
    return decisions


def _write_decisions(decisions: list[Decision]) -> None:
    uf = _UnionFind()
    high_conf_edges: dict[tuple, Decision] = {}

    for d in decisions:
        if d.confidence >= HIGH_CONFIDENCE:
            ka = _node_key(d.candidate.a.source, d.candidate.a.source_id)
            kb = _node_key(d.candidate.b.source, d.candidate.b.source_id)
            uf.union(ka, kb)
            high_conf_edges[tuple(sorted([ka, kb]))] = d
        elif LOW_CONFIDENCE <= d.confidence < HIGH_CONFIDENCE:
            aid = _person_node_id(d.candidate.a.source, d.candidate.a.source_id)
            bid = _person_node_id(d.candidate.b.source, d.candidate.b.source_id)
            upsert_edge(
                "SUGGESTED_SAME_AS", "Person", aid, "Person", bid,
                stable_id("rel", "SUGGESTED_SAME_AS", aid, bid),
                {"confidence": d.confidence, "evidence": d.reason, "method": "llm+heuristic"},
            )

    clusters: dict[str, set[str]] = {}
    for node_key in uf.parent:
        root = uf.find(node_key)
        clusters.setdefault(root, set()).add(node_key)

    node_lookup = {}
    for d in decisions:
        node_lookup[_node_key(d.candidate.a.source, d.candidate.a.source_id)] = d.candidate.a
        node_lookup[_node_key(d.candidate.b.source, d.candidate.b.source_id)] = d.candidate.b

    for root, members in clusters.items():
        if len(members) < 2:
            continue
        member_nodes = [node_lookup[m] for m in members if m in node_lookup]
        if not member_nodes:
            continue
        canonical_name = max((n.name for n in member_nodes), key=len)
        # Deterministic from the cluster's members (not a fresh uuid4 every
        # run) so re-running resolution on the same data reuses the same
        # canonical node instead of creating a duplicate each time.
        sorted_members = sorted(members)
        canonical_uid = "canonical:" + "|".join(sorted_members)
        canonical_node_id = stable_id("canonical", *sorted_members)

        upsert_node(canonical_node_id, "Person", {"canonical": True, "uid": canonical_uid, "name": canonical_name})

        for member_key in members:
            node = node_lookup.get(member_key)
            if node is None:
                continue
            best_conf, best_reason = 0.0, ""
            for pair_key, d in high_conf_edges.items():
                if member_key in pair_key:
                    if d.confidence > best_conf:
                        best_conf, best_reason = d.confidence, d.reason
            member_id = _person_node_id(node.source, node.source_id)
            upsert_edge(
                "MERGED_FROM", "Person", canonical_node_id, "Person", member_id,
                stable_id("rel", "MERGED_FROM", canonical_node_id, member_id),
                {"confidence": best_conf, "evidence": best_reason, "method": "llm+heuristic"},
            )
