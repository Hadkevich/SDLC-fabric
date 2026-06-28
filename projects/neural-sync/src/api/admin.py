"""Admin / system-override endpoints (Task04 §6 Admin View).

Manual allocation management is the concrete "system override": a manager/admin assigns or
moves a developer onto a project, confirms a reallocation suggestion, or benches someone.
Allocations drive the risk engine (bench/burnout), so editing them reshapes the next risk
refresh and the reallocation suggestions — i.e. this is the human-in-the-loop control over
the optimization engine the spec calls for.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, require_admin
from src.db.models import AllocationRecord, DeveloperProfile, ProjectProfile
from src.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class AllocationCreate(BaseModel):
    developer_id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    start_date: date
    end_date: date
    workload_intensity: float = Field(..., ge=0.0, le=1.0)
    is_active: bool = True


class AllocationUpdate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    workload_intensity: Optional[float] = Field(None, ge=0.0, le=1.0)
    is_active: Optional[bool] = None


class AllocationResponse(BaseModel):
    id: uuid.UUID
    developer_id: uuid.UUID
    project_id: Optional[uuid.UUID]
    start_date: date
    end_date: date
    workload_intensity: float
    is_active: bool


def _to_resp(a: AllocationRecord) -> AllocationResponse:
    return AllocationResponse(
        id=a.id, developer_id=a.developer_id, project_id=a.project_id,
        start_date=a.start_date, end_date=a.end_date,
        workload_intensity=a.workload_intensity, is_active=a.is_active,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes (manager OR admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/allocations", status_code=status.HTTP_201_CREATED, response_model=AllocationResponse)
async def create_allocation(
    payload: AllocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_admin),
) -> AllocationResponse:
    """Assign a developer to a project (or record a bench period with project_id=null)."""
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")
    dev = await db.get(DeveloperProfile, payload.developer_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Developer {payload.developer_id} not found")
    if payload.project_id is not None:
        proj = await db.get(ProjectProfile, payload.project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail=f"Project {payload.project_id} not found")

    alloc = AllocationRecord(
        id=uuid.uuid4(), developer_id=payload.developer_id, project_id=payload.project_id,
        start_date=payload.start_date, end_date=payload.end_date,
        workload_intensity=payload.workload_intensity, is_active=payload.is_active,
    )
    db.add(alloc)
    await db.flush()
    return _to_resp(alloc)


@router.put("/allocations/{allocation_id}", response_model=AllocationResponse)
async def update_allocation(
    allocation_id: uuid.UUID,
    payload: AllocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_admin),
) -> AllocationResponse:
    """Override an existing allocation (move project, change dates/intensity, bench/un-bench)."""
    alloc = await db.get(AllocationRecord, allocation_id)
    if alloc is None:
        raise HTTPException(status_code=404, detail=f"Allocation {allocation_id} not found")

    if payload.project_id is not None:
        proj = await db.get(ProjectProfile, payload.project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail=f"Project {payload.project_id} not found")
        alloc.project_id = payload.project_id
    if payload.start_date is not None:
        alloc.start_date = payload.start_date
    if payload.end_date is not None:
        alloc.end_date = payload.end_date
    if payload.workload_intensity is not None:
        alloc.workload_intensity = payload.workload_intensity
    if payload.is_active is not None:
        alloc.is_active = payload.is_active

    if alloc.end_date < alloc.start_date:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")

    await db.flush()
    return _to_resp(alloc)


@router.delete("/allocations/{allocation_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_allocation(
    allocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_admin),
) -> None:
    alloc = await db.get(AllocationRecord, allocation_id)
    if alloc is None:
        raise HTTPException(status_code=404, detail=f"Allocation {allocation_id} not found")
    await db.delete(alloc)


@router.get("/developers/{developer_id}/allocations", response_model=list[AllocationResponse])
async def list_developer_allocations(
    developer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(require_admin),
) -> list[AllocationResponse]:
    rows = (
        await db.execute(
            select(AllocationRecord)
            .where(AllocationRecord.developer_id == developer_id)
            .order_by(AllocationRecord.start_date)
        )
    ).scalars().all()
    return [_to_resp(a) for a in rows]
