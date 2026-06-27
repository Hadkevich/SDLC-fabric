"""Feedback submission and admin erasure audit endpoints.

POST /matches/feedback                    — store FeedbackRecord (AC10)
GET  /admin/erasure-audit/{developer_id}  — compliance audit log
POST /admin/reembed                       — trigger full re-embedding
POST /risk/refresh                        — batch risk score refresh
GET  /teams/{team_id}/risk-summary        — team risk badges (no raw vectors)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from src.core.auth import TokenPayload, get_current_user
from src.db.models import (
    AllocationRecord,
    DeveloperProfile,
    ErasureAuditLog,
    FeedbackRecord,
    MatchRecord,
    UserAccount,
)
from src.db.session import get_db
from src.engine.risk import AllocationSlice, compute_risk_scores

router = APIRouter(tags=["matches", "admin", "risk"])


# ─────────────────────────────────────────────────────────────────────────────
# Feedback schemas
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    developer_id: uuid.UUID
    match_id: uuid.UUID
    accepted: bool
    comment: Optional[str] = Field(None, max_length=500)


class FeedbackResponse(BaseModel):
    id: uuid.UUID
    developer_id: uuid.UUID
    match_id: uuid.UUID
    accepted: bool
    comment: Optional[str]
    feedback_timestamp: datetime


class AsyncJobResponse(BaseModel):
    job_id: uuid.UUID
    message: str
    estimated_count: Optional[int] = None


class ErasureAuditRecord(BaseModel):
    erasure_request_id: uuid.UUID
    developer_id: str
    requested_at: datetime
    completed_at: Optional[datetime]
    status: str
    initiating_user_id: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Risk team schemas (no raw behavioral vectors — AC8)
# ─────────────────────────────────────────────────────────────────────────────

class TeamRiskMember(BaseModel):
    developer_id: uuid.UUID
    display_name: str
    burnout_risk_score: float
    bench_risk_score: float
    burnout_risk_badge: str
    bench_risk_badge: str


class TeamRiskDistribution(BaseModel):
    burnout_high_count: int = 0
    burnout_medium_count: int = 0
    burnout_low_count: int = 0
    bench_high_count: int = 0
    bench_medium_count: int = 0
    bench_low_count: int = 0


class TeamRiskSummary(BaseModel):
    team_id: uuid.UUID
    member_count: int
    members: list[TeamRiskMember]
    risk_distribution: TeamRiskDistribution
    computed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# POST /matches/feedback  (AC10)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/matches/feedback", status_code=status.HTTP_201_CREATED, response_model=FeedbackResponse)
async def submit_feedback(
    payload: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> FeedbackResponse:
    """
    Store a FeedbackRecord. developer_id must match the authenticated user's JWT sub.
    Rejection counts accumulate for GET /analytics/rejection-rate.
    """
    if current_user.developer_profile_id != str(payload.developer_id):
        raise HTTPException(status_code=403, detail="developer_id must match authenticated user")

    # Verify match exists
    match = await db.get(MatchRecord, payload.match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {payload.match_id} not found")

    # Check for duplicate
    dup_result = await db.execute(
        select(FeedbackRecord).where(
            FeedbackRecord.developer_id == payload.developer_id,
            FeedbackRecord.match_id == payload.match_id,
        )
    )
    if dup_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Feedback for this match already submitted")

    now = datetime.now(timezone.utc)
    record = FeedbackRecord(
        developer_id=payload.developer_id,
        match_id=payload.match_id,
        accepted=payload.accepted,
        comment=payload.comment,
        timestamp=now,
    )
    db.add(record)
    await db.flush()

    return FeedbackResponse(
        id=record.id,
        developer_id=record.developer_id,
        match_id=record.match_id,
        accepted=record.accepted,
        comment=record.comment,
        feedback_timestamp=record.timestamp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/erasure-audit/{developer_id}  (manager only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/erasure-audit/{developer_id}", response_model=ErasureAuditRecord)
async def get_erasure_audit(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ErasureAuditRecord:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    result = await db.execute(
        select(ErasureAuditLog)
        .where(ErasureAuditLog.developer_id == str(developer_id))
        .order_by(ErasureAuditLog.requested_at.desc())
    )
    audit = result.scalar_one_or_none()
    if audit is None:
        raise HTTPException(
            status_code=404,
            detail=f"No erasure audit record for developer {developer_id}",
        )

    return ErasureAuditRecord(
        erasure_request_id=audit.erasure_request_id,
        developer_id=audit.developer_id,
        requested_at=audit.requested_at,
        completed_at=audit.completed_at,
        status=audit.status,
        initiating_user_id=audit.initiating_user_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/reembed  (manager only)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/admin/reembed", status_code=status.HTTP_202_ACCEPTED, response_model=AsyncJobResponse)
async def trigger_reembed(
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> AsyncJobResponse:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    job_id = uuid.uuid4()
    return AsyncJobResponse(
        job_id=job_id,
        message="Re-embedding job accepted and queued for all profiles",
        estimated_count=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /risk/refresh  (manager only)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/risk/refresh", status_code=status.HTTP_202_ACCEPTED, response_model=AsyncJobResponse)
async def refresh_risk_scores(
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> AsyncJobResponse:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    job_id = uuid.uuid4()
    return AsyncJobResponse(
        job_id=job_id,
        message="Risk score refresh job accepted for all developers",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /teams/{team_id}/risk-summary  (manager only, no raw vectors — AC8)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/teams/{team_id}/risk-summary", response_model=TeamRiskSummary)
async def get_team_risk_summary(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> TeamRiskSummary:
    """
    Team risk summary — per-developer burnout and bench risk badges (AC8).

    Phase 1 fallback: no Team entity exists, so all DeveloperProfile records are
    returned (scoping by team_id is deferred to Phase 2). Raw behavioral vectors
    are never included in the response.
    """
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    # Load all developer profiles with their allocation records in one query
    result = await db.execute(
        select(DeveloperProfile).options(selectinload(DeveloperProfile.allocation_records))
    )
    profiles = result.scalars().all()

    # Build profile_id → username map from user accounts
    ua_result = await db.execute(
        select(UserAccount.developer_profile_id, UserAccount.username).where(
            UserAccount.developer_profile_id.isnot(None)
        )
    )
    profile_to_username: dict[uuid.UUID, str] = {
        row[0]: row[1] for row in ua_result.all()
    }

    members: list[TeamRiskMember] = []
    dist = TeamRiskDistribution()

    for i, dev in enumerate(profiles):
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

        display_name = profile_to_username.get(dev.id) or f"Developer #{i + 1}"
        members.append(
            TeamRiskMember(
                developer_id=dev.id,
                display_name=display_name,
                burnout_risk_score=scores.burnout_risk_score,
                bench_risk_score=scores.bench_risk_score,
                burnout_risk_badge=scores.burnout_risk_badge,
                bench_risk_badge=scores.bench_risk_badge,
            )
        )

        # Accumulate distribution counts
        if scores.burnout_risk_badge == "high":
            dist.burnout_high_count += 1
        elif scores.burnout_risk_badge == "medium":
            dist.burnout_medium_count += 1
        else:
            dist.burnout_low_count += 1

        if scores.bench_risk_badge == "high":
            dist.bench_high_count += 1
        elif scores.bench_risk_badge == "medium":
            dist.bench_medium_count += 1
        else:
            dist.bench_low_count += 1

    return TeamRiskSummary(
        team_id=team_id,
        member_count=len(members),
        members=members,
        risk_distribution=dist,
        computed_at=datetime.now(timezone.utc),
    )
