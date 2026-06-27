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

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user, require_manager
from src.db.models import (
    AllocationRecord,
    DeveloperProfile,
    ErasureAuditLog,
    ExplanationCache,
    FeedbackRecord,
    MatchRecord,
    UserAccount,
)
from src.db.session import get_db
from src.engine.risk import AllocationSlice, compute_risk_scores

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


async def _enqueue_embeddings(dev: DeveloperProfile) -> None:
    """Schedule background embedding generation for the developer profile."""
    from src.engine.embeddings import generate_developer_embeddings
    # In production this would be dispatched to an async worker queue.
    # For Phase 1, we call directly (fire-and-forget pattern shown here).
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
        return vecs
    except Exception:
        return None


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

    dev = DeveloperProfile(
        id=dev_id,
        skills=payload.skills,
        experience_years=payload.experience_years,
        preferred_stack=payload.preferred_stack,
        work_style_vector=payload.work_style,
        motivation_vector=payload.motivation_vector,
        timezone=payload.timezone,
        availability_hours=payload.availability_hours,
        career_goals=payload.career_goals,
        project_history=[h.model_dump() for h in payload.project_history],
        is_behavioral_self_reported=payload.is_self_reported,
        embedding_status="pending",
    )
    db.add(dev)
    await db.flush()

    background_tasks.add_task(_enqueue_embeddings, dev)
    return _profile_to_response(dev)


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
    if current_user.role == "developer" and current_user.user_id != str(developer_id):
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

    if current_user.role == "developer" and current_user.user_id != str(developer_id):
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
    background_tasks.add_task(_enqueue_embeddings, dev)
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
    if current_user.role == "developer" and current_user.user_id != str(developer_id):
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

    # motivation_alignment_factor defaults to 0.0 (unknown/worst-case)
    scores = compute_risk_scores(slices)

    return RiskResponse(
        developer_id=developer_id,
        burnout_risk_score=scores.burnout_risk_score,
        bench_risk_score=scores.bench_risk_score,
        burnout_risk_badge=scores.burnout_risk_badge,
        bench_risk_badge=scores.bench_risk_badge,
        computed_at=scores.computed_at,
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

    if current_user.role == "developer" and current_user.user_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    query = (
        select(MatchRecord)
        .where(MatchRecord.developer_id == developer_id)
        .order_by(MatchRecord.match_score.desc())
    )
    if min_score is not None:
        query = query.where(MatchRecord.match_score >= min_score)

    total_result = await db.execute(
        select(MatchRecord).where(MatchRecord.developer_id == developer_id)
    )
    total = len(total_result.scalars().all())

    query = query.limit(top_k)
    result = await db.execute(query)
    records = result.scalars().all()

    from src.api.matches import _match_record_to_response, DeveloperMatchesResponse
    return DeveloperMatchesResponse(
        developer_id=developer_id,
        matches=[_match_record_to_response(r) for r in records],
        total=total,
    )
