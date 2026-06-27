"""Phase-3 tests for the deterministic router (src/orchestrator/orchestrator.py).

Drives route() directly with synthesized terminal events (no watcher, no LLM):
verifies prelude growth, workplan expansion, the review/e2e rework loops, the
deploy gate, evaluator healing, dead-letters at the caps, and human approvals.
"""
import pytest

from sdlcdb.db import Database
from orchestrator.orchestrator import Orchestrator
from orchestrator.evaluator import Evaluator

# A minimal but complete workplan DAG (one of each downstream agent).
WORKPLAN = {"tasks": [
    {"task_id": "A", "owner_agent": "architect-agent", "title": "arch",
     "outputs": ["artifacts/architecture.json"], "depends_on": []},
    {"task_id": "D", "owner_agent": "developer-agent", "title": "dev",
     "outputs": ["src/main.py"], "depends_on": ["A"]},
    {"task_id": "R", "owner_agent": "reviewer-agent", "title": "review",
     "outputs": ["artifacts/review_report.json"], "depends_on": ["D"]},
    {"task_id": "Q", "owner_agent": "qa-agent", "title": "qa",
     "outputs": ["artifacts/test_plan.json"], "depends_on": ["R"]},
    {"task_id": "DP", "owner_agent": "devops-agent", "title": "deploy",
     "outputs": ["artifacts/release_report.json"], "depends_on": ["R", "Q"]},
    {"task_id": "E", "owner_agent": "e2e-agent", "title": "e2e",
     "outputs": ["artifacts/e2e_report.json"], "depends_on": ["DP"]},
]}

APPROVED = {"verdict": "approved"}
REJECTED = {"verdict": "rejected", "blocking_issues": ["missing /auth/refresh"]}
TESTS_OK = {"summary": {"failed": 0}}
E2E_OK = {"verdict": "passed", "summary": {"failed": 0}}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    yield d
    d.close()


def complete(db, orch, task_id, artifacts=None, status="success", payload=None):
    """Simulate the worker finishing a task, then route its event."""
    t = db.get_task(task_id)
    for name, content in (artifacts or {}).items():
        db.put_artifact(t["pipeline_id"], name, content, producer_task_id=task_id)
    db.update_task(task_id, status="done" if status == "success" else "error",
                   payload=payload)
    eid = db.append_event(t["pipeline_id"], t["stage"], t["agent_role"], status,
                          task_id=task_id)
    orch.route({"event_id": eid, "task_id": task_id, "status": status})


def _role(db, pid, role):
    return db.list_tasks(pipeline_id=pid, agent_role=role)[0]


def _drive_to_expansion(db, orch):
    pid = orch.submit("build an app", name="app")
    prod = _role(db, pid, "product-agent")
    complete(db, orch, prod["task_id"], {"artifacts/requirements.json": {"r": 1}})
    plan = _role(db, pid, "planner-agent")
    complete(db, orch, plan["task_id"], {"artifacts/workplan.json": WORKPLAN})
    return pid


def test_prelude_grows_product_then_planner(db):
    orch = Orchestrator(db, auto_approve=True)
    pid = orch.submit("x")
    assert _role(db, pid, "product-agent")["status"] == "pending"
    complete(db, orch, _role(db, pid, "product-agent")["task_id"],
             {"artifacts/requirements.json": {"r": 1}})
    planner = _role(db, pid, "planner-agent")
    assert planner["status"] == "pending"               # planner task created
    assert planner["depends_on"] == [_role(db, pid, "product-agent")["task_id"]]


def test_workplan_expands_into_dag(db):
    orch = Orchestrator(db, auto_approve=True)
    pid = _drive_to_expansion(db, orch)
    roles = {t["agent_role"] for t in db.list_tasks(pipeline_id=pid)}
    assert {"architect-agent", "developer-agent", "reviewer-agent", "qa-agent",
            "devops-agent", "e2e-agent"} <= roles
    # depends_on got remapped to real DB ids (developer waits on architect)
    dev = _role(db, pid, "developer-agent")
    arch = _role(db, pid, "architect-agent")
    assert dev["depends_on"] == [arch["task_id"]]


def test_full_happy_path_completes(db):
    orch = Orchestrator(db, auto_approve=True)
    pid = _drive_to_expansion(db, orch)
    complete(db, orch, _role(db, pid, "architect-agent")["task_id"],
             {"artifacts/architecture.json": {"a": 1}})
    complete(db, orch, _role(db, pid, "developer-agent")["task_id"],
             {"src/main.py": "print(1)"})
    complete(db, orch, _role(db, pid, "reviewer-agent")["task_id"],
             {"artifacts/review_report.json": APPROVED})
    complete(db, orch, _role(db, pid, "qa-agent")["task_id"],
             {"artifacts/test_plan.json": TESTS_OK})
    complete(db, orch, _role(db, pid, "devops-agent")["task_id"],
             {"artifacts/release_report.json": {"verdict": "success"}})
    complete(db, orch, _role(db, pid, "e2e-agent")["task_id"],
             {"artifacts/e2e_report.json": E2E_OK})
    assert db.get_pipeline(pid)["status"] == "complete"


def test_rejected_review_resets_developer_subtree(db):
    orch = Orchestrator(db, auto_approve=True, max_rework=2)
    pid = _drive_to_expansion(db, orch)
    dev = _role(db, pid, "developer-agent")
    complete(db, orch, _role(db, pid, "architect-agent")["task_id"],
             {"artifacts/architecture.json": {"a": 1}})
    complete(db, orch, dev["task_id"], {"src/main.py": "print(1)"})
    complete(db, orch, _role(db, pid, "reviewer-agent")["task_id"],
             {"artifacts/review_report.json": REJECTED})

    dev_after = db.get_task(dev["task_id"])
    review_after = _role(db, pid, "reviewer-agent")
    assert dev_after["status"] == "pending"                      # dev re-queued
    assert "missing /auth/refresh" in dev_after["healing_prompt"]
    assert review_after["status"] == "pending"                   # review re-runs after fix
    assert review_after["heal_round"] == 1                       # rework counted


def test_rework_cap_dead_letters(db):
    orch = Orchestrator(db, auto_approve=True, max_rework=0)
    pid = _drive_to_expansion(db, orch)
    complete(db, orch, _role(db, pid, "architect-agent")["task_id"],
             {"artifacts/architecture.json": {"a": 1}})
    complete(db, orch, _role(db, pid, "developer-agent")["task_id"],
             {"src/main.py": "print(1)"})
    complete(db, orch, _role(db, pid, "reviewer-agent")["task_id"],
             {"artifacts/review_report.json": REJECTED})
    assert db.get_pipeline(pid)["status"] == "failed"            # cap 0 -> dead-letter


def test_failure_triggers_evaluator_heal(db):
    orch = Orchestrator(db, auto_approve=True,
                        evaluator=Evaluator(db, max_heal=2))
    pid = _drive_to_expansion(db, orch)
    dev = _role(db, pid, "developer-agent")
    complete(db, orch, _role(db, pid, "architect-agent")["task_id"],
             {"artifacts/architecture.json": {"a": 1}})
    complete(db, orch, dev["task_id"], status="failure",
             payload={"issues": ["build error in main.py"], "error": "build error"})

    healed = db.get_task(dev["task_id"])
    assert healed["status"] == "pending"
    assert healed["heal_round"] == 1
    assert "build error" in healed["healing_prompt"]


def test_heal_cap_dead_letters(db):
    orch = Orchestrator(db, auto_approve=True, evaluator=Evaluator(db, max_heal=0))
    pid = _drive_to_expansion(db, orch)
    dev = _role(db, pid, "developer-agent")
    complete(db, orch, dev["task_id"], status="failure",
             payload={"issues": ["fatal"], "error": "fatal"})
    assert db.get_pipeline(pid)["status"] == "failed"


def test_human_gate_holds_then_releases(db):
    orch = Orchestrator(db, auto_approve=False)               # gates active
    pid = orch.submit("x")
    complete(db, orch, _role(db, pid, "product-agent")["task_id"],
             {"artifacts/requirements.json": {"r": 1}})
    planner = _role(db, pid, "planner-agent")
    assert planner["status"] == "awaiting_approval"           # requirements gate holds
    released = orch.approve(pid, "requirements")
    assert released == 1
    assert db.get_task(planner["task_id"])["status"] == "pending"
