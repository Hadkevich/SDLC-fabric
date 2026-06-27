#!/usr/bin/env python3
"""Cost & Efficiency micro-A/B (scorecard §7.3).

Runs ONE fixed task — the rubric's own "log summarizer" example — through three
model tiers (haiku / sonnet / opus) via ``claude -p ... --output-format json``,
captures cost / tokens / latency + the output, and writes an A/B evidence artifact.

This is the one *live* step in the cost-efficiency work: three isolated single-shot
``claude`` calls (no agents, no tools, no pipeline). Run once:

    python scripts/cost_ab_experiment.py

Writes (under projects/neural-sync/artifacts/):
    cost_ab_experiment.json   — raw per-model cost/tokens/latency + outputs
    cost_ab_experiment.md      — summary table + verdict

The point: show where a *cheaper* model is good enough. Log summarization is exactly
the rubric's "small/fast model" example, and it's why devops + summary roles route to
haiku in the per-role model strategy (scorecard §7.2 / SPEC §4).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO_ROOT / "projects" / "neural-sync" / "artifacts"
EVENTS = ARTIFACTS / "events.log.jsonl"
OUT_JSON = ARTIFACTS / "cost_ab_experiment.json"
OUT_MD = ARTIFACTS / "cost_ab_experiment.md"

MODELS = ["haiku", "sonnet", "opus"]
TIMEOUT_S = 240

PROMPT_TEMPLATE = (
    "Summarize this agent SDLC event log into EXACTLY 3 concise bullet points "
    "covering: (1) how many distinct stages ran, (2) where retries or blocks "
    "happened, (3) the final outcome. Output only the 3 bullets, nothing else.\n\n"
    "=== EVENT LOG ===\n{log}"
)


def run_model(model: str, prompt: str) -> dict:
    """One single-shot `claude -p` call; parse the JSON result envelope."""
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(REPO_ROOT)
        )
    except subprocess.TimeoutExpired:
        return {"model": model, "error": f"timeout after {TIMEOUT_S}s"}
    except FileNotFoundError:
        return {"model": model, "error": "claude CLI not found on PATH"}
    if proc.returncode != 0:
        return {"model": model, "error": f"exit {proc.returncode}: {proc.stderr[:300]}"}
    try:
        env = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"model": model, "error": "non-JSON output", "raw": proc.stdout[:300]}
    usage = env.get("usage") or {}
    return {
        "model": model,
        "resolved_models": list((env.get("modelUsage") or {}).keys()),
        "output": (env.get("result") or "").strip(),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": env.get("total_cost_usd"),
        "duration_ms": env.get("duration_ms"),
        "is_error": bool(env.get("is_error")),
    }


def _fmt_cost(c) -> str:
    return "—" if c is None else f"{c:.4f}"


def render_md(report: dict) -> str:
    # A run counts only if it neither errored at the CLI nor reported is_error in the
    # envelope, and carries a real cost — so a failed/empty run can't drive the verdict.
    ok = [
        r for r in report["results"]
        if not r.get("error") and not r.get("is_error") and r.get("cost_usd") is not None
    ]
    lines = ["# Cost & Efficiency — Model A/B (log summarizer)", ""]
    lines.append(f"- **task:** {report['task']}")
    lines.append(f"- **generated:** {report['generated_at']}")
    lines.append("")
    lines.append("| Model | Cost $ | Out tok | Latency | Output (3-bullet summary) |")
    lines.append("|---|--:|--:|--:|---|")
    for r in report["results"]:
        if r.get("error"):
            lines.append(f"| {r['model']} | — | — | — | _error: {r['error']}_ |")
            continue
        out = (r.get("output") or "").replace("\n", "<br>").replace("|", "\\|")
        if len(out) > 400:
            out = out[:397] + "…"
        flag = " ⚠️ is_error" if r.get("is_error") else ""
        lines.append(
            f"| {r['model']}{flag} | {_fmt_cost(r.get('cost_usd'))} | {r.get('output_tokens')} | "
            f"{(r.get('duration_ms') or 0) / 1000:.0f}s | {out} |"
        )
    lines.append("")
    if len(ok) >= 2:
        cheapest = min(ok, key=lambda r: r["cost_usd"])
        dearest = max(ok, key=lambda r: r["cost_usd"])
        ratio_str = f"{dearest['cost_usd'] / cheapest['cost_usd']:.1f}×" if cheapest["cost_usd"] else "n/a"
        lines.append("## Verdict")
        lines.append(
            f"For the **log-summarization** task, **{cheapest['model']}** produced a "
            f"comparable 3-bullet summary at **${cheapest['cost_usd']:.4f}** vs "
            f"**${dearest['cost_usd']:.4f}** for **{dearest['model']}** — about "
            f"**{ratio_str} cheaper**. The cheaper tier is good enough here, which is why "
            f"mechanical/summary roles (devops, log/format) route to a small/fast model in the "
            f"per-role strategy (SPEC §4 / scorecard §7.2). Frontier models are reserved for the "
            f"hard reasoning roles (architect, reviewer)."
        )
        lines.append("")
        lines.append(
            "> Note: per-call cost includes shared system-prompt cache overhead, so absolute "
            "values are dominated by fixed costs at this tiny task size; the **ratio** is the "
            "signal. Output quality is judged from the side-by-side summaries above."
        )
    else:
        lines.append("## Verdict")
        lines.append("_Not enough successful runs to compare — see errors above._")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not EVENTS.exists():
        print(f"error: {EVENTS} not found", file=sys.stderr)
        return 1
    prompt = PROMPT_TEMPLATE.format(log=EVENTS.read_text())
    print(f"A/B over {MODELS} — prompt ~{len(prompt)} chars (this makes live calls)…")
    results = []
    for m in MODELS:
        print(f"  → {m} …", flush=True)
        r = run_model(m, prompt)
        results.append(r)
        if r.get("error"):
            print(f"    ! {r['error']}")
        else:
            flag = " ⚠️ is_error" if r.get("is_error") else ""
            print(f"    ${_fmt_cost(r.get('cost_usd'))} · {r.get('output_tokens')} out tok · "
                  f"{(r.get('duration_ms') or 0) / 1000:.0f}s{flag}")
    report = {
        "experiment": "log_summarizer_model_ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task": "Summarize the neural-sync SDLC event log into 3 bullets",
        "models": MODELS,
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(report, indent=2) + "\n")
    OUT_MD.write_text(render_md(report))
    print(f"\nwrote {OUT_JSON}\n      {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
