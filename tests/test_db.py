"""Phase-1 tests for the SQLite control-plane store (src/sdlcdb/db.py).

Covers the properties the rest of the system depends on:
  * atomic claim — concurrent workers never grab the same task,
  * dependency gating — a task is only claimable when its deps are `done`,
  * global-N accounting — in-flight counts per role,
  * lease expiry requeues a dead worker's task,
  * artifact upsert keeps the DB the single source of truth,
  * the event log folds back into a status projection.
"""
import threading

import pytest

from sdlcdb.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    yield d
    d.close()


def test_pipeline_and_task_roundtrip(db):
    pid = db.create_pipeline("build a todo app", name="todo")
    assert db.get_pipeline(pid)["status"] == "running"
    tid = db.insert_task(pid, "product-agent", "requirement_ingestion",
                         outputs=["requirements.json"])
    t = db.get_task(tid)
    assert t["status"] == "pending"
    assert t["outputs"] == ["requirements.json"]
    assert db.list_tasks(pipeline_id=pid)[0]["task_id"] == tid


def test_claim_is_atomic_under_concurrency(db):
    """100 threads race to claim 10 tasks; each task is claimed exactly once."""
    pid = db.create_pipeline("x")
    for _ in range(10):
        db.insert_task(pid, "developer-agent", "code_generation")

    claimed: list[str] = []
    lock = threading.Lock()

    def worker(i):
        t = db.claim_next_task("developer-agent", worker_id=f"w{i}")
        if t:
            with lock:
                claimed.append(t["task_id"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 10
    assert len(set(claimed)) == 10  # no task claimed twice
    assert db.count_inflight_by_role()["developer-agent"] == 10


def test_claim_respects_dependencies(db):
    pid = db.create_pipeline("x")
    up = db.insert_task(pid, "developer-agent", "code_generation")
    down = db.insert_task(pid, "reviewer-agent", "code_review", depends_on=[up])

    assert db.claim_next_task("reviewer-agent", "w1") is None  # blocked on dep
    db.update_task(up, status="done")
    claimed = db.claim_next_task("reviewer-agent", "w1")
    assert claimed and claimed["task_id"] == down


def test_expired_lease_is_requeued(db):
    pid = db.create_pipeline("x")
    tid = db.insert_task(pid, "qa-agent", "testing_validation")
    db.claim_next_task("qa-agent", "w1", lease_seconds=-1)  # already-expired lease
    assert db.get_task(tid)["status"] == "claimed"
    assert db.requeue_expired_leases() == 1
    assert db.get_task(tid)["status"] == "pending"


def test_artifact_upsert_keeps_latest(db):
    pid = db.create_pipeline("x")
    db.put_artifact(pid, "requirements.json", {"v": 1})
    db.put_artifact(pid, "requirements.json", {"v": 2})  # overwrite
    art = db.get_artifact(pid, "requirements.json")
    assert '"v": 2' in art["content"]
    assert len(db.list_artifacts(pid)) == 1               # one row per (pipeline, name)
    assert "requirements.json" in db.artifact_hashes(pid)
    db.delete_artifact(pid, "requirements.json")
    assert db.get_artifact(pid, "requirements.json") is None


def test_approvals(db):
    pid = db.create_pipeline("x")
    assert not db.is_approved(pid, "architecture")
    db.approve(pid, "architecture")
    db.approve(pid, "architecture")  # idempotent
    assert db.is_approved(pid, "architecture")


def test_events_drive_trigger_and_fold(db):
    pid = db.create_pipeline("x")
    tid = db.insert_task(pid, "product-agent", "requirement_ingestion")
    db.append_event(pid, "requirement_ingestion", "product-agent", "success",
                    task_id=tid, summary="done", metrics={"cost_usd": 0.5})
    db.append_event(pid, "code_review", "reviewer-agent", "retry", task_id=tid)  # non-terminal

    pending = db.next_unprocessed_events()
    assert len(pending) == 1  # only the terminal 'success' event triggers routing
    db.mark_event_processed(pending[0]["event_id"])
    assert db.next_unprocessed_events() == []

    assert db.total_cost(pid) == pytest.approx(0.5)
    assert db.fold_state(pid)["tasks"][tid]["status"] == "retry"  # latest event wins
