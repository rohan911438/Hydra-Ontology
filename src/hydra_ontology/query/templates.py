"""Fixed Cypher query templates the CLI/NL router dispatches to.

Every template returns a dict with at least:
  - "cypher": the exact statement(s) run (for the demo to show its work)
  - "found": False when there's nothing to answer from -- the caller uses
    this to print an explicit abstention instead of an empty/misleading result

Written strictly inside HydraDB's supported Cypher subset: every RETURN
projects explicit `<binding>.<property>` columns (never a bare node), every
relationship pattern is single-type and directed, and no IN/CONTAINS/ENDS
WITH/IS NULL appear in WHERE.
"""
from __future__ import annotations

import difflib

from ..db import run_query, stable_id

# ---------------------------------------------------------------------------
# Template 1: ownership / status lookup (conflict-aware)
# ---------------------------------------------------------------------------


def lookup_entity(key: str) -> dict:
    entity_cypher = "MATCH (e {key: $key}) RETURN e.key AS key, e.title AS title, e.source AS source"
    rows = run_query(entity_cypher, key=key)
    if not rows:
        return {"found": False, "cypher": entity_cypher}

    owner_cypher = (
        "MATCH (e {key: $key}) OPTIONAL MATCH (e)-[:ASSIGNED_TO]->(a:Person) "
        "RETURN a.source AS source, a.source_id AS source_id, a.name AS name"
    )
    author_cypher = (
        "MATCH (e {key: $key}) OPTIONAL MATCH (p:Person)-[:AUTHORED]->(e) "
        "RETURN p.source AS source, p.source_id AS source_id, p.name AS name"
    )
    claims_cypher = (
        "MATCH (c:Claim)-[:ABOUT]->(e {key: $key}) "
        "RETURN c.uid AS id, c.value AS value, c.note AS note, c.source AS source, c.doc_id AS doc_id"
    )
    contradictions_cypher = (
        "MATCH (c1:Claim)-[:ABOUT]->(e {key: $key}) "
        "MATCH (c1)-[r:CONTRADICTS]->(c2:Claim) "
        "RETURN c1.value AS a_value, c1.source AS a_source, c2.value AS b_value, "
        "c2.source AS b_source, r.reason AS reason"
    )

    owners = [r for r in run_query(owner_cypher, key=key) if r.get("source_id")]
    authors = [r for r in run_query(author_cypher, key=key) if r.get("source_id")]
    claims = run_query(claims_cypher, key=key)
    contradictions = run_query(contradictions_cypher, key=key)

    return {
        "found": True,
        "entity": rows[0],
        "owners": [canonicalize_person(r) for r in owners],
        "authors": [canonicalize_person(r) for r in authors],
        "claims": claims,
        "contradictions": contradictions,
        "cypher": "\n".join([entity_cypher, owner_cypher, author_cypher, claims_cypher, contradictions_cypher]),
    }


# ---------------------------------------------------------------------------
# Person resolution helpers
# ---------------------------------------------------------------------------


def canonicalize_person(raw: dict) -> dict:
    """Given a raw {source, source_id, name} row, looks up whether it was
    merged into a canonical Person, and returns enriched info either way."""
    cypher = (
        "MATCH (c:Person {canonical: true})-[m:MERGED_FROM]->(p:Person {source: $source, source_id: $source_id}) "
        "RETURN c.name AS canonical_name, c.uid AS canonical_id, m.confidence AS confidence, m.evidence AS evidence"
    )
    rows = run_query(cypher, source=raw["source"], source_id=raw["source_id"])
    result = dict(raw)
    if rows:
        result["canonical_name"] = rows[0]["canonical_name"]
        result["canonical_id"] = rows[0]["canonical_id"]
        result["merge_confidence"] = rows[0]["confidence"]
        result["merge_evidence"] = rows[0]["evidence"]
    return result


def find_person(name_query: str) -> dict | None:
    """Fuzzy-resolves a human-typed name to the best matching raw Person node
    (source, source_id). Fetches all Person names and matches in Python since
    HydraDB's WHERE has no CONTAINS/IN -- fine at this dataset's scale."""
    rows = run_query("MATCH (p:Person) RETURN p.source AS source, p.source_id AS source_id, p.name AS name")
    if not rows:
        return None
    nq = name_query.strip().lower()
    best, best_score = None, 0.0
    for r in rows:
        # Canonical Person nodes (created by resolution) have no
        # source/source_id -- only raw per-source identities do, and those
        # are what every downstream Cypher call here is keyed on.
        if not r.get("source_id"):
            continue
        name = (r.get("name") or "").lower()
        if not name:
            continue
        if name == nq:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, name, nq).ratio()
            if name.startswith(nq):
                # The stored name begins with what was typed (e.g. query
                # "Samira" -> name "Samira Patel"): a strong, specific match.
                score = max(score, 0.95)
            elif nq.startswith(name):
                # What was typed is longer than the stored name (e.g. query
                # "Samira" against the unrelated short raw identity "sam"):
                # real, but weaker the shorter the overlap is proportionally.
                score = max(score, 0.5 + 0.3 * (len(name) / len(nq)))
        if score > best_score:
            best, best_score = r, score
    if best_score < 0.5:
        return None
    return best


# ---------------------------------------------------------------------------
# Template 2/3: multi-hop reasoning via algo.SSpaths / algo.SPpaths
# ---------------------------------------------------------------------------

_MULTI_HOP_REL_TYPES = ["REPORTED", "MENTIONS", "RESOLVES", "BLOCKED_BY", "REVIEWED_BY", "AUTHORED", "ASSIGNED_TO"]


def _is_person_node(node: dict) -> bool:
    return "source_id" in node


def who_touched_the_fix(reporter_name: str, max_len: int = 5, path_count: int = 8) -> dict:
    """"Who [authored/reviewed/was assigned] the PR/ticket connected to the
    thing <reporter_name> reported?" -- explores outward from the reporter
    with algo.SSpaths (native HydraDB path procedure) and keeps paths that
    end on a different Person than the source.

    HydraDB's SSpaths (this build) requires a raw integer `sourceNode`, not
    the sourceLabel/sourceProperty/sourceValues form its own docs mention --
    confirmed live. Its YIELD path column comes back as a flat
    [node_props, rel_type, node_props, rel_type, ...] list, not a driver Path
    object, so it's walked as plain dicts/strings here."""
    person = find_person(reporter_name)
    if person is None:
        return {"found": False, "reason": f"no person matching '{reporter_name}' in the graph"}

    source_node_id = stable_id("person", person["source"], person["source_id"])
    cypher = (
        "CALL algo.SSpaths({sourceNode: $src, "
        f"relTypes: {_MULTI_HOP_REL_TYPES!r}, relDirection: 'both', "
        f"maxLen: {max_len}, pathCount: {path_count}, resultLimit: 50"
        "}) YIELD path RETURN path"
    )
    rows = run_query(cypher, src=source_node_id)

    hits = []
    for row in rows:
        path = row["path"]
        if not path:
            continue
        nodes = path[0::2]
        hop_types = path[1::2]
        end = nodes[-1]
        if not _is_person_node(end):
            continue
        if end.get("source") == person["source"] and end.get("source_id") == person["source_id"]:
            continue
        hits.append(
            {
                "end_person": {"source": end.get("source"), "source_id": end.get("source_id"), "name": end.get("name")},
                "hop_types": hop_types,
                "node_keys": [n.get("key") or n.get("source_id") for n in nodes],
            }
        )

    if not hits:
        return {"found": False, "reason": "no connected path found", "cypher": cypher, "source_person": person}

    # Longest, most-informative path first.
    hits.sort(key=lambda h: len(h["hop_types"]), reverse=True)
    return {"found": True, "source_person": person, "hits": hits, "cypher": cypher}


# ---------------------------------------------------------------------------
# Template 4: conflict query
# ---------------------------------------------------------------------------


def conflicts_for(key: str) -> dict:
    cypher = (
        "MATCH (c1:Claim)-[:ABOUT]->(e {key: $key}) "
        "MATCH (c1)-[r:CONTRADICTS]->(c2:Claim) "
        "RETURN c1.value AS a_value, c1.source AS a_source, c1.doc_id AS a_doc_id, "
        "c2.value AS b_value, c2.source AS b_source, c2.doc_id AS b_doc_id, r.reason AS reason"
    )
    rows = run_query(cypher, key=key)
    return {"found": bool(rows), "contradictions": rows, "cypher": cypher}


# ---------------------------------------------------------------------------
# Template 5: merge evidence
# ---------------------------------------------------------------------------


def merge_evidence(name_query: str) -> dict:
    person = find_person(name_query)
    if person is None:
        return {"found": False, "reason": f"no person matching '{name_query}' in the graph"}

    merged_cypher = (
        "MATCH (c:Person {canonical: true})-[m:MERGED_FROM]->(p:Person {source: $source, source_id: $source_id}) "
        "MATCH (c)-[m2:MERGED_FROM]->(sibling:Person) "
        "RETURN c.name AS canonical_name, sibling.source AS source, sibling.source_id AS source_id, "
        "sibling.name AS name, m2.confidence AS confidence, m2.evidence AS evidence"
    )
    suggested_cypher = (
        "MATCH (p:Person {source: $source, source_id: $source_id})-[r:SUGGESTED_SAME_AS]->(other:Person) "
        "RETURN other.source AS source, other.source_id AS source_id, other.name AS name, "
        "r.confidence AS confidence, r.evidence AS evidence"
    )
    suggested_rev_cypher = (
        "MATCH (other:Person)-[r:SUGGESTED_SAME_AS]->(p:Person {source: $source, source_id: $source_id}) "
        "RETURN other.source AS source, other.source_id AS source_id, other.name AS name, "
        "r.confidence AS confidence, r.evidence AS evidence"
    )

    merged = run_query(merged_cypher, source=person["source"], source_id=person["source_id"])
    suggested = run_query(suggested_cypher, source=person["source"], source_id=person["source_id"])
    suggested += run_query(suggested_rev_cypher, source=person["source"], source_id=person["source_id"])

    found = bool(merged) or bool(suggested)
    return {
        "found": found,
        "person": person,
        "merged_cluster": merged,
        "suggested_same_as": suggested,
        "cypher": "\n".join([merged_cypher, suggested_cypher, suggested_rev_cypher]),
    }


# ---------------------------------------------------------------------------
# Demo anchor discovery -- finds real examples of each showcase category in
# whatever's actually in the graph, so the CLI demo isn't hardcoded to
# specific names/keys that may not exist in a given ingest run.
# ---------------------------------------------------------------------------


def _looks_like_person_name(name: str) -> bool:
    """Extraction occasionally mislabels a product/company/channel as a
    "person" (e.g. "LexiSearch" with a REPORTED edge). A real person's name
    as written in this corpus is virtually always "First Last" (has a
    space) or a lowercase handle; a single CamelCase word is the
    tell-tale sign of a non-person slipping through."""
    if " " in name:
        return True
    return name == name.lower() or name == name.upper()


def discover_demo_anchors() -> dict:
    reporter_candidates = run_query("MATCH (p:Person)-[:REPORTED]->(e) RETURN DISTINCT p.name AS name LIMIT 30")
    if not reporter_candidates:
        reporter_candidates = run_query("MATCH (p:Person)-[:AUTHORED]->(e) RETURN DISTINCT p.name AS name LIMIT 30")
    person_like = [r for r in reporter_candidates if _looks_like_person_name(r["name"])] or reporter_candidates
    # Prefer a candidate whose algo.SSpaths call actually finds a connected
    # multi-hop path (confirmed live: plenty exist, e.g. a REPORTED ->
    # MENTIONS -> MENTIONS -> REVIEWED_BY chain) -- picking blindly from the
    # first few graph-order results too often lands on someone with no
    # onward connections, and abstention is only the interesting demo
    # outcome when there genuinely isn't a path, not when there's one that
    # just wasn't looked for.
    reporter = None
    for cand in person_like[:15]:
        if who_touched_the_fix(cand["name"]).get("found"):
            reporter = cand
            break
    reporter = [reporter] if reporter else person_like[:1]

    merged = run_query(
        "MATCH (c:Person {canonical: true})-[:MERGED_FROM]->(p:Person) "
        "MATCH (c)-[:MERGED_FROM]->(p2:Person) "
        "WHERE p.id <> p2.id "
        "RETURN p.name AS a, p2.name AS b LIMIT 1"
    )

    conflicted = run_query(
        "MATCH (c1:Claim)-[:CONTRADICTS]->(c2:Claim) MATCH (c1)-[:ABOUT]->(e) RETURN e.key AS key LIMIT 1"
    )

    return {
        "reporter_name": reporter[0]["name"] if reporter else None,
        "merged_pair": (merged[0]["a"], merged[0]["b"]) if merged else None,
        "conflicted_key": conflicted[0]["key"] if conflicted else None,
    }
