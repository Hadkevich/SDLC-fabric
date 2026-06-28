"""ETL orchestrator for the NEURAL SYNC data ingestion pipeline.

Coordinates the enrichment and (optionally) persistence of
:class:`~src.connectors.base.SourceDocument` records produced by the five
source connectors.

Pipeline stages
---------------
1. **Batch cap** — Truncates the batch to ``INGESTION_MAX_RECORDS`` before
   processing begins; surplus records are noted in
   :attr:`IngestionSummary.errors` [AC28].
2. **Enrich** — Calls :func:`~src.services.enrichment.enrich_profile` off the
   event loop via ``asyncio.to_thread`` for each :class:`SourceDocument`
   [AC23].
3. **Skip** — Records where :func:`enrich_profile` returns an empty skills
   list are counted in :attr:`IngestionSummary.skipped` and no
   :class:`~src.db.models.DeveloperProfile` is created; remaining records
   continue processing [AC24].
4. **Preview mode** (``mode='preview'``) — Populates
   :attr:`IngestionSummary.drafts` with enriched profile data;
   ``created == 0``; nothing is persisted [AC15].
5. **Commit mode** (``mode='commit'``) — Persists
   :class:`~src.db.models.DeveloperProfile` rows via the shared
   :func:`~src.core.helpers.create_developer_profile` helper and enqueues
   embeddings via :class:`~fastapi.BackgroundTasks` [AC16].
6. **Provenance** — Aggregates :attr:`IngestionSummary.provenance` from each
   :class:`~src.services.enrichment.EnrichmentResult`; ``provenance.llm +
   provenance.heuristic`` always equals :attr:`IngestionSummary.enriched`
   [AC25].
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastapi import BackgroundTasks
    from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.base import SourceDocument
from src.core.settings import settings
from src.services.enrichment import EnrichmentResult, enrich_profile

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Output data models  (also re-exported from src/etl/__init__.py)
# ─────────────────────────────────────────────────────────────────────────────

class Provenance(BaseModel):
    """Counts of records enriched via LLM vs heuristic path.

    Invariant: ``llm + heuristic == IngestionSummary.enriched`` [AC25].
    """

    llm: int = 0
    heuristic: int = 0


class DraftProfile(BaseModel):
    """A preview draft of an enriched developer profile.

    Returned in :attr:`IngestionSummary.drafts` in both preview and commit
    modes so the frontend DraftReviewTable always has data to render [AC15].
    """

    external_id: str
    display_name: str
    email: str
    source: Optional[str] = None

    # Enriched signals
    skills: list[str] = Field(default_factory=list)
    preferred_stack: list[str] = Field(default_factory=list)
    work_style: list[float] = Field(default_factory=list)
    motivation_vector: list[float] = Field(default_factory=list)
    career_goals: list[str] = Field(default_factory=list)

    # Source text channels (optional; included for human review)
    cv_text: str = ""
    git_log_text: Optional[str] = None
    slack_text: Optional[str] = None

    # Optional profile metadata from the source
    timezone: Optional[str] = None
    availability_hours: Optional[int] = None
    experience_years: Optional[int] = None

    # 'llm' or 'heuristic'
    provenance: str = "heuristic"


class IngestionSummary(BaseModel):
    """Aggregated outcome of an ingestion batch.

    Invariants:
    - ``skipped + enriched == extracted``  (after cap enforcement)
    - ``provenance.llm + provenance.heuristic == enriched``  [AC25]
    - ``created == 0`` in preview mode [AC15]
    - ``created >= 1`` in commit mode on success [AC16]
    """

    extracted: int = 0
    enriched: int = 0
    skipped: int = 0
    created: int = 0
    provenance: Provenance = Field(default_factory=Provenance)
    errors: list[str] = Field(default_factory=list)
    drafts: list[DraftProfile] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _infer_experience_years(cv_text: str) -> int:
    """Infer years of experience from free-text CV; default 3."""
    yrs = [
        int(n)
        for n in re.findall(r"(\d{1,2})\s*\+?\s*years?\b", cv_text, re.IGNORECASE)
    ]
    return min(max(yrs), 60) if yrs else 3


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator function
# ─────────────────────────────────────────────────────────────────────────────

async def run_ingestion(
    source_docs: list[SourceDocument],
    mode: Literal["preview", "commit"],
    *,
    connector_errors: Optional[list[str]] = None,
    db: Optional["AsyncSession"] = None,
    background_tasks: Optional["BackgroundTasks"] = None,
    max_records: Optional[int] = None,
) -> IngestionSummary:
    """Run the ETL pipeline on *source_docs* and return an :class:`IngestionSummary`.

    Args:
        source_docs: Documents produced by a connector's ``fetch()`` call.
        mode: ``"preview"`` or ``"commit"``.
        connector_errors: Error strings from the connector (forwarded to
            :attr:`IngestionSummary.errors`).
        db: AsyncSession for commit mode; should be ``None`` in preview mode.
        background_tasks: FastAPI :class:`BackgroundTasks` for embedding
            enqueue in commit mode; should be ``None`` in preview mode.
        max_records: Override for the batch size cap.  Defaults to
            ``settings.ingestion_max_records``.

    Returns:
        An :class:`IngestionSummary` with all outcome counters populated.
    """
    cap = max_records if max_records is not None else settings.ingestion_max_records
    summary = IngestionSummary()

    # Forward any errors the connector already collected
    if connector_errors:
        summary.errors.extend(connector_errors)

    # ── Enforce per-batch record cap before processing begins ─────────────
    if len(source_docs) > cap:
        dropped = len(source_docs) - cap
        summary.errors.append(
            f"Batch truncated: {dropped} record(s) beyond the "
            f"INGESTION_MAX_RECORDS cap ({cap}) were dropped."
        )
        source_docs = source_docs[:cap]

    summary.extracted = len(source_docs)

    # ── Process each SourceDocument ───────────────────────────────────────
    for doc in source_docs:
        # Call enrich_profile off the event loop [AC23]
        try:
            result: EnrichmentResult = await asyncio.to_thread(
                enrich_profile,
                doc.cv_text or "",
                doc.git_log_text or "",
                doc.slack_text or "",
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"enrich_profile error for '{doc.external_id}': {exc}"
            summary.errors.append(msg)
            logger.warning(msg)
            summary.skipped += 1
            continue

        # Skip records with no extractable skills [AC24]
        if not result.skills:
            logger.debug(
                "Skipping '%s': enrich_profile returned empty skills list.",
                doc.external_id,
            )
            summary.skipped += 1
            continue

        summary.enriched += 1

        # Track provenance counts; invariant: llm + heuristic == enriched [AC25]
        if result.provenance == "llm":
            summary.provenance.llm += 1
        else:
            summary.provenance.heuristic += 1

        # Derive experience years from cv_text when not provided by the source
        exp_years = doc.experience_years or _infer_experience_years(doc.cv_text or "")
        # Clamp to DB constraint
        exp_years = max(0, min(60, exp_years))

        avail_hours = doc.availability_hours or 40
        # Clamp to DB constraint
        avail_hours = max(1, min(168, avail_hours))

        draft = DraftProfile(
            external_id=doc.external_id,
            display_name=doc.display_name,
            email=doc.email,
            source=doc.source,
            skills=result.skills,
            preferred_stack=result.preferred_stack,
            work_style=result.work_style,
            motivation_vector=result.motivation_vector,
            career_goals=result.career_goals,
            cv_text=doc.cv_text or "",
            git_log_text=doc.git_log_text or None,
            slack_text=doc.slack_text or None,
            timezone=doc.timezone,
            availability_hours=avail_hours,
            experience_years=exp_years,
            provenance=result.provenance,
        )
        summary.drafts.append(draft)

        # ── Commit mode: persist DeveloperProfile via the shared helper ───
        if mode == "commit":
            if db is None or background_tasks is None:
                summary.errors.append(
                    f"Commit mode: missing db session or BackgroundTasks for "
                    f"'{doc.external_id}'; record not persisted."
                )
                continue

            try:
                from src.core.helpers import create_developer_profile  # local import avoids circularity

                await create_developer_profile(
                    db,
                    background_tasks,
                    skills=result.skills,
                    experience_years=exp_years,
                    preferred_stack=result.preferred_stack,
                    work_style=result.work_style,
                    motivation_vector=result.motivation_vector,
                    timezone_str=doc.timezone or "UTC",
                    availability_hours=avail_hours,
                    career_goals=result.career_goals or ["general software development"],
                    project_history=[],
                    is_self_reported=False,
                )
                summary.created += 1
            except Exception as exc:  # noqa: BLE001
                msg = (
                    f"Failed to create DeveloperProfile for '{doc.external_id}': {exc}"
                )
                summary.errors.append(msg)
                logger.warning(msg)

    return summary
