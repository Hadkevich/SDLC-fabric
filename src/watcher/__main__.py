"""CLI for the DB-backed Agentic SDLC engine.

    # Start a new pipeline (creates a tasks-table row; the watcher runs it):
    python -m watcher submit --prompt "Build a CLI todo app" --name todo

    # Run the watcher loop (polls every --tick seconds; --once for a single tick):
    python -m watcher run --yes

    # Approve a human checkpoint so a paused pipeline resumes:
    python -m watcher approve <pipeline_id> architecture

    # Inspect pipelines / tasks:
    python -m watcher status [<pipeline_id>]

All state lives in the SQLite DB (default: ./artifacts.db); project *code* files
land under <projects-root>/<pipeline-name>/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sdlcdb.db import Database
from orchestrator.lifecycle import ALL_GATES
from orchestrator.orchestrator import Orchestrator
from orchestrator.evaluator import Evaluator, make_llm_heal_fn
from orchestrator.runners import ClaudeAgentRunner
from .watcher import Watcher

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = "artifacts.db"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m watcher",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help="path to the SQLite DB")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="create a new pipeline")
    s.add_argument("--prompt", required=True, help="the raw user request")
    s.add_argument("--name", help="project dir name (projects/<name>)")
    s.add_argument("--yes", "-y", action="store_true",
                   help="auto-approve all human checkpoints for this pipeline")

    r = sub.add_parser("run", help="run the watcher poll loop")
    r.add_argument("--projects-root", default=str(REPO_ROOT / "projects"))
    r.add_argument("--tick", type=float, default=5.0, help="seconds between polls")
    r.add_argument("--once", action="store_true", help="run a single tick and exit")
    r.add_argument("--idle-exit", action="store_true",
                   help="exit when there is no more work (instead of polling forever)")
    r.add_argument("--developers", type=int, default=3,
                   help="global max concurrent developer-agent workers")
    r.add_argument("--default-n", type=int, default=1,
                   help="global max concurrent workers per other role")
    r.add_argument("--max-rework", type=int, default=2)
    r.add_argument("--max-heal", type=int, default=2)
    r.add_argument("--model", help="override the model for all agents")
    r.add_argument("--permission-mode", default="acceptEdits")

    a = sub.add_parser("approve", help="record a human sign-off")
    a.add_argument("pipeline_id")
    a.add_argument("gate", choices=sorted(ALL_GATES))

    st = sub.add_parser("status", help="show pipelines / tasks")
    st.add_argument("pipeline_id", nargs="?")
    st.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON (for CI / observability)")

    e = sub.add_parser("export", help="write JSON snapshots for the dashboard")
    e.add_argument("--out", default=str(REPO_ROOT / "observability" / "db"),
                   help="output directory (default: observability/db/)")
    return p


def _cmd_submit(db, args) -> int:
    orch = Orchestrator(db, auto_approve=args.yes)
    pid = orch.submit(args.prompt, name=args.name)
    print(f"submitted pipeline {pid} (name={args.name or '-'})")
    return 0


def _cmd_run(db, args) -> int:
    runner = ClaudeAgentRunner(permission_mode=args.permission_mode,
                               add_dirs=[str(REPO_ROOT)], model=args.model)
    heal_fn = make_llm_heal_fn(runner, Path(args.projects_root))
    orch = Orchestrator(db, max_rework=args.max_rework,
                        evaluator=Evaluator(db, max_heal=args.max_heal, heal_fn=heal_fn))
    watcher = Watcher(db, runner, args.projects_root,
                      concurrency={"developer-agent": args.developers},
                      default_n=args.default_n, tick=args.tick, orchestrator=orch)
    if args.once:
        print(watcher.tick_once())
    else:
        watcher.run(stop_when_idle=args.idle_exit)
    return 0


def _cmd_approve(db, args) -> int:
    orch = Orchestrator(db)
    n = orch.approve(args.pipeline_id, args.gate)
    print(f"approved '{args.gate}' for {args.pipeline_id} — released {n} task(s)")
    return 0


def _cmd_status(db, args) -> int:
    pipelines = ([db.get_pipeline(args.pipeline_id)] if args.pipeline_id
                 else db.list_pipelines())
    if args.json:
        import json
        out = []
        for p in pipelines:
            if not p:
                print("no such pipeline", file=sys.stderr)
                return 1
            entry = {**p, "cost_usd": db.total_cost(p["pipeline_id"])}
            if args.pipeline_id:
                entry["tasks"] = db.list_tasks(pipeline_id=p["pipeline_id"])
            out.append(entry)
        print(json.dumps(out, indent=2))
        return 0
    for p in pipelines:
        if not p:
            print("no such pipeline", file=sys.stderr)
            return 1
        icon = {"complete": "✅", "failed": "❌"}.get(p["status"], "⏳")
        print(f"{icon} {p['pipeline_id']}  {p['status']}  "
              f"(name={p['name'] or '-'}, cost=${db.total_cost(p['pipeline_id']):.4f})")
        if args.pipeline_id:
            for t in db.list_tasks(pipeline_id=p["pipeline_id"]):
                mark = {"done": "✓", "blocked": "✗", "error": "✗",
                        "awaiting_approval": "⏸"}.get(t["status"], "·")
                print(f"   {mark} {t['agent_role']:<16} {t['stage']:<22} {t['status']}")
    return 0


def _cmd_export(db, args) -> int:
    from .export import export_all
    n = export_all(db, args.out)
    print(f"exported {n} pipeline(s) -> {args.out}")
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        return {"submit": _cmd_submit, "run": _cmd_run, "approve": _cmd_approve,
                "status": _cmd_status, "export": _cmd_export}[args.cmd](db, args)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
