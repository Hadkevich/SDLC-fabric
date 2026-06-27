# Agentic SDLC Specification v1

## 1. Purpose
This specification defines an agent-native software delivery lifecycle for autonomous coding agents. The goal is to deliver small but real software projects end-to-end with minimal human intervention while maintaining deterministic control, machine-readable artifacts, and safe escalation paths.

This document is **authoritative**. Where prose and a schema in `schemas/` disagree, the schema wins. `CLAUDE.md` carries the operational rules and must stay consistent with this spec.

## 2. Design Principles
- Agents are role-specialized and never do everything.
- Every transition requires structured output and validation.
- Orchestration is stateful; conversation is not the source of truth.
- Determinism and verifiability outrank creativity in all non-ideation stages.
- Humans intervene only at explicit escalation points or safety gates.

## 3. Lifecycle Stages

The pipeline is linear. Stages are identified by the exact keys used in `workflow_state.json`
(`schemas/workflow_state.schema.json`):

```
requirement_ingestion → task_decomposition → planning_architecture
→ code_generation → code_review → testing_validation → deployment
→ (monitoring_feedback) → complete
```

Each stage starts only when its input artifact exists and validates against its schema.

### 3.1 Requirement Ingestion — `requirement_ingestion`
Owner: Product Agent
Input: raw user request, constraints, goals, non-goals
Output: `requirements.json`, `requirements.md`
Success: requirements are unambiguous, testable, scoped, and include acceptance criteria plus open questions.

### 3.2 Task Decomposition — `task_decomposition`
Owner: Planner Agent
Input: `requirements.json`
Output: `workplan.json`
Success: all requirements map to ≥1 task; dependencies form a DAG (no cycles); no task is underspecified.

### 3.3 Planning & Architecture — `planning_architecture`
Owner: Architect Agent
Input: `workplan.json`, `requirements.json`
Output: `architecture.json`, `api-contracts.json` (OpenAPI 3.1), `data-model.json`, `adr/*.json`
Success: component boundaries, typed interfaces, runtime shape, persistence, failure modes, and deployment topology are defined; each component traces to a requirement.

### 3.4 Code Generation — `code_generation`
Owner: Developer Agent(s)
Input: architecture artifacts, one assigned workplan task
Output: source files, `code_spec.json`
Success: code builds, matches the declared contracts, and stays within the assigned task scope.

### 3.5 Code Review — `code_review`
Owner: Reviewer Agent
Input: `code_spec.json`, `api-contracts.json`, `architecture.json`, `requirements.json`
Output: `review_report.json`
Success: blocking defects are identified reliably; non-blocking comments are separated clearly. Verdict ∈ {`approved`, `approved_with_comments`, `rejected`}.
A `rejected` verdict triggers a **bounded rework loop**: the orchestrator re-dispatches
the upstream developer task(s) (and everything downstream of them) with the
`blocking_issues` as feedback, up to `max_rework` rounds (default 2), then escalates.

### 3.6 Testing & Validation — `testing_validation`
Owner: QA Agent
Input: `code_spec.json`, `api-contracts.json`, source code, requirements
Output: test files, `test_plan.json`
Success: tests trace to requirements; `summary.failed == 0`; critical paths are covered; failures are reproducible.

### 3.7 Deployment — `deployment`
Owner: DevOps Agent
Input: `review_report.json`, `test_plan.json`, validated build
Output: `release_report.json`
Success: app is deployed to the target environment and passes health checks; a rollback handle is recorded. **Production deploy is human-led (🟣).**

### 3.8 Monitoring & Feedback — `monitoring_feedback`
Owner: Orchestrator Agent
Input: the deployment's `release_report.json` (verdict + health checks)
Output: a `monitoring_feedback` event and, on an unhealthy deploy, remediation items
appended to `artifacts/backlog.json`
Success: the system detects an unhealthy deploy, summarizes impact, and queues remediation
that a future run's product agent can fold into new requirements.

> **Status:** implemented as a **minimal** post-deploy feedback pass owned by the
> orchestrator (`engine.py:_monitor`): after a successful deployment it folds the release
> health into a `monitoring_feedback` event and queues `backlog.json` remediation when the
> deploy is unhealthy. It is a feedback *signal*, not a gate — the deploy gate owns go/no-go.
> A fuller loop (live runtime telemetry, automatic re-planning) remains future work.

## 4. Agent Roles
Eight role-specialized agents are defined in `.claude/agents/`. There is no separate Monitor agent;
Stage 8, when built, is owned by the Orchestrator.

- **Product Agent** — interprets requirements and normalizes them into structured artifacts.
- **Planner Agent** — breaks requirements into a dependency-ordered task list.
- **Architect Agent** — defines system design, contracts, and ADRs.
- **Developer Agent(s)** — implement scoped code changes (parallelizable per task).
- **Reviewer Agent** — evaluates code quality, correctness, and risk; emits a verdict.
- **QA Agent** — generates and runs tests; reports pass/fail and coverage.
- **DevOps Agent** — deploys approved builds and records health checks.
- **Orchestrator Agent** — manages state transitions, retries, and escalation; owns no stage content.

**Model strategy (cost ↔ capability).** Models are assigned per role, not globally:
`architect` and `reviewer` run on **opus** (hardest reasoning; the reviewer is deliberately
a *different and stronger* model than the `developer` to break the echo chamber);
`developer`, `product`, `planner`, `qa`, `orchestrator` run on **sonnet**; the mechanical
`devops` step runs on **haiku**. Each choice is pinned in the agent's frontmatter so cost
can't silently regress; `--model` overrides all agents for a run when needed.

**Least privilege.** Each agent's tool list is the minimum for its job: only `developer`
keeps `Edit` (it patches existing code); authoring agents (`product`, `planner`,
`architect`) have no `Bash`; `reviewer` has neither `Bash` nor `Edit` (read + report only).

## 5. Communication Protocol
All agent interactions are recorded as immutable JSON events appended to `events.log.jsonl`
(`schemas/event.schema.json`). The source of truth is the event log plus the artifact files,
**not** chat history. Each event carries: `event_id`, `workflow_id`, `stage`, `agent`,
`status` ∈ {`success`, `failure`, `blocked`, `retry`}, `input_refs[]`, `output_refs[]`,
`summary`, `blocking_issues[]`, `retry_count`, and `timestamp`.

Live workflow status is held in `workflow_state.json`: each stage has a `status` ∈
{`pending`, `in_progress`, `success`, `failure`, `blocked`, `skipped`} and an `attempt` counter.

## 6. Artifact Standards
All artifacts must be parseable, versioned (`"spec_version": "v1"`), and schema-validated against
`schemas/` before the next stage runs. Where a `.json`/`.md` pair exists, the **JSON is authoritative**.

| Artifact | Owner | Schema |
|----------|-------|--------|
| `requirements.json` | Product | `schemas/requirements.schema.json` |
| `workplan.json` | Planner | `schemas/workplan.schema.json` |
| `architecture.json` | Architect | `schemas/architecture.schema.json` |
| `api-contracts.json` | Architect | `schemas/api-contracts.schema.json` (OpenAPI 3.x structural) |
| `data-model.json` | Architect | `schemas/data-model.schema.json` |
| `adr/*.json` | Architect | `schemas/adr.schema.json` |
| `code_spec.json` | Developer | `schemas/code_spec.schema.json` |
| `test_plan.json` | QA | `schemas/test_plan.schema.json` |
| `review_report.json` | Reviewer | `schemas/review_report.schema.json` |
| `release_report.json` | DevOps | `schemas/release_report.schema.json` |
| `workflow_state.json` | Orchestrator | `schemas/workflow_state.schema.json` |
| `events.log.jsonl` | all (append-only) | `schemas/event.schema.json` |

## 7. Stage Gates
A stage advances only when the gate below passes (enforced before the next stage runs):

- `task_decomposition` requires valid `requirements.json`
- `planning_architecture` requires valid `workplan.json`
- `code_generation` requires valid `architecture.json` + `api-contracts.json`
- `code_review` requires valid `code_spec.json`; the gate evaluates the **verdict**:
  `rejected` → bounded review→fix rework loop (§3.5, §8.3), never an advance;
  not‑`approved`/missing → recoverable. This catches a rejection **here**, before the
  expensive QA/deploy stages run — so QA must depend on an approved review (planner
  orders `testing_validation` after `code_review`).
- `testing_validation` requires valid `code_spec.json`
- `deployment` requires `review_report.json` verdict ∈ {`approved`, `approved_with_comments`} **AND** `test_plan.json` `summary.failed == 0` (defense‑in‑depth; a rejection is normally caught at `code_review`)

## 8. Orchestrator Contract
The Orchestrator owns **control flow only** — never the content of any artifact. Its single
responsibility is to drive `workflow_state.json` from `requirement_ingestion` to `complete`
(or `failed`) deterministically and verifiably. It must solve the following, and the rules below
are the contract it is held to:

**8.1 State & resumability.** `workflow_state.json` is the single source of truth — never the
conversation. Every action reads state, acts, then persists state with an atomic write
(write-temp + rename). On startup it reconciles: a stage marked `in_progress` whose output
artifact already exists and validates resumes at validation rather than re-invoking the agent.
The state must be reconstructable by folding `events.log.jsonl`.

**8.2 Deterministic gates.** Validation and gate predicates (§7) are evaluated mechanically, not
by judgment: load the schema → validate → evaluate the boolean. The Orchestrator may not advance a
stage without a passing gate, and may not skip validation.

**8.3 Retry vs. escalate.** Failures are classified:
- *Recoverable* (schema-validation miss, partial output, transient tool error) → retry with
  back-off up to `max_retries` (default 3).
- *Reworkable* (review verdict `rejected`) → run a bounded review→fix loop: re-dispatch the
  upstream developer subtree with the review's `blocking_issues` as feedback, up to
  `max_rework` rounds (default 2), then escalate. This is the agent-level "generate →
  feedback → modify" loop, distinct from a transient retry.
- *Unrecoverable* (security violation, unsatisfiable contract, ambiguous
  requirements, unsafe request) → escalate immediately; do not retry.
Retries are counted **per task**, not per stage. Rework rounds are counted on the reviewer
task. Repeated escalation trips a circuit breaker that halts new dispatch.

**8.4 Idempotency & exactly-once effects.** A retry must not double-apply side effects. Work is
keyed by `task_id` + `attempt`; the Orchestrator checks for a valid existing output before
re-invoking an agent, and it (not the agent) stamps `event_id` and `timestamp` on every event so
the audit log cannot be fabricated.

**8.5 Dependency scheduling.** The next unit of work is chosen from the `depends_on` DAG in
`workplan.json`: a task is runnable when all its dependencies are `success`. Independent tasks may
run concurrently. State is tracked per task so a single failed task retries in isolation.

**8.6 Human checkpoints & kill switch.** Three gates are mandatory and modeled as explicit
`awaiting_approval` states the engine blocks on and resumes from: requirements sign-off,
architecture sign-off, and production deploy. A `HALT` flag stops new dispatch while letting
in-flight work finish.

**8.7 Least privilege.** The Orchestrator coordinates; it does not author requirements, design,
code, or tests, and does not modify another agent's artifacts.

Reference control loop:
```
load state → pick next runnable task(s) from the DAG
           → if human gate: block until approved
           → invoke agent (with back-off)
           → validate output against schema (code)
           → evaluate gate predicate (code)
                pass        → mark success, stamp event, advance
                recoverable → attempt++ ; retry or escalate at cap
                unrecoverable → block + escalate
           → atomically persist state → loop
```

## 9. Governance
- No secrets in prompts or logs.
- No deployment without passing QA (`summary.failed == 0`).
- No advance past review with a blocking issue (verdict `rejected`) — enforced at the
  `code_review` gate, which triggers the bounded rework loop (§8.3).
- Security baseline has two tiers: code-execution / injection / secret sinks (`eval`,
  `exec`, `shell=True`, hard-coded secrets, …) are an **unrecoverable block**; XSS-prone
  DOM sinks that are often legitimate (`innerHTML`, `document.write`) are surfaced as
  non-blocking **warnings** to avoid false-positive hard-fails.
- No tool use outside an agent's declared (least-privilege) permissions.
- Retries are capped at `max_retries` (default 3); rework at `max_rework` (default 2);
  exhaustion or an unsafe request escalates to a human.
- An optional run-level cost ceiling (`max_cost_usd`) trips a breaker that halts new
  dispatch once cumulative agent spend (folded from the event log) reaches it.

## 10. Success Criteria
- At least 80% of workflow runs complete without human intervention.
- Artifacts are machine-readable and validate against their schemas.
- Code passes QA-generated tests.
- The system recovers from at least two simulated failures via retry/escalation.
- The workflow can be rerun with modified requirements.
