"""ETL pipeline package for the NEURAL SYNC data ingestion service.

Exports:
  * :class:`IngestionSummary` — aggregated outcome of an ingestion batch.
  * :class:`DraftProfile` — preview record returned before commit.
  * :func:`run_ingestion` — async function that orchestrates enrichment,
    skipping, and (optionally) persistence of :class:`SourceDocument` records.
"""
from .orchestrator import DraftProfile, IngestionSummary, run_ingestion

__all__ = ["DraftProfile", "IngestionSummary", "run_ingestion"]
