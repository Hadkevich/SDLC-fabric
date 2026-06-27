"""Project profile CRUD endpoints. Manager role required for write operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import TokenPayload, get_current_user
from src.db.models import ProjectProfile
from src.db.session import get_db

router = APIRouter(prefix="/projects", tags=["projects"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class ProjectProfileCreate(BaseModel):
    id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    required_skills: list[str] = Field(..., min_length=1)
    team_structure: object  # string or dict
    workload_intensity: float = Field(..., ge=0.0, le=1.0)
    innovation_level: float = Field(..., ge=0.0, le=1.0)
    timezone_overlap_required: str
    duration_weeks: int = Field(..., ge=1)
    growth_opportunities: list[str] = Field(default_factory=list)


class ProjectProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    required_skills: list[str]
    team_structure: object
    workload_intensity: float
    innovation_level: float
    timezone_overlap_required: str
    duration_weeks: int
    growth_opportunities: list[str]
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_response(proj: ProjectProfile) -> ProjectProfileResponse:
    return ProjectProfileResponse(
        id=proj.id,
        name=proj.name or "",
        required_skills=proj.required_skills or [],
        team_structure=proj.team_structure,
        workload_intensity=proj.workload_intensity,
        innovation_level=proj.innovation_level,
        timezone_overlap_required=proj.timezone_overlap_required,
        duration_weeks=proj.duration_weeks,
        growth_opportunities=proj.growth_opportunities or [],
        created_at=proj.created_at,
        updated_at=proj.updated_at,
    )


async def _enqueue_project_embedding(proj: ProjectProfile) -> None:
    """Background: generate skill embedding for the project."""
    from src.engine.embeddings import generate_project_embedding
    try:
        generate_project_embedding(
            project_id=str(proj.id),
            required_skills=proj.required_skills or [],
            team_structure=proj.team_structure,
            growth_opportunities=proj.growth_opportunities or [],
            innovation_level=proj.innovation_level,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectProfileResponse)
async def create_project(
    payload: ProjectProfileCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ProjectProfileResponse:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required to create projects")

    proj_id = payload.id or uuid.uuid4()
    existing = await db.get(ProjectProfile, proj_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Project {proj_id} already exists")

    proj = ProjectProfile(
        id=proj_id,
        name=payload.name or "Unnamed Project",
        required_skills=payload.required_skills,
        team_structure=payload.team_structure,
        workload_intensity=payload.workload_intensity,
        innovation_level=payload.innovation_level,
        timezone_overlap_required=payload.timezone_overlap_required,
        duration_weeks=payload.duration_weeks,
        growth_opportunities=payload.growth_opportunities,
    )
    db.add(proj)
    await db.flush()

    background_tasks.add_task(_enqueue_project_embedding, proj)
    return _to_response(proj)


@router.get("/{project_id}", response_model=ProjectProfileResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ProjectProfileResponse:
    proj = await db.get(ProjectProfile, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return _to_response(proj)


@router.put("/{project_id}", response_model=ProjectProfileResponse)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectProfileCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ProjectProfileResponse:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    proj = await db.get(ProjectProfile, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    proj.name = payload.name or proj.name
    proj.required_skills = payload.required_skills
    proj.team_structure = payload.team_structure
    proj.workload_intensity = payload.workload_intensity
    proj.innovation_level = payload.innovation_level
    proj.timezone_overlap_required = payload.timezone_overlap_required
    proj.duration_weeks = payload.duration_weeks
    proj.growth_opportunities = payload.growth_opportunities
    proj.updated_at = datetime.now(timezone.utc)

    await db.flush()
    background_tasks.add_task(_enqueue_project_embedding, proj)
    return _to_response(proj)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> None:
    if current_user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required")

    proj = await db.get(ProjectProfile, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    await db.delete(proj)
