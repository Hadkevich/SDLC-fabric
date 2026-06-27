"""Weight configuration endpoints.

GET  /config/weights   — return current WeightConfig
PUT  /config/weights   — update weights (manager role required)

Validation: all wi ≥ 0.0 AND |w1+w2+w3+w4+w5 − 1.0| < 0.001.
On validation failure, HTTP 400 is returned and the previous config is preserved.

The matching engine always reads from this table on every match request
(no in-process caching) to ensure weight changes propagate immediately (AC6).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user
from src.db.models import WeightConfig
from src.db.session import get_db

router = APIRouter(prefix="/config", tags=["config"])

WEIGHT_SUM_TOLERANCE = 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class WeightConfigUpdate(BaseModel):
    """All five weights must sum to 1.0 (±0.001) and each wi ≥ 0.0."""
    w1: float = Field(..., ge=0.0, le=1.0, description="Skill dimension weight")
    w2: float = Field(..., ge=0.0, le=1.0, description="Work-style dimension weight")
    w3: float = Field(..., ge=0.0, le=1.0, description="Motivation dimension weight")
    w4: float = Field(..., ge=0.0, le=1.0, description="Timezone dimension weight")
    w5: float = Field(..., ge=0.0, le=1.0, description="Growth dimension weight")

    @model_validator(mode="after")
    def validate_sum(self) -> "WeightConfigUpdate":
        total = self.w1 + self.w2 + self.w3 + self.w4 + self.w5
        if abs(total - 1.0) >= WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"Weights must sum to 1.0 (±{WEIGHT_SUM_TOLERANCE}). "
                f"Received: w1={self.w1} w2={self.w2} w3={self.w3} "
                f"w4={self.w4} w5={self.w5} sum={total:.6f}"
            )
        return self


class WeightConfigResponse(BaseModel):
    w1: float
    w2: float
    w3: float
    w4: float
    w5: float
    version: int
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_or_create_weight_config(db: AsyncSession) -> WeightConfig:
    result = await db.execute(select(WeightConfig).where(WeightConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        config = WeightConfig(
            id=1,
            w1_skill=0.30,
            w2_workstyle=0.25,
            w3_motivation=0.20,
            w4_timezone=0.15,
            w5_growth=0.10,
            version=1,
        )
        db.add(config)
        await db.flush()
    return config


def _to_response(config: WeightConfig) -> WeightConfigResponse:
    return WeightConfigResponse(
        w1=config.w1_skill,
        w2=config.w2_workstyle,
        w3=config.w3_motivation,
        w4=config.w4_timezone,
        w5=config.w5_growth,
        version=config.version,
        updated_at=config.updated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/weights", response_model=WeightConfigResponse)
async def get_weights(
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> WeightConfigResponse:
    """Return the active weight configuration."""
    config = await _get_or_create_weight_config(db)
    return _to_response(config)


@router.put("/weights", response_model=WeightConfigResponse)
async def update_weights(
    payload: WeightConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> WeightConfigResponse:
    """
    Replace the active weight configuration.

    AC6: All subsequent POST /matches calls will use the new weights immediately.
    The matching engine reads from this table on every request with no in-process cache.

    Returns HTTP 400 (preserving previous config) on constraint violation.
    Manager role required.
    """
    if current_user.role != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role required to update matching weights",
        )

    config = await _get_or_create_weight_config(db)

    # Update fields
    config.w1_skill = payload.w1
    config.w2_workstyle = payload.w2
    config.w3_motivation = payload.w3
    config.w4_timezone = payload.w4
    config.w5_growth = payload.w5
    config.version = config.version + 1
    config.updated_by = uuid.UUID(current_user.user_id) if current_user.user_id else None
    config.updated_at = datetime.now(timezone.utc)

    await db.flush()
    # ExplanationCache is implicitly invalidated: the cache_key includes the
    # weights_snapshot_hash, which changes when the version increments.

    return _to_response(config)
