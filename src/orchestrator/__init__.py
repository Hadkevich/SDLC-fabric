"""Agentic SDLC orchestration engine.

The deterministic control plane that drives the multi-agent pipeline from the
first task to ``complete`` (or ``failed``) per SPEC §8. See ``engine.Orchestrator``.

Public API:
    Orchestrator        — the control plane (schedule, gate, retry, persist).
    Escalation          — internal hand-off-to-human signal.
    Runner              — the work-plane protocol the engine delegates to.
    CallableRunner      — wrap a plain ``fn(task, project_root)`` (tests/glue).
    ReplayRunner        — reuse on-disk outputs to resume without re-invoking.
    ClaudeAgentRunner   — invoke the real Claude Code subagent for a task.
    RecoverableError    — raise from a runner to request a retry.
    UnrecoverableError  — raise from a runner to escalate immediately.
"""
from .engine import Orchestrator, Escalation
from .runners import (
    Runner,
    CallableRunner,
    ReplayRunner,
    ClaudeAgentRunner,
    RunnerError,
    RecoverableError,
    UnrecoverableError,
)

__all__ = [
    "Orchestrator",
    "Escalation",
    "Runner",
    "CallableRunner",
    "ReplayRunner",
    "ClaudeAgentRunner",
    "RunnerError",
    "RecoverableError",
    "UnrecoverableError",
]
