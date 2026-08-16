# Hack Hydra Track 1 — Enterprise Context & Ontology

Turns messy, multi-source enterprise data (Slack, GitHub, Jira) into a
queryable ontology in [HydraDB](https://github.com/hydra-db/hydradb), then
answers questions ranging from simple lookups to multi-hop reasoning,
conflict resolution, and correct abstention — all as real Cypher queries
against a real HydraDB server, not application-side fakery.

Built for Hack Hydra (Aug 12–20, 2026), Track 1.

## The problem

Extraction is cheap now — any LLM can turn a Slack thread into structured
JSON. The hard parts are:

1. **Entity resolution** — deciding that `sam` in a Slack incident channel,
   `Samira Patel` on a Jira ticket, and `S. Patel` in a GitHub PR comment are
   (or aren't) the same person.
2. **Conflict resolution** — Jira says a ticket is "Done," a Slack thread
   from the same week says it's still broken. Don't silently pick a winner.
3. **Abstention** — if nothing in the graph supports an answer, say so.
   Never fabricate.

This project builds a pipeline that does all three, with every decision
(a merge, a contradiction, an abstention) stored as inspectable graph data,
not hidden in application logic.

## What was built

- **Ingestion**: raw Slack/GitHub/Jira documents (plain-text threads) are
  sent to Gemini once each, extracting people, the primary entity (a PR, a
  Jira ticket, or a Slack thread), relationships (`AUTHORED`, `ASSIGNED_TO`,
  `REVIEWED_BY`, `REPORTED`, `PARTICIPATED_IN`, `RESOLVES`, `BLOCKED_BY`,
  `MENTIONS`), and any status claim the document makes. Results are
  validated with pydantic and written into HydraDB as real Cypher writes,
  tagged with `source`/`doc_id` for provenance. (The ticket key / PR number
  itself comes from the source filename, not the LLM — see "Cypher subset
  note" below for why the write path is more involved than a plain `MERGE`.)
- **Entity resolution**: heuristic blocking (name similarity + graph
  co-occurrence) narrows all cross-source person pairs down to plausible
  candidates, each judged by Gemini for same-person confidence. High
  confidence → merged into one canonical `Person` node via `MERGED_FROM`
  edges (with confidence + evidence). Low confidence → linked by
  `SUGGESTED_SAME_AS` instead of being merged. Nothing is force-merged.
- **Conflict detection**: every status claim becomes a `Claim` node
  (`-[:ABOUT]->` the entity it's *actually about* — a Slack thread saying
  "SUP-4127 is still broken" attaches its claim to that ticket, not to the
  thread itself, via `status_claim.about_ref`; without this, cross-source
  claims never share an entity to disagree about in the first place, which
  is what an earlier version of this pipeline did — confirmed live: 0/543
  claims shared an entity across sources before this field existed). Claims
  from different sources that land in different status buckets (resolved
  vs. unresolved) are checked by Gemini for a genuine contradiction (not
  just normal progression); confirmed ones get a `CONTRADICTS` edge with a
  reason. Example found live in this dataset: a GitHub PR marked `done`
  while a Slack thread says it's `open` for the same `PR-8342`.
- **Query layer**: a small fixed set of Cypher templates — lookup,
  conflict-aware status, multi-hop (via HydraDB's native `algo.SSpaths`
  procedure), conflict query, and merge-evidence — dispatched to by an
  NL router (one Gemini call classifies the question + extracts parameters,
  with a keyword fallback if the LLM is unavailable).
- **CLI demo**: `python -m hydra_ontology.query.cli`, or `demo` inside it /
  `scripts/smoke.py` to run the four showcase questions non-interactively.

## What HydraDB is doing here (and what would be lost without it)

Every one of the three hard parts above is answered *as a graph query*, not
in application code:

- The multi-hop question ("who touched the fix connected to what X
  reported?") runs as `CALL algo.SSpaths(...)` — HydraDB's native bounded
  path procedure — walking `Person -[:REPORTED]-> Message -[:MENTIONS]->
  Ticket <-[:RESOLVES]- PullRequest -[:REVIEWED_BY]-> Person` across three
  ingested sources in one call. Without a graph database, this is either a
  hand-rolled BFS over an ad-hoc in-memory adjacency list (which is just
  reimplementing a graph engine badly) or an unbounded chain of application
  joins across three separate document stores.
- Entity-resolution decisions are *graph edges* (`MERGED_FROM`,
  `SUGGESTED_SAME_AS`) sitting right next to the data they're about, so
  "why were these merged?" is a one-hop query, not a lookup into a separate
  audit log that can drift out of sync with the data it's explaining.
- Conflicts are `CONTRADICTS` edges between `Claim` nodes, so "is there
  disagreement about X?" is a pattern match, and the graph can hold
  contradictory claims side by side indefinitely — a relational schema would
  need either a mutable "current status" column (which is exactly the
  silent-overwrite behavior this project refuses to do) or a bolted-on
  changelog table that isn't natively queryable alongside the entities it
  describes.
- A vector DB could retrieve *documents* that mention a ticket or a person,
  but it can't tell you that two specific identities were merged with 0.82
  confidence for a specific reason, or walk a bounded 5-hop path across
  three typed relationship chains — that requires an actual graph with typed
  edges, not similarity search over embeddings.

## Ontology

```
(:Person {source, source_id, name, role_hint})        one per raw per-source identity
(:Person {canonical:true, uid, name})                  canonical identity, only created when merged
(:Ticket {key, title, source, doc_id})                 Jira
(:PullRequest {key, title, repo, source, doc_id})       GitHub ("key" = "PR-<n>")
(:Message {key, title, source, doc_id})                 Slack thread
(:Repo {name})
(:Claim {uid, type, value, note, source, doc_id, bucket}) a status assertion about an entity

(person)-[:AUTHORED {source, doc_id}]->(entity)
(entity)-[:ASSIGNED_TO {source, doc_id}]->(person)
(entity)-[:REVIEWED_BY {source, doc_id}]->(person)
(person)-[:REPORTED {source, doc_id}]->(entity)
(person)-[:PARTICIPATED_IN {source, doc_id}]->(entity)
(entity)-[:RESOLVES {source, doc_id}]->(other_entity)
(entity)-[:BLOCKED_BY {source, doc_id}]->(other_entity)
(entity)-[:MENTIONS {source, doc_id}]->(other_entity)
(pr)-[:AUTHORED_IN]->(:Repo)

(canonical:Person)-[:MERGED_FROM {confidence, evidence, method}]->(raw:Person)   -- high-confidence merge
(raw:Person)-[:SUGGESTED_SAME_AS {confidence, evidence}]->(raw:Person)          -- low-confidence, NOT merged
(claim:Claim)-[:ABOUT]->(entity)
(claim:Claim)-[:CONTRADICTS {reason}]->(other_claim:Claim)
```

Every node also carries a stable, deterministic integer `id` (derived from
its natural key) — see "Cypher subset note" for why.

## Dataset

[EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)
(v1.0.0 release), a synthetic enterprise dataset describing one fictional
company ("Redwood Inference") across Slack/GitHub/Jira/etc. — chosen because
it has genuine, distinct Slack, GitHub, *and* Jira slices describing one
coherent company, unlike the alternative (Salesforce HERB) which has no
separate Jira source. One slice each of `github_slice_0001.zip`,
`jira_slice_0001.zip`, `slack_slice_0001.zip` (~5,000 docs/source) was
downloaded; `scripts/sample_dataset.py` then picks a coherent working subset
by prioritizing documents that genuinely cross-reference each other (a Jira
key or PR number mentioned in a Slack thread, etc.) before filling up to the
target count per source, so the resulting subset actually supports
multi-hop questions across sources rather than being a disconnected random
sample. See `NOTICE.md` for the dataset's license/attribution.

## LLM

The brief suggests the Claude API; this build uses **Gemini** instead (the
user's available key at build time). Every LLM call goes through one module,
`src/hydra_ontology/llm_client.py`, so the provider is swappable without
touching extraction/resolution/conflict/query logic. HydraDB is the graded
integration; the specific LLM vendor doing extraction is an implementation
detail.

## How to run

### 1. Start HydraDB

```bash
docker compose up -d
```

This runs `ghcr.io/hydra-db/hydradb:latest` with Bolt on `127.0.0.1:7687`,
HTTP on `8443`, admin/health on `9090`.

### 2. Set up Python

```bash
py -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env    # then fill in GEMINI_API_KEY
```

### 3. Get the dataset (already-sampled subset is committed under `data/sample/`)

To refresh from scratch:

```bash
.venv/Scripts/python scripts/download_dataset.py
.venv/Scripts/python scripts/sample_dataset.py
```

### 4. Run the whole pipeline + showcase queries

```bash
.venv/Scripts/python scripts/smoke.py
```

By default this reuses the committed LLM extraction/resolution cache under
`data/extracted/` (produced against Gemini for real during this build) so it
runs without requiring your own API key — but every HydraDB write is always
a real Cypher write against the live database, never faked. Pass
`--reextract` / `--reresolve` to re-run the live Gemini calls end to end
with your own key. Every write is a `MERGE` keyed on a stable id, so
re-running is already idempotent — `--wipe` exists but is slow on this
engine (see the Cypher subset note); for a truly clean slate, stop the
container, clear `.hydradb/store` and `.hydradb/cache`, and start it again.

### 5. Ask your own questions

```bash
.venv/Scripts/python -m hydra_ontology.query.cli
```

Type a question, or `demo` to run the four showcase questions (multi-hop,
merge evidence, a surfaced conflict, a correct abstention).

## Example run

Real output from `scripts/smoke.py` against the committed sample/cache —
every line below is a live Cypher result, not scripted:

```
=== multi-hop across sources ===
Q: Who touched the fix connected to what Daniel Carter reported?
  [template: multi_hop]
  --- Cypher run ---
  CALL algo.SSpaths({sourceNode: $src, relTypes: [...], relDirection: 'both', maxLen: 5, ...}) YIELD path RETURN path
  Source person: Daniel Carter (jira/Daniel Carter)
    -> Allison Grant (jira/Allison Grant) via REPORTED -> ASSIGNED_TO

=== entity-merge evidence ===
Q: Why were the identities behind Ibrahim Khan merged, if at all?
  [template: merge_evidence]
  Person: Ibrahim Khan (github/Ibrahim Khan)
  Merged (high-confidence) into canonical identity:
    - canonical='Ibrahim Khan' includes github/Ibrahim Khan confidence=0.85:
      The identical names and co-occurrence in the same discussion thread
      strongly suggest they are the same individual.
    - canonical='Ibrahim Khan' includes slack/Ibrahim Khan confidence=0.85: [same reason]

=== surfaced conflict ===
Q: Is there disagreement about the status of PR-8342?
  [template: conflict]
    - slack='open' vs github='done': A work item cannot be simultaneously open and done.

=== correct abstention ===
Q: What is the status of TICKET-DOES-NOT-EXIST-999?
  [template: lookup]
  => NOT FOUND IN DATA (no supporting path/entity in the graph). Abstaining rather than guessing.
```

## Cypher subset note

HydraDB implements a deliberately narrow OpenCypher subset, and the deployed
engine turned out to be considerably stricter than its own `cypher-compat.md`
suggests — confirmed by hitting real errors against a live server while
building this:

- Every relationship pattern must be single-type and directed (no undirected
  or multi-type patterns), and `RETURN` must project explicit properties
  (never a bare node).
- `CREATE`/`MERGE` only execute as **one-hop, edge-inclusive patterns** where
  every node carries an integer `id`; a bare, edge-less node write is always
  rejected ("only one-hop edge patterns are executable"). Attaching a
  relationship between two already-existing nodes, or upserting a standalone
  node at all, only works through an `UNWIND $rows AS row ...` batch — even
  for a single row. This is why every write in this project goes through
  `db.upsert_node()` / `db.upsert_edge()` rather than ad-hoc `MERGE`.
- `algo.SSpaths`'s `sourceLabel`/`sourceProperty`/`sourceValues` form
  (documented in HydraDB's own README) isn't wired up in this build; it
  needs a raw integer `sourceNode`, which is exactly what the stable `id`
  scheme above provides. Its `YIELD path` column also comes back as a flat
  `[node_props, rel_type, node_props, ...]` list, not a driver `Path` object.
- `WHERE` has no `IN`/`CONTAINS`/`ENDS WITH`/`IS NULL`; fuzzy person-name
  lookup is done by fetching and scoring in Python instead (see
  `query/templates.py::find_person`).
- `DETACH DELETE` is expensive on this engine (roughly 1+ second per node,
  confirmed live) — see `db.wipe_graph()`'s docstring for why the pipeline
  doesn't wipe by default.
- Round-tripping one `UNWIND` row at a time cost ~1.5s/row; batching many
  rows into one `UNWIND` call cut that to ~0.1s/row (~15x), which is the
  difference between the full ~550-document ingest finishing in minutes vs.
  hours. `write_graph.GraphBatch` stages every node/edge and flushes in a
  handful of large calls instead of one round trip per item — capped at 500
  rows/call, since the server's admission control rejects a single batch
  above ~1,024 rows.
- This server's local-filesystem storage backend logs a recurring
  garbage-collection failure (`PutMode::Update` unimplemented for
  `LocalFileSystem`) that, over a long write-heavy session, eventually drops
  the Bolt connection outright (`ServiceUnavailable`/`SessionExpired`) —
  unrelated to the graph data itself. `db.run_query()` closes the stale
  driver and retries with a fresh connection rather than crashing the
  pipeline mid-batch.

All of this was worked out empirically against a running HydraDB server, not
guessed from documentation — see `src/hydra_ontology/db.py` for the primitives
every other module builds on.

## Tests

```bash
.venv/Scripts/python -m pytest tests/
```

Unit tests cover extraction-schema validation, resolution heuristics
(name-similarity + co-occurrence scoring), and conflict-detection logic —
all with mocked LLM/DB calls, so they run without a live server or API key.

## Credits

- [HydraDB](https://github.com/hydra-db/hydradb) — AGPL-3.0. Used here only
  as a standalone database server over Bolt; no HydraDB source is
  redistributed.
- [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) —
  see `NOTICE.md`.
- [neo4j Python driver](https://github.com/neo4j/neo4j-python-driver) — used
  to talk to HydraDB over Bolt.
- Google Gemini API — extraction, entity-resolution judging, conflict
  classification, NL query routing.

This project's own code is MIT-licensed — see `LICENSE`.
