"""Agent-driven Git flow (scorecard stretch: PR open → review → merge by agents).

A deterministic post-deploy step — the SCM/release agent's mechanical work, the
same way devops deployment is mechanical. After a healthy run it ships the
generated project through a real pull request: branch → commit → push →
``gh pr create`` → post the reviewer agent's verdict as a PR comment → squash
merge. The PR review body is the *reviewer-agent's* `review_report.json` verdict,
so the human-readable review on GitHub is genuinely agent-authored.

Gated behind ``gh auth status``: with no authenticated GitHub CLI it is a clean
no-op (``status: "skipped"``), never a hard failure. Every remote step degrades
gracefully — a push/PR/merge failure is recorded in ``scm_report.json`` and the
rest of the run is unaffected (``status`` downgrades to ``pr_open`` / ``local_only``).

Used via ``python -m orchestrator <project> --git-flow`` (or ``--git-flow`` on a
normal run, which fires it after the pipeline completes).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


def _run(cmd: list[str], cwd: Path, *, timeout: int = 120) -> tuple[int, str]:
    """Run a command, returning (returncode, combined stdout+stderr stripped)."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return p.returncode, (p.stdout + p.stderr).strip()


def gh_available(cwd: Path) -> bool:
    """True when the GitHub CLI is installed and authenticated."""
    if _run(["gh", "--version"], cwd)[0] != 0:
        return False
    return _run(["gh", "auth", "status"], cwd)[0] == 0


def _current_branch(cwd: Path) -> Optional[str]:
    rc, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return out if rc == 0 else None


def _review_summary(project: Path) -> str:
    """Build a PR comment from the reviewer-agent's review_report.json."""
    rr = project / "artifacts" / "review_report.json"
    if not rr.exists():
        return "Automated agent review: no review_report.json found."
    try:
        d = json.loads(rr.read_text())
    except (json.JSONDecodeError, OSError):
        return "Automated agent review: review_report.json unreadable."
    verdict = d.get("verdict", "?")
    blocking = d.get("blocking_issues") or []
    nonblocking = d.get("non_blocking_issues") or []
    lines = [
        f"## 🤖 Agent review — verdict: **{verdict}**",
        "",
        f"Reviewer agent: `{d.get('reviewer', 'reviewer-agent')}` · "
        f"{len(blocking)} blocking · {len(nonblocking)} non-blocking issue(s).",
    ]
    for i in nonblocking[:5]:
        lines.append(f"- ({i.get('category', '?')}) {i.get('description', '')}")
    lines.append("")
    lines.append("_Opened, reviewed, and merged by the agentic SDLC pipeline._")
    return "\n".join(lines)


def agent_git_flow(
    project: Path,
    repo_root: Path,
    *,
    base: Optional[str] = None,
    merge: bool = True,
    title: Optional[str] = None,
) -> dict:
    """Ship the project through a real agent-driven PR. Returns a report dict
    (also written to ``artifacts/scm_report.json``)."""
    project = Path(project)
    repo_root = Path(repo_root)
    rel = project.relative_to(repo_root) if project.is_relative_to(repo_root) else project
    wf_id = "run"
    state_path = project / "artifacts" / "workflow_state.json"
    if state_path.exists():
        try:
            wf_id = (json.loads(state_path.read_text()).get("workflow_id") or "run")[:8]
        except (json.JSONDecodeError, OSError):
            pass

    report: dict = {
        "project": str(rel), "branch": None, "base": base, "pr_url": None,
        "merged": False, "status": "skipped", "steps": [],
    }

    def step(name: str, rc: int, out: str) -> bool:
        report["steps"].append({"step": name, "ok": rc == 0, "detail": out[:300]})
        return rc == 0

    if not gh_available(repo_root):
        report["steps"].append({"step": "gh_auth", "ok": False,
                                "detail": "gh not installed or not authenticated — skipping git flow"})
        _write(project, report)
        return report

    base = base or _current_branch(repo_root) or "main"
    report["base"] = base
    branch = f"agent/{rel.name}-{wf_id}"
    report["branch"] = branch
    title = title or f"feat({rel.name}): agent-built release {wf_id}"

    # 1. fresh branch off the base
    if not step("branch", *_run(["git", "checkout", "-B", branch], repo_root)):
        _write(project, report)
        return report

    # 2. stage + commit the shipped project (allow-empty so the PR is never blank
    #    when the generated files are already committed on the base).
    _run(["git", "add", "--", str(rel)], repo_root)
    body = _review_summary(project)
    commit_msg = f"{title}\n\nShipped by the agentic SDLC pipeline (workflow {wf_id}).\n"
    step("commit", *_run(["git", "commit", "--allow-empty", "-m", commit_msg], repo_root))
    report["status"] = "local_only"

    # 3. push
    if not step("push", *_run(["git", "push", "-u", "origin", branch, "--force-with-lease"], repo_root)):
        _restore(repo_root, base)
        _write(project, report)
        return report

    # 4. open PR
    rc, out = _run(["gh", "pr", "create", "--base", base, "--head", branch,
                    "--title", title, "--body", body], repo_root)
    step("pr_create", rc, out)
    if rc == 0:
        report["pr_url"] = out.splitlines()[-1].strip() if out else None
        report["status"] = "pr_open"
        # 5. post the agent review as a PR comment (explicit review trail)
        if report["pr_url"]:
            step("pr_review_comment",
                 *_run(["gh", "pr", "comment", report["pr_url"], "--body", body], repo_root))
            # 6. merge
            if merge:
                mrc, mout = _run(["gh", "pr", "merge", report["pr_url"],
                                  "--squash", "--delete-branch"], repo_root)
                if step("pr_merge", mrc, mout):
                    report["merged"] = True
                    report["status"] = "merged"

    _restore(repo_root, base)
    _write(project, report)
    return report


def _restore(repo_root: Path, base: str) -> None:
    """Return the working tree to the base branch (best-effort)."""
    _run(["git", "checkout", base], repo_root)


def _write(project: Path, report: dict) -> None:
    out = project / "artifacts" / "scm_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
