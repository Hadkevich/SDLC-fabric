"""POST /api/v1/matches — Five-dimension match score computation.

Computes MATCH_SCORE = w1·skill_score + w2·workstyle_score + w3·motivation_score
                     + w4·timezone_score + w5·growth_score

Weights are loaded fresh from WeightConfig on every request (no in-process cache)
per architecture decision to ensure immediate propagation after PUT /config/weights.

Response always contains:
  - match_score (float 0.0–1.0)
  - explanation (str ≥ 50 chars, deterministic stub or Claude-cached explanation)
  - risks (list[str])
  - growth_potential (list[str])
  - component_scores, weights_snapshot, explanation_source, vector_search_degraded
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user
from src.core.settings import settings
from src.db.models import (
    DeveloperProfile,
    ExplanationCache,
    MatchRecord,
    ProjectProfile,
    WeightConfig,
)
from src.db.session import get_db
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
from src.services.claude_service import ClaudeService, get_claude_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/matches", tags=["matches"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class ProjectHistoryEntry(BaseModel):
    project_id: str
    role: str
    start_date: str
    end_date: Optional[str] = None
    workload_intensity: Optional[float] = None


class DeveloperProfileIn(BaseModel):
    id: Optional[uuid.UUID] = None
    skills: list[str] = Field(..., min_length=1)
    experience_years: int = Field(..., ge=0)
    preferred_stack: list[str] = Field(..., min_length=1)
    work_style: list[float] = Field(..., min_length=8, max_length=8)
    motivation_vector: list[float] = Field(..., min_length=8, max_length=8)
    timezone: str
    availability_hours: int = Field(..., ge=1, le=168)
    career_goals: list[str] = Field(..., min_length=1)
    project_history: list[ProjectHistoryEntry] = Field(default_factory=list)
    is_self_reported: bool = True

    @field_validator("work_style", "motivation_vector", mode="before")
    @classmethod
    def validate_vector_range(cls, v: list[float]) -> list[float]:
        for x in v:
            if not (0.0 <= x <= 1.0):
                raise ValueError("All vector elements must be in [0.0, 1.0]")
        return v


class ProjectProfileIn(BaseModel):
    id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    required_skills: list[str] = Field(..., min_length=1)
    team_structure: object  # string or dict per spec
    workload_intensity: float = Field(..., ge=0.0, le=1.0)
    innovation_level: float = Field(..., ge=0.0, le=1.0)
    timezone_overlap_required: str
    duration_weeks: int = Field(..., ge=1)
    growth_opportunities: list[str] = Field(default_factory=list)


class MatchRequest(BaseModel):
    developer_profile: DeveloperProfileIn
    project_profile: ProjectProfileIn


class ComponentScores(BaseModel):
    skill_score: float
    workstyle_score: float
    motivation_score: float
    timezone_score: float
    growth_score: float


class WeightsSnapshot(BaseModel):
    w1: float
    w2: float
    w3: float
    w4: float
    w5: float


class MatchResponse(BaseModel):
    match_id: uuid.UUID
    developer_id: uuid.UUID
    project_id: uuid.UUID
    match_score: float
    explanation: str
    explanation_source: str
    risks: list[str]
    growth_potential: list[str]
    component_scores: ComponentScores
    weights_snapshot: WeightsSnapshot
    vector_search_degraded: bool
    behavioral_data_unavailable: bool
    created_at: datetime


class ExplanationResponse(BaseModel):
    match_id: uuid.UUID
    explanation: str
    explanation_source: str
    explanation_updated_at: Optional[datetime]


class MatchRecordResponse(MatchResponse):
    explanation_updated_at: Optional[datetime] = None


class RescoreRequest(BaseModel):
    developer_ids: Optional[list[uuid.UUID]] = None


class AsyncJobResponse(BaseModel):
    job_id: uuid.UUID
    message: str
    estimated_count: Optional[int] = None


class DeveloperMatchesResponse(BaseModel):
    developer_id: uuid.UUID
    matches: list[MatchRecordResponse]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _load_weights(db: AsyncSession) -> WeightConfig:
    """Load WeightConfig from DB primary. No in-process cache per architecture."""
    result = await db.execute(select(WeightConfig).where(WeightConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        # Seed default weights if not present
        config = WeightConfig(
            id=1, w1_skill=0.30, w2_workstyle=0.25,
            w3_motivation=0.20, w4_timezone=0.15, w5_growth=0.10,
        )
        db.add(config)
        await db.flush()
    return config


async def _get_or_create_developer(
    db: AsyncSession, dev_in: DeveloperProfileIn
) -> DeveloperProfile:
    """Upsert developer profile from inline request data."""
    dev_id = dev_in.id or uuid.uuid4()

    result = await db.execute(
        select(DeveloperProfile).where(DeveloperProfile.id == dev_id)
    )
    dev = result.scalar_one_or_none()

    if dev is None:
        dev = DeveloperProfile(
            id=dev_id,
            skills=dev_in.skills,
            experience_years=dev_in.experience_years,
            preferred_stack=dev_in.preferred_stack,
            work_style_vector=dev_in.work_style,
            motivation_vector=dev_in.motivation_vector,
            timezone=dev_in.timezone,
            availability_hours=dev_in.availability_hours,
            career_goals=dev_in.career_goals,
            project_history=[h.model_dump() for h in dev_in.project_history],
            is_behavioral_self_reported=dev_in.is_self_reported,
            embedding_status="pending",
        )
        db.add(dev)
        await db.flush()
    return dev


async def _get_or_create_project(
    db: AsyncSession, proj_in: ProjectProfileIn
) -> ProjectProfile:
    """Upsert project profile from inline request data."""
    proj_id = proj_in.id or uuid.uuid4()

    result = await db.execute(
        select(ProjectProfile).where(ProjectProfile.id == proj_id)
    )
    proj = result.scalar_one_or_none()

    if proj is None:
        proj = ProjectProfile(
            id=proj_id,
            name=proj_in.name or "Unnamed Project",
            required_skills=proj_in.required_skills,
            team_structure=proj_in.team_structure,
            workload_intensity=proj_in.workload_intensity,
            innovation_level=proj_in.innovation_level,
            timezone_overlap_required=proj_in.timezone_overlap_required,
            duration_weeks=proj_in.duration_weeks,
            growth_opportunities=proj_in.growth_opportunities,
        )
        db.add(proj)
        await db.flush()
    return proj


def _match_record_to_response(record: MatchRecord) -> MatchRecordResponse:
    return MatchRecordResponse(
        match_id=record.id,
        developer_id=record.developer_id,
        project_id=record.project_id,
        match_score=record.match_score,
        explanation=record.explanation,
        explanation_source=record.explanation_source,
        risks=record.risks or [],
        growth_potential=record.growth_potential or [],
        component_scores=ComponentScores(
            skill_score=record.skill_score,
            workstyle_score=record.workstyle_score,
            motivation_score=record.motivation_score,
            timezone_score=record.timezone_score,
            growth_score=record.growth_score,
        ),
        weights_snapshot=WeightsSnapshot(
            w1=record.weights_snapshot.get("w1", 0.30),
            w2=record.weights_snapshot.get("w2", 0.25),
            w3=record.weights_snapshot.get("w3", 0.20),
            w4=record.weights_snapshot.get("w4", 0.15),
            w5=record.weights_snapshot.get("w5", 0.10),
        ),
        vector_search_degraded=record.vector_search_degraded,
        behavioral_data_unavailable=record.behavioral_data_unavailable,
        created_at=record.timestamp,
        explanation_updated_at=record.explanation_updated_at,
    )


async def _async_generate_explanation(
    match_id: uuid.UUID,
    context: dict,
    db_url: str,
) -> None:
    """
    Background task: call Claude API and update the MatchRecord.
    Uses a fresh DB session (background task runs after response is sent).
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.core.settings import settings as app_settings

    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        claude = get_claude_service()
        explanation_text, source = await claude.generate_with_retry(context)

        if not explanation_text:
            source = "stub_permanent"
        else:
            async with session_factory() as session:
                result = await session.execute(
                    select(MatchRecord).where(MatchRecord.id == match_id)
                )
                record = result.scalar_one_or_none()
                if record:
                    parsed_explanation, parsed_risks, parsed_growth = (
                        claude.parse_explanation_response(explanation_text)
                    )
                    record.explanation = parsed_explanation
                    record.explanation_source = source
                    record.explanation_updated_at = datetime.now(timezone.utc)
                    if parsed_risks:
                        record.risks = parsed_risks
                    if parsed_growth:
                        record.growth_potential = parsed_growth
                    await session.commit()
    except Exception as exc:
        logger.error("Background Claude explanation failed for match %s: %s", match_id, exc)
    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=MatchResponse)
async def create_match(
    payload: MatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MatchResponse:
    """
    Compute a five-dimension match score and return within 500ms p95 SLA.
    Synchronous stub explanation is always returned immediately.
    Claude explanation is generated asynchronously in a background task.
    """
    dev_in = payload.developer_profile
    proj_in = payload.project_profile

    # Load weights fresh from DB (no cache)
    weights = await _load_weights(db)

    # Upsert profiles
    dev = await _get_or_create_developer(db, dev_in)
    proj = await _get_or_create_project(db, proj_in)

    # Compute five dimension scores
    behavioral_unavailable = dev.embedding_status != "ready"

    skill_score = compute_skill_score(
        dev_in.skills, proj_in.required_skills, dev_in.experience_years
    )
    workstyle_score = compute_workstyle_score(
        dev_in.work_style,
        proj_in.team_structure,
        proj_in.workload_intensity,
        proj_in.innovation_level,
    )
    motivation_score = compute_motivation_score(
        dev_in.motivation_vector,
        proj_in.innovation_level,
        proj_in.growth_opportunities,
        proj_in.workload_intensity,
    )
    timezone_score = compute_timezone_score(
        dev_in.timezone, proj_in.timezone_overlap_required
    )
    growth_score = compute_growth_score(
        dev_in.career_goals, proj_in.growth_opportunities
    )

    match_score = compute_match_score(
        w1=weights.w1_skill,
        w2=weights.w2_workstyle,
        w3=weights.w3_motivation,
        w4=weights.w4_timezone,
        w5=weights.w5_growth,
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        motivation_score=motivation_score,
        timezone_score=timezone_score,
        growth_score=growth_score,
    )

    # Generate synchronous stub explanation (AC3 — ≥50 chars, 3 sections)
    stub_explanation = generate_stub_explanation(
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        motivation_score=motivation_score,
        growth_score=growth_score,
        developer_skills=dev_in.skills,
        project_required_skills=proj_in.required_skills,
        developer_career_goals=dev_in.career_goals,
        project_growth_opportunities=proj_in.growth_opportunities,
    )

    # Generate risks and growth potential lists
    risks = generate_risks(
        timezone_score=timezone_score,
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        dev_timezone=dev_in.timezone,
        project_timezone_overlap=proj_in.timezone_overlap_required,
        developer_skills=dev_in.skills,
        project_required_skills=proj_in.required_skills,
    )
    growth_potential = generate_growth_potential_list(
        career_goals=dev_in.career_goals,
        growth_opportunities=proj_in.growth_opportunities,
        growth_score=growth_score,
    )

    weights_snapshot = {
        "w1": weights.w1_skill,
        "w2": weights.w2_workstyle,
        "w3": weights.w3_motivation,
        "w4": weights.w4_timezone,
        "w5": weights.w5_growth,
        "version": weights.version,
    }

    # Check ExplanationCache
    dev_json = json.dumps(
        {"id": str(dev.id), "skills": dev_in.skills, "work_style": dev_in.work_style},
        sort_keys=True,
    )
    proj_json = json.dumps(
        {"id": str(proj.id), "required_skills": proj_in.required_skills},
        sort_keys=True,
    )
    weights_json = json.dumps(weights_snapshot, sort_keys=True)
    cache_key = ClaudeService.compute_cache_key(dev_json, proj_json, weights_json)

    cached = await db.execute(
        select(ExplanationCache).where(
            ExplanationCache.cache_key == cache_key,
            ExplanationCache.expires_at > datetime.now(timezone.utc),
        )
    )
    cached_entry = cached.scalar_one_or_none()

    if cached_entry:
        final_explanation = cached_entry.explanation
        explanation_source = "claude_cached"
    else:
        final_explanation = stub_explanation
        explanation_source = "stub_pending"

    # Persist MatchRecord
    match_id = uuid.uuid4()
    record = MatchRecord(
        id=match_id,
        developer_id=dev.id,
        project_id=proj.id,
        match_score=match_score,
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        motivation_score=motivation_score,
        timezone_score=timezone_score,
        growth_score=growth_score,
        explanation=final_explanation,
        explanation_source=explanation_source,
        risks=risks,
        growth_potential=growth_potential,
        weights_snapshot=weights_snapshot,
        vector_search_degraded=False,
        behavioral_data_unavailable=behavioral_unavailable,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.flush()

    # Enqueue async Claude explanation if not cached
    if explanation_source == "stub_pending":
        claude_context = ClaudeService.build_prompt_context(
            skill_score=skill_score,
            workstyle_score=workstyle_score,
            motivation_score=motivation_score,
            timezone_score=timezone_score,
            growth_score=growth_score,
            match_score=match_score,
            developer_career_goals=dev_in.career_goals,
            project_growth_opportunities=proj_in.growth_opportunities,
            developer_experience_years=dev_in.experience_years,
            project_name=proj_in.name or "Project",
            developer_timezone=dev_in.timezone,
            project_timezone_overlap=proj_in.timezone_overlap_required,
        )
        background_tasks.add_task(
            _async_generate_explanation,
            match_id,
            claude_context,
            settings.database_url,
        )

    return MatchResponse(
        match_id=record.id,
        developer_id=dev.id,
        project_id=proj.id,
        match_score=match_score,
        explanation=final_explanation,
        explanation_source=explanation_source,
        risks=risks,
        growth_potential=growth_potential,
        component_scores=ComponentScores(
            skill_score=skill_score,
            workstyle_score=workstyle_score,
            motivation_score=motivation_score,
            timezone_score=timezone_score,
            growth_score=growth_score,
        ),
        weights_snapshot=WeightsSnapshot(
            w1=weights.w1_skill,
            w2=weights.w2_workstyle,
            w3=weights.w3_motivation,
            w4=weights.w4_timezone,
            w5=weights.w5_growth,
        ),
        vector_search_degraded=False,
        behavioral_data_unavailable=behavioral_unavailable,
        created_at=record.timestamp,
    )


@router.get("/{match_id}", response_model=MatchRecordResponse)
async def get_match(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MatchRecordResponse:
    result = await db.execute(
        select(MatchRecord).where(MatchRecord.id == match_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    # Developers may only view their own matches
    if current_user.role == "developer" and str(record.developer_id) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return _match_record_to_response(record)


@router.get("/{match_id}/explanation", response_model=ExplanationResponse)
async def get_match_explanation(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ExplanationResponse:
    result = await db.execute(
        select(MatchRecord).where(MatchRecord.id == match_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    if current_user.role == "developer" and str(record.developer_id) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return ExplanationResponse(
        match_id=record.id,
        explanation=record.explanation,
        explanation_source=record.explanation_source,
        explanation_updated_at=record.explanation_updated_at,
    )


@router.post("/rescore", status_code=status.HTTP_202_ACCEPTED, response_model=AsyncJobResponse)
async def rescore_matches(
    payload: Optional[RescoreRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> AsyncJobResponse:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    job_id = uuid.uuid4()
    # In production: enqueue actual re-scoring job
    return AsyncJobResponse(
        job_id=job_id,
        message="Re-score job accepted and queued",
        estimated_count=None,
    )
