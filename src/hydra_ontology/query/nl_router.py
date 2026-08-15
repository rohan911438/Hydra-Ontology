"""Routes a natural-language question to one of the fixed query templates.

Uses one Gemini call (JSON mode) to pick a template and pull out parameters
(a ticket/PR key, a person's name). Falls back to a keyword heuristic if the
LLM is unavailable or fails, so the CLI still works against already-ingested
data without a live key.
"""
from __future__ import annotations

import re

from ..llm_client import generate_json
from . import templates as tpl

_KEY_RE = re.compile(r"\b([A-Z]{2,10}-\d{3,7}|PR-\d+)\b")

ROUTE_PROMPT = """Classify this question about an internal enterprise \
knowledge graph (people, Jira tickets, GitHub PRs, Slack threads) into ONE \
of these templates and extract parameters. Return ONLY JSON:

{{"template": "lookup" | "multi_hop" | "conflict" | "merge_evidence", \
"key": "<ticket key like SUP-4127 or PR-<number>, or null>", \
"person_name": "<a person's name/handle mentioned, or null>"}}

Guidance:
- "lookup": asks who owns/is assigned/authored something, or what its status is.
- "multi_hop": asks about a chain across sources, e.g. "who reviewed the PR that fixed the bug X reported".
- "conflict": asks if there's disagreement/contradiction about something.
- "merge_evidence": asks why two identities were merged / if two names are the same person.

Question: {question}
"""


def _keyword_fallback(question: str) -> dict:
    q = question.lower()
    key_match = _KEY_RE.search(question)
    key = key_match.group(1) if key_match else None
    if "conflict" in q or "disagree" in q or "contradict" in q:
        template = "conflict"
    elif "merged" in q or "same person" in q or "same identity" in q:
        template = "merge_evidence"
    elif "who reviewed" in q or "chain" in q or ("who" in q and " that " in q):
        template = "multi_hop"
    else:
        template = "lookup"
    words = [w.strip("?,.:;'\"") for w in question.split()]
    capitalized = [w for w in words if w[:1].isupper() and w.lower() not in ("who", "what", "is", "the")]
    person_name = capitalized[0] if capitalized else None
    return {"template": template, "key": key, "person_name": person_name}


def route(question: str) -> dict:
    try:
        raw = generate_json(ROUTE_PROMPT.format(question=question))
        if raw.get("template") in ("lookup", "multi_hop", "conflict", "merge_evidence"):
            return raw
    except Exception:  # noqa: BLE001
        pass
    return _keyword_fallback(question)


def answer(question: str) -> dict:
    routed = route(question)
    template = routed.get("template")
    key = routed.get("key")
    person_name = routed.get("person_name")

    if template == "lookup":
        if not key:
            return {"found": False, "template": template, "reason": "no ticket/PR key recognized in the question"}
        result = tpl.lookup_entity(key)
    elif template == "conflict":
        if not key:
            return {"found": False, "template": template, "reason": "no ticket/PR key recognized in the question"}
        result = tpl.conflicts_for(key)
    elif template == "multi_hop":
        if not person_name:
            return {"found": False, "template": template, "reason": "no person name recognized in the question"}
        result = tpl.who_touched_the_fix(person_name)
    elif template == "merge_evidence":
        if not person_name:
            return {"found": False, "template": template, "reason": "no person name recognized in the question"}
        result = tpl.merge_evidence(person_name)
    else:
        result = {"found": False, "reason": "could not classify the question"}

    result["template"] = template
    return result
