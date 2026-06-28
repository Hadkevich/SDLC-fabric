"""Developer profile CRUD, risk scores, recommendations, and GDPR erasure.

Endpoints:
  POST   /developers                     — create profile
  GET    /developers/{id}                — get profile (AC8: no raw vectors in response)
  PUT    /developers/{id}                — replace profile
  DELETE /developers/{id}                — GDPR full cascade (AC9)
  GET    /developers/{id}/risk           — burnout + bench risk (AC4, AC5)
  GET    /developers/{id}/matches        — ranked recommendations

AC8: Manager-facing responses must NOT expose raw work_style vectors or
     motivation_vector arrays. Only aggregated risk scores and badges are returned.
AC9: DELETE triggers atomic cascade covering all 6 entity classes.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user, require_manager
from src.db.models import (
    AllocationRecord,
    DeveloperProfile,
    ErasureAuditLog,
    ExplanationCache,
    FeedbackRecord,
    MatchRecord,
    ProjectProfile,
    UserAccount,
)
from src.db.session import get_db
from src.engine.risk import AllocationSlice, compute_risk_scores
from src.engine.matching import (
    compute_skill_score,
    compute_workstyle_score,
    compute_motivation_score,
    compute_timezone_score,
    compute_growth_score,
    compute_match_score,
)
from src.core.helpers import create_developer_profile, _enqueue_embeddings

router = APIRouter(prefix="/developers", tags=["developers"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas  (AC8: raw vectors EXCLUDED from all response models)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectHistoryEntry(BaseModel):
    project_id: str
    role: str
    start_date: str
    end_date: Optional[str] = None
    workload_intensity: Optional[float] = None


class DeveloperProfileCreate(BaseModel):
    """Request body — includes work_style and motivation_vector for input validation."""
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
    def validate_range(cls, v: list[float]) -> list[float]:
        for x in v:
            if not (0.0 <= x <= 1.0):
                raise ValueError("All vector elements must be in [0.0, 1.0]")
        return v


class DeveloperProfileResponse(BaseModel):
    """
    Response model — raw work_style and motivation_vector are EXCLUDED (AC8).
    Only aggregate attributes and embedding_status are returned.
    """
    id: uuid.UUID
    skills: list[str]
    experience_years: int
    preferred_stack: list[str]
    timezone: str
    availability_hours: int
    career_goals: list[str]
    project_history: list
    is_self_reported: bool
    embedding_status: str
    created_at: datetime
    updated_at: datetime


class RiskResponse(BaseModel):
    developer_id: uuid.UUID
    burnout_risk_score: float
    bench_risk_score: float
    burnout_risk_badge: str
    bench_risk_badge: str
    computed_at: datetime
    # Task04-requirements §1 (Mission Objective) third prediction. Null when the
    # developer has no match record yet (behavioral fit unknown).
    team_mismatch_probability: Optional[float] = None
    team_mismatch_badge: Optional[str] = None


class SuggestedProjectMove(BaseModel):
    project_id: uuid.UUID
    project_name: str
    match_score: float
    component_scores: dict
    action_type: str
    rationale: str
    projected_burnout_after_move: float


class ReallocationSuggestionResponse(BaseModel):
    developer_id: uuid.UUID
    trigger: str
    current_burnout_score: float
    current_bench_score: float
    current_burnout_badge: str
    current_bench_badge: str
    suggestion: Optional[SuggestedProjectMove]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _profile_to_response(dev: DeveloperProfile) -> DeveloperProfileResponse:
    """Convert DB model to response — raw vectors are never included."""
    return DeveloperProfileResponse(
        id=dev.id,
        skills=dev.skills or [],
        experience_years=dev.experience_years,
        preferred_stack=dev.preferred_stack or [],
        timezone=dev.timezone,
        availability_hours=dev.availability_hours,
        career_goals=dev.career_goals or [],
        project_history=dev.project_history or [],
        is_self_reported=dev.is_behavioral_self_reported,
        embedding_status=dev.embedding_status,
        created_at=dev.created_at,
        updated_at=dev.updated_at,
    )



# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=DeveloperProfileResponse)
async def create_developer(
    payload: DeveloperProfileCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> DeveloperProfileResponse:
    dev_id = payload.id or uuid.uuid4()

    existing = await db.get(DeveloperProfile, dev_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Developer {dev_id} already exists")

    # Use the shared create-plus-embed helper (AC16, AC23).
    # POST /api/v1/developers and commit-mode ingestion share this single code path.
    dev = await create_developer_profile(
        db,
        background_tasks,
        dev_id=payload.id,
        skills=payload.skills,
        experience_years=payload.experience_years,
        preferred_stack=payload.preferred_stack,
        work_style=payload.work_style,
        motivation_vector=payload.motivation_vector,
        timezone_str=payload.timezone,
        availability_hours=payload.availability_hours,
        career_goals=payload.career_goals,
        project_history=[h.model_dump() for h in payload.project_history],
        is_self_reported=payload.is_self_reported,
    )
    return _profile_to_response(dev)


# ─────────────────────────────────────────────────────────────────────────────
# POST /developers/enrich  (Task04-requirements §3.1 — raw text → structured vectors)
# ─────────────────────────────────────────────────────────────────────────────

class EnrichmentRequest(BaseModel):
    """Raw operator text to derive a structured profile from."""
    cv_text: str = Field(..., min_length=1)
    git_log_text: str = ""
    slack_text: str = ""
    timezone: str = "UTC"
    availability_hours: int = Field(40, ge=1, le=168)
    experience_years: Optional[int] = Field(None, ge=0)


class EnrichmentDraftResponse(BaseModel):
    """A DeveloperProfileCreate-shaped DRAFT for human review before creation.

    Provenance is "llm" or "heuristic"; is_self_reported is False because these
    vectors were derived from raw text, not self-declared.
    """
    skills: list[str]
    preferred_stack: list[str]
    work_style: list[float]
    motivation_vector: list[float]
    career_goals: list[str]
    timezone: str
    availability_hours: int
    experience_years: int
    is_self_reported: bool = False
    provenance: str


@router.post("/enrich", response_model=EnrichmentDraftResponse)
async def enrich_developer_profile(
    payload: EnrichmentRequest,
    current_user: TokenPayload = Depends(get_current_user),
) -> EnrichmentDraftResponse:
    """Derive a structured profile draft from raw CV / Git / Slack text.

    Returns a draft only — the caller reviews it and POSTs to /developers to create
    an actual profile. The LLM path is used when configured; otherwise a deterministic
    heuristic extraction runs (always available, no key required).
    """
    from src.services.enrichment import enrich_profile

    # enrich_profile is synchronous and may call the Gemini API; run it off the event
    # loop so a slow LLM call can't block every other concurrent request.
    result = await asyncio.to_thread(
        enrich_profile, payload.cv_text, payload.git_log_text, payload.slack_text
    )

    # The draft feeds DeveloperProfileCreate, which requires a non-empty skills list.
    # Fail fast here with a clear message instead of a confusing 422 at POST /developers.
    if not result.skills:
        raise HTTPException(
            status_code=422,
            detail="No skills could be extracted from the provided text — add them manually.",
        )

    # Infer experience years from the request, or roughly from the CV. Take the LARGEST
    # "<n> years" figure (CVs mention project durations too), clamped to a sane range.
    exp = payload.experience_years
    if exp is None:
        yrs = [int(n) for n in re.findall(r"(\d{1,2})\s*\+?\s*years?\b", payload.cv_text, re.IGNORECASE)]
        exp = min(max(yrs), 60) if yrs else 3

    return EnrichmentDraftResponse(
        skills=result.skills,
        preferred_stack=result.preferred_stack,
        work_style=result.work_style,
        motivation_vector=result.motivation_vector,
        career_goals=result.career_goals,
        timezone=payload.timezone,
        availability_hours=payload.availability_hours,
        experience_years=exp,
        is_self_reported=False,
        provenance=result.provenance,
    )


@router.get("/{developer_id}", response_model=DeveloperProfileResponse)
async def get_developer(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> DeveloperProfileResponse:
    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    # Developers can only view their own profile
    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    return _profile_to_response(dev)


@router.put("/{developer_id}", response_model=DeveloperProfileResponse)
async def update_developer(
    developer_id: uuid.UUID,
    payload: DeveloperProfileCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> DeveloperProfileResponse:
    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    dev.skills = payload.skills
    dev.experience_years = payload.experience_years
    dev.preferred_stack = payload.preferred_stack
    dev.work_style_vector = payload.work_style
    dev.motivation_vector = payload.motivation_vector
    dev.timezone = payload.timezone
    dev.availability_hours = payload.availability_hours
    dev.career_goals = payload.career_goals
    dev.project_history = [h.model_dump() for h in payload.project_history]
    dev.is_behavioral_self_reported = payload.is_self_reported
    dev.embedding_status = "pending"
    dev.updated_at = datetime.now(timezone.utc)

    await db.flush()
    dev_data = {
        "skills": dev.skills or [],
        "preferred_stack": dev.preferred_stack or [],
        "experience_years": dev.experience_years,
        "project_history": dev.project_history or [],
        "work_style_vector": dev.work_style_vector or [],
        "motivation_vector": dev.motivation_vector or [],
        "career_goals": dev.career_goals or [],
    }
    background_tasks.add_task(_enqueue_embeddings, dev.id, dev_data)
    return _profile_to_response(dev)


@router.delete("/{developer_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_developer(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> None:
    """
    GDPR right-to-erasure: atomic cascade deletion across all 6 entity classes.

    Steps (executed within a single transaction via SQLAlchemy cascade):
      1. DeveloperProfile row — root deletion
      2. DeveloperEmbedding rows — ON DELETE CASCADE from developer_profiles.id
      3. MatchRecord rows — ON DELETE CASCADE
      4. FeedbackRecord rows — ON DELETE CASCADE (two paths)
      5. AllocationRecord rows — ON DELETE CASCADE
      6. ExplanationCache rows — ON DELETE CASCADE via developer_id FK

    ErasureAuditLog row is created OUTSIDE the transaction so it persists
    even on rollback (status='failed' in that case).

    Returns HTTP 204 on success; subsequent GET /developers/{id} returns 404.
    """
    # Only the developer themselves or a manager may request erasure
    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    # Record the linked user account BEFORE cascade (FK will become NULL)
    ua_result = await db.execute(
        select(UserAccount).where(UserAccount.developer_profile_id == developer_id)
    )
    linked_ua = ua_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    erasure_request_id = uuid.uuid4()

    try:
        # Step 1 (+ cascades 2-6 automatically via ON DELETE CASCADE)
        await db.delete(dev)
        await db.flush()

        # Step 7: explicitly delete linked UserAccount
        if linked_ua:
            await db.delete(linked_ua)
            await db.flush()

        # Create ErasureAuditLog (committed with the main transaction)
        audit = ErasureAuditLog(
            erasure_request_id=erasure_request_id,
            developer_id=str(developer_id),
            requested_at=now,
            completed_at=datetime.now(timezone.utc),
            status="completed",
            initiating_user_id=current_user.user_id,
            steps_completed=[
                "developer_profile", "developer_embeddings",
                "match_records", "feedback_records",
                "allocation_records", "explanation_cache",
            ],
        )
        db.add(audit)
        # session.commit() is called by get_db on success

    except Exception as exc:
        # Rollback is handled by get_db; log audit entry with failed status
        audit_fail = ErasureAuditLog(
            erasure_request_id=erasure_request_id,
            developer_id=str(developer_id),
            requested_at=now,
            status="failed",
            initiating_user_id=current_user.user_id,
            error_detail=str(exc),
            steps_completed=[],
        )
        # Use a separate session for the audit entry since ours was rolled back
        from src.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as audit_session:
            audit_session.add(audit_fail)
            await audit_session.commit()

        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "ERASURE_TRANSACTION_FAILED",
                "message": (
                    "Developer profile erasure failed and was rolled back. "
                    "Data is preserved. Reference erasure_request_id for support."
                ),
                "erasure_request_id": str(erasure_request_id),
            },
        ) from exc

    # No response body for 204


@router.get("/{developer_id}/risk", response_model=RiskResponse)
async def get_developer_risk(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> RiskResponse:
    """
    Compute burnout and bench risk scores from AllocationRecord data.
    Raw behavioral vectors are NEVER returned (AC8).
    """
    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    # Load allocation records
    alloc_result = await db.execute(
        select(AllocationRecord).where(AllocationRecord.developer_id == developer_id)
    )
    alloc_rows = alloc_result.scalars().all()

    slices = [
        AllocationSlice(
            start_date=a.start_date,
            end_date=a.end_date,
            workload_intensity=a.workload_intensity,
            is_active=a.is_active,
        )
        for a in alloc_rows
    ]

    # Behavioral fit for the team-mismatch prediction comes from the developer's match
    # against their ASSIGNED project — the project_id of an active allocation, newest
    # match. (Not merely the latest match overall, which could be against a project the
    # developer is not on.) None when no match exists for an assigned project.
    active_project_ids = {
        a.project_id for a in alloc_rows if a.is_active and a.project_id is not None
    }
    assigned_match = None
    if active_project_ids:
        match_result = await db.execute(
            select(MatchRecord)
            .where(
                MatchRecord.developer_id == developer_id,
                MatchRecord.project_id.in_(active_project_ids),
            )
            .order_by(MatchRecord.timestamp.desc())
            .limit(1)
        )
        assigned_match = match_result.scalar_one_or_none()

    # motivation_alignment_factor defaults to 0.0 (unknown/worst-case)
    scores = compute_risk_scores(
        slices,
        workstyle_score=assigned_match.workstyle_score if assigned_match else None,
        motivation_score=assigned_match.motivation_score if assigned_match else None,
    )

    return RiskResponse(
        developer_id=developer_id,
        burnout_risk_score=scores.burnout_risk_score,
        bench_risk_score=scores.bench_risk_score,
        burnout_risk_badge=scores.burnout_risk_badge,
        bench_risk_badge=scores.bench_risk_badge,
        computed_at=scores.computed_at,
        team_mismatch_probability=scores.team_mismatch_probability,
        team_mismatch_badge=scores.team_mismatch_badge,
    )


@router.get("/{developer_id}/matches")
async def get_developer_matches(
    developer_id: uuid.UUID,
    top_k: int = 10,
    min_score: Optional[float] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Return top-K ranked match records for a developer."""
    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    query = (
        select(MatchRecord)
        .where(MatchRecord.developer_id == developer_id)
        .order_by(MatchRecord.match_score.desc())
    )
    if min_score is not None:
        query = query.where(MatchRecord.match_score >= min_score)

    count_stmt = select(func.count()).select_from(MatchRecord).where(
        MatchRecord.developer_id == developer_id,
        *(
            [MatchRecord.match_score >= min_score]
            if min_score is not None
            else []
        ),
    )
    count_raw = (await db.execute(count_stmt)).scalar()
    total = int(count_raw) if count_raw is not None else 0

    query = query.limit(top_k)
    result = await db.execute(query)
    records = result.scalars().all()

    # Load project names in one query
    project_ids = list({r.project_id for r in records})
    proj_result = await db.execute(
        select(ProjectProfile).where(ProjectProfile.id.in_(project_ids))
    )
    project_name_map = {p.id: (p.name or "Project") for p in proj_result.scalars().all()}

    from src.api.matches import _match_record_to_response, DeveloperMatchesResponse
    return DeveloperMatchesResponse(
        developer_id=developer_id,
        matches=[
            _match_record_to_response(r, project_name=project_name_map.get(r.project_id, "Project"))
            for r in records
        ],
        total=total,
    )


@router.get("/{developer_id}/reallocation-suggestion", response_model=ReallocationSuggestionResponse)
async def get_reallocation_suggestion(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ReallocationSuggestionResponse:
    """
    Example D from idea-brief: when burnout > 0.6 or bench > 0.7, find the
    best bridge project via the deterministic matching engine and return a
    structured reallocation proposal. Human (manager) confirms — system suggests.
    """
    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    # Compute current risk
    alloc_result = await db.execute(
        select(AllocationRecord).where(AllocationRecord.developer_id == developer_id)
    )
    alloc_rows = alloc_result.scalars().all()
    slices = [
        AllocationSlice(
            start_date=a.start_date,
            end_date=a.end_date,
            workload_intensity=a.workload_intensity,
            is_active=a.is_active,
        )
        for a in alloc_rows
    ]
    scores = compute_risk_scores(slices)

    trigger = "none"
    if scores.burnout_risk_score > 0.6:
        trigger = "burnout"
    elif scores.bench_risk_score > 0.7:
        trigger = "bench"

    if trigger == "none":
        return ReallocationSuggestionResponse(
            developer_id=developer_id,
            trigger="none",
            current_burnout_score=scores.burnout_risk_score,
            current_bench_score=scores.bench_risk_score,
            current_burnout_badge=scores.burnout_risk_badge,
            current_bench_badge=scores.bench_risk_badge,
            suggestion=None,
        )

    # Load current weights
    from src.db.models import WeightConfig as WeightConfigModel
    weights_result = await db.execute(
        select(WeightConfigModel).order_by(WeightConfigModel.version.desc()).limit(1)
    )
    weights = weights_result.scalar_one_or_none()
    w1 = weights.w1_skill if weights else 0.30
    w2 = weights.w2_workstyle if weights else 0.25
    w3 = weights.w3_motivation if weights else 0.20
    w4 = weights.w4_timezone if weights else 0.15
    w5 = weights.w5_growth if weights else 0.10

    # Load candidate projects (lower intensity for burnout cases)
    proj_query = select(ProjectProfile)
    if trigger == "burnout":
        proj_query = proj_query.where(ProjectProfile.workload_intensity <= 0.6)
    proj_result = await db.execute(proj_query.limit(50))
    projects = proj_result.scalars().all()

    if not projects:
        proj_result = await db.execute(select(ProjectProfile).limit(50))
        projects = proj_result.scalars().all()

    # Score each candidate project using the deterministic matching engine
    best_score = -1.0
    best_project = None
    best_components: dict = {}

    for proj in projects:
        skill_score = compute_skill_score(
            developer_skills=dev.skills or [],
            required_skills=proj.required_skills or [],
            experience_years=dev.experience_years,
        )
        workstyle_score = compute_workstyle_score(
            dev_work_style=dev.work_style_vector or [0.5] * 8,
            team_structure=proj.team_structure or "",
            workload_intensity=proj.workload_intensity,
            innovation_level=proj.innovation_level,
        )
        motivation_score = compute_motivation_score(
            dev_motivation_vector=dev.motivation_vector or [0.5] * 8,
            innovation_level=proj.innovation_level,
            growth_opportunities=proj.growth_opportunities or [],
            workload_intensity=proj.workload_intensity,
        )
        timezone_score = compute_timezone_score(
            dev_timezone=dev.timezone or "UTC+0",
            project_timezone_overlap=proj.timezone_overlap_required or "UTC+0..UTC+3",
            availability_hours=dev.availability_hours,
            workload_intensity=proj.workload_intensity,
        )
        growth_score = compute_growth_score(
            career_goals=dev.career_goals or [],
            growth_opportunities=proj.growth_opportunities or [],
        )
        match_score = compute_match_score(
            w1=w1, w2=w2, w3=w3, w4=w4, w5=w5,
            skill_score=skill_score,
            workstyle_score=workstyle_score,
            motivation_score=motivation_score,
            timezone_score=timezone_score,
            growth_score=growth_score,
        )
        if match_score > best_score:
            best_score = match_score
            best_project = proj
            best_components = {
                "skill_score": skill_score,
                "workstyle_score": workstyle_score,
                "motivation_score": motivation_score,
                "timezone_score": timezone_score,
                "growth_score": growth_score,
            }

    if best_project is None:
        return ReallocationSuggestionResponse(
            developer_id=developer_id,
            trigger=trigger,
            current_burnout_score=scores.burnout_risk_score,
            current_bench_score=scores.bench_risk_score,
            current_burnout_badge=scores.burnout_risk_badge,
            current_bench_badge=scores.bench_risk_badge,
            suggestion=None,
        )

    # Project burnout after move to lower-intensity project
    projected_burnout = round(
        min(1.0, scores.burnout_risk_score * (best_project.workload_intensity / 0.9)), 6
    )

    # Determine action type
    dev_skills_lower = {s.lower() for s in (dev.skills or [])}
    proj_skills_lower = {s.lower() for s in (best_project.required_skills or [])}
    overlap = dev_skills_lower & proj_skills_lower
    action_type = "bench-fill" if trigger == "bench" else (
        "skill-bridge" if len(overlap) < len(proj_skills_lower) * 0.7 else "lateral-move"
    )

    if trigger == "burnout":
        rationale = (
            f"Reduces workload intensity from current high level to "
            f"{best_project.workload_intensity:.1f} — projected burnout drops "
            f"from {scores.burnout_risk_score:.2f} to {projected_burnout:.2f}. "
            f"Match score {best_score:.2f} ensures alignment is maintained."
        )
    else:
        rationale = (
            f"Developer is currently benched (bench risk {scores.bench_risk_score:.2f}). "
            f"Project '{best_project.name or 'Project'}' provides immediate engagement "
            f"with a {best_score:.2f} compatibility score."
        )

    return ReallocationSuggestionResponse(
        developer_id=developer_id,
        trigger=trigger,
        current_burnout_score=scores.burnout_risk_score,
        current_bench_score=scores.bench_risk_score,
        current_burnout_badge=scores.burnout_risk_badge,
        current_bench_badge=scores.bench_risk_badge,
        suggestion=SuggestedProjectMove(
            project_id=best_project.id,
            project_name=best_project.name or "Project",
            match_score=best_score,
            component_scores=best_components,
            action_type=action_type,
            rationale=rationale,
            projected_burnout_after_move=projected_burnout,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /developers/{id}/recommendations  (WS-B4 — on-demand ANN-backed matching)
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationRequest(BaseModel):
    """Tuning knobs for on-demand recommendations (all optional)."""
    top_k: int = Field(10, ge=1, le=50)
    candidate_pool: int = Field(50, ge=1, le=200)
    min_score: Optional[float] = Field(None, ge=0.0, le=1.0)


@router.post("/{developer_id}/recommendations")
async def recommend_projects(
    developer_id: uuid.UUID,
    payload: Optional[RecommendationRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
):
    """Compute fresh recommendations on demand: pgvector ANN pre-selects candidate
    projects, the deterministic 5-dimension engine scores them, and MatchRecords are
    upserted (never deleted — that would cascade-drop feedback). Returns the ranked
    top-K. Falls back to a bounded relational scan with vector_search_degraded=True
    when ANN is unavailable.
    """
    from src.api.matches import _load_weights, _match_record_to_response, DeveloperMatchesResponse
    from src.engine.retrieval import ann_candidate_projects, VectorSearchDegraded
    from src.engine.matching import (
        generate_stub_explanation, generate_risks, generate_growth_potential_list,
    )

    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    req = payload or RecommendationRequest()
    weights = await _load_weights(db)

    degraded = False
    try:
        candidate_ids = await ann_candidate_projects(db, developer_id, top_n=req.candidate_pool)
    except VectorSearchDegraded:
        degraded = True
        res = await db.execute(select(ProjectProfile.id).limit(req.candidate_pool))
        candidate_ids = [r[0] for r in res.all()]

    if not candidate_ids:
        return DeveloperMatchesResponse(developer_id=developer_id, matches=[], total=0)

    proj_rows = (
        await db.execute(select(ProjectProfile).where(ProjectProfile.id.in_(candidate_ids)))
    ).scalars().all()
    existing_rows = (
        await db.execute(
            select(MatchRecord).where(
                MatchRecord.developer_id == developer_id,
                MatchRecord.project_id.in_(candidate_ids),
            )
        )
    ).scalars().all()
    existing_by_proj = {m.project_id: m for m in existing_rows}

    weights_snapshot = {
        "w1": weights.w1_skill, "w2": weights.w2_workstyle, "w3": weights.w3_motivation,
        "w4": weights.w4_timezone, "w5": weights.w5_growth, "version": weights.version,
    }
    behavioral_unavailable = dev.embedding_status != "ready"
    now = datetime.now(timezone.utc)
    scored: list[tuple[MatchRecord, str]] = []

    for proj in proj_rows:
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
        ms = compute_match_score(
            w1=weights.w1_skill, w2=weights.w2_workstyle, w3=weights.w3_motivation,
            w4=weights.w4_timezone, w5=weights.w5_growth,
            skill_score=skill, workstyle_score=ws, motivation_score=mot,
            timezone_score=tz, growth_score=gr,
        )
        risks = generate_risks(
            timezone_score=tz, skill_score=skill, workstyle_score=ws,
            dev_timezone=dev.timezone or "", project_timezone_overlap=proj.timezone_overlap_required or "",
            developer_skills=dev.skills or [], project_required_skills=proj.required_skills or [],
        )
        growth = generate_growth_potential_list(
            career_goals=dev.career_goals or [], growth_opportunities=proj.growth_opportunities or [],
            growth_score=gr,
        )
        stub = generate_stub_explanation(
            skill_score=skill, workstyle_score=ws, motivation_score=mot, growth_score=gr,
            developer_skills=dev.skills or [], project_required_skills=proj.required_skills or [],
            developer_career_goals=dev.career_goals or [], project_growth_opportunities=proj.growth_opportunities or [],
        )

        rec = existing_by_proj.get(proj.id)
        if rec is not None:
            # Update in place — never delete (FK cascade would drop feedback records).
            rec.match_score, rec.skill_score, rec.workstyle_score = ms, skill, ws
            rec.motivation_score, rec.timezone_score, rec.growth_score = mot, tz, gr
            rec.risks, rec.growth_potential = risks, growth
            rec.weights_snapshot = weights_snapshot
            rec.vector_search_degraded = degraded
            rec.behavioral_data_unavailable = behavioral_unavailable
            rec.timestamp = now
            if rec.explanation_source in ("stub_pending", "stub_permanent"):
                rec.explanation = stub  # keep any Claude-cached explanation intact
        else:
            rec = MatchRecord(
                id=uuid.uuid4(), developer_id=dev.id, project_id=proj.id,
                match_score=ms, skill_score=skill, workstyle_score=ws, motivation_score=mot,
                timezone_score=tz, growth_score=gr, explanation=stub,
                explanation_source="stub_pending", risks=risks, growth_potential=growth,
                weights_snapshot=weights_snapshot, vector_search_degraded=degraded,
                behavioral_data_unavailable=behavioral_unavailable, timestamp=now,
            )
            db.add(rec)
        scored.append((rec, proj.name or "Project"))

    await db.flush()

    scored.sort(key=lambda rp: rp[0].match_score, reverse=True)
    if req.min_score is not None:
        scored = [rp for rp in scored if rp[0].match_score >= req.min_score]
    top = scored[: req.top_k]

    return DeveloperMatchesResponse(
        developer_id=developer_id,
        matches=[_match_record_to_response(r, project_name=n) for r, n in top],
        total=len(scored),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /developers/{id}/similar  (WS-B4 — ANN over the 10k developer set, §1 mobility)
# ─────────────────────────────────────────────────────────────────────────────

class SimilarDeveloperItem(BaseModel):
    """Peer summary — AC8-safe (no raw work_style / motivation vectors)."""
    developer_id: uuid.UUID
    skills: list[str]
    experience_years: int
    timezone: str


class SimilarDevelopersResponse(BaseModel):
    developer_id: uuid.UUID
    similar: list[SimilarDeveloperItem]
    vector_search_degraded: bool


@router.get("/{developer_id}/similar", response_model=SimilarDevelopersResponse)
async def get_similar_developers(
    developer_id: uuid.UUID,
    top_k: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> SimilarDevelopersResponse:
    """Nearest peers by behavioral embedding (ANN over developer_embeddings) — the
    genuine 10k-scale ANN path, and the basis for internal-mobility suggestions (§1).
    Raw behavioral vectors are never returned (AC8)."""
    from src.engine.retrieval import ann_similar_developers, VectorSearchDegraded

    if current_user.role == "developer" and current_user.developer_profile_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    dev = await db.get(DeveloperProfile, developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {developer_id} not found")

    degraded = False
    try:
        sim_ids = await ann_similar_developers(db, developer_id, top_n=top_k)
    except VectorSearchDegraded:
        degraded, sim_ids = True, []

    if not sim_ids:
        return SimilarDevelopersResponse(
            developer_id=developer_id, similar=[], vector_search_degraded=degraded
        )

    rows = (
        await db.execute(select(DeveloperProfile).where(DeveloperProfile.id.in_(sim_ids)))
    ).scalars().all()
    by_id = {d.id: d for d in rows}
    similar = [
        SimilarDeveloperItem(
            developer_id=i, skills=by_id[i].skills or [],
            experience_years=by_id[i].experience_years, timezone=by_id[i].timezone,
        )
        for i in sim_ids if i in by_id  # preserve ANN distance order
    ]
    return SimilarDevelopersResponse(
        developer_id=developer_id, similar=similar, vector_search_degraded=degraded
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /developers  (WS-B5 — paginated, filterable roster for 10k+ scale)
# ─────────────────────────────────────────────────────────────────────────────

class DeveloperListItem(BaseModel):
    """Roster row — AC8-safe (no raw work_style / motivation vectors)."""
    developer_id: uuid.UUID
    display_name: Optional[str]
    skills: list[str]
    experience_years: int
    timezone: str
    availability_hours: int
    embedding_status: str
    burnout_risk_badge: Optional[str]
    bench_risk_badge: Optional[str]


class DeveloperListResponse(BaseModel):
    items: list[DeveloperListItem]
    total: int
    limit: int
    offset: int
    next_offset: Optional[int]


@router.get("", response_model=DeveloperListResponse)
async def list_developers(
    limit: int = 50,
    offset: int = 0,
    skill: Optional[str] = None,
    timezone: Optional[str] = None,
    embedding_status: Optional[str] = None,
    risk_badge: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "display_name",
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> DeveloperListResponse:
    """Paginated, filterable developer roster (manager/admin). Server-side
    pagination + indexed filters (skill via JSONB GIN, timezone, embedding_status,
    cached risk badge, display_name search) keep this O(page) at 10k+ developers.
    Raw behavioral vectors are never returned (AC8)."""
    if current_user.role not in ("manager", "admin"):
        raise HTTPException(status_code=403, detail="Manager or admin role required")

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    conditions = []
    if skill:
        conditions.append(DeveloperProfile.skills.contains([skill]))  # JSONB @> (GIN-indexed)
    if timezone:
        conditions.append(DeveloperProfile.timezone == timezone)
    if embedding_status:
        conditions.append(DeveloperProfile.embedding_status == embedding_status)
    if risk_badge:
        conditions.append(
            or_(
                DeveloperProfile.burnout_risk_badge == risk_badge,
                DeveloperProfile.bench_risk_badge == risk_badge,
            )
        )
    if search:
        conditions.append(DeveloperProfile.display_name.ilike(f"%{search}%"))

    count_stmt = select(func.count()).select_from(DeveloperProfile)
    page_stmt = select(DeveloperProfile)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        page_stmt = page_stmt.where(*conditions)

    total = int((await db.execute(count_stmt)).scalar() or 0)

    sort_col = {
        "display_name": DeveloperProfile.display_name,
        "experience_years": DeveloperProfile.experience_years,
    }.get(sort, DeveloperProfile.display_name)
    rows = (
        await db.execute(
            page_stmt.order_by(sort_col, DeveloperProfile.id).limit(limit).offset(offset)
        )
    ).scalars().all()

    items = [
        DeveloperListItem(
            developer_id=d.id,
            display_name=d.display_name,
            skills=d.skills or [],
            experience_years=d.experience_years,
            timezone=d.timezone,
            availability_hours=d.availability_hours,
            embedding_status=d.embedding_status,
            burnout_risk_badge=d.burnout_risk_badge,
            bench_risk_badge=d.bench_risk_badge,
        )
        for d in rows
    ]
    next_offset = offset + limit if (offset + limit) < total else None
    return DeveloperListResponse(
        items=items, total=total, limit=limit, offset=offset, next_offset=next_offset
    )
