"""Shared create-plus-embed helper for DeveloperProfile creation.

This module provides the single shared profile-creation code path consumed by:
  * POST /api/v1/developers  (src/api/developers.py)
  * commit-mode ETL ingestion  (src/etl/orchestrator.py)

Extracting this helper into a neutral module prevents duplication and ensures
both paths always behave identically [AC16, AC23].
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeveloperProfile


async def _enqueue_embeddings(dev_id: uuid.UUID, dev_data: dict) -> None:
    """Generate embeddings and persist them; update embedding_status on the profile.

    This coroutine runs as a FastAPI background task.  It opens its own
    session so it can commit independently of the request session.
    """
    from sqlalchemy import select

    from src.db.models import DeveloperEmbedding
    from src.db.session import AsyncSessionLocal
    from src.engine.embeddings import generate_developer_embeddings

    try:
        vecs = generate_developer_embeddings(
            developer_id=str(dev_id),
            skills=dev_data["skills"],
            preferred_stack=dev_data["preferred_stack"],
            experience_years=dev_data["experience_years"],
            project_history=dev_data["project_history"],
            work_style_vector=dev_data["work_style_vector"],
            motivation_vector=dev_data["motivation_vector"],
            career_goals=dev_data["career_goals"],
        )
    except Exception:
        vecs = None

    async with AsyncSessionLocal() as session:
        try:
            profile = await session.get(DeveloperProfile, dev_id)
            if profile is None:
                return

            if vecs:
                for emb_type, vector in vecs.items():
                    if not vector:
                        continue
                    existing = await session.execute(
                        select(DeveloperEmbedding).where(
                            DeveloperEmbedding.developer_id == dev_id,
                            DeveloperEmbedding.embedding_type == emb_type,
                        )
                    )
                    row = existing.scalar_one_or_none()
                    if row:
                        row.vector = vector
                        row.updated_at = datetime.now(timezone.utc)
                    else:
                        session.add(
                            DeveloperEmbedding(
                                developer_id=dev_id,
                                embedding_type=emb_type,
                                vector=vector,
                                model_name="auto",
                                model_version="1",
                            )
                        )
                profile.embedding_status = "ready"
            else:
                profile.embedding_status = "failed"

            await session.commit()
        except Exception:
            await session.rollback()


async def create_developer_profile(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    *,
    dev_id: Optional[uuid.UUID] = None,
    skills: list[str],
    experience_years: int,
    preferred_stack: list[str],
    work_style: list[float],
    motivation_vector: list[float],
    timezone_str: str = "UTC",
    availability_hours: int = 40,
    career_goals: list[str],
    project_history: list,
    is_self_reported: bool = False,
) -> DeveloperProfile:
    """Create a DeveloperProfile row and enqueue embedding generation.

    This is the **single** profile-creation code path.  Both
    ``create_developer`` (POST /api/v1/developers) and the commit-mode
    ETL orchestrator call this function so there is exactly one place to
    update when the creation logic changes.

    The caller is responsible for committing (or rolling back) *db*.
    The ``get_db`` FastAPI dependency commits the session when the request
    handler returns successfully.

    Args:
        db: Active async SQLAlchemy session.
        background_tasks: FastAPI BackgroundTasks for embedding enqueue.
        dev_id: Optional pre-assigned UUID; a new one is generated if ``None``.
        skills: Extracted or self-reported skill list (must be non-empty).
        experience_years: Years of professional experience (0–60).
        preferred_stack: Technology preferences.
        work_style: 8-dimensional work-style vector (each element ∈ [0, 1]).
        motivation_vector: 8-dimensional motivation vector (each element ∈ [0, 1]).
        timezone_str: IANA timezone string (e.g. "America/New_York").
        availability_hours: Weekly hours available for project work (1–168).
        career_goals: Career goal statements.
        project_history: List of past project engagement dicts.
        is_self_reported: ``False`` for data-ingestion-derived profiles.

    Returns:
        The newly created :class:`DeveloperProfile` ORM instance (flushed but
        not yet committed — the caller commits the session).
    """
    dev_id = dev_id or uuid.uuid4()

    dev = DeveloperProfile(
        id=dev_id,
        skills=skills,
        experience_years=experience_years,
        preferred_stack=preferred_stack,
        work_style_vector=work_style,
        motivation_vector=motivation_vector,
        timezone=timezone_str,
        availability_hours=availability_hours,
        career_goals=career_goals,
        project_history=project_history,
        is_behavioral_self_reported=is_self_reported,
        embedding_status="pending",
    )
    db.add(dev)
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
    return dev
