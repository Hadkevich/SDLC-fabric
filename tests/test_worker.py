"""Phase-2 tests for the file-on-edge worker (src/watcher/worker.py).

Uses CallableRunner (no LLM) so the adapter is exercised deterministically:
inputs materialize from the DB, the fake agent writes outputs, the worker
validates + ingests them back to the DB, and records the right terminal state.
"""
import json

import pytest

from sdlcdb.db import Database
from orchestrator.runners import CallableRunner, RecoverableError, UnrecoverableError
from watcher.worker import execute_task


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    yield d
    d.close()


def _writer(outputs: dict):
    """A fake agent that writes the given {rel_path: json_obj} outputs."""
    def fn(task, project_root):
        from pathlib import Path
        for rel, obj in outputs.items():
            p = Path(project_root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
        return {"total_cost_usd": 0.01, "usage": {"input_tokens": 5, "output_tokens": 7}}
    return CallableRunner(fn)


def _task(db, pid, **over):
    defaults = dict(agent_role="product-agent", stage="requirement_ingestion",
                    outputs=["artifacts/note.json"])  # schemaless artifact path
    defaults.update(over)
    tid = db.insert_task(pid, defaults.pop("agent_role"), defaults.pop("stage"),
                         **defaults)
    return db.get_task(tid)


def test_success_ingests_artifact_and_removes_local(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    task = _task(db, pid, outputs=["artifacts/note.json"])
    runner = _writer({"artifacts/note.json": {"hello": "world"}})

    res = execute_task(db, task, proj, runner)

    assert res["status"] == "done"
    assert db.get_task(task["task_id"])["status"] == "done"
    art = db.get_artifact(pid, "artifacts/note.json")          # ingested to DB
    assert art and json.loads(art["content"])["hello"] == "world"
    assert not (proj / "artifacts/note.json").exists()         # local copy removed
    ev = db.next_unprocessed_events()[0]                        # terminal success event
    assert ev["status"] == "success" and ev["metrics"]["cost_usd"] == 0.01


def test_inputs_are_materialized_from_db(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    db.put_artifact(pid, "artifacts/requirements.json", {"req": 1})

    seen = {}

    def fn(task, project_root):
        from pathlib import Path
        seen["materialized"] = (Path(project_root) / "artifacts/requirements.json").read_text()
        (Path(project_root) / "artifacts/note.json").write_text("{}")
    task = _task(db, pid, agent_role="planner-agent", stage="task_decomposition",
                 inputs=["artifacts/requirements.json"], outputs=["artifacts/note.json"])

    execute_task(db, task, proj, CallableRunner(fn))
    assert json.loads(seen["materialized"])["req"] == 1


def test_code_output_stays_on_disk(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    task = _task(db, pid, agent_role="developer-agent", stage="code_generation",
                 outputs=["src/main.py"])  # code file, not an artifact
    res = execute_task(db, task, proj, _writer({"src/main.py": "print('hi')"}))
    assert res["status"] == "done"
    assert (proj / "src/main.py").exists()                     # code kept on disk
    assert db.get_artifact(pid, "src/main.py") is None         # not in DB


def test_missing_output_retries_then_fails(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    task = _task(db, pid, max_retries=1, outputs=["artifacts/note.json"])
    runner = _writer({})  # writes nothing -> declared output missing

    r1 = execute_task(db, task, proj, runner)
    assert r1["status"] == "retry"
    t = db.get_task(task["task_id"])
    assert t["status"] == "pending" and t["attempt"] == 1

    r2 = execute_task(db, t, proj, runner)                     # second attempt at cap
    assert r2["status"] == "error"
    assert db.get_task(task["task_id"])["status"] == "error"


def test_schema_invalid_output_is_caught(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    task = _task(db, pid, max_retries=0, outputs=["artifacts/requirements.json"])
    runner = _writer({"artifacts/requirements.json": {}})       # {} fails the schema
    res = execute_task(db, task, proj, runner)
    assert res["status"] == "error"
    assert db.get_artifact(pid, "artifacts/requirements.json") is None  # not ingested


def test_unrecoverable_runner_error_fails_immediately(db, tmp_path):
    pid = db.create_pipeline("x")
    proj = tmp_path / "proj"
    task = _task(db, pid)

    def boom(task, project_root):
        raise UnrecoverableError("unsafe request")
    res = execute_task(db, task, proj, CallableRunner(boom))
    assert res["status"] == "error"
    t = db.get_task(task["task_id"])
    assert t["status"] == "error" and t["payload"]["kind"] == "unrecoverable"
    assert db.next_unprocessed_events()[0]["status"] == "failure"
