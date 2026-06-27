"""SQLite-backed store for the Agentic SDLC control plane.

Single module gating *all* database access so the rest of the system never
touches SQL directly — and a later swap to Postgres stays localized here.

The DB holds three things that used to be files:
  * the **all-tasks table** shared by every agent across every pipeline,
  * inter-agent **JSON artifacts** (DB is the source of truth; project *code*
    files stay on disk via the file-on-edge worker), and
  * an append-only **event log** that the live status is a projection of.

Concurrency: many short-lived worker *processes* plus one watcher process hit the
same file. WAL lets readers run during a single writer's commit; ``busy_timeout``
makes contending writers wait rather than fail; task claims use ``BEGIN
IMMEDIATE`` so exactly one worker wins a row. Each :class:`Database` instance owns
one connection guarded by a re-entrant lock (each process owns its own instance).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Event statuses that mean "this task reached a terminal state — route it".
TERMINAL_EVENT_STATUSES = ("success", "failure", "blocked")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


class Database:
    """Typed accessor over the SQLite control-plane schema.

    ``now``/``new_id`` are injectable so the engine is deterministic under test
    (mirrors the old Orchestrator's seams).
    """

    def __init__(self, path, *, now=None, new_id=None):
        self.path = str(path)
        self._now = now or _utcnow
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    # ------------------------------------------------------------------ lifecycle
    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_PATH.read_text())
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ pipelines
    def create_pipeline(self, request: str, name: str | None = None,
                        pipeline_id: str | None = None) -> str:
        pid = pipeline_id or self._new_id()
        ts = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO pipelines (pipeline_id, name, request, status, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (pid, name, request, "running", ts, ts))
            self._conn.commit()
        return pid

    def set_pipeline_status(self, pipeline_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipelines SET status=?, updated_at=? WHERE pipeline_id=?",
                (status, self._now(), pipeline_id))
            self._conn.commit()

    def get_pipeline(self, pipeline_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipelines WHERE pipeline_id=?", (pipeline_id,)).fetchone()
        return dict(row) if row else None

    def list_pipelines(self, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM pipelines", ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY created_at", args).fetchall()
        return [dict(r) for r in rows]

    def count_active_pipelines(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM pipelines WHERE status IN "
                "('running','awaiting_approval')").fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ approvals
    def approve(self, pipeline_id: str, gate: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO approvals (pipeline_id, gate, approved_at) "
                "VALUES (?,?,?)", (pipeline_id, gate, self._now()))
            self._conn.commit()

    def is_approved(self, pipeline_id: str, gate: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM approvals WHERE pipeline_id=? AND gate=?",
                (pipeline_id, gate)).fetchone()
        return row is not None

    # ------------------------------------------------------------------ tasks
    def insert_task(self, pipeline_id: str, agent_role: str, stage: str, *,
                    title: str = "", inputs=None, outputs=None, depends_on=None,
                    request: str | None = None, healing_prompt: str | None = None,
                    fingerprint: str | None = None, max_retries: int = 3,
                    heal_round: int = 0, status: str = "pending",
                    task_id: str | None = None) -> str:
        tid = task_id or self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, pipeline_id, agent_role, stage, title, "
                "status, attempt, max_retries, heal_round, depends_on, inputs, outputs, "
                "request, healing_prompt, fingerprint, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, pipeline_id, agent_role, stage, title, status, 0, max_retries,
                 heal_round, json.dumps(depends_on or []), json.dumps(inputs or []),
                 json.dumps(outputs or []), request, healing_prompt, fingerprint,
                 self._now()))
            self._conn.commit()
        return tid

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._task_row(row)

    def list_tasks(self, pipeline_id: str | None = None,
                   status: str | None = None, agent_role: str | None = None) -> list[dict]:
        clauses, args = [], []
        for col, val in (("pipeline_id", pipeline_id), ("status", status),
                         ("agent_role", agent_role)):
            if val is not None:
                clauses.append(f"{col}=?")
                args.append(val)
        q = "SELECT * FROM tasks"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY created_at", tuple(args)).fetchall()
        return [self._task_row(r) for r in rows]

    def count_inflight_by_role(self) -> dict:
        """role -> number of claimed/in_progress tasks (the global N/role accounting)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT agent_role, COUNT(*) AS n FROM tasks WHERE status IN "
                "('claimed','in_progress') GROUP BY agent_role").fetchall()
        return {r["agent_role"]: int(r["n"]) for r in rows}

    def claim_next_task(self, agent_role: str, worker_id: str,
                        lease_seconds: int = 1800) -> dict | None:
        """Atomically claim the oldest runnable ``pending`` task for ``agent_role``.

        ``BEGIN IMMEDIATE`` ensures two contending workers can never grab the same
        row (the loser updates zero rows). A task is runnable only when every id in
        ``depends_on`` is ``done``. Returns the claimed task dict or None.
        """
        lease_until = (self._parse(self._now()) + timedelta(seconds=lease_seconds)
                       ).strftime(_TS_FMT)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cand = None
                for row in self._conn.execute(
                        "SELECT * FROM tasks WHERE agent_role=? AND status='pending' "
                        "ORDER BY created_at", (agent_role,)).fetchall():
                    if self._all_done(json.loads(row["depends_on"] or "[]")):
                        cand = row
                        break
                if cand is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    "UPDATE tasks SET status='claimed', claimed_by=?, claimed_at=?, "
                    "lease_until=? WHERE task_id=? AND status='pending'",
                    (worker_id, self._now(), lease_until, cand["task_id"]))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.get_task(cand["task_id"])

    def _all_done(self, dep_ids) -> bool:
        if not dep_ids:
            return True
        placeholders = ",".join("?" * len(dep_ids))
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM tasks WHERE task_id IN ({placeholders}) "
            f"AND status='done'", tuple(dep_ids)).fetchone()
        return int(row["n"]) == len(dep_ids)

    def update_task(self, task_id: str, **fields) -> None:
        """Generic task update; JSON-encodes list/dict values."""
        if not fields:
            return
        cols, args = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        args.append(task_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(cols)} WHERE task_id=?", tuple(args))
            self._conn.commit()

    def requeue_expired_leases(self) -> int:
        """Reset claimed/in_progress tasks whose lease expired (a dead worker) back
        to ``pending`` so the watcher re-dispatches them. Idempotent recovery."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='pending', claimed_by=NULL, lease_until=NULL "
                "WHERE status IN ('claimed','in_progress') AND lease_until IS NOT NULL "
                "AND lease_until < ?", (self._now(),))
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _task_row(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in ("depends_on", "inputs", "outputs"):
            d[k] = json.loads(d.get(k) or "[]")
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    # ------------------------------------------------------------------ artifacts
    def put_artifact(self, pipeline_id: str, name: str, content, *,
                     producer_task_id: str | None = None,
                     schema_name: str | None = None) -> str:
        """Insert/replace the artifact for (pipeline, name). DB = source of truth."""
        text = content if isinstance(content, str) else json.dumps(content, indent=2)
        aid = self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO artifacts (artifact_id, pipeline_id, producer_task_id, "
                "name, content, schema_name, content_hash, created_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(pipeline_id, name) DO UPDATE SET "
                "content=excluded.content, producer_task_id=excluded.producer_task_id, "
                "schema_name=excluded.schema_name, content_hash=excluded.content_hash, "
                "created_at=excluded.created_at",
                (aid, pipeline_id, producer_task_id, name, text, schema_name,
                 sha256_text(text), self._now()))
            self._conn.commit()
        return aid

    def get_artifact(self, pipeline_id: str, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE pipeline_id=? AND name=?",
                (pipeline_id, name)).fetchone()
        return dict(row) if row else None

    def delete_artifact(self, pipeline_id: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM artifacts WHERE pipeline_id=? AND name=?",
                (pipeline_id, name))
            self._conn.commit()

    def list_artifacts(self, pipeline_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifacts WHERE pipeline_id=? ORDER BY created_at",
                (pipeline_id,)).fetchall()
        return [dict(r) for r in rows]

    def artifact_hashes(self, pipeline_id: str) -> dict:
        """name -> content_hash, for fingerprinting / skip-unchanged."""
        return {a["name"]: a["content_hash"] for a in self.list_artifacts(pipeline_id)}

    # ------------------------------------------------------------------ events
    def append_event(self, pipeline_id: str, stage: str, agent: str, status: str, *,
                     task_id: str | None = None, summary: str = "",
                     blocking_issues=None, input_refs=None, output_refs=None,
                     metrics=None, retry_count: int = 0) -> str:
        """Append one immutable event. The engine stamps event_id + timestamp so the
        audit log can't be fabricated. Returns the event_id."""
        eid = self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_id, pipeline_id, task_id, stage, agent, "
                "status, summary, blocking_issues, input_refs, output_refs, metrics, "
                "retry_count, timestamp, processed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (eid, pipeline_id, task_id, stage, agent, status, summary,
                 json.dumps(blocking_issues or []), json.dumps(input_refs or []),
                 json.dumps(output_refs or []),
                 json.dumps(metrics) if metrics else None, retry_count, self._now()))
            self._conn.commit()
        return eid

    def next_unprocessed_events(self, limit: int = 50) -> list[dict]:
        """Terminal events not yet routed by the orchestrator (the watcher trigger)."""
        placeholders = ",".join("?" * len(TERMINAL_EVENT_STATUSES))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM events WHERE processed_at IS NULL AND status IN "
                f"({placeholders}) ORDER BY timestamp LIMIT ?",
                (*TERMINAL_EVENT_STATUSES, limit)).fetchall()
        return [self._event_row(r) for r in rows]

    def list_events(self, pipeline_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE pipeline_id=? ORDER BY timestamp",
                (pipeline_id,)).fetchall()
        return [self._event_row(r) for r in rows]

    def mark_event_processed(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET processed_at=? WHERE event_id=?",
                (self._now(), event_id))
            self._conn.commit()

    def total_cost(self, pipeline_id: str | None = None) -> float:
        """Fold total USD cost from event metrics (survives restarts)."""
        q, args = "SELECT metrics FROM events WHERE metrics IS NOT NULL", ()
        if pipeline_id:
            q += " AND pipeline_id=?"
            args = (pipeline_id,)
        total = 0.0
        with self._lock:
            for row in self._conn.execute(q, args).fetchall():
                try:
                    cost = (json.loads(row["metrics"]) or {}).get("cost_usd")
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(cost, (int, float)):
                    total += cost
        return total

    @staticmethod
    def _event_row(row) -> dict:
        d = dict(row)
        for k in ("blocking_issues", "input_refs", "output_refs"):
            d[k] = json.loads(d.get(k) or "[]")
        if d.get("metrics"):
            try:
                d["metrics"] = json.loads(d["metrics"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    # ------------------------------------------------------------------ projection
    def fold_state(self, pipeline_id: str) -> dict:
        """Rebuild a pipeline's status projection purely from the event log — the
        recovery/verification path proving the log is the source of truth (a task's
        status is the status of its latest event)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, status, stage, agent, timestamp FROM events "
                "WHERE pipeline_id=? ORDER BY timestamp", (pipeline_id,)).fetchall()
        tasks: dict[str, dict] = {}
        for r in rows:
            if r["task_id"]:
                tasks[r["task_id"]] = {
                    "status": r["status"], "stage": r["stage"],
                    "agent": r["agent"], "at": r["timestamp"]}
        return {"pipeline_id": pipeline_id, "tasks": tasks}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _parse(ts: str) -> datetime:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
