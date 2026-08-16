# Demo video workflow — 3:00 hard cap

Every beat below has an explicit time budget. They add up to exactly 3:00.
Rehearse the terminal part once beforehand — the CLI `demo` command is a
single command that prints everything for beats 4-7, so the tight part is
narrating over it, not typing.

Prep before recording (do this off-camera):
- `docker compose ps` — confirm HydraDB is already up.
- Have README.md open in an editor, scrolled to the top.
- Have a terminal open in the project root, `.venv` activated.
- Optionally pre-open one raw file from each source in tabs (see beat 2) so
  you're not hunting for paths on camera.

---

## 0:00–0:20 — The problem (README, top of file)

Screen: README, "The problem" section.

Say: "Enterprise knowledge is scattered across Slack, GitHub, Jira. Any LLM
can extract structured facts from that now — that part's cheap. What's hard
is deciding two mentions are the same person, deciding which of two
contradictory claims to trust, and knowing when to just say 'not in the
data' instead of guessing."

## 0:20–0:35 — Make "messy" concrete (raw file tabs)

Screen: flip through the 3 pre-opened raw files (or `data/raw/unz/{slack,github,jira}/.../*.txt`).

Say: "No shared schema. Different name formats. Free text." (No more —
keep this to a glance at each, ~5s per file.)

## 0:35–0:50 — What was built (README, "Ontology" section)

Screen: scroll README to the ontology code block.

Say: "Real typed schema — Person, Ticket, PullRequest, Message, Claim, and
the relationships between them. Ingest and extract with Gemini, write into
HydraDB as a real graph, resolve entities, detect conflicts, then query."

## 0:50–0:55 — HydraDB is real and running (terminal)

Screen: `docker compose ps` (already run, just show the output).

Say: "HydraDB running locally over Bolt." (Don't re-run it live — paste the
already-captured output if timing is tight.)

## 0:55–2:35 — Live demo: the four showcase queries (100s, ~25s each)

Screen: terminal, run:
```
.venv/Scripts/python -m hydra_ontology.query.cli
```
then type `demo` and let it print. Narrate over each block as it appears —
don't wait for the whole thing to finish before talking.

1. **Multi-hop** ("Who touched the fix connected to what Daniel Carter
   reported?"): point at the `CALL algo.SSpaths(...)` line on screen.
   Say: "That's HydraDB's native path procedure walking REPORTED to
   ASSIGNED_TO across two sources in one call — not an app-side join."
2. **Entity-merge evidence** ("Why were the identities behind Ibrahim Khan
   merged..."): point at `confidence=0.85` and the evidence sentence.
   Say: "The merge decision is a graph edge you can inspect, not a hidden
   dedup step."
3. **Surfaced conflict** ("Is there disagreement about PR-8342?"): point at
   `slack='open' vs github='done'`.
   Say: "Neither claim got deleted or overwritten — both live in the graph,
   flagged as contradicting."
4. **Abstention** (nonexistent ticket): point at `NOT FOUND IN DATA`.
   Say: "No supporting path, so it says so instead of guessing."

## 2:35–2:50 — Why HydraDB, not a vector DB (README section)

Screen: scroll to "What HydraDB is doing here" section.

Say: "A vector DB retrieves similar documents. It can't hold '0.85
confidence, this specific reason' as a queryable fact next to the data,
and it can't walk a bounded typed path across three sources in one native
call. That needs an actual graph."

## 2:50–3:00 — Close (GitHub repo page)

Screen: repo page, commit history visible for a second.

Say: "Real Cypher, against a real HydraDB server, for all three hard
problems — not faked in application code."

---

## If you're running short on time, cut in this order

1. Beat 2 (raw file tabs) — skip entirely, mention "messy multi-source
   data" verbally instead.
2. Beat 5 (docker ps) — cut, mention HydraDB is running in beat 0.
3. Shorten beat 6 to 3 queries (drop abstention or merge-evidence, whichever
   the audience is less likely to need spelled out) — but keep multi-hop
   and surfaced-conflict, they're the two graded pillars with real live
   Cypher output.

Never cut beat 8 (why HydraDB) — it's the answer to the specific judging
question "what would be lost without it."
