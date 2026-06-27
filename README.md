# Agentic SDLC & Software Factory

An agent-native SDLC that delivers software projects end-to-end using role-specialized Claude Code subagents, schema-validated artifacts, and a deterministic orchestration engine that owns control flow.

## Structure

```
.claude/agents/          # Subagent definitions (product, planner, architect, developer, reviewer, qa, devops, orchestrator)
schemas/                 # JSON Schema definitions for every artifact
artifacts/               # Reference instances (*.example.json) validated by the test suite
src/orchestrator/        # Deterministic engine: state machine, schema gates, DAG scheduling, retries, event log
observability/           # Zero-dependency live dashboard (reads workflow_state.json + events.log.jsonl)
projects/<name>/         # Self-contained projects; per-project artifacts/ holds run state + events.log.jsonl
tests/                   # Schema validation + orchestrator engine tests
SPEC.md                  # Authoritative lifecycle + orchestrator specification
CLAUDE.md                # Rules, artifact table, stage gates
```

Each project is self-contained under `projects/<name>/`, including its own
`artifacts/workflow_state.json` and `artifacts/events.log.jsonl` (there is no
repo-root run log).

## Pipeline

```
product-agent → planner-agent → architect-agent → developer-agent(s)
→ reviewer-agent → qa-agent → devops-agent → e2e-agent
```

Each stage reads input artifacts → produces output artifacts → the orchestrator
validates them against their schema and applies the stage gate → appends an event
to `events.log.jsonl`.

For projects with a browser UI, `devops-agent` deploys the full app on a single
browsable URL and `e2e-agent` then validates it in a real browser via the
**Playwright MCP** server (`@playwright/mcp`), producing `e2e_report.json`. A failed
E2E run re-dispatches the developer subtree once (bounded rework) before escalating.

**Browser-validation prerequisites** (only needed to run the live `e2e-agent`): Node.js
on PATH for `npx @playwright/mcp`, and the Playwright browsers installed once with
`npx playwright install --with-deps chromium`. The Playwright MCP server is declared in
`.mcp.json`; `e2e-agent` is granted `mcp__playwright__*` tools in `.claude/settings.json`.

## Running a workflow

The orchestrator owns control flow; agents own content (SPEC §8). To drive (or
resume) a project through the deterministic engine:

```bash
# Start a new workflow from a raw request (product → planner → architect → DAG):
PYTHONPATH=src python3 -m orchestrator projects/<name> --prompt "Build a CLI todo app" --yes

# Resume an existing run and approve the next checkpoint(s):
PYTHONPATH=src python3 -m orchestrator projects/<name> --approve requirements,architecture,production_deploy

# Validate an already-produced run without invoking any agent (no LLM/cost):
PYTHONPATH=src python3 -m orchestrator projects/<name> --replay
```

- The engine schedules the `workplan.json` task DAG, validates every output, enforces
  the stage gates and the three human checkpoints, retries recoverable failures, runs a
  bounded review→fix rework loop on a rejected review (`--max-rework`, default 2), and
  escalates the rest.
- `--yes` auto-approves every human checkpoint (unattended). Without it the run pauses at
  each checkpoint; resume with `--approve requirements,architecture,production_deploy`.
- Default runner is the live `ClaudeAgentRunner` (invokes the real `.claude/agents/`
  subagent per task). `--replay` uses the `ReplayRunner` to re-validate prior outputs
  with no LLM cost. (`CallableRunner` is the in-process test runner — see
  `src/orchestrator/runners.py`.)
- Recover a blocked run with `--retry <task_id>` or `--retry-failed`.

Monitor progress live with the dashboard:

```bash
./observability/serve.sh <project-name>
```

## Tests

```bash
python3 -m pytest tests/          # schema validation + orchestrator engine (incl. fault injection)
```
