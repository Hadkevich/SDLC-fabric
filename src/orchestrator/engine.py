"""The Orchestrator — deterministic control plane for the Agentic SDLC.

Implements the SPEC §8 contract: it owns *control flow only* and drives
``artifacts/workflow_state.json`` from the first task to ``complete`` (or
``failed``). It schedules tasks off the workplan DAG, invokes a Runner per task,
mechanically validates outputs + evaluates stage gates, classifies failures into
retry-vs-escalate, honours human checkpoints, stamps an immutable event per
transition, and persists state atomically after every step.

The engine never inspects an artifact's *meaning* with judgment — gates are pure
predicates over schemas and a couple of numeric/enum checks (SPEC §8.2).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .runners import RecoverableError, UnrecoverableError
from .validation import schema_for_output, validate_artifact, scan_source

DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"

# Linear lifecycle order (SPEC §3) — used to pick the earliest stage to resume from.
STAGE_SEQUENCE = [
    "requirement_ingestion", "task_decomposition", "planning_architecture",
    "code_generation", "code_review", "testing_validation", "deployment",
    "e2e_validation", "monitoring_feedback",
]

# Which lifecycle stage each agent's task belongs to.
AGENT_STAGE = {
    "product-agent": "requirement_ingestion",
    "planner-agent": "task_decomposition",
    "architect-agent": "planning_architecture",
    "developer-agent": "code_generation",
    "reviewer-agent": "code_review",
    "qa-agent": "testing_validation",
    "devops-agent": "deployment",
    "e2e-agent": "e2e_validation",
    "orchestrator-agent": "monitoring_feedback",
}

# Per-stage rework cap override (SPEC §8.3). Stages not listed use ``max_rework``.
# An e2e_validation failure runs against the *deployed* app, so a full re-dispatch
# of the developer subtree (re-dev → re-deploy → re-E2E, re-firing the production
# deploy checkpoint) is expensive — cap it at a single round before escalating.
STAGE_REWORK_CAP = {
    "e2e_validation": 1,
}

# Mandatory human sign-offs before entering a stage (SPEC §8.6).
HUMAN_GATES = {
    "task_decomposition": "requirements",
    "code_generation": "architecture",
    "deployment": "production_deploy",
}

_APPROVED_VERDICTS = {"approved", "approved_with_comments"}
_E2E_PASS_VERDICTS = {"passed", "passed_with_warnings"}

# The linear "prelude" stages that run BEFORE a workplan DAG exists. They are not
# tasks in the DAG — they *produce* it (requirements → workplan). Modelled as
# synthetic tasks so they reuse the exact same run/validate/retry/gate machinery
# as DAG tasks (SPEC §3.1–3.2). depends_on chains them in order.
#
# Architecture is intentionally NOT here: the planner naturally emits an
# architect task as the first node of the workplan (SPEC §3.3), so the architect
# runs once, in the DAG. The architecture human checkpoint still fires before the
# first code_generation task (see HUMAN_GATES), i.e. after the architect runs.
PRELUDE_TASKS = [
    {"task_id": "STAGE-REQUIREMENTS",
     "title": "Normalize the user request into structured requirements",
     "owner_agent": "product-agent", "inputs": [],
     "outputs": ["artifacts/requirements.json", "artifacts/requirements.md"],
     "depends_on": [], "done_criteria": ["requirements.json validates against schema"]},
    {"task_id": "STAGE-PLAN",
     "title": "Decompose requirements into a dependency-ordered workplan",
     "owner_agent": "planner-agent", "inputs": ["artifacts/requirements.json"],
     "outputs": ["artifacts/workplan.json"],
     "depends_on": ["STAGE-REQUIREMENTS"],
     "done_criteria": ["workplan.json validates; dependencies form a DAG"]},
]


class Escalation(Exception):
    """Raised internally when control must hand off to a human (e.g. a workplan
    cycle). The engine records it as a blocked event + failed state rather than
    propagating, so callers always get a persisted state back."""

    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or [message]


class Orchestrator:
    """Drive one workflow run/resume to a terminal state.

    Parameters mirror the SPEC contract; ``now``/``new_id``/``sleep``/``backoff``
    are injectable so the control plane is fully deterministic under test.
    """

    def __init__(self, project, runner, *, auto_approve: bool = False,
                 approvals=None, schemas_dir=None, max_retries: int = 3,
                 max_rework: int = 2, max_parallel: int = 4,
                 max_cost_usd: float | None = None,
                 now=None, new_id=None, sleep=None, backoff=None):
        self.project = Path(project)
        self.runner = runner
        self.auto_approve = auto_approve
        self.approvals = set(approvals or ())
        self.schemas_dir = Path(schemas_dir) if schemas_dir else DEFAULT_SCHEMAS_DIR
        self.max_retries = max_retries
        # Bounded review->fix rework rounds before a rejected review escalates
        # (SPEC §8.3). Each round re-dispatches the upstream developer task(s).
        self.max_rework = max(0, max_rework)
        # Run-level cost ceiling (SPEC §9 / ENG-8). When the cumulative cost folded
        # from events.log.jsonl reaches this, the breaker halts new dispatch. None
        # disables the check (the per-task timeout is the only other bound).
        self.max_cost_usd = max_cost_usd
        # Reviewer task_ids whose review came back `rejected`; drained after each
        # wave joins, so the developer subtree is reset *outside* a running wave.
        self._rework_requests: set[str] = set()
        # How many independent DAG tasks may run at once (SPEC §8.5). Each task
        # runs in its own thread; the slow work (the agent subprocess) happens
        # outside the state lock, so tasks genuinely overlap.
        self.max_parallel = max(1, max_parallel)
        self._lock = threading.RLock()  # serializes state mutation + file writes
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._sleep = sleep or time.sleep
        self._backoff = backoff or (lambda attempt: 0)

        self.artifacts = self.project / "artifacts"
        self.state_path = self.artifacts / "workflow_state.json"
        self.events_path = self.artifacts / "events.log.jsonl"
        self.workplan_path = self.artifacts / "workplan.json"

    # ------------------------------------------------------------------ helpers
    def _ts(self) -> str:
        return self._now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _approved(self, gate: str) -> bool:
        return self.auto_approve or gate in self.approvals

    # ------------------------------------------------------------------ state IO
    def _load_workplan(self) -> dict:
        if not self.workplan_path.exists():
            raise Escalation("workplan.json missing — nothing to schedule")
        return json.loads(self.workplan_path.read_text())

    def _load_or_init_state(self, workplan: dict) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        ts = self._ts()
        return {
            "spec_version": "v1",
            "workflow_id": workplan.get("workflow_id") or self._new_id(),
            "current_stage": "requirement_ingestion",
            "stages": {},
            "tasks": {},
            "halted": False,
            "max_retries": self.max_retries,
            "max_rework": self.max_rework,
            "created_at": ts,
            "updated_at": ts,
        }

    def _persist(self, state: dict) -> None:
        """Atomic write: temp file + rename (SPEC §8.1) so a crash can never leave
        a half-written state file. Lock-guarded so concurrent tasks (the parallel
        wave) can't race on the shared temp path or interleave the state snapshot."""
        with self._lock:
            state["updated_at"] = self._ts()
            self.artifacts.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(self.state_path)

    def _event(self, state, stage, agent, status, task=None, *, summary="",
               blocking_issues=None, retry_count=0, metrics=None) -> dict:
        """Append exactly one immutable event. The engine — not the agent — stamps
        ``event_id`` and ``timestamp`` so the audit log cannot be fabricated
        (SPEC §8.4). ``metrics`` (cost/tokens/duration), when the runner reports
        them, is attached so observability can total resource spend."""
        ev = {
            "event_id": self._new_id(),
            "workflow_id": state["workflow_id"],
            "stage": stage,
            "agent": agent,
            "status": status,
            "input_refs": (task or {}).get("inputs", []),
            "output_refs": (task or {}).get("outputs", []),
            "summary": summary,
            "blocking_issues": blocking_issues or [],
            "retry_count": retry_count,
            "timestamp": self._ts(),
        }
        if metrics:
            ev["metrics"] = metrics
        with self._lock:  # serialize appends so concurrent tasks can't interleave lines
            self.artifacts.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a") as f:
                f.write(json.dumps(ev) + "\n")
        return ev

    def _total_cost(self) -> float:
        """Fold the total USD cost from the event log (metrics.cost_usd). The event
        log is the source of truth, so the breaker survives a resume (ENG-8)."""
        if not self.events_path.exists():
            return 0.0
        total = 0.0
        for line in self.events_path.read_text().splitlines():
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            cost = (ev.get("metrics") or {}).get("cost_usd")
            if isinstance(cost, (int, float)):
                total += cost
        return total

    def _over_budget(self) -> bool:
        return self.max_cost_usd is not None and self._total_cost() >= self.max_cost_usd

    @staticmethod
    def _extract_metrics(result):
        """Pull cost / token / duration figures out of an agent runner's return
        value (the ``claude -p --output-format json`` envelope). Returns None when
        the runner reports nothing (e.g. CallableRunner/ReplayRunner in tests)."""
        if not isinstance(result, dict):
            return None
        usage = result.get("usage") or {}
        inp = usage.get("input_tokens")
        out = usage.get("output_tokens")
        cache_create = usage.get("cache_creation_input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        cost = result.get("total_cost_usd")
        dur = result.get("duration_ms")
        if inp is None and out is None and cost is None and dur is None:
            return None
        in_total = (inp or 0) + cache_create + cache_read
        out_total = out or 0
        return {
            "input_tokens": in_total,
            "output_tokens": out_total,
            "total_tokens": in_total + out_total,
            "cost_usd": cost,
            "duration_ms": dur,
        }

    def _ensure_task_states(self, state, tasks) -> None:
        state.setdefault("tasks", {})
        state.setdefault("stages", {})
        for t in tasks:
            tid = t["task_id"]
            if tid not in state["tasks"]:
                state["tasks"][tid] = {
                    "status": "pending",
                    "attempt": 0,
                    "rework": 0,
                    "owner_agent": t["owner_agent"],
                    "stage": AGENT_STAGE.get(t["owner_agent"], "code_generation"),
                    "depends_on": list(t.get("depends_on", [])),
                }

    def _mark_stage(self, state, stage, status, *, agent=None, attempt=0, **extra):
        st = state.setdefault("stages", {}).setdefault(stage, {"status": "pending", "attempt": 0})
        st["status"] = status
        st["attempt"] = attempt
        if agent:
            st["agent"] = agent
        for k, v in extra.items():
            st[k] = v

    # ------------------------------------------------------------------ scheduling
    @staticmethod
    def _topo_order(tasks) -> list[str]:
        """Kahn topological sort over ``depends_on``; raises Escalation on a cycle.
        Ties preserve workplan order for determinism."""
        ids = [t["task_id"] for t in tasks]
        deps = {t["task_id"]: list(t.get("depends_on", [])) for t in tasks}
        indeg = {i: sum(1 for d in deps[i] if d in deps) for i in ids}
        queue = [i for i in ids if indeg[i] == 0]
        order: list[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for i in ids:
                if n in deps[i]:
                    indeg[i] -= 1
                    if indeg[i] == 0:
                        queue.append(i)
        if len(order) != len(ids):
            stuck = [i for i in ids if i not in order]
            raise Escalation("dependency cycle in workplan", [f"cycle among tasks: {stuck}"])
        return order

    # ------------------------------------------------------------------ run loop
    def run_from_prompt(self, request: str) -> dict:
        """Full pipeline from a raw user request (SPEC §3.1 → §3.7).

        Runs the linear prelude (product → planner → architect) to *produce* the
        workplan DAG, then hands off to the DAG scheduler in :meth:`run`. Safe to
        re-invoke: finished prelude stages are skipped, so this resumes a run that
        paused at a human checkpoint.
        """
        wp = self._load_workplan() if self.workplan_path.exists() else {}
        state = self._load_or_init_state(wp)
        self._ensure_task_states(state, PRELUDE_TASKS)
        if state.get("halted"):
            return state
        self._persist(state)

        outcome = self._run_prelude(request, state)
        if outcome == "paused":
            return state
        if outcome == "failed":
            return self._finalize_failed(state)
        # prelude done → workplan.json now exists → run the DAG phase
        return self.run()

    def _run_prelude(self, request, state) -> str:
        """Run the three pre-DAG stages in order. Returns 'ok'|'paused'|'failed'."""
        for spec in PRELUDE_TASKS:
            task = dict(spec)
            if task["owner_agent"] == "product-agent":
                task["request"] = request  # the raw prompt is the product agent's input
            ts = state["tasks"][task["task_id"]]
            if ts["status"] == "success":
                continue  # idempotent resume
            outcome = self._dispatch(state, task, AGENT_STAGE[task["owner_agent"]])
            if outcome != "ok":
                return outcome
        return "ok"

    def unblock(self, task_ids=None) -> int:
        """Operator-driven recovery: reset blocked/stuck tasks to ``pending`` and
        clear the halt flag so the workflow can be re-dispatched. With no
        ``task_ids``, resets every blocked task. Returns how many were reset.

        Use after fixing the cause of a failure (a bug, a hit rate/session limit,
        a transient tool error). A retry runs the task fresh — the agent is
        stochastic, so a re-run often succeeds; for systematic failures, fix the
        input first. Resetting attempt to 0 gives the task a full retry budget."""
        if not self.state_path.exists():
            return 0
        state = json.loads(self.state_path.read_text())
        tasks = state.get("tasks", {})
        targets = list(task_ids) if task_ids else [
            tid for tid, t in tasks.items() if t.get("status") == "blocked"]
        n = 0
        for tid in targets:
            t = tasks.get(tid)
            if t and t.get("status") in ("blocked", "failure", "in_progress"):
                t["status"] = "pending"
                t["attempt"] = 0
                for k in ("blocking_issues", "started_at", "completed_at"):
                    t.pop(k, None)
                n += 1
        if n:
            state["halted"] = False
            if state.get("current_stage") == "failed":
                # resume at the earliest reset stage (run() then recomputes precisely)
                ranked = [tasks[tid].get("stage") for tid in targets
                          if tid in tasks and tasks[tid].get("stage") in STAGE_SEQUENCE]
                state["current_stage"] = (min(ranked, key=STAGE_SEQUENCE.index)
                                          if ranked else "code_generation")
            self._event(state, "monitoring_feedback", "orchestrator-agent", "retry",
                        summary=f"operator reset {n} task(s) for retry: "
                                f"{', '.join(targets)}", retry_count=0)
            self._persist(state)
        return n

    def run(self) -> dict:
        try:
            workplan = self._load_workplan()
        except Escalation as e:
            return self._abort(self._minimal_failed_state(), "task_decomposition", e)

        tasks = workplan["tasks"]
        state = self._load_or_init_state(workplan)
        self._ensure_task_states(state, tasks)

        if state.get("halted"):
            return state  # kill switch already tripped; dispatch nothing

        task_by_id = {t["task_id"]: t for t in tasks}

        try:
            order = self._topo_order(tasks)
        except Escalation as e:
            return self._abort(state, "task_decomposition", e)

        # Wave scheduler (SPEC §8.5). Each iteration selects every task whose
        # dependencies are all `success` and dispatches the whole set concurrently.
        # Independent tasks (e.g. parallel developer-agent tasks, or reviewer + QA
        # on the same finished code) overlap; dependent tasks fall to a later wave.
        # Topo order only breaks ties, keeping selection deterministic.
        while True:
            if any(state["tasks"][tid]["status"] == "blocked" for tid in order):
                return self._finalize_failed(state)  # an upstream task escalated

            if self._over_budget():  # cost breaker (ENG-8) — halt before new spend
                spent = self._total_cost()
                self._event(state, state.get("current_stage", "deployment"),
                            "orchestrator-agent", "blocked",
                            summary=f"cost budget ${self.max_cost_usd:.2f} reached "
                                    f"(spent ${spent:.2f}) — halting new dispatch",
                            blocking_issues=[f"cost budget ${self.max_cost_usd:.2f} "
                                             f"exceeded at ${spent:.2f}"])
                return self._finalize_failed(state)

            remaining = [tid for tid in order
                         if state["tasks"][tid]["status"] != "success"]
            if not remaining:
                break  # every task done

            runnable = [tid for tid in remaining
                        if all(state["tasks"].get(d, {}).get("status") == "success"
                               for d in state["tasks"][tid].get("depends_on", []))]
            if not runnable:
                # remaining tasks depend on something that can never succeed
                return self._finalize_failed(state)

            # Human checkpoints fire before the wave launches: if any runnable task
            # needs an un-granted sign-off, pause the whole run there (SPEC §8.6).
            for tid in runnable:
                if self._gate_pause(state, task_by_id[tid], state["tasks"][tid]["stage"]):
                    return state  # re-run after approval to resume

            if self._run_wave(state, [task_by_id[tid] for tid in runnable]):
                return self._finalize_failed(state)  # a task in the wave blocked

            # A rejected review (caught by the code_review gate) re-dispatches the
            # upstream developer subtree. Apply resets now, after the wave joined,
            # so we never mutate a task that a sibling thread is still running.
            self._drain_rework(state, task_by_id)

        self._monitor(state)  # monitoring_feedback pass (SPEC §3.9) before completing
        state["current_stage"] = "complete"
        state["halted"] = False
        self._persist(state)
        return state

    def _run_wave(self, state, tasks) -> bool:
        """Run every task in ``tasks`` concurrently and return True if ANY ended
        blocked. The tasks share no ordering constraint (all deps satisfied), so
        they overlap freely. The slow work — the runner/agent subprocess — runs
        outside ``self._lock``; only state mutation, persistence, and event
        appends are serialized, so genuine parallelism is preserved."""
        if len(tasks) == 1:  # common case: no thread-pool overhead
            t = tasks[0]
            return not self._run_task(state, t, state["tasks"][t["task_id"]]["stage"])
        workers = min(self.max_parallel, len(tasks))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(self._run_task, state, t,
                                 state["tasks"][t["task_id"]]["stage"]) for t in tasks]
            results = [f.result() for f in futures]  # re-raises any unexpected error
        return not all(results)  # any False (blocked) → wave failed

    def _gate_pause(self, state, task, stage) -> bool:
        """If ``stage`` has a mandatory human checkpoint that isn't approved, mark
        the task ``awaiting_approval`` and persist. Returns True when it paused."""
        gate = HUMAN_GATES.get(stage)
        if not gate or self._approved(gate):
            return False
        ts = state["tasks"][task["task_id"]]
        ts["status"] = "awaiting_approval"
        state["current_stage"] = stage
        self._mark_stage(state, stage, "awaiting_approval",
                         agent=task["owner_agent"], attempt=ts["attempt"])
        self._persist(state)
        return True

    def _dispatch(self, state, task, stage) -> str:
        """Apply the human checkpoint for ``stage`` then run the task. Returns
        'ok' on success, 'paused' if blocked on a sign-off, 'failed' if blocked.
        Used by the linear prelude (product → planner); the DAG phase schedules
        in waves via :meth:`_run_wave`."""
        if self._gate_pause(state, task, stage):
            return "paused"
        return "ok" if self._run_task(state, task, stage) else "failed"

    def _run_task(self, state, task, stage) -> bool:
        """Invoke one task with retry/escalate semantics. Returns True on success,
        False when the task ends blocked."""
        tid = task["task_id"]
        ts = state["tasks"][tid]
        agent = task["owner_agent"]

        while True:
            with self._lock:
                ts["status"] = "in_progress"
                ts["started_at"] = self._ts()
                state["current_stage"] = stage
                self._mark_stage(state, stage, "in_progress", agent=agent,
                                 attempt=ts["attempt"], started_at=ts["started_at"])
                self._persist(state)

            # The slow part runs OUTSIDE the lock so a parallel wave truly overlaps.
            try:
                result = self.runner.run(task, self.project)
            except UnrecoverableError as e:
                with self._lock:
                    return self._block(state, task, stage, [f"unrecoverable: {e}"])
            except RecoverableError as e:
                with self._lock:
                    proceed = self._retry(state, task, stage, [f"runner error: {e}"])
                    if not proceed:
                        return self._block(state, task, stage,
                                           [f"max_retries exhausted: {e}"])
                    backoff = self._backoff(ts["attempt"])
                self._sleep(backoff)  # back-off outside the lock — don't stall peers
                continue

            kind, issues = self._check(state, task, stage)  # read-only disk checks
            with self._lock:
                if kind == "ok":
                    ts["status"] = "success"
                    ts["completed_at"] = self._ts()
                    ts["artifact_refs"] = list(task.get("outputs", []))
                    metrics = self._extract_metrics(result)
                    self._mark_stage(state, stage, "success", agent=agent,
                                     attempt=ts["attempt"], completed_at=ts["completed_at"],
                                     artifact_refs=list(task.get("outputs", [])))
                    summary = f"{tid} complete"
                    if issues:  # non-blocking warnings (e.g. soft security findings)
                        summary += f" — warnings: {'; '.join(issues)}"
                    self._event(state, stage, agent, "success", task,
                                summary=summary, retry_count=ts["attempt"],
                                metrics=metrics)
                    self._persist(state)
                    return True
                if kind == "rework":
                    # review verdict == rejected → bounded fix loop (SPEC §8.3)
                    return self._request_rework(state, task, stage, issues)
                if kind == "unrecoverable":
                    return self._block(state, task, stage, issues)
                # recoverable gate failure → retry, then escalate at the cap
                proceed = self._retry(state, task, stage, issues)
                if not proceed:
                    return self._block(state, task, stage, issues)
                backoff = self._backoff(ts["attempt"])
            self._sleep(backoff)  # back-off outside the lock

    def _retry(self, state, task, stage, issues) -> bool:
        """Account for one retry. Returns False once ``max_retries`` is exhausted.
        Caller holds ``self._lock`` and performs the back-off sleep after releasing
        it, so a retrying task never stalls a concurrent peer."""
        ts = state["tasks"][task["task_id"]]
        if ts["attempt"] >= self.max_retries:
            return False
        ts["attempt"] += 1
        self._event(state, stage, task["owner_agent"], "retry", task,
                    summary="retrying after recoverable failure",
                    blocking_issues=issues, retry_count=ts["attempt"])
        self._persist(state)
        return True

    def _block(self, state, task, stage, issues) -> bool:
        ts = state["tasks"][task["task_id"]]
        ts["status"] = "blocked"
        ts["blocking_issues"] = list(issues)
        self._mark_stage(state, stage, "blocked", agent=task["owner_agent"],
                         attempt=ts["attempt"], blocking_issues=list(issues))
        self._event(state, stage, task["owner_agent"], "blocked", task,
                    summary="task blocked; escalating to human",
                    blocking_issues=list(issues), retry_count=ts["attempt"])
        self._persist(state)
        return False

    # ------------------------------------------------------------------ rework loop
    def _request_rework(self, state, task, stage, issues) -> bool:
        """A gate asked for a fix (review `rejected`, or e2e `failed`). Run a bounded
        fix loop (SPEC §8.3): record the request, reset this task to ``pending``, and
        let the post-wave drain reset the upstream developer subtree so the fix
        re-runs. The cap is per-stage (``STAGE_REWORK_CAP``, default ``max_rework``);
        once spent, escalate — and queue the failure to ``backlog.json`` so the signal
        survives. Caller holds ``self._lock``. Returns True (not blocked) so the wave
        proceeds and :meth:`run` reschedules the reset tasks."""
        ts = state["tasks"][task["task_id"]]
        cap = STAGE_REWORK_CAP.get(stage, self.max_rework)
        if ts.get("rework", 0) >= cap:
            self._append_backlog(state, list(issues), f"{stage}_rework_exhausted")
            return self._block(state, task, stage, list(issues) + [
                f"{stage} gate still failing after {cap} rework round(s)"])
        ts["rework"] = ts.get("rework", 0) + 1
        ts["status"] = "pending"  # re-run after the developer subtree is reset
        ts.pop("blocking_issues", None)
        self._rework_requests.add(task["task_id"])
        self._event(state, stage, task["owner_agent"], "retry", task,
                    summary=f"{stage} gate failed; rework round {ts['rework']} — "
                            f"re-dispatching upstream developer task(s)",
                    blocking_issues=list(issues), retry_count=ts["rework"])
        self._persist(state)
        return True

    def _drain_rework(self, state, task_by_id) -> None:
        """Apply any rework requests queued during the wave that just joined."""
        with self._lock:
            pending = list(self._rework_requests)
            self._rework_requests.clear()
        for gate_tid in pending:
            self._apply_rework(state, gate_tid, task_by_id)

    def _apply_rework(self, state, gate_tid, task_by_id) -> None:
        """Reset the developer ancestor(s) of a gate task that asked for a fix (a
        rejected review or a failed e2e run) — and every task downstream of them
        (QA, the review/e2e itself, deploy) — back to ``pending`` so the fix re-runs
        end to end. Stale declared outputs are removed so a failed re-run can't
        validate against last round's artifacts. The per-task ``rework`` counter is
        preserved so the cap still bites."""
        devs = {a for a in self._ancestors(gate_tid, task_by_id)
                if task_by_id[a].get("owner_agent") == "developer-agent"}
        if not devs:
            gate_stage = state["tasks"][gate_tid].get("stage", "code_review")
            self._block(state, task_by_id[gate_tid], gate_stage,
                        ["gate asked for rework but no upstream developer-agent task "
                         "to rework — escalating"])
            return
        reset = devs | self._dependents(devs, task_by_id)
        with self._lock:
            for tid in reset:
                t = state["tasks"][tid]
                t["status"] = "pending"
                t["attempt"] = 0
                for k in ("started_at", "completed_at", "blocking_issues",
                          "artifact_refs"):
                    t.pop(k, None)
                # Only delete the *developer* outputs (code + code spec) so a failed
                # re-run can't validate against last round's code. The reviewer's
                # review_report.json is deliberately kept on disk: the developer
                # reads its blocking_issues as the fix feedback (it gets overwritten
                # when the reviewer re-runs after the fix).
                if tid in devs:
                    for rel in task_by_id[tid].get("outputs", []):
                        try:
                            (self.project / rel).unlink()
                        except (FileNotFoundError, IsADirectoryError, OSError):
                            pass
            self._persist(state)

    @staticmethod
    def _ancestors(tid, task_by_id) -> set:
        """All transitive dependencies of ``tid`` (the tasks it waits on)."""
        seen: set[str] = set()
        stack = list(task_by_id.get(tid, {}).get("depends_on", []))
        while stack:
            n = stack.pop()
            if n in seen or n not in task_by_id:
                continue
            seen.add(n)
            stack.extend(task_by_id[n].get("depends_on", []))
        return seen

    @staticmethod
    def _dependents(roots, task_by_id) -> set:
        """All tasks that transitively depend on any task in ``roots`` (excludes
        ``roots`` themselves). Fixed-point over the depends_on graph."""
        roots = set(roots)
        out: set[str] = set()
        changed = True
        while changed:
            changed = False
            for t in task_by_id:
                if t in out or t in roots:
                    continue
                deps = set(task_by_id[t].get("depends_on", []))
                if deps & (roots | out):
                    out.add(t)
                    changed = True
        return out

    # ------------------------------------------------------------------ gates
    def _check(self, state, task, stage):
        """Mechanical post-run validation. Returns (kind, issues) where kind is
        'ok' | 'recoverable' | 'unrecoverable'."""
        # 1. every declared output must exist; JSON outputs must validate
        for rel in task.get("outputs", []):
            schema_file = schema_for_output(rel)
            if schema_file is None:
                if not (self.project / rel).exists():
                    return ("recoverable", [f"declared output missing: {rel}"])
                continue
            errors = validate_artifact(self.project / rel, schema_file, self.schemas_dir)
            if errors:
                return ("recoverable", errors)

        # 2. stage-specific gates (SPEC §7, §9)
        if stage == "code_review":
            hits = scan_source(self.project)
            blockers = [h for h, sev in hits if sev == "block"]
            if blockers:
                return ("unrecoverable", [f"security baseline: {h}" for h in blockers])
            warnings = [f"security warning: {h}" for h, sev in hits if sev == "warn"]
            kind, issues = self._review_gate(task)
            # carry non-blocking security warnings into the success summary
            if kind == "ok" and warnings:
                return ("ok", warnings)
            return (kind, issues)
        if stage == "deployment":
            return self._deploy_gate()
        if stage == "e2e_validation":
            return self._e2e_gate(task)
        return ("ok", [])

    def _review_gate(self, task):
        """code_review gate (SPEC §7, §9): the review verdict decides flow *before*
        QA/deploy, not two stages later. Returns:
          - ('ok', [])         verdict ∈ {approved, approved_with_comments}
          - ('rework', issues) verdict == 'rejected' → bounded fix loop (§8.3)
          - ('recoverable', …) report missing/unreadable or verdict absent
        """
        review = None
        for rel in task.get("outputs", []):
            if Path(rel).name == "review_report.json":
                review = self.project / rel
                break
        if review is None:
            review = self.artifacts / "review_report.json"
        if not review.exists():
            return ("recoverable", ["review gate: review_report.json missing"])
        try:
            data = json.loads(review.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return ("recoverable", [f"review gate: cannot read review_report.json: {e}"])
        verdict = data.get("verdict")
        if verdict == "rejected":
            issues = ["review gate: verdict is 'rejected'"]
            for bi in (data.get("blocking_issues") or [])[:10]:
                desc = (bi.get("description") or bi.get("title")) if isinstance(bi, dict) else str(bi)
                if desc:
                    issues.append(f"blocking: {desc}")
            return ("rework", issues)
        if verdict not in _APPROVED_VERDICTS:
            return ("recoverable", [f"review gate: verdict {verdict!r} not approved"])
        return ("ok", [])

    def _deploy_gate(self):
        """deployment gate (SPEC §7): review verdict ∈ approved set AND
        test_plan.summary.failed == 0. A rejected verdict is unrecoverable;
        everything else is recoverable (the build may be re-driven)."""
        review = self.artifacts / "review_report.json"
        plan = self.artifacts / "test_plan.json"
        issues = []

        if not review.exists():
            return ("recoverable", ["deploy gate: review_report.json missing"])
        verdict = json.loads(review.read_text()).get("verdict")
        if verdict == "rejected":
            return ("unrecoverable", ["deploy gate: review verdict is 'rejected'"])
        if verdict not in _APPROVED_VERDICTS:
            issues.append(f"deploy gate: review verdict {verdict!r} not approved")

        if not plan.exists():
            issues.append("deploy gate: test_plan.json missing")
        else:
            failed = json.loads(plan.read_text()).get("summary", {}).get("failed")
            if failed != 0:
                issues.append(
                    f"deploy gate: test_plan.json summary.failed == {failed} (must be 0)")

        return ("recoverable", issues) if issues else ("ok", [])

    def _e2e_gate(self, task):
        """e2e_validation gate (SPEC §3.x, §7): browser validation of the *deployed*
        app drives flow. Mirrors the review gate so a real UI regression is fixed,
        not shipped. Returns:
          - ('ok', [])         verdict ∈ {passed, passed_with_warnings}, no failures
          - ('rework', issues) verdict == 'failed' / summary.failed > 0 → bounded fix
                               loop (§8.3), capped at one round for e2e (STAGE_REWORK_CAP)
          - ('recoverable', …) report missing/unreadable or verdict absent
        """
        report = None
        for rel in task.get("outputs", []):
            if Path(rel).name == "e2e_report.json":
                report = self.project / rel
                break
        if report is None:
            report = self.artifacts / "e2e_report.json"
        if not report.exists():
            return ("recoverable", ["e2e gate: e2e_report.json missing"])
        try:
            data = json.loads(report.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return ("recoverable", [f"e2e gate: cannot read e2e_report.json: {e}"])
        verdict = data.get("verdict")
        failed = data.get("summary", {}).get("failed")
        if verdict == "failed" or (isinstance(failed, int) and failed > 0):
            issues = [f"e2e gate: verdict {verdict!r}, summary.failed == {failed}"]
            for sc in (data.get("scenarios") or []):
                if isinstance(sc, dict) and sc.get("status") == "fail":
                    issues.append(f"failed scenario: {sc.get('name') or sc.get('scenario_id')}"
                                  + (f" — {sc['error']}" if sc.get("error") else ""))
            return ("rework", issues[:11])
        if verdict not in _E2E_PASS_VERDICTS:
            return ("recoverable", [f"e2e gate: verdict {verdict!r} not passing"])
        return ("ok", [])

    # ------------------------------------------------------------------ monitoring
    def _monitor(self, state) -> None:
        """Minimal monitoring_feedback stage (SPEC §3.9). After a successful
        deployment, fold the release health into a feedback event; if the deploy is
        unhealthy, append a remediation item to ``artifacts/backlog.json`` so the
        signal can seed a future run. This is a feedback *signal*, not a gate — it
        never fails a build that already passed every prior gate (the deploy gate
        owns go/no-go). Owned by the orchestrator; it is not a DAG task."""
        dep = next((t for t in state.get("tasks", {}).values()
                    if t.get("stage") == "deployment" and t.get("status") == "success"),
                   None)
        report = self.artifacts / "release_report.json"
        if not dep or not report.exists():
            return
        try:
            data = json.loads(report.read_text())
        except (json.JSONDecodeError, OSError):
            return
        verdict = data.get("verdict")
        checks = data.get("health_checks") or []
        failed = [c for c in checks if isinstance(c, dict) and c.get("status") == "fail"]
        healthy = verdict == "success" and not failed
        if healthy:
            self._event(state, "monitoring_feedback", "orchestrator-agent", "success",
                        summary=f"deploy healthy (verdict={verdict}, "
                                f"{len(checks)} health check(s) passed)")
            return
        issues = ([f"health check failed: {c.get('name', '?')}" for c in failed]
                  or [f"release verdict {verdict!r} is not 'success'"])
        self._event(state, "monitoring_feedback", "orchestrator-agent", "failure",
                    summary=f"deploy unhealthy (verdict={verdict}, "
                            f"{len(failed)}/{len(checks)} health check(s) failed) — "
                            f"remediation queued to backlog.json",
                    blocking_issues=issues)
        self._append_backlog(state, issues, verdict)

    def _append_backlog(self, state, issues, verdict) -> None:
        """Append a remediation entry to ``artifacts/backlog.json`` (atomic write).
        A future run's product agent can fold these into new requirements — the
        minimal closed feedback loop the brief's Stage 8 calls for."""
        path = self.artifacts / "backlog.json"
        try:
            existing = json.loads(path.read_text()) if path.exists() else []
        except (json.JSONDecodeError, OSError):
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing.append({
            "id": f"REMEDIATION-{len(existing) + 1}",
            "source": "monitoring_feedback",
            "workflow_id": state.get("workflow_id"),
            "release_verdict": verdict,
            "issues": list(issues),
            "created_at": self._ts(),
        })
        with self._lock:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(existing, indent=2))
            tmp.replace(path)

    # ------------------------------------------------------------------ terminals
    def _finalize_failed(self, state) -> dict:
        state["current_stage"] = "failed"
        state["halted"] = True  # circuit breaker: stop dispatching new work
        self._persist(state)
        return state

    def _minimal_failed_state(self) -> dict:
        ts = self._ts()
        return {
            "spec_version": "v1",
            "workflow_id": self._new_id(),
            "current_stage": "failed",
            "stages": {},
            "tasks": {},
            "halted": True,
            "max_retries": self.max_retries,
            "created_at": ts,
            "updated_at": ts,
        }

    def _abort(self, state, stage, esc: Escalation) -> dict:
        state["current_stage"] = "failed"
        state["halted"] = True
        self._mark_stage(state, stage, "blocked", agent="orchestrator-agent",
                         attempt=0, blocking_issues=esc.issues)
        self._event(state, stage, "orchestrator-agent", "blocked",
                    summary=str(esc), blocking_issues=esc.issues)
        self._persist(state)
        return state
