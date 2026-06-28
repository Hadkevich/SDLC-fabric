"""Tests for the agent-driven Git flow (src/orchestrator/scm.py).

No network: the real git branch/commit path runs in a local temp repo with no
remote, so the push step fails gracefully and the flow downgrades to local_only.
The gh-unauthenticated path is exercised by forcing gh_available False.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from orchestrator import scm


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    proj = repo / "projects" / "x"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "review_report.json").write_text(json.dumps({
        "verdict": "approved_with_comments", "reviewer": "reviewer-agent",
        "blocking_issues": [],
        "non_blocking_issues": [{"id": "NBI-1", "category": "maintainability",
                                 "description": "tidy the imports"}],
    }))
    (proj / "artifacts" / "workflow_state.json").write_text(
        json.dumps({"workflow_id": "abcd1234ef", "current_stage": "complete"}))
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo, proj


def test_review_summary_reads_reviewer_verdict(tmp_path):
    repo, proj = _repo(tmp_path)
    summary = scm._review_summary(proj)
    assert "approved_with_comments" in summary
    assert "reviewer-agent" in summary
    assert "tidy the imports" in summary


def test_git_flow_skips_without_gh(tmp_path, monkeypatch):
    repo, proj = _repo(tmp_path)
    monkeypatch.setattr(scm, "gh_available", lambda cwd: False)
    rep = scm.agent_git_flow(proj, repo)
    assert rep["status"] == "skipped"
    assert (proj / "artifacts" / "scm_report.json").exists()
    assert rep["merged"] is False


def test_git_flow_branches_and_commits_then_degrades(tmp_path, monkeypatch):
    """gh 'available' but no remote → real branch+commit, push fails → local_only,
    and the working tree is restored to the base branch afterwards."""
    repo, proj = _repo(tmp_path)
    monkeypatch.setattr(scm, "gh_available", lambda cwd: True)
    rep = scm.agent_git_flow(proj, repo)

    assert rep["branch"] == "agent/x-abcd1234"
    assert rep["base"] == "main"
    steps = {s["step"]: s["ok"] for s in rep["steps"]}
    assert steps.get("branch") is True
    assert steps.get("commit") is True
    assert steps.get("push") is False          # no remote → push fails, recorded
    assert rep["status"] == "local_only"
    # the agent branch was really created, and we're back on base
    branches = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                              cwd=str(repo), capture_output=True, text=True).stdout
    assert "agent/x-abcd1234" in branches
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         cwd=str(repo), capture_output=True, text=True).stdout.strip()
    assert cur == "main"
    report_on_disk = json.loads((proj / "artifacts" / "scm_report.json").read_text())
    assert report_on_disk["status"] == "local_only"
