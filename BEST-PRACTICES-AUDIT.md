# Team‑4 Project — Best‑Practices Audit & Change List

> Scope: `team-4-project/` (the "Agentic SDLC & Software Factory" submission).
> Method: read every agent definition, the orchestrator engine, schemas, the
> real demo run (`projects/neural-sync`), and the local config; cross‑checked
> against `HACKATHON-REQUIREMENTS.md` (the Task / Success Criteria / Verification
> Checklist) and current external best practice (Anthropic engineering, Claude
> Code subagent docs, agentic‑SDLC writeups — see **Sources**).
> Date: 2026‑06‑27.

## TL;DR verdict

The **control plane is genuinely good**: the orchestrator (`src/orchestrator/engine.py`)
is deterministic, event‑sourced, atomically persisted, injectable for testing,
DAG‑scheduled with per‑task retry/escalate, and least‑privilege by role. That part
already matches best practice and the hackathon's "determinism > creativity" constraint.

The problems are **at the edges**: the feedback loop is missing, the review→deploy
gating lets rejected code burn a full QA cycle, the agent fleet is model‑monotone
(no reviewer/developer separation), the local config is checked in with a personal
machine path, and the docs describe a CLI that no longer exists. Most decisively:
**the only real end‑to‑end demo (`neural-sync`) ended in `failed`/`halted`** — it did
not complete autonomously, which directly threatens the Phase‑3 / Success‑Criteria claims.

Items are ordered P0 (blocks a requirement / correctness) → P1 (best‑practice gap) → P2 (polish).

---

## P0 — Must fix (requirement or correctness risk)

### P0‑1. The headline demo did not finish — there is no rework/feedback loop
**Evidence:** `projects/neural-sync/artifacts/workflow_state.json` → `current_stage: failed`,
`halted: true`, `T-08-DEPLOY: blocked`. `review_report.json` verdict = `rejected` with 3
**legitimate** contract violations (missing `POST /auth/refresh`, login not setting the
HttpOnly cookie, `allow_origins=["*"]` + `allow_credentials=True`). The pipeline has **no
path that feeds a rejected review back to the developer**; a `rejected` verdict is classed
*unrecoverable* (`engine.py:_deploy_gate`) → block → human.
**Why it matters:** Hackathon Phase 1 mandates a *Monitoring & Feedback Loop* stage and the
Success Criteria require "recover from ≥2 simulated failures" + "≥80% without human
intervention." A quality rejection that can only be resolved by a human is the exact failure
mode the brief warns about. `SPEC.md §3.8` itself admits `monitoring_feedback` is *reserved /
not implemented*.
**Change:**
- Implement a bounded **review→fix rework cycle**: on `rejected`, the orchestrator
  re‑dispatches the owning `developer-agent` task with `review_report.blocking_issues`
  injected as input, capped (e.g. 2 rework rounds) before escalating. This is the
  "generate → feedback → modify" self‑refine loop that is now standard practice.
- Either implement Stage 8 (`monitoring_feedback`) minimally (post‑deploy health‑check
  event → backlog task) **or** delete the stage and the Orchestrator's `monitoring_feedback`
  mapping from the spec so the deliverable doesn't claim a stage it doesn't run.
- Re‑run `neural-sync` (or a fresh demo) to a real `complete` state and capture the artifacts;
  a `failed` run cannot back the Phase‑3 "built end‑to‑end by agents" claim.

### P0‑2. `code_review` gate does not enforce the verdict — QA money is spent on rejected code
**Evidence:** `engine.py:_check` only runs `scan_source` for `stage == "code_review"`; the
review **verdict is never gated there**. In the demo DAG, `T-06-REVIEW` and `T-07-QA` are
**siblings in the same wave** (both `depends_on: [T-04, T-05]`), so QA runs concurrently with
review — it timed out at 1800s, retried, and cost **$2.08 / ~2.06M tokens** (`events.log.jsonl`)
for code that review was simultaneously rejecting. The `rejected` verdict is only caught two
stages later at the deploy gate.
**Why it matters:** `SPEC.md §9` governance literally says *"No advance past review with a
blocking issue (verdict `rejected`)"* — the implementation violates its own spec, and the
topology guarantees wasted spend (cost optimisation is an explicit stretch goal).
**Change:**
- Add a `code_review` gate predicate: `verdict == "rejected"` → block/route‑to‑rework *before*
  QA/deploy. Make `code_review` a dependency of `testing_validation` (QA after review, not
  beside it) so a rejection short‑circuits the expensive QA stage.
- Reconcile `SPEC.md §7` (which omits a review‑verdict gate) with `§9` (which requires one).

### P0‑3. Single shared artifact paths break the "parallel developers" claim
**Evidence:** Every developer task's declared output is the single path
`artifacts/code_spec.json` (`CLAUDE.md`/`SPEC.md §6`, `developer.agent.md`). The engine
supports `max_parallel` and the spec advertises *"sibling developer-agent tasks … overlap"*
(`orchestrator.agent.md` step 2). Two developer tasks in one wave would both write the same
`artifacts/code_spec.json` → last‑writer‑wins clobber. (The demo only dodged this because its
workplan serialised T‑04→T‑05.)
**Why it matters:** "Multi‑agent parallelization" is a stated stretch goal; the artifact
convention silently makes it unsafe.
**Change:** Make per‑task outputs task‑scoped, e.g. `artifacts/code_spec/<task_id>.json`
(and same for any per‑task review). Update the schema map in `validation.py:SCHEMA_BY_NAME`
to resolve by glob/prefix, and the agent prompts accordingly.

### P0‑4. Docs/config describe a CLI that doesn't exist
**Evidence:** `README.md:40` → `python3 -m orchestrator run <project-name> [--auto-approve]`
and `:46` reference `--auto-approve`. The actual entrypoint (`__main__.py`) takes a positional
`project` (no `run` subcommand) and uses `--yes`, `--prompt`, `--approve`. The same dead
invocation is baked into `settings.local.json` (`...orchestrator run _replay_demo --auto-approve`).
Anyone following the README gets an argparse error.
**Why it matters:** Reproducibility is a judged quality; a working‑prototype deliverable whose
documented run command fails is an automatic credibility hit.
**Change:** Update `README.md` (and any `settings` allow‑entries) to the real interface:
`PYTHONPATH=src python3 -m orchestrator <project> --prompt "…" --yes` /
`--approve requirements,architecture,production_deploy` / `--replay`.

---

## P1 — Best‑practice gaps (agents, config, governance)

### P1‑1. The whole fleet is one model — reviewer/developer echo chamber
**Evidence:** all 8 `.claude/agents/*.agent.md` declare `model: sonnet`, including
`developer-agent` **and** `reviewer-agent`.
**Best practice:** Diversify the reviewer from the implementer to break the echo chamber, and
match model strength to task ("one Opus orchestrator + Sonnet workers is ~40% cheaper than all‑Opus";
"code review on Sonnet, linting on Haiku, enforced in YAML"). The sibling ForgeLoop project does
exactly this (REVIEW on a different model from DEV).
**Change:** Give `reviewer-agent` a different/stronger model than `developer-agent` (or vice‑versa),
route the cheap mechanical stages (devops Dockerfile authoring) to Haiku, and codify the choice in
each agent's frontmatter so cost can't silently regress. The engine already supports
per‑run `--model`; consider a per‑agent default instead of a global override.

### P1‑2. `settings.local.json` is committed, machine‑specific, and over‑broad
**Evidence:** `.claude/settings.local.json` is **git‑tracked** and contains a hardcoded
`/Users/hadkevich/Developer/agentic-sdlc/...` path, dead `run/--auto-approve` commands, broad
allows (`Bash(python3 *)`, `Bash(node *)`, `Bash(pip3 install *)`, `Bash(git rm *)`), and
`enabledMcpjsonServers: ["notion"]` although **no `.mcp.json` exists in `team-4-project/`**.
There is **no shared `settings.json`**.
**Best practice:** `settings.local.json` is personal and should be git‑ignored; shared,
reproducible config (and least‑privilege allow‑lists scoped to exact commands) belongs in a
committed `settings.json`. Wildcards like `python3 *` defeat the point of an allow‑list.
**Change:**
- `git rm --cached .claude/settings.local.json` and add it to `.gitignore`.
- Add a committed `.claude/settings.json` with **narrow** permissions (exact pytest/orchestrator
  invocations) and the real MCP servers (drop `notion`, or add a real `.mcp.json`).
- Remove the leaked absolute path.

### P1‑3. No CI, no agent‑driven Git integration
**Evidence:** no automated test gate; tests (`tests/test_orchestrator.py` 466 LOC,
`tests/test_schemas.py`) exist but run only locally. DevOps agent deploys **locally in Docker**
only.
**Best practice / brief:** "Integration with Git (PRs fully agent‑driven)" and automated
deployment are stretch goals; a test gate that runs the schema+engine tests before each commit
is table stakes for a "software factory."
**Change:** Add a test gate that runs `pytest tests/` (engine + schema validation) and
optionally a `--replay` validation of a reference project — as a **local pre-commit/pre-push
hook or a `make check` target**.
Consider an opt‑in PR‑creation step in the DevOps agent (create branch + PR with the release
report attached) to satisfy the agent‑driven‑Git stretch goal.

### P1‑4. Security gate is a regex denylist only
**Evidence:** `validation.py:_DANGEROUS` is a small regex list (eval, innerHTML, `shell=True`,
hardcoded‑secret heuristic) run once at `code_review`.
**Risk:** false negatives (no dependency/secret scanning, no AST) and false positives (a React app
legitimately touching `innerHTML`/`document.write` would be an *unrecoverable* block).
**Change:** Keep the denylist as a fast pre‑filter but add (a) a dependency/secret scanner step in
the QA or DevOps stage (e.g. `pip-audit`/`npm audit`, gitleaks), and (b) make denylist hits a
*reviewer‑surfaced finding* rather than an automatic unrecoverable block, to cut false positives.

### P1‑5. Three mandatory human gates vs the ≥80%‑autonomous criterion
**Evidence:** `HUMAN_GATES` = requirements, architecture, production_deploy. Unattended runs need
`--yes`, which blanket‑approves all three.
**Best practice:** Humans "set intent and validate outputs," not gate every stage. The all‑or‑nothing
`--yes` is coarse.
**Change:** Make gates configurable per‑run (approve a subset, or a `--trust` profile), and define
the autonomy metric explicitly (e.g. "checkpoints are advisory in CI mode") so the ≥80% claim is
measurable and defensible rather than dependent on `--yes`.

---

## P2 — Polish / consistency

- **P2‑1. Spec vs. impl drift beyond the CLI.** `orchestrator-agent` is mapped to the unimplemented
  `monitoring_feedback` stage (`engine.py:AGENT_STAGE`); the agent markdown still lists the full
  8‑stage sequence. Align the docs with what actually runs (see P0‑1).
- **P2‑2. Observability hardcodes projects.** `observability/dashboard.html` `KNOWN_PROJECTS`
  defaults to `tic-tac-toe`; new projects must be added by hand. Auto‑discover `projects/*` instead.
- **P2‑3. Reviewer reads `test_plan.json` "if exists" but review runs beside QA** — so the reviewer
  usually never sees test results. Either order QA→review or drop the stale input reference.
- **P2‑4. `data-model.json` / `api-contracts.json` are existence‑checked only** (`validation.py`
  comment) — no OpenAPI 3.1 validation despite the spec naming the format. Add a real OpenAPI check.
- **P2‑5. No top‑level `requirements.txt`/`pyproject` for the engine itself** — deps (`jsonschema`,
  `pytest`) are installed ad‑hoc via the local settings. Pin them.

---

## Mapping to the hackathon acceptance criteria

| Criterion (`HACKATHON-REQUIREMENTS.md`) | Status in team‑4 | Gap → item |
|---|---|---|
| 8 lifecycle stages, each agent‑owned w/ I/O contracts | 7 implemented; Stage 8 reserved | P0‑1, P2‑1 |
| Structured I/O, schema‑validated, no free‑form chat | ✅ strong | — |
| State mgmt / event log / retries / escalation | ✅ strong (event‑sourced, atomic) | — |
| ≥80% runs without human intervention | Unproven — only demo ended `failed`/`halted` | P0‑1, P1‑5 |
| Recover from ≥2 simulated failures | Transient/crash recovery ✅; **quality‑rejection rework ✗** | P0‑1, P0‑2 |
| Re‑run with modified requirements | Supported (per‑project `--prompt`) ✅ | — |
| Working prototype (reproducible) | Engine ✅ but documented CLI is broken | P0‑4, P1‑2, P2‑5 |
| Demo built end‑to‑end by agents | `neural-sync` reached deploy then **blocked** | P0‑1, P0‑2 |
| Stretch: parallelization / cost / git‑PRs / self‑improve | Parallelism unsafe; no CI/PR; no feedback loop | P0‑3, P1‑1, P1‑3 |

---

# Appendix B — Implementation status (this branch)

The following P0 items are **implemented and tested** on
`claude/team4-best-practices-analysis-mg4wyw` (engine tests: 32 passing):

- **ENG‑2 ✅** `code_review` gate now evaluates the verdict (`engine.py:_review_gate`).
- **ENG‑1 ✅** Bounded review→fix rework loop (`_request_rework`/`_drain_rework`/
  `_apply_rework`) — resets the developer ancestors + dependents, preserves the review
  report as feedback, caps at `max_rework` (default 2, `--max-rework`).
- **ENG‑4 ✅** Per‑task `artifacts/code_spec/<task_id>.json` resolution
  (`validation.py:schema_for_output`).
- **ENG‑3 ✅ (convention)** Planner now orders QA after review; the verdict gate
  short‑circuits before QA when `max_rework=0`.
- **DOC‑1 ✅** README CLI corrected to the real interface.
- **SDLC‑1 ✅ (partial)** `SPEC.md` §3.5/§7/§8.3, `CLAUDE.md` gates, and the mermaid
  diagram now agree on the rework loop.
- **CFG‑1 ✅ / CFG‑2 ✅** `settings.local.json` untracked + git‑ignored; narrow shared
  `.claude/settings.json` added.
- **TST‑1 ✅ / TST‑2 ✅** New tests: rework‑converges, rework‑escalates‑at‑cap,
  verdict‑caught‑before‑QA, per‑task code‑spec paths.

**Second pass (platform hardening, 43 tests passing):**
- **AGT‑1 ✅** Per‑role models: architect+reviewer→opus (reviewer ≠ sonnet developer),
  devops→haiku; pinned in frontmatter, documented in `SPEC.md §4`.
- **AGT‑2 ✅** Least‑privilege tools: `Edit` removed from authoring agents (only
  `developer` patches code); `Bash` dropped where unused.
- **SEC‑2 ✅** Two‑tier security baseline — exec/injection/secret sinks block;
  `innerHTML`/`document.write` are non‑blocking warnings (no false‑positive hard‑fail).
- **ENG‑8 ✅** Run‑level cost breaker (`--max-cost-usd`) folding cost from the event log.
- **ENG‑6 ✅** `--json` final‑state output for CI.
- **OBS‑1 ✅** Dashboard auto‑discovers `projects/*`. **OBS‑2 ✅** (cost rollup already shipped).
- **SCH‑1 ✅** Real schemas for `api-contracts.json` (OpenAPI 3.x structural) and
  `data-model.json`, wired into validation; real artifacts validate.
- **CI‑1 ⛔ (not applicable)** No automated CI gate in this project — run the suite locally
  with `python3 -m pytest tests/` (optionally wired as a local pre-commit/pre-push hook).
- **ENG‑5 ✅** Minimal Stage 8 `monitoring_feedback`: post‑deploy health → feedback event
  + `backlog.json` remediation queue.

- **ENG‑7 ✅** `unblock()` now derives the resume stage from the DAG (earliest reset
  stage) instead of hardcoding `code_generation`.
- **SEC‑1 ◑ (partial)** QA agent instructed to run a dependency vulnerability check
  (`pip-audit`/`npm audit`) and record findings.

Still open (not yet done): CI‑2 (agent‑driven PR), AGT‑3/4, SCH‑2/3/4, OBS‑3, SDLC‑2/3,
and **DEMO‑1** (re‑drive a demo to `complete` — needs a live agent run / API budget).

---

# Appendix A — Complete improvement checklist

Exhaustive list of every necessary improvement, grouped by area. Each item has a
stable ID, the evidence, and the concrete change. `[P0]`/`[P1]`/`[P2]` = priority.

## A. Orchestrator / engine (`src/orchestrator/`)
- **ENG‑1 [P0]** Implement the **review→rework loop**. The authoritative diagram
  (`.claude/docs/workflow/mermaid.md`) draws `E --|blocked|--> D` and
  `F --|failed|--> D`, but `engine.py` implements neither — a `rejected` review or a
  failing QA only blocks/escalates. Add bounded re‑dispatch of the developer task with
  `blocking_issues` injected (cap rounds, then escalate).
- **ENG‑2 [P0]** Add a **`code_review` gate predicate** on `verdict` (`engine.py:_check`
  currently only runs `scan_source` for that stage). Block/route‑to‑rework on `rejected`
  *before* QA/deploy.
- **ENG‑3 [P0]** Make QA **depend on** review (or share a verdict short‑circuit) so a
  rejection doesn't run the expensive QA wave (sibling topology cost — see P0‑2).
- **ENG‑4 [P0]** Fix **per‑task output collisions**: task outputs resolve to a single
  `artifacts/code_spec.json`; parallel developer tasks clobber it. Use task‑scoped paths
  and update `validation.py:schema_for_output` to resolve by prefix/glob.
- **ENG‑5 [P1]** Implement **Stage 8 `monitoring_feedback`** minimally (post‑deploy
  health event → backlog/remediation task) or remove it from the contract; today
  `AGENT_STAGE` maps `orchestrator-agent → monitoring_feedback` but nothing runs it.
- **ENG‑6 [P1]** Surface a **machine‑readable run summary** (exit JSON: stage, blocked
  tasks, cost totals) so callers/CI can assert outcomes without parsing stdout.
- **ENG‑7 [P2]** `unblock()` resets `current_stage` to a hardcoded `code_generation` on
  recovery — derive it from the DAG instead of assuming the failure was in dev.
- **ENG‑8 [P2]** Add a **global wall‑clock / total‑cost budget** breaker (per‑task
  timeout exists; a run‑level cap does not) to bound runaway spend.

## B. SDLC pipeline & stage design
- **SDLC‑1 [P0]** Reconcile the lifecycle across `SPEC.md`, `CLAUDE.md`, the mermaid
  diagram, and the engine — they disagree on whether review/QA loop back and whether
  Stage 8 exists. Pick one truth and make all four match.
- **SDLC‑2 [P1]** Define and **enforce traceability** mechanically:
  `requirements.acceptance_criteria → workplan task → test_case.maps_to_requirement`.
  Today it's only asked of agents in prompts, never validated by the orchestrator.
- **SDLC‑3 [P1]** Add an explicit **idempotent re‑run with modified requirements** flow
  (the brief requires it): diff new vs old requirements → regenerate only affected
  workplan subtrees, instead of re‑running the whole pipeline.
- **SDLC‑4 [P2]** Document the **escalation → human → resume** contract end‑to‑end
  (which `--approve`/`--retry` clears which state) as a runbook.

## C. Agent definitions & model strategy (`.claude/agents/`)
- **AGT‑1 [P1]** Diversify models: all 8 agents are `model: sonnet`. Make
  `reviewer-agent` differ from `developer-agent` (break the echo chamber); route the
  mechanical devops Dockerfile step to Haiku; consider Opus for architect. Codify in
  frontmatter so cost can't silently regress.
- **AGT‑2 [P1]** Tighten **per‑agent toolsets** to least privilege: `product`,
  `planner`, `architect` should not need `Edit` (they author new JSON, not patch code);
  `reviewer` correctly has no `Bash`/`Write`‑to‑src — apply the same rigor everywhere.
- **AGT‑3 [P2]** Sharpen each agent's **`description`** for correct auto‑delegation
  (Claude routes on the description) — make them action‑oriented and disjoint.
- **AGT‑4 [P2]** Add an explicit **output‑contract reminder + self‑validation step** to
  every agent prompt (most have it; make it uniform) so agents validate against the
  schema before returning, cutting recoverable retries.
- **AGT‑5 [P2]** Give the `reviewer-agent` access to test results: it lists
  `test_plan.json (if exists)` as input, but review runs beside QA so it never sees them
  (depends on fixing ENG‑3 ordering).

## D. Communication protocol, artifacts & schemas (`schemas/`)
- **SCH‑1 [P1]** Validate `api-contracts.json` as **real OpenAPI 3.1** and add a schema
  for `data-model.json`; both are currently existence‑checked only
  (`validation.py:SCHEMA_BY_NAME` comment).
- **SCH‑2 [P1]** Version the schemas and assert `spec_version` consistency across
  artifacts in a run (the field exists; nothing checks it matches).
- **SCH‑3 [P2]** Add a `schema_version`/`$id` to each schema file and a test that every
  example artifact validates (partly covered by `tests/test_schemas.py`).
- **SCH‑4 [P2]** Make `event.schema.json` require `metrics` for `success` events from
  real‑agent runs so cost observability can't silently go missing.

## E. Governance, security & guardrails
- **SEC‑1 [P1]** Replace/augment the **regex denylist** (`validation.py:_DANGEROUS`)
  with real scanning: dependency audit (`pip-audit`/`npm audit`) + secret scan
  (gitleaks) as a QA/devops step.
- **SEC‑2 [P1]** Make denylist hits a **reviewer finding**, not an automatic
  *unrecoverable* block — a React app legitimately using `innerHTML`/`document.write`
  would be hard‑blocked today (false positives).
- **SEC‑3 [P1]** Define the **autonomy/human‑gate policy** explicitly so the
  "≥80% without human intervention" claim is measurable; replace all‑or‑nothing `--yes`
  with per‑gate / trust‑profile approval.
- **SEC‑4 [P2]** Add a **secrets‑in‑logs guard** (SPEC §9 forbids secrets in
  prompts/logs; nothing enforces it) — scrub artifact/event writes.
- **SEC‑5 [P2]** Sandbox/network‑restrict the `ClaudeAgentRunner` subprocess for
  unattended `bypassPermissions` runs (it runs generated code).

## F. Observability & traceability
- **OBS‑1 [P1]** Auto‑discover `projects/*` in `observability/dashboard.html`
  (`KNOWN_PROJECTS` is hardcoded, defaults to `tic-tac-toe`).
- **OBS‑2 [P1]** Add a **cost/token rollup view** (per stage + run total) from the
  `metrics` already on events — directly supports the cost‑optimisation stretch goal.
- **OBS‑3 [P2]** Emit `decision`/ADR events to the log so the "observability of
  decisions" governance item is satisfiable from the event stream.

## G. Repo hygiene & configuration (`.claude/`, root)
- **CFG‑1 [P1]** `git rm --cached .claude/settings.local.json`, add it to `.gitignore`
  (it's personal), and remove the leaked `/Users/hadkevich/...` absolute paths.
- **CFG‑2 [P1]** Add a committed **`.claude/settings.json`** (shared) with **narrow**
  allow‑lists (exact pytest/orchestrator commands, not `Bash(python3 *)`).
- **CFG‑3 [P1]** Fix MCP config: `settings.local.json` enables `notion` but there is **no
  `team-4-project/.mcp.json`**. Add a real `.mcp.json` or drop the reference.
- **CFG‑4 [P1]** Add an engine **dependency manifest** (`requirements.txt`/`pyproject`)
  pinning `jsonschema`, `pytest`; today deps are installed ad‑hoc via local settings.
- **CFG‑5 [P2]** Add `.python-version`/tooling pin so runs are reproducible.

## H. CI/CD & Git integration
- **CI‑1 [P1]** Add a **local test gate** (pre-commit/pre-push hook or `make check`)
  running `pytest tests/` (engine + schema) and a `--replay` validation of a reference
  project. No automated remote CI — that is an explicit project constraint.
- **CI‑2 [P1]** Add the agent‑driven **PR flow** stretch goal: optional devops step that
  creates a branch + PR with the release report attached.
- **CI‑3 [P2]** Gate merges on the CI run; publish coverage.

## I. Testing
- **TST‑1 [P0]** Add tests for the new **rework loop** (review `rejected` → developer
  re‑dispatch → converge or escalate at cap).
- **TST‑2 [P1]** Add a test for **parallel developer tasks not clobbering** outputs
  (guards ENG‑4).
- **TST‑3 [P1]** Add an **integration smoke test** of `ClaudeAgentRunner` command
  construction (mock subprocess) — currently only `CallableRunner`/`ReplayRunner` are
  exercised.
- **TST‑4 [P2]** Add a traceability test (every acceptance criterion maps to ≥1 task and
  ≥1 test) once SDLC‑2 lands.

## J. Cost & performance
- **COST‑1 [P1]** Implement ENG‑3 (don't QA rejected code) + AGT‑1 (cheaper models per
  task) — the demo spent **$2.08/run on QA alone**, much of it on rejected code.
- **COST‑2 [P2]** Cache/reuse prior artifacts on resume (ReplayRunner exists; wire a
  "changed‑inputs‑only" re‑run, see SDLC‑3).
- **COST‑3 [P2]** Tune `--max-parallel` defaults and the per‑task `timeout` (QA hit the
  1800s wall and retried — a wasted full attempt).

## K. Documentation & spec consistency
- **DOC‑1 [P0]** Fix the **broken CLI in `README.md`** (`orchestrator run … --auto-approve`
  → `python -m orchestrator <project> --prompt … --yes`), and the same dead command in
  `settings.local.json`.
- **DOC‑2 [P1]** Make `SPEC.md`, `CLAUDE.md`, mermaid, and engine agree (see SDLC‑1);
  remove claims about stages/loops that don't run.
- **DOC‑3 [P2]** Add a short **EVALUATION** doc for team‑4 itself (the root `EVALUATION.md`
  is the *sibling ForgeLoop* project, not this one) mapping the real demo run to the
  success criteria.

## L. Demo & evaluation
- **DEMO‑1 [P0]** Drive a demo (fixed `neural-sync` or a fresh one) to a real
  `complete` state and commit the artifacts — the current only run is `failed`/`halted`,
  which cannot back the Phase‑3 deliverable.
- **DEMO‑2 [P1]** Demonstrate the **two failure recoveries** the brief requires with
  reproducible commands (one transient/crash via `--retry`, one quality‑rejection via the
  new rework loop).
- **DEMO‑3 [P1]** Demonstrate the **re‑run with modified requirements** path explicitly
  (SDLC‑3) and capture the diff‑driven re‑plan.

## Sources
- [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)
- [Claude Code Subagents: A 2026 Practical Guide — Tembo](https://www.tembo.io/blog/claude-code-subagents)
- [Best practices for Claude Code subagents — PubNub](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)
- [How we built our multi-agent research system — Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Building Effective AI Agents — Anthropic](https://resources.anthropic.com/building-effective-ai-agents)
- [Multi-Agent Orchestration Patterns — Prateek Sharma](https://www.prateek-sharma.com/blog/multi-agent-orchestration-patterns/)
- [How Anthropic Built Multi-Agent Deep Research — The AI Engineer (Substack)](https://theaiengineer.substack.com/p/how-anthropic-built-multi-agent-deep)
- [A guide to the agentic SDLC — CodeRabbit](https://www.coderabbit.ai/guides/agentic-sdlc)
- [Building an Agentic SDLC in Practice — Vantor Engineering](https://vantor.com/blog/building-an-agentic-sdlc-anthropics-emerging-harness-design-patterns/)
- [Traceability and Accountability in Role-Specialized Multi-Agent LLM Pipelines — arXiv 2510.07614](https://arxiv.org/pdf/2510.07614)
</content>
</invoke>
