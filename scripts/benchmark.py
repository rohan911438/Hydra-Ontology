"""Runs the project's live query system against EnterpriseRAG-Bench's real
"info_not_found" questions and grades correct-abstention vs. fabrication.

Why only this category: EnterpriseRAG-Bench's official questions.jsonl /
extra_questions.jsonl (600 questions total) reference expected_doc_ids from
the *full* released corpus (~500K docs across 9 source types). This project
ingests a Slack/GitHub/Jira sample. A coverage check (see
data/extracted/coverage_check_notes.txt) confirmed that of the 194 questions
whose source_types are a subset of {slack, github, jira}, ZERO have their
expected_doc_ids present even after downloading and checking 100% of the
released GitHub (8,052 docs) and Jira (6,120 docs) slices plus a Slack slice
(19,171 documents total) -- the specific grounding documents those questions
need simply aren't in the released per-source-type slices we could
practically fetch. Grading those questions would not be a real test of this
system.

"info_not_found" questions are different: they're source-agnostic by
design, with empty expected_doc_ids, because the correct answer is that no
document anywhere answers them. That makes them fairly gradable against
ANY ingested subset, including ours -- and they map directly onto this
project's "correct abstention" pillar. This script is what actually ran to
produce the numbers in README.md's "Benchmark results" section; nothing in
that section is estimated or hand-typed.

Usage:
    .venv/Scripts/python scripts/benchmark.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from hydra_ontology.query import nl_router  # noqa: E402
QUESTIONS_FILE = ROOT / "data" / "raw" / "questions.jsonl"
OUTPUT_FILE = ROOT / "data" / "extracted" / "benchmark_results.jsonl"


def load_info_not_found_questions() -> list[dict]:
    rows = []
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["question_type"] == "info_not_found":
                rows.append(r)
    return rows


def grade(question: str, result: dict) -> tuple[bool, str]:
    """A correct answer to an info_not_found question is one that abstains
    -- our system's `found: False` -- rather than fabricating specific
    facts it has no grounding for. `found: True` here would mean the
    router matched an unrelated real entity in our graph and returned its
    (irrelevant) data as if it answered the question -- a fabrication
    failure mode, graded incorrect."""
    if result.get("found") is False:
        return True, "abstained (found=False): " + str(result.get("reason", ""))
    return False, "did NOT abstain -- returned data as if it answered the question"


def main() -> None:
    questions = load_info_not_found_questions()
    assert len(questions) == 20, f"expected 20 info_not_found questions, got {len(questions)}"

    results = []
    correct = 0
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['question_id']}: {q['question'][:80]}...")
        result = nl_router.answer(q["question"])
        is_correct, note = grade(q["question"], result)
        correct += is_correct
        print(f"  -> template={result.get('template')} found={result.get('found')} correct={is_correct}")
        results.append(
            {
                "question_id": q["question_id"],
                "question": q["question"],
                "routed_template": result.get("template"),
                "found": result.get("found"),
                "correct": is_correct,
                "note": note,
            }
        )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print()
    print(f"Correct abstentions: {correct}/{len(questions)}")
    print(f"Raw results written to {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
