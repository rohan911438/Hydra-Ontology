# Voiceover script — read over the recorded screen capture

Matches the terminal-only walkthrough actually recorded (see
`DEMO_SCRIPT.md` for the beat-by-beat screen actions). Timestamps are
cumulative targets, not hard cuts — read at a natural pace and let the beats
drift a couple seconds either way; the whole thing should land close to
3:30. Ignore any Gemini "AFC" log lines or line-wrapping in the terminal —
don't narrate those, just talk over them.

---

**[0:00–0:10] — Title card**

> This is Hack Hydra, Track 1: turning messy, multi-source enterprise data
> into a real, queryable graph.

**[0:10–0:25] — Problem statement on screen**

> Extraction is cheap now — any LLM can turn a Slack thread into structured
> JSON. What's actually hard is deciding two mentions are the same person,
> deciding which of two contradicting claims to trust, and knowing when to
> just say "not in the data" instead of guessing.

**[0:25–0:40] — Raw Slack thread on screen**

> Here's what the raw data actually looks like — an unstructured Slack
> incident thread. No shared schema, informal names, free text. Multiply
> that by three different tools and you've got the real problem.

**[0:40–0:55] — Ontology block on screen**

> What we built turns that into a real typed schema inside HydraDB — Person,
> Ticket, Pull Request, Message, and Claim nodes, connected by relationships
> like AUTHORED, ASSIGNED TO, and RESOLVES.

**[0:55–1:05] — `docker compose ps` on screen**

> And HydraDB is running locally right now, over the Bolt protocol — this
> isn't a mockup.

**[1:05–1:10] — CLI launching**

> Let's ask it four real questions.

**[1:10–1:40] — Multi-hop query result on screen**

> First, a multi-hop question across sources: who touched the fix connected
> to what Daniel Carter reported? That runs as HydraDB's native path
> procedure — algo dot S-S-paths — walking REPORTED to ASSIGNED_TO in one
> call. Not an application loop joining three separate document stores —
> one native graph call. It finds Allison Grant.

**[1:40–2:10] — Entity-merge evidence on screen**

> Next: why were two identities merged? "Ibrahim Khan" showed up separately
> in GitHub and in Slack. The graph shows the merge decision directly —
> eighty-five percent confidence, with the model's actual reasoning attached
> as inspectable data. This isn't a hidden dedup step — it's a graph edge
> you can query.

**[2:10–2:40] — Surfaced conflict on screen**

> Third: is there disagreement about a ticket? PR-8342 is marked "done" on
> GitHub but "open" on Slack. Neither claim got deleted or overwritten —
> both live in the graph, flagged as contradicting each other, with the
> reason attached. That's the conflict resolution problem, surfaced instead
> of silently picked.

**[2:40–3:00] — Abstention on screen**

> And finally — ask about something that isn't in the graph at all. No
> path, no guess. It says "not found in the data" and stops there.

**[3:00–3:15] — Why HydraDB (if you kept that beat) or straight to close**

> A vector database can retrieve similar documents. It can't hold "merged
> with eighty-five percent confidence, for this specific reason" as a
> queryable fact, and it can't walk a bounded, typed path across three
> sources in one native call. That needs an actual graph.

**[3:15–3:30] — Close**

> Real Cypher, against a real HydraDB server, for all three of these hard
> problems — not faked in application code.

---

## Timing notes

- If you're running long, cut the "why HydraDB" beat (3:00–3:15) — the four
  live queries are the part that actually proves the pipeline works.
- If you're running short, let the pauses between queries breathe a little
  longer instead of speeding up — a beat of silence while the Cypher is
  visible reads better than rushed narration.
- Say "confidence eighty-five percent" naturally, not "point eight five" —
  it reads more like a real conversation.
