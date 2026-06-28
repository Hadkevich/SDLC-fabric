"""Agent runtime — the layer that actually *does the work* of a stage.

The Orchestrator (control plane) never executes a task itself; it delegates to a
``Runner`` (work plane). A runner takes a workplan task plus the project root and
fulfils the task's declared ``outputs`` (writing artifacts / source files), then
returns. It signals trouble by raising one of two errors, which is how the
Orchestrator decides retry-vs-escalate (SPEC §8.3):

* ``RecoverableError``  — transient/partial failure → retry with back-off.
* ``UnrecoverableError`` — unsafe/unsatisfiable → escalate immediately, no retry.

Three concrete runners ship here:

* ``CallableRunner``       — wraps a plain ``fn(task, project_root)`` (tests + glue).
* ``ReplayRunner``         — reuses on-disk outputs to resume without re-invoking.
* ``ClaudeAgentRunner``    — invokes the real Claude Code subagent for the task.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .frontmatter import read_frontmatter_lines


class RunnerError(Exception):
    """Base class for runner failures."""


class RecoverableError(RunnerError):
    """A transient/partial failure. The Orchestrator retries with back-off up to
    ``max_retries`` before escalating (SPEC §8.3)."""


class UnrecoverableError(RunnerError):
    """A failure that retrying cannot fix (unsafe request, unsatisfiable contract).
    The Orchestrator blocks and escalates immediately — no retry (SPEC §8.3)."""


class Runner:
    """Runner protocol. Implementations fulfil a task's declared outputs."""

    def run(self, task: dict, project_root: Path):  # pragma: no cover - interface
        raise NotImplementedError


class CallableRunner(Runner):
    """Adapt a plain ``fn(task, project_root)`` into a Runner.

    The function fulfils the task (writes its ``outputs``) or raises
    ``RecoverableError`` / ``UnrecoverableError`` to drive the control plane.
    """

    def __init__(self, fn):
        self._fn = fn

    def run(self, task, project_root):
        return self._fn(task, project_root)


class ReplayRunner(Runner):
    """Replay a prior run by reusing outputs already on disk.

    Used to resume / re-validate a workflow without re-invoking an agent
    (idempotency, SPEC §8.4). If a declared output is missing there is nothing to
    replay, which is a recoverable condition.
    """

    def __init__(self, strict: bool = True):
        self.strict = strict

    def run(self, task, project_root):
        root = Path(project_root)
        missing = [rel for rel in task.get("outputs", []) if not (root / rel).exists()]
        if missing and self.strict:
            raise RecoverableError(f"no recorded output to replay: {missing}")


class ClaudeAgentRunner(Runner):
    """Invoke the *real* Claude Code subagent that owns a task (the LLM wrapper).

    Dispatches the task to the actual agent defined in ``.claude/agents/`` using
    the CLI's ``--agent <name>`` flag — so the agent runs with its own system
    prompt **and** its declared least-privilege toolset (SPEC §9). The runner does
    not pretend, hint, or re-describe the role; it selects the defined agent by
    name. ``owner_agent`` in the workplan (e.g. ``developer-agent``) is exactly the
    ``name:`` in the agent's markdown frontmatter, so the names line up 1:1.

    The runner only (a) launches the agent and (b) classifies launch outcomes into
    recoverable / unrecoverable. Whatever lands on disk is validated by the
    Orchestrator — artifacts are the source of truth, not the agent's chat output.

    Requires Claude Code CLI >= 2.1 (for ``--agent``). Tests use ``CallableRunner``
    instead, so the control plane is exercised deterministically without an LLM.

    Parameters
    ----------
    cli : str
        CLI executable name/path (default ``"claude"``).
    permission_mode : str
        Headless permission mode. ``"acceptEdits"`` (default) auto-allows file
        writes but still gates other tools; use ``"bypassPermissions"`` for fully
        unattended runs in a sandbox (the agent's tool list already constrains it).
    add_dirs : list[str] | None
        Extra directories the agent may read (e.g. the repo root, so schemas/ and
        SPEC.md are reachable when cwd is a sub-project). Passed via ``--add-dir``.
    model : str | None
        Override the agent's model (alias like ``"sonnet"``/``"opus"``). Default
        ``None`` keeps the model declared in the agent definition.
    timeout : int
        Per-task wall-clock seconds before the launch is treated as a recoverable
        timeout.
    extra_args : list[str] | None
        Additional raw CLI args appended verbatim (escape hatch).
    """

    _PERMISSION_MODES = {"acceptEdits", "auto", "bypassPermissions", "default",
                         "dontAsk", "plan"}

    def __init__(self, *, cli: str = "claude", permission_mode: str = "acceptEdits",
                 add_dirs=None, model: str | None = None, timeout: int = 1800,
                 extra_args=None, mcp_config: str | None = None):
        if permission_mode not in self._PERMISSION_MODES:
            raise ValueError(
                f"permission_mode {permission_mode!r} not in {sorted(self._PERMISSION_MODES)}")
        self.cli = cli
        self.permission_mode = permission_mode
        self.add_dirs = [str(d) for d in (add_dirs or [])]
        self.model = model
        self.timeout = timeout
        self.extra_args = list(extra_args or ())
        # MCP server config (e.g. the repo .mcp.json with the Playwright server the
        # e2e-agent needs). The CLI is spawned with cwd=<project>, which has no
        # .mcp.json, so without this an agent that declares mcp__* tools (e2e) finds
        # no server and can't drive the browser. Passed via --mcp-config when set.
        self.mcp_config = str(mcp_config) if mcp_config else None

    def _prompt(self, task, project_root):
        """Task-specific user message. The *role* comes from the agent definition
        (via --agent); this only supplies the concrete unit of work."""
        outputs = "\n".join(f"  - {o}" for o in task.get("outputs", [])) or "  (none)"
        inputs = "\n".join(f"  - {i}" for i in task.get("inputs", [])) or "  (none)"
        criteria = "\n".join(f"  - {c}" for c in task.get("done_criteria", [])) or "  (none)"
        request = f"User request:\n{task['request']}\n\n" if task.get("request") else ""
        return (
            f"{request}"
            f"Task {task['task_id']}: {task.get('title', '')}\n"
            f"Project root (your working directory): {project_root}\n\n"
            f"Inputs:\n{inputs}\n\n"
            f"Produce exactly these outputs — nothing outside this task's scope:\n"
            f"{outputs}\n\n"
            f"Done criteria:\n{criteria}\n\n"
            f"Write artifacts to the paths above, valid against the schemas in "
            f"schemas/. The orchestrator validates your output mechanically, so "
            f"conform exactly. Do not modify another agent's artifacts."
        )

    def _mcp_tools_from_frontmatter(self, owner_agent: str, project_root: Path) -> list[str]:
        """Parse the agent's .md frontmatter and return declared MCP tool names.

        Agents that declare ``mcp__*`` tools in their frontmatter ``tools:`` list
        need those tools explicitly granted via ``--allowedTools`` in headless
        (``-p``) mode, where an interactive permission prompt is not available.
        This is a belt-and-suspenders complement to ``settings.json`` ``allow``.
        """
        # Agent files follow the convention <short-name>.agent.md
        # (e.g. "e2e-agent" → "e2e.agent.md", "developer-agent" → "developer.agent.md").
        # Fall back to <owner_agent>.md for any non-standard names.
        # Search the project root AND the configured add_dirs (which include the repo
        # root) — for a sub-project under projects/, the agent definitions live at the
        # repo root, not in the project. Without this the e2e-agent's mcp__playwright
        # tools were never granted via --allowedTools, so it couldn't drive the browser.
        short = owner_agent.removesuffix("-agent")
        roots = [Path(project_root), *(Path(d) for d in self.add_dirs)]
        names = [f"{short}.agent.md", f"{owner_agent}.md"]
        agent_file = next(
            (root / ".claude" / "agents" / name
             for root in roots for name in names
             if (root / ".claude" / "agents" / name).exists()),
            Path(project_root) / ".claude" / "agents" / f"{short}.agent.md",
        )
        for stripped in read_frontmatter_lines(agent_file):
            if stripped.startswith("tools:"):
                bracket_start = stripped.find("[")
                bracket_end = stripped.find("]")
                if bracket_start != -1 and bracket_end != -1:
                    return [
                        t.strip()
                        for t in stripped[bracket_start + 1:bracket_end].split(",")
                        if t.strip().startswith("mcp__")
                    ]
        return []

    def _build_cmd(self, task, project_root):
        cmd = [self.cli, "-p", self._prompt(task, project_root),
               "--output-format", "stream-json", "--verbose",
               "--agent", task["owner_agent"],
               "--permission-mode", self.permission_mode]
        if self.model:
            cmd += ["--model", self.model]
        for d in self.add_dirs:
            cmd += ["--add-dir", d]
        mcp_tools = self._mcp_tools_from_frontmatter(task["owner_agent"], project_root)
        # Load the MCP servers (e.g. Playwright) only when this agent actually
        # declares mcp__* tools — the CLI's cwd=<project> has no .mcp.json of its own.
        if mcp_tools and self.mcp_config:
            cmd += ["--mcp-config", self.mcp_config]
        for tool in mcp_tools:
            cmd += ["--allowedTools", tool]
        cmd += self.extra_args
        return cmd

    # ---- live activity trace ------------------------------------------------
    def _trace_path(self, project_root, task_id):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(task_id))
        return Path(project_root) / "artifacts" / "agent-trace" / f"{safe}.jsonl"

    @staticmethod
    def _tool_detail(inp):
        """Pick the single most informative arg of a tool call for the feed."""
        inp = inp or {}
        for key in ("file_path", "path", "command", "pattern", "url", "query", "prompt"):
            if inp.get(key):
                v = str(inp[key]).replace("\n", " ")
                return v if len(v) <= 140 else v[:137] + "..."
        return ""

    def _distill(self, obj):
        """Turn one stream-json line into 0+ compact feed records (Light mode:
        tool calls + short text + the final result; reasoning detail dropped)."""
        recs = []
        t = obj.get("type")
        if t == "assistant":
            for c in obj.get("message", {}).get("content", []):
                if c.get("type") == "text" and (c.get("text") or "").strip():
                    recs.append({"kind": "text", "text": c["text"].strip()[:200]})
                elif c.get("type") == "tool_use":
                    recs.append({"kind": "tool", "tool": c.get("name"),
                                 "detail": self._tool_detail(c.get("input"))})
        elif t == "result":
            recs.append({"kind": "result", "status": obj.get("subtype")})
        return recs

    def _append_trace(self, trace, rec):
        rec.setdefault("ts", time.strftime("%H:%M:%S"))
        with trace.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def run(self, task, project_root):
        cmd = self._build_cmd(task, project_root)
        trace = self._trace_path(project_root, task["task_id"])
        trace.parent.mkdir(parents=True, exist_ok=True)
        # fresh trace per invocation so the feed reflects the current attempt
        trace.write_text(json.dumps({
            "seq": 0, "kind": "start", "ts": time.strftime("%H:%M:%S"),
            "agent": task["owner_agent"], "task_id": task["task_id"],
            "title": task.get("title", ""),
        }) + "\n")

        try:
            proc = subprocess.Popen(
                cmd, cwd=str(project_root), text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            raise UnrecoverableError(f"agent CLI {self.cli!r} not found: {e}") from e

        # Watchdog: kill a hung agent so a timeout stays a recoverable failure.
        killed = {"v": False}
        timer = threading.Timer(self.timeout,
                                lambda: (killed.__setitem__("v", True), proc.kill()))
        timer.start()
        result_obj, diag, seq = None, [], 0
        try:
            for raw in proc.stdout:               # stream line-by-line as it arrives
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    diag.append(raw[:200])        # non-JSON (e.g. stderr) → diagnostics
                    continue
                for rec in self._distill(obj):
                    seq += 1
                    rec["seq"] = seq
                    self._append_trace(trace, rec)
                if obj.get("type") == "result":
                    result_obj = obj
            proc.wait()
        finally:
            timer.cancel()

        if killed["v"]:
            raise RecoverableError(f"agent {task['owner_agent']} timed out "
                                   f"after {self.timeout}s")
        if proc.returncode:
            # Non-zero exit is transient; the engine retries, and the retry cap
            # converts a persistent failure into an escalation (SPEC §8.3).
            raise RecoverableError(
                f"agent {task['owner_agent']} exited {proc.returncode}: "
                f"{' | '.join(diag[-3:])[:500]}")
        if isinstance(result_obj, dict) and result_obj.get("is_error"):
            raise RecoverableError(
                f"agent {task['owner_agent']} reported error: "
                f"{str(result_obj.get('subtype'))[:200]}")
        # on-disk artifacts are authoritative; the result envelope carries metrics.
        return result_obj or {"raw": " | ".join(diag[-3:])}
