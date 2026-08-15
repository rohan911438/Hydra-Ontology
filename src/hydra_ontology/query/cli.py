"""Minimal REPL demo. Type a question, see the Cypher HydraDB actually ran,
the result, and any surfaced conflicts/abstentions.

Usage: python -m hydra_ontology.query.cli
Type `demo` to run the four showcase questions in sequence (multi-hop,
entity-merge evidence, a surfaced conflict, and a correct abstention).
"""
from __future__ import annotations

from . import nl_router, templates


def _build_demo_questions() -> list[tuple[str, str]]:
    """Finds real examples of each showcase category in whatever's actually
    in the graph right now, rather than hardcoding names/keys that may not
    exist in a given ingest run. Abstention always uses a key that can't
    exist, so it's the one question guaranteed to demonstrate correctly."""
    anchors = templates.discover_demo_anchors()
    questions = []

    if anchors["reporter_name"]:
        questions.append(
            ("multi-hop across sources", f"Who touched the fix connected to what {anchors['reporter_name']} reported?")
        )
    else:
        questions.append(("multi-hop across sources (no reporter found in this ingest)", "Who touched the fix connected to what nobody reported?"))

    if anchors["merged_pair"]:
        a, b = anchors["merged_pair"]
        questions.append(("entity-merge evidence", f"Why were {a} and {b} merged, if at all?"))
    else:
        questions.append(("entity-merge evidence (no merge in this ingest)", "Why were X and Y merged, if at all?"))

    if anchors["conflicted_key"]:
        questions.append(("surfaced conflict", f"Is there disagreement about the status of {anchors['conflicted_key']}?"))
    else:
        questions.append(("surfaced conflict (none found in this ingest)", "Is there disagreement about the status of NONE-FOUND?"))

    questions.append(("correct abstention", "What is the status of TICKET-DOES-NOT-EXIST-999?"))
    return questions


def _print_result(result: dict) -> None:
    template = result.get("template", "?")
    print(f"  [template: {template}]")
    if "cypher" in result:
        print("  --- Cypher run ---")
        for line in result["cypher"].splitlines():
            print(f"  {line}")
    if not result.get("found"):
        reason = result.get("reason", "no supporting path/entity in the graph")
        print(f"  => NOT FOUND IN DATA ({reason}). Abstaining rather than guessing.")
        return

    if "entity" in result:
        e = result["entity"]
        print(f"  Entity: {e.get('key')} - {e.get('title')} (source: {e.get('source')})")
        if result.get("owners"):
            for o in result["owners"]:
                name = o.get("canonical_name") or o.get("name")
                print(f"  Assigned to: {name} (raw: {o['source']}/{o['source_id']})")
        if result.get("authors"):
            for a in result["authors"]:
                name = a.get("canonical_name") or a.get("name")
                print(f"  Authored by: {name} (raw: {a['source']}/{a['source_id']})")
        if result.get("claims"):
            print("  Status claims:")
            for c in result["claims"]:
                print(f"    - [{c['source']}] '{c['value']}' (doc {c['doc_id']})")
        if result.get("contradictions"):
            print("  !! CONTRADICTIONS:")
            for c in result["contradictions"]:
                print(f"    - {c['a_source']}='{c['a_value']}' vs {c['b_source']}='{c['b_value']}': {c['reason']}")

    if "hits" in result:
        sp = result["source_person"]
        print(f"  Source person: {sp.get('name')} ({sp['source']}/{sp['source_id']})")
        for h in result["hits"][:5]:
            end = h["end_person"]
            print(f"    -> {end.get('name')} ({end['source']}/{end['source_id']}) via {' -> '.join(h['hop_types'])}")

    if "contradictions" in result and "entity" not in result:
        for c in result["contradictions"]:
            print(f"    - {c['a_source']}='{c['a_value']}' vs {c['b_source']}='{c['b_value']}': {c['reason']}")

    if "merged_cluster" in result:
        p = result["person"]
        print(f"  Person: {p.get('name')} ({p['source']}/{p['source_id']})")
        if result["merged_cluster"]:
            print("  Merged (high-confidence) into canonical identity:")
            for m in result["merged_cluster"]:
                print(f"    - canonical='{m['canonical_name']}' includes {m['source']}/{m['source_id']} "
                      f"('{m['name']}') confidence={m['confidence']:.2f}: {m['evidence']}")
        if result["suggested_same_as"]:
            print("  Suggested (low-confidence, NOT merged):")
            for s in result["suggested_same_as"]:
                print(f"    - maybe same as {s['source']}/{s['source_id']} ('{s['name']}') "
                      f"confidence={s['confidence']:.2f}: {s['evidence']}")


def run_demo() -> None:
    for label, question in _build_demo_questions():
        print(f"\n=== {label} ===")
        print(f"Q: {question}")
        result = nl_router.answer(question)
        _print_result(result)


def repl() -> None:
    print("Hack Hydra Track 1 -- ask a question about the graph (or 'demo', or 'quit').")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            break
        if question.lower() == "demo":
            run_demo()
            continue
        result = nl_router.answer(question)
        _print_result(result)


if __name__ == "__main__":
    repl()
