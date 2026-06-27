"""Phase-4 integration tests for the watcher loop (src/watcher/watcher.py).

No LLM: a CallableRunner writes role-appropriate artifacts and the worker ingests
them. Drives a full pipeline to `complete`, runs two pipelines concurrently, and
checks the global N-per-role dispatch ceiling.
"""
import json
from pathlib import Path

import pytest

from sdlcdb.db import Database
from orchestrator.runners import CallableRunner
from orchestrator.orchestrator import Orchestrator
from watcher.watcher import Watcher, SyncExecutor

WORKPLAN = {"spec_version": "v1", "tasks": [
    {"task_id": "A", "owner_agent": "architect-agent",
     "outputs": ["artifacts/architecture.json"], "depends_on": []},
    {"task_id": "D", "owner_agent": "developer-agent",
     "outputs": ["src/main.py"], "depends_on": ["A"]},
    {"task_id": "R", "owner_agent": "reviewer-agent",
     "outputs": ["artifacts/review_report.json"], "depends_on": ["D"]},
    {"task_id": "Q", "owner_agent": "qa-agent",
     "outputs": ["artifacts/test_plan.json"], "depends_on": ["R"]},
    {"task_id": "DP", "owner_agent": "devops-agent",
     "outputs": ["artifacts/release_report.json"], "depends_on": ["R", "Q"]},
    {"task_id": "E", "owner_agent": "e2e-agent",
     "outputs": ["artifacts/e2e_report.json"], "depends_on": ["DP"]},
]}

PRODUCE = {
    "product-agent": {"artifacts/requirements.json": {"spec_version": "v1"}},
    "planner-agent": {"artifacts/workplan.json": WORKPLAN},
    "architect-agent": {"artifacts/architecture.json": {"spec_version": "v1"}},
    "reviewer-agent": {"artifacts/review_report.json": {"verdict": "approved"}},
    "qa-agent": {"artifacts/test_plan.json": {"summary": {"failed": 0}}},
    "devops-agent": {"artifacts/release_report.json": {"verdict": "success"}},
    "e2e-agent": {"artifacts/e2e_report.json":
                  {"verdict": "passed", "summary": {"failed": 0}}},
}

_SCHEMA_FILES = ["requirements", "workplan", "architecture", "api-contracts",
                 "data-model", "code_spec", "review_report", "test_plan",
                 "release_report", "e2e_report", "adr"]


@pytest.fixture
def schemas_dir(tmp_path):
    """Permissive schemas (accept-anything) so the worker's validation is exercised
    without hand-crafting fully valid artifacts."""
    d = tmp_path / "schemas"
    d.mkdir()
    for name in _SCHEMA_FILES:
        (d / f"{name}.schema.json").write_text("{}")
    return d


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    yield d
    d.close()


def make_runner():
    def fn(task, project_root):
        written = set()
        for rel, content in PRODUCE.get(task["owner_agent"], {}).items():
            p = Path(project_root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(content))
            written.add(rel)
        for rel in task["outputs"]:
            if rel not in written:                       # code outputs (src/…)
                p = Path(project_root) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("// code")
        return {"total_cost_usd": 0.001}
    return CallableRunner(fn)


def _watcher(db, schemas_dir, projects_root, **over):
    orch = Orchestrator(db, auto_approve=True)
    kw = dict(concurrency={}, default_n=1, schemas_dir=schemas_dir,
              orchestrator=orch, executor=SyncExecutor())
    kw.update(over)
    return Watcher(db, make_runner(), projects_root, **kw), orch


def test_full_pipeline_completes(db, schemas_dir, tmp_path):
    w, orch = _watcher(db, schemas_dir, tmp_path / "projects")
    pid = orch.submit("build an app", name="app")

    w.run(stop_when_idle=True, max_ticks=100)

    assert db.get_pipeline(pid)["status"] == "complete"
    assert db.get_artifact(pid, "artifacts/requirements.json") is not None  # in DB
    assert (tmp_path / "projects/app/src/main.py").exists()                 # code on disk
    assert not (tmp_path / "projects/app/artifacts/requirements.json").exists()  # JSON not on disk
    assert db.total_cost(pid) > 0                                           # metrics folded


def test_two_pipelines_run_concurrently(db, schemas_dir, tmp_path):
    w, orch = _watcher(db, schemas_dir, tmp_path / "projects")
    p1 = orch.submit("app one", name="one")
    p2 = orch.submit("app two", name="two")

    w.run(stop_when_idle=True, max_ticks=200)

    assert db.get_pipeline(p1)["status"] == "complete"
    assert db.get_pipeline(p2)["status"] == "complete"
    assert (tmp_path / "projects/one/src/main.py").exists()
    assert (tmp_path / "projects/two/src/main.py").exists()


class _ParkingExecutor:
    """Never completes a worker — so claimed tasks stay in-flight, letting us assert
    the global N-per-role ceiling holds."""

    def submit(self, fn, *a, **k):
        from concurrent.futures import Future
        return Future()  # never resolved

    def shutdown(self, wait=True):
        pass


def test_global_n_per_role_ceiling(db, schemas_dir, tmp_path):
    pid = db.create_pipeline("x", name="x")
    for _ in range(5):                                   # 5 runnable developer tasks
        db.insert_task(pid, "developer-agent", "code_generation")
    w, _ = _watcher(db, schemas_dir, tmp_path / "projects",
                    concurrency={"developer-agent": 2}, executor=_ParkingExecutor())

    w.tick_once()

    inflight = db.count_inflight_by_role()
    assert inflight["developer-agent"] == 2              # only N claimed at once
    assert len(db.list_tasks(pipeline_id=pid, status="pending")) == 3
