"""The agent worker — the file-on-edge adapter (work plane).

One worker runs exactly one attempt of one claimed task:

  1. **Materialize** the task's input artifacts from the DB into temp JSON files
     in the project workdir (agents are Claude Code subprocesses that read files).
  2. **Run** the owning agent via the injected runner (``ClaudeAgentRunner`` in
     prod; a fake in tests). The agent reads those files and writes its outputs.
  3. **Validate + ingest**: each declared JSON artifact under ``artifacts/`` is
     schema-checked (reusing ``orchestrator.validation``) and written back to the
     DB (the source of truth), then its local copy is removed. Project *code*
     files (``src/…``, ``frontend/…``) are existence-checked and left on disk.
  4. **Record**: set the task's terminal status + payload and append one event.
     A recoverable failure under the retry cap re-queues the task (``pending`` +
     a non-terminal ``retry`` event); past the cap, or an unrecoverable failure,
     the task goes ``error`` with a terminal ``failure`` event the router/evaluator
     picks up.

The orchestrator (router) owns the *gates* (review verdict, deploy gate, …); the
worker only proves outputs exist and validate. DB writes are the source of truth.
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.runners import RecoverableError, UnrecoverableError
from orchestrator.validation import schema_for_output, validate_artifact
from orchestrator.lifecycle import is_artifact_path

DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def extract_metrics(result):
    """Pull cost/token/duration out of a runner's return envelope (claude -p JSON).
    Returns None when the runner reports nothing (e.g. fake runners in tests)."""
    if not isinstance(result, dict):
        return None
    usage = result.get("usage") or {}
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    cache = (usage.get("cache_creation_input_tokens") or 0) + \
            (usage.get("cache_read_input_tokens") or 0)
    cost, dur = result.get("total_cost_usd"), result.get("duration_ms")
    if inp is None and out is None and cost is None and dur is None:
        return None
    in_total = (inp or 0) + cache
    return {"input_tokens": in_total, "output_tokens": out or 0,
            "total_tokens": in_total + (out or 0), "cost_usd": cost,
            "duration_ms": dur}


def _runner_task(task: dict) -> dict:
    """Build the task dict the runner expects from a DB task row. A healing prompt
    (set by the evaluator) is prepended to the request so the fix feedback reaches
    the agent."""
    request = task.get("request") or ""
    if task.get("healing_prompt"):
        request = (f"HEALING CONTEXT — a previous run failed; fix the root cause:\n"
                   f"{task['healing_prompt']}\n\n{request}").strip()
    return {
        "task_id": task["task_id"],
        "owner_agent": task["agent_role"],
        "title": task.get("title", ""),
        "inputs": task.get("inputs", []),
        "outputs": task.get("outputs", []),
        "done_criteria": task.get("done_criteria", []),
        "request": request or None,
    }


def execute_task(db, task: dict, project_root, runner, *,
                 schemas_dir=DEFAULT_SCHEMAS_DIR, worker_id: str = "worker") -> dict:
    """Run one attempt of ``task``. Returns a small result dict for the caller
    (the watcher) and tests: ``{"status": done|error|retry, "event_id": ...}``."""
    project_root = Path(project_root)
    pid = task["pipeline_id"]
    stage = task["stage"]
    agent = task["agent_role"]
    materialized: list[Path] = []

    db.update_task(task["task_id"], status="in_progress", started_at=db._now())

    # 1. materialize input artifacts (DB -> temp files)
    for rel in task.get("inputs", []):
        if not is_artifact_path(rel):
            continue  # code-file inputs already live on disk
        art = db.get_artifact(pid, rel)
        if art is None:
            continue
        dest = project_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(art["content"])
        materialized.append(dest)

    # 2. run the agent
    try:
        result = runner.run(_runner_task(task), project_root)
    except UnrecoverableError as e:
        return _fail(db, task, [f"unrecoverable: {e}"], unrecoverable=True)
    except RecoverableError as e:
        return _retry_or_fail(db, task, [f"runner error: {e}"])
    finally:
        _cleanup(materialized)

    # 3. validate + ingest declared outputs
    issues: list[str] = []
    pending_ingest: list[tuple[str, str, str | None]] = []  # (rel, text, schema)
    for rel in task.get("outputs", []):
        path = project_root / rel
        if is_artifact_path(rel):
            if not path.exists():
                issues.append(f"declared output missing: {rel}")
                continue
            schema = schema_for_output(rel)
            if schema:
                errs = validate_artifact(path, schema, schemas_dir)
                if errs:
                    issues.extend(errs)
                    continue
            pending_ingest.append((rel, path.read_text(), schema))
        elif not path.exists():
            issues.append(f"declared output missing: {rel}")  # code file

    if issues:
        return _retry_or_fail(db, task, issues)

    # all good — ingest artifacts into the DB, drop their local copies
    output_names = []
    for rel, text, schema in pending_ingest:
        db.put_artifact(pid, rel, text, producer_task_id=task["task_id"],
                        schema_name=schema)
        output_names.append(rel)
        try:
            (project_root / rel).unlink()
        except OSError:
            pass

    metrics = extract_metrics(result)
    payload = {"summary": f"{task['task_id']} complete", "outputs": output_names}
    db.update_task(task["task_id"], status="done", completed_at=db._now(),
                   payload=payload)
    eid = db.append_event(pid, stage, agent, "success", task_id=task["task_id"],
                          summary=payload["summary"],
                          output_refs=task.get("outputs", []),
                          input_refs=task.get("inputs", []),
                          metrics=metrics, retry_count=task.get("attempt", 0))
    return {"status": "done", "event_id": eid}


def _cleanup(paths) -> None:
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


def _retry_or_fail(db, task, issues) -> dict:
    """Recoverable failure: re-queue under the retry cap, else escalate to error."""
    attempt = task.get("attempt", 0)
    if attempt < task.get("max_retries", 3):
        db.update_task(task["task_id"], status="pending", attempt=attempt + 1,
                       claimed_by=None, lease_until=None)
        eid = db.append_event(task["pipeline_id"], task["stage"], task["agent_role"],
                              "retry", task_id=task["task_id"],
                              summary="retrying after recoverable failure",
                              blocking_issues=issues, retry_count=attempt + 1)
        return {"status": "retry", "event_id": eid}
    return _fail(db, task, issues + [f"max_retries ({task.get('max_retries', 3)}) "
                                     f"exhausted"])


def _fail(db, task, issues, *, unrecoverable: bool = False) -> dict:
    """Terminal failure: mark error + emit a `failure` event (router/evaluator picks
    it up). ``payload`` carries the issues + kind so the evaluator can classify."""
    payload = {"error": "; ".join(issues),
               "kind": "unrecoverable" if unrecoverable else "recoverable",
               "issues": list(issues)}
    db.update_task(task["task_id"], status="error", completed_at=db._now(),
                   payload=payload)
    eid = db.append_event(task["pipeline_id"], task["stage"], task["agent_role"],
                          "failure", task_id=task["task_id"],
                          summary="task failed; routing to evaluator",
                          blocking_issues=issues, retry_count=task.get("attempt", 0))
    return {"status": "error", "event_id": eid}
