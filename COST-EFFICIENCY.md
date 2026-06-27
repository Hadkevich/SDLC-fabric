# Cost & Efficiency

> Scorecard §7 (15 pts): **7.1** auto-collected tokens/cost/time-per-role report ·
> **7.2** different models per role · **7.3** A/B evidence that a cheaper model is good enough.
> This page maps each sub-item to the artifact that satisfies it.

The factory already records resource spend per agent: `engine._extract_metrics`
(`src/orchestrator/engine.py`) pulls `usage` / `total_cost_usd` / `duration_ms` from every
agent's `claude -p --output-format json` envelope, and `_event` writes them into
`events.log.jsonl` under `metrics`. A run-level `--max-cost-usd` breaker (`SPEC.md §9`) folds that
same cost and halts dispatch when a ceiling is hit.

---

## 7.1 — Auto-collected per-role report

A persisted report is generated from the event log — automatically at run finalization
(`Orchestrator._write_cost_report`, best-effort) and on demand:

```bash
python -m orchestrator projects/neural-sync --cost-report
# → projects/neural-sync/artifacts/cost_report.json  (+ cost_report.md)
```

Generator: [`src/orchestrator/cost_reporter.py`](src/orchestrator/cost_reporter.py) ·
schema: [`schemas/cost_report.schema.json`](schemas/cost_report.schema.json) · it reuses the
event-log fold (same source of truth as the cost breaker) and resolves each role's model tier from
`.claude/agents/*.agent.md` frontmatter. Roles whose work is mechanical/non-LLM (devops,
orchestrator) or not exercised (e2e) are marked `coverage: none` with a clarifying note rather than
read as a gap.

**Recorded `neural-sync` run** ([`cost_report.md`](projects/neural-sync/artifacts/cost_report.md)):

| Agent role | Model | Cost $ | Coverage |
|---|---|--:|---|
| developer-agent | sonnet | 5.071 | partial |
| reviewer-agent | opus | 4.983 | partial |
| architect-agent | opus | 3.725 | full |
| qa-agent | sonnet | 2.079 | partial |
| planner-agent | sonnet | 0.368 | full |
| product-agent | sonnet | 0.283 | full |
| devops-agent | haiku | 0.000 | none (mechanical deploy) |
| orchestrator-agent | sonnet | 0.000 | none (orchestration logic) |
| **TOTAL** | | **16.508** | 16.2M tokens · 5611s |

The same per-event spend is also rendered live on the observability dashboard
(`observability/dashboard.html`).

---

## 7.2 — Different models per role (routing enforced in code)

Models are pinned per role in each agent's frontmatter (`.claude/agents/*.agent.md`), so cost
can't silently regress; `--model` overrides all agents only when explicitly requested. Documented
in `SPEC.md §4`.

| Role | Model tier | Rationale |
|---|---|---|
| architect, reviewer | **opus** (frontier) | hardest reasoning; reviewer is deliberately a *stronger, different* model than the developer to break the echo chamber |
| developer, planner, product, qa, e2e, orchestrator | **sonnet** (mid) | standard generation / structured work |
| devops | **haiku** (small/fast) | mechanical deploy + log/format work |

This matches the scorecard's routing guide (Architect/complex → large; Developer → mid; simple →
small) and the recorded report above confirms the spend follows the tiers (opus roles dominate cost,
haiku devops ≈ $0).

---

## 7.3 — A/B evidence: where a cheaper model is good enough

A live micro-A/B runs the rubric's own **"log summarizer"** example through all three tiers and
records cost/tokens/latency + the outputs side-by-side:

```bash
python scripts/cost_ab_experiment.py
# → projects/neural-sync/artifacts/cost_ab_experiment.json  (+ .md)
```

Harness: [`scripts/cost_ab_experiment.py`](scripts/cost_ab_experiment.py) · results:
[`cost_ab_experiment.md`](projects/neural-sync/artifacts/cost_ab_experiment.md).

**Finding (recorded run):** all three tiers produced a correct, comparable 3-bullet summary of the
event log (8 stages, the code_review/code_generation/testing retries, final outcome) — haiku's was
in fact the most detailed:

| Model | Cost $ | Latency |
|---|--:|--:|
| **haiku** | **0.0447** | 37s |
| sonnet | 0.1117 | 40s |
| opus | 0.1809 | 35s |

→ **haiku is ~4.0× cheaper than opus** for an equivalent result. The cheap/fast tier is good enough
for log summarization, which is exactly why mechanical/summary roles (devops, log/format) route to
**haiku** while **opus** is reserved for the hard reasoning roles (architect, reviewer). (Cost
includes shared system-prompt cache overhead at this tiny task size, so the **ratio** is the signal;
side-by-side outputs are in the artifact.)
