"""The Watcher — poll loop + worker supervisor (one process, the control loop).

Each tick (every 5–10s in prod) it:
  1. **requeues** tasks whose worker lease expired (a dead worker),
  2. **routes** every new terminal event through the Orchestrator (which grows the
     pipeline / applies gates / heals), then
  3. **dispatches** runnable ``pending`` tasks to agent workers, respecting a
     **global N-per-role** ceiling across all pipelines, and
  4. **reaps** finished workers.

Workers run on an injected executor (a real ``ThreadPoolExecutor`` in prod; an
inline executor in tests) — the heavy work is the agent subprocess the runner
spawns, so threads give genuine overlap. Many pipelines advance concurrently
against the one shared tasks table; ``submit`` enforces ``max_pipelines``.
"""
from __future__ import annotations

import itertools
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from orchestrator.lifecycle import LIFECYCLE
from orchestrator.orchestrator import Orchestrator
from .worker import DEFAULT_SCHEMAS_DIR, execute_task

# The agent roles the watcher dispatches (orchestrator/evaluator are not queued).
AGENT_ROLES = [agent for _, agent in LIFECYCLE]


class SyncExecutor:
    """Runs each submitted callable inline and returns a completed Future. Used in
    tests so a tick is fully deterministic (no thread timing)."""

    def submit(self, fn, *args, **kwargs):
        fut: Future = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as e:  # surface in the future like a real executor
            fut.set_exception(e)
        return fut

    def shutdown(self, wait=True):
        pass


class Watcher:
    def __init__(self, db, runner, projects_root, *, concurrency=None,
                 default_n: int = 1, schemas_dir=DEFAULT_SCHEMAS_DIR,
                 tick: float = 5.0, lease_seconds: int = 1800,
                 orchestrator=None, executor=None, sleep=None):
        self.db = db
        self.runner = runner
        self.projects_root = Path(projects_root)
        self.concurrency = dict(concurrency or {})
        self.default_n = default_n
        self.schemas_dir = schemas_dir
        self.tick = tick
        self.lease_seconds = lease_seconds
        self.orch = orchestrator or Orchestrator(db)
        self.executor = executor or ThreadPoolExecutor(
            max_workers=max(1, sum(self.concurrency.values()) or len(AGENT_ROLES)))
        self._sleep = sleep or time.sleep
        self._futures: list[Future] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ one tick
    def tick_once(self) -> dict:
        self.db.requeue_expired_leases()
        routed = self._route()
        spawned = self._dispatch()
        self._reap()
        return {"routed": routed, "spawned": spawned}

    def _route(self) -> int:
        events = self.db.next_unprocessed_events()
        for ev in events:
            self.orch.route(ev)
        return len(events)

    def _dispatch(self) -> int:
        spawned = 0
        inflight = self.db.count_inflight_by_role()
        for role in AGENT_ROLES:
            slots = self.concurrency.get(role, self.default_n) - inflight.get(role, 0)
            while slots > 0:
                task = self.db.claim_next_task(
                    role, worker_id=f"w{next(self._ids)}",
                    lease_seconds=self.lease_seconds)
                if task is None:
                    break
                self._spawn(task)
                spawned += 1
                slots -= 1
        return spawned

    def _spawn(self, task: dict) -> None:
        pipeline = self.db.get_pipeline(task["pipeline_id"])
        proj = self.projects_root / (pipeline.get("name") or task["pipeline_id"])
        proj.mkdir(parents=True, exist_ok=True)
        self._futures.append(self.executor.submit(
            execute_task, self.db, task, proj, self.runner,
            schemas_dir=self.schemas_dir, worker_id=task["claimed_by"]))

    def _reap(self) -> None:
        still = []
        for f in self._futures:
            if f.done():
                f.result()  # re-raise unexpected worker crashes (not agent failures)
            else:
                still.append(f)
        self._futures = still

    # ------------------------------------------------------------------ run loop
    def run(self, *, stop_when_idle: bool = False, max_ticks: int | None = None) -> int:
        ticks = 0
        while True:
            self.tick_once()
            ticks += 1
            if stop_when_idle and self._idle():
                break
            if max_ticks is not None and ticks >= max_ticks:
                break
            if not stop_when_idle:
                self._sleep(self.tick)
        return ticks

    def _idle(self) -> bool:
        """True when nothing is left to do anywhere: no unrouted events, no live
        workers, no dispatchable task. (Tasks parked in awaiting_approval/blocked
        are not 'active' — the loop yields so a human/operator can act.)"""
        if self.db.next_unprocessed_events():
            return False
        if any(not f.done() for f in self._futures):
            return False
        for st in ("pending", "claimed", "in_progress"):
            if self.db.list_tasks(status=st):
                return False
        return True
