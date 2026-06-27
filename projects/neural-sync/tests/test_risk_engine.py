"""Tests for the risk prediction engine (src/engine/risk.py).

Pure unit tests — no database or network calls required.

Acceptance criteria covered:
  AC4 — burnout_risk_score > 0.6 for developer with ≥48 consecutive weeks
         at workload_intensity ≥ 0.8
  AC5 — bench_risk_score > 0.7 for developer whose current project end_date
         is within 28 days with no follow-on allocation
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engine.risk import (
    AllocationSlice,
    bench_badge,
    burnout_badge,
    compute_bench_risk,
    compute_burnout_risk,
    compute_risk_scores,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

TODAY = date(2026, 6, 26)


def _alloc(weeks_ago: int, weeks_duration: int, intensity: float, is_active: bool = True) -> AllocationSlice:
    """Convenience: build an AllocationSlice anchored to TODAY."""
    start = TODAY - timedelta(weeks=weeks_ago)
    end = start + timedelta(weeks=weeks_duration)
    return AllocationSlice(
        start_date=start,
        end_date=end,
        workload_intensity=intensity,
        is_active=is_active,
    )


def _future_alloc(days_ahead: int, duration_weeks: int, intensity: float = 0.7) -> AllocationSlice:
    """Build a future (not-yet-active) AllocationSlice."""
    start = TODAY + timedelta(days=days_ahead)
    end = start + timedelta(weeks=duration_weeks)
    return AllocationSlice(
        start_date=start,
        end_date=end,
        workload_intensity=intensity,
        is_active=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — Burnout risk > 0.6 after 48 consecutive weeks at workload ≥ 0.8
# ─────────────────────────────────────────────────────────────────────────────

def test_burnout_risk_high_after_48_consecutive_weeks_high_intensity():
    """
    [AC4] A developer with ≥48 consecutive weeks at workload_intensity ≥ 0.8
    and no motivation alignment factor must return burnout_risk_score > 0.6.

    Formula: min(1.0, (consecutive_weeks / 48) × intensity_mean × (1 − maf))
    With 48 weeks at 0.8: (48/48) × 0.8 × 1.0 = 0.8 > 0.6 ✓
    """
    alloc = AllocationSlice(
        start_date=date(2025, 6, 25),
        end_date=date(2026, 6, 25),  # exactly 52 weeks (366 days → 52.29 weeks)
        workload_intensity=0.8,
        is_active=True,
    )
    score = compute_burnout_risk([alloc], motivation_alignment_factor=0.0)

    assert score > 0.6, (
        f"[AC4] burnout_risk_score {score:.4f} must be > 0.6 for ≥48 consecutive "
        "weeks at workload_intensity ≥ 0.8 with no motivation alignment."
    )
    assert 0.0 <= score <= 1.0, f"Score {score} out of [0.0, 1.0]"


def test_burnout_risk_exactly_48_weeks_produces_score_above_threshold():
    """[AC4] Exactly 48 weeks at intensity=0.9 → score > 0.6."""
    alloc = AllocationSlice(
        start_date=date(2025, 6, 26),
        end_date=date(2026, 6, 25),  # 364 days = 52 weeks
        workload_intensity=0.9,
        is_active=True,
    )
    # Trim to exactly 48 weeks: 48 × 7 = 336 days
    alloc = AllocationSlice(
        start_date=date(2025, 10, 1),
        end_date=date(2025, 10, 1) + timedelta(weeks=48),
        workload_intensity=0.9,
        is_active=True,
    )
    score = compute_burnout_risk([alloc], motivation_alignment_factor=0.0)
    assert score > 0.6, f"48 weeks at 0.9 intensity should give score > 0.6, got {score}"


def test_burnout_risk_below_threshold_for_less_than_48_weeks():
    """Burnout risk is lower when consecutive high-intensity weeks < 48."""
    alloc = AllocationSlice(
        start_date=date(2026, 3, 1),
        end_date=date(2026, 6, 25),  # ~16 weeks
        workload_intensity=0.85,
        is_active=True,
    )
    score = compute_burnout_risk([alloc], motivation_alignment_factor=0.0)
    # (16/48) × 0.85 × 1.0 ≈ 0.28 < 0.6
    assert score < 0.6, f"16 weeks at 0.85 should give score < 0.6, got {score}"


def test_burnout_risk_zero_with_high_motivation_alignment():
    """High motivation_alignment_factor should significantly dampen burnout risk."""
    alloc = AllocationSlice(
        start_date=date(2025, 6, 26),
        end_date=date(2026, 6, 25),
        workload_intensity=0.85,
        is_active=True,
    )
    score_no_alignment = compute_burnout_risk([alloc], motivation_alignment_factor=0.0)
    score_full_alignment = compute_burnout_risk([alloc], motivation_alignment_factor=1.0)
    assert score_full_alignment == 0.0, "Full motivation alignment should give 0.0 burnout risk"
    assert score_no_alignment > score_full_alignment


def test_burnout_risk_zero_when_no_allocations():
    """No allocations means no burnout risk."""
    score = compute_burnout_risk([], motivation_alignment_factor=0.0)
    assert score == 0.0


def test_burnout_risk_zero_for_low_intensity():
    """Allocations below the high-intensity threshold (< 0.8) contribute zero."""
    alloc = _alloc(weeks_ago=60, weeks_duration=60, intensity=0.79)
    score = compute_burnout_risk([alloc], motivation_alignment_factor=0.0)
    assert score == 0.0, "Intensity 0.79 is below threshold; should give 0.0"


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — Bench risk > 0.7 when project ends within 28 days with no follow-on
# ─────────────────────────────────────────────────────────────────────────────

def test_bench_risk_high_when_project_ends_in_7_days_no_follow_on():
    """
    [AC5] Developer whose current project end_date is within 28 days and has
    no follow-on allocation returns bench_risk_score > 0.7.

    Formula: (28 − days_until_end) / 28
    With 7 days until end: (28 − 7) / 28 = 21/28 = 0.75 > 0.7 ✓
    """
    end_date = TODAY + timedelta(days=7)
    alloc = AllocationSlice(
        start_date=TODAY - timedelta(weeks=20),
        end_date=end_date,
        workload_intensity=0.7,
        is_active=True,
    )
    score = compute_bench_risk([alloc], reference_date=TODAY)

    assert score > 0.7, (
        f"[AC5] bench_risk_score {score:.4f} must be > 0.7 when project ends in "
        "7 days and no follow-on allocation exists."
    )
    assert 0.0 <= score <= 1.0, f"Score {score} out of [0.0, 1.0]"


def test_bench_risk_high_when_project_ends_tomorrow():
    """[AC5] Project ending in 1 day (no follow-on) → bench risk very high."""
    end_date = TODAY + timedelta(days=1)
    alloc = AllocationSlice(
        start_date=TODAY - timedelta(weeks=12),
        end_date=end_date,
        workload_intensity=0.7,
        is_active=True,
    )
    score = compute_bench_risk([alloc], reference_date=TODAY)
    # (28 - 1) / 28 ≈ 0.964
    assert score > 0.7


def test_bench_risk_zero_with_follow_on_allocation():
    """Bench risk is 0.0 when a follow-on allocation starts within 28 days of project end."""
    current_end = TODAY + timedelta(days=7)
    follow_on_start = current_end + timedelta(days=5)  # 5 days after current end

    current = AllocationSlice(
        start_date=TODAY - timedelta(weeks=20),
        end_date=current_end,
        workload_intensity=0.7,
        is_active=True,
    )
    follow_on = AllocationSlice(
        start_date=follow_on_start,
        end_date=follow_on_start + timedelta(weeks=12),
        workload_intensity=0.7,
        is_active=False,
    )
    score = compute_bench_risk([current, follow_on], reference_date=TODAY)
    assert score == 0.0, (
        f"Follow-on allocation within 28 days should give bench_risk=0.0, got {score}"
    )


def test_bench_risk_1_when_no_active_allocation():
    """Developer with no active allocation is already benched → score = 1.0."""
    # Only a past (not active) allocation
    past_alloc = AllocationSlice(
        start_date=TODAY - timedelta(weeks=12),
        end_date=TODAY - timedelta(days=3),
        workload_intensity=0.7,
        is_active=False,
    )
    score = compute_bench_risk([past_alloc], reference_date=TODAY)
    assert score == 1.0, f"No active allocation should give bench_risk=1.0, got {score}"


def test_bench_risk_low_when_project_ends_far_future():
    """Bench risk is low when project end is far in the future."""
    end_date = TODAY + timedelta(weeks=20)
    alloc = AllocationSlice(
        start_date=TODAY - timedelta(weeks=10),
        end_date=end_date,
        workload_intensity=0.7,
        is_active=True,
    )
    score = compute_bench_risk([alloc], reference_date=TODAY)
    # (28 - 140) / 28 → clamped to 0.0
    assert score == 0.0, (
        f"Project ending in 20 weeks should give bench_risk=0.0, got {score}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Badge mapping tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected_badge", [
    (0.0, "low"),
    (0.39, "low"),
    (0.4, "medium"),
    (0.6, "medium"),
    (0.61, "high"),
    (1.0, "high"),
])
def test_burnout_badge_maps_correctly(score: float, expected_badge: str):
    """burnout_badge: low < 0.4, medium [0.4, 0.6], high > 0.6."""
    assert burnout_badge(score) == expected_badge, (
        f"burnout_badge({score}) = {burnout_badge(score)}, expected {expected_badge}"
    )


@pytest.mark.parametrize("score,expected_badge", [
    (0.0, "low"),
    (0.39, "low"),
    (0.4, "medium"),
    (0.7, "medium"),
    (0.71, "high"),
    (1.0, "high"),
])
def test_bench_badge_maps_correctly(score: float, expected_badge: str):
    """bench_badge: low < 0.4, medium [0.4, 0.7], high > 0.7."""
    assert bench_badge(score) == expected_badge, (
        f"bench_badge({score}) = {bench_badge(score)}, expected {expected_badge}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# compute_risk_scores integration
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_risk_scores_returns_all_fields():
    """compute_risk_scores returns a RiskScores dataclass with all required fields."""
    alloc = AllocationSlice(
        start_date=date(2025, 6, 26),
        end_date=date(2026, 6, 25),
        workload_intensity=0.85,
        is_active=True,
    )
    result = compute_risk_scores([alloc], motivation_alignment_factor=0.0, reference_date=TODAY)

    assert hasattr(result, "burnout_risk_score")
    assert hasattr(result, "bench_risk_score")
    assert hasattr(result, "burnout_risk_badge")
    assert hasattr(result, "bench_risk_badge")
    assert hasattr(result, "computed_at")
    assert result.burnout_risk_badge in ("low", "medium", "high")
    assert result.bench_risk_badge in ("low", "medium", "high")
    assert 0.0 <= result.burnout_risk_score <= 1.0
    assert 0.0 <= result.bench_risk_score <= 1.0


def test_compute_risk_scores_no_allocations():
    """No allocations → burnout=0.0 (low), bench=1.0 (high, already benched)."""
    result = compute_risk_scores([], reference_date=TODAY)
    assert result.burnout_risk_score == 0.0
    assert result.bench_risk_score == 1.0
    assert result.burnout_risk_badge == "low"
    assert result.bench_risk_badge == "high"
