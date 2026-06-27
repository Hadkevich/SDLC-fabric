"""Export the DB-backed control plane to static JSON for the observability dashboard.

The dashboard is a zero-dependency static page served by ``http.server`` — it
can't query SQLite. So this writes per-pipeline snapshots (and an index) in the
same shape the dashboard already understands (``workflow_state``-like ``state`` +
an ``events`` list), letting the page render DB pipelines unchanged from its file
pipelines. Run ``python -m watcher export`` (optionally on a loop) alongside the
watcher.
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.lifecycle import STAGE_AGENT, STAGE_ORDER

# Map a task status onto the status vocabulary the dashboard styles.
_TASK_TO_STAGE_STATUS = {
    "done": "success", "error": "failure", "blocked": "blocked",
    "claimed": "in_progress", "in_progress": "in_progress",
    "awaiting_approval": "awaiting_approval", "pending": "pending",
    "skipped": "skipped",
}


def _stage_status(statuses: list[str]) -> str:
    """Aggregate a stage's task statuses into one stage status."""
    if "error" in statuses:
        return "failure"
    if "blocked" in statuses:
        return "blocked"
    if statuses and all(s == "done" for s in statuses):
        return "success"
    if any(s in ("claimed", "in_progress") for s in statuses):
        return "in_progress"
    if any(s == "awaiting_approval" for s in statuses):
        return "awaiting_approval"
    return "pending"


def _task_issues(task: dict) -> list[str]:
    payload = task.get("payload") or {}
    if isinstance(payload, dict):
        return payload.get("blocking_issues") or payload.get("issues") or []
    return []


def build_snapshot(db, pipeline_id: str) -> dict:
    """Build a dashboard-shaped snapshot: ``{state, events}`` for one pipeline."""
    p = db.get_pipeline(pipeline_id) or {}
    tasks = db.list_tasks(pipeline_id=pipeline_id)
    events = db.list_events(pipeline_id)

    stages: dict[str, dict] = {}
    for stage in STAGE_ORDER:
        in_stage = [t for t in tasks if t["stage"] == stage]
        if not in_stage:
            continue
        started = [t["started_at"] for t in in_stage if t.get("started_at")]
        completed = [t["completed_at"] for t in in_stage if t.get("completed_at")]
        refs, issues = [], []
        for t in in_stage:
            if t["status"] == "done":
                refs.extend(t.get("outputs", []))
            issues.extend(_task_issues(t))
        stages[stage] = {
            "status": _stage_status([t["status"] for t in in_stage]),
            "agent": STAGE_AGENT.get(stage, ""),
            "attempt": max((t.get("attempt", 0) for t in in_stage), default=0),
            "artifact_refs": refs,
            "blocking_issues": issues,
            **({"started_at": min(started)} if started else {}),
            **({"completed_at": max(completed)} if completed else {}),
        }

    task_map = {t["task_id"]: {
        "status": t["status"], "attempt": t.get("attempt", 0),
        "owner_agent": t["agent_role"], "stage": t["stage"],
        "started_at": t.get("started_at"), "completed_at": t.get("completed_at"),
        "artifact_refs": t.get("outputs", []),
        "blocking_issues": _task_issues(t),
    } for t in tasks}

    state = {
        "workflow_id": pipeline_id,
        "project_name": p.get("name"),
        "current_stage": _current_stage(p, tasks),
        "stages": stages,
        "tasks": task_map,
    }
    return {"state": state, "events": events}


def _current_stage(pipeline: dict, tasks: list[dict]) -> str:
    if pipeline.get("status") in ("complete", "failed"):
        return pipeline["status"]
    active = [t for t in tasks if t["status"] in ("claimed", "in_progress")]
    if active:
        return active[-1]["stage"]
    done_stages = [t["stage"] for t in tasks if t["status"] == "done"]
    return (max(done_stages, key=STAGE_ORDER.index) if done_stages
            else STAGE_ORDER[0])


def build_index(db) -> list[dict]:
    return [{"pipeline_id": p["pipeline_id"], "name": p["name"], "status": p["status"]}
            for p in db.list_pipelines()]


def export_all(db, out_dir) -> int:
    """Write ``index.json`` + ``<pipeline_id>.json`` for every pipeline. Returns the
    count written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = build_index(db)
    (out / "index.json").write_text(json.dumps(index, indent=2))
    for entry in index:
        snap = build_snapshot(db, entry["pipeline_id"])
        (out / f"{entry['pipeline_id']}.json").write_text(json.dumps(snap, indent=2))
    return len(index)
