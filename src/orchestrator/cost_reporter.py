"""Cost & Efficiency report generator (scorecard §7.1).

Folds the per-agent ``metrics`` already written to a project's
``artifacts/events.log.jsonl`` into a persisted, per-agent-role rollup —
tokens / cost_usd / duration + the model tier each role used + totals.

Pure read of the event log: no LLM, no Orchestrator instance. Used two ways:
  * on demand —  ``python -m orchestrator <project> --cost-report``
  * automatic —  written best-effort at run finalization (engine.py), so the
    report is genuinely *auto-collected* at the end of every run.

The per-event ``metrics`` are produced by ``engine._extract_metrics`` /
``engine._event`` from each agent's ``claude -p --output-format json`` envelope.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .frontmatter import read_frontmatter_lines

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "cost_report.schema.json"

# Anthropic input price per 1M tokens by model tier (source: /claude-api skill —
# opus-4-8 $5, sonnet-4-6 $3, haiku-4-5 $1). Used only to value the prompt-cache
# savings counterfactual; absolute spend comes from the API's reported cost_usd.
_INPUT_PRICE_PER_MTOK = {"opus": 5.0, "sonnet": 3.0, "haiku": 1.0}
# Cache read bills at ~0.1x input (so reading a cached token saves 0.9x its full
# price); a 5-minute cache write bills at ~1.25x input (a 0.25x premium over a
# fresh read). Net savings = 0.9*read - 0.25*creation, priced at the role's model.
_CACHE_READ_SAVING_MULT = 0.9
_CACHE_WRITE_PREMIUM_MULT = 0.25


def _cache_savings_usd(model: Optional[str], cache_read: int, cache_creation: int) -> float:
    """Counterfactual $ saved by prompt caching for one role, priced at its model
    tier. Returns 0.0 when the model tier has no known input price."""
    price = _INPUT_PRICE_PER_MTOK.get((model or "").lower())
    if not price:
        return 0.0
    per_token = price / 1_000_000.0
    saved = cache_read * _CACHE_READ_SAVING_MULT - cache_creation * _CACHE_WRITE_PREMIUM_MULT
    return round(saved * per_token, 6)

# Roles whose stage work is mechanical / not an LLM call (or wasn't exercised on a
# given project), so absent metrics are expected — not a coverage gap.
_NON_LLM_NOTE = {
    "devops-agent": "deployment is a mechanical/local step — no LLM usage recorded",
    "orchestrator-agent": "monitoring_feedback is orchestration logic — no LLM call",
    "e2e-agent": "browser validation not exercised on this project",
}

_METRIC_KEYS = ("input_tokens", "output_tokens", "cost_usd", "duration_ms")


def _num(value, cast):
    """Coerce a metric value to int/float, returning cast(0) on a malformed value."""
    try:
        return cast(value or 0)
    except (TypeError, ValueError):
        return cast(0)


# ─────────────────────────────────────────────────────────────────────────────
# Agent → model mapping (parsed from .claude/agents/*.agent.md frontmatter)
# ─────────────────────────────────────────────────────────────────────────────

def _frontmatter_field(path: Path, field: str) -> Optional[str]:
    """Return a single scalar frontmatter field (e.g. ``model:``) or None."""
    for s in read_frontmatter_lines(path):
        if s.startswith(f"{field}:"):
            return s.split(":", 1)[1].strip().strip("'\"") or None
    return None


def agent_model_map(repo_root: Path) -> dict[str, str]:
    """Map ``<role>-agent`` → model tier from ``.claude/agents/<short>.agent.md``.

    The agent file convention is ``<short>.agent.md`` and the event-log ``agent``
    field is ``<short>-agent`` (e.g. ``developer.agent.md`` → ``developer-agent``).
    """
    agents_dir = Path(repo_root) / ".claude" / "agents"
    out: dict[str, str] = {}
    if not agents_dir.is_dir():
        return out
    for f in sorted(agents_dir.glob("*.agent.md")):
        short = f.name[: -len(".agent.md")]
        name = short if short.endswith("-agent") else f"{short}-agent"
        model = _frontmatter_field(f, "model")
        if model:
            out[name] = model
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fold the event log
# ─────────────────────────────────────────────────────────────────────────────

def fold_events_by_agent(events_path: Path) -> dict[str, dict]:
    """Aggregate ``events.log.jsonl`` per agent. Tolerant line-by-line parse,
    mirroring ``engine._total_cost`` (the event log is the source of truth)."""
    roll: dict[str, dict] = {}
    events_path = Path(events_path)
    if not events_path.exists():
        return roll
    for line in events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        agent = ev.get("agent") or "unknown"
        r = roll.setdefault(agent, {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "duration_ms": 0.0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "event_count": 0, "events_with_metrics": 0,
        })
        r["event_count"] += 1
        m = ev.get("metrics")
        if isinstance(m, dict) and any(m.get(k) is not None for k in _METRIC_KEYS):
            r["events_with_metrics"] += 1
            # Defensive coercion: a malformed metric value (e.g. cost_usd:"n/a") must not
            # crash report generation — treat a non-numeric field as 0.
            inp = _num(m.get("input_tokens"), int)
            out = _num(m.get("output_tokens"), int)
            r["input_tokens"] += inp
            r["output_tokens"] += out
            r["total_tokens"] += _num(m.get("total_tokens"), int) or (inp + out)
            r["cost_usd"] += _num(m.get("cost_usd"), float)
            r["duration_ms"] += _num(m.get("duration_ms"), float)
            # Prompt-cache breakdown (optional, absent on older runs → folds as 0).
            r["cache_read_input_tokens"] += _num(m.get("cache_read_input_tokens"), int)
            r["cache_creation_input_tokens"] += _num(m.get("cache_creation_input_tokens"), int)
    return roll


def compute_parallelism(events_path: Path) -> Optional[dict]:
    """Measure realized concurrency from the event log (scorecard stretch).

    Each completion event carries a ``timestamp`` (when it finished) and
    ``metrics.duration_ms`` (how long it ran), so its start ≈ end − duration.
    Within a stage, tasks that overlap in time ran in parallel. We measure the
    **union** of the task intervals (merged busy time) vs the serial sum of
    durations: overlapping tasks shrink the union (speedup > 1), while tasks
    separated by idle gaps — re-runs, rework rounds hours apart, human-gate
    waits — stay disjoint and count as serial (speedup ≈ 1), so gaps never make
    the pipeline look *slower* than serial. Returns None when no timed events
    exist."""
    events_path = Path(events_path)
    if not events_path.exists():
        return None
    by_stage: dict[str, list[tuple[float, float, float]]] = {}
    for line in events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        m = ev.get("metrics")
        if not isinstance(m, dict):
            continue
        dur = m.get("duration_ms")
        ts = ev.get("timestamp")
        if dur is None or not ts:
            continue
        try:
            end = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000.0
            dur = float(dur)
        except (ValueError, AttributeError, TypeError):
            continue
        by_stage.setdefault(ev.get("stage") or "?", []).append((end - dur, end, dur))

    if not by_stage:
        return None

    def _union_ms(intervals: list[tuple[float, float, float]]) -> float:
        """Total length covered by the [start, end] intervals, merging overlaps."""
        covered = 0.0
        cur_s = cur_e = None
        for s, e, _ in sorted(intervals, key=lambda x: x[0]):
            if cur_e is None or s > cur_e:           # disjoint → close prior run
                if cur_e is not None:
                    covered += cur_e - cur_s
                cur_s, cur_e = s, e
            else:                                    # overlap → extend
                cur_e = max(cur_e, e)
        if cur_e is not None:
            covered += cur_e - cur_s
        return covered

    waves: list[dict] = []
    serial_total = 0.0
    wallclock_total = 0.0
    for stage, items in by_stage.items():
        serial = sum(d for _, _, d in items)
        busy = _union_ms(items)
        serial_total += serial
        wallclock_total += busy
        speedup = round(serial / busy, 3) if busy > 0 else None
        # Only surface stages with genuine overlap (concurrent tasks), not stages
        # that merely ran twice across the run.
        if len(items) >= 2 and speedup and speedup > 1.01:
            waves.append({
                "stage": stage,
                "tasks": len(items),
                "serial_agent_ms": round(serial, 3),
                "wallclock_ms": round(busy, 3),
                "speedup": speedup,
            })

    return {
        "serial_agent_ms": round(serial_total, 3),
        "wallclock_ms": round(wallclock_total, 3),
        "speedup": round(serial_total / wallclock_total, 3) if wallclock_total > 0 else None,
        "waves": sorted(waves, key=lambda w: -(w["speedup"] or 0)),
    }


def _coverage(r: dict) -> str:
    if r["events_with_metrics"] == 0:
        return "none"
    if r["events_with_metrics"] < r["event_count"]:
        return "partial"
    return "full"


# ─────────────────────────────────────────────────────────────────────────────
# Build / render / write
# ─────────────────────────────────────────────────────────────────────────────

def build_cost_report(project_root: Path, repo_root: Optional[Path] = None) -> dict:
    """Construct the cost-report dict from a project's event log."""
    repo_root = Path(repo_root) if repo_root else _REPO_ROOT
    project_root = Path(project_root)
    events_path = project_root / "artifacts" / "events.log.jsonl"
    models = agent_model_map(repo_root)
    folded = fold_events_by_agent(events_path)

    wf_id = None
    state_path = project_root / "artifacts" / "workflow_state.json"
    if state_path.exists():
        try:
            wf_id = json.loads(state_path.read_text()).get("workflow_id")
        except (json.JSONDecodeError, OSError):
            wf_id = None

    by_role: dict[str, dict] = {}
    notes: list[str] = []
    totals = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
              "total_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_savings_usd": 0.0,
              "duration_ms": 0.0, "event_count": 0}

    for agent, r in sorted(folded.items(), key=lambda kv: -kv[1]["cost_usd"]):
        cov = _coverage(r)
        model = models.get(agent)
        cache_read = r.get("cache_read_input_tokens", 0)
        cache_creation = r.get("cache_creation_input_tokens", 0)
        savings = _cache_savings_usd(model, cache_read, cache_creation)
        by_role[agent] = {
            "model": model,
            "total_cost_usd": round(r["cost_usd"], 6),
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_tokens": r["total_tokens"],
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "cache_savings_usd": savings,
            "total_duration_ms": round(r["duration_ms"], 3),
            "event_count": r["event_count"],
            "events_with_metrics": r["events_with_metrics"],
            "coverage": cov,
        }
        for k in ("cost_usd", "input_tokens", "output_tokens", "total_tokens",
                  "cache_read_input_tokens", "cache_creation_input_tokens",
                  "duration_ms", "event_count"):
            totals[k] += r.get(k, 0)
        totals["cache_savings_usd"] += savings
        if cov == "none" and agent in _NON_LLM_NOTE:
            notes.append(f"{agent}: {_NON_LLM_NOTE[agent]}")

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["cache_savings_usd"] = round(totals["cache_savings_usd"], 6)
    totals["duration_ms"] = round(totals["duration_ms"], 3)

    report = {
        "workflow_id": wf_id,
        "report_generated_at": datetime.now(timezone.utc).isoformat(),
        "by_agent_role": by_role,
        "totals": totals,
        "notes": notes,
    }
    parallelism = compute_parallelism(events_path)
    if parallelism:
        report["parallelism"] = parallelism
    return report


def render_markdown(report: dict) -> str:
    """Human-readable per-role table + totals."""
    t = report["totals"]
    lines = ["# Cost & Efficiency Report", ""]
    if report.get("workflow_id"):
        lines.append(f"- **workflow_id:** `{report['workflow_id']}`")
    lines.append(f"- **generated:** {report['report_generated_at']}")
    lines.append(
        f"- **total cost:** ${t['cost_usd']:.4f} · "
        f"**tokens:** {t['total_tokens']:,} · "
        f"**agent time:** {t['duration_ms'] / 1000:.0f}s"
    )
    cache_read = t.get("cache_read_input_tokens", 0)
    if cache_read:
        lines.append(
            f"- **prompt cache:** {cache_read:,} tok read"
            f" · saved ≈ ${t.get('cache_savings_usd', 0.0):.4f}"
        )
    par = report.get("parallelism")
    if par and par.get("speedup"):
        lines.append(
            f"- **parallelism:** {par['speedup']:.2f}× "
            f"({par['serial_agent_ms'] / 1000:.0f}s agent-time in "
            f"{par['wallclock_ms'] / 1000:.0f}s wall-clock)"
        )
    lines += [
        "",
        "| Agent role | Model | Runs | In tok | Out tok | Cost $ | Cache saved $ | Time | Coverage |",
        "|---|---|--:|--:|--:|--:|--:|--:|---|",
    ]
    for role, r in report["by_agent_role"].items():
        lines.append(
            f"| {role} | {r['model'] or '—'} | {r['event_count']} | "
            f"{r['input_tokens']:,} | {r['output_tokens']:,} | "
            f"{r['total_cost_usd']:.3f} | {r.get('cache_savings_usd', 0.0):.4f} | "
            f"{r['total_duration_ms'] / 1000:.0f}s | {r['coverage']} |"
        )
    lines.append(
        f"| **TOTAL** | | {t['event_count']} | {t['input_tokens']:,} | "
        f"{t['output_tokens']:,} | {t['cost_usd']:.3f} | "
        f"{t.get('cache_savings_usd', 0.0):.4f} | {t['duration_ms'] / 1000:.0f}s | |"
    )
    if par and par.get("waves"):
        lines += ["", "**Parallel waves** (concurrent tasks within a stage)", ""]
        lines += ["| Stage | Tasks | Agent-time | Wall-clock | Speedup |",
                  "|---|--:|--:|--:|--:|"]
        for w in par["waves"]:
            sp = f"{w['speedup']:.2f}×" if w.get("speedup") else "—"
            lines.append(
                f"| {w['stage']} | {w['tasks']} | "
                f"{w['serial_agent_ms'] / 1000:.0f}s | "
                f"{w['wallclock_ms'] / 1000:.0f}s | {sp} |"
            )
    if report.get("notes"):
        lines += ["", "**Notes**"]
        lines += [f"- {n}" for n in report["notes"]]
    lines.append("")
    return "\n".join(lines)


def _validate(report: dict) -> None:
    """Validate against schemas/cost_report.schema.json. Skips gracefully if the
    schema file or jsonschema is unavailable; raises on a genuine mismatch."""
    try:
        import jsonschema
    except ImportError:
        logger.warning("jsonschema not installed — skipping cost_report validation")
        return
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
    except OSError:
        logger.warning("cost_report schema not found at %s — skipping validation", _SCHEMA_PATH)
        return
    jsonschema.validate(report, schema)


def write_cost_report(project_root: Path, repo_root: Optional[Path] = None) -> dict:
    """Build, validate, and write ``cost_report.json`` + ``cost_report.md`` into the
    project's ``artifacts/`` directory. Returns the report dict."""
    report = build_cost_report(project_root, repo_root)
    _validate(report)
    artifacts = Path(project_root) / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "cost_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (artifacts / "cost_report.md").write_text(render_markdown(report))
    return report
