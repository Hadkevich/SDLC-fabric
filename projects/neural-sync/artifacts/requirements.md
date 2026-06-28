# NEURAL SYNC — Requirements (Phase 1 MVP + Data Pipeline & Source Connectors)

**ID:** neural-sync-req-v2  
**Spec version:** v1  
**Date:** 2026-06-28  
**Amends:** neural-sync-req-v1 (Phase 1 MVP — all 13 original acceptance criteria preserved intact)

---

## 1. Problem Statement

Traditional developer-to-project allocation relies on skill-set matching alone, ignoring behavioral traits, motivation alignment, work-style compatibility, and career trajectory. This produces suboptimal team composition, elevated attrition, burnout, and low developer satisfaction.

**NEURAL SYNC** must replace static skill-matching with a multi-dimensional, AI-driven compatibility engine that:

1. Scores developer↔project fit across **five dimensions** — skills, work style, motivation, timezone, and growth potential.
2. Generates a **human-readable explanation** for every match decision via the configurable LLM explanation layer.
3. **Predicts bench and burnout risk** before they materialize.
4. **Continuously re-optimizes** allocation so that the system never becomes static.

> _A match the system cannot explain is a broken match._

**Phase-1 MVP is complete and deployed.** This increment adds the **Data Pipeline and Source Connectors** feature (Task04 §5 Data Pipelines; §3.1 Profile Enrichment). A uniform Connector abstraction ingests developer signals from external sources (HR systems, GitLab, Jira, Slack, CV documents), maps them into the three text channels consumed by the existing `enrich_profile` transform (`cv_text`, `git_log_text`, `slack_text`), and flows enriched profiles through the **existing** create-plus-embed pipeline. Profiles derived from external sources go through the same five-dimension matching engine and LLM explanation layer as manually-created profiles — there is **no parallel pipeline**.

> **GOVERNANCE NOTE:** Phase-1 requirements explicitly deferred external-source connectors to Phase 2 and marked CV document parsing as a non-goal. This increment deliberately pulls both forward with explicit product-agent ratification recorded in this document (section 2.1 and this problem statement). No downstream artifact may contradict this scope change without a further product-agent revision.

---

## 2. Scope

### 2.1 In Scope

| Area | Detail |
|------|--------|
| **Developer profile ingestion** | Structured `DeveloperProfile` schema: id, skills, experience_years, preferred_stack, work_style vector, motivation_vector, timezone, availability_hours, career_goals, project history |
| **Project profile ingestion** | Structured `ProjectProfile` schema: id, required_skills, team_structure, workload_intensity, innovation_level, timezone_overlap_required, duration_weeks, growth_opportunities |
| **Matching engine** | `MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score + w4·timezone_score + w5·growth_score` with configurable weights |
| **AI explanation layer** (Phase-1 Gemini) | Per-match natural-language output: match rationale, identified risks, growth potential |
| **Bench-risk prediction** | Risk score per developer based on project end dates and allocation schedule |
| **Burnout-risk detection** | Risk score from over-allocation signals, workload intensity, and motivation alignment |
| **REST API** | FastAPI backend with OpenAPI 3.1 contracts for all resources |
| **Data storage** | PostgreSQL for structured data; vector-compatible embedding store |
| **Developer dashboard** | Ranked recommended projects with match scores and LLM-generated explanations |
| **Manager dashboard** | Team composition health, risk alerts, allocation suggestions |
| **Match feedback loop** | Developer accepts/rejects recommendations; rejection rate queryable per developer |
| **GDPR erasure** | Full profile + embedding + history deletion on request |
| **Versioned prompts** | LLM prompt templates stored as auditable, versioned artifacts (not hardcoded) |
| **Test scenarios** | Good-match and bad-match test cases with documented expected score ranges |
| **GOVERNANCE RATIFICATION** _(NEW)_ | External-source connectors and CV document parsing pulled forward from Phase 2 with explicit product-agent sign-off; recorded here as the authoritative scope change |
| **Connector abstraction** _(NEW)_ | Base class yielding `SourceDocument` records (external_id, display_name, email, cv_text, git_log_text, slack_text, optional timezone/availability_hours/experience_years/source); graceful degradation without credentials — no connector may raise HTTP 5xx |
| **GitLab connector** _(NEW)_ | Live, read-only, network kind; pulls commit messages + MR titles into `git_log_text` via `httpx`; optional token + configurable `base_url` (default `https://gitlab.com`); respects `GITLAB_MAX_PAGES` and per-request timeout; no-token → degraded result |
| **HR connector** _(NEW)_ | File-based, bulk, file kind; parses CSV or JSON into one `SourceDocument` per employee row; case-insensitive column mapping (title/role/bio → cv_text, weekly_hours → availability_hours, years_experience → experience_years) |
| **Slack connector** _(NEW)_ | File-based, file kind; parses Slack export JSON; aggregates each user's messages into `slack_text`; one `SourceDocument` per user |
| **CV connector** _(NEW)_ | File-based, file kind; parses a single .txt or .md file into one `SourceDocument` (cv_text); PDF support guarded by a conditional import — unavailability reported cleanly, not as an exception |
| **Jira connector** _(NEW)_ | Credential-gated, network kind; reads assigned issues, labels, comments into `git_log_text` via thin httpx client; missing credentials → degraded `IngestionSummary` documenting requirements, never HTTP 5xx |
| **Ingestion ETL orchestrator + endpoints** _(NEW)_ | Under `/api/v1/ingestion/` prefix (manager role only): `GET /ingestion/connectors`, `POST /ingestion/file`, `POST /ingestion/gitlab`, `POST /ingestion/jira`; modes: **preview** (returns drafts, creates nothing) and **commit** (creates profiles + enqueues embeddings) |
| **IngestionSummary** _(NEW)_ | `{extracted, enriched, skipped, created, provenance: {llm, heuristic}, errors}` |
| **Shared create-plus-embed helper** _(NEW)_ | Refactored from `create_developer` + `_enqueue_embeddings` in `src/api/developers.py`; used by both `POST /api/v1/developers` and commit-mode ingestion — no duplication |
| **In-memory file processing + size guard** _(NEW)_ | Uploads parsed in memory only; `MAX_UPLOAD_BYTES` guard (default 10 MB) returns HTTP 413; nothing written to disk |
| **Batch record cap** _(NEW)_ | `INGESTION_MAX_RECORDS` (default 500) prevents runaway ingestion jobs |
| **Frontend Ingestion page** _(NEW)_ | Manager-role tab (no react-router); connector picker; file dropzone; GitLab + Jira forms; Preview table; Approve & Create action; multipart helper in `client.ts`; inline-style + `data-testid` conventions |
| **New environment settings** _(NEW)_ | `GITLAB_BASE_URL`, `GITLAB_TOKEN`, `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`, `MAX_UPLOAD_BYTES`, `INGESTION_MAX_RECORDS`, `GITLAB_MAX_PAGES` — all in `settings.py` and `.env.example` |
| **Updated API contracts artifact** _(NEW)_ | `artifacts/api-contracts.json` extended with ingestion path items + `ConnectorInfo` + `IngestionSummary` schemas |
| **Updated architecture artifact** _(NEW)_ | `artifacts/architecture.json` extended with `data-ingestion-service` component + connector subcomponents + failure modes |

### 2.2 Out of Scope

- Admin weight-tuning UI panel _(weights configurable via API only)_
- Real-time streaming reallocation _(batch re-optimization sufficient)_
- Mobile native applications
- SSO / external identity-provider integration
- Multi-tenant / multi-organization support
- Production-scale managed vector DB deployment _(architecture phase decides)_

> **Note:** "Live integration with external data sources" and "CV document parsing pipeline" have been **removed** from out-of-scope and are now explicitly in scope per the governance ratification above.

---

## 3. Non-Goals

- **Not a decision-maker** — the system advises; humans decide on allocation.
- **Not a throughput optimizer** — developer wellbeing is never sacrificed for short-term project velocity.
- **Not an HR system** — payroll, performance reviews, and HR workflow automation are out of scope.
- **Not a raw-data exposure layer** — behavioral vectors and motivation scalars are never surfaced directly to unauthorized UI consumers.
- **Not fully autonomous** — allocation requires a developer feedback loop; the system does not act without human confirmation.

---

## 4. Constraints

| # | Constraint |
|---|-----------|
| 1 | **Explainability is mandatory.** Every match response must include a non-empty LLM-generated explanation. Skill-only scoring without the behavioral and explainability layer is a **hard failure condition**. |
| 2 | **Re-optimization capability is mandatory.** Static allocation without a re-score/re-rank endpoint is a **hard failure condition**. |
| 3 | **Rejection feedback is mandatory from day 1.** The match-feedback endpoint is not optional; the rejection rate must be measurable before launch. |
| 4 | **GDPR compliance.** All PII must be erasable on request. Embeddings must be purged alongside structured records. No data retained beyond consent scope. |
| 5 | **Latency SLA.** Match API response ≤ 500ms at p95. Async explanation delivery is acceptable if the score is returned synchronously. |
| 6 | **Scale target.** Architecture must support a developer pool of 10,000+ without structural rework. |
| 7 | **Agentic build + configurable LLM.** Claude Code agents are the mandated build layer. Runtime LLM stays Google Gemini (Phase-1); Claude API is a supported alternate. Provider/model is swappable via the versioned prompt artifact. |
| 8 | **Tech stack.** Python/FastAPI · React · configurable LLM provider (Phase-1: Google Gemini) · PostgreSQL · vector-compatible storage. |
| 9 | **Configurable weights.** Matching weights w1–w5 must be updatable without a code deployment. |
| 10 | **LLM stays Google Gemini for the data pipeline.** _(NEW)_ The Anthropic SDK must not be introduced. Provider/model remains swappable via the versioned prompt artifact with no source change required. |
| 11 | **Ingestion endpoints are manager-only.** _(NEW)_ All `/api/v1/ingestion/*` endpoints must use the existing `require_manager` dependency from `src/core/auth.py`. Developer-role JWTs → HTTP 403. Unauthenticated → HTTP 401. |
| 12 | **Connectors must never raise HTTP 5xx.** _(NEW)_ A missing, expired, or invalid credential must produce a degraded `IngestionSummary` (partial or empty result with an entry in `errors`) with HTTP 200. |
| 13 | **Uploads are in-memory only.** _(NEW)_ Writing uploads to disk is prohibited. Files exceeding `MAX_UPLOAD_BYTES` must be rejected with HTTP 413 before any parsing begins. |
| 14 | **Batch-safe skipping.** _(NEW)_ Records that yield no extractable skills are counted in `IngestionSummary.skipped` without aborting the rest of the batch; a single bad record must not cause HTTP 5xx. |
| 15 | **All new tests must be fully mocked and offline.** _(NEW)_ GitLab/Jira HTTP via mock httpx transport; file connectors from in-memory bytes. All 120 existing tests must remain green. |

---

## 5. Acceptance Criteria

All criteria are observable and testable. Criteria 1–13 are unchanged from Phase-1 MVP (neural-sync-req-v1). Criteria 14–30 cover the new Data Pipeline & Source Connectors feature.

### 5.1 Phase-1 MVP Criteria (unchanged)

1. **Match endpoint response completeness & latency**  
   `POST /api/v1/matches` with valid Developer + Project JSON returns `match_score` (float 0–1), `explanation` (≥ 50 chars), `risks` (list), `growth_potential` (list) within **500ms at p95** under nominal load.

2. **Behavioral dimension is active**  
   Two profile pairs that are identical in skills but differ in `work_style` vectors produce different match scores, with the misaligned pair scoring strictly lower.

3. **Explanation structure**  
   Every LLM-generated explanation contains at least: one sentence on skill alignment, one sentence on behavioral/work-style alignment, one sentence on growth/career potential.

4. **Burnout risk threshold**  
   `GET /api/v1/developers/{id}/risk` returns `burnout_risk_score > 0.6` for a developer with ≥ 48 consecutive weeks at `workload_intensity ≥ 0.8`.

5. **Bench risk threshold**  
   `GET /api/v1/developers/{id}/risk` returns `bench_risk_score > 0.7` for a developer whose current project ends within 28 days with no follow-on allocation.

6. **Weight configurability**  
   `PUT /api/v1/config/weights` causes subsequent match calls on identical profile pairs to produce scores reflecting the new weights, verified by a deterministic unit test.

7. **Developer dashboard renders recommendations**  
   The Developer view renders ≥ 1 project recommendation card (match score + explanation) for any developer with a fully populated profile.

8. **Manager dashboard does not leak behavioral data**  
   The Manager view displays risk badges and team health without exposing raw behavioral vectors or motivation scalars to the UI layer.

9. **GDPR erasure**  
   `DELETE /api/v1/developers/{id}` purges structured profile, all embeddings, and all match history → HTTP 204. Subsequent `GET /api/v1/developers/{id}` → HTTP 404.

10. **Feedback collection and rejection-rate query**  
    `POST /api/v1/matches/feedback` stores accept/reject records. `GET /api/v1/analytics/rejection-rate?developer_id={id}` returns the ratio for developers with ≥ 1 feedback sample.

11. **Good-match and bad-match test scenarios**  
    Test suite contains a labeled _good-match_ case (match_score ≥ 0.75) and a labeled _bad-match_ case (match_score ≤ 0.45) with expected ranges documented in `test_plan.json`.

12. **Versioned prompt artifacts**  
    All LLM prompt templates live in a versioned artifact file referenced by `code_spec.json`; no prompt strings appear inline in application source code.

13. **Full pipeline integration test**  
    An automated integration test covers the complete match pipeline (ingest → embed → score → LLM explain → response) and passes in CI against a local or mocked environment.

### 5.2 Data Pipeline & Source Connectors Criteria (NEW — AC14–AC30)

14. **Connector list endpoint**  
    `GET /api/v1/ingestion/connectors` returns HTTP 200 with a JSON array of ≥ 5 connector descriptors (gitlab, hr, slack, cv, jira); each descriptor includes `kind` ("file" or "network") and `availability` ("live" or "credential-gated"); returns HTTP 401/403 for non-manager callers.

15. **CV connector — preview mode creates nothing**  
    `POST /api/v1/ingestion/file` with `source=cv`, a valid UTF-8 .txt body, and `mode=preview` returns HTTP 200 with an `IngestionSummary` containing ≥ 1 draft; `created` is 0; no `DeveloperProfile` row is persisted.

16. **CV connector — commit mode creates a profile**  
    `POST /api/v1/ingestion/file` with `source=cv`, a valid UTF-8 .txt body, and `mode=commit` returns HTTP 200 with `IngestionSummary.created ≥ 1`; a `DeveloperProfile` is persisted via the shared create-plus-embed helper; embeddings are enqueued asynchronously via `BackgroundTasks`.

17. **HR connector — column mapping**  
    `POST /api/v1/ingestion/file` with `source=hr` and a valid CSV payload returns one draft per employee row; column mapping is case-insensitive and handles alternate names (title/role/bio → cv_text; weekly_hours → availability_hours; years_experience → experience_years).

18. **Slack connector — one document per user**  
    `POST /api/v1/ingestion/file` with `source=slack` and a valid Slack export JSON payload returns one draft per user with that user's channel messages concatenated as `slack_text`.

19. **GitLab connector — live extraction and credential degradation**  
    `POST /api/v1/ingestion/gitlab` with a valid username and a mock httpx transport returning commit and MR data returns HTTP 200 with an `IngestionSummary` whose draft's `git_log_text` contains the mocked data; without a token (or with an invalid token), returns HTTP 200 with a degraded result, **never HTTP 5xx**.

20. **Jira connector — missing credentials return graceful result**  
    `POST /api/v1/ingestion/jira` called with missing or invalid credentials returns HTTP 200 with an `IngestionSummary` where `errors` contains a message documenting the missing credential; **never HTTP 5xx** regardless of credential state.

21. **Role enforcement on all ingestion endpoints**  
    All `/api/v1/ingestion/*` endpoints return HTTP 403 for developer-role JWTs and HTTP 401 for unauthenticated requests; manager-role JWTs are accepted.

22. **Oversized upload returns HTTP 413**  
    `POST /api/v1/ingestion/file` with a body exceeding `MAX_UPLOAD_BYTES` (default 10 MB) returns HTTP 413; no data is written to disk and no `DeveloperProfile` is created.

23. **Single code path for profile creation**  
    In `mode=commit`, the orchestrator calls `enrich_profile(cv_text, git_log_text, slack_text)` for each `SourceDocument` (off the event loop via `asyncio.to_thread`) and creates `DeveloperProfile` records through the same shared create-plus-embed helper used by `POST /api/v1/developers`; no duplicate code path exists.

24. **Zero-skill records skipped without failing the batch**  
    When `enrich_profile` returns an empty `skills` list for a `SourceDocument`, that record is counted in `IngestionSummary.skipped` and no profile is created; the remaining records in the batch are processed normally and the response is HTTP 200.

25. **Provenance counts in IngestionSummary**  
    The `IngestionSummary` includes `provenance: {llm: int, heuristic: int}` derived from `EnrichmentResult.provenance`; `provenance.llm + provenance.heuristic == IngestionSummary.enriched`.

26. **Frontend Ingestion page (manager only)**  
    The Ingestion tab (manager role only, wired without react-router) renders: connector picker from `GET /api/v1/ingestion/connectors` (credential-gated entries shown disabled with credential tooltip); file dropzone for file-kind connectors (CSV, JSON, TXT, MD); GitLab form (username, project, optional token); Jira form (base_url, email, token, project_key, usernames); Preview button → drafts review table; Approve & Create button → commit mode; all interactive elements carry `data-testid` attributes following the existing codebase convention.

27. **End-to-end ingestion pipeline integration test**  
    A test in `tests/integration/` covers: HR CSV connector extraction → `enrich_profile` called per document (mocked deterministic result) → commit mode → `DeveloperProfile` rows in `MockAsyncSession.added` → embeddings enqueued (BackgroundTasks mock); all HTTP uses a mock httpx transport; no live DB or external API; passes in CI alongside all 120 existing tests (all remain green).

28. **New settings documented in settings.py and .env.example**  
    `src/core/settings.py` and `.env.example` document: `GITLAB_BASE_URL` (default `https://gitlab.com`), `GITLAB_TOKEN` (empty), `JIRA_BASE_URL` (empty), `JIRA_EMAIL` (empty), `JIRA_TOKEN` (empty), `MAX_UPLOAD_BYTES` (10485760), `INGESTION_MAX_RECORDS` (500), `GITLAB_MAX_PAGES` (10).

29. **api-contracts.json updated**  
    `artifacts/api-contracts.json` includes OpenAPI 3.x path items for `GET /api/v1/ingestion/connectors`, `POST /api/v1/ingestion/file`, `POST /api/v1/ingestion/gitlab`, `POST /api/v1/ingestion/jira`, plus `ConnectorInfo` and `IngestionSummary` component schemas.

30. **architecture.json updated**  
    `artifacts/architecture.json` includes a `data-ingestion-service` component with five connector subcomponents (gitlab-connector, hr-connector, slack-connector, cv-connector, jira-connector) and documented failure modes (rate-limit, missing-credentials, oversized-upload) each with a `degradation` field describing the graceful fallback.

---

## 6. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| 1 | **LLM API latency** exceeds 500ms SLA | Medium | High | Return algorithmic score synchronously; generate explanation asynchronously with result caching for repeated pairs |
| 2 | **Weight misconfiguration** produces counterintuitive matches, eroding developer trust | Medium | High | Ship default weights validated against the defined good-match / bad-match test scenarios |
| 3 | **GDPR scope creep** as external sources (Slack, Git) introduce PII before erasure is hardened | Medium | High | Erasure pipeline must be production-ready before any external integration begins — it is (Phase 1 complete) |
| 4 | **Sparse rejection-rate signal** makes the 50% threshold unreliable at MVP scale | High | Medium | Enforce a minimum floor of ≥ 10 feedback records before surfacing rejection-rate alerts |
| 5 | **Vector search cold-start** degrades similarity quality with tiny datasets | High | Medium | Ship a synthetic seed dataset of ≥ 50 developer and ≥ 20 project profiles |
| 6 | **LLM prompt drift** on model version upgrades silently alters explanation structure | Low | Medium | Snapshot expected explanation structure in regression tests; check for required sentence types |
| 7 | **Self-reported behavioral vectors** are inaccurate, reducing match quality | Medium | Medium | Flag whether behavioral data is self-reported vs. inferred; document limitation in system |
| 8 | **GitLab/Jira rate-limiting** mid-batch produces partial results _(NEW)_ | Medium | Medium | Enforce `GITLAB_MAX_PAGES` (default 10) + per-request timeout; log partial results in `IngestionSummary.errors`; never abort batch or return 5xx |
| 9 | **Large file memory pressure** from concurrent multi-MB uploads causes OOM _(NEW)_ | Medium | High | Enforce `MAX_UPLOAD_BYTES` (default 10 MB); reject oversized uploads with HTTP 413 before parsing |
| 10 | **Duplicate profile creation** from successive commit runs on the same source _(NEW)_ | High | High | Resolve commit-mode idempotency in open questions before activating commit mode in production; interim: surface duplicates in `IngestionSummary.errors` |
| 11 | **Scope increment complexity** risks breaking 120 existing passing tests _(NEW)_ | Medium | High | All new tests are fully mocked and offline; shared create-plus-embed helper prevents code drift; reviewer-agent must verify existing suite remains green before approving |

---

## 7. Open Questions

> Questions marked ⛔ block downstream implementation decisions.

1. ⛔ **Vector storage technology** — Should Phase 1 use an embedded solution (pgvector, Chroma) or a managed service (Pinecone, Weaviate)? _Blocks DB schema and infrastructure design._

2. ⛔ **Latency SLA scope** — Is the 500ms p95 target end-to-end (including the LLM call) or algorithmic-only (async explanation acceptable)? _Determines whether synchronous LLM calls are permissible._

3. **Authentication model** — Does Phase 1 require user login (JWT/session) for developer and manager views, or is API-key access sufficient for MVP?

4. **Seed data availability** — Are existing developer/project profiles available, or must the team generate a fully synthetic dataset?

5. **LLM provider/model selection** — Phase-1 uses Google Gemini (gemini-1.5-flash, free tier); Claude API (claude-3-5-haiku) is a supported alternate. The model identifier lives in the versioned prompt artifact, so provider/model swaps need no source change.

6. **Cyberpunk dashboard theme** — Is the visual theme required for Phase 1, or can the MVP deliver a functional unstyled UI with theming deferred?

7. ⛔ **Commit-mode idempotency** _(NEW)_ — When a `SourceDocument`'s email or `external_id` matches an existing `DeveloperProfile`, should commit mode: (a) update the existing profile in-place, (b) skip and note in `IngestionSummary.errors`, or (c) create a second profile? Decision blocks the ingestion DB write path and has GDPR implications. _Blocks ingestion implementation._

8. **PDF CV support activation** _(NEW)_ — Should the optional PDF parsing capability (guarded by a conditional PyMuPDF/pdfminer import) be promoted to a first-class supported feature in a follow-on increment via a `PDF_CV_ENABLED` environment flag, or remain permanently optional-import-guarded with a clear "unavailable" message?

---

## Appendix A: Key Schema References

### DeveloperProfile (abridged)
```json
{
  "id": "uuid",
  "skills": ["python", "react"],
  "experience_years": 5,
  "preferred_stack": ["ai", "backend"],
  "work_style": { "async_vs_sync": 0.8, "team_vs_individual": 0.6, "structure_vs_flexibility": 0.7 },
  "motivation_vector": { "learning": 0.9, "stability": 0.4, "innovation": 0.8 },
  "timezone": "UTC+1",
  "availability_hours": 40,
  "career_goals": ["move to ML", "lead role"],
  "history": ["<project_id>"]
}
```

### ProjectProfile (abridged)
```json
{
  "id": "uuid",
  "required_skills": ["python", "ml"],
  "team_structure": { "size": 6, "communication_style": "async-heavy" },
  "workload_intensity": 0.7,
  "innovation_level": 0.9,
  "timezone_overlap_required": 4,
  "duration_weeks": 24,
  "growth_opportunities": ["ml", "distributed systems"]
}
```

### Matching Formula
```
MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score
            + w4·timezone_score + w5·growth_score
            where w1 + w2 + w3 + w4 + w5 = 1.0
```

### SourceDocument (NEW)
```json
{
  "external_id": "string",
  "display_name": "string",
  "email": "string",
  "cv_text": "string",
  "git_log_text": "string",
  "slack_text": "string",
  "timezone": "UTC+1",
  "availability_hours": 40,
  "experience_years": 5,
  "source": "gitlab | hr | slack | cv | jira"
}
```

### IngestionSummary (NEW)
```json
{
  "extracted": 10,
  "enriched": 9,
  "skipped": 1,
  "created": 9,
  "provenance": { "llm": 3, "heuristic": 6 },
  "errors": ["record 3: no skills extracted — skipped"]
}
```

---

## Appendix B: Connector Reference

| Connector | Kind | Availability | Input | Output channel |
|-----------|------|-------------|-------|----------------|
| `gitlab` | network | live | username, optional project + token | `git_log_text` (commit messages + MR titles) |
| `hr` | file | live | CSV or JSON file | `cv_text` (title/bio/skills), `availability_hours`, `experience_years` |
| `slack` | file | live | Slack export JSON | `slack_text` (aggregated user messages) |
| `cv` | file | live | .txt or .md (PDF optional) | `cv_text` |
| `jira` | network | credential-gated | base_url, email, token, project_key | `git_log_text` (issues + labels + comments) |

All connectors degrade gracefully: missing/invalid credentials → degraded `IngestionSummary` with HTTP 200, never HTTP 5xx.

---

_This document is the authoritative product specification for NEURAL SYNC Phase 1 MVP + Data Pipeline increment. No downstream artifacts (workplan, architecture, code) may contradict or expand scope without a Product Agent revision._
