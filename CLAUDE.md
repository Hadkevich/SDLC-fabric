# Agentic SDLC — Project Instructions

## Goal
Deliver small real software projects end-to-end using a multi-agent pipeline with deterministic handoffs and machine-readable artifacts.

## Rules
- Follow the spec in `SPEC.md` exactly.
- Produce structured JSON artifacts — never substitute prose for a required JSON file.
- Do not change requirements without explicit product-agent output.
- Do not write code before architecture artifacts exist.
- Always run tests before marking any stage complete.
- Log every stage transition as an event in `events.log.jsonl`.
- Each agent writes only the artifacts assigned to its stage — no cross-stage writes.
- Escalate on ambiguity, repeated failures (> max_retries), or unsafe requests.

## Project layout
Every project is self-contained under `projects/<project-name>/`:
```
projects/<project-name>/
  artifacts/          ← all pipeline JSON artifacts + events.log.jsonl
    adr/
  <source files>      ← actual code produced by developer-agent
  tests/              ← tests produced by qa-agent
```
Global example/reference artifacts stay in the root `artifacts/` folder (do not write run outputs there).

## Required artifacts (per run)
All paths below are relative to `projects/<project-name>/artifacts/`.

| File | Owner | Schema |
|------|-------|--------|
| `requirements.json` | product-agent | `schemas/requirements.schema.json` |
| `workplan.json` | planner-agent | `schemas/workplan.schema.json` |
| `architecture.json` | architect-agent | `schemas/architecture.schema.json` |
| `api-contracts.json` | architect-agent | `schemas/api-contracts.schema.json` (OpenAPI 3.x) |
| `data-model.json` | architect-agent | `schemas/data-model.schema.json` |
| `adr/*.json` | architect-agent | `schemas/adr.schema.json` |
| `code_spec.json` | developer-agent | `schemas/code_spec.schema.json` |
| `test_plan.json` | qa-agent | `schemas/test_plan.schema.json` |
| `review_report.json` | reviewer-agent | `schemas/review_report.schema.json` |
| `release_report.json` | devops-agent | `schemas/release_report.schema.json` |
| `e2e_report.json` | e2e-agent | `schemas/e2e_report.schema.json` |
| `workflow_state.json` | orchestrator-agent | `schemas/workflow_state.schema.json` |
| `events.log.jsonl` | all agents (append-only) | `schemas/event.schema.json` |

## Stage gates (must pass before advancing)
- `task_decomposition` requires valid `requirements.json`
- `planning_architecture` requires valid `workplan.json`
- `code_generation` requires valid `architecture.json` + `api-contracts.json`
- `code_review` requires valid `code_spec.json`; a `rejected` verdict triggers a bounded
  review→fix rework loop (re-dispatches the developer subtree, `max_rework` default 2) —
  it never advances to QA/deploy
- `testing_validation` requires valid `code_spec.json` (planner orders it **after** `code_review`)
- `deployment` requires `review_report.json` verdict ∈ {approved, approved_with_comments} AND `test_plan.json` summary.failed == 0
- `e2e_validation` (UI projects only) requires valid `e2e_report.json` with verdict ∈
  {passed, passed_with_warnings} AND summary.failed == 0; a `failed` verdict triggers a
  bounded developer rework loop **capped at one round** (post-deploy re-runs are
  expensive), then escalates and queues the failure to `backlog.json`

## Workflow Reference
The agentic loop diagram is at `workflow/mermaid.md`. All agents should use it as the authoritative visual reference for stage sequence, ownership, and escalation paths.

## Event log format
Every agent appends one JSONL event on completion. Fields: event_id (uuid), workflow_id, stage, agent, status (success|failure|blocked|retry), input_refs[], output_refs[], summary, blocking_issues[], retry_count, timestamp (ISO 8601).

## Two execution engines (same agents, same gates)
The agents, schemas, and gate predicates above are shared by **two** orchestration
runtimes (see `SPEC.md §11`):

1. **File-state engine** — `python -m orchestrator` (`src/orchestrator/engine.py`):
   single process, one `workflow_state.json` + `events.log.jsonl` per project,
   thread-pool DAG scheduling. The layout/tables above describe this engine.
2. **DB-backed, multi-pipeline engine** — `python -m watcher`
   (`src/sdlcdb/` + `src/watcher/`): inter-agent JSON artifacts live in a SQLite DB
   (the source of truth) instead of files; project **code** still lands on disk
   under `projects/<name>/`. A **watcher** polls the DB and dispatches agent workers
   under a global N-per-role limit; a deterministic **router** applies the same
   gates and grows the task table; an **evaluator-agent** (the only LLM on the
   failure path) diagnoses an `error` and emits a healing prompt that re-injects the
   failed subtree (bounded by a heal cap, then dead-letter). Several pipelines run
   concurrently. The event log is the source of truth; live status is a projection.

When working in `src/sdlcdb/` or `src/watcher/`, target engine #2; everything else
(agents, `schemas/`, gates) is shared.
