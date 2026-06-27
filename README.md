# Agentic SDLC & Software Factory

An agent-native SDLC that delivers software projects end-to-end using role-specialized Claude Code subagents, schema-validated artifacts, and a deterministic orchestration engine that owns control flow.

> **Auditing or collecting data on this repo?** Read
> **[`REQUIREMENTS-TRACEABILITY.md`](REQUIREMENTS-TRACEABILITY.md)** first — it maps every
> hackathon requirement to the exact file that satisfies it. The source-of-truth
> requirements are vendored under [`docs/requirements/`](docs/requirements/).

## 📋 Deliverables map — where to find what

| Hackathon deliverable (brief → "Final Deliverables") | Where |
|------------------------------------------------------|-------|
| 1. Agentic SDLC Spec (document) | **[`SPEC.md`](SPEC.md)** — authoritative |
| 2. Architecture diagram of the agent ecosystem | **[`ARCHITECTURE-DIAGRAM.md`](ARCHITECTURE-DIAGRAM.md)** |
| 3. Working prototype (codebase) | **[`src/orchestrator/`](src/orchestrator/)** + [`schemas/`](schemas/) + [`tests/`](tests/) |
| 4. Demo project built by agents | **[`projects/neural-sync/`](projects/neural-sync/)** ([README](projects/neural-sync/README.md)) |
| 5. Evaluation report (what worked / failed) | **[`EVALUATION.md`](EVALUATION.md)** |

| Required Phase-1 spec component | Where (all in `SPEC.md`) |
|---------------------------------|--------------------------|
| Lifecycle stages (agent-native, 8 brief stages + a 9th `e2e_validation`) | `SPEC.md §3` |
| Agent roles & responsibilities (9 agents) | `SPEC.md §4` + [`.claude/agents/`](.claude/agents/) |
| Communication protocol | `SPEC.md §5` (+ `event` / `workflow_state` schemas) |
| Artifact standards | `SPEC.md §6` + [`schemas/`](schemas/) |
| Governance & constraints | `SPEC.md §9` (+ security scan in `src/orchestrator/validation.py`) |

| Reference | Where |
|-----------|-------|
| Requirement → evidence checklist | **[`REQUIREMENTS-TRACEABILITY.md`](REQUIREMENTS-TRACEABILITY.md)** |
| Source-of-truth requirements (verbatim) | **[`docs/requirements/`](docs/requirements/)** — `HACKATHON-REQUIREMENTS.md`, `Task04-requirements.md`, `Acceptance-Criteria.md`, `Task04-idea-brief.md` |

## Structure

```
.claude/agents/          # Subagent definitions (product, planner, architect, developer, reviewer, qa, devops, e2e, orchestrator)
schemas/                 # JSON Schema definitions for every artifact
artifacts/               # Reference instances (*.example.json) validated by the test suite
src/orchestrator/        # Deterministic engine: state machine, schema gates, DAG scheduling, retries, event log
observability/           # Zero-dependency live dashboard (reads workflow_state.json + events.log.jsonl)
projects/<name>/         # Self-contained projects; per-project artifacts/ holds run state + events.log.jsonl
tests/                   # Schema validation + orchestrator engine tests
docs/requirements/       # Source-of-truth requirements (hackathon brief + Task-04), vendored verbatim
SPEC.md                  # Authoritative lifecycle + orchestrator specification (Phase-1 deliverable)
ARCHITECTURE-DIAGRAM.md  # Agent-ecosystem + engine diagrams (Mermaid) — deliverable #2
EVALUATION.md            # What worked / what failed on the real demo run — deliverable #5
REQUIREMENTS-TRACEABILITY.md  # Every requirement → the file that satisfies it (start here when auditing)
CLAUDE.md                # Rules, artifact table, stage gates, audit entry-point
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
