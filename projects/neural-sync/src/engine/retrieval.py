"""pgvector ANN candidate retrieval (WS-B3 — Task04 §5 vector DB / §8 scale).

Selects candidate projects (or peer developers) by embedding **cosine distance** so the
deterministic five-dimension scorer in ``matching.py`` runs only over a bounded candidate
set instead of the whole table. This is what puts the pgvector store on the critical path
and makes "scalable to 10k+ developers" real rather than aspirational.

Design invariant: ANN only chooses *which* candidates to score — it never changes *how*
they score. ``matching.py`` is untouched, so every deterministic matching acceptance test
still holds. On any failure (missing embedding, query timeout, degraded vector store) the
helper raises :class:`VectorSearchDegraded`; callers fall back to a bounded relational scan
and set ``vector_search_degraded=True`` on the resulting records.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import settings
from src.db.models import DeveloperEmbedding, ProjectEmbedding


class VectorSearchDegraded(Exception):
    """ANN retrieval could not run (no embedding / timeout / vector-store error).

    Callers catch this and fall back to a relational scan, marking the produced
    MatchRecords ``vector_search_degraded=True`` so the degradation is observable.
    """


def _timeout_seconds() -> float:
    # vector_search_timeout_ms is the architecture's ANN budget (ADR-003 / §8 latency).
    return max(0.05, settings.vector_search_timeout_ms / 1000.0)


def _dev_vector_subquery(developer_id: uuid.UUID, embedding_type: str):
    """Scalar subquery selecting one developer embedding vector of the given type."""
    return (
        select(DeveloperEmbedding.vector)
        .where(
            DeveloperEmbedding.developer_id == developer_id,
            DeveloperEmbedding.embedding_type == embedding_type,
        )
        .limit(1)
        .scalar_subquery()
    )


async def ann_candidate_projects(
    db: AsyncSession,
    developer_id: uuid.UUID,
    top_n: int = 50,
    embedding_type: str = "skill",
) -> list[uuid.UUID]:
    """Return up to ``top_n`` project ids nearest to the developer's skill embedding.

    Raises :class:`VectorSearchDegraded` on timeout, store error, or when no candidate
    comes back (e.g. the developer has no ready embedding yet).
    """
    dev_vec = _dev_vector_subquery(developer_id, embedding_type)
    stmt = (
        select(ProjectEmbedding.project_id)
        .where(ProjectEmbedding.embedding_type == "skill")
        .order_by(ProjectEmbedding.vector.cosine_distance(dev_vec))
        .limit(top_n)
    )
    try:
        result = await asyncio.wait_for(db.execute(stmt), timeout=_timeout_seconds())
    except Exception as exc:  # timeout, missing pgvector op, store error → degrade
        raise VectorSearchDegraded(f"ANN project search failed: {exc}") from exc

    ids = [row[0] for row in result.all()]
    if not ids:
        raise VectorSearchDegraded(
            "ANN returned no candidate projects (developer embedding missing/not ready)"
        )
    return ids


async def ann_similar_developers(
    db: AsyncSession,
    developer_id: uuid.UUID,
    top_n: int = 10,
    embedding_type: str = "behavioral",
) -> list[uuid.UUID]:
    """Return up to ``top_n`` developer ids nearest to the given developer's embedding
    (excluding self). This is the genuine 10k-scale ANN proof: it ranks over the whole
    ``developer_embeddings`` set. Raises :class:`VectorSearchDegraded` on failure.
    """
    dev_vec = _dev_vector_subquery(developer_id, embedding_type)
    stmt = (
        select(DeveloperEmbedding.developer_id)
        .where(
            DeveloperEmbedding.embedding_type == embedding_type,
            DeveloperEmbedding.developer_id != developer_id,
        )
        .order_by(DeveloperEmbedding.vector.cosine_distance(dev_vec))
        .limit(top_n)
    )
    try:
        result = await asyncio.wait_for(db.execute(stmt), timeout=_timeout_seconds())
    except Exception as exc:
        raise VectorSearchDegraded(f"ANN developer search failed: {exc}") from exc

    return [row[0] for row in result.all()]
