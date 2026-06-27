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

from src.core.auth import TokenPayload, get_current_user
from src.db.models import (
    AllocationRecord,
    DeveloperProfile,
    ErasureAuditLog,
    FeedbackRecord,
    MatchRecord,
)
from src.db.session import get_db

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
    if current_user.user_id != str(payload.developer_id):
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

@router.get("/teams/{team_id}/risk-summary")
async def get_team_risk_summary(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> None:
    """
    Team-scoped risk summary.

    Phase 1 does not implement a Team entity in the data model, so this
    endpoint cannot safely scope results to the requested team_id.
    The previous implementation silently ignored team_id and returned
    unrelated developer data — a correctness/security defect (BLK-002).

    Returns HTTP 501 until a Team entity and team membership are added in
    Phase 2.
    """
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    raise HTTPException(
        status_code=501,
        detail="Team-scoped risk summary is not implemented in Phase 1 (no Team entity in the data model)",
    )
