"""Validate example artifacts against their JSON schemas."""
import json
import os
import pytest
import jsonschema
from jsonschema import validate, Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f)


CASES = [
    ("artifacts/requirements.example.json", "schemas/requirements.schema.json"),
    ("artifacts/workplan.example.json", "schemas/workplan.schema.json"),
    ("artifacts/architecture.example.json", "schemas/architecture.schema.json"),
    ("artifacts/test_plan.example.json", "schemas/test_plan.schema.json"),
    ("artifacts/review_report.example.json", "schemas/review_report.schema.json"),
    ("artifacts/release_report.example.json", "schemas/release_report.schema.json"),
    ("artifacts/e2e_report.example.json", "schemas/e2e_report.schema.json"),
]


@pytest.mark.parametrize("artifact_path,schema_path", CASES)
def test_artifact_validates(artifact_path, schema_path):
    artifact = load(artifact_path)
    schema = load(schema_path)
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(artifact))
    assert not errors, "\n".join(e.message for e in errors)


def test_event_schema_is_valid_json():
    schema = load("schemas/event.schema.json")
    assert schema["title"] == "WorkflowEvent"


def test_workflow_state_schema_is_valid_json():
    schema = load("schemas/workflow_state.schema.json")
    assert schema["title"] == "WorkflowState"


def test_adr_schema_is_valid_json():
    schema = load("schemas/adr.schema.json")
    assert schema["title"] == "ArchitectureDecisionRecord"


# SCH-1: architecture sibling artifacts now have real schemas (were existence-only).

@pytest.mark.parametrize("schema_path", [
    "schemas/api-contracts.schema.json",
    "schemas/data-model.schema.json",
])
def test_new_schemas_are_valid_schemas(schema_path):
    Draft202012Validator.check_schema(load(schema_path))


@pytest.mark.parametrize("artifact_path,schema_path", [
    ("projects/neural-sync/artifacts/api-contracts.json", "schemas/api-contracts.schema.json"),
    ("projects/neural-sync/artifacts/data-model.json", "schemas/data-model.schema.json"),
])
def test_real_architecture_artifacts_validate(artifact_path, schema_path):
    errors = list(Draft202012Validator(load(schema_path)).iter_errors(load(artifact_path)))
    assert not errors, "\n".join(e.message for e in errors)


def test_api_contracts_schema_rejects_non_openapi():
    schema = load("schemas/api-contracts.schema.json")
    bad = {"info": {"title": "x", "version": "1"}, "paths": {}}  # no openapi version
    assert list(Draft202012Validator(schema).iter_errors(bad))


def test_data_model_schema_rejects_untyped_fields():
    schema = load("schemas/data-model.schema.json")
    bad = {"entities": [{"name": "User", "fields": [{"name": "id"}]}]}  # field has no type
    assert list(Draft202012Validator(schema).iter_errors(bad))


def test_e2e_report_schema_is_valid_schema():
    Draft202012Validator.check_schema(load("schemas/e2e_report.schema.json"))


def test_e2e_report_schema_rejects_scenario_without_status():
    schema = load("schemas/e2e_report.schema.json")
    bad = {  # scenario is missing the required "status"
        "spec_version": "v1", "workflow_id": "wf", "base_url": "http://localhost:8080",
        "scenarios": [{"scenario_id": "E2E-1", "name": "loads"}],
        "summary": {"total": 1, "passed": 0, "failed": 0, "skipped": 1},
        "verdict": "passed", "validated_at": "2026-01-01T00:00:00Z",
    }
    assert list(Draft202012Validator(schema).iter_errors(bad))


# Stage-8 feedback loop (SPEC §3.9): backlog.json is now a schema-governed artifact.

def test_backlog_schema_is_valid_schema():
    Draft202012Validator.check_schema(load("schemas/backlog.schema.json"))


def test_backlog_schema_accepts_a_remediation_item():
    schema = load("schemas/backlog.schema.json")
    good = [{
        "id": "REMEDIATION-1", "source": "monitoring_feedback",
        "workflow_id": "wf-test", "feedback_cycle": 0, "release_verdict": "partial",
        "issues": ["health check failed: GET /"], "status": "open",
        "created_at": "2026-01-01T00:00:00Z",
    }]
    errors = list(Draft202012Validator(schema).iter_errors(good))
    assert not errors, "\n".join(e.message for e in errors)


def test_backlog_schema_rejects_unknown_status():
    schema = load("schemas/backlog.schema.json")
    bad = [{  # status not in the enum
        "id": "REMEDIATION-1", "source": "monitoring_feedback",
        "issues": ["x"], "status": "wishlist", "created_at": "2026-01-01T00:00:00Z",
    }]
    assert list(Draft202012Validator(schema).iter_errors(bad))
