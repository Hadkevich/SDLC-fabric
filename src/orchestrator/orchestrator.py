"""The Orchestrator (router) — the deterministic control brain of the DB engine.

Invoked by the watcher once per *terminal* event (a task reached ``done``/``error``).
It owns control flow only — never an artifact's content. For one event it:

  * on **success**: applies the stage gate (review / deploy / e2e), and either
    advances the pipeline (create the planner task, expand the workplan DAG, or
    just let the dependency graph release the next task), routes a rejected
    review / failed e2e into a bounded developer **rework** loop, or **dead-letters**
    on an unrecoverable gate; then checks for completion.
  * on **failure**: asks the :class:`Evaluator` for a healing prompt and re-injects
    the failed subtree (skip-unchanged), or dead-letters once the heal cap is spent.

Happy-path routing is pure code (no LLM); the evaluator is the only LLM, and only
on failure. Multi-pipeline: every method is scoped by the event's ``pipeline_id``,
so many pipelines advance independently against the one shared tasks table.
"""
from __future__ import annotations

import json

from .evaluator import Evaluator
from .lifecycle import AGENT_STAGE, ALL_GATES, HUMAN_GATES, is_artifact_path
from .routing import STAGE_GATE, deploy_gate, e2e_gate, review_gate

REVIEW_ARTIFACT = "artifacts/review_report.json"
TESTPLAN_ARTIFACT = "artifacts/test_plan.json"
E2E_ARTIFACT = "artifacts/e2e_report.json"
WORKPLAN_ARTIFACT = "artifacts/workplan.json"
REQUIREMENTS_ARTIFACT = "artifacts/requirements.json"

# Gate token -> the stages it releases (reverse of HUMAN_GATES).
GATE_STAGES: dict[str, list[str]] = {}
for _stage, _gate in HUMAN_GATES.items():
    GATE_STAGES.setdefault(_gate, []).append(_stage)


class Orchestrator:
    def __init__(self, db, *, max_rework: int = 2, max_e2e_rework: int = 1,
                 max_heal: int = 2, auto_approve: bool = False, evaluator=None,
                 max_pipelines: int | None = None):
        self.db = db
        self.max_rework = max_rework
        self.max_e2e_rework = max_e2e_rework
        self.auto_approve = auto_approve
        self.max_pipelines = max_pipelines
        self.evaluator = evaluator or Evaluator(db, max_heal=max_heal)

    # ------------------------------------------------------------------ submit
    def submit(self, request: str, name: str | None = None) -> str:
        """Create a pipeline and its first (product) task. The product stage is not
        human-gated; the requirements sign-off gates the *planner* that follows.
        Raises if it would exceed ``max_pipelines`` active runs."""
        if (self.max_pipelines is not None
                and self.db.count_active_pipelines() >= self.max_pipelines):
            raise RuntimeError(
                f"max_pipelines ({self.max_pipelines}) reached — refuse new pipeline")
        pid = self.db.create_pipeline(request, name)
        if self.auto_approve:
            for gate in ALL_GATES:
                self.db.approve(pid, gate)
        self.db.insert_task(
            pid, "product-agent", "requirement_ingestion",
            title="Normalize the user request into structured requirements",
            outputs=[REQUIREMENTS_ARTIFACT], request=request, status="pending")
        return pid

    def approve(self, pipeline_id: str, gate: str) -> int:
        """Record a human sign-off and release any tasks waiting on it. Returns the
        number of tasks moved from ``awaiting_approval`` to ``pending``."""
        self.db.approve(pipeline_id, gate)
        released = 0
        for stage in GATE_STAGES.get(gate, []):
            for t in self.db.list_tasks(pipeline_id=pipeline_id,
                                        status="awaiting_approval"):
                if t["stage"] == stage:
                    self.db.update_task(t["task_id"], status="pending")
                    released += 1
        return released

    # ------------------------------------------------------------------ route
    def route(self, event: dict) -> None:
        """Handle one terminal event, then mark it processed (so it is routed once)."""
        try:
            task = self.db.get_task(event.get("task_id")) if event.get("task_id") else None
            if task is not None:
                if event["status"] == "success":
                    self._on_success(task)
                elif event["status"] == "failure":
                    self._on_failure(task)
                # 'blocked' events are orchestrator-emitted and pre-processed.
        finally:
            self.db.mark_event_processed(event["event_id"])

    # ------------------------------------------------------------------ success
    def _on_success(self, task: dict) -> None:
        pid, stage = task["pipeline_id"], task["stage"]
        gate = STAGE_GATE.get(stage)
        if gate == "review":
            kind, issues = review_gate(self._artifact(pid, REVIEW_ARTIFACT))
            if kind == "rework":
                return self._rework(task, issues, self.max_rework)
            if kind == "block":
                return self._dead_letter(task, issues)
        elif gate == "deploy":
            kind, issues = deploy_gate(self._artifact(pid, REVIEW_ARTIFACT),
                                       self._artifact(pid, TESTPLAN_ARTIFACT))
            if kind in ("block", "recoverable") and issues:
                return self._dead_letter(task, issues)
        elif gate == "e2e":
            kind, issues = e2e_gate(self._artifact(pid, E2E_ARTIFACT))
            if kind == "rework":
                return self._rework(task, issues, self.max_e2e_rework)
            if kind == "block":
                return self._dead_letter(task, issues)
        else:
            # non-gated advance: grow the pipeline as the linear prelude completes.
            if stage == "requirement_ingestion":
                self._create_planner(task)
            elif stage == "task_decomposition":
                self._expand_workplan(pid, task)
        self._maybe_complete(pid)

    def _create_planner(self, product_task: dict) -> None:
        pid = product_task["pipeline_id"]
        status = self._initial_status(pid, "task_decomposition")
        self.db.insert_task(
            pid, "planner-agent", "task_decomposition",
            title="Decompose requirements into a dependency-ordered workplan",
            inputs=[REQUIREMENTS_ARTIFACT], outputs=[WORKPLAN_ARTIFACT],
            depends_on=[product_task["task_id"]], status=status)

    def _expand_workplan(self, pid: str, planner_task: dict) -> None:
        """Turn the planner's workplan.json into task rows. Workplan-local task_ids
        are mapped to fresh DB task_ids; depends_on is translated; human-gated
        stages start ``awaiting_approval`` until their sign-off lands."""
        art = self._artifact(pid, WORKPLAN_ARTIFACT)
        wp_tasks = (art or {}).get("tasks") or []
        id_map: dict[str, str] = {}
        for wt in wp_tasks:
            id_map[wt["task_id"]] = self.db._new_id()
        for wt in wp_tasks:
            stage = AGENT_STAGE.get(wt["owner_agent"], "code_generation")
            deps = [id_map[d] for d in wt.get("depends_on", []) if d in id_map]
            self.db.insert_task(
                pid, wt["owner_agent"], stage, title=wt.get("title", ""),
                inputs=wt.get("inputs", []), outputs=wt.get("outputs", []),
                depends_on=deps, status=self._initial_status(pid, stage),
                task_id=id_map[wt["task_id"]])

    def _initial_status(self, pid: str, stage: str) -> str:
        gate = HUMAN_GATES.get(stage)
        if gate and not (self.auto_approve or self.db.is_approved(pid, gate)):
            return "awaiting_approval"
        return "pending"

    # ------------------------------------------------------------------ rework
    def _rework(self, gate_task: dict, issues: list[str], cap: int) -> None:
        """Bounded developer rework: reset the developer ancestors of a rejected
        review / failed e2e (and everything downstream of them) so the fix re-runs
        end to end. The cap counter lives on the gate task; past it we dead-letter."""
        if gate_task.get("heal_round", 0) >= cap:
            return self._dead_letter(
                gate_task, issues + [f"still failing after {cap} rework round(s)"])
        pid = gate_task["pipeline_id"]
        by_id = {t["task_id"]: t for t in self.db.list_tasks(pipeline_id=pid)}
        devs = {a for a in self._ancestors(gate_task["task_id"], by_id)
                if by_id[a]["agent_role"] == "developer-agent"}
        if not devs:
            return self._dead_letter(
                gate_task, issues + ["no upstream developer task to rework"])
        prompt = "Rework required. Address these blocking issues:\n- " + "\n- ".join(issues)
        self._reset_subtree(devs, by_id, healing_prompt=prompt)
        self.db.update_task(gate_task["task_id"], heal_round=gate_task["heal_round"] + 1)

    # ------------------------------------------------------------------ failure
    def _on_failure(self, task: dict) -> None:
        prompt = self.evaluator.diagnose(task)
        if prompt is None:
            return self._dead_letter(task, (task.get("payload") or {}).get("issues")
                                     or ["heal budget exhausted"])
        pid = task["pipeline_id"]
        by_id = {t["task_id"]: t for t in self.db.list_tasks(pipeline_id=pid)}
        self._reset_subtree({task["task_id"]}, by_id, healing_prompt=prompt, heal=True)

    # ------------------------------------------------------------------ helpers
    def _reset_subtree(self, roots: set[str], by_id: dict, *,
                       healing_prompt: str | None = None, heal: bool = False) -> None:
        """Reset ``roots`` and everything transitively downstream to ``pending`` so
        they re-run. Root tasks get the healing prompt (and, when ``heal``, a bumped
        heal_round). A root's declared JSON outputs are removed from the DB so a
        re-run can't validate against last round's artifacts."""
        affected = set(roots) | self._dependents(roots, by_id)
        for tid in affected:
            t = by_id[tid]
            fields = dict(status="pending", attempt=0, claimed_by=None,
                          lease_until=None, started_at=None, completed_at=None,
                          payload=None)
            if tid in roots:
                if healing_prompt:
                    fields["healing_prompt"] = healing_prompt
                if heal:
                    fields["heal_round"] = t.get("heal_round", 0) + 1
                for rel in t.get("outputs", []):
                    if is_artifact_path(rel):
                        self.db.delete_artifact(t["pipeline_id"], rel)
            self.db.update_task(tid, **fields)

    def _dead_letter(self, task: dict, issues) -> None:
        self.db.update_task(task["task_id"], status="blocked",
                            payload={"blocking_issues": list(issues)})
        eid = self.db.append_event(
            task["pipeline_id"], task["stage"], task["agent_role"], "blocked",
            task_id=task["task_id"], summary="task blocked; escalating to human",
            blocking_issues=list(issues))
        self.db.mark_event_processed(eid)  # orchestrator-emitted: don't re-route
        self.db.set_pipeline_status(task["pipeline_id"], "failed")

    def _maybe_complete(self, pid: str) -> None:
        tasks = self.db.list_tasks(pipeline_id=pid)
        if tasks and all(t["status"] == "done" for t in tasks):
            eid = self.db.append_event(pid, "monitoring_feedback", "orchestrator-agent",
                                       "success", summary="pipeline complete")
            self.db.mark_event_processed(eid)
            self.db.set_pipeline_status(pid, "complete")

    def _artifact(self, pid: str, name: str):
        art = self.db.get_artifact(pid, name)
        if not art:
            return None
        try:
            return json.loads(art["content"])
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _ancestors(tid: str, by_id: dict) -> set:
        seen, stack = set(), list(by_id.get(tid, {}).get("depends_on", []))
        while stack:
            n = stack.pop()
            if n in seen or n not in by_id:
                continue
            seen.add(n)
            stack.extend(by_id[n].get("depends_on", []))
        return seen

    @staticmethod
    def _dependents(roots: set, by_id: dict) -> set:
        roots, out, changed = set(roots), set(), True
        while changed:
            changed = False
            for tid, t in by_id.items():
                if tid in out or tid in roots:
                    continue
                if set(t.get("depends_on", [])) & (roots | out):
                    out.add(tid)
                    changed = True
        return out
