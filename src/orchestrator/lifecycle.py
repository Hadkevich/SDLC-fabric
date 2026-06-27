"""Shared, judgment-free lifecycle constants for the DB-backed engine.

Both the worker (which stamps a task's stage on its event) and the router (which
decides the next agent) import these, so the stage/agent model has one home.
Mirrors the original engine's AGENT_STAGE/HUMAN_GATES, extended with the
post-deploy ``e2e_validation`` stage (CLAUDE.md).
"""
from __future__ import annotations

# Linear lifecycle: (stage, owning agent). `owner_agent` in a task == the agent's
# `name:` 1:1, which is also the `--agent` value the runner passes to the CLI.
LIFECYCLE = [
    ("requirement_ingestion", "product-agent"),
    ("task_decomposition", "planner-agent"),
    ("planning_architecture", "architect-agent"),
    ("code_generation", "developer-agent"),
    ("code_review", "reviewer-agent"),
    ("testing_validation", "qa-agent"),
    ("deployment", "devops-agent"),
    ("e2e_validation", "e2e-agent"),
]

STAGE_ORDER = [stage for stage, _ in LIFECYCLE]
STAGE_AGENT = {stage: agent for stage, agent in LIFECYCLE}
AGENT_STAGE = {agent: stage for stage, agent in LIFECYCLE}
AGENT_STAGE["orchestrator-agent"] = "monitoring_feedback"
AGENT_STAGE["evaluator-agent"] = "evaluation"

# Mandatory human sign-offs before a stage's tasks may run (SPEC §8.6). Keyed by
# the stage that is gated; the value is the approval token an operator records.
HUMAN_GATES = {
    "task_decomposition": "requirements",      # after product, before planner
    "code_generation": "architecture",         # after architect, before developers
    "deployment": "production_deploy",
}
ALL_GATES = set(HUMAN_GATES.values())

# Verdict vocabularies the gates check.
APPROVED_VERDICTS = {"approved", "approved_with_comments"}
E2E_PASS_VERDICTS = {"passed", "passed_with_warnings"}

# Where inter-agent JSON artifacts live (relative paths under the project root).
# Anything under this prefix is a DB-backed artifact; everything else an agent
# writes (src/…, frontend/…, tests/…) is project *code* that stays on disk.
ARTIFACT_PREFIX = "artifacts/"


def is_artifact_path(path: str) -> bool:
    """True when an output path is an inter-agent JSON artifact (DB-backed)."""
    p = str(path).replace("\\", "/")
    return p.startswith(ARTIFACT_PREFIX) and p.endswith(".json")
