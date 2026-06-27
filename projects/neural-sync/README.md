# NEURAL SYNC — Operator ↔ Project Alignment Platform

> *"Night City doesn't reward drift. It rewards alignment."*

The Task-04 demo application, built end-to-end by the agentic SDLC pipeline in this repo.
NEURAL SYNC matches developers to projects on a **weighted signal score** — not skills
alone — and explains **why** each match works. A deterministic core computes the score
(auditable, reproducible, no LLM); a separate LLM layer turns the scores into a
human-readable explanation **without ever seeing the raw behavioral vectors**.

- **Backend:** Python 3.11 · FastAPI · async SQLAlchemy (asyncpg) · PostgreSQL + pgvector
- **Frontend:** React 18 · TypeScript · Vite
- **LLM:** Google Gemini (free tier) by default; provider-swappable via a prompt artifact
- **Auth:** JWT access token + HttpOnly refresh cookie (ADR-002)

> ✅ **Run status:** the recorded pipeline run completes end-to-end —
> 77/77 tests pass, the review gate caught one contract defect (**BLK-001**) which was
> fixed and re-approved, and deployment passed: `release_report.json` verdict `success`,
> the app live in a local Docker container with a passing health check. See
> `../../EVALUATION.md`. The setup below runs the same app locally.

---

## 1. The matching model

```
MATCH_SCORE = 0.30·skill + 0.25·workstyle + 0.20·motivation + 0.15·timezone + 0.10·growth
```

All five components are in `[0, 1]` and computed deterministically in
[`src/engine/matching.py`](src/engine/matching.py). Weights are loaded fresh from the DB
on every request (so an admin weight change propagates immediately) and must sum to 1.0.

| Component | Weight | How it's computed |
|-----------|:------:|-------------------|
| **skill** | 0.30 | set overlap (Jaccard + required-coverage) × experience factor |
| **workstyle** | 0.25 | cosine of the dev's 8-dim work-style vector vs a project-derived vector |
| **motivation** | 0.20 | cosine of the dev's motivation vector vs a project-derived vector |
| **timezone** | 0.15 | 1.0 inside the project window, else decays with hour-distance |
| **growth** | 0.10 | Jaccard of `career_goals` tokens vs `growth_opportunities` (stop-words removed) |

**Why this isn't skill-only:** two developers with identical skills can score ~0.89 vs
~0.36 purely on workstyle / motivation / timezone divergence (idea-brief Examples A & B;
tested as the good-match vs skill-only-**trap** cases in `artifacts/test_plan.json`).

### Re-optimization (`src/engine/risk.py`)
- **Burnout risk** — `min(1, (consecutive_high_intensity_weeks / 48) · intensity ·
  (1 − motivation_alignment))`; badge `high` above 0.6.
- **Bench risk** — rises as a project's end date approaches with no follow-on allocation;
  badge `high` above 0.7.

### LLM explanation layer (`src/services/claude_service.py`)
`POST /matches` returns a deterministic stub explanation immediately (inside the <500 ms
SLA); a background task then calls the LLM and fills in a 3-section explanation
(skill / behavioral / growth) + risks + growth potential. **Privacy invariant:**
`build_prompt_context` passes only the aggregate scores and structural facts — the raw
`work_style_vector` / `motivation_vector` are **never** sent to the LLM. The prompt is a
versioned artifact: [`artifacts/prompts/match_explanation_v1.json`](artifacts/prompts/match_explanation_v1.json).

---

## 2. Quick start (local)

### Prerequisites
- Python **3.11+**, Node **16+**, PostgreSQL **13+** with the `pgvector` extension.
- (Optional) a free Gemini API key — without it the app still runs; explanations stay as
  deterministic stubs.

### Backend

```bash
cd projects/neural-sync

# 1. deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. config
cp .env.example .env
#    then edit .env — at minimum set GEMINI_API_KEY (optional) and, for any shared
#    environment, a strong NEURAL_SYNC_JWT_SECRET:
#    python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. database (pgvector)
createdb neuralsync
psql neuralsync -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head            # creates schema (001) + seed data (002)

# 4. run
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs: <http://localhost:8000/docs> · health:
<http://localhost:8000/api/v1/health?verbose=true>

### Frontend

```bash
cd projects/neural-sync/frontend
npm install
npm run dev                     # http://localhost:5173 ; /api proxied to :8000
```

Build: `npm run build` → `frontend/dist/`. Type-check: `npm run typecheck`.

### Docker (backend)

```bash
docker build -t neural-sync .
docker run -p 8000:8080 \
  -e DATABASE_URL='postgresql+asyncpg://neuralsync:neuralsync@host.docker.internal:5432/neuralsync' \
  -e NEURAL_SYNC_JWT_SECRET='<strong-random-value>' \
  -e GEMINI_API_KEY='<key>' \
  -e DEBUG=false \
  neural-sync
```

The image listens on `$PORT` (default 8080). In `DEBUG=false` the startup guard rejects
the default dev JWT secret — set a real one (see [`Dockerfile`](Dockerfile)).

---

## 3. Configuration (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | *(empty)* | Gemini key for LLM explanations; empty → deterministic stubs only |
| `DATABASE_URL` | `postgresql+asyncpg://neuralsync:neuralsync@localhost:5432/neuralsync` | Async DSN (runtime) |
| `DATABASE_URL_SYNC` | `postgresql://…/neuralsync` | Sync DSN (Alembic only) |
| `NEURAL_SYNC_JWT_SECRET` | `dev-secret-change-in-production` | JWT signing key — **must change** outside dev |
| `DEBUG` | `false` | Debug mode; relaxes the JWT-secret startup guard |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:3000` | CORS allow-list |
| `COOKIE_SECURE` / `COOKIE_SAMESITE` | `false` / `strict` | Refresh-cookie flags (set `Secure` under HTTPS) |
| `OPENAI_API_KEY` | *(empty)* | Optional — switches embeddings to OpenAI (else local sentence-transformers) |

See [`.env.example`](.env.example) for the annotated template (incl. optional
`CLAUDE_MAX_CONCURRENT` / `CLAUDE_QUEUE_MAX_DEPTH` concurrency knobs).

---

## 4. API reference

All routes are under the **`/api/v1`** prefix. Auth: Bearer JWT access token unless noted;
"Manager" means the token's role must be `manager`.

### Auth — `src/api/auth.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/auth/login` | none | issues access token + HttpOnly refresh cookie |
| POST | `/auth/refresh` | refresh cookie | rotates the refresh token, issues a new access token |

### Health — `src/main.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/health` | none | `?verbose=true` includes LLM queue depth |

### Matches — `src/api/matches.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/matches` | required | compute a match → **201**; returns score + stub explanation immediately |
| GET | `/matches/{match_id}` | required | fetch a stored match record |
| GET | `/matches/{match_id}/explanation` | required | poll for the async LLM explanation (`explanation_source`) |
| POST | `/matches/rescore` | Manager | queue async batch re-scoring → **202** |

### Feedback / admin / risk — `src/api/feedback.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/matches/feedback` | required | record accept/reject → **201** (one per match) |
| GET | `/admin/erasure-audit/{developer_id}` | Manager | GDPR erasure compliance log |
| POST | `/admin/reembed` | Manager | trigger full re-embedding → **202** |
| POST | `/risk/refresh` | Manager | batch refresh all risk scores → **202** |
| GET | `/teams/{team_id}/risk-summary` | Manager | per-developer burnout/bench risk badges (AC8); BLK-001 resolved |

### Developers — `src/api/developers.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/developers` | required | create profile (background embedding job) |
| GET | `/developers/{id}` | required | fetch profile (raw behavioral vectors never returned) |
| PUT | `/developers/{id}` | required | replace profile (marks `embedding_status=pending`) |
| DELETE | `/developers/{id}` | required | **GDPR cascade erasure** across 6 entity classes + audit row |
| GET | `/developers/{id}/risk` | required | burnout + bench risk scores & badges |
| GET | `/developers/{id}/matches` | required | top-K recommendations (optional `min_score`) |

### Projects — `src/api/projects.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/projects` | Manager | create project (background embedding) |
| GET | `/projects/{id}` | required | fetch project |
| PUT | `/projects/{id}` | Manager | replace project |
| DELETE | `/projects/{id}` | Manager | delete project |

### Config (weights) — `src/api/config.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/config/weights` | required | active weight configuration |
| PUT | `/config/weights` | Manager | update weights (sum must equal 1.0 ± 0.001) |

### Analytics — `src/api/analytics.py`
| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/analytics/rejection-rate` | required | developer rejection ratio (null below `REJECTION_RATE_MIN_SAMPLES`) |
| GET | `/analytics/team-rejection-rate` | Manager | team-level rejection aggregation |
| GET | `/analytics/match-stats` | required | total / accepted / rejected counts |

---

## 5. Frontend (3 roles)

`frontend/src/` (Vite + React + TS). API client base URL is
`VITE_API_BASE_URL` or `http://localhost:8000/api/v1`
([`frontend/src/api/client.ts`](frontend/src/api/client.ts) — in-memory access token,
HttpOnly refresh cookie, one silent refresh on 401).

| Page / role | File | Shows |
|-------------|------|-------|
| **Developer** | `pages/DeveloperDashboard.tsx` | recommended projects, match explanations, risks, growth paths; Accept/Reject |
| **Manager** | `pages/ManagerDashboard.tsx` | team health, per-developer **risk badges** (no raw behavioral vectors) — backed by `/teams/{id}/risk-summary` (BLK-001 resolved) |
| **Admin / weights** | `pages/WeightConfigPage.tsx` | weight tuning via `/config/weights` — live as the **Weight Config** tab in the Manager view (`App.tsx`) |

Components: `ProjectCard.tsx` (polls explanation until ready), `RiskBadge.tsx`.

---

## 6. Data model

`src/db/models.py` (+ `artifacts/data-model.json`). Highlights:

- **`DeveloperProfile`** — skills, experience, `work_style_vector` & `motivation_vector`
  (8-dim, **never exposed in API responses**), timezone, availability, `career_goals`,
  project history, `embedding_status`.
- **`ProjectProfile`** — required skills, team structure, workload intensity, innovation
  level, timezone overlap, duration, growth opportunities.
- **`DeveloperEmbedding` / `ProjectEmbedding`** — pgvector columns with an **HNSW** index
  for ANN retrieval at scale.
- **`MatchRecord`** — stores `match_score` + all five component scores + explanation +
  `weights_snapshot` (so each match is auditable / explainable).
- **`WeightConfig`** — singleton (id=1), versioned, sum-validated.
- **GDPR:** `DELETE /developers/{id}` cascades across `DeveloperEmbedding`,
  `MatchRecord`, `FeedbackRecord`, `AllocationRecord`, `ExplanationCache`, then the linked
  `UserAccount`; an `ErasureAuditLog` row (no FK, survives the cascade) records the
  erasure.

---

## 7. Tests

```bash
cd projects/neural-sync
pytest                 # config in pytest.ini (asyncio auto mode)
pytest --cov=src       # with coverage
```

The recorded QA run: **77/77 passing, 0 failing**, all 13 acceptance criteria covered —
including the good-match (score ≥ 0.75) and skill-only-trap (score ≤ 0.45) signature
cases. Details in [`artifacts/test_plan.json`](artifacts/test_plan.json).

---

## 8. How this app was produced

Every file here was generated by the agent pipeline described in the repo root
([`../../SPEC.md`](../../SPEC.md), [`../../ARCHITECTURE-DIAGRAM.md`](../../ARCHITECTURE-DIAGRAM.md)).
The full pipeline artifacts (requirements → workplan → architecture → ADRs → code_spec →
review → test_plan → release) and the event log live under
[`artifacts/`](artifacts/); the run's outcome and lessons are in
[`../../EVALUATION.md`](../../EVALUATION.md).

Because NEURAL SYNC has a browser UI, the pipeline's post-deploy **`e2e_validation`**
stage applies here: with `devops-agent` serving the built React UI alongside the API, the
`e2e-agent` drives that live URL in a real browser via the **Playwright MCP** server and
emits `e2e_report.json` (`../../SPEC.md` §3.8). The recorded run now reaches `complete`
through deployment (`release_report.json` verdict `success`); exercising this browser
stage end-to-end against the live UI is the immediate next step (see
`../../EVALUATION.md` → Known limitations).
