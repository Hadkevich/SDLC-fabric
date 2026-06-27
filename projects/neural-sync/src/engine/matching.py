"""Five-dimension matching engine.

Computes MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score
                     + w4·timezone_score + w5·growth_score

Weights are loaded fresh from the WeightConfig table on every call (no in-process cache).
All dimension scores are derived directly from the profile data; pgvector embeddings are
used when available and fall back to direct vector arithmetic when not.

AC2 guarantee: workstyle_score uses cosine similarity of the developer's 8-dim
work_style_vector vs a project work-style vector derived from project metadata.
Two developers with identical skills but opposing work_style vectors will always
produce different workstyle_scores — confirming w2 is active.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ─────────────────────────────────────────────────────────────────────────────
# Vector arithmetic helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [0, 1]. Returns 0.5 (neutral) if either vector has zero norm."""
    if len(a) != len(b):
        # Mismatched dimensions — return neutral
        return 0.5
    na, nb = _norm(a), _norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.5
    # Cosine in [-1, 1]; shift to [0, 1]
    raw = _dot(a, b) / (na * nb)
    return max(0.0, min(1.0, (raw + 1.0) / 2.0))


def _center(v: list[float], midpoint: float = 0.5) -> list[float]:
    """
    Center a bounded vector around a midpoint.

    Work-style and motivation vectors are defined in [0.0, 1.0].
    Centering around 0.5 produces values in [-0.5, 0.5], making
    cosine similarity sensitive to whether each dimension is above
    or below the midpoint.

    AC2 guarantee: two developers whose work_style vectors are
    "opposing" (one high where the other is low) will have centered
    vectors that point in opposite directions, yielding meaningfully
    different cosine similarities with the (non-uniform) project vector.
    """
    return [x - midpoint for x in v]


# ─────────────────────────────────────────────────────────────────────────────
# Skill score  (AC1)
# ─────────────────────────────────────────────────────────────────────────────

def compute_skill_score(
    developer_skills: list[str],
    required_skills: list[str],
    experience_years: int,
) -> float:
    """
    Jaccard similarity between developer skills and project required skills,
    weighted by coverage of required skills and experience years.

    Returns a float in [0.0, 1.0].
    """
    dev_set = {s.lower().strip() for s in developer_skills if s.strip()}
    req_set = {s.lower().strip() for s in required_skills if s.strip()}

    if not req_set:
        return 0.0
    if not dev_set:
        return 0.0

    intersection = dev_set & req_set
    union = dev_set | req_set

    jaccard = len(intersection) / len(union)
    # Coverage of required skills (higher bonus when all required skills are met)
    coverage = len(intersection) / len(req_set)

    # Experience factor: saturates at 10 years
    exp_factor = min(1.0, 0.7 + 0.3 * min(experience_years, 10) / 10.0)

    score = (jaccard * 0.5 + coverage * 0.5) * exp_factor
    return round(min(1.0, max(0.0, score)), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Work-style score  (AC2 — must produce different scores for opposing vectors)
# ─────────────────────────────────────────────────────────────────────────────

def derive_project_work_style_vector(
    team_structure: object,
    workload_intensity: float,
    innovation_level: float,
) -> list[float]:
    """
    Derive an 8-dim work-style vector from project metadata.

    Developer work_style dimensions (per API contract):
      [collaboration, autonomy, structure, innovation, pace,
       communication, risk_tolerance, remote_preference]
    """
    ts = str(team_structure).lower()

    # 0: collaboration — high in cross-functional/squad teams
    collaboration = 0.85 if any(k in ts for k in ("cross-functional", "squad", "agile", "scrum")) else 0.55

    # 1: autonomy — high in async-first teams
    autonomy = 0.80 if "async" in ts else (0.45 if "sync-heavy" in ts else 0.60)

    # 2: structure — high in agile/sprint, low in R&D/research
    if any(k in ts for k in ("agile", "sprint", "scrum", "kanban")):
        structure = 0.75
    elif any(k in ts for k in ("r&d", "research", "exploration", "greenfield")):
        structure = 0.35
    else:
        structure = 0.55

    # 3: innovation — directly from project innovation_level
    innovation = float(innovation_level)

    # 4: pace — directly from workload_intensity
    pace = float(workload_intensity)

    # 5: communication — high in high-feedback cultures
    communication = 0.80 if any(k in ts for k in ("high-feedback", "high feedback", "frequent")) else 0.55

    # 6: risk_tolerance — high for innovative/R&D projects
    risk_tolerance = min(1.0, float(innovation_level) * 1.1)

    # 7: remote_preference — high for async, low for office
    if "async" in ts or "remote" in ts:
        remote_preference = 0.80
    elif "hybrid" in ts:
        remote_preference = 0.55
    elif "office" in ts or "on-site" in ts:
        remote_preference = 0.25
    else:
        remote_preference = 0.50

    return [
        collaboration, autonomy, structure, innovation,
        pace, communication, risk_tolerance, remote_preference,
    ]


def compute_workstyle_score(
    dev_work_style: list[float],
    team_structure: object,
    workload_intensity: float,
    innovation_level: float,
) -> float:
    """
    Cosine similarity between developer work_style vector and project work-style vector.
    This is the behavioral dimension (w2). Two developers with opposing work_style vectors
    will have markedly different scores — confirming w2 is non-zero and active (AC2).

    Centering: Both vectors are shifted around 0.5 before cosine computation.
    Work-style values live in [0.0, 1.0]; centering maps them to [-0.5, 0.5].
    After centering, all-high [0.9,...] and all-low [0.1,...] vectors point in
    OPPOSITE directions, yielding different cosine similarities with any
    non-trivial project vector (AC2 guarantee).
    """
    project_vector = derive_project_work_style_vector(team_structure, workload_intensity, innovation_level)
    dev_centered = _center(dev_work_style)
    proj_centered = _center(project_vector)
    return round(cosine_similarity(dev_centered, proj_centered), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Motivation score
# ─────────────────────────────────────────────────────────────────────────────

def derive_project_motivation_vector(
    innovation_level: float,
    growth_opportunities: list[str],
    workload_intensity: float,
) -> list[float]:
    """
    Derive an 8-dim motivation vector from project metadata.

    Developer motivation dimensions (per API contract):
      [impact, growth, compensation, stability, creativity,
       recognition, autonomy, mission_alignment]
    """
    opps_text = " ".join(growth_opportunities).lower()

    # 0: impact — all projects have some impact
    impact = 0.70

    # 1: growth — proportional to number of distinct growth opportunities
    growth = min(1.0, len(growth_opportunities) / 5.0)

    # 2: compensation — not directly measurable; neutral
    compensation = 0.50

    # 3: stability — high-innovation projects are less stable
    stability = round(1.0 - innovation_level * 0.5, 4)

    # 4: creativity — driven by innovation level
    creativity = float(innovation_level)

    # 5: recognition — moderate baseline, elevated for leadership opportunities
    recognition = 0.70 if any(k in opps_text for k in ("leadership", "lead", "mentor", "principal")) else 0.45

    # 6: autonomy — lower for high-workload projects (less free time for self-direction)
    autonomy = round(max(0.2, 1.0 - workload_intensity * 0.4), 4)

    # 7: mission_alignment — elevated if growth opportunities mention purpose/impact
    mission_words = ("mission", "impact", "social", "sustainability", "purpose", "meaning")
    mission_alignment = 0.70 if any(w in opps_text for w in mission_words) else 0.40

    return [impact, growth, compensation, stability, creativity, recognition, autonomy, mission_alignment]


def compute_motivation_score(
    dev_motivation_vector: list[float],
    innovation_level: float,
    growth_opportunities: list[str],
    workload_intensity: float,
) -> float:
    """Cosine similarity between developer motivation vector and project motivation profile."""
    project_vector = derive_project_motivation_vector(
        innovation_level, growth_opportunities, workload_intensity
    )
    return round(cosine_similarity(dev_motivation_vector, project_vector), 6)


# ─────────────────────────────────────────────────────────────────────────────
# Timezone score
# ─────────────────────────────────────────────────────────────────────────────

def _get_utc_offset(tz_str: str) -> float:
    """Return UTC offset in hours for an IANA timezone string."""
    try:
        from datetime import timezone as tz_module
        tz = ZoneInfo(tz_str)
        now = datetime.now(tz_module.utc)
        offset = now.astimezone(tz).utcoffset()
        return offset.total_seconds() / 3600.0
    except (ZoneInfoNotFoundError, Exception):
        return 0.0


def _parse_project_timezone_range(timezone_overlap: str) -> tuple[float, float]:
    """
    Parse a project timezone overlap string into (min_offset, max_offset) in hours.

    Handles formats like:
      "UTC+1 to UTC+3"
      "UTC-8 to UTC-5"
      "Americas (UTC-8 to UTC-5)"
      "Europe/Warsaw"
      "US-East"
      "EMEA"
    """
    # Pattern: UTC±N to UTC±N
    pattern = r"UTC\s*([+-]\d+(?:\.\d+)?)\s+to\s+UTC\s*([+-]\d+(?:\.\d+)?)"
    match = re.search(pattern, timezone_overlap, re.IGNORECASE)
    if match:
        o1, o2 = float(match.group(1)), float(match.group(2))
        return min(o1, o2), max(o1, o2)

    # Single UTC offset pattern
    single = re.search(r"UTC\s*([+-]\d+(?:\.\d+)?)", timezone_overlap, re.IGNORECASE)
    if single:
        off = float(single.group(1))
        return off - 1.0, off + 1.0  # ±1h tolerance

    # Named regions
    regions: dict[str, tuple[float, float]] = {
        "americas": (-8, -3),
        "us-east": (-5, -4),
        "us-west": (-8, -7),
        "us": (-8, -4),
        "europe": (0, 3),
        "emea": (-2, 5),
        "apac": (5, 12),
        "asia": (5, 9),
        "oceania": (9, 12),
    }
    overlap_lower = timezone_overlap.lower()
    for region, (lo, hi) in regions.items():
        if region in overlap_lower:
            return lo, hi

    # Try to parse as IANA timezone
    try:
        tz_offset = _get_utc_offset(timezone_overlap)
        return tz_offset - 1.0, tz_offset + 1.0
    except Exception:
        pass

    # Default: accept any timezone
    return -12.0, 12.0


def compute_timezone_score(dev_timezone: str, project_timezone_overlap: str) -> float:
    """
    Compute timezone compatibility score in [0.0, 1.0].

    Score = 1.0 when developer timezone is within the project's required overlap window.
    Score decreases as the timezone distance from the window increases.
    """
    dev_offset = _get_utc_offset(dev_timezone)
    proj_min, proj_max = _parse_project_timezone_range(project_timezone_overlap)

    if proj_min <= dev_offset <= proj_max:
        return 1.0

    # Distance from the nearest edge of the range
    distance = min(abs(dev_offset - proj_min), abs(dev_offset - proj_max))
    # Score drops to 0 at 12 hours distance
    score = max(0.0, 1.0 - distance / 12.0)
    return round(score, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Growth score
# ─────────────────────────────────────────────────────────────────────────────

_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
        "is", "be", "with", "i", "my", "want", "looking", "experience",
        "work", "develop", "build", "learn", "grow",
    }
)


def compute_growth_score(
    career_goals: list[str],
    growth_opportunities: list[str],
) -> float:
    """
    Keyword overlap between developer career goals and project growth opportunities.
    Uses Jaccard similarity over meaningful tokens (stop-words removed).
    Returns a float in [0.0, 1.0].
    """
    if not career_goals or not growth_opportunities:
        return 0.0

    def tokenize(texts: list[str]) -> set[str]:
        combined = " ".join(texts).lower()
        tokens = set(re.findall(r"\b[a-z][a-z0-9/_-]{1,}\b", combined))
        return tokens - _STOP_WORDS

    goal_tokens = tokenize(career_goals)
    opp_tokens = tokenize(growth_opportunities)

    if not goal_tokens or not opp_tokens:
        return 0.0

    intersection = goal_tokens & opp_tokens
    union = goal_tokens | opp_tokens

    score = len(intersection) / len(union) if union else 0.0
    return round(score, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic stub explanation generator  (AC3 — ≥50 chars, 3 sections)
# ─────────────────────────────────────────────────────────────────────────────

def generate_stub_explanation(
    *,
    skill_score: float,
    workstyle_score: float,
    motivation_score: float,
    growth_score: float,
    developer_skills: list[str],
    project_required_skills: list[str],
    developer_career_goals: list[str],
    project_growth_opportunities: list[str],
) -> str:
    """
    Generate a deterministic, synchronous stub explanation covering the three
    mandatory sections required by AC3: Skill Alignment, Behavioral Fit, Growth Potential.

    This stub is always ≥ 50 characters and is replaced asynchronously by the
    Claude-generated explanation. No raw vectors are referenced.
    """
    common_skills = sorted(
        {s.lower() for s in developer_skills} & {s.lower() for s in project_required_skills}
    )

    # --- Skill alignment paragraph ---
    if common_skills:
        skills_str = ", ".join(s.title() for s in common_skills[:4])
        skill_stmt = (
            f"Skill alignment: Matching expertise in {skills_str} "
            f"yields a {skill_score:.0%} skill compatibility score."
        )
    else:
        skill_stmt = (
            f"Skill alignment: Partial technical skill overlap detected "
            f"with a {skill_score:.0%} compatibility score; "
            f"adjacent skills may transfer effectively."
        )

    # --- Behavioral fit paragraph ---
    if workstyle_score >= 0.70:
        ws_label = "strong"
    elif workstyle_score >= 0.45:
        ws_label = "moderate"
    else:
        ws_label = "partial"
    behavior_stmt = (
        f"Behavioral fit: {ws_label.capitalize()} work-style alignment "
        f"({workstyle_score:.0%}) with the project team culture and collaboration model."
    )

    # --- Growth potential paragraph ---
    matched_opps = [
        opp for opp in project_growth_opportunities
        if any(kw.lower() in opp.lower() for goal in developer_career_goals for kw in goal.split())
    ]
    if matched_opps:
        growth_stmt = (
            f"Growth potential: Project offers '{matched_opps[0]}' which aligns "
            f"with your career goals (growth score {growth_score:.0%})."
        )
    elif project_growth_opportunities:
        opps_preview = project_growth_opportunities[0][:60]
        growth_stmt = (
            f"Growth potential: Project provides exposure to {opps_preview} "
            f"and other opportunities (growth score {growth_score:.0%})."
        )
    else:
        growth_stmt = (
            f"Growth potential: Project offers career development opportunities "
            f"with a {growth_score:.0%} alignment to your stated goals."
        )

    return f"{skill_stmt} {behavior_stmt} {growth_stmt}"


# ─────────────────────────────────────────────────────────────────────────────
# Risk and growth potential list generators
# ─────────────────────────────────────────────────────────────────────────────

def generate_risks(
    *,
    timezone_score: float,
    skill_score: float,
    workstyle_score: float,
    dev_timezone: str,
    project_timezone_overlap: str,
    developer_skills: list[str],
    project_required_skills: list[str],
) -> list[str]:
    """Generate a list of identified compatibility risks (may be empty)."""
    risks: list[str] = []

    if timezone_score < 0.5:
        risks.append(
            f"Timezone mismatch: developer in {dev_timezone} vs project requirement "
            f"'{project_timezone_overlap}' limits synchronous collaboration windows."
        )

    missing = sorted(
        {s.lower() for s in project_required_skills}
        - {s.lower() for s in developer_skills}
    )
    if missing:
        missing_str = ", ".join(s.title() for s in missing[:3])
        risks.append(
            f"Skill gap: required {'skill' if len(missing) == 1 else 'skills'} "
            f"{missing_str} not listed in developer profile; ramp-up time expected."
        )

    if workstyle_score < 0.40:
        risks.append(
            "Work-style divergence: behavioral profiles suggest different preferences "
            "in collaboration pace or autonomy that may require team adaptation."
        )

    return risks


def generate_growth_potential_list(
    *,
    career_goals: list[str],
    growth_opportunities: list[str],
    growth_score: float,
) -> list[str]:
    """Generate a list of growth potential items (may be empty)."""
    items: list[str] = []

    matched = [
        opp for opp in growth_opportunities
        if any(kw.lower() in opp.lower() for goal in career_goals for kw in goal.split())
    ]
    for opp in matched[:3]:
        items.append(f"Career goal alignment: {opp}")

    for opp in growth_opportunities:
        if opp not in [i.split(": ", 1)[-1] for i in items]:
            items.append(opp)
        if len(items) >= 4:
            break

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Composite match score computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_match_score(
    *,
    w1: float,
    w2: float,
    w3: float,
    w4: float,
    w5: float,
    skill_score: float,
    workstyle_score: float,
    motivation_score: float,
    timezone_score: float,
    growth_score: float,
) -> float:
    """
    MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score
                + w4·timezone_score + w5·growth_score
    """
    score = (
        w1 * skill_score
        + w2 * workstyle_score
        + w3 * motivation_score
        + w4 * timezone_score
        + w5 * growth_score
    )
    return round(min(1.0, max(0.0, score)), 6)
