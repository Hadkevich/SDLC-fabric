"""Demo fixture — make NEURAL SYNC presentable for the demo recording.

Idempotent. Re-running it produces the same end state. It only touches DEMO
presentation data; it does NOT change any product logic. All scores and
explanations are produced by the real engine (src/engine) and the real LLM
service (src/services/claude_service), so what the demo shows is exactly what
the app computes.

What it does
------------
1. Strips the synthetic placeholder skill (e.g. "BackendSkill1") from every
   developer profile so skill lists look real on camera.
2. Reshapes the demo developer account (user1) into a coherent "backend dev
   growing into ML/Data" persona that produces at least one strong match
   (>= 0.80) with a positive, on-narrative explanation.
3. Re-seeds allocation records across the 50 developers so the Manager team
   dashboard shows a believable spread of burnout/bench risk (not a wall of
   red) and the "Suggest Move" reallocation feature has high-risk members to
   act on.
4. Recomputes user1's match records from scratch (delete + recompute via the
   engine) and fills the top-10 with real LLM explanations (claude_async);
   the rest get a finalized deterministic stub (stub_permanent — no UI spinner).

Run (inside the backend container, which has Python 3.11 + deps + the code):
    docker exec -w /app neural-sync-backend-1 python scripts/demo_fixture.py
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from src.db.session import AsyncSessionLocal
from src.db.models import (
    AllocationRecord,
    DeveloperProfile,
    MatchRecord,
    ProjectProfile,
    UserAccount,
    WeightConfig,
)
from src.engine.matching import (
    compute_growth_score,
    compute_match_score,
    compute_motivation_score,
    compute_skill_score,
    compute_timezone_score,
    compute_workstyle_score,
    generate_growth_potential_list,
    generate_risks,
    generate_stub_explanation,
)
from src.engine.risk import AllocationSlice, compute_risk_scores
from src.services.claude_service import ClaudeService, get_claude_service

USER1_DEV_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
# First "data" domain developer (idx 31) — promoted to an ML burnout star so the
# Manager "Suggest Move" reallocation modal shows a strong bridge suggestion.
ML_STAR_DEV_ID = uuid.UUID("20000000-0000-0000-0000-000000000031")

_PLACEHOLDER_RE = re.compile(r"Skill\d+$")

# An ML/Data persona aligned with the "Predictive Churn Model" project
# (Python/scikit-learn/MLflow/PostgreSQL/Pandas, UTC-5..UTC+2, growth in
# "ML model development"/"feature engineering").
ML_PERSONA = {
    "skills": ["Python", "scikit-learn", "Pandas", "MLflow", "PostgreSQL", "SQL"],
    "experience_years": 8,
    "preferred_stack": ["Python", "scikit-learn", "Pandas"],
    "timezone": "America/New_York",
    "availability_hours": 40,
    "career_goals": ["ML model development", "feature engineering", "data science"],
    # 8-dim work_style aligned to a data-science / agile / research team
    "work_style_vector": [0.85, 0.55, 0.75, 0.80, 0.55, 0.60, 0.80, 0.45],
    # 8-dim motivation: learning/impact/creativity driven
    "motivation_vector": [0.80, 0.85, 0.40, 0.50, 0.80, 0.55, 0.70, 0.65],
}


def _dev_idx(dev_id: uuid.UUID) -> int:
    """Recover the 1-based seed index from a 20000000-...-NNN developer id."""
    return int(str(dev_id).split("-")[-1])


# Risk pattern assignment by developer index. Anything not listed → "healthy".
# Target: burnout high=6 / med=8, bench high=5 / med=8, rest low → health ~78%.
_PATTERNS: dict[int, str] = {}
for _i in (5, 12, 22, 31, 33, 44):
    _PATTERNS[_i] = "burnout_high"
for _i in (2, 7, 15, 24, 36, 41, 46, 49):
    _PATTERNS[_i] = "burnout_med"
for _i in (9, 18, 27, 38, 50):
    _PATTERNS[_i] = "bench_high"
for _i in (3, 8, 11, 20, 29, 34, 42, 47):
    _PATTERNS[_i] = "bench_med"
_PATTERNS[1] = "healthy"  # user1 stays healthy → normal developer dashboard


def _allocations_for(pattern: str, today: date, project_id: uuid.UUID,
                     follow_on_project_id: uuid.UUID) -> list[dict]:
    """Return allocation row dicts (sans developer_id) for a risk pattern."""
    if pattern == "burnout_high":
        # ~58 weeks at 0.9 intensity, still active, ends comfortably in future
        return [dict(project_id=project_id, start_date=today - timedelta(days=360),
                     end_date=today + timedelta(days=45), workload_intensity=0.9,
                     is_active=True)]
    if pattern == "burnout_med":
        # ~27 weeks at 0.85 → medium burnout
        return [dict(project_id=project_id, start_date=today - timedelta(days=150),
                     end_date=today + timedelta(days=40), workload_intensity=0.85,
                     is_active=True)]
    if pattern == "bench_high":
        # ends in 5 days, no follow-on → high bench risk
        return [dict(project_id=project_id, start_date=today - timedelta(days=120),
                     end_date=today + timedelta(days=5), workload_intensity=0.5,
                     is_active=True)]
    if pattern == "bench_med":
        # ends in 12 days, no follow-on → medium bench risk
        return [dict(project_id=project_id, start_date=today - timedelta(days=120),
                     end_date=today + timedelta(days=12), workload_intensity=0.55,
                     is_active=True)]
    # healthy: active comfortable allocation + a queued follow-on
    return [
        dict(project_id=project_id, start_date=today - timedelta(days=60),
             end_date=today + timedelta(days=160), workload_intensity=0.5,
             is_active=True),
        dict(project_id=follow_on_project_id, start_date=today + timedelta(days=150),
             end_date=today + timedelta(days=300), workload_intensity=0.5,
             is_active=False),
    ]


async def main() -> None:
    today = date.today()
    async with AsyncSessionLocal() as db:
        # ── 0. Load reference data ────────────────────────────────────────────
        projects = (await db.execute(select(ProjectProfile))).scalars().all()
        project_ids = [p.id for p in projects]
        weights = (await db.execute(
            select(WeightConfig).where(WeightConfig.id == 1)
        )).scalar_one()
        devs = (await db.execute(select(DeveloperProfile))).scalars().all()
        print(f"Loaded {len(devs)} developers, {len(projects)} projects.")

        # ── 1. Strip placeholder skills from every developer ──────────────────
        stripped = 0
        for dev in devs:
            cleaned = [s for s in (dev.skills or []) if not _PLACEHOLDER_RE.search(str(s))]
            if cleaned != dev.skills:
                dev.skills = cleaned
                stripped += 1
        print(f"Stripped placeholder skills from {stripped} developers.")

        # ── 2. Personas: user1 + ML burnout star (idx 31) ─────────────────────
        for dev in devs:
            if dev.id in (USER1_DEV_ID, ML_STAR_DEV_ID):
                dev.skills = list(ML_PERSONA["skills"])
                dev.experience_years = ML_PERSONA["experience_years"]
                dev.preferred_stack = list(ML_PERSONA["preferred_stack"])
                dev.timezone = ML_PERSONA["timezone"]
                dev.availability_hours = ML_PERSONA["availability_hours"]
                dev.career_goals = list(ML_PERSONA["career_goals"])
                dev.work_style_vector = list(ML_PERSONA["work_style_vector"])
                dev.motivation_vector = list(ML_PERSONA["motivation_vector"])
                dev.embedding_status = "ready"
        print("Applied ML/Data persona to user1 and the reallocation star (idx 31).")

        # ── 3. Re-seed allocations for a realistic risk spread ────────────────
        await db.execute(delete(AllocationRecord))
        n_alloc = 0
        for dev in devs:
            idx = _dev_idx(dev.id)
            pattern = _PATTERNS.get(idx, "healthy")
            pid = project_ids[idx % len(project_ids)]
            follow_pid = project_ids[(idx + 1) % len(project_ids)]
            for row in _allocations_for(pattern, today, pid, follow_pid):
                db.add(AllocationRecord(developer_id=dev.id, **row))
                n_alloc += 1
        print(f"Seeded {n_alloc} allocation records.")

        await db.flush()

        # ── 4. Recompute user1's matches (delete + engine recompute) ──────────
        await db.execute(
            delete(MatchRecord).where(MatchRecord.developer_id == USER1_DEV_ID)
        )
        user1 = next(d for d in devs if d.id == USER1_DEV_ID)
        weights_snapshot = {
            "w1": weights.w1_skill, "w2": weights.w2_workstyle,
            "w3": weights.w3_motivation, "w4": weights.w4_timezone,
            "w5": weights.w5_growth, "version": weights.version,
        }

        computed: list[tuple[ProjectProfile, MatchRecord, dict]] = []
        for proj in projects:
            ss = compute_skill_score(user1.skills, proj.required_skills, user1.experience_years)
            ws = compute_workstyle_score(user1.work_style_vector, proj.team_structure,
                                         proj.workload_intensity, proj.innovation_level)
            ms = compute_motivation_score(user1.motivation_vector, proj.innovation_level,
                                          proj.growth_opportunities, proj.workload_intensity)
            tz = compute_timezone_score(user1.timezone, proj.timezone_overlap_required)
            gs = compute_growth_score(user1.career_goals, proj.growth_opportunities)
            score = compute_match_score(
                w1=weights.w1_skill, w2=weights.w2_workstyle, w3=weights.w3_motivation,
                w4=weights.w4_timezone, w5=weights.w5_growth,
                skill_score=ss, workstyle_score=ws, motivation_score=ms,
                timezone_score=tz, growth_score=gs,
            )
            stub = generate_stub_explanation(
                skill_score=ss, workstyle_score=ws, motivation_score=ms, growth_score=gs,
                developer_skills=user1.skills, project_required_skills=proj.required_skills,
                developer_career_goals=user1.career_goals,
                project_growth_opportunities=proj.growth_opportunities,
            )
            risks = generate_risks(
                timezone_score=tz, skill_score=ss, workstyle_score=ws,
                dev_timezone=user1.timezone, project_timezone_overlap=proj.timezone_overlap_required,
                developer_skills=user1.skills, project_required_skills=proj.required_skills,
            )
            growth = generate_growth_potential_list(
                career_goals=user1.career_goals,
                growth_opportunities=proj.growth_opportunities, growth_score=gs,
            )
            rec = MatchRecord(
                id=uuid.uuid4(), developer_id=USER1_DEV_ID, project_id=proj.id,
                match_score=score, skill_score=ss, workstyle_score=ws,
                motivation_score=ms, timezone_score=tz, growth_score=gs,
                explanation=stub, explanation_source="stub_permanent",
                risks=risks, growth_potential=growth, weights_snapshot=weights_snapshot,
                vector_search_degraded=False, behavioral_data_unavailable=False,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(rec)
            ctx = ClaudeService.build_prompt_context(
                skill_score=ss, workstyle_score=ws, motivation_score=ms,
                timezone_score=tz, growth_score=gs, match_score=score,
                developer_career_goals=user1.career_goals,
                project_growth_opportunities=proj.growth_opportunities,
                developer_experience_years=user1.experience_years,
                project_name=proj.name, developer_timezone=user1.timezone,
                project_timezone_overlap=proj.timezone_overlap_required,
            )
            computed.append((proj, rec, ctx))

        await db.flush()
        print(f"Recomputed {len(computed)} match records for user1.")

        # ── 5. Fill the top-10 matches with real LLM explanations ─────────────
        computed.sort(key=lambda t: t[1].match_score, reverse=True)
        claude = get_claude_service()
        llm_ok = 0
        for proj, rec, ctx in computed[:10]:
            try:
                text, source = await claude.generate_with_retry(ctx)
                if text and source == "claude_async":
                    expl, parsed_risks, parsed_growth = claude.parse_explanation_response(text)
                    rec.explanation = expl
                    rec.explanation_source = "claude_async"
                    rec.explanation_updated_at = datetime.now(timezone.utc)
                    if parsed_risks:
                        rec.risks = parsed_risks
                    if parsed_growth:
                        rec.growth_potential = parsed_growth
                    llm_ok += 1
            except Exception as exc:  # noqa: BLE001 — best effort; stub remains
                print(f"  LLM failed for '{proj.name}': {exc}")
        print(f"Filled {llm_ok}/10 top matches with live LLM explanations.")

        await db.commit()

        # ── 6. Verification report ────────────────────────────────────────────
        print("\n── user1 top recommendations ──")
        for proj, rec, _ in computed[:6]:
            print(f"  {rec.match_score*100:5.1f}%  {rec.explanation_source:14s}  {proj.name}")

        # team risk distribution (same computation the Manager dashboard uses)
        bd = {"high": 0, "medium": 0, "low": 0}
        bench = {"high": 0, "medium": 0, "low": 0}
        for dev in devs:
            rows = (await db.execute(
                select(AllocationRecord).where(AllocationRecord.developer_id == dev.id)
            )).scalars().all()
            slices = [AllocationSlice(a.start_date, a.end_date, a.workload_intensity, a.is_active)
                      for a in rows]
            sc = compute_risk_scores(slices)
            bd[sc.burnout_risk_badge] += 1
            bench[sc.bench_risk_badge] += 1
        print("\n── team risk distribution (50 devs) ──")
        print(f"  burnout  high={bd['high']:2d}  medium={bd['medium']:2d}  low={bd['low']:2d}")
        print(f"  bench    high={bench['high']:2d}  medium={bench['medium']:2d}  low={bench['low']:2d}")
        at_risk = bd["high"] + bench["high"]
        print(f"  team health score ≈ {round((len(devs)-at_risk)/len(devs)*100)}%")
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
