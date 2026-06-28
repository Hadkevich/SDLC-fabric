"""Tests for the real re-optimization primitives + the opt-in scheduler (WS-C).

Replaces the previously-stubbed admin endpoints' behavior. All DB calls go through the
MockAsyncSession FIFO queue; the matching/risk engines run for real (they are pure).
"""
from __future__ import annotations

from src.services.reoptimization import (
    reembed_all_developers,
    refresh_all_risk_scores,
    rescore_all_matches,
)
from src.services.scheduler import reopt_interval_seconds, start_scheduler
from tests.conftest import (
    MockAsyncSession,
    MockDeveloperProfile,
    MockMatchRecord,
    MockProjectProfile,
    MockWeightConfig,
)


async def test_refresh_writes_risk_cache():
    """A developer with no active allocation is benched → cache gets bench badge 'high'."""
    session = MockAsyncSession()
    dev = MockDeveloperProfile()
    dev.allocation_records = []  # no allocations → benched
    session.queue_execute(all_values=[dev])

    n = await refresh_all_risk_scores(session)
    assert n == 1
    assert dev.bench_risk_badge == "high"          # benched → bench risk 1.0
    assert dev.bench_risk_score == 1.0
    assert dev.burnout_risk_badge in ("low", "medium", "high")
    assert dev.risk_computed_at is not None


async def test_rescore_updates_match_in_place():
    """rescore recomputes match_score against current weights, updating the record."""
    session = MockAsyncSession()
    match = MockMatchRecord()
    match.match_score = 0.0  # stale value the rescore must overwrite
    session.queue_execute(value=MockWeightConfig())        # weights singleton
    session.queue_execute(all_values=[match])              # all match records
    session.queue_execute(all_values=[MockDeveloperProfile()])  # referenced developers
    session.queue_execute(all_values=[MockProjectProfile()])    # referenced projects

    n = await rescore_all_matches(session)
    assert n == 1
    assert 0.0 < match.match_score <= 1.0                  # recomputed, non-trivial
    assert match.weights_snapshot["version"] == 1


async def test_reembed_marks_ready_and_upserts():
    """reembed regenerates skill+behavioral embeddings and flips status to ready."""
    from src.db.models import DeveloperEmbedding

    session = MockAsyncSession()
    dev = MockDeveloperProfile()
    session.queue_execute(all_values=[dev])   # all developers
    session.queue_execute(value=None)         # existing 'skill' embedding lookup → none
    session.queue_execute(value=None)         # existing 'behavioral' embedding lookup → none

    n = await reembed_all_developers(session)
    assert n == 1
    assert dev.embedding_status == "ready"
    added = [o for o in session.added if isinstance(o, DeveloperEmbedding)]
    assert len(added) == 2                     # skill + behavioral
    assert all(len(e.vector) == 1536 for e in added)


def test_scheduler_disabled_by_default(monkeypatch):
    """Without NEURAL_SYNC_REOPT_INTERVAL the loop stays off (returns None, no apscheduler)."""
    monkeypatch.delenv("NEURAL_SYNC_REOPT_INTERVAL", raising=False)
    assert reopt_interval_seconds() == 0
    assert start_scheduler() is None
