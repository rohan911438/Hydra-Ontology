# Demo video outline

Target length: ~4-5 minutes.

## 1. The problem (30-45s)

- State it directly: enterprise knowledge is scattered across Slack, GitHub,
  Jira. Extraction is cheap now (any LLM does it). The hard parts are entity
  resolution, conflict resolution, and knowing when to say "I don't know."
- Show the three raw source formats side by side (a Slack thread, a GitHub
  PR thread, a Jira ticket) to make "messy, multi-source" concrete before
  showing anything structured.

## 2. What was built (30s)

- One sentence per stage: ingest+extract (Gemini) -> write into HydraDB as a
  real graph -> resolve entities -> detect conflicts -> query.
- Show the ontology diagram from the README for 3-5 seconds — just enough to
  establish there's a real typed schema, not a blob store.

## 3. Live demo — run `python scripts/smoke.py` or `python -m hydra_ontology.query.cli` then `demo` (2.5-3 min)

Walk through all four showcase questions, narrating what's happening at each:

1. **Multi-hop across sources**: "Who touched the fix connected to what
   [person] reported in Slack?" — call out that this runs as
   `CALL algo.SSpaths(...)`, HydraDB's native path procedure, not an
   application-side loop joining three separate document stores. Point at
   the printed Cypher and the hop chain in the output.
2. **Entity-merge evidence**: ask why two raw identities were (or weren't)
   merged. Show the `MERGED_FROM` edge with its confidence + evidence, and
   contrast with a `SUGGESTED_SAME_AS` case that was deliberately *not*
   force-merged — make the point that low-confidence matches stay visible
   and separate instead of silently collapsing two different people.
3. **Surfaced conflict**: ask about a ticket with disagreeing status claims
   across sources. Show both `Claim` nodes and the `CONTRADICTS` edge with
   its reason — emphasize neither claim was deleted or silently overwritten.
4. **Correct abstention**: ask about something not in the graph. Show the
   CLI printing "NOT FOUND IN DATA" instead of guessing.

## 4. Why HydraDB, not a vector DB (30-45s)

- A vector DB retrieves documents that are *similar* to a query. It can't
  represent "these two identities were merged with 0.82 confidence for this
  specific reason" as a first-class, queryable fact sitting next to the data
  it's about, and it can't walk a bounded, typed 5-hop path across three
  sources in one native call the way `algo.SSpaths` does here.
- Point at the `CONTRADICTS` and `MERGED_FROM` edges again: these are
  *decisions*, stored as graph structure, not text a vector search would
  need to re-interpret every time.

## 5. Close (10-15s)

- Recap: real Cypher, against a real HydraDB server, for every one of the
  three hard problems — not faked in application code.
