"""The Evaluator — heals failed tasks (the only LLM on the failure path).

When a task ends in ``error``, the router asks the evaluator to *diagnose* it: it
produces a ``healing_prompt`` describing the root-cause fix, which the router
attaches to the re-injected task(s). Re-entry is at the failure point — with the
fingerprint skip-unchanged optimization, that is exactly "re-enter at the front
and cache-hit everything upstream that didn't change", just realized efficiently.

``diagnose`` is bounded by ``max_heal`` rounds; past the cap it returns ``None`` and
the router dead-letters the pipeline. The actual analysis is an injectable
``heal_fn(db, task) -> str`` so the control flow is testable with no LLM; the
production factory spawns the ``evaluator-agent`` subagent.
"""
from __future__ import annotations


def default_heal_fn(db, task) -> str:
    """No-LLM diagnosis: summarize the failure payload into a fix instruction.
    Good enough to drive the loop deterministically in tests / offline runs."""
    payload = task.get("payload") or {}
    issues = payload.get("issues") or ([payload.get("error")] if payload.get("error") else [])
    body = "; ".join(i for i in issues if i) or "the task failed without detail"
    return (f"A previous {task.get('agent_role')} attempt failed: {body}. "
            f"Diagnose the root cause and fix it so the declared outputs validate.")


def make_llm_heal_fn(runner, project_root):
    """Production factory: spawn the evaluator-agent to analyze the failure and
    return its healing prompt. Falls back to the default summary if the agent
    yields nothing usable. ``runner`` is a ClaudeAgentRunner-like object."""
    def heal_fn(db, task):
        payload = task.get("payload") or {}
        spec = {
            "task_id": f"EVAL-{task['task_id']}",
            "owner_agent": "evaluator-agent",
            "title": f"Diagnose failure of {task['agent_role']} task {task['task_id']}",
            "inputs": task.get("inputs", []),
            "outputs": [],
            "request": (f"The {task['agent_role']} task failed: {payload.get('error')}. "
                        f"Analyze the artifacts and produce a concise root-cause fix "
                        f"instruction (the healing prompt) for re-running it."),
        }
        try:
            result = runner.run(spec, project_root)
        except Exception:
            return default_heal_fn(db, task)
        text = (result or {}).get("result") if isinstance(result, dict) else None
        return text.strip() if isinstance(text, str) and text.strip() else default_heal_fn(db, task)
    return heal_fn


class Evaluator:
    def __init__(self, db, *, max_heal: int = 2, heal_fn=None):
        self.db = db
        self.max_heal = max(0, max_heal)
        self.heal_fn = heal_fn or default_heal_fn

    def diagnose(self, task: dict) -> str | None:
        """Return a healing prompt, or None when the heal budget is spent."""
        if task.get("heal_round", 0) >= self.max_heal:
            return None
        return self.heal_fn(self.db, task)
