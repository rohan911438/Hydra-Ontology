"""Thin wrapper around the neo4j Bolt driver pointed at a local HydraDB server.

HydraDB's write path (empirically confirmed against a live server, since its
docs undersell how strict this is) only executes mutations through two
narrow shapes:

  1. A one-hop CREATE/MERGE pattern where every node in the pattern carries
     an integer `id` (e.g. `MERGE (a {id: 1})-[:REL]->(b {id: 2})`). A bare,
     edge-less node CREATE/MERGE is always rejected ("only one-hop edge
     patterns are executable").
  2. An `UNWIND $rows AS row ...` batch, which is the ONLY way to upsert a
     standalone node or attach a relationship between two already-existing
     nodes. Each UNWIND MATCH...CREATE/MERGE endpoint needs exactly one
     label, and both vertex and relationship MERGE patterns match on `id`
     alone (extra properties go in a following SET).

So every node here gets a stable, deterministic non-negative integer `id`
(see stable_id()), and all node/relationship writes go through upsert_node()
/ upsert_edge() below, which always take the UNWIND path -- even for a
single row, since that's the only path the write engine accepts for this
shape of write. Reads are unaffected: plain MATCH on arbitrary properties
(no id required) works fine, confirmed live.
"""
from __future__ import annotations

import hashlib
from typing import Any

from neo4j import GraphDatabase

from .config import CONFIG

# HydraDB authenticates over Bolt the same way Neo4j does: a basic-auth tuple
# where the username is the literal "neo4j" and the password is the token
# from GRAPH_AUTH_TOKEN_FILE (confirmed against hydradb's own
# scripts/runtime_smoke.sh).
_AUTH = ("neo4j", CONFIG.hydra_auth_token)

_driver = None

# Every label this project ever creates, used by wipe_graph() -- HydraDB
# rejects an unconstrained `MATCH (n)` ("node-only MATCH requires an id,
# label, or property predicate"), so wiping has to go label by label.
ALL_LABELS = ("Person", "Ticket", "PullRequest", "Message", "Repo", "Claim")


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(CONFIG.hydra_bolt_uri, auth=_AUTH)
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def run_query(cypher: str, **params: Any) -> list[dict]:
    with get_driver().session(database=CONFIG.hydra_database) as session:
        result = session.run(cypher, **params)
        return [record.data() for record in result]


def run_write(cypher: str, **params: Any) -> list[dict]:
    return run_query(cypher, **params)


def stable_id(*parts: Any) -> int:
    """Deterministic non-negative integer id for a node/relationship, derived
    from its natural key (e.g. ("person", "slack", "alex")). Same input
    always yields the same id, so re-ingesting is idempotent via MERGE."""
    s = "::".join(str(p) for p in parts)
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % (2**53)


def upsert_node(node_id: int, label: str, props: dict[str, Any]) -> None:
    """Creates or updates a single node, going through the UNWIND vertex-
    upsert path (the only mutation shape HydraDB accepts for a standalone
    node). None-valued properties are dropped -- Cypher property values must
    be int/float/bool/string, no null."""
    clean = {k: v for k, v in props.items() if v is not None}
    row = {"id": node_id, **clean}
    set_parts = [f"n:{label}"] + [f"n.{k} = row.{k}" for k in clean]
    cypher = f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET {', '.join(set_parts)}"
    run_write(cypher, rows=[row])


def upsert_edge(
    rel_type: str,
    src_label: str,
    src_id: int,
    dst_label: str,
    dst_id: int,
    edge_id: int,
    props: dict[str, Any] | None = None,
) -> None:
    """Creates or updates a relationship between two already-existing nodes.
    Also only reachable through the UNWIND path; each endpoint needs exactly
    one label, and the relationship itself needs its own stable id."""
    clean = {k: v for k, v in (props or {}).items() if v is not None}
    row = {"s": src_id, "d": dst_id, "rid": edge_id, **clean}
    set_clause = ""
    if clean:
        set_clause = " SET " + ", ".join(f"r.{k} = row.{k}" for k in clean)
    cypher = (
        f"UNWIND $rows AS row "
        f"MATCH (s:{src_label} {{id: row.s}}), (d:{dst_label} {{id: row.d}}) "
        f"MERGE (s)-[r:{rel_type} {{id: row.rid}}]->(d){set_clause}"
    )
    run_write(cypher, rows=[row])


# Confirmed live: the server's admission control rejects a single UNWIND
# batch above ~1024 rows ("client_query_batch_items rejected by admission
# control"). Chunking keeps every batch comfortably under that.
_MAX_BATCH_ROWS = 500


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def upsert_nodes_batch(label: str, rows: list[dict[str, Any]]) -> None:
    """Batched form of upsert_node(): one UNWIND round trip per chunk of
    rows instead of one round trip per row. Confirmed live: ~0.1s/row
    batched vs ~1.5s/row calling upsert_node() in a loop -- a ~15x
    difference that matters a lot once you're writing thousands of nodes.

    Every row must be a dict with an "id" key; every row carries the SAME
    set of other keys (this project's callers ensure that -- see
    write_graph.GraphBatch), because the SET clause is built once from the
    union of keys and applied to all rows uniformly."""
    if not rows:
        return
    prop_keys = sorted({k for row in rows for k in row if k != "id"})
    set_parts = [f"n:{label}"] + [f"n.{k} = row.{k}" for k in prop_keys]
    cypher = f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET {', '.join(set_parts)}"
    for chunk in _chunks(rows, _MAX_BATCH_ROWS):
        clean_rows = []
        for row in chunk:
            clean = {k: v for k, v in row.items() if v is not None}
            for k in prop_keys:
                clean.setdefault(k, "")
            clean_rows.append(clean)
        run_write(cypher, rows=clean_rows)


def upsert_edges_batch(
    rel_type: str, src_label: str, dst_label: str, rows: list[dict[str, Any]]
) -> None:
    """Batched form of upsert_edge(). Every row needs "s" (source id), "d"
    (dest id), and "rid" (the relationship's own stable id); same uniform-
    keys and chunking caveats as upsert_nodes_batch()."""
    if not rows:
        return
    prop_keys = sorted({k for row in rows for k in row if k not in ("s", "d", "rid")})
    set_clause = ""
    if prop_keys:
        set_clause = " SET " + ", ".join(f"r.{k} = row.{k}" for k in prop_keys)
    cypher = (
        f"UNWIND $rows AS row "
        f"MATCH (s:{src_label} {{id: row.s}}), (d:{dst_label} {{id: row.d}}) "
        f"MERGE (s)-[r:{rel_type} {{id: row.rid}}]->(d){set_clause}"
    )
    for chunk in _chunks(rows, _MAX_BATCH_ROWS):
        clean_rows = []
        for row in chunk:
            clean = {k: v for k, v in row.items() if v is not None}
            for k in prop_keys:
                clean.setdefault(k, "")
            clean_rows.append(clean)
        run_write(cypher, rows=clean_rows)


def verify_roundtrip() -> bool:
    """Upserts + reads back a throwaway node to prove the Bolt connection
    and write path work end to end. Deliberately doesn't delete it
    afterwards -- DETACH DELETE is expensive on this engine, the id is
    deterministic so repeat calls just re-MERGE the same node, and an
    unconstrained `MATCH (s:Smoke) DETACH DELETE s` here previously swept up
    every Smoke node from prior runs and blew the server's ~30s query
    timeout (confirmed live)."""
    tag_id = stable_id("smoke", "hydra-ontology-roundtrip")
    upsert_node(tag_id, "Smoke", {"tag": "hydra-ontology-roundtrip"})
    rows = run_query("MATCH (s:Smoke {id: $id}) RETURN s.tag AS tag LIMIT 1", id=tag_id)
    return bool(rows) and rows[0]["tag"] == "hydra-ontology-roundtrip"


def wipe_graph(batch_size: int = 25) -> None:
    """Deletes everything this project creates, label by label (HydraDB has
    no unconstrained `MATCH (n)`), in small UNWIND batches.

    DETACH DELETE is expensive on this engine -- confirmed live at roughly
    1+ second per node, so a single unbatched `MATCH (n:Label) DETACH DELETE
    n` times out on anything but a handful of nodes. Batching keeps each
    round trip small enough to land, but this is still slow for a graph of
    any real size: for a genuinely clean slate, it is far faster to reset
    the HydraDB docker volume instead of calling this (see README) --
    `docker compose down`, clear `.hydradb/store`/`.hydradb/cache`,
    `docker compose up -d`. All writes in this project are MERGE-on-stable-id
    anyway, so re-running the pipeline without wiping is already idempotent."""
    for label in ALL_LABELS:
        while True:
            rows = run_query(f"MATCH (n:{label}) RETURN n.id AS id LIMIT {batch_size}")
            if not rows:
                break
            run_write(
                "UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n",
                rows=[{"id": r["id"]} for r in rows],
            )
