# Agentic SDLC & Software Factory

An agent-native SDLC that delivers software projects end-to-end using role-specialized Claude Code subagents, schema-validated artifacts, and a deterministic orchestration engine that owns control flow.

## Structure

```
.claude/agents/          # Subagent definitions (product, planner, architect, developer, reviewer, qa, devops, orchestrator)
schemas/                 # JSON Schema definitions for every artifact
artifacts/               # Reference instances (*.example.json) validated by the test suite
src/orchestrator/        # Deterministic engine: state machine, schema gates, DAG scheduling, retries, event log
src/sdlcdb/              # DB-backed control plane: SQLite store (tasks / artifacts / events), atomic claim
src/watcher/             # Watcher poll loop + file-on-edge worker + CLI (multi-pipeline engine)
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

## DB-backed, multi-pipeline engine (`src/sdlcdb` + `src/watcher`)

A second engine runs the same agents and gates but stores all inter-agent JSON
artifacts in a **SQLite DB** (the source of truth) instead of files, and drives
**many pipelines concurrently** off one shared tasks table. Project *code* files
still land on disk under `projects/<name>/`. Three roles:

- **Watcher** (`src/watcher/watcher.py`) — polls the DB every few seconds: routes
  new terminal events, then dispatches runnable tasks to agent workers up to a
  **global N-per-role** ceiling, and requeues any worker whose lease expired.
- **Orchestrator / router** (`src/orchestrator/orchestrator.py`) — deterministic:
  on each completed task it applies the stage gate (review / deploy / e2e), grows
  the pipeline (product → planner → expand the workplan DAG), runs the bounded
  developer **rework** loop on a rejected review / failed e2e, or dead-letters.
- **Evaluator** (`src/orchestrator/evaluator.py`, `evaluator-agent`) — the only LLM
  on the failure path: diagnoses an `error`, writes a **healing prompt**, and the
  router re-injects the failed subtree (skip-unchanged), bounded by a heal cap.

Workers (`src/watcher/worker.py`) are the **file-on-edge adapter**: they materialize
a task's input artifacts from the DB into temp files, run the agent unchanged,
validate its JSON outputs against `schemas/`, ingest them back into the DB, and
delete the local JSON (code stays on disk).

```bash
# Submit a pipeline (creates a tasks-table row; --yes auto-approves human gates):
PYTHONPATH=src python3 -m watcher submit --prompt "Build a CLI todo app" --name todo --yes

# Run the watcher loop (polls every --tick s; --idle-exit stops when there's no work):
PYTHONPATH=src python3 -m watcher run --developers 3 --tick 5

# Approve a human checkpoint so a paused pipeline resumes:
PYTHONPATH=src python3 -m watcher approve <pipeline_id> architecture

# Inspect state (add --json for CI / observability):
PYTHONPATH=src python3 -m watcher status [<pipeline_id>] [--json]
```

All state lives in one `artifacts.db` (override with `--db`). The DB is the source
of truth; the live event log folds back into a status projection
(`Database.fold_state`), so a crash never silently loses progress.

> The two engines are complementary: `python -m orchestrator` is the original
> single-process, file-state engine; `python -m watcher` is the DB-backed,
> multi-pipeline engine. They share the agents, schemas, and gate logic.

## Tests

```bash
python3 -m pytest tests/          # schema validation + both engines (incl. fault injection)
```

Coverage spans schema validation (`test_schemas.py`), the original engine
(`test_orchestrator.py`), and the DB-backed engine: the store (`test_db.py`), the
file-on-edge worker (`test_worker.py`), the router/evaluator (`test_router.py`),
and the watcher loop incl. multi-pipeline + the global N ceiling (`test_watcher.py`).
