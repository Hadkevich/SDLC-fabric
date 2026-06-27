"""Deterministic gate predicates for the DB-backed router.

Ported from the original engine's ``_review_gate`` / ``_deploy_gate`` but decoupled
from storage: each takes already-parsed artifact dicts and returns
``(kind, issues)``. ``kind`` ∈ {``ok``, ``rework``, ``block``, ``recoverable``}:

  * ``ok``          — advance.
  * ``rework``      — bounded review→fix / e2e→fix loop (reset developer subtree).
  * ``block``       — unrecoverable; dead-letter the pipeline.
  * ``recoverable`` — artifact missing/unreadable; let the task retry.

The router maps these onto task-table mutations; this module makes no DB calls and
embeds no judgment beyond the SPEC §7/§9 predicates.
"""
from __future__ import annotations

from .lifecycle import APPROVED_VERDICTS, E2E_PASS_VERDICTS


def review_gate(report: dict | None) -> tuple[str, list[str]]:
    """code_review (SPEC §7/§9): the verdict decides flow before QA/deploy."""
    if not isinstance(report, dict):
        return ("recoverable", ["review gate: review_report.json missing/unreadable"])
    verdict = report.get("verdict")
    if verdict == "rejected":
        issues = ["review gate: verdict is 'rejected'"]
        for bi in (report.get("blocking_issues") or [])[:10]:
            desc = (bi.get("description") or bi.get("title")) if isinstance(bi, dict) else str(bi)
            if desc:
                issues.append(f"blocking: {desc}")
        return ("rework", issues)
    if verdict not in APPROVED_VERDICTS:
        return ("recoverable", [f"review gate: verdict {verdict!r} not approved"])
    return ("ok", [])


def deploy_gate(review: dict | None, test_plan: dict | None) -> tuple[str, list[str]]:
    """deployment (SPEC §7): review approved AND test_plan summary.failed == 0.
    A rejected review here is unrecoverable (defense-in-depth)."""
    if not isinstance(review, dict):
        return ("recoverable", ["deploy gate: review_report.json missing"])
    verdict = review.get("verdict")
    if verdict == "rejected":
        return ("block", ["deploy gate: review verdict is 'rejected'"])
    issues = []
    if verdict not in APPROVED_VERDICTS:
        issues.append(f"deploy gate: review verdict {verdict!r} not approved")
    if not isinstance(test_plan, dict):
        issues.append("deploy gate: test_plan.json missing")
    else:
        failed = (test_plan.get("summary") or {}).get("failed")
        if failed != 0:
            issues.append(f"deploy gate: test_plan summary.failed == {failed} (must be 0)")
    return ("recoverable", issues) if issues else ("ok", [])


def e2e_gate(report: dict | None) -> tuple[str, list[str]]:
    """e2e_validation (CLAUDE.md): verdict ∈ {passed, passed_with_warnings} AND
    summary.failed == 0; a `failed` verdict triggers a one-round developer rework."""
    if not isinstance(report, dict):
        return ("recoverable", ["e2e gate: e2e_report.json missing/unreadable"])
    verdict = report.get("verdict")
    failed = (report.get("summary") or {}).get("failed")
    if verdict == "failed" or (isinstance(failed, int) and failed > 0):
        issues = [f"e2e gate: verdict={verdict!r}, summary.failed={failed}"]
        return ("rework", issues)
    if verdict not in E2E_PASS_VERDICTS:
        return ("recoverable", [f"e2e gate: verdict {verdict!r} not a pass"])
    return ("ok", [])


# Which gate predicate (if any) fires when a task of a given stage completes.
STAGE_GATE = {
    "code_review": "review",
    "deployment": "deploy",
    "e2e_validation": "e2e",
}
