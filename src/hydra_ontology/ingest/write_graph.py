"""Writes document extraction results into HydraDB as real Cypher writes
over the Bolt driver.

Every node/relationship gets a stable, deterministic integer `id` (see
db.stable_id) so re-ingesting the same data is idempotent. Writes are staged
into a GraphBatch and flushed as a handful of large UNWIND calls rather than
one round trip per node/edge -- confirmed live that batching is roughly 15x
faster per row (~0.1s vs ~1.5s), and at hundreds of documents that's the
difference between a run finishing in minutes vs. hours.

Ontology (see README for the full write-up):
  (:Person {source, source_id, name, role_hint})       -- one per raw per-source identity
  (:Ticket {key, title, source, doc_id})                -- Jira
  (:PullRequest {key, title, repo, source, doc_id})     -- GitHub ("key" = "PR-<n>")
  (:Message {key, title, source, doc_id})               -- Slack thread
  (:Repo {name})

  (person)-[:AUTHORED {source, doc_id}]->(entity)
  (entity)-[:ASSIGNED_TO {source, doc_id}]->(person)
  (entity)-[:REVIEWED_BY {source, doc_id}]->(person)
  (person)-[:REPORTED {source, doc_id}]->(entity)
  (person)-[:PARTICIPATED_IN {source, doc_id}]->(entity)
  (entity)-[:RESOLVES {source, doc_id}]->(ref_entity)
  (entity)-[:BLOCKED_BY {source, doc_id}]->(ref_entity)
  (entity)-[:MENTIONS {source, doc_id}]->(ref_entity)
  (entity)-[:AUTHORED_IN]->(:Repo)                      -- PRs only, when repo is known

Conflict-relevant status claims are written as first-class Claim nodes by
conflicts/detect.py, not here -- this module only lays down the raw graph.
"""
from __future__ import annotations

import re

from ..db import stable_id, upsert_edges_batch, upsert_nodes_batch
from .extract import ExtractionResult, RelMention
from .load_dataset import DocumentRecord

_TICKET_KEY_RE = re.compile(r"^[A-Z]{2,10}-\d{3,7}$")
_PR_KEY_RE = re.compile(r"^PR-\d+$")


def _entity_label(key: str | None, fallback: str) -> str:
    if key and _TICKET_KEY_RE.match(key):
        return "Ticket"
    if key and _PR_KEY_RE.match(key):
        return "PullRequest"
    return fallback


def _entity_id(key: str) -> int:
    return stable_id("entity", key)


def _person_id(source: str, source_id: str) -> int:
    return stable_id("person", source, source_id)


class GraphBatch:
    """Accumulates node/edge writes across many documents and flushes them
    as one large UNWIND call per (label) / (rel_type, src_label, dst_label)
    bucket. Writing the same node id twice just overwrites its staged
    properties -- last write wins, which is why _merge_entity_by_key checks
    _known_keys before staging a stub, so a real document's full properties
    are never clobbered by a later stub reference (or vice versa)."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[int, dict]] = {}
        self._edges: dict[tuple[str, str, str], dict[int, dict]] = {}
        self._known_keys: dict[str, tuple[str, int]] = {}

    def add_node(self, node_id: int, label: str, props: dict) -> None:
        self._nodes.setdefault(label, {})[node_id] = props

    def add_edge(
        self, rel_type: str, src_label: str, src_id: int, dst_label: str, dst_id: int,
        edge_id: int, props: dict | None = None,
    ) -> None:
        bucket = self._edges.setdefault((rel_type, src_label, dst_label), {})
        bucket[edge_id] = {"s": src_id, "d": dst_id, **(props or {})}

    def merge_entity_by_key(self, key: str, label_hint: str) -> tuple[str, int]:
        """Ensures a (possibly stub) entity is staged for the given key.
        Returns (label, node_id). Real documents always overwrite stubs
        (see write_document) because they stage under the same id."""
        if key in self._known_keys:
            return self._known_keys[key]
        label = _entity_label(key, label_hint)
        node_id = _entity_id(key)
        self.add_node(node_id, label, {"key": key, "title": key, "source": "", "doc_id": ""})
        self._known_keys[key] = (label, node_id)
        return label, node_id

    def flush(self) -> None:
        for label, id_map in self._nodes.items():
            rows = [{"id": nid, **props} for nid, props in id_map.items()]
            upsert_nodes_batch(label, rows)
        for (rel_type, src_label, dst_label), id_map in self._edges.items():
            rows = [{"rid": rid, **data} for rid, data in id_map.items()]
            upsert_edges_batch(rel_type, src_label, dst_label, rows)
        self._nodes.clear()
        self._edges.clear()


def write_document(batch: GraphBatch, doc: DocumentRecord, extraction: ExtractionResult) -> tuple[str, str, int]:
    """Stages the document's entities/relationships into batch (call
    batch.flush() to actually write them). Returns (key, label, node_id)
    for the primary entity -- used by conflicts/detect.py to attach status
    claims."""
    source = doc["source"]
    doc_id = doc["doc_id"]
    pe = extraction.primary_entity

    # The real ticket key / PR number lives in the source filename, not
    # necessarily in the document body -- so it's not something the LLM can
    # reliably extract. Prefer the deterministic value threaded through from
    # scripts/sample_dataset.py; only fall back to the LLM's guess (or a
    # doc_id-based stub) when that's unavailable.
    natural_key = doc.get("natural_key")
    if pe.type == "PullRequest":
        key = natural_key or pe.key or f"PR-{doc_id[:8]}"
        label = "PullRequest"
    elif pe.type == "Ticket":
        key = natural_key or pe.key or doc_id
        label = "Ticket"
    else:
        key = f"MSG-{doc_id}"
        label = "Message"

    entity_id = _entity_id(key)
    # Always stage the real entity (overwrites any earlier stub for this
    # same deterministic id, regardless of processing order within a batch).
    batch.add_node(entity_id, label, {"key": key, "title": pe.title, "source": source, "doc_id": doc_id})
    batch._known_keys[key] = (label, entity_id)

    if label == "PullRequest" and pe.repo:
        repo_id = stable_id("repo", pe.repo)
        batch.add_node(repo_id, "Repo", {"name": pe.repo})
        batch.add_edge(
            "AUTHORED_IN", "PullRequest", entity_id, "Repo", repo_id,
            stable_id("rel", "AUTHORED_IN", entity_id, repo_id),
        )

    people_by_name = {p.name: p for p in extraction.people}

    def stage_person(name: str) -> int:
        role_hint = people_by_name.get(name).role_hint if name in people_by_name else None
        pid = _person_id(source, name)
        batch.add_node(pid, "Person", {"source": source, "source_id": name, "name": name, "role_hint": role_hint})
        return pid

    person_ids = {name: stage_person(name) for name in people_by_name}

    # (rel_type, person_is_source) -- direction matches the ontology docstring above.
    person_rel_directions = {
        "AUTHORED": True,
        "ASSIGNED_TO": False,
        "REVIEWED_BY": False,
        "REPORTED": True,
        "PARTICIPATED_IN": True,
    }
    entity_rel_types = {"RESOLVES", "BLOCKED_BY", "MENTIONS"}

    for rel in extraction.relationships:
        rel: RelMention
        if rel.type in person_rel_directions and rel.person:
            pid = person_ids.get(rel.person)
            if pid is None:
                pid = stage_person(rel.person)
                person_ids[rel.person] = pid
            edge_id = stable_id("rel", rel.type, entity_id, pid, doc_id)
            if person_rel_directions[rel.type]:
                batch.add_edge(rel.type, "Person", pid, label, entity_id, edge_id, {"source": source, "doc_id": doc_id})
            else:
                batch.add_edge(rel.type, label, entity_id, "Person", pid, edge_id, {"source": source, "doc_id": doc_id})
        elif rel.type in entity_rel_types and rel.ref:
            ref_label, ref_id = batch.merge_entity_by_key(rel.ref, "Ticket")
            edge_id = stable_id("rel", rel.type, entity_id, ref_id, doc_id)
            batch.add_edge(rel.type, label, entity_id, ref_label, ref_id, edge_id, {"source": source, "doc_id": doc_id})

    for ref in extraction.mentioned_refs:
        ref_label, ref_id = batch.merge_entity_by_key(ref, "Ticket")
        edge_id = stable_id("rel", "MENTIONS", entity_id, ref_id, doc_id)
        batch.add_edge("MENTIONS", label, entity_id, ref_label, ref_id, edge_id, {"source": source, "doc_id": doc_id})

    return key, label, entity_id
