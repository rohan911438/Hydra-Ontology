import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from hydra_ontology.conflicts import detect


def test_bucket_recognizes_resolved_terms():
    assert detect._bucket("Done") == "resolved"
    assert detect._bucket("merged") == "resolved"
    assert detect._bucket("Closed as fixed") == "resolved"


def test_bucket_recognizes_unresolved_terms():
    assert detect._bucket("still broken") == "unresolved"
    assert detect._bucket("in_progress") == "unresolved"
    assert detect._bucket("Blocked on infra") == "unresolved"


def test_bucket_unclear_for_ambiguous_text():
    assert detect._bucket("investigating with the customer") == "unclear"


def test_detect_contradictions_flags_cross_source_disagreement(monkeypatch):
    claims_rows = [
        {"uid": "c1", "value": "Done", "note": "shipped", "source": "jira", "bucket": "resolved", "entity_key": "SUP-4127"},
        {"uid": "c2", "value": "still broken", "note": "customer reports it", "source": "slack", "bucket": "unresolved", "entity_key": "SUP-4127"},
    ]

    monkeypatch.setattr(detect, "run_query", lambda *a, **k: claims_rows)
    monkeypatch.setattr(detect, "generate_json", lambda *a, **k: {"contradicts": True, "reason": "one says fixed, other says still broken"})

    written = []
    monkeypatch.setattr(detect, "upsert_edge", lambda *a, **k: written.append((a, k)))

    count = detect.detect_contradictions(verbose=False)
    assert count == 1
    assert written[0][0][0] == "CONTRADICTS"


def test_detect_contradictions_skips_same_source_pairs(monkeypatch):
    claims_rows = [
        {"uid": "c1", "value": "Done", "note": None, "source": "jira", "bucket": "resolved", "entity_key": "SUP-1"},
        {"uid": "c2", "value": "reopened", "note": None, "source": "jira", "bucket": "unresolved", "entity_key": "SUP-1"},
    ]
    monkeypatch.setattr(detect, "run_query", lambda *a, **k: claims_rows)
    calls = []
    monkeypatch.setattr(detect, "generate_json", lambda *a, **k: calls.append(1) or {"contradicts": True, "reason": "x"})
    monkeypatch.setattr(detect, "upsert_edge", lambda *a, **k: None)

    count = detect.detect_contradictions(verbose=False)
    assert count == 0
    assert calls == []


def test_detect_contradictions_skips_same_bucket(monkeypatch):
    claims_rows = [
        {"uid": "c1", "value": "Done", "note": None, "source": "jira", "bucket": "resolved", "entity_key": "SUP-1"},
        {"uid": "c2", "value": "merged", "note": None, "source": "github", "bucket": "resolved", "entity_key": "SUP-1"},
    ]
    monkeypatch.setattr(detect, "run_query", lambda *a, **k: claims_rows)
    calls = []
    monkeypatch.setattr(detect, "generate_json", lambda *a, **k: calls.append(1) or {"contradicts": True, "reason": "x"})
    monkeypatch.setattr(detect, "upsert_edge", lambda *a, **k: None)

    count = detect.detect_contradictions(verbose=False)
    assert count == 0
    assert calls == []
