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
