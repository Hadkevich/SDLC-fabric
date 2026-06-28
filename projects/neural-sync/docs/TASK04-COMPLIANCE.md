# NEURAL SYNC — Task04 Requirements Compliance

Honest, file-level mapping of each Task04 requirement to its implementation status after the
compliance pass. Status: ✅ done · 🟡 partial / pragmatic · ⚠️ deliberate deviation (documented)
· ❌ not done.

> Verification baseline: `docker exec neural-sync-backend-1 pytest` → **247/247 passing, 0 failed**; pgvector
> ANN verified live; risk-refresh / admin overrides / ingestion / project CRUD verified live against
> the running container.

---

## §1 Mission Objective

| Capability | Status | Where |
|---|---|---|
| Match devs↔projects: technical | ✅ | `engine/matching.py` `compute_skill_score` (alias-aware set overlap × experience) |
| …work style & communication | ✅ | `compute_workstyle_score` (centered cosine, 8-dim) |
| …motivation & career intent | ✅ | `compute_motivation_score`, `compute_growth_score` |
| …availability & time zone | ✅ | `compute_timezone_score` now folds an **availability-fit** factor into w4 (was: tz only) |
| Continuously re-optimize allocation | ✅ | `services/reoptimization.py` (real `rescore`/`risk-refresh`/`reembed`) + opt-in `services/scheduler.py` (APScheduler, `NEURAL_SYNC_REOPT_INTERVAL`) |
| Predict bench / burnout | ✅ | `engine/risk.py` (`compute_bench_risk`, `compute_burnout_risk`) |
| Predict team mismatch | ✅ | `risk.py` `compute_team_mismatch_probability`; surfaced in Manager UI ("Team Fit") |
| Recommend project transitions / internal mobility | ✅ | `developers.py` `/reallocation-suggestion` (ANN-backed) + `/similar` (ANN peers) |
| Recommend skill growth paths | 🟡 | `growth_potential` list (deterministic; LLM career-hints are a noted enhancement) |

## §2 Architecture
- **2.1 Identity / 2.2 Project Genome** ✅ — `db/models.py` (DeveloperProfile, ProjectProfile).
- **2.3 Matching Engine** ✅ — 5-weight formula, weights live from `WeightConfig` per request.

## §3 AI Layer (Claude)
- **Provider** ⚠️ — runs on **Google Gemini** (free tier) by deliberate cost decision; provider
  is swappable via the prompt artifact `model_name` (ADR-004). The SDLC factory that *builds*
  this app uses Claude agents. Documented, not hidden.
- **3.1 Profile Enrichment** ✅ — `services/enrichment.py` (LLM + heuristic fallback), now fed by
  real **source connectors** (§5).
- **3.2 Recommendation generation** ✅ "why this match" (LLM) · 🟡 transitions/career hints (deterministic).
- **3.2 / §11 Cyberpunk prompt directive** ✅ — the `match_explanation` system prompt now opens with
  "workforce optimization AI in a cyberpunk setting … prioritize long-term engagement, not short-term
  efficiency" (`artifacts/prompts/match_explanation_v1.json`), while keeping the "EXACTLY three
  sections" output contract. No test pins the wording; placeholders/sections unchanged.

## §4 Real-Time Optimization Engine
- **4.1 Bench / 4.2 Burnout** ✅. **4.3 Reallocation** ✅ (`/reallocation-suggestion`).
- **Real-time / continuous** ✅ — endpoints are real (no longer stubs); optional scheduler loop
  closes the continuous path. **§10 "static allocation" failure condition → avoided.**

## §5 Data Pipelines
- **Sources** ✅/🟡 — `src/connectors/`: **GitLab (live, httpx, read-only)**, HR (CSV/JSON file),
  Slack (export JSON), CV (txt/md), Jira (credential-gated adapter). All degrade gracefully
  offline/without credentials.
- **ETL → vectorization → storage** ✅ — `src/etl/orchestrator.py` → existing `enrich_profile`
  → `embeddings.py` → pgvector. Endpoints `/ingestion/{connectors,file,gitlab,jira}`.
- **Vector DB** ⚠️ — **pgvector** (not Pinecone/Weaviate) — chosen for atomic GDPR erasure
  (ADR-001). Now genuinely **load-bearing**: `engine/retrieval.py` ANN (`<=>`) on the critical
  path for recommendations + similar-developer search.

## §6 Frontend (3 roles) — gated to match the spec's view split exactly

| View | Role | Capabilities (and the gate) |
|---|---|---|
| **Developer View** | `developer` | Recommended projects, match explanations, growth paths, accept/reject — own data only |
| **Manager View** | `manager` (+admin) | Team composition health + risk alerts (`/teams/{id}/risk-summary`, Team-Fit), allocation **suggestions** (`/reallocation-suggestion`), Roster (paginated/filterable for 10k), team analytics |
| **Admin View** | `admin` only | **Weight tuning** (`PUT /config/weights`), **system overrides** (`/admin/allocations` CRUD), **Project Genome management** (`GET/POST/PUT/DELETE /projects`, Projects tab), re-optimization triggers (`rescore`/`reembed`/`risk/refresh`), GDPR erasure-audit |

`admin` is a **superset** of `manager` (also sees team health), but the frontend now renders the
Admin-exclusive tools in a **visually separated "Admin" group** (after a divider) that does **not
render at all** for managers — not just a hidden button. Admin-exclusive capabilities (weight tuning,
system overrides, project create) return **403 for managers** on the backend too — matching the spec,
where weight tuning and system overrides are the *Admin* View, not the Manager View. The role chip
shows 🛡 Admin vs ⚙ Manager. Gates: `require_admin` / `require_admin_or_manager` in `core/auth.py`,
plus the inline manager+admin gate on `/projects`. (Ingestion §5 is not a §6 view — allowed for
manager+admin.)

## §7 Tech Stack
- Backend FastAPI/Python ✅ · Frontend React/TS ✅ · AI ⚠️ Gemini (see §3) ·
  Vector DB ⚠️ pgvector (see §5) · Infra 🟡 Docker + Cloud Run/Terraform manifests (`deploy/`, see §F).

## §8 Non-Functional
- GDPR erasure ✅ (cascade + audit). Explainable AI ✅. Latency <500ms ✅ (sync stub, async LLM).
- **Scalable to 10k+** ✅ — pgvector HNSW + ANN candidate retrieval + O(page) paginated roster
  (`GET /developers`, GIN/btree indexes) + denormalized risk cache. Seed + load harness:
  `scripts/seed_scale.py`, `loadtest/` (see `artifacts/perf/`).

## §9 MVP / §10 Failure Conditions
- MVP ✅ (ingestion, matching, explanation, dashboards). Failure conditions all **avoided**:
  not skill-only ✅, explainable ✅, not static (re-optimization real) ✅, rejection tracked ✅.

## §11 Cyberpunk Directive / §12 Deliverables
- Signal extraction over UI polish ✅; explainability mandatory ✅.
- Module impl ✅ · API contracts (`artifacts/api-contracts.json`) ✅ · prompt artifacts ✅ ·
  good/bad-match test scenarios ✅.

---

## Notable deviations (deliberate, documented)
1. **Gemini, not Claude** for the app LLM — cost decision; Claude is a config-swap (ADR-004).
2. **pgvector, not Pinecone/Weaviate** — atomic GDPR erasure beats a second store (ADR-001).
3. **Connectors**: GitLab is live; Slack/Jira/HR via file-import + credential-gated adapters
   (full live OAuth integrations remain a roadmap item).
4. **Deploy/e2e**: the multi-stage `Dockerfile` now **builds** (verified; the bogus `COPY alembic`
   was fixed). The Playwright e2e still needs a browser-MCP environment to run live (see
   `../../EVALUATION.md`).
