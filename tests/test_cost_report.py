"""Tests for the cost/efficiency report generator (src/orchestrator/cost_reporter.py).

Deterministic — builds a synthetic event log + stub agent definitions in a tmp dir.
No LLM, no network. Covers the scorecard §7.1 auto-collected report.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from orchestrator.cost_reporter import (
    agent_model_map,
    build_cost_report,
    compute_parallelism,
    fold_events_by_agent,
    render_markdown,
    write_cost_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _event(agent, status="success", metrics=None):
    ev = {"event_id": "x", "workflow_id": "wf", "stage": "s", "agent": agent,
          "status": status, "timestamp": "2026-06-28T00:00:00Z"}
    if metrics is not None:
        ev["metrics"] = metrics
    return ev


def _write_events(tmp_path: Path, events) -> Path:
    proj = tmp_path / "proj"
    (proj / "artifacts").mkdir(parents=True)
    log = proj / "artifacts" / "events.log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return proj


M1 = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
      "cost_usd": 0.50, "duration_ms": 1000}
M2 = {"input_tokens": 300, "output_tokens": 40, "total_tokens": 340,
      "cost_usd": 1.50, "duration_ms": 3000}


def test_fold_events_by_agent_aggregates_and_marks_coverage(tmp_path):
    proj = _write_events(tmp_path, [
        _event("developer-agent", metrics=M1),
        _event("developer-agent", status="retry"),          # no metrics → partial
        _event("architect-agent", metrics=M2),              # full
        _event("devops-agent"),                             # none
    ])
    folded = fold_events_by_agent(proj / "artifacts" / "events.log.jsonl")
    assert folded["developer-agent"]["cost_usd"] == 0.50
    assert folded["developer-agent"]["event_count"] == 2
    assert folded["developer-agent"]["events_with_metrics"] == 1
    assert folded["architect-agent"]["total_tokens"] == 340
    assert folded["devops-agent"]["events_with_metrics"] == 0


def test_agent_model_map_parses_frontmatter(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.agent.md").write_text("---\nname: developer-agent\nmodel: sonnet\n---\nbody")
    (agents / "reviewer.agent.md").write_text("---\nmodel: opus\n---\nbody")
    (agents / "nomodel.agent.md").write_text("---\nname: x\n---\nbody")
    m = agent_model_map(tmp_path)
    assert m["developer-agent"] == "sonnet"
    assert m["reviewer-agent"] == "opus"
    assert "nomodel-agent" not in m  # no model line → omitted


def test_build_cost_report_totals_and_coverage(tmp_path):
    proj = _write_events(tmp_path, [
        _event("developer-agent", metrics=M1),
        _event("developer-agent", status="retry"),
        _event("architect-agent", metrics=M2),
        _event("devops-agent"),
    ])
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.agent.md").write_text("---\nmodel: sonnet\n---\n")
    (agents / "architect.agent.md").write_text("---\nmodel: opus\n---\n")
    (agents / "devops.agent.md").write_text("---\nmodel: haiku\n---\n")

    rep = build_cost_report(proj, repo_root=tmp_path)
    assert rep["totals"]["cost_usd"] == 2.0           # 0.5 + 1.5
    assert rep["totals"]["event_count"] == 4
    assert rep["by_agent_role"]["developer-agent"]["model"] == "sonnet"
    assert rep["by_agent_role"]["developer-agent"]["coverage"] == "partial"
    assert rep["by_agent_role"]["architect-agent"]["coverage"] == "full"
    assert rep["by_agent_role"]["devops-agent"]["coverage"] == "none"
    # devops has no metrics → a clarifying note is emitted, not treated as a gap.
    assert any("devops-agent" in n for n in rep["notes"])
    # ordering: highest cost first (architect 1.5 > developer 0.5)
    assert list(rep["by_agent_role"])[0] == "architect-agent"


def test_cost_report_validates_against_schema(tmp_path):
    proj = _write_events(tmp_path, [_event("developer-agent", metrics=M1)])
    rep = build_cost_report(proj, repo_root=tmp_path)
    schema = json.loads((REPO_ROOT / "schemas" / "cost_report.schema.json").read_text())
    jsonschema.validate(rep, schema)  # raises on mismatch


def test_write_cost_report_emits_json_and_md(tmp_path):
    proj = _write_events(tmp_path, [_event("developer-agent", metrics=M1)])
    rep = write_cost_report(proj, repo_root=tmp_path)
    assert (proj / "artifacts" / "cost_report.json").exists()
    md = (proj / "artifacts" / "cost_report.md").read_text()
    assert "Cost & Efficiency Report" in md
    assert "developer-agent" in md
    assert rep["totals"]["cost_usd"] == 0.50


def test_malformed_metric_value_does_not_crash(tmp_path):
    """A non-numeric metric value (e.g. cost_usd:'n/a') is coerced to 0, not a crash."""
    proj = _write_events(tmp_path, [
        _event("developer-agent", metrics={"input_tokens": "unknown", "output_tokens": 5,
                                           "cost_usd": "n/a", "duration_ms": None}),
    ])
    folded = fold_events_by_agent(proj / "artifacts" / "events.log.jsonl")
    r = folded["developer-agent"]
    assert r["input_tokens"] == 0      # "unknown" → 0
    assert r["output_tokens"] == 5
    assert r["cost_usd"] == 0.0        # "n/a" → 0.0
    assert r["events_with_metrics"] == 1


def test_cache_savings_computed_per_model(tmp_path):
    """cache_read tokens save ~0.9x input price; the role's model sets the rate."""
    # sonnet input = $3/MTok. 1,000,000 cache-read tok, no writes →
    # savings = 1e6 * 0.9 * (3/1e6) = $2.70
    proj = _write_events(tmp_path, [
        _event("developer-agent", metrics={
            "input_tokens": 1_000_000, "output_tokens": 10, "total_tokens": 1_000_010,
            "cost_usd": 0.30, "duration_ms": 1000,
            "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 0,
        }),
    ])
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.agent.md").write_text("---\nmodel: sonnet\n---\n")
    rep = build_cost_report(proj, repo_root=tmp_path)
    role = rep["by_agent_role"]["developer-agent"]
    assert role["cache_read_input_tokens"] == 1_000_000
    assert abs(role["cache_savings_usd"] - 2.70) < 1e-6
    assert abs(rep["totals"]["cache_savings_usd"] - 2.70) < 1e-6
    # write premium reduces savings: 1M reads + 1M writes → 0.9*3 - 0.25*3 = $1.95
    assert "prompt cache" in render_markdown(rep)


def test_cache_savings_zero_without_known_model(tmp_path):
    proj = _write_events(tmp_path, [
        _event("mystery-agent", metrics={
            "input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
            "cost_usd": 0.1, "duration_ms": 10, "cache_read_input_tokens": 5000,
        }),
    ])
    rep = build_cost_report(proj, repo_root=tmp_path)  # no agent .md → no model
    assert rep["by_agent_role"]["mystery-agent"]["cache_savings_usd"] == 0.0


def test_parallelism_detects_overlapping_tasks(tmp_path):
    """Two tasks in one stage that overlap in time → speedup > 1."""
    # Both ran ~10s; T-02 ends at :12, T-03 ends at :13 → starts at :02 and :03.
    # span = 13 - 2 = 11s; serial = 20s; speedup ≈ 1.8x.
    proj = _write_events(tmp_path, [
        {"event_id": "1", "workflow_id": "wf", "stage": "planning_architecture",
         "agent": "architect-agent", "status": "success",
         "timestamp": "2026-06-28T00:00:12Z",
         "metrics": {"duration_ms": 10000, "cost_usd": 1.0}},
        {"event_id": "2", "workflow_id": "wf", "stage": "planning_architecture",
         "agent": "architect-agent", "status": "success",
         "timestamp": "2026-06-28T00:00:13Z",
         "metrics": {"duration_ms": 10000, "cost_usd": 1.0}},
    ])
    par = compute_parallelism(proj / "artifacts" / "events.log.jsonl")
    assert par is not None
    assert par["serial_agent_ms"] == 20000
    assert par["wallclock_ms"] == 11000
    assert par["speedup"] > 1.5
    assert par["waves"][0]["stage"] == "planning_architecture"
    rep = build_cost_report(proj, repo_root=tmp_path)
    assert rep["parallelism"]["speedup"] > 1.5
    assert "parallelism" in render_markdown(rep)


def test_parallelism_none_without_timed_events(tmp_path):
    proj = _write_events(tmp_path, [_event("developer-agent")])  # no metrics
    assert compute_parallelism(proj / "artifacts" / "events.log.jsonl") is None


def test_empty_log_is_safe(tmp_path):
    proj = tmp_path / "proj"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "events.log.jsonl").write_text("")
    rep = build_cost_report(proj, repo_root=tmp_path)
    assert rep["by_agent_role"] == {}
    assert rep["totals"]["cost_usd"] == 0.0
    render_markdown(rep)  # must not raise on empty
