"""Tests for the five-dimension matching engine (src/engine/matching.py).

These are pure unit tests — no database or network calls required.

Acceptance criteria covered:
  AC1  — match_score is float in [0.0, 1.0]; explanation ≥ 50 chars; latency
  AC2  — behavioral dimension (w2) is non-zero and active
  AC3  — explanation contains skill, behavioral, growth sections
  AC6  — weight change causes deterministic score delta
  AC11 — labeled good-match ≥ 0.75; labeled bad-match ≤ 0.45
"""
from __future__ import annotations

import math
import time

import pytest

from src.engine.matching import (
    compute_growth_score,
    compute_match_score,
    compute_motivation_score,
    compute_skill_score,
    compute_timezone_score,
    compute_workstyle_score,
    cosine_similarity,
    derive_project_work_style_vector,
    generate_stub_explanation,
)

# ─────────────────────────────────────────────────────────────────────────────
# Canonical test profiles
# ─────────────────────────────────────────────────────────────────────────────

# GOOD MATCH: High skill overlap, aligned work-style & motivation, same timezone,
#             matching career goals.  Expected overall score ≥ 0.75.
GOOD_DEV_SKILLS = ["Python", "FastAPI", "PostgreSQL", "Docker", "React"]
GOOD_DEV_EXP = 8
GOOD_DEV_WORK_STYLE = [0.9, 0.8, 0.7, 0.9, 0.7, 0.8, 0.9, 0.8]  # all high
GOOD_DEV_MOTIVATION = [0.9, 0.8, 0.5, 0.6, 0.7, 0.6, 0.8, 0.9]
GOOD_DEV_TIMEZONE = "Europe/Warsaw"
GOOD_DEV_GOALS = ["technical leadership", "distributed systems"]

GOOD_PROJ_SKILLS = ["Python", "FastAPI", "PostgreSQL"]
GOOD_PROJ_TEAM = "Cross-functional agile squad, async-first"
GOOD_PROJ_WORKLOAD = 0.7
GOOD_PROJ_INNOVATION = 0.8
GOOD_PROJ_TZ_REQ = "UTC+1 to UTC+3"
GOOD_PROJ_GROWTH = ["distributed systems", "technical leadership", "ML pipeline"]

# BAD MATCH: Zero skill overlap, opposing work-style vectors, misaligned
#            timezone.  Expected overall score ≤ 0.45.
BAD_DEV_SKILLS = ["Java", "Spring", "Hibernate", "JPA"]
BAD_DEV_EXP = 5
BAD_DEV_WORK_STYLE = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]  # all low
BAD_DEV_MOTIVATION = [0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
BAD_DEV_TIMEZONE = "US/Pacific"
BAD_DEV_GOALS = ["legacy systems", "process management"]

BAD_PROJ_SKILLS = ["Python", "FastAPI", "PostgreSQL", "React"]
BAD_PROJ_TEAM = "Cross-functional agile squad, async-first"
BAD_PROJ_WORKLOAD = 0.8
BAD_PROJ_INNOVATION = 0.8
BAD_PROJ_TZ_REQ = "UTC+5 to UTC+8"
BAD_PROJ_GROWTH = ["distributed systems", "ML pipeline", "technical leadership"]

# Default weights
DEFAULT_W = dict(w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: compute all five dimension scores for a profile pair
# ─────────────────────────────────────────────────────────────────────────────

def _compute_all_scores(
    dev_skills, dev_exp, dev_ws, dev_mv, dev_tz, dev_goals,
    proj_skills, proj_team, proj_workload, proj_innovation, proj_tz_req, proj_growth,
):
    skill = compute_skill_score(dev_skills, proj_skills, dev_exp)
    ws = compute_workstyle_score(dev_ws, proj_team, proj_workload, proj_innovation)
    mot = compute_motivation_score(dev_mv, proj_innovation, proj_growth, proj_workload)
    tz = compute_timezone_score(dev_tz, proj_tz_req)
    growth = compute_growth_score(dev_goals, proj_growth)
    return skill, ws, mot, tz, growth


# ─────────────────────────────────────────────────────────────────────────────
# AC11 — Labeled good-match scenario: match_score ≥ 0.75
# ─────────────────────────────────────────────────────────────────────────────

def test_good_match_score_at_least_075():
    """
    [AC11] Labeled 'good match': high skill + work_style + motivation alignment.
    Expected score range: 0.75 – 1.0.
    """
    skill, ws, mot, tz, growth = _compute_all_scores(
        GOOD_DEV_SKILLS, GOOD_DEV_EXP, GOOD_DEV_WORK_STYLE, GOOD_DEV_MOTIVATION,
        GOOD_DEV_TIMEZONE, GOOD_DEV_GOALS,
        GOOD_PROJ_SKILLS, GOOD_PROJ_TEAM, GOOD_PROJ_WORKLOAD, GOOD_PROJ_INNOVATION,
        GOOD_PROJ_TZ_REQ, GOOD_PROJ_GROWTH,
    )
    score = compute_match_score(
        **DEFAULT_W,
        skill_score=skill,
        workstyle_score=ws,
        motivation_score=mot,
        timezone_score=tz,
        growth_score=growth,
    )
    assert 0.0 <= score <= 1.0, "Score must be in [0.0, 1.0]"
    assert score >= 0.75, (
        f"[AC11] Good-match score {score:.4f} must be ≥ 0.75. "
        f"Component scores: skill={skill:.3f}, ws={ws:.3f}, "
        f"motivation={mot:.3f}, tz={tz:.3f}, growth={growth:.3f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC11 — Labeled bad-match scenario: match_score ≤ 0.45
# ─────────────────────────────────────────────────────────────────────────────

def test_bad_match_score_at_most_045():
    """
    [AC11] Labeled 'bad match': skill overlap but opposing behavioral vectors.
    Expected score range: 0.0 – 0.45.
    """
    skill, ws, mot, tz, growth = _compute_all_scores(
        BAD_DEV_SKILLS, BAD_DEV_EXP, BAD_DEV_WORK_STYLE, BAD_DEV_MOTIVATION,
        BAD_DEV_TIMEZONE, BAD_DEV_GOALS,
        BAD_PROJ_SKILLS, BAD_PROJ_TEAM, BAD_PROJ_WORKLOAD, BAD_PROJ_INNOVATION,
        BAD_PROJ_TZ_REQ, BAD_PROJ_GROWTH,
    )
    score = compute_match_score(
        **DEFAULT_W,
        skill_score=skill,
        workstyle_score=ws,
        motivation_score=mot,
        timezone_score=tz,
        growth_score=growth,
    )
    assert 0.0 <= score <= 1.0, "Score must be in [0.0, 1.0]"
    assert score <= 0.45, (
        f"[AC11] Bad-match score {score:.4f} must be ≤ 0.45. "
        f"Component scores: skill={skill:.3f}, ws={ws:.3f}, "
        f"motivation={mot:.3f}, tz={tz:.3f}, growth={growth:.3f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — Behavioral dimension is non-zero and active
# ─────────────────────────────────────────────────────────────────────────────

def test_behavioral_dimension_pair_a_strictly_lower_than_pair_b():
    """
    [AC2] Pair A (identical skills, opposing work_style) must score strictly
    lower than Pair B (identical skills, aligned work_style), confirming
    w2 is non-zero and active.
    """
    common_skills = ["Python", "FastAPI", "PostgreSQL"]
    exp = 5
    dev_motivation = [0.7, 0.6, 0.5, 0.5, 0.6, 0.5, 0.6, 0.5]
    dev_tz = "Europe/Warsaw"
    dev_goals = ["technical leadership"]

    project = dict(
        proj_skills=["Python", "FastAPI"],
        proj_team="Cross-functional agile squad, async-first",
        proj_workload=0.8,
        proj_innovation=0.8,
        proj_tz_req="UTC+1 to UTC+3",
        proj_growth=["technical leadership", "distributed systems"],
    )

    # Pair A: opposing work_style — all low (0.1) vs a high-collaboration project
    ws_a_low = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    # Pair B: aligned work_style — all high (0.9)
    ws_b_high = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]

    def _score(work_style):
        skill = compute_skill_score(common_skills, project["proj_skills"], exp)
        ws = compute_workstyle_score(
            work_style,
            project["proj_team"],
            project["proj_workload"],
            project["proj_innovation"],
        )
        mot = compute_motivation_score(
            dev_motivation,
            project["proj_innovation"],
            project["proj_growth"],
            project["proj_workload"],
        )
        tz = compute_timezone_score(dev_tz, project["proj_tz_req"])
        growth = compute_growth_score(dev_goals, project["proj_growth"])
        return compute_match_score(
            **DEFAULT_W,
            skill_score=skill,
            workstyle_score=ws,
            motivation_score=mot,
            timezone_score=tz,
            growth_score=growth,
        )

    score_pair_a = _score(ws_a_low)
    score_pair_b = _score(ws_b_high)

    assert score_pair_a < score_pair_b, (
        f"[AC2] Pair A score ({score_pair_a:.4f}) must be strictly lower than "
        f"Pair B score ({score_pair_b:.4f}). w2 must be non-zero and active."
    )
    # The difference should be meaningful (≥ 0.10 due to w2=0.25)
    assert (score_pair_b - score_pair_a) >= 0.10, (
        f"[AC2] Score difference ({score_pair_b - score_pair_a:.4f}) should be "
        f"≥ 0.10, confirming behavioral dimension has significant weight."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — Stub explanation structure: skill, behavioral, growth sections
# ─────────────────────────────────────────────────────────────────────────────

def test_stub_explanation_contains_skill_behavioral_growth_sections():
    """
    [AC3] The stub explanation must contain:
      - one statement about skill alignment
      - one statement about work-style / behavioral alignment
      - one statement about growth or career potential
    and must be ≥ 50 characters.
    """
    explanation = generate_stub_explanation(
        skill_score=0.80,
        workstyle_score=0.75,
        motivation_score=0.70,
        growth_score=0.65,
        developer_skills=GOOD_DEV_SKILLS,
        project_required_skills=GOOD_PROJ_SKILLS,
        developer_career_goals=GOOD_DEV_GOALS,
        project_growth_opportunities=GOOD_PROJ_GROWTH,
    )
    assert isinstance(explanation, str), "Explanation must be a string"
    assert len(explanation) >= 50, (
        f"[AC3] Explanation length {len(explanation)} must be ≥ 50 chars"
    )
    exp_lower = explanation.lower()
    assert "skill" in exp_lower, (
        "[AC3] Explanation must contain a statement about skill alignment"
    )
    assert any(kw in exp_lower for kw in ("behavioral", "work-style", "work style")), (
        "[AC3] Explanation must contain a statement about behavioral / work-style alignment"
    )
    assert any(kw in exp_lower for kw in ("growth", "career", "potential")), (
        "[AC3] Explanation must contain a statement about growth or career potential"
    )


def test_stub_explanation_zero_skill_overlap_still_valid():
    """[AC3] Explanation is valid even with zero skill overlap."""
    explanation = generate_stub_explanation(
        skill_score=0.0,
        workstyle_score=0.10,
        motivation_score=0.30,
        growth_score=0.05,
        developer_skills=BAD_DEV_SKILLS,
        project_required_skills=BAD_PROJ_SKILLS,
        developer_career_goals=BAD_DEV_GOALS,
        project_growth_opportunities=BAD_PROJ_GROWTH,
    )
    assert len(explanation) >= 50
    exp_lower = explanation.lower()
    assert "skill" in exp_lower
    assert any(kw in exp_lower for kw in ("behavioral", "work-style", "work style"))
    assert any(kw in exp_lower for kw in ("growth", "career", "potential"))


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — match_score stays within [0.0, 1.0]
# ─────────────────────────────────────────────────────────────────────────────

def test_match_score_is_bounded_between_0_and_1():
    """[AC1] match_score must always be a float in [0.0, 1.0]."""
    cases = [
        (0.0, 0.0, 0.0, 0.0, 0.0),   # all zeros → 0.0
        (1.0, 1.0, 1.0, 1.0, 1.0),   # all ones → 1.0
        (0.5, 0.5, 0.5, 0.5, 0.5),   # all midpoints
        (0.92, 0.97, 0.98, 1.00, 0.67),  # good match approximation
    ]
    for skill, ws, mot, tz, growth in cases:
        score = compute_match_score(
            **DEFAULT_W,
            skill_score=skill,
            workstyle_score=ws,
            motivation_score=mot,
            timezone_score=tz,
            growth_score=growth,
        )
        assert isinstance(score, float), "Score must be a float"
        assert 0.0 <= score <= 1.0, f"Score {score} out of [0.0, 1.0]"


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — Weight change causes deterministic score delta
# ─────────────────────────────────────────────────────────────────────────────

def test_weight_change_causes_deterministic_score_delta():
    """
    [AC6] Given identical profile pair inputs, changing weights must cause a
    score delta in the expected range. This is a deterministic unit test.

    Profile pair chosen so skill_score ≈ 0 and behavioral scores dominate:
    increasing w1 (skill weight) at the expense of w2 (behavioural) must
    decrease the overall match score.
    """
    # Profile pair with zero skill overlap (a Java dev vs the Python project) but
    # strongly aligned behavioral fit (work-style, motivation, timezone) against the
    # GOOD project — so the behavioral dimensions dominate the score. Shifting weight
    # from behavioural (w2/w3/w4) to skill (w1) must therefore decrease the score.
    skill, ws, mot, tz, growth = _compute_all_scores(
        BAD_DEV_SKILLS, GOOD_DEV_EXP, GOOD_DEV_WORK_STYLE, GOOD_DEV_MOTIVATION,
        GOOD_DEV_TIMEZONE, GOOD_DEV_GOALS,
        GOOD_PROJ_SKILLS, GOOD_PROJ_TEAM, GOOD_PROJ_WORKLOAD, GOOD_PROJ_INNOVATION,
        GOOD_PROJ_TZ_REQ, GOOD_PROJ_GROWTH,
    )

    # Default weights
    score_default = compute_match_score(
        w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10,
        skill_score=skill, workstyle_score=ws,
        motivation_score=mot, timezone_score=tz, growth_score=growth,
    )

    # Skill-heavy weights (increase w1 substantially)
    score_skill_heavy = compute_match_score(
        w1=0.70, w2=0.10, w3=0.10, w4=0.05, w5=0.05,
        skill_score=skill, workstyle_score=ws,
        motivation_score=mot, timezone_score=tz, growth_score=growth,
    )

    delta = score_default - score_skill_heavy
    assert delta >= 0.04, (
        f"[AC6] Weight change from default to skill-heavy must cause score to "
        f"decrease by ≥ 0.04. Got delta={delta:.4f} "
        f"(default={score_default:.4f}, skill_heavy={score_skill_heavy:.4f}). "
        f"Component scores: skill={skill:.3f}, ws={ws:.3f}, mot={mot:.3f}"
    )


def test_weight_change_moves_score_for_low_fit_candidate():
    """
    [AC6] Weight-sensitivity must also hold for a genuinely LOW-fit candidate (the
    all-low BAD pair). Here every dimension is weak, so the absolute delta is small;
    we assert *direction* — shifting weight onto the (near-zero) skill dimension and
    off the slightly-higher behavioural dimensions still strictly lowers the score.
    This preserves the coverage the primary AC6 test dropped when its inputs were
    changed to a behaviourally-strong pair (post motivation-centering).
    """
    skill, ws, mot, tz, growth = _compute_all_scores(
        BAD_DEV_SKILLS, BAD_DEV_EXP, BAD_DEV_WORK_STYLE, BAD_DEV_MOTIVATION,
        BAD_DEV_TIMEZONE, BAD_DEV_GOALS,
        BAD_PROJ_SKILLS, BAD_PROJ_TEAM, BAD_PROJ_WORKLOAD, BAD_PROJ_INNOVATION,
        BAD_PROJ_TZ_REQ, BAD_PROJ_GROWTH,
    )
    score_default = compute_match_score(
        w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10,
        skill_score=skill, workstyle_score=ws,
        motivation_score=mot, timezone_score=tz, growth_score=growth,
    )
    score_skill_heavy = compute_match_score(
        w1=0.70, w2=0.10, w3=0.10, w4=0.05, w5=0.05,
        skill_score=skill, workstyle_score=ws,
        motivation_score=mot, timezone_score=tz, growth_score=growth,
    )
    assert score_skill_heavy < score_default, (
        f"[AC6] Re-weighting must still move a low-fit candidate's score in the "
        f"expected direction. default={score_default:.4f}, "
        f"skill_heavy={score_skill_heavy:.4f}, components "
        f"skill={skill:.3f} ws={ws:.3f} mot={mot:.3f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Skill score unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_skill_score_zero_when_no_overlap():
    """Skill score is 0.0 when there is no overlap between developer and required skills."""
    score = compute_skill_score(["Java", "Spring"], ["Python", "FastAPI"], 5)
    assert score == 0.0


def test_skill_score_perfect_when_exact_match():
    """Skill score is at its maximum when developer has exactly the required skills."""
    skills = ["Python", "FastAPI", "PostgreSQL"]
    score = compute_skill_score(skills, skills, 10)
    assert score > 0.90, f"Perfect-overlap score {score} should be > 0.90"


def test_skill_score_increases_with_experience():
    """More experience years should yield a higher skill score."""
    skills = ["Python"]
    score_junior = compute_skill_score(skills, skills, 0)
    score_senior = compute_skill_score(skills, skills, 10)
    assert score_senior > score_junior


def test_skill_score_bounded_0_1():
    """Skill score must always be in [0.0, 1.0]."""
    score = compute_skill_score(
        ["Python", "FastAPI", "PostgreSQL", "Docker"],
        ["Python"],
        15,
    )
    assert 0.0 <= score <= 1.0


def test_skill_score_matches_spelling_variants_via_aliases():
    """Semantic-hybrid: equivalent spellings score identically to the canonical match
    (react ≈ react.js, ml ≈ machine learning, postgres ≈ postgresql)."""
    # A spelling variant covers the requirement exactly like the canonical token.
    assert compute_skill_score(["React.js"], ["React"], 10) == compute_skill_score(["React"], ["React"], 10)
    assert compute_skill_score(["reactjs"], ["react"], 10) == compute_skill_score(["react"], ["react"], 10)
    assert compute_skill_score(["Machine Learning"], ["ML"], 10) == compute_skill_score(["ML"], ["ML"], 10)
    assert compute_skill_score(["postgresql"], ["Postgres"], 10) == compute_skill_score(["postgres"], ["postgres"], 10)
    # …and that canonical match is near-perfect.
    assert compute_skill_score(["React.js"], ["React"], 10) > 0.90


def test_skill_aliases_do_not_invent_cross_language_matches():
    """Normalization must not create false matches between unrelated skills."""
    assert compute_skill_score(["Java"], ["Python"], 5) == 0.0
    assert compute_skill_score(["Go"], ["React"], 5) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Timezone score unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_timezone_score_1_when_within_range():
    """Timezone score is 1.0 when developer's UTC offset is within the required range."""
    score = compute_timezone_score("Europe/Warsaw", "UTC+1 to UTC+3")
    assert score == 1.0, f"Warsaw (UTC+1/UTC+2) should score 1.0 for UTC+1 to UTC+3"


def test_timezone_score_0_when_far_outside_range():
    """Timezone score is 0.0 when developer is 12+ hours outside the required window."""
    # US/Pacific is UTC-8 (or -7 DST); project requires UTC+5 to UTC+8
    # Distance ≥ 12h → score = 0.0
    score = compute_timezone_score("US/Pacific", "UTC+5 to UTC+8")
    assert score == 0.0, (
        f"US/Pacific vs UTC+5 to UTC+8 should give 0.0, got {score}"
    )


def test_timezone_score_partial_when_moderate_distance():
    """Timezone score is between 0 and 1 for moderate timezone distance."""
    # US/Eastern (UTC-5/-4) vs UTC+0 to UTC+3
    # distance ≈ 5-8 hours → score between 0 and 1
    score = compute_timezone_score("US/Eastern", "UTC+0 to UTC+3")
    assert 0.0 < score < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Growth score unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_growth_score_high_when_goals_match_opportunities():
    """Growth score is high when career goals closely match project opportunities."""
    goals = ["technical leadership", "distributed systems"]
    opps = ["distributed systems", "technical leadership", "ML pipeline"]
    score = compute_growth_score(goals, opps)
    assert score >= 0.50, f"Closely matched goals/opportunities should score ≥ 0.50, got {score}"


def test_growth_score_zero_when_no_match():
    """Growth score is 0.0 when there is no keyword overlap."""
    score = compute_growth_score(
        ["legacy systems"],
        ["kubernetes", "microservices", "ML pipeline"],
    )
    # "systems" is not a stop word but "legacy" vs all ops: 1 common token "systems" isn't in opps
    # This should be very low or zero
    assert score <= 0.15, f"Non-overlapping goals should give low growth score, got {score}"


def test_growth_score_empty_goals_returns_zero():
    """Growth score is 0.0 when career_goals list is empty."""
    score = compute_growth_score([], ["technical leadership"])
    assert score == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Cosine similarity helper tests
# ─────────────────────────────────────────────────────────────────────────────

def test_cosine_similarity_identical_vectors():
    """Cosine similarity of a vector with itself is 1.0 (scaled to [0,1] → 1.0)."""
    v = [0.8, 0.6, 0.7, 0.9, 0.4]
    result = cosine_similarity(v, v)
    assert abs(result - 1.0) < 1e-6, f"Self-cosine should be 1.0, got {result}"


def test_cosine_similarity_opposite_vectors():
    """Cosine similarity of opposing vectors should be 0.0 (scaled)."""
    v1 = [1.0, 1.0, 1.0]
    v2 = [-1.0, -1.0, -1.0]
    result = cosine_similarity(v1, v2)
    assert abs(result - 0.0) < 1e-6, f"Opposite vectors should give 0.0, got {result}"


def test_cosine_similarity_zero_vector_returns_neutral():
    """Cosine similarity returns 0.5 (neutral) when either vector has zero norm."""
    result = cosine_similarity([0.0, 0.0, 0.0], [1.0, 0.5, 0.3])
    assert result == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — Latency: pure computation completes in well under 500ms
# ─────────────────────────────────────────────────────────────────────────────

def test_pure_computation_latency_under_500ms():
    """
    [AC1] The pure algorithmic match computation (CPU-bound, no I/O) must
    complete well within the 500ms p95 SLA.  Measured over 100 iterations.
    """
    times = []
    for _ in range(100):
        start = time.perf_counter()
        skill, ws, mot, tz, growth = _compute_all_scores(
            GOOD_DEV_SKILLS, GOOD_DEV_EXP, GOOD_DEV_WORK_STYLE, GOOD_DEV_MOTIVATION,
            GOOD_DEV_TIMEZONE, GOOD_DEV_GOALS,
            GOOD_PROJ_SKILLS, GOOD_PROJ_TEAM, GOOD_PROJ_WORKLOAD, GOOD_PROJ_INNOVATION,
            GOOD_PROJ_TZ_REQ, GOOD_PROJ_GROWTH,
        )
        compute_match_score(
            **DEFAULT_W,
            skill_score=skill, workstyle_score=ws,
            motivation_score=mot, timezone_score=tz, growth_score=growth,
        )
        generate_stub_explanation(
            skill_score=skill, workstyle_score=ws,
            motivation_score=mot, growth_score=growth,
            developer_skills=GOOD_DEV_SKILLS,
            project_required_skills=GOOD_PROJ_SKILLS,
            developer_career_goals=GOOD_DEV_GOALS,
            project_growth_opportunities=GOOD_PROJ_GROWTH,
        )
        times.append(time.perf_counter() - start)

    # p95 of pure computation (not including DB or Claude)
    times_sorted = sorted(times)
    p95_ms = times_sorted[int(0.95 * len(times))] * 1000

    assert p95_ms < 500.0, (
        f"[AC1] Pure computation p95 latency {p95_ms:.1f}ms must be < 500ms. "
        "This leaves budget for DB and HTTP overhead."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Negative tests
# ─────────────────────────────────────────────────────────────────────────────

def test_match_score_with_all_zero_components():
    """Match score is 0.0 when all component scores are 0.0."""
    score = compute_match_score(
        **DEFAULT_W,
        skill_score=0.0, workstyle_score=0.0,
        motivation_score=0.0, timezone_score=0.0, growth_score=0.0,
    )
    assert score == 0.0


def test_match_score_does_not_exceed_1_with_large_components():
    """Match score is clamped to 1.0 even with overflowing inputs."""
    score = compute_match_score(
        w1=1.0, w2=0.0, w3=0.0, w4=0.0, w5=0.0,
        skill_score=1.0, workstyle_score=1.0,
        motivation_score=1.0, timezone_score=1.0, growth_score=1.0,
    )
    assert score == 1.0


def test_project_work_style_vector_has_8_dimensions():
    """Derived project work-style vector must always have exactly 8 dimensions."""
    vec = derive_project_work_style_vector(
        team_structure="agile squad",
        workload_intensity=0.7,
        innovation_level=0.8,
    )
    assert len(vec) == 8
    for val in vec:
        assert 0.0 <= val <= 1.0, f"Work-style dim {val} not in [0,1]"


# ─────────────────────────────────────────────────────────────────────────────
# AC12 — Claude prompt is stored in a versioned artifact file, not inline code
# ─────────────────────────────────────────────────────────────────────────────

def test_prompt_artifact_is_versioned_file_not_inline_code():
    """
    [AC12] The Claude prompt template must be stored in a versioned JSON artifact
    file (artifacts/prompts/match_explanation_v1.json), NOT hardcoded in Python
    source files.

    Verifies:
    1. The prompt artifact file exists at artifacts/prompts/match_explanation_v1.json.
    2. The JSON has the required versioned structure (prompt_key, version,
       template_text, model_name).
    3. ClaudeService loads the template from the file at runtime.
    """
    import json
    from pathlib import Path

    from src.services.claude_service import ClaudeService

    # Locate the artifact relative to the project root (tests/../artifacts/...)
    # tests/test_matching_engine.py → parents[0] = tests/ → parents[1] = project root
    project_root = Path(__file__).resolve().parents[1]
    artifact_path = project_root / "artifacts" / "prompts" / "match_explanation_v1.json"

    # 1. Artifact file must exist on disk
    assert artifact_path.exists(), (
        f"[AC12] Prompt artifact not found at {artifact_path}. "
        "All Claude prompt templates must be stored in versioned artifact files."
    )

    # 2. Artifact must be valid JSON with the required versioned schema
    with open(artifact_path, encoding="utf-8") as f:
        data = json.load(f)

    assert "prompt_key" in data, "[AC12] Prompt artifact must contain 'prompt_key'"
    assert "version" in data, "[AC12] Prompt artifact must contain 'version'"
    assert "template_text" in data, "[AC12] Prompt artifact must contain 'template_text'"
    assert "model_name" in data, "[AC12] Prompt artifact must contain 'model_name'"
    assert int(data["version"]) >= 1, "[AC12] Prompt version must be ≥ 1"
    assert len(data["template_text"]) > 50, (
        "[AC12] template_text must be a non-trivial prompt (> 50 chars)"
    )
    # Raw behavioral vectors must be prohibited (per AC8 x AC12 intersection)
    prohibited = data.get("prohibited_placeholders", [])
    assert "work_style_vector" in prohibited or "motivation_vector" in prohibited, (
        "[AC12] Prompt artifact must list raw vector fields as prohibited_placeholders"
    )

    # 3. ClaudeService loads from the artifact file (not from inline strings)
    service = ClaudeService(prompt_artifact_path=str(artifact_path))
    assert service.prompt.is_loaded(), (
        "[AC12] ClaudeService.prompt must successfully load from the artifact file"
    )
    assert service.prompt.template_text == data["template_text"], (
        "[AC12] ClaudeService must use the template_text from the versioned artifact, "
        "not a hardcoded string"
    )
    assert service.prompt.version == int(data["version"]), (
        "[AC12] ClaudeService.prompt.version must match the artifact version"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Availability factor inside the w4 dimension (Task04 §1 "Availability & time zone")
# ─────────────────────────────────────────────────────────────────────────────

def test_availability_is_neutral_by_default():
    """Omitting availability args must leave the timezone score unchanged (back-compat)."""
    base = compute_timezone_score("Europe/Warsaw", "UTC+1 to UTC+3")
    same = compute_timezone_score("Europe/Warsaw", "UTC+1 to UTC+3", None, None)
    assert base == same == 1.0


def test_full_availability_does_not_penalize():
    """A developer who meets the expected weekly load keeps the full base score."""
    score = compute_timezone_score(
        "Europe/Warsaw", "UTC+1 to UTC+3",
        availability_hours=40, workload_intensity=0.9,
    )
    assert score == 1.0


def test_low_availability_reduces_high_workload_score():
    """Insufficient availability for a high-workload project softly reduces w4."""
    full = compute_timezone_score(
        "Europe/Warsaw", "UTC+1 to UTC+3",
        availability_hours=40, workload_intensity=0.9,
    )
    low = compute_timezone_score(
        "Europe/Warsaw", "UTC+1 to UTC+3",
        availability_hours=10, workload_intensity=0.9,
    )
    assert low < full
    assert 0.0 <= low <= 1.0
    # Soft signal: never collapses the dimension below the 0.7 floor of the multiplier.
    assert low >= 0.7


def test_availability_factor_bounded():
    """The availability-adjusted score stays within [0, 1] across extremes."""
    for hours in (1, 20, 40, 168):
        for intensity in (0.0, 0.5, 1.0):
            s = compute_timezone_score(
                "US/Pacific", "UTC+5 to UTC+8",
                availability_hours=hours, workload_intensity=intensity,
            )
            assert 0.0 <= s <= 1.0
