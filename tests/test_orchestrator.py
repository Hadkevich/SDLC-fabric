"""Tests for the deterministic orchestration engine.

These exercise the control plane with fake runners — no LLM — covering the SPEC §8
contract: DAG scheduling, mechanical gates, retry vs. escalate, idempotent resume,
machine-stamped events, human checkpoints, and the security baseline. The
fault-injection tests satisfy the success criterion "recovers from >=2 injected
failures via retry/escalation".
"""
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator import Orchestrator, ReplayRunner, CallableRunner
from orchestrator.engine import Escalation
from orchestrator.runners import RecoverableError, UnrecoverableError


# --------------------------------------------------------------------------- fixtures

def _task(tid, agent, outputs, deps):
    return {
        "task_id": tid, "title": tid, "owner_agent": agent,
        "inputs": [], "outputs": outputs, "depends_on": deps,
        "done_criteria": ["done"],
    }


# DAG: dev -> qa -> reviewer -> devops  (linear, exercises every gate)
DEFAULT_TASKS = [
    _task("T-DEV", "developer-agent", ["src/app.js"], []),
    _task("T-QA", "qa-agent", ["artifacts/test_plan.json"], ["T-DEV"]),
    _task("T-REV", "reviewer-agent", ["artifacts/review_report.json"], ["T-QA"]),
    _task("T-OPS", "devops-agent", ["artifacts/release_report.json"], ["T-REV"]),
]

# Adds the post-deploy e2e_validation stage (a UI project). T-E2E depends on devops.
E2E_TASKS = DEFAULT_TASKS + [
    _task("T-E2E", "e2e-agent", ["artifacts/e2e_report.json"], ["T-OPS"]),
]


def _write_workplan(project: Path, tasks=DEFAULT_TASKS):
    (project / "artifacts").mkdir(parents=True, exist_ok=True)
    (project / "artifacts" / "workplan.json").write_text(json.dumps({
        "spec_version": "v1", "workflow_id": "wf-test", "tasks": tasks,
    }))


def _valid_artifact(name, verdict="approved", failed=0):
    if name == "code_spec.json" or (name.startswith("code_spec") and name.endswith(".json")):
        return {"spec_version": "v1", "workflow_id": "wf-test", "task_id": "T",
                "title": "code spec", "implementation_notes": "impl", "test_refs": [],
                "files_affected": [{"path": "src/app.js", "change_type": "created"}]}
    if name == "test_plan.json":
        return {"spec_version": "v1", "workflow_id": "wf-test",
                "test_suites": [{"suite_id": "s1", "name": "suite", "owner_agent": "qa-agent",
                                 "test_cases": []}],
                "summary": {"total": 1, "passed": 1 - failed, "failed": failed, "skipped": 0}}
    if name == "review_report.json":
        return {"spec_version": "v1", "workflow_id": "wf-test", "task_id": "T-REV",
                "reviewer": "reviewer-agent", "verdict": verdict,
                "blocking_issues": [], "non_blocking_issues": [],
                "reviewed_at": "2026-01-01T00:00:00Z"}
    if name == "release_report.json":
        return {"spec_version": "v1", "workflow_id": "wf-test", "environment": "local",
                "artifact_ref": "build", "url": "http://localhost:8080",
                "verdict": "success", "deployed_at": "2026-01-01T00:00:00Z",
                "health_checks": [{"name": "GET /", "status": "pass"}]}
    if name == "e2e_report.json":
        return {"spec_version": "v1", "workflow_id": "wf-test",
                "base_url": "http://localhost:8080",
                "scenarios": [{"scenario_id": "E2E-1", "name": "loads",
                               "status": "fail" if failed else "pass"}],
                "summary": {"total": 1, "passed": 0 if failed else 1,
                            "failed": failed, "skipped": 0},
                "verdict": "failed" if failed else "passed",
                "validated_at": "2026-01-01T00:00:00Z"}
    raise ValueError(name)


def make_writer_runner(project: Path, *, verdict="approved", failed=0, src_body="// code\n"):
    """A runner that fulfils each task by writing valid declared outputs."""
    def fn(task, project_root):
        for rel in task["outputs"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".json"):
                name = "code_spec.json" if "/code_spec/" in ("/" + rel) else path.name
                path.write_text(json.dumps(_valid_artifact(name, verdict=verdict, failed=failed)))
            else:
                path.write_text(src_body)
    return CallableRunner(fn)


def make_rework_runner(project: Path, *, reject_rounds=1):
    """A writer runner whose reviewer rejects the first ``reject_rounds`` reviews
    then approves. Tracks developer/reviewer invocation counts so tests can assert
    the rework loop actually re-ran the developer (ENG-1)."""
    counts = {"dev": 0, "review": 0}

    def fn(task, project_root):
        if task["owner_agent"] == "developer-agent":
            counts["dev"] += 1
        for rel in task["outputs"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "review_report.json":
                counts["review"] += 1
                v = "rejected" if counts["review"] <= reject_rounds else "approved"
                path.write_text(json.dumps(_valid_artifact("review_report.json", verdict=v)))
            elif rel.endswith(".json"):
                path.write_text(json.dumps(_valid_artifact(path.name)))
            else:
                path.write_text("// code\n")
    return CallableRunner(fn), counts


def make_e2e_rework_runner(project: Path, *, fail_rounds=1):
    """A writer runner whose e2e_report fails the first ``fail_rounds`` validations
    then passes. Tracks developer/e2e invocation counts so tests can assert the
    post-deploy e2e failure re-ran the developer subtree (bounded at one round)."""
    counts = {"dev": 0, "e2e": 0}

    def fn(task, project_root):
        if task["owner_agent"] == "developer-agent":
            counts["dev"] += 1
        for rel in task["outputs"]:
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "e2e_report.json":
                counts["e2e"] += 1
                fail = 1 if counts["e2e"] <= fail_rounds else 0
                path.write_text(json.dumps(_valid_artifact("e2e_report.json", failed=fail)))
            elif rel.endswith(".json"):
                path.write_text(json.dumps(_valid_artifact(path.name)))
            else:
                path.write_text("// code\n")
    return CallableRunner(fn), counts


SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


def _orch(project, runner, **kw):
    # deterministic clock + ids so event assertions are stable
    clock = itertools.count()
    ids = itertools.count()
    return Orchestrator(
        project, runner,
        auto_approve=kw.pop("auto_approve", True),
        schemas_dir=kw.pop("schemas_dir", SCHEMAS_DIR),
        now=lambda: datetime.fromtimestamp(next(clock), tz=timezone.utc),
        new_id=lambda: f"id-{next(ids)}",
        **kw,
    )


def _events(project):
    p = project / "artifacts" / "events.log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# --------------------------------------------------------------------------- tests

def test_happy_path_reaches_complete(tmp_path):
    _write_workplan(tmp_path)
    state = _orch(tmp_path, make_writer_runner(tmp_path)).run()
    assert state["current_stage"] == "complete"
    assert all(t["status"] == "success" for t in state["tasks"].values())


def test_topo_order_respects_dependencies(tmp_path):
    _write_workplan(tmp_path)
    order = []
    def fn(task, root):
        order.append(task["task_id"])
        for rel in task["outputs"]:
            p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_valid_artifact(p.name)) if rel.endswith(".json") else "// code")
    _orch(tmp_path, CallableRunner(fn)).run()
    assert order == ["T-DEV", "T-QA", "T-REV", "T-OPS"]


def test_dependency_cycle_escalates(tmp_path):
    tasks = [_task("A", "developer-agent", ["src/a.js"], ["B"]),
             _task("B", "developer-agent", ["src/b.js"], ["A"])]
    _write_workplan(tmp_path, tasks)
    state = _orch(tmp_path, ReplayRunner()).run()
    assert state["current_stage"] == "failed"
    assert state["halted"] is True


def test_deploy_gate_blocks_on_failing_tests(tmp_path):
    _write_workplan(tmp_path)
    # tests report a failure -> deploy gate must refuse, engine retries then escalates
    state = _orch(tmp_path, make_writer_runner(tmp_path, failed=1)).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-OPS"]["status"] == "blocked"
    assert any("summary.failed" in i for i in state["tasks"]["T-OPS"]["blocking_issues"])


def test_rejected_review_reworks_then_completes(tmp_path):
    """ENG-1/ENG-2: a rejected review re-dispatches the upstream developer subtree;
    once the fix lands and review approves, the run completes. QA/deploy never run
    on the rejected revision."""
    _write_workplan(tmp_path)
    runner, counts = make_rework_runner(tmp_path, reject_rounds=1)
    state = _orch(tmp_path, runner).run()
    assert state["current_stage"] == "complete"
    assert counts["dev"] == 2        # original + one rework round
    assert counts["review"] == 2
    assert state["tasks"]["T-REV"]["rework"] == 1
    assert state["tasks"]["T-OPS"]["status"] == "success"


def test_rejected_review_escalates_after_max_rework(tmp_path):
    """ENG-1: a persistently rejected review escalates after max_rework rounds —
    the reviewer blocks, deploy never runs, and the cap bounds the dev re-runs."""
    _write_workplan(tmp_path)
    runner, counts = make_rework_runner(tmp_path, reject_rounds=99)  # always reject
    state = _orch(tmp_path, runner, max_rework=2).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-REV"]["status"] == "blocked"
    assert state["tasks"]["T-OPS"]["status"] != "success"
    assert counts["dev"] == 3        # original + 2 rework rounds, then escalate
    assert any("rejected" in i for i in state["tasks"]["T-REV"]["blocking_issues"])


def test_review_gate_catches_rejection_before_qa_and_deploy(tmp_path):
    """ENG-3: with max_rework=0 a rejected review blocks at the review stage —
    not two stages later at deploy — so QA/deploy are never reached."""
    _write_workplan(tmp_path)
    runner, counts = make_rework_runner(tmp_path, reject_rounds=99)
    state = _orch(tmp_path, runner, max_rework=0).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-REV"]["status"] == "blocked"
    assert state["tasks"]["T-OPS"]["status"] == "pending"   # deploy never ran
    assert counts["dev"] == 1                                # no rework attempted


def test_e2e_stage_reaches_complete(tmp_path):
    """The post-deploy e2e_validation stage runs as a normal DAG task: a passing
    e2e_report lets the run reach complete."""
    _write_workplan(tmp_path, E2E_TASKS)
    state = _orch(tmp_path, make_writer_runner(tmp_path)).run()
    assert state["current_stage"] == "complete"
    assert state["tasks"]["T-E2E"]["status"] == "success"
    assert state["tasks"]["T-E2E"]["stage"] == "e2e_validation"


def test_e2e_failure_reworks_then_completes(tmp_path):
    """A failed e2e run (against the deployed app) re-dispatches the developer
    subtree; once the fix lands and e2e passes, the run completes."""
    _write_workplan(tmp_path, E2E_TASKS)
    runner, counts = make_e2e_rework_runner(tmp_path, fail_rounds=1)
    state = _orch(tmp_path, runner).run()
    assert state["current_stage"] == "complete"
    assert counts["dev"] == 2        # original + one rework round
    assert counts["e2e"] == 2
    assert state["tasks"]["T-E2E"]["rework"] == 1
    assert state["tasks"]["T-E2E"]["status"] == "success"


def test_e2e_failure_escalates_after_one_rework(tmp_path):
    """E2E has a per-stage rework cap of 1 (STAGE_REWORK_CAP): a persistently failing
    e2e run escalates after a single rework round, and the failure is queued to
    backlog.json — even though the global max_rework is higher."""
    _write_workplan(tmp_path, E2E_TASKS)
    runner, counts = make_e2e_rework_runner(tmp_path, fail_rounds=99)  # always fail
    state = _orch(tmp_path, runner, max_rework=2).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-E2E"]["status"] == "blocked"
    assert counts["dev"] == 2        # original + exactly one rework round, then escalate
    assert state["tasks"]["T-E2E"]["rework"] == 1
    backlog = json.loads((tmp_path / "artifacts" / "backlog.json").read_text())
    assert any("e2e_validation" in str(b.get("release_verdict", "")) for b in backlog)


def test_per_task_code_spec_paths_resolve_and_run(tmp_path):
    """ENG-4: parallel developer tasks write task-scoped code specs
    (artifacts/code_spec/<id>.json) instead of clobbering one shared file."""
    from orchestrator.validation import schema_for_output
    assert schema_for_output("artifacts/code_spec/T-04.json") == "code_spec.schema.json"
    assert schema_for_output("artifacts/code_spec.json") == "code_spec.schema.json"

    tasks = [
        _task("D1", "developer-agent", ["artifacts/code_spec/D1.json", "src/d1.js"], []),
        _task("D2", "developer-agent", ["artifacts/code_spec/D2.json", "src/d2.js"], []),
    ]
    _write_workplan(tmp_path, tasks)
    state = _orch(tmp_path, make_writer_runner(tmp_path)).run()
    assert state["current_stage"] == "complete"
    assert (tmp_path / "artifacts/code_spec/D1.json").exists()
    assert (tmp_path / "artifacts/code_spec/D2.json").exists()


def test_soft_security_warning_does_not_block(tmp_path):
    """SEC-2: a 'warn'-tier sink (innerHTML) is surfaced but does not fail the run,
    so a legitimate frontend isn't hard-blocked on a false positive."""
    _write_workplan(tmp_path)
    runner = make_writer_runner(tmp_path, src_body="el.innerHTML = userInput;\n")
    state = _orch(tmp_path, runner).run()
    assert state["current_stage"] == "complete"
    warned = [e for e in _events(tmp_path)
              if e["status"] == "success" and "security warning" in e.get("summary", "")]
    assert warned, "expected a non-blocking security warning in a success event"


def test_hard_security_violation_blocks(tmp_path):
    """SEC-2: a 'block'-tier pattern (eval) is still an unrecoverable block."""
    _write_workplan(tmp_path)
    runner = make_writer_runner(tmp_path, src_body="eval(userInput);\n")
    state = _orch(tmp_path, runner).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-REV"]["status"] == "blocked"
    assert any("eval()" in i for i in state["tasks"]["T-REV"]["blocking_issues"])


def test_cost_budget_breaker_halts(tmp_path):
    """ENG-8: once cumulative agent cost reaches the ceiling, the breaker halts new
    dispatch and finalizes the run as failed."""
    _write_workplan(tmp_path)

    def fn(task, project_root):
        for rel in task["outputs"]:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_valid_artifact(p.name)) if rel.endswith(".json") else "// code")
        return {"usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 1.0}

    state = _orch(tmp_path, CallableRunner(fn), max_cost_usd=0.5).run()
    assert state["current_stage"] == "failed"
    assert state["halted"] is True
    assert state["tasks"]["T-DEV"]["status"] == "success"   # first task ran
    assert state["tasks"]["T-QA"]["status"] == "pending"    # breaker stopped the rest
    assert any("cost budget" in e.get("summary", "") for e in _events(tmp_path))


def test_monitoring_feedback_success_on_healthy_deploy(tmp_path):
    """ENG-5: a healthy deploy emits a monitoring_feedback success event and no backlog."""
    _write_workplan(tmp_path)
    state = _orch(tmp_path, make_writer_runner(tmp_path)).run()
    assert state["current_stage"] == "complete"
    mon = [e for e in _events(tmp_path) if e["stage"] == "monitoring_feedback"]
    assert mon and mon[-1]["status"] == "success"
    assert not (tmp_path / "artifacts/backlog.json").exists()


def test_monitoring_feedback_queues_remediation_on_unhealthy_deploy(tmp_path):
    """ENG-5: an unhealthy deploy (failed health check) still completes the build
    (the deploy gate already passed) but emits a failure feedback event and queues a
    remediation item to backlog.json — the minimal Stage 8 feedback loop."""
    _write_workplan(tmp_path)

    def fn(task, project_root):
        for rel in task["outputs"]:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == "release_report.json":
                rep = _valid_artifact("release_report.json")
                rep["verdict"] = "partial"
                rep["health_checks"] = [{"name": "GET /", "status": "fail"}]
                p.write_text(json.dumps(rep))
            elif rel.endswith(".json"):
                p.write_text(json.dumps(_valid_artifact(p.name)))
            else:
                p.write_text("// code")

    state = _orch(tmp_path, CallableRunner(fn)).run()
    assert state["current_stage"] == "complete"
    mon = [e for e in _events(tmp_path) if e["stage"] == "monitoring_feedback"]
    assert mon and mon[-1]["status"] == "failure"
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and backlog[0]["source"] == "monitoring_feedback"


def test_recovers_from_transient_failures(tmp_path):
    """Injected failures: T-DEV fails twice (recoverable) then succeeds within retries."""
    _write_workplan(tmp_path)
    calls = {"T-DEV": 0}
    def fn(task, root):
        if task["task_id"] == "T-DEV":
            calls["T-DEV"] += 1
            if calls["T-DEV"] <= 2:
                raise RecoverableError("flaky tool")
        for rel in task["outputs"]:
            p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_valid_artifact(p.name)) if rel.endswith(".json") else "// code")
    state = _orch(tmp_path, CallableRunner(fn)).run()
    assert calls["T-DEV"] == 3                       # 2 failures + 1 success
    assert state["current_stage"] == "complete"
    assert state["tasks"]["T-DEV"]["attempt"] == 2   # two retries recorded
    assert any(e["status"] == "retry" for e in _events(tmp_path))


def test_escalates_after_max_retries(tmp_path):
    """A task that always fails must escalate (block) after max_retries, not loop."""
    _write_workplan(tmp_path)
    calls = {"n": 0}
    def fn(task, root):
        if task["task_id"] == "T-DEV":
            calls["n"] += 1
            raise RecoverableError("permanently broken")
        for rel in task["outputs"]:
            p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// code")
    state = _orch(tmp_path, CallableRunner(fn)).run()
    assert state["tasks"]["T-DEV"]["status"] == "blocked"
    assert state["current_stage"] == "failed"
    assert calls["n"] == 4                            # 1 + max_retries(3)
    # downstream tasks never ran
    assert state["tasks"]["T-QA"]["status"] == "pending"


def test_unrecoverable_blocks_immediately(tmp_path):
    _write_workplan(tmp_path)
    calls = {"n": 0}
    def fn(task, root):
        if task["task_id"] == "T-DEV":
            calls["n"] += 1
            raise UnrecoverableError("unsafe request")
    state = _orch(tmp_path, CallableRunner(fn)).run()
    assert calls["n"] == 1                            # no retries
    assert state["tasks"]["T-DEV"]["status"] == "blocked"


def test_security_scan_blocks_eval(tmp_path):
    _write_workplan(tmp_path)
    state = _orch(tmp_path, make_writer_runner(tmp_path, src_body="const x = eval(userInput);\n")).run()
    # reviewer stage runs the security baseline over project source
    assert state["tasks"]["T-REV"]["status"] == "blocked"
    assert state["current_stage"] == "failed"


def test_events_are_machine_stamped(tmp_path):
    _write_workplan(tmp_path)
    _orch(tmp_path, make_writer_runner(tmp_path)).run()
    evs = _events(tmp_path)
    assert evs, "expected events"
    for e in evs:
        assert e["event_id"].startswith("id-")        # engine-stamped, not fabricated
        assert e["timestamp"].endswith("Z")
        assert e["workflow_id"] == "wf-test" or e["workflow_id"]


def test_human_checkpoint_pauses_then_resumes(tmp_path):
    _write_workplan(tmp_path)
    # no auto-approve: must pause before the developer task (architecture sign-off)
    paused = _orch(tmp_path, make_writer_runner(tmp_path), auto_approve=False).run()
    assert paused["tasks"]["T-DEV"]["status"] == "awaiting_approval"
    assert paused["current_stage"] == "code_generation"

    # resume with the approval granted -> runs to completion
    resumed = _orch(tmp_path, make_writer_runner(tmp_path), auto_approve=False,
                    approvals={"architecture", "production_deploy"}).run()
    assert resumed["current_stage"] == "complete"


def test_idempotent_resume_does_not_rerun_done_tasks(tmp_path):
    _write_workplan(tmp_path)
    runs = {"n": 0}
    def fn(task, root):
        runs["n"] += 1
        for rel in task["outputs"]:
            p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_valid_artifact(p.name)) if rel.endswith(".json") else "// code")
    _orch(tmp_path, CallableRunner(fn)).run()
    first = runs["n"]
    _orch(tmp_path, CallableRunner(fn)).run()          # second run resumes
    assert runs["n"] == first                           # no task re-executed


# --------------------------------------------------------------- bootstrap (Chunk 4)

def _valid_requirements():
    return {"spec_version": "v1", "id": "REQ-1", "title": "t", "problem_statement": "p",
            "scope": {"in_scope": ["x"], "out_of_scope": []}, "non_goals": [],
            "constraints": [], "acceptance_criteria": ["a"], "risks": [], "open_questions": []}


def _valid_architecture():
    return {"spec_version": "v1", "workflow_id": "wf-test",
            "components": [{"id": "c1", "name": "app", "type": "cli",
                            "responsibilities": ["r"], "interfaces": [{"name": "cli", "type": "cli"}]}],
            "runtime": {"language": "js", "runtime_version": "20"},
            "persistence": {"type": "none"},
            "failure_modes": [{"scenario": "s", "mitigation": "m"}]}


# The planner emits the architect as the first DAG node (the prelude no longer
# runs the architect — it does product → planner only).
PLANNED_TASKS = [
    _task("T-ARCH", "architect-agent",
          ["artifacts/architecture.json", "artifacts/api-contracts.json",
           "artifacts/data-model.json"], []),
    _task("T-DEV", "developer-agent", ["src/app.js"], ["T-ARCH"]),
    _task("T-QA", "qa-agent", ["artifacts/test_plan.json"], ["T-DEV"]),
    _task("T-REV", "reviewer-agent", ["artifacts/review_report.json"], ["T-QA"]),
    _task("T-OPS", "devops-agent", ["artifacts/release_report.json"], ["T-REV"]),
]


def make_prelude_runner(project: Path):
    """A fake runner that fulfils the prelude stages (product/planner) AND the DAG
    tasks — so the whole prompt→complete pipeline runs without an LLM. The planner
    stage writes PLANNED_TASKS (architect as the first DAG node) into workplan.json."""
    def fn(task, project_root):
        for rel in task["outputs"]:
            p = project / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            name = p.name
            if name == "requirements.json":
                p.write_text(json.dumps(_valid_requirements()))
            elif name == "requirements.md":
                p.write_text("# requirements\n")
            elif name == "workplan.json":
                p.write_text(json.dumps({"spec_version": "v1", "workflow_id": "wf-test",
                                         "tasks": PLANNED_TASKS}))
            elif name == "architecture.json":
                p.write_text(json.dumps(_valid_architecture()))
            elif name == "api-contracts.json":
                p.write_text(json.dumps({"openapi": "3.1.0",
                                         "info": {"title": "t", "version": "1"}, "paths": {}}))
            elif name == "data-model.json":
                p.write_text(json.dumps({"entities": [
                    {"name": "E", "fields": [{"name": "id", "type": "UUID"}]}]}))
            elif name.endswith(".json"):
                p.write_text(json.dumps(_valid_artifact(name)))
            else:
                p.write_text("// code\n")
    return CallableRunner(fn)


def test_run_from_prompt_drives_full_pipeline(tmp_path):
    # no workplan supplied: the prelude must CREATE it from the prompt
    state = _orch(tmp_path, make_prelude_runner(tmp_path)).run_from_prompt("build a thing")
    assert state["current_stage"] == "complete"
    assert (tmp_path / "artifacts" / "requirements.json").exists()
    assert (tmp_path / "artifacts" / "workplan.json").exists()      # produced by planner
    assert (tmp_path / "artifacts" / "architecture.json").exists()   # produced by the DAG architect task
    assert state["tasks"]["STAGE-REQUIREMENTS"]["status"] == "success"
    assert "STAGE-ARCH" not in state["tasks"]                         # architect no longer in the prelude
    assert all(state["tasks"][t]["status"] == "success"
               for t in ("T-ARCH", "T-DEV", "T-QA", "T-REV", "T-OPS"))


def test_run_from_prompt_pauses_at_requirements_signoff(tmp_path):
    # first stage (product) runs; then the requirements gate must pause the planner
    paused = _orch(tmp_path, make_prelude_runner(tmp_path),
                   auto_approve=False).run_from_prompt("build a thing")
    assert paused["current_stage"] == "task_decomposition"
    assert paused["tasks"]["STAGE-PLAN"]["status"] == "awaiting_approval"
    assert paused["tasks"]["STAGE-REQUIREMENTS"]["status"] == "success"

    # resume with every sign-off granted → runs end to end
    resumed = _orch(tmp_path, make_prelude_runner(tmp_path), auto_approve=False,
                    approvals={"requirements", "architecture", "production_deploy"}
                    ).run_from_prompt("build a thing")
    assert resumed["current_stage"] == "complete"


def test_unblock_recovers_a_blocked_task(tmp_path):
    """A task that blocks (always-failing agent) can be reset + re-run after the
    cause is fixed — the operator-driven recovery path."""
    _write_workplan(tmp_path)
    fail = {"on": True}
    calls = {"n": 0}
    def fn(task, root):
        if task["task_id"] == "T-DEV" and fail["on"]:
            calls["n"] += 1
            raise RecoverableError("session limit")
        for rel in task["outputs"]:
            p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_valid_artifact(p.name)) if rel.endswith(".json") else "// code")

    # first run: T-DEV exhausts retries and blocks; circuit breaker halts
    state = _orch(tmp_path, CallableRunner(fn)).run()
    assert state["tasks"]["T-DEV"]["status"] == "blocked"
    assert state["current_stage"] == "failed" and state["halted"] is True

    # a plain re-run does nothing while halted
    assert _orch(tmp_path, CallableRunner(fn)).run()["halted"] is True

    # fix the cause, reset the task, resume → runs to completion
    fail["on"] = False
    orch = _orch(tmp_path, CallableRunner(fn))
    assert orch.unblock(["T-DEV"]) == 1
    resumed = orch.run()
    assert resumed["current_stage"] == "complete"
    assert resumed["tasks"]["T-DEV"]["status"] == "success"


def test_unblock_all_blocked_defaults(tmp_path):
    _write_workplan(tmp_path)
    state = _orch(tmp_path, make_writer_runner(tmp_path, failed=1)).run()
    assert state["tasks"]["T-OPS"]["status"] == "blocked"
    # unblock() with no args resets every blocked task
    assert _orch(tmp_path, make_writer_runner(tmp_path)).unblock() >= 1


# --------------------------------------------------------------- parallel scheduling

# A diamond DAG: two independent developer tasks fan out and fan back in. T-DEV-A
# and T-DEV-B have no dependency on each other (both roots), so the wave scheduler
# must run them concurrently (SPEC §8.5). They emit only un-validated source files,
# keeping the fixture focused on scheduling rather than schema details.
def _diamond_tasks():
    return [
        _task("T-DEV-A", "developer-agent", ["src/a.js"], []),
        _task("T-DEV-B", "developer-agent", ["src/b.js"], []),
        _task("T-QA", "qa-agent", ["artifacts/test_plan.json"], ["T-DEV-A", "T-DEV-B"]),
        _task("T-REV", "reviewer-agent", ["artifacts/review_report.json"], ["T-QA"]),
        _task("T-OPS", "devops-agent", ["artifacts/release_report.json"], ["T-REV"]),
    ]


def _write_outputs(task, tmp_path):
    for rel in task["outputs"]:
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            p.write_text(json.dumps(_valid_artifact(p.name)))
        else:
            p.write_text("// code\n")


def test_independent_tasks_run_concurrently(tmp_path):
    """The two sibling dev tasks must overlap in time — proving the wave scheduler
    dispatches runnable tasks in parallel rather than one after another."""
    import threading
    _write_workplan(tmp_path, _diamond_tasks())

    inside = set()
    max_concurrent = {"n": 0}
    overlap = threading.Event()
    barrier = threading.Barrier(2, timeout=5)  # both dev tasks must reach this
    guard = threading.Lock()

    def fn(task, root):
        tid = task["task_id"]
        if tid in ("T-DEV-A", "T-DEV-B"):
            try:  # if both threads reach the barrier, they were running at once
                barrier.wait()
                overlap.set()
            except threading.BrokenBarrierError:
                pass
            with guard:
                inside.add(tid)
                max_concurrent["n"] = max(max_concurrent["n"], len(inside))
        _write_outputs(task, tmp_path)
        if tid in ("T-DEV-A", "T-DEV-B"):
            with guard:
                inside.discard(tid)

    state = _orch(tmp_path, CallableRunner(fn), max_parallel=4).run()
    assert state["current_stage"] == "complete"
    assert overlap.is_set(), "sibling dev tasks did not overlap — ran sequentially"
    assert max_concurrent["n"] == 2


def test_max_parallel_one_forces_sequential(tmp_path):
    """max_parallel=1 must serialize even independent tasks (escape hatch)."""
    import threading
    _write_workplan(tmp_path, _diamond_tasks())
    concurrent = {"n": 0, "max": 0}
    guard = threading.Lock()

    def fn(task, root):
        with guard:
            concurrent["n"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["n"])
        _write_outputs(task, tmp_path)
        with guard:
            concurrent["n"] -= 1

    state = _orch(tmp_path, CallableRunner(fn), max_parallel=1).run()
    assert state["current_stage"] == "complete"
    assert concurrent["max"] == 1  # never more than one task in flight


def test_parallel_wave_blocks_when_one_sibling_fails(tmp_path):
    """If one task in a concurrent wave exhausts retries, the run escalates and
    the workflow ends failed; downstream tasks never run."""
    _write_workplan(tmp_path, _diamond_tasks())
    def fn(task, root):
        if task["task_id"] == "T-DEV-B":
            raise RecoverableError("always broken")
        _write_outputs(task, tmp_path)
    state = _orch(tmp_path, CallableRunner(fn), max_parallel=4).run()
    assert state["current_stage"] == "failed"
    assert state["tasks"]["T-DEV-B"]["status"] == "blocked"
    assert state["tasks"]["T-QA"]["status"] == "pending"   # downstream never ran


def test_atomic_state_is_valid_against_schema(tmp_path):
    import jsonschema
    _write_workplan(tmp_path)
    state = _orch(tmp_path, make_writer_runner(tmp_path)).run()
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas"
                         / "workflow_state.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(state)


# ---------------------------------------------- monitoring_feedback loop (SPEC §3.9)

def make_health_rework_runner(project: Path, *, unhealthy_rounds=1):
    """A writer runner whose devops emits an UNHEALTHY release_report for the first
    ``unhealthy_rounds`` deploys then a healthy one. Tracks developer/deploy counts so
    tests can assert the post-deploy health rework re-ran the developer subtree."""
    counts = {"dev": 0, "deploy": 0}

    def fn(task, project_root):
        if task["owner_agent"] == "developer-agent":
            counts["dev"] += 1
        for rel in task["outputs"]:
            p = project / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == "release_report.json":
                counts["deploy"] += 1
                rep = _valid_artifact("release_report.json")
                if counts["deploy"] <= unhealthy_rounds:
                    rep["verdict"] = "partial"
                    rep["health_checks"] = [{"name": "GET /", "status": "fail"}]
                p.write_text(json.dumps(rep))
            elif rel.endswith(".json"):
                name = "code_spec.json" if "/code_spec/" in ("/" + rel) else p.name
                p.write_text(json.dumps(_valid_artifact(name)))
            else:
                p.write_text("// code\n")
    return CallableRunner(fn), counts


def make_prelude_health_runner(project: Path, *, unhealthy_deploys=2):
    """A full prompt→complete runner (product/planner/DAG, like make_prelude_runner)
    whose devops emits an UNHEALTHY release_report for the first ``unhealthy_deploys``
    deploys then a healthy one — so a cross-run feedback cycle (re-plan) is needed to
    converge. Tracks product/dev/deploy counts across cycles."""
    counts = {"product": 0, "dev": 0, "deploy": 0}

    def fn(task, project_root):
        agent = task["owner_agent"]
        if agent == "product-agent":
            counts["product"] += 1
        if agent == "developer-agent":
            counts["dev"] += 1
        for rel in task["outputs"]:
            p = project / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            name = p.name
            if name == "requirements.json":
                p.write_text(json.dumps(_valid_requirements()))
            elif name == "requirements.md":
                p.write_text("# requirements\n")
            elif name == "workplan.json":
                p.write_text(json.dumps({"spec_version": "v1", "workflow_id": "wf-test",
                                         "tasks": PLANNED_TASKS}))
            elif name == "architecture.json":
                p.write_text(json.dumps(_valid_architecture()))
            elif name == "api-contracts.json":
                p.write_text(json.dumps({"openapi": "3.1.0",
                                         "info": {"title": "t", "version": "1"}, "paths": {}}))
            elif name == "data-model.json":
                p.write_text(json.dumps({"entities": [
                    {"name": "E", "fields": [{"name": "id", "type": "UUID"}]}]}))
            elif name == "release_report.json":
                counts["deploy"] += 1
                rep = _valid_artifact("release_report.json")
                if counts["deploy"] <= unhealthy_deploys:
                    rep["verdict"] = "partial"
                    rep["health_checks"] = [{"name": "GET /", "status": "fail"}]
                p.write_text(json.dumps(rep))
            elif name.endswith(".json"):
                p.write_text(json.dumps(_valid_artifact(name)))
            else:
                p.write_text("// code\n")
    return CallableRunner(fn), counts


def test_health_rework_remediates_unhealthy_deploy(tmp_path):
    """Level 1: an unhealthy deploy drives a bounded in-run health rework (re-dispatch
    the developer subtree → re-deploy → re-monitor); once healthy, the run completes and
    the backlog item is resolved."""
    _write_workplan(tmp_path)
    runner, counts = make_health_rework_runner(tmp_path, unhealthy_rounds=1)
    state = _orch(tmp_path, runner, max_feedback_cycles=1).run()
    assert state["current_stage"] == "complete"
    assert state["health_rework"] == 1
    assert counts["dev"] == 2       # original + one health rework round
    assert counts["deploy"] == 2    # original + re-deploy
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and all(b["status"] == "resolved" for b in backlog)


def test_health_rework_escalates_when_unfixable_without_replan(tmp_path):
    """Level 1 cap (STAGE_REWORK_CAP['monitoring_feedback']=1): a persistently unhealthy
    DAG-only deploy reworks once, and — with no product agent to re-plan with — escalates,
    queueing the failure to backlog.json as 'escalated'."""
    _write_workplan(tmp_path)
    runner, counts = make_health_rework_runner(tmp_path, unhealthy_rounds=99)
    state = _orch(tmp_path, runner, max_feedback_cycles=1).run()
    assert state["current_stage"] == "failed"
    assert state["health_rework"] == 1
    assert counts["dev"] == 2       # original + exactly one rework round, then escalate
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and all(b["status"] == "escalated" for b in backlog)


def test_feedback_cycle_replans_to_remediate(tmp_path):
    """Level 2: when an in-run health rework doesn't fix the deploy, a cross-run feedback
    cycle re-runs the product agent (folding backlog.json into updated requirements) and
    the whole pipeline; once healthy the loop closes and the backlog resolves."""
    runner, counts = make_prelude_health_runner(tmp_path, unhealthy_deploys=2)
    # deploy#1 unhealthy → health rework → deploy#2 unhealthy → re-plan cycle 1 → deploy#3 healthy
    state = _orch(tmp_path, runner, max_feedback_cycles=1).run_from_prompt("build a thing")
    assert state["current_stage"] == "complete"
    assert state["feedback_cycle"] == 1
    assert counts["product"] >= 2    # product re-ran for the feedback cycle
    assert counts["deploy"] == 3
    # both remediation levels ran — recorded in the event log (the audit source of truth)
    mon = [e["summary"] for e in _events(tmp_path) if e["stage"] == "monitoring_feedback"]
    assert any("health rework" in s for s in mon)
    assert any("feedback cycle" in s for s in mon)
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and all(b["status"] == "resolved" for b in backlog)
    # the final multi-cycle state still validates against the (extended) schema
    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas"
                         / "workflow_state.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(state)


def test_feedback_loop_escalates_after_cycle_cap(tmp_path):
    """Level 2 cap: a deploy that never becomes healthy exhausts the in-run rework and the
    bounded re-plan cycles, then escalates — the backlog items end 'escalated' and the run
    fails."""
    runner, counts = make_prelude_health_runner(tmp_path, unhealthy_deploys=99)
    state = _orch(tmp_path, runner, max_feedback_cycles=1).run_from_prompt("build a thing")
    assert state["current_stage"] == "failed"
    assert state["feedback_cycle"] == 1
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and all(b["status"] == "escalated" for b in backlog)
    mon = [e for e in _events(tmp_path) if e["stage"] == "monitoring_feedback"]
    assert mon[-1]["status"] == "blocked"


def test_feedback_loop_honours_production_deploy_gate(tmp_path):
    """Each deploy in the loop — including a health-rework re-deploy — passes through the
    production_deploy checkpoint. With the gate un-approved the loop cannot deploy; granting
    it lets the same loop deploy → detect unhealthy → rework → re-deploy → complete."""
    _write_workplan(tmp_path)
    # gate closed → pauses at the first deployment (before any monitoring/rework)
    paused = _orch(tmp_path, make_health_rework_runner(tmp_path)[0],
                   auto_approve=False, approvals={"requirements", "architecture"},
                   max_feedback_cycles=1).run()
    assert paused["current_stage"] == "deployment"
    assert paused["tasks"]["T-OPS"]["status"] == "awaiting_approval"
    # gate granted → resume: deploy (unhealthy) → health rework → re-deploy (gated again,
    # now approved) → healthy → complete
    runner, counts = make_health_rework_runner(tmp_path, unhealthy_rounds=1)
    done = _orch(tmp_path, runner, auto_approve=False,
                 approvals={"requirements", "architecture", "production_deploy"},
                 max_feedback_cycles=1).run()
    assert done["current_stage"] == "complete"
    assert done["health_rework"] == 1
    assert counts["deploy"] == 2     # the post-rework re-deploy went through the gate


def test_produced_backlog_validates_against_schema(tmp_path):
    """The backlog.json the engine writes is itself schema-valid (backlog.schema.json) —
    the monitoring_feedback signal is a first-class, validated artifact, not loose JSON."""
    import jsonschema
    _write_workplan(tmp_path)
    runner, _ = make_health_rework_runner(tmp_path, unhealthy_rounds=99)
    _orch(tmp_path, runner, max_feedback_cycles=1).run()
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas"
                         / "backlog.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(backlog)


def test_feedback_loop_disabled_by_default_is_signal_only(tmp_path):
    """Back-compat: with the loop disabled (default max_feedback_cycles=0) an unhealthy
    deploy never reworks — it queues a single open backlog signal and the run still
    completes (the deploy gate already owned go/no-go)."""
    _write_workplan(tmp_path)
    runner, counts = make_health_rework_runner(tmp_path, unhealthy_rounds=99)
    state = _orch(tmp_path, runner).run()      # max_feedback_cycles defaults to 0
    assert state["current_stage"] == "complete"
    assert state.get("health_rework", 0) == 0
    assert counts["deploy"] == 1               # no re-deploy attempted
    backlog = json.loads((tmp_path / "artifacts/backlog.json").read_text())
    assert backlog and backlog[0]["status"] == "open"


# --------------------------------------------------------------- brownfield extension

# A feature subtree whose new gate tasks depend ONLY on the new developer task — so a
# rejected feature review can never reset/unlink the existing T-DEV's source (the
# load-bearing safety rule). T-DEV2 depends on the existing (success) architect node.
FEATURE_TASKS = [
    _task("T-DEV2", "developer-agent", ["src/feature.js"], ["T-ARCH"]),
    _task("T-QA2", "qa-agent", ["artifacts/test_plan.json"], ["T-DEV2"]),
    _task("T-REV2", "reviewer-agent", ["artifacts/review_report.json"], ["T-QA2"]),
    _task("T-OPS2", "devops-agent", ["artifacts/release_report.json"], ["T-REV2"]),
]
EXTENDED_TASKS = PLANNED_TASKS + FEATURE_TASKS


def make_extension_runner(project: Path):
    """Prelude+DAG runner whose planner emits an ADDITIVE workplan (existing tasks
    verbatim + a new feature subtree). Tracks developer invocations per task_id so a
    test can assert the existing developer task is never re-run."""
    dev_calls: dict = {}

    def fn(task, project_root):
        if task["owner_agent"] == "developer-agent":
            dev_calls[task["task_id"]] = dev_calls.get(task["task_id"], 0) + 1
        for rel in task["outputs"]:
            p = project / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            name = p.name
            if name == "requirements.json":
                p.write_text(json.dumps(_valid_requirements()))
            elif name == "requirements.md":
                p.write_text("# requirements (amended)\n")
            elif name == "workplan.json":
                p.write_text(json.dumps({"spec_version": "v1", "workflow_id": "wf-test",
                                         "tasks": EXTENDED_TASKS}))
            elif name == "architecture.json":
                p.write_text(json.dumps(_valid_architecture()))
            elif name == "api-contracts.json":
                p.write_text(json.dumps({"openapi": "3.1.0",
                                         "info": {"title": "t", "version": "1"}, "paths": {}}))
            elif name == "data-model.json":
                p.write_text(json.dumps({"entities": [
                    {"name": "E", "fields": [{"name": "id", "type": "UUID"}]}]}))
            elif name.endswith(".json"):
                p.write_text(json.dumps(_valid_artifact(name)))
            else:
                p.write_text("// code\n")
    return CallableRunner(fn), dev_calls


def test_extend_with_feature_is_additive_and_nondestructive(tmp_path):
    """Brownfield extension re-runs product+planner additively and builds ONLY the new
    tasks — the existing developer task is never re-invoked and its source is untouched."""
    base = _orch(tmp_path, make_prelude_runner(tmp_path)).run_from_prompt("build a thing")
    assert base["current_stage"] == "complete"
    app = tmp_path / "src" / "app.js"
    original_app = app.read_text()

    runner, dev_calls = make_extension_runner(tmp_path)
    state = _orch(tmp_path, runner).extend_with_feature("add a data export feature")

    assert state["current_stage"] == "complete"
    # the new feature dev task ran exactly once; the EXISTING dev task was NOT re-run
    assert dev_calls.get("T-DEV2") == 1
    assert dev_calls.get("T-DEV", 0) == 0
    # existing generated source is byte-for-byte untouched; new source exists
    assert app.read_text() == original_app
    assert (tmp_path / "src" / "feature.js").exists()
    # every task (old + new) is success
    for t in ("T-ARCH", "T-DEV", "T-QA", "T-REV", "T-OPS",
              "T-DEV2", "T-QA2", "T-REV2", "T-OPS2"):
        assert state["tasks"][t]["status"] == "success"
    # the brownfield extension is audited
    assert any("brownfield feature extension" in e.get("summary", "")
               for e in _events(tmp_path))


def test_extend_with_feature_requires_complete_state(tmp_path):
    """The guard refuses to extend when there is no completed workflow to build on."""
    runner, _ = make_extension_runner(tmp_path)
    # no workflow_state.json at all
    with pytest.raises(Escalation):
        _orch(tmp_path, runner).extend_with_feature("x")
    # an in-flight (non-complete) workflow must not be extended
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "workflow_state.json").write_text(json.dumps({
        "workflow_id": "wf", "current_stage": "code_generation",
        "tasks": {}, "stages": {}}))
    with pytest.raises(Escalation):
        _orch(tmp_path, runner).extend_with_feature("x")
