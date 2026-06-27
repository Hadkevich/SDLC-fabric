"""Command-line entrypoint for the Agentic SDLC engine.

    # Start a brand-new workflow from a raw request (runs product → … → deploy):
    python -m orchestrator projects/todo-app --prompt "Build a CLI todo app" --yes

    # Resume an existing workflow (re-reads workflow_state.json, picks up where it
    # paused or failed); approve the next human checkpoint(s):
    python -m orchestrator projects/todo-app --approve architecture

    # Validate an already-produced run without invoking any agent (no LLM/cost):
    python -m orchestrator projects/smart-expense-tracker --replay

Exit codes: 0 = complete, 1 = failed, 2 = paused on a human checkpoint.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import Orchestrator, DEFAULT_SCHEMAS_DIR, PRELUDE_TASKS, HUMAN_GATES
from .runners import ClaudeAgentRunner, ReplayRunner

PRELUDE_IDS = {t["task_id"] for t in PRELUDE_TASKS}

REPO_ROOT = Path(__file__).resolve().parents[2]

# All human checkpoints (SPEC §8.6); --yes approves all of them.
ALL_APPROVALS = {"requirements", "architecture", "production_deploy"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m orchestrator",
        description="Drive the Agentic SDLC pipeline for a project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("project", help="path to the project dir (e.g. projects/todo-app)")
    p.add_argument("--prompt", help="raw request — runs the full prelude (product → "
                                    "planner → architect) before the DAG. Omit to "
                                    "resume from an existing workplan.json.")
    p.add_argument("--approve", default="",
                   help="comma-separated checkpoints to approve "
                        "(requirements,architecture,production_deploy)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="auto-approve every human checkpoint (unattended run)")
    p.add_argument("--replay", action="store_true",
                   help="validate existing artifacts without invoking any agent (no LLM)")
    p.add_argument("--retry", default="",
                   help="comma-separated task ids to reset to pending and re-run "
                        "(clears the halt flag). E.g. --retry TASK-002")
    p.add_argument("--retry-failed", action="store_true",
                   help="reset ALL blocked tasks and resume (recover after a failure)")
    p.add_argument("--max-retries", type=int, default=3,
                   help="retries per task before escalating (default 3)")
    p.add_argument("--max-rework", type=int, default=2,
                   help="review->fix rework rounds before a rejected review "
                        "escalates (default 2)")
    p.add_argument("--max-cost-usd", type=float, default=None,
                   help="run-level cost ceiling in USD; the breaker halts new "
                        "dispatch once cumulative agent cost reaches it (default: none)")
    p.add_argument("--feedback-loop", type=int, default=0, metavar="N",
                   help="enable the monitoring_feedback loop (SPEC §3.9): an unhealthy "
                        "deploy drives a Level-1 in-run health rework, then up to N "
                        "Level-2 cross-run re-plans (product folds backlog.json into "
                        "updated requirements), then escalates. 0 (default) keeps the "
                        "one-shot signal. Each re-deploy still honours the "
                        "production_deploy checkpoint — pass --approve "
                        "requirements,architecture (not production_deploy) to keep the "
                        "loop automatic but gate every deploy, or --yes for full auto.")
    p.add_argument("--json", action="store_true",
                   help="print the final workflow_state as JSON to stdout (for CI / "
                        "machine consumption) instead of the human summary")
    p.add_argument("--max-parallel", type=int, default=4,
                   help="independent DAG tasks to run concurrently (default 4); "
                        "1 forces sequential execution")
    p.add_argument("--permission-mode", default="acceptEdits",
                   help="agent permission mode; use 'bypassPermissions' for "
                        "fully unattended runs (default acceptEdits)")
    p.add_argument("--model", help="override the model for all agents (e.g. sonnet, opus)")
    p.add_argument("--schemas-dir", default=str(DEFAULT_SCHEMAS_DIR),
                   help="directory of JSON schemas (default: repo schemas/)")
    p.add_argument("--cost-report", action="store_true",
                   help="fold the project's events.log.jsonl into a per-agent-role "
                        "cost/efficiency report (artifacts/cost_report.{json,md}) and "
                        "exit — no agent is invoked (no LLM/cost)")
    return p


def _print_summary(state: dict, project: Path) -> None:
    stage = state.get("current_stage", "?")
    icon = {"complete": "✅", "failed": "❌"}.get(stage, "⏸")
    print(f"\n{icon}  workflow {state.get('workflow_id', '?')} → {stage}")
    print(f"   artifacts: {project / 'artifacts'}")
    print(f"   events:    {project / 'artifacts' / 'events.log.jsonl'}")

    # Monitoring feedback loop (SPEC §3.9): summarise backlog status + re-plan cycles.
    # The round-by-round detail lives in events.log.jsonl (the audit source of truth).
    backlog_path = project / "artifacts" / "backlog.json"
    if backlog_path.exists():
        try:
            items = json.loads(backlog_path.read_text())
            counts: dict = {}
            for e in items if isinstance(items, list) else []:
                counts[e.get("status")] = counts.get(e.get("status"), 0) + 1
            if counts:
                summary = ", ".join(f"{n} {s}" for s, n in counts.items())
                cyc = (f", {state['feedback_cycle']} re-plan cycle(s)"
                       if state.get("feedback_cycle") else "")
                print(f"   backlog:   {backlog_path}  ({summary}{cyc})")
        except (json.JSONDecodeError, OSError):
            pass

    tasks = state.get("tasks", {})
    if tasks:
        print("\n   tasks:")
        mark = {"success": "✓", "blocked": "✗", "awaiting_approval": "⏸",
                "in_progress": "…", "pending": "·"}
        for tid, t in tasks.items():
            m = mark.get(t.get("status"), "?")
            line = f"     {m} {tid:<22} {t.get('status')}"
            if t.get("attempt"):
                line += f"  (attempts: {t['attempt']})"
            print(line)
            for issue in t.get("blocking_issues", []):
                print(f"         ↳ {issue}")

    if stage == "awaiting_approval" or icon == "⏸":
        waiting = [(tid, t) for tid, t in tasks.items()
                   if t.get("status") == "awaiting_approval"]
        if waiting:
            names = ", ".join(tid for tid, _ in waiting)
            # map each waiting task's stage → the named human checkpoint it needs
            gates = sorted({HUMAN_GATES.get(t.get("stage")) for _, t in waiting} - {None})
            print(f"\n   ⏸ paused for human sign-off before: {names}")
            rel = project if not project.is_absolute() else project.name
            if gates:
                glist = ",".join(gates)
                which = (f"the '{gates[0]}' checkpoint" if len(gates) == 1
                         else f"checkpoints: {glist}")
                print(f"   this is {which} — review the artifacts above, then continue with:")
                print(f"     python -m orchestrator {rel} --approve {glist}")
            print(f"   or run the rest unattended:")
            print(f"     python -m orchestrator {rel} --yes")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    project = Path(args.project).resolve()
    if not project.exists():
        if args.prompt:
            # a new workflow: create the project scaffold (engine fills artifacts/)
            (project / "artifacts").mkdir(parents=True, exist_ok=True)
            print(f"created new project: {project}")
        else:
            print(f"error: project path does not exist: {project}\n"
                  f"       pass --prompt \"...\" to start a new workflow there.",
                  file=sys.stderr)
            return 1

    # Read-only observability: fold the event log into a cost/efficiency report and
    # exit (mirrors --replay in that no agent/runner is constructed).
    if args.cost_report:
        from .cost_reporter import write_cost_report
        report = write_cost_report(project, REPO_ROOT)
        t = report["totals"]
        print(f"cost report → {project / 'artifacts' / 'cost_report.json'} "
              f"(+ .md)\n  total ${t['cost_usd']:.4f} · {t['total_tokens']:,} tokens · "
              f"{t['duration_ms'] / 1000:.0f}s across {len(report['by_agent_role'])} roles")
        return 0

    approvals = ALL_APPROVALS if args.yes else {
        a.strip() for a in args.approve.split(",") if a.strip()
    }

    if args.replay:
        runner = ReplayRunner()
    else:
        runner = ClaudeAgentRunner(
            permission_mode=args.permission_mode,
            add_dirs=[str(REPO_ROOT)],  # so agents can read schemas/ + SPEC.md
            model=args.model,
        )

    orch = Orchestrator(
        project, runner,
        auto_approve=args.yes,
        approvals=approvals,
        schemas_dir=Path(args.schemas_dir),
        max_retries=args.max_retries,
        max_rework=args.max_rework,
        max_parallel=args.max_parallel,
        max_cost_usd=args.max_cost_usd,
        max_feedback_cycles=args.feedback_loop,
    )

    state_file = project / "artifacts" / "workflow_state.json"
    workplan_file = project / "artifacts" / "workplan.json"

    # operator recovery: reset blocked task(s) + clear the halt before dispatching
    if args.retry or args.retry_failed:
        targets = [t.strip() for t in args.retry.split(",") if t.strip()] or None
        n = orch.unblock(targets)
        print(f"reset {n} task(s) for retry" if n else
              "nothing to reset (no matching blocked tasks)")

    if args.prompt:
        # start (or restart) a prompt-driven workflow: full prelude → DAG
        state = orch.run_from_prompt(args.prompt)
    elif state_file.exists():
        # resume an existing run from workflow_state.json (single source of truth)
        prior = json.loads(state_file.read_text())
        prior_tasks = prior.get("tasks", {})
        if PRELUDE_IDS & prior_tasks.keys():
            # this was a prompt-driven workflow — it may still be mid-prelude (e.g.
            # paused at the requirements sign-off, before workplan.json exists).
            # run_from_prompt resumes it: finished prelude stages are skipped, so
            # the original prompt isn't needed again.
            if prior_tasks.get("STAGE-REQUIREMENTS", {}).get("status") != "success":
                print("error: requirements stage hasn't completed — re-run with "
                      "--prompt \"...\" to (re)start this workflow.", file=sys.stderr)
                return 1
            state = orch.run_from_prompt("")
        elif workplan_file.exists():
            state = orch.run()  # DAG-only workflow (hand-authored / pre-existing workplan)
        else:
            print("error: workflow_state.json has no tasks and no workplan.json — "
                  "pass --prompt to start a new workflow.", file=sys.stderr)
            return 1
    elif workplan_file.exists():
        # no prior state, but a workplan is present (hand-authored or --replay)
        state = orch.run()
    else:
        print("error: nothing to run — pass --prompt \"...\" to start a new "
              "workflow, or point at a project that already has a workplan.json.",
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(state, indent=2))
    else:
        _print_summary(state, project)
    stage = state.get("current_stage")
    return 0 if stage == "complete" else 1 if stage == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
