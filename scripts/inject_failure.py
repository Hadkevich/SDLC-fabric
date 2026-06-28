#!/usr/bin/env python3
"""Resilience demo — inject failures and watch the orchestrator auto-recover.

Scorecard §4.2 asks the system to recover from >=2 injected failures, live. This
script drives the **real** deterministic engine (src/orchestrator) with scripted
runners — no LLM, no Claude spend, runs in seconds — so the recovery is
reproducible on demand at the demo. Each scenario injects a different failure
class and prints the resulting events.log.jsonl showing retry/blocked -> success
with NO manual code edits in between.

    python scripts/inject_failure.py            # run all three scenarios
    python scripts/inject_failure.py rework     # one of: rework | health | corrupt

Failure classes demonstrated:
  rework  - reviewer rejects the build (a real defect, like BLK-001) -> the engine
            re-dispatches the developer subtree and re-reviews until approved, then
            the deploy gate ships. Fully autonomous (closes the "manual fix" caveat).
  health  - the deploy comes up UNHEALTHY -> monitoring_feedback queues a backlog
            item and the Level-1 health-rework loop re-builds + re-deploys until
            healthy; backlog goes open -> resolved.
  corrupt - an agent emits an invalid/garbled artifact -> the engine classifies it
            recoverable and retries the task (same path the live run took on the
            developer/QA timeouts).
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from orchestrator import CallableRunner, Orchestrator  # noqa: E402

SCHEMAS_DIR = _REPO / "schemas"

# DAG: dev -> qa -> reviewer -> devops (linear, exercises every gate)
TASKS = [
    {"task_id": "T-DEV", "title": "build", "owner_agent": "developer-agent",
     "inputs": [], "outputs": ["src/app.js"], "depends_on": [], "done_criteria": ["d"]},
    {"task_id": "T-QA", "title": "test", "owner_agent": "qa-agent", "inputs": [],
     "outputs": ["artifacts/test_plan.json"], "depends_on": ["T-DEV"], "done_criteria": ["d"]},
    {"task_id": "T-REV", "title": "review", "owner_agent": "reviewer-agent", "inputs": [],
     "outputs": ["artifacts/review_report.json"], "depends_on": ["T-QA"], "done_criteria": ["d"]},
    {"task_id": "T-OPS", "title": "deploy", "owner_agent": "devops-agent", "inputs": [],
     "outputs": ["artifacts/release_report.json"], "depends_on": ["T-REV"], "done_criteria": ["d"]},
]


def _artifact(name, *, verdict="approved", failed=0, healthy=True):
    if name == "test_plan.json":
        return {"spec_version": "v1", "workflow_id": "wf-demo",
                "test_suites": [{"suite_id": "s1", "name": "suite",
                                 "owner_agent": "qa-agent", "test_cases": []}],
                "summary": {"total": 1, "passed": 1 - failed, "failed": failed, "skipped": 0}}
    if name == "review_report.json":
        return {"spec_version": "v1", "workflow_id": "wf-demo", "task_id": "T-REV",
                "reviewer": "reviewer-agent", "verdict": verdict,
                "blocking_issues": [] if verdict != "rejected" else
                [{"id": "BLK-DEMO", "category": "contract_violation",
                  "description": "injected defect: endpoint returns 501, not 200"}],
                "non_blocking_issues": [], "reviewed_at": "2026-01-01T00:00:00Z"}
    if name == "release_report.json":
        rep = {"spec_version": "v1", "workflow_id": "wf-demo", "environment": "local",
               "artifact_ref": "build", "url": "http://localhost:8080",
               "verdict": "success", "deployed_at": "2026-01-01T00:00:00Z",
               "health_checks": [{"name": "GET /", "status": "pass"}]}
        if not healthy:
            rep["verdict"] = "partial"
            rep["health_checks"] = [{"name": "GET /", "status": "fail"}]
        return rep
    raise ValueError(name)


def _project(tmp: Path) -> Path:
    proj = tmp / "demo-app"
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts" / "workplan.json").write_text(
        json.dumps({"spec_version": "v1", "workflow_id": "wf-demo", "tasks": TASKS}))
    return proj


def _orch(project: Path, runner, **kw) -> Orchestrator:
    clock, ids = count(), count()
    return Orchestrator(
        project, runner, auto_approve=True, schemas_dir=SCHEMAS_DIR,
        now=lambda: datetime.fromtimestamp(next(clock), tz=timezone.utc),
        new_id=lambda: f"id-{next(ids)}", **kw)


def _print_log(project: Path) -> None:
    log = project / "artifacts" / "events.log.jsonl"
    for line in log.read_text().splitlines():
        ev = json.loads(line)
        mark = {"success": "  ok ", "retry": " ↻retry", "blocked": " ⛔block",
                "failure": " ✗fail"}.get(ev["status"], ev["status"])
        issues = ev.get("blocking_issues") or []
        note = f"  ← {issues[0]}" if issues else ""
        print(f"   {mark:8} {ev['stage']:22} {ev['summary'][:48]}{note}")


# --------------------------------------------------------------------------- scenarios

def scenario_rework(tmp: Path) -> bool:
    print("\n● REWORK — reviewer rejects a real defect; engine re-builds autonomously")
    proj = _project(tmp)
    counts = {"dev": 0, "review": 0}

    def fn(task, _root):
        if task["owner_agent"] == "developer-agent":
            counts["dev"] += 1
        for rel in task["outputs"]:
            p = proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == "review_report.json":
                counts["review"] += 1
                v = "rejected" if counts["review"] == 1 else "approved"  # reject round 1
                p.write_text(json.dumps(_artifact("review_report.json", verdict=v)))
            elif rel.endswith(".json"):
                p.write_text(json.dumps(_artifact(p.name)))
            else:
                p.write_text("// code\n")

    state = _orch(proj, CallableRunner(fn)).run()
    _print_log(proj)
    ok = state["current_stage"] == "complete" and counts["dev"] == 2 and counts["review"] == 2
    print(f"   → developer re-ran {counts['dev']}x, reviewer re-ran {counts['review']}x, "
          f"final stage = {state['current_stage']}  [{'RECOVERED' if ok else 'FAILED'}]")
    return ok


def scenario_health(tmp: Path) -> bool:
    print("\n● HEALTH — first deploy is UNHEALTHY; feedback loop re-deploys until healthy")
    proj = _project(tmp)
    counts = {"deploy": 0}

    def fn(task, _root):
        for rel in task["outputs"]:
            p = proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == "release_report.json":
                counts["deploy"] += 1
                healthy = counts["deploy"] > 1  # first deploy unhealthy
                p.write_text(json.dumps(_artifact("release_report.json", healthy=healthy)))
            elif rel.endswith(".json"):
                p.write_text(json.dumps(_artifact(p.name)))
            else:
                p.write_text("// code\n")

    state = _orch(proj, CallableRunner(fn), max_feedback_cycles=1).run()
    _print_log(proj)
    backlog = json.loads((proj / "artifacts" / "backlog.json").read_text()) \
        if (proj / "artifacts" / "backlog.json").exists() else []
    statuses = [b.get("status") for b in backlog]
    ok = state["current_stage"] == "complete" and counts["deploy"] == 2 and "resolved" in statuses
    print(f"   → deployed {counts['deploy']}x, backlog statuses = {statuses}, "
          f"final stage = {state['current_stage']}  [{'RECOVERED' if ok else 'FAILED'}]")
    return ok


def scenario_corrupt(tmp: Path) -> bool:
    print("\n● CORRUPT — an agent emits an invalid artifact; engine retries the task")
    proj = _project(tmp)
    counts = {"qa": 0}

    def fn(task, _root):
        for rel in task["outputs"]:
            p = proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == "test_plan.json":
                counts["qa"] += 1
                if counts["qa"] == 1:
                    p.write_text("{ this is not valid json :(")  # garbled first attempt
                else:
                    p.write_text(json.dumps(_artifact("test_plan.json")))
            elif rel.endswith(".json"):
                p.write_text(json.dumps(_artifact(p.name)))
            else:
                p.write_text("// code\n")

    state = _orch(proj, CallableRunner(fn)).run()
    _print_log(proj)
    ok = state["current_stage"] == "complete" and counts["qa"] == 2
    print(f"   → qa re-ran {counts['qa']}x after the invalid artifact, "
          f"final stage = {state['current_stage']}  [{'RECOVERED' if ok else 'FAILED'}]")
    return ok


SCENARIOS = {"rework": scenario_rework, "health": scenario_health, "corrupt": scenario_corrupt}


def main() -> int:
    pick = sys.argv[1] if len(sys.argv) > 1 else None
    todo = [SCENARIOS[pick]] if pick in SCENARIOS else list(SCENARIOS.values())
    if pick and pick not in SCENARIOS:
        print(f"unknown scenario {pick!r}; choose from {', '.join(SCENARIOS)} (or omit for all)")
        return 2
    print("=" * 78)
    print("  RESILIENCE DEMO — injected failures, autonomous engine recovery (§4.2)")
    print("=" * 78)
    results = []
    with tempfile.TemporaryDirectory() as d:
        for fn in todo:
            results.append(fn(Path(d) / fn.__name__))
    print("\n" + "=" * 78)
    passed = sum(results)
    print(f"  {passed}/{len(results)} scenarios auto-recovered without manual intervention")
    print("=" * 78)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
