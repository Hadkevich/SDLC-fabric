# Evaluation Report — Agentic SDLC & NEURAL SYNC demo

> Final deliverable #5 (hackathon brief: *"Evaluation report — what worked / what
> failed"*). Grounded in the recorded run under `projects/neural-sync/artifacts/`
> and the engine in `src/orchestrator/`. Every claim cites a file. Honest by design:
> the demo did **not** finish autonomously, and this report says exactly why.

---

## 1. TL;DR

**The control plane is the win; the demo run is the cautionary tale.**

The orchestrator (`src/orchestrator/engine.py`) is genuinely good: deterministic,
event-sourced, atomically persisted, DAG-scheduled with per-task retry/rework/escalate,
and least-privilege + model-diverse by role. It matches the brief's *"determinism >
creativity, orchestration > model intelligence"* constraint.

The single recorded end-to-end demo (`neural-sync`) produced **all** artifacts and a
**green QA suite (77/77)** but **halted at the deployment gate**: the reviewer returned
`verdict: rejected` on a real contract violation (BLK-001), and the run — executed under
an earlier engine that lacked the review→fix rework loop — escalated to a human three
times instead of looping the fix back. `workflow_state.json` ends at
`current_stage: "failed"`, `halted: true`.

Net: end-to-end **autonomy is not yet demonstrated**, but the failure is structural and
well-understood, and the current engine already contains the mechanism to close it.

---

## 2. What worked

### 2.1 Deterministic, recoverable control plane
- **Atomic state + event sourcing.** `_persist` writes temp + renames; `_event` stamps
  `event_id`/`timestamp` so the audit log can't be fabricated (`engine.py`). State is
  reconstructable by folding `events.log.jsonl` (`SPEC.md §8.1`).
- **Transient-failure recovery actually fired.** The demo log shows the developer agent
  and the QA agent each time out at 1800 s and **recover on retry** —
  `code_generation` succeeded on attempt 1, `testing_validation` on attempt 1
  (`events.log.jsonl`; `workflow_state.json` attempt counters). This is real evidence
  for success-criterion 4.3 (recover from simulated failures), at least for the
  transient class.
- **Self-healing artifact generation.** `code_review` retried twice on a
  missing/malformed `review_report.json` (invalid JSON) and then succeeded — the gate
  caught bad output mechanically rather than passing it downstream.

### 2.2 Schema-gated, structured handoffs
Every required artifact is present and schema-valid in
`projects/neural-sync/artifacts/`: `requirements`, `workplan`, `architecture`,
`api-contracts` (~84 KB), `data-model` (~88 KB), 4 ADRs, `code_spec`, `test_plan`,
`review_report`, `release_report`, `workflow_state`, `events.log.jsonl`. No stage
advanced on free-form prose.

### 2.3 QA was green and traceable
`test_plan.json` → `summary: { total: 77, passed: 77, failed: 0, skipped: 0 }`, with
**all 13 acceptance criteria (AC1–AC13) covered by ≥1 test**, including the two
signature cases from the idea brief: good-match score ≥ 0.75 (TC-001) and the skill-only
**trap** bad-match score ≤ 0.45 (TC-002). The behavioral layer demonstrably changes the
verdict — the system is *not* skill-only.

### 2.4 The review gate did its job
The reviewer caught a genuine, runtime-breaking defect (BLK-001, below) — not a nitpick.
A weaker pipeline would have shipped it. The `opus` reviewer being a *stronger, different*
model than the `sonnet` developer (`SPEC.md §4`) is exactly the echo-chamber break that
surfaced it.

---

## 3. What failed

### 3.1 The run halted at deploy — root cause BLK-001
From `review_report.json` (`verdict: "rejected"`, 1 blocking + 7 non-blocking issues):

> **BLK-001 (contract_violation):** `GET /teams/{team_id}/risk-summary` returns HTTP 501
> Not Implemented (`src/api/feedback.py:248`) instead of the contracted 200 +
> `TeamRiskSummary`. The Manager dashboard calls `getTeamRiskSummary(teamId)`, gets an
> error for every team, and renders the error state — so **AC8 (manager risk badges) is
> non-functional at runtime.**

`release_report.json` → `verdict: "failed"`, health checks:
`gate-check-review = FAIL` (verdict rejected), `gate-check-tests = PASS` (failed == 0),
`rollback_available: false`, no Docker image built. The deploy gate behaved correctly —
it refused to ship code the reviewer rejected (`SPEC.md §7`, `_deploy_gate`).

### 3.2 No autonomous closure on a quality rejection (in the recorded run)
`events.log.jsonl` shows the deploy task `blocked` and "escalating to human" **three
times** (the human re-approved/re-ran, the review was re-produced, the gate re-blocked).
The run was executed before the engine's bounded **review→fix rework loop** existed, so a
`rejected` verdict had no path back to the developer — exactly the failure mode the brief
warns about, and the reason `current_stage: "failed"`, `halted: true`.

### 3.3 Wasted compute: QA ran on code the review would reject
In this run's DAG the reviewer and QA tasks were effectively siblings, so the **QA cycle
ran (and timed out + retried) on code that was simultaneously being rejected** — the
rejection only surfaced at the deploy gate, two stages later. On a metered run that is a
full QA pass (and its retry) spent on doomed code.

### 3.4 Secondary correctness debt (non-blocking, from `review_report.json`)
- **NBI-003:** embeddings never written (`embedding_status` stays `pending`) → the
  semantic-similarity / ANN path is not actually exercised (affects N6 in traceability).
- **NBI-005:** logout doesn't revoke the 7-day HttpOnly refresh cookie.
- **NBI-002:** total count ignores the `min_score` filter; **NBI-001:** O(n) count where
  O(1) would do; **NBI-006:** example weights in the API docs differ from code defaults.

---

## 4. Important nuance: the engine already has the loop the run lacked

The recorded run shows the *old* failure mode — a `rejected` review escalating to a human,
with QA having already run on the rejected code. **The current committed engine fixes
both**, so the demo's halt is a stale-artifact problem, not a missing capability:

- **Bounded review→fix rework loop** — `_request_rework` / `_drain_rework` /
  `_apply_rework` (`engine.py:578–631`): a `rejected` verdict resets the developer
  subtree with the `blocking_issues` as feedback, up to `max_rework` (default 2), then
  escalates. Wired into the wave loop at `engine.py:442`.
- **Verdict gate at `code_review`** — `_review_gate` (`engine.py:712`) returns
  `rework` on `rejected` *at the review stage*, before QA/deploy, so rejected code no
  longer burns a QA cycle. Matches `SPEC.md §3.5/§7/§8.3` and `CLAUDE.md`.
- **New post-deploy `e2e_validation` stage** — added since this run (`SPEC.md §3.8`,
  `_e2e_gate` at `engine.py:770`): the `e2e-agent` drives the *deployed* UI in a real
  browser via Playwright MCP and gates on `e2e_report.json`; a `failed` verdict feeds the
  same bounded rework loop, capped at **one** round (`STAGE_REWORK_CAP['e2e_validation']`)
  because a post-deploy re-run is expensive. The recorded run halted at deploy and never
  reached this stage, so e2e is implemented-but-not-yet-exercised on the demo.

So the gap is no longer "the engine can't recover from a quality rejection" — it's "the
recorded demo predates the fix (and the new browser-validation stage) and was never
re-run." Re-running `neural-sync` on the current engine is the single highest-value action
to convert the Phase-3 / success-criteria claims from 🟡 to ✅.

---

## 5. Scorecard vs. hackathon success criteria

| Criterion | Verdict | Basis |
|-----------|:------:|-------|
| ≥80% of runs without human intervention | 🟡 unproven | Only one end-to-end run recorded; it escalated at deploy. Engine supports `--yes` unattended runs; needs ≥1 clean run to demonstrate |
| Artifacts consistent (QA tests pass) | ✅ | 77/77 pass, all AC covered; all artifacts schema-valid |
| Recover from ≥2 simulated failures | 🟡 | Transient timeouts recovered twice (✅); quality-rejection rework exists in code but isn't shown closing in the recorded run |
| Re-run with modified requirements | ✅ | `workflow_id`-keyed runs; product update mode; `--replay` re-validation |

---

## 6. Recommended next actions (highest leverage first)

1. **Re-run `neural-sync` on the current engine** after fixing BLK-001 — demonstrates the
   rework loop closing, exercises the new `e2e_validation` browser stage, and gives one
   clean autonomous run (closes 3.1 / 3.6 / 3.7 / 4.1 / 4.3).
2. **Fix BLK-001** — implement `GET /teams/{team_id}/risk-summary` (or add the Team
   entity) so AC8 / N16 works at runtime.
3. **Write embeddings on profile create/update** (NBI-003) so the hybrid skill score and
   ANN retrieval are real (N6).
4. **Verify parallel-developer artifact isolation** — task-scoped `code_spec/<task_id>.json`
   (planner rule) so concurrent dev tasks can't clobber a shared path.
5. **Add CI + agent-driven Git/PR** (stretch S2.d) — a test gate before commit and
   PR-mode deploy.
6. **Add the Admin page** (N15) and **revoke the refresh cookie on logout** (NBI-005).

---

## 7. Honesty statement

The orchestrator design is the strongest part of this submission and stands on its own.
The demo is real but **did not complete autonomously** in its recorded form — it halted at
a correctly-functioning deploy gate on a legitimate defect. We are not claiming a clean
end-to-end autonomous ship; we are claiming a sound, deterministic factory that built a
substantial app through review and QA, caught its own defect, and now has (in code) the
feedback loop required to close that defect without a human. The remaining work to prove
it is one fix and one re-run.

*Cross-references:* `REQUIREMENTS-TRACEABILITY.md` (per-item status),
`ARCHITECTURE-DIAGRAM.md` (engine + gates), `projects/neural-sync/README.md` (the demo app).
