import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import pytest
from pydantic import ValidationError

from hydra_ontology.ingest.extract import ExtractionResult


def test_valid_pr_extraction_parses():
    raw = {
        "primary_entity": {"type": "PullRequest", "key": "PR-99501", "title": "Add KMS guardrails", "repo": "core-services"},
        "people": [{"name": "Maya Patel", "role_hint": "author"}, {"name": "Elliot Zhang", "role_hint": "reviewer"}],
        "relationships": [
            {"type": "AUTHORED", "person": "Maya Patel"},
            {"type": "REVIEWED_BY", "person": "Elliot Zhang"},
            {"type": "RESOLVES", "ref": "SEC-1471"},
        ],
        "status_claim": {"value": "merged", "note": "squash and merge"},
        "mentioned_refs": ["SEC-1471", "ENG-8842"],
    }
    result = ExtractionResult.model_validate(raw)
    assert result.primary_entity.key == "PR-99501"
    assert len(result.people) == 2
    assert result.relationships[0].type == "AUTHORED"
    assert result.status_claim.value == "merged"


def test_slack_thread_has_no_key():
    raw = {
        "primary_entity": {"type": "Message", "key": None, "title": "support incident", "repo": None},
        "people": [],
        "relationships": [],
        "status_claim": None,
        "mentioned_refs": [],
    }
    result = ExtractionResult.model_validate(raw)
    assert result.primary_entity.key is None
    assert result.primary_entity.type == "Message"


def test_invalid_relationship_type_rejected():
    raw = {
        "primary_entity": {"type": "Ticket", "key": "SUP-1", "title": "t", "repo": None},
        "people": [],
        "relationships": [{"type": "NOT_A_REAL_TYPE", "person": "X"}],
        "status_claim": None,
        "mentioned_refs": [],
    }
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(raw)


def test_invalid_primary_entity_type_rejected():
    raw = {
        "primary_entity": {"type": "Email", "key": None, "title": "t", "repo": None},
        "people": [],
        "relationships": [],
        "status_claim": None,
        "mentioned_refs": [],
    }
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(raw)


def test_defaults_are_empty_not_missing():
    raw = {"primary_entity": {"type": "Ticket", "key": "SUP-2", "title": "t", "repo": None}}
    result = ExtractionResult.model_validate(raw)
    assert result.people == []
    assert result.relationships == []
    assert result.mentioned_refs == []
    assert result.status_claim is None
