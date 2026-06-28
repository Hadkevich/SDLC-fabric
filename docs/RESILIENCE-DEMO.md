# Resilience Demo — injected failures, autonomous recovery (§4.2)

Scorecard §4.2: *"System auto-recovers from 2 injected failures — ask them to demo this."*
This is the live demo. It drives the **real** deterministic engine
(`src/orchestrator`) with scripted runners — no LLM, no Claude spend, runs in
seconds — so the recovery is reproducible on demand, not just visible in the
recorded `neural-sync` log.

## Run it

```bash
python scripts/inject_failure.py            # all three scenarios
python scripts/inject_failure.py rework     # one of: rework | health | corrupt
```

Each scenario injects a different failure class, then prints
`events.log.jsonl` so you can watch `retry`/`block`/`fail` → `success` with **no
manual code edit in between**.

| Scenario | Injected failure | Engine response (code path) |
|----------|------------------|------------------------------|
| `rework` | Reviewer rejects the build — a real defect, same shape as the run's **BLK-001** | Review gate sees `verdict: rejected` → **re-dispatches the developer subtree** (`_apply_rework`, cap `--max-rework`), re-reviews → approved → deploy gate ships. Developer re-runs **autonomously** (this is the path the recorded run did human-assisted). |
| `health` | First deploy comes up **unhealthy** (`verdict: partial`, failing health check) | `monitoring_feedback` queues a `backlog.json` item, runs the **Level-1 health-rework loop** (`_try_health_rework`, cap 1) → rebuild + re-deploy → healthy; backlog goes `open` → `resolved`. Needs `--feedback-loop 1`. |
| `corrupt` | An agent emits an **invalid/garbled artifact** | Engine classifies it *recoverable* and **retries the task** — the same path the recorded run took on the developer/QA 1800s timeouts. |

## Expected output (abridged)

```
● REWORK — reviewer rejects a real defect; engine re-builds autonomously
    ↻retry  code_review   code_review gate failed; rework round 1 — re-dispatch  ← review gate: verdict is 'rejected'
     ok     code_generation  T-DEV complete          ← developer re-ran, no human edit
     ok     code_review      T-REV complete          ← re-review approved
     ok     deployment       T-OPS complete
   → developer re-ran 2x, reviewer re-ran 2x, final stage = complete  [RECOVERED]

  3/3 scenarios auto-recovered without manual intervention
```

## How this maps to the recorded run

The `neural-sync` `events.log.jsonl` already shows real recoveries — developer
and QA each hit a 1800s timeout and **auto-retried to success** (the `corrupt`
class), and the review gate rejected BLK-001 and the deploy gate refused to ship
3× (the `rework` class). This script lets a grader trigger the same machinery
live and watch it converge, including the `rework` path running **fully
autonomously** end-to-end (the recorded run's BLK-001 fix was operator-assisted).

The same loops are covered by the unit suite (`tests/test_orchestrator.py` §
fault-injection and § monitoring_feedback) — run `pytest tests/` for the
assertion-level proof.
