import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from hydra_ontology.resolution import candidates as cand


def test_exact_name_match_scores_one():
    assert cand._name_score("Maya Patel", "maya patel") == 1.0


def test_nickname_prefix_gets_partial_credit():
    score = cand._name_score("sam", "Samira Patel")
    assert 0.0 < score < 1.0


def test_unrelated_names_score_low():
    score = cand._name_score("Elliot Zhang", "Ravi Desai")
    assert score < 0.3


def test_shared_token_boosts_score():
    score_shared = cand._name_score("Liam O'Connor", "O'Connor")
    score_unrelated = cand._name_score("Liam O'Connor", "Ravi Desai")
    assert score_shared > score_unrelated


def test_heuristic_score_adds_cooccurrence_bonus():
    a = cand.PersonNode(source="slack", source_id="sam", name="sam", role_hint=None)
    b = cand.PersonNode(source="jira", source_id="Samira Patel", name="Samira Patel", role_hint=None)
    without = cand.Candidate(a=a, b=b, name_score=0.5, cooccurs=False)
    with_cooc = cand.Candidate(a=a, b=b, name_score=0.5, cooccurs=True)
    assert with_cooc.heuristic_score > without.heuristic_score


def test_build_cooccurrence_index_links_shared_entities(monkeypatch):
    fake_rows = {
        "MATCH (p:Person)-[:AUTHORED]->(e) RETURN p.source AS source, p.source_id AS source_id, e.key AS ekey": [
            {"source": "github", "source_id": "Maya Patel", "ekey": "PR-1"}
        ],
        "MATCH (e)-[:ASSIGNED_TO]->(p:Person) RETURN p.source AS source, p.source_id AS source_id, e.key AS ekey": [
            {"source": "jira", "source_id": "M. Patel", "ekey": "SUP-1"}
        ],
        "MATCH (a)-[:MENTIONS]->(b) RETURN a.key AS a_key, b.key AS b_key": [
            {"a_key": "PR-1", "b_key": "SUP-1"}
        ],
    }

    def fake_run_query(cypher, **params):
        return fake_rows.get(cypher.strip(), [])

    monkeypatch.setattr(cand, "run_query", fake_run_query)
    index = cand._build_cooccurrence_index()
    assert "SUP-1" in index[("github", "Maya Patel")]
    assert "PR-1" in index[("jira", "M. Patel")]
