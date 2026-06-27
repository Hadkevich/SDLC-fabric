"""Tests for the dashboard exporter (src/watcher/export.py).

Drives a pipeline to completion with the watcher, then checks the exported
snapshot has the dashboard-shaped state (stages folded from tasks) + events.
"""
import json

import pytest

from sdlcdb.db import Database
from watcher.export import build_snapshot, build_index, export_all
from watcher.watcher import Watcher, SyncExecutor
from orchestrator.orchestrator import Orchestrator
# reuse the watcher integration fixtures/helpers
from test_watcher import make_runner, schemas_dir, WORKPLAN  # noqa: F401


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    yield d
    d.close()


def _run_to_complete(db, schemas_dir, tmp_path, name="app"):
    orch = Orchestrator(db, auto_approve=True)
    w = Watcher(db, make_runner(), tmp_path / "projects", concurrency={},
                default_n=1, schemas_dir=schemas_dir, orchestrator=orch,
                executor=SyncExecutor())
    pid = orch.submit("build an app", name=name)
    w.run(stop_when_idle=True, max_ticks=100)
    return pid


def test_snapshot_shape(db, schemas_dir, tmp_path):
    pid = _run_to_complete(db, schemas_dir, tmp_path)
    snap = build_snapshot(db, pid)

    state = snap["state"]
    assert state["workflow_id"] == pid
    assert state["current_stage"] == "complete"
    assert state["project_name"] == "app"
    # stages folded from tasks; the reviewer stage shows success
    assert state["stages"]["code_review"]["status"] == "success"
    assert state["stages"]["code_review"]["agent"] == "reviewer-agent"
    # every event carries the dashboard's expected keys
    assert snap["events"] and all(
        {"agent", "stage", "status", "timestamp"} <= e.keys() for e in snap["events"])
    assert any(e["status"] == "success" for e in snap["events"])


def test_export_all_writes_index_and_snapshots(db, schemas_dir, tmp_path):
    pid = _run_to_complete(db, schemas_dir, tmp_path)
    out = tmp_path / "obs"
    n = export_all(db, out)

    assert n == 1
    index = json.loads((out / "index.json").read_text())
    assert index[0]["pipeline_id"] == pid and index[0]["status"] == "complete"
    snap = json.loads((out / f"{pid}.json").read_text())
    assert snap["state"]["current_stage"] == "complete"


def test_index_reflects_failed_pipeline(db):
    pid = db.create_pipeline("x", name="x")
    db.set_pipeline_status(pid, "failed")
    assert build_index(db)[0]["status"] == "failed"
