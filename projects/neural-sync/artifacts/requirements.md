# NEURAL SYNC — Requirements (Phase 1 MVP)

**ID:** neural-sync-req-v1  
**Spec version:** v1  
**Date:** 2026-06-26  

---

## 1. Problem Statement

Traditional developer-to-project allocation relies on skill-set matching alone, ignoring behavioral traits, motivation alignment, work-style compatibility, and career trajectory. This produces suboptimal team composition, elevated attrition, burnout, and low developer satisfaction.

**NEURAL SYNC** must replace static skill-matching with a multi-dimensional, AI-driven compatibility engine that:

1. Scores developer↔project fit across **five dimensions** — skills, work style, motivation, timezone, and growth potential.
2. Generates a **human-readable explanation** for every match decision via the configurable LLM explanation layer.
3. **Predicts bench and burnout risk** before they materialize.
4. **Continuously re-optimizes** allocation so that the system never becomes static.

> _A match the system cannot explain is a broken match._

---

## 2. Scope

### 2.1 In Scope (Phase 1 MVP)

| Area | Detail |
|------|--------|
| **Developer profile ingestion** | Structured `DeveloperProfile` schema: id, skills, experience_years, preferred_stack, work_style vector, motivation_vector, timezone, availability_hours, career_goals, project history |
| **Project profile ingestion** | Structured `ProjectProfile` schema: id, required_skills, team_structure, workload_intensity, innovation_level, timezone_overlap_required, duration_weeks, growth_opportunities |
| **Matching engine** | `MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score + w4·timezone_score + w5·growth_score` with configurable weights |
| **AI explanation layer** (configurable LLM provider, Phase-1 Gemini) | Per-match natural-language output: match rationale, identified risks, growth potential |
| **Bench-risk prediction** | Risk score per developer based on project end dates and allocation schedule |
| **Burnout-risk detection** | Risk score from over-allocation signals, workload intensity, and motivation alignment |
| **REST API** | FastAPI backend with OpenAPI 3.1 contracts for all resources |
| **Data storage** | PostgreSQL for structured data; vector-compatible embedding store (technology resolved in architecture phase) |
| **Developer dashboard** | Ranked recommended projects with match scores and LLM-generated explanations |
| **Manager dashboard** | Team composition health, risk alerts, allocation suggestions |
| **Match feedback loop** | Developer accepts/rejects recommendations; rejection rate queryable per developer |
| **GDPR erasure** | Full profile + embedding + history deletion on request |
| **Versioned prompts** | LLM prompt templates stored as auditable, versioned artifacts (not hardcoded) |
| **Test scenarios** | Good-match and bad-match test cases with documented expected score ranges |

### 2.2 Out of Scope (Phase 1)

- Live integration with external sources: Git repos, Jira, Slack, HR systems _(Phase 2)_
- Admin weight-tuning UI panel _(weights configurable via API only)_
- Real-time streaming reallocation _(batch re-optimization sufficient)_
- Mobile native applications
- SSO / external identity-provider integration
- Multi-tenant / multi-organization support
- CV or document parsing pipeline _(profiles ingested as structured JSON)_
- Production-scale managed vector DB deployment _(architecture phase decides)_

---

## 3. Non-Goals

- **Not a decision-maker** — the system advises; humans decide on allocation.
- **Not a throughput optimizer** — developer wellbeing is never sacrificed for short-term project velocity.
- **Not an HR system** — payroll, performance reviews, and HR workflow automation are out of scope.
- **Not a raw-data exposure layer** — behavioral vectors and motivation scalars are never surfaced directly to unauthorized UI consumers.
- **Not fully autonomous** — allocation requires a developer feedback loop; the system does not act without human confirmation.

---

## 4. Constraints

1. **Explainability is mandatory.** Every match response must include a non-empty LLM-generated explanation. Skill-only scoring without the behavioral and explainability layer is a **hard failure condition**.

2. **Re-optimization capability is mandatory.** Static allocation without a re-score/re-rank endpoint is a **hard failure condition**. The system must expose at least one endpoint that re-optimizes allocation on demand or on schedule.

3. **Rejection feedback is mandatory from day 1.** The match-feedback endpoint is not optional; the rejection rate must be measurable before launch.

4. **GDPR compliance.** All PII must be erasable on request. Embeddings must be purged alongside structured records. No data retained beyond consent scope.

5. **Latency SLA.** Match API response ≤ 500ms at p95 (scope of the SLA — whether it includes the LLM call — to be confirmed; see Open Questions). Async explanation delivery is acceptable if the score is returned synchronously within the window.

6. **Scale target.** Architecture must support a developer pool of 10,000+ without structural rework.

7. **Versioned prompts.** All LLM prompt templates are stored as versioned artifacts. Prompt changes must not require modifying application source code.

8. **Configurable weights.** Matching weights w1–w5 must be updatable without a code deployment.

9. **Tech stack.** Python/FastAPI · React · configurable LLM explanation provider (Phase-1: Google Gemini; Claude API supported) · PostgreSQL · vector-compatible storage.

---

## 5. Acceptance Criteria

All criteria are observable and testable.

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

---

## 6. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **LLM API latency** exceeds 500ms SLA | Medium | High | Return algorithmic score synchronously; generate explanation asynchronously with result caching for repeated pairs |
| **Weight misconfiguration** produces counterintuitive matches, eroding developer trust | Medium | High | Ship default weights validated against the defined good-match / bad-match test scenarios |
| **GDPR scope creep** when Phase 2 adds external sources (Slack, Git) before erasure is hardened | Medium | High | Erasure pipeline must be production-ready in Phase 1 before any external integration begins |
| **Sparse rejection-rate signal** makes the 50% threshold statistically unreliable at MVP scale | High | Medium | Enforce a minimum floor of ≥ 10 feedback records before surfacing rejection-rate alerts |
| **Vector search cold-start** degrades similarity quality with tiny datasets | High | Medium | Ship a synthetic seed dataset of ≥ 50 developer and ≥ 20 project profiles |
| **LLM prompt drift** on model version upgrades silently alters explanation structure | Low | Medium | Snapshot expected explanation structure in regression tests; check for required sentence types |
| **Self-reported behavioral vectors** are inaccurate, reducing match quality | Medium | Medium | Flag whether behavioral data is self-reported vs. inferred; document limitation in system |

---

## 7. Open Questions

> Questions marked ⛔ block downstream architecture/design decisions.

1. ⛔ **Vector storage technology** — Should Phase 1 use an embedded solution (pgvector, Chroma) to minimize operational overhead, or a managed service (Pinecone, Weaviate) for future scalability? _Blocks DB schema and infrastructure design._

2. ⛔ **Latency SLA scope** — Is the 500ms p95 target end-to-end (including the LLM call) or algorithmic-only (async explanation acceptable)? _Determines whether synchronous LLM calls are permissible._

3. **Authentication model** — Does Phase 1 require user login (JWT/session) for developer and manager views, or is API-key access sufficient for MVP?

4. **Seed data availability** — Are existing developer/project profiles available, or must the team generate a fully synthetic dataset?

5. **LLM provider/model selection** — Phase-1 uses Google Gemini (gemini-1.5-flash, free tier); Claude API (claude-3-5-haiku) is a supported alternate. The model identifier lives in the versioned prompt artifact, so provider/model swaps need no source change. _Affects latency SLA feasibility and API cost._

6. **Cyberpunk dashboard theme** — Is the visual theme required for Phase 1, or can the MVP deliver a functional unstyled UI with theming deferred to Phase 2?

---

## Appendix: Key Schema References

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

---

_This document is the authoritative product specification for NEURAL SYNC Phase 1. No downstream artifacts (workplan, architecture, code) may contradict or expand scope without a Product Agent revision._
