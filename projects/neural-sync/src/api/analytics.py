"""Analytics endpoints — rejection rate and match statistics.

GET /analytics/rejection-rate?developer_id={id}   — per-developer rejection ratio (AC10)
GET /analytics/team-rejection-rate?team_id={id}   — team-level aggregation
GET /analytics/match-stats?developer_id={id}      — total/accepted/rejected counts

AC10: POST /matches/feedback stores FeedbackRecord; this endpoint returns
      rejection_ratio computed from stored feedback with ≥1 sample.

Minimum sample floor: rejection_ratio returns null when sample_count < MIN_SAMPLES
(default 10, configurable via REJECTION_RATE_MIN_SAMPLES env var) to prevent
statistically unreliable data from reaching the Manager Dashboard.

AC8: Raw behavioral vectors and motivation scalars are NEVER returned.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user
from src.core.settings import settings
from src.db.models import FeedbackRecord, MatchRecord
from src.db.session import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class RejectionRateResponse(BaseModel):
    developer_id: uuid.UUID
    rejection_ratio: Optional[float]
    sample_count: int
    min_sample_floor_met: bool
    accepted_count: int
    rejected_count: int


class TeamMemberRejectionRate(BaseModel):
    developer_id: uuid.UUID
    rejection_ratio: Optional[float]
    sample_count: int
    min_sample_floor_met: bool


class TeamRejectionRateResponse(BaseModel):
    team_id: uuid.UUID
    members: list[TeamMemberRejectionRate]
    team_average_rejection_ratio: Optional[float]


class MatchStatsResponse(BaseModel):
    developer_id: uuid.UUID
    total_matches: int
    accepted_count: int
    rejected_count: int
    rejection_ratio: Optional[float]
    min_sample_floor_met: bool


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_rejection_stats(db: AsyncSession, developer_id: uuid.UUID) -> dict:
    """
    Compute rejection statistics for a developer.
    Returns: {accepted_count, rejected_count, sample_count, rejection_ratio, min_sample_floor_met}
    """
    result = await db.execute(
        select(
            func.count().label("total"),
            func.sum(
                case((FeedbackRecord.accepted == False, 1), else_=0)  # noqa: E712
            ).label("rejected"),
        ).where(FeedbackRecord.developer_id == developer_id)
    )
    row = result.one()
    total = int(row.total or 0)
    rejected = int(row.rejected or 0)
    accepted = total - rejected

    min_floor = settings.rejection_rate_min_samples
    floor_met = total >= min_floor

    rejection_ratio = (rejected / total) if floor_met and total > 0 else None

    return {
        "sample_count": total,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "min_sample_floor_met": floor_met,
        "rejection_ratio": round(rejection_ratio, 6) if rejection_ratio is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/rejection-rate", response_model=RejectionRateResponse)
async def get_rejection_rate(
    developer_id: uuid.UUID = Query(..., description="UUID of the developer"),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> RejectionRateResponse:
    """
    Compute rejection_ratio = count(accepted=false) / count(*) from FeedbackRecord.

    Returns rejection_ratio=null when sample_count < REJECTION_RATE_MIN_SAMPLES (default 10).
    Developers may only query their own rejection rate.
    """
    if current_user.role == "developer" and current_user.user_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Developers may only query their own rejection rate")

    stats = await _get_rejection_stats(db, developer_id)

    return RejectionRateResponse(
        developer_id=developer_id,
        rejection_ratio=stats["rejection_ratio"],
        sample_count=stats["sample_count"],
        min_sample_floor_met=stats["min_sample_floor_met"],
        accepted_count=stats["accepted_count"],
        rejected_count=stats["rejected_count"],
    )


@router.get("/team-rejection-rate", response_model=TeamRejectionRateResponse)
async def get_team_rejection_rate(
    team_id: uuid.UUID = Query(..., description="Team identifier"),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> TeamRejectionRateResponse:
    """Team-level rejection rate aggregation. Manager role required."""
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    # Get all developers with feedback records (team scoping simplified for Phase 1)
    result = await db.execute(
        select(FeedbackRecord.developer_id).distinct()
    )
    dev_ids = [row[0] for row in result.all()]

    members: list[TeamMemberRejectionRate] = []
    valid_ratios: list[float] = []

    for dev_id in dev_ids:
        stats = await _get_rejection_stats(db, dev_id)
        member = TeamMemberRejectionRate(
            developer_id=dev_id,
            rejection_ratio=stats["rejection_ratio"],
            sample_count=stats["sample_count"],
            min_sample_floor_met=stats["min_sample_floor_met"],
        )
        members.append(member)
        if stats["rejection_ratio"] is not None:
            valid_ratios.append(stats["rejection_ratio"])

    team_avg = sum(valid_ratios) / len(valid_ratios) if valid_ratios else None

    return TeamRejectionRateResponse(
        team_id=team_id,
        members=members,
        team_average_rejection_ratio=round(team_avg, 6) if team_avg is not None else None,
    )


@router.get("/match-stats", response_model=MatchStatsResponse)
async def get_match_stats(
    developer_id: uuid.UUID = Query(..., description="Developer identifier"),
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MatchStatsResponse:
    """Return match count and feedback statistics for a developer."""
    if current_user.role == "developer" and current_user.user_id != str(developer_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Total match records
    match_result = await db.execute(
        select(func.count()).where(MatchRecord.developer_id == developer_id)
    )
    total_matches = int(match_result.scalar() or 0)

    # Feedback stats
    stats = await _get_rejection_stats(db, developer_id)

    return MatchStatsResponse(
        developer_id=developer_id,
        total_matches=total_matches,
        accepted_count=stats["accepted_count"],
        rejected_count=stats["rejected_count"],
        rejection_ratio=stats["rejection_ratio"],
        min_sample_floor_met=stats["min_sample_floor_met"],
    )
