# Requirements → Evidence Traceability

> **Audit map.** Every hackathon requirement (the *Agent-Driven SDLC & Software
> Factory* brief) and every Task-04 *NEURAL SYNC* requirement, mapped to concrete
> evidence in this repo. Use this as the checklist when grading the submission.
>
> Legend: **✅ met** · **🟡 partial / caveated** · **❌ gap**.
> Status is honest, not aspirational — caveats are stated inline and expanded in
> `EVALUATION.md`. Source requirements live in the team's brief docs
> (`docs/requirements/HACKATHON-REQUIREMENTS.md`, `docs/requirements/Task04-requirements.md`,
> `docs/requirements/Acceptance-Criteria.md`).

---

## Part A — Agentic SDLC & Software Factory (the hackathon task)

### Phase 1 — Define the Agentic SDLC (the spec)

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| 1.1 | All 8 brief lifecycle stages, agent-native (+ a 9th `e2e_validation`) | ✅ | `SPEC.md §3` + `STAGE_SEQUENCE` (`src/orchestrator/engine.py:29`). Stages: requirement_ingestion → task_decomposition → planning_architecture → code_generation → code_review → testing_validation → deployment → **e2e_validation** → monitoring_feedback |
| 1.2 | Each stage: dedicated agent role, I/O contract, success criteria | ✅ | `SPEC.md §3.1–3.9` (owner + input + output + success per stage); `AGENT_STAGE` map (`engine.py:36`) |
| 1.3 | Agent roles & responsibilities (inputs / outputs / decision boundaries) | ✅ | `.claude/agents/*.agent.md` (9 agents incl. `e2e-agent`); `SPEC.md §4`; per-role tool lists are least-privilege |
| 1.4 | Communication protocol: message schema, task contract, state, errors, escalation | ✅ | `SPEC.md §5` + `event.schema.json` (immutable JSONL events); `workflow_state.schema.json` (live state); `CLAUDE.md` "Event log format" |
| 1.5 | Artifact standards (requirements / tasks / code specs / tests / review reports) | ✅ | `schemas/` (12 JSON Schemas); `SPEC.md §6` artifact table; `CLAUDE.md` required-artifacts table; reference instances in `artifacts/*.example.json` |
| 1.6 | Governance & constraints (guardrails, observability) | ✅ | `SPEC.md §9` (two-tier security baseline, no-secrets, no-deploy-without-QA); `validation.py` `scan_source`; `observability/` dashboard + `events.log.jsonl` |

**Deliverable "Agentic SDLC Specification v1":** `SPEC.md` (authoritative;
schema-wins rule stated in §1).

### Phase 2 — Build the Agentic Workflow Engine

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| 2.1 | Orchestrator (state machine / DAG) | ✅ | `src/orchestrator/engine.py` — deterministic wave scheduler over the `workplan.json` `depends_on` DAG (`_topo_order`, `run`, `_run_wave`) |
| 2.2 | Passes structured outputs between agents (not free-form chat) | ✅ | Every output schema-validated at the gate (`validation.py` `validate_artifact`, `schema_for_output`; `engine.py` `_check`) |
| 2.3 | Tracks state of the entire workflow | ✅ | `workflow_state.json` single source of truth, atomic write+rename (`engine.py` `_persist`); reconstructable by folding `events.log.jsonl` |
| 2.4 | Agent runtime + memory layer + tooling layer | ✅ | Runtime: `runners.py` `ClaudeAgentRunner` (spawns `claude --agent <role>`). Memory: artifact files + event log. Tooling: per-agent least-privilege tool lists |
| S2.a | *Stretch:* multi-agent parallelization | 🟡 | Engine runs independent tasks concurrently (`_run_wave`, `ThreadPoolExecutor`, `max_parallel`). Caveat: confirm parallel developer tasks write **task-scoped** `code_spec/<task_id>.json` (planner rule, `planner.agent.md`) so outputs don't collide — see `EVALUATION.md` |
| S2.b | *Stretch:* self-improving / feedback loop | ✅ | Bounded fix loops at three points: a rejected review and a failed e2e run (`_request_rework`/`_drain_rework`/`_apply_rework`; per-stage cap `STAGE_REWORK_CAP` — review 2, e2e 1), **and a closed `monitoring_feedback` loop** — an unhealthy deploy drives a Level-1 in-run health rework (cap 1) then up to `max_feedback_cycles` Level-2 cross-run re-plans where the **product agent folds `backlog.json` into updated requirements** (`engine.py:_try_health_rework`/`_try_feedback_cycle`/`_append_backlog`; `--feedback-loop N`; default 0 = signal only). Live runtime telemetry beyond the deploy health probe (APM/error-rate) remains future work (`SPEC.md §3.9`) |
| S2.c | *Stretch:* cost/performance optimization | ✅ | Per-role model strategy (opus/sonnet/haiku, `SPEC.md §4`); per-task cost/token metrics folded from the event log; optional `max_cost_usd` breaker (`SPEC.md §9`) |
| S2.d | *Stretch:* Git integration (agent-driven PRs) | ❌ | Not implemented; deploy is local-Docker. Candidate next step |

### Phase 3 — Prove It Works (end-to-end demo)

The demo project is **`projects/neural-sync/`** — the Task-04 NEURAL SYNC app
(FastAPI + React + pgvector), built by the agent pipeline.

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| 3.1 | Non-trivial app built end-to-end | 🟡 | `projects/neural-sync/` — real FastAPI backend (`src/`), React frontend (`frontend/`), 12-table data model, pgvector. **Caveat:** the recorded run reached review+QA but **halted at the deploy gate** (see 4.3 / `EVALUATION.md`) |
| 3.2 | Requirements generated internally | ✅ | `projects/neural-sync/artifacts/requirements.json` + `.md` (product-agent) |
| 3.3 | Architecture defined by agent | ✅ | `artifacts/architecture.json`, `api-contracts.json`, `data-model.json`, `adr/adr-001..004.json` (architect-agent) |
| 3.4 | Code generated | ✅ | `projects/neural-sync/src/**` + `frontend/src/**`; `artifacts/code_spec.json` |
| 3.5 | Tests created & executed | ✅ | `artifacts/test_plan.json` — **77/77 pass, 0 failed**, all 13 acceptance criteria covered |
| 3.6 | Deployment automated (local or cloud) | 🟡 | DevOps path exists (`devops.agent.md` builds Dockerfile + health-checks → `release_report.json`). In the recorded run deployment was **aborted at the gate** (review verdict `rejected`); `release_report.json` verdict = `failed`, no image built |
| 3.7 | Deployed app validated in a real browser *(extra, beyond brief)* | 🟡 | `e2e_validation` stage (`e2e-agent` + Playwright MCP) drives the live UI per acceptance criterion → `e2e_report.json` (`SPEC.md §3.8`; schema `schemas/e2e_report.schema.json`; example `artifacts/e2e_report.example.json`). **Caveat:** the recorded `neural-sync` run halted at deploy, so e2e was never reached — capability exists, not yet exercised on the demo |

### Success criteria

| # | Criterion | Status | Evidence |
|---|-----------|:------:|----------|
| 4.1 | ≥80% of runs without human intervention | 🟡 | Engine supports unattended runs (`--yes`); the **single recorded** end-to-end run ended in human escalation at deploy. Claim needs ≥1 clean autonomous run to be demonstrated — see `EVALUATION.md` |
| 4.2 | Artifacts consistent (QA-generated tests pass) | ✅ | `test_plan.json` 77/77 pass; every required artifact present and schema-valid in `projects/neural-sync/artifacts/` |
| 4.3 | Recover from ≥2 simulated failures | 🟡 | Mechanically present: per-task retry with back-off (`_retry`, transient timeouts recovered twice in the demo log) + bounded fix loop on a **rejected review or a failed e2e run** (`_request_rework`, per-stage `STAGE_REWORK_CAP`). **Caveat:** the recorded run predates the rework loop and shows the rejected review escalating instead of looping — a fresh re-run is needed to demonstrate closure |
| 4.4 | Re-run with modified requirements | ✅ | Each run is keyed by `workflow_id`; product-agent supports update mode; `--replay` re-validates prior outputs (`runners.py` `ReplayRunner`) |

### Final deliverables

| # | Deliverable | Status | Evidence |
|---|-------------|:------:|----------|
| 5.1 | Agentic SDLC Spec (document) | ✅ | `SPEC.md` |
| 5.2 | Architecture diagram of agent ecosystem | ✅ | `ARCHITECTURE-DIAGRAM.md` (Mermaid: ecosystem + pipeline + control loop + gates) |
| 5.3 | Working prototype (codebase) | ✅ | `src/orchestrator/` + `schemas/` + `tests/` (`python3 -m pytest tests/`) |
| 5.4 | Demo project built by agents | 🟡 | `projects/neural-sync/` (caveat as in 3.1/3.6) |
| 5.5 | Evaluation report | ✅ | `EVALUATION.md` |

---

## Part B — Task-04 NEURAL SYNC (the demo product's own requirements)

Source: `docs/requirements/Task04-requirements.md`, `docs/requirements/Acceptance-Criteria.md`,
`docs/requirements/Task04-idea-brief.md`.
Evidence paths are under `projects/neural-sync/`.

### Core architecture & matching

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| N1 | Identity Layer — `DeveloperProfile` (skills, work_style, motivation, timezone, goals, history) | ✅ | `src/db/models.py` `DeveloperProfile`; `artifacts/data-model.json` |
| N2 | Project Genome — `ProjectProfile` (required_skills, team_structure, intensity, innovation, tz overlap, growth) | ✅ | `src/db/models.py` `ProjectProfile`; `artifacts/data-model.json` |
| N3 | Matching engine: `MATCH_SCORE = Σ wᵢ·componentᵢ` over skill/workstyle/motivation/timezone/growth | ✅ | `src/engine/matching.py` (per-component functions); weights default 0.30/0.25/0.20/0.15/0.10 |
| N4 | **Not skill-only** — behavioral layer (workstyle + motivation) is load-bearing | ✅ | `compute_workstyle_score` / `compute_motivation_score` (cosine over centered vectors); the "skill-only trap" case (idea brief Example B) is a test in `test_plan.json` (bad-match score ≤ 0.45) |
| N5 | Weights configurable via admin panel (Σ = 1.0) | ✅ | `GET/PUT /api/v1/config/weights` (`src/api/config.py`); `WeightConfig` singleton, sum validated to 1.0 ±0.001 |
| N6 | Hybrid skill scoring (set overlap + embedding similarity) + ANN retrieval at scale | 🟡 | Set-overlap + experience weighting in `matching.py`; embeddings layer present (`src/engine/embeddings.py`, pgvector HNSW in data model). Caveat (NBI-003 in `review_report.json`): embeddings recorded as `embedding_status='pending'` — semantic-similarity path not fully exercised in the recorded run |

### AI / LLM explanation layer

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| N7 | LLM generates explanation / risks / growth potential | ✅ | `src/services/claude_service.py`; prompt artifact `artifacts/prompts/match_explanation_v1.json` (3 sections: skill / behavioral / growth) |
| N8 | **Raw behavioral vectors never passed to the LLM** (privacy + audit) | ✅ | `ClaudeService.build_prompt_context` passes only aggregate scores + structural facts; `work_style_vector` / `motivation_vector` excluded (idea brief Example C contract) |
| N9 | Prompt is a versioned artifact; LLM provider swappable without code change | ✅ | `artifacts/prompts/match_explanation_v1.json` + `PromptVersion` table; provider configurable (Gemini default, Claude alternative) |
| N10 | Explanation is async / outside the match SLA | ✅ | `POST /matches` returns a deterministic stub immediately (201); background task fills the LLM explanation; `explanation_source` tracks state |

### Re-optimization engine

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| N11 | Bench prediction | ✅ | `src/engine/risk.py` bench-risk from project end dates + follow-on detection |
| N12 | Burnout detection — `min(1, (weeks/48)·intensity·(1−motivation_alignment))` | ✅ | `src/engine/risk.py` burnout-risk; matches idea-brief formula; AC4 test in `test_plan.json` |
| N13 | Reallocation suggestions (internal moves / skill-bridge), human-in-the-loop | 🟡 | Risk scores + badges surfaced (`GET /developers/{id}/risk`); reallocation *proposal* via the same engine is specified (idea brief §5/Example D) but is a thin slice in the recorded run — confirm against `src/` |

### Data, frontend, non-functional

| # | Requirement | Status | Evidence |
|---|-------------|:------:|----------|
| N14 | PostgreSQL + vector store | ✅ | `requirements.txt` (asyncpg, pgvector); `src/db/`; migrations `src/db/migrations/versions/001_initial_schema.py` |
| N15 | Three roles: Developer / Manager / Admin views | 🟡 | `frontend/src/pages/DeveloperDashboard.tsx`, `ManagerDashboard.tsx` (✅). Admin weight-tuning exists at the **API** (`/config/weights`); a dedicated Admin **page** is not present in `frontend/` |
| N16 | Manager view shows risk badges **without** raw behavioral vectors | 🟡 | Enforced server-side (vectors never in responses) + `RiskBadge.tsx`. Caveat (**BLK-001**): `GET /teams/{team_id}/risk-summary` returns HTTP 501 in the recorded run → Manager dashboard non-functional at runtime until fixed (`EVALUATION.md`) |
| N17 | GDPR: cascade erasure + audit log | ✅ | `DELETE /developers/{id}` cascade across 6 entity classes + `ErasureAuditLog`; `GET /admin/erasure-audit/{id}`; GDPR tests in `test_plan.json` (TC-051–055) |
| N18 | Explainable AI (each score component inspectable) | ✅ | Component scores stored per `MatchRecord` (skill/workstyle/motivation/timezone/growth); deterministic core is auditable by construction |
| N19 | Latency < 500ms per match (LLM async, outside SLA) | ✅ | Synchronous deterministic score + stub; `VECTOR_SEARCH_TIMEOUT_MS` graceful degradation; ADR-003 (`artifacts/adr/adr-003-latency-sla.json`) |
| N20 | Scale to 10k+ developers | 🟡 | Architecture supports it (pgvector HNSW ANN retrieval, indexed queries); not load-tested in the recorded run |

### Failure conditions (system is "broken" if any hold)

| # | Failure condition | Avoided? | Evidence |
|---|-------------------|:--------:|----------|
| F1 | Matching is skill-only (no behavioral layer) | ✅ avoided | Behavioral cosine is load-bearing (N4); skill-only-trap test passes |
| F2 | No explainability (can't say **why** a match exists) | ✅ avoided | Per-component scores + LLM explanation (N7, N18) |
| F3 | Static allocation (no re-optimization) | ✅ avoided | Bench/burnout engine + reallocation proposal (N11–N13) |
| F4 | Developers reject recommendations > 50% | ⏳ unmeasured | Feedback + rejection-rate analytics exist (`/analytics/rejection-rate`, returns null below `REJECTION_RATE_MIN_SAMPLES`); needs real usage data |

---

## Summary

- **Control plane (Phase 1 + 2):** strong and complete — deterministic engine,
  schema-gated handoffs, event-sourced state, least-privilege model-diverse fleet,
  bounded retry + rework + escalation. This is the core of the submission.
- **Demo (Phase 3):** the NEURAL SYNC app is real and substantial, and QA is green
  (77/77), but the **recorded run halted at the deploy gate** on a legitimate contract
  violation (BLK-001) and predates both the rework loop and the `e2e_validation` stage
  now in the engine. The honest claim is "built through review + QA by agents; deploy
  blocked pending one fix and a re-run."
- **Open items** to turn 🟡 → ✅: re-run `neural-sync` on the current engine (closes
  3.1/3.6/3.7/4.1/4.3 and exercises the `e2e_validation` browser stage), fix BLK-001
  (N16), exercise the embeddings/ANN path (N6), add an Admin page (N15), and add
  Git/PR + CI automation (S2.d).

Full narrative and per-item detail: **`EVALUATION.md`**.
Architecture detail: **`ARCHITECTURE-DIAGRAM.md`**. Authoritative spec: **`SPEC.md`**.
