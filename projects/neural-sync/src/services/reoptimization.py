"""Continuous re-optimization primitives (Task04 §1 / §4 — and the §10 "static
allocation" failure condition).

These are the *real* implementations behind the previously-stubbed admin endpoints
(POST /risk/refresh, /matches/rescore, /admin/reembed) and the optional background
scheduler (src/services/scheduler.py). Each takes a session, does bounded real work,
and returns a count — so an endpoint can report it and the scheduler can log it.

Manager-triggered and small-fleet by default; for very large fleets these should move
behind a job queue (the endpoints already speak the AsyncJobResponse 202 contract).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    DeveloperEmbedding,
    DeveloperProfile,
    MatchRecord,
    ProjectProfile,
    WeightConfig,
)
from src.engine.matching import (
    compute_growth_score,
    compute_match_score,
    compute_motivation_score,
    compute_skill_score,
    compute_timezone_score,
    compute_workstyle_score,
)
from src.engine.risk import AllocationSlice, compute_risk_scores


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def refresh_all_risk_scores(db: AsyncSession) -> int:
    """Recompute burnout/bench risk for every developer from their allocations and
    write the denormalized cache (badge + score + computed_at). This is what makes the
    roster's risk filter and the team risk distribution O(1)-per-row instead of O(N)
    recompute. Returns the number of developers refreshed."""
    devs = (
        await db.execute(
            select(DeveloperProfile).options(selectinload(DeveloperProfile.allocation_records))
        )
    ).scalars().all()

    now = _now()
    for dev in devs:
        slices = [
            AllocationSlice(
                start_date=a.start_date,
                end_date=a.end_date,
                workload_intensity=a.workload_intensity,
                is_active=a.is_active,
            )
            for a in (dev.allocation_records or [])
        ]
        scores = compute_risk_scores(slices)
        dev.burnout_risk_score = scores.burnout_risk_score
        dev.bench_risk_score = scores.bench_risk_score
        dev.burnout_risk_badge = scores.burnout_risk_badge
        dev.bench_risk_badge = scores.bench_risk_badge
        dev.risk_computed_at = now

    await db.flush()
    return len(devs)


async def rescore_all_matches(db: AsyncSession) -> int:
    """Recompute every stored MatchRecord against the CURRENT weights and profiles,
    updating in place (never deleting — that would cascade-drop feedback). This is the
    re-optimization that keeps allocations from going stale after a weight change or a
    profile edit. Returns the number of matches rescored."""
    weights = (await db.execute(select(WeightConfig).where(WeightConfig.id == 1))).scalar_one_or_none()
    if weights is None:
        return 0
    snapshot = {
        "w1": weights.w1_skill, "w2": weights.w2_workstyle, "w3": weights.w3_motivation,
        "w4": weights.w4_timezone, "w5": weights.w5_growth, "version": weights.version,
    }

    matches = (await db.execute(select(MatchRecord))).scalars().all()
    if not matches:
        return 0

    dev_ids = {m.developer_id for m in matches}
    proj_ids = {m.project_id for m in matches}
    devs = {
        d.id: d for d in (
            await db.execute(select(DeveloperProfile).where(DeveloperProfile.id.in_(dev_ids)))
        ).scalars().all()
    }
    projs = {
        p.id: p for p in (
            await db.execute(select(ProjectProfile).where(ProjectProfile.id.in_(proj_ids)))
        ).scalars().all()
    }

    now = _now()
    n = 0
    for m in matches:
        dev, proj = devs.get(m.developer_id), projs.get(m.project_id)
        if dev is None or proj is None:
            continue
        skill = compute_skill_score(dev.skills or [], proj.required_skills or [], dev.experience_years)
        ws = compute_workstyle_score(
            dev.work_style_vector or [0.5] * 8, proj.team_structure or "",
            proj.workload_intensity, proj.innovation_level,
        )
        mot = compute_motivation_score(
            dev.motivation_vector or [0.5] * 8, proj.innovation_level,
            proj.growth_opportunities or [], proj.workload_intensity,
        )
        tz = compute_timezone_score(
            dev.timezone or "UTC+0", proj.timezone_overlap_required or "UTC+0 to UTC+3",
            availability_hours=dev.availability_hours, workload_intensity=proj.workload_intensity,
        )
        gr = compute_growth_score(dev.career_goals or [], proj.growth_opportunities or [])
        m.skill_score, m.workstyle_score, m.motivation_score = skill, ws, mot
        m.timezone_score, m.growth_score = tz, gr
        m.match_score = compute_match_score(
            w1=weights.w1_skill, w2=weights.w2_workstyle, w3=weights.w3_motivation,
            w4=weights.w4_timezone, w5=weights.w5_growth,
            skill_score=skill, workstyle_score=ws, motivation_score=mot,
            timezone_score=tz, growth_score=gr,
        )
        m.weights_snapshot = snapshot
        m.timestamp = now
        n += 1

    await db.flush()
    return n


async def reembed_all_developers(db: AsyncSession) -> int:
    """Regenerate skill + behavioral embeddings for every developer and upsert them,
    marking embedding_status='ready'. Returns the number of developers re-embedded."""
    from src.engine.embeddings import generate_developer_embeddings

    devs = (await db.execute(select(DeveloperProfile))).scalars().all()
    n = 0
    for dev in devs:
        try:
            vecs = generate_developer_embeddings(
                developer_id=str(dev.id),
                skills=dev.skills or [],
                preferred_stack=dev.preferred_stack or [],
                experience_years=dev.experience_years,
                project_history=dev.project_history or [],
                work_style_vector=dev.work_style_vector or [],
                motivation_vector=dev.motivation_vector or [],
                career_goals=dev.career_goals or [],
            )
        except Exception:
            dev.embedding_status = "failed"
            continue

        for emb_type, vector in vecs.items():
            if not vector:
                continue
            existing = (
                await db.execute(
                    select(DeveloperEmbedding).where(
                        DeveloperEmbedding.developer_id == dev.id,
                        DeveloperEmbedding.embedding_type == emb_type,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.vector = vector
                existing.updated_at = _now()
            else:
                db.add(
                    DeveloperEmbedding(
                        developer_id=dev.id, embedding_type=emb_type, vector=vector,
                        model_name="auto", model_version="1",
                    )
                )
        dev.embedding_status = "ready"
        n += 1

    await db.flush()
    return n
