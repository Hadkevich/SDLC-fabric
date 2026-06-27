"""Mechanical validation: schema checks, the security baseline, and gate helpers.

Everything here is deterministic and judgment-free (SPEC §8.2): load a schema,
validate, return errors. The Orchestrator calls these to decide whether a stage
may advance — it never asks an LLM.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

# Map an output artifact (by basename) to the schema that governs it. Outputs not
# listed are only checked for existence + JSON parseability. api-contracts.json and
# data-model.json now have structural schemas (SCH-1); per-task code specs under
# code_spec/ are resolved by prefix below.
SCHEMA_BY_NAME = {
    "requirements.json": "requirements.schema.json",
    "workplan.json": "workplan.schema.json",
    "architecture.json": "architecture.schema.json",
    "api-contracts.json": "api-contracts.schema.json",
    "data-model.json": "data-model.schema.json",
    "code_spec.json": "code_spec.schema.json",
    "test_plan.json": "test_plan.schema.json",
    "review_report.json": "review_report.schema.json",
    "release_report.json": "release_report.schema.json",
    "workflow_state.json": "workflow_state.schema.json",
}

_VALIDATOR_CACHE: dict = {}


def _validator(schemas_dir: Path, schema_file: str) -> jsonschema.Draft202012Validator:
    key = (str(schemas_dir), schema_file)
    if key not in _VALIDATOR_CACHE:
        schema = json.loads((Path(schemas_dir) / schema_file).read_text())
        _VALIDATOR_CACHE[key] = jsonschema.Draft202012Validator(schema)
    return _VALIDATOR_CACHE[key]


def schema_for_output(rel_path: str) -> str | None:
    """Return the schema filename that governs ``rel_path``, or None."""
    p = str(rel_path).replace("\\", "/")
    name = Path(p).name
    if name in SCHEMA_BY_NAME:
        return SCHEMA_BY_NAME[name]
    if ("/adr/" in ("/" + p) or p.startswith("adr/")) and name.endswith(".json"):
        return "adr.schema.json"
    # Per-task code specs (ENG-4): parallel developer tasks write task-scoped
    # files (artifacts/code_spec/<task_id>.json) instead of clobbering a single
    # artifacts/code_spec.json. They share the code_spec schema.
    if ("/code_spec/" in ("/" + p) or p.startswith("code_spec/")) and name.endswith(".json"):
        return "code_spec.schema.json"
    return None


def validate_artifact(path: Path, schema_file: str, schemas_dir: Path) -> list[str]:
    """Validate a JSON artifact against its schema. Returns [] when valid."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return [f"missing artifact: {path.name}"]
    except json.JSONDecodeError as e:
        return [f"invalid JSON in {path.name}: {e}"]
    validator = _validator(schemas_dir, schema_file)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [f"{path.name}: {e.message}" for e in errors]


# --------------------------------------------------------------- security baseline
# A small, deliberately conservative denylist with two severity tiers (SEC-2):
#   "block" — code-execution / injection / secret sinks that must never ship; a hit
#             is an unrecoverable block (SPEC §9 — no advance with a security
#             violation).
#   "warn"  — XSS-prone DOM sinks that are *often* legitimate (e.g. a framework
#             setting innerHTML). These are surfaced to the reviewer/observer but do
#             NOT auto-block, so a real app isn't hard-failed on a false positive.
_DANGEROUS = [
    (re.compile(r"\beval\s*\("), "use of eval()", "block"),
    (re.compile(r"\bnew\s+Function\s*\("), "use of new Function()", "block"),
    (re.compile(r"\bexec\s*\("), "use of exec()", "block"),
    (re.compile(r"child_process"), "shelling out via child_process", "block"),
    (re.compile(r"\bos\.system\s*\("), "os.system() shell call", "block"),
    (re.compile(r"subprocess\.(?:call|run|Popen)\s*\([^)]*shell\s*=\s*True"),
     "subprocess with shell=True", "block"),
    (re.compile(r"\bdocument\.write\s*\("), "document.write() XSS sink", "warn"),
    (re.compile(r"\.innerHTML\s*="), "innerHTML assignment (XSS sink)", "warn"),
]

# Secret detection is kept separate from the code-execution denylist above for
# two reasons: (1) the value must start with a non-whitespace char, so a string
# literal's *closing* quote followed by code — e.g. `"refresh_token=" in cookie`
# — is no longer mistaken for `token="<secret>"` (this exact false positive once
# halted a whole pipeline at the review gate); (2) it is skipped for test files,
# which legitimately hardcode fake credentials in fixtures and assertions.
_SECRET_RX = re.compile(
    r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"\s][^'\"]{7,}"
)

_SOURCE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".rb",
                ".go", ".java", ".php", ".sh", ".html", ".vue"}
_SKIP_DIRS = {"artifacts", ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def _is_test_file(rel: Path) -> bool:
    """True for test modules — fixtures there carry fake credentials by design,
    so the secret heuristic (only) is skipped for them. Dangerous-call patterns
    are still scanned everywhere."""
    name = rel.name
    return ("tests" in rel.parts or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def scan_source(project_root: Path) -> list[tuple[str, str]]:
    """Scan project source (excluding artifacts/ and vendor dirs) for dangerous
    patterns. Returns ``(issue, severity)`` tuples where severity ∈ {"block",
    "warn"} ([] when clean). The caller blocks on "block" hits and surfaces "warn"
    hits without failing the stage."""
    root = Path(project_root)
    issues: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTS:
            continue
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for rx, label, severity in _DANGEROUS:
            if rx.search(text):
                issues.append((f"{rel}: {label}", severity))
        # Secret heuristic runs on shipped source only — test fixtures hardcode
        # fake credentials by design and would otherwise block the pipeline.
        if not _is_test_file(rel) and _SECRET_RX.search(text):
            issues.append((f"{rel}: possible hard-coded secret", "block"))
    return issues
