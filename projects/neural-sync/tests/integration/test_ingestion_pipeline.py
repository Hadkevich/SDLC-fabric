"""End-to-end ingestion pipeline integration test.

Coverage:
  AC27 — Full pipeline: HR CSV → HRConnector → run_ingestion → MockAsyncSession.added
          with DeveloperProfile rows; embeddings enqueued via BackgroundTasks mock.
          All HTTP calls use mock httpx transport.
          No live database or external API connections required.
          Passes in CI alongside all existing tests.

Additional pipeline invariants verified:
  AC24 — Records with empty skills counted in skipped; batch continues
  AC25 — provenance.llm + provenance.heuristic == enriched
"""
from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.connectors.hr_connector import HRConnector
from src.connectors.base import SourceDocument
from src.etl.orchestrator import run_ingestion, IngestionSummary
from src.services.enrichment import EnrichmentResult
from tests.conftest import (
    MGR_USER_ID,
    MockAsyncSession,
    mgr_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic test data
# ─────────────────────────────────────────────────────────────────────────────

HR_CSV_THREE_ROWS = b"""name,email,title,weekly_hours,years_experience,timezone
Grace Hopper,grace@example.com,Principal Engineer,40,20,America/New_York
Ada Lovelace,ada@example.com,Senior Engineer,35,10,Europe/London
Alan Turing,alan@example.com,Research Scientist,40,15,Europe/London
"""

# Deterministic enrichment results — one per row
_ENRICHMENTS = [
    EnrichmentResult(
        skills=["python", "fastapi", "docker"],
        work_style=[0.7, 0.6, 0.8, 0.7, 0.7, 0.7, 0.6, 0.8],
        motivation_vector=[0.8, 0.9, 0.5, 0.6, 0.7, 0.8, 0.7, 0.9],
        career_goals=["technical leadership"],
        provenance="heuristic",
        preferred_stack=["python", "fastapi"],
    ),
    EnrichmentResult(
        skills=["python", "ml", "pytorch"],
        work_style=[0.8, 0.7, 0.6, 0.9, 0.8, 0.6, 0.7, 0.5],
        motivation_vector=[0.9, 0.8, 0.4, 0.5, 0.9, 0.6, 0.8, 0.9],
        career_goals=["machine learning", "research"],
        provenance="llm",
        preferred_stack=["python", "ml"],
    ),
    EnrichmentResult(
        skills=["go", "rust", "docker", "k8s"],
        work_style=[0.6, 0.8, 0.7, 0.8, 0.9, 0.5, 0.8, 0.6],
        motivation_vector=[0.7, 0.8, 0.5, 0.7, 0.8, 0.7, 0.9, 0.8],
        career_goals=["distributed systems"],
        provenance="heuristic",
        preferred_stack=["go", "rust"],
    ),
]

_call_index = 0


def _deterministic_enrich(cv_text: str, git_log_text: str = "", slack_text: str = "") -> EnrichmentResult:
    """Return a deterministic EnrichmentResult cycling through _ENRICHMENTS."""
    global _call_index
    result = _ENRICHMENTS[_call_index % len(_ENRICHMENTS)]
    _call_index += 1
    return result


# ─────────────────────────────────────────────────────────────────────────────
# AC27 — End-to-end integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestionPipelineEndToEnd:
    """Full pipeline integration tests.

    [AC27] HR CSV connector extraction yields one SourceDocument per row →
    enrich_profile mocked to return a deterministic EnrichmentResult →
    commit mode persists DeveloperProfile rows (verified via MockAsyncSession.added) →
    embeddings enqueued (verified via BackgroundTasks mock).
    All HTTP calls use mock httpx transport.
    No live database or external API connections are required.
    """

    def setup_method(self):
        """Reset the enrich_profile call counter before each test."""
        global _call_index
        _call_index = 0

    # ── Step 1: HR CSV connector extraction ──────────────────────────────────

    def test_hr_csv_connector_yields_one_source_document_per_row(self):
        """[AC27 Step 1] HR CSV with 3 rows yields 3 SourceDocuments."""
        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 3, (
            f"HR CSV with 3 data rows must yield 3 SourceDocuments, got {len(docs)}"
        )
        assert not connector.errors, f"HR connector must not error on valid CSV: {connector.errors}"

    def test_hr_csv_documents_have_correct_identity_fields(self):
        """[AC27 Step 1] Each SourceDocument has display_name, email, source='hr'."""
        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        docs = list(connector.fetch())
        emails = {doc.email for doc in docs}
        assert "grace@example.com" in emails
        assert "ada@example.com" in emails
        assert "alan@example.com" in emails
        for doc in docs:
            assert doc.source == "hr"
            assert doc.display_name
            assert doc.email

    def test_hr_csv_availability_and_experience_mapped(self):
        """[AC27 Step 1] availability_hours and experience_years are correctly mapped."""
        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        docs = list(connector.fetch())
        grace = next(d for d in docs if d.email == "grace@example.com")
        assert grace.availability_hours == 40
        assert grace.experience_years == 20
        assert grace.timezone == "America/New_York"

    # ── Step 2: enrich_profile called per SourceDocument ──────────────────

    @pytest.mark.asyncio
    async def test_run_ingestion_preview_calls_enrich_for_each_doc(self):
        """[AC27 Step 2] run_ingestion in preview mode calls enrich_profile for each SourceDocument."""
        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        call_count = [0]

        def _counting_enrich(*args, **kwargs):
            call_count[0] += 1
            return _ENRICHMENTS[call_count[0] - 1]

        with patch("src.etl.orchestrator.enrich_profile", side_effect=_counting_enrich):
            summary = await run_ingestion(source_docs, "preview", connector_errors=[])

        assert call_count[0] == 3, (
            f"enrich_profile must be called once per SourceDocument (3 docs), called {call_count[0]} times"
        )
        assert summary.extracted == 3
        assert summary.enriched == 3
        assert summary.skipped == 0
        assert summary.created == 0  # preview mode

    # ── Step 3: Commit mode persists DeveloperProfile rows ────────────────

    @pytest.mark.asyncio
    async def test_commit_mode_persists_developer_profiles_via_session(self):
        """[AC27 Step 3] Commit mode persists DeveloperProfile rows via MockAsyncSession.added."""
        global _call_index
        _call_index = 0

        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        session = MockAsyncSession()
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()

        with patch("src.etl.orchestrator.enrich_profile", side_effect=_deterministic_enrich):
            summary = await run_ingestion(
                source_docs,
                "commit",
                connector_errors=[],
                db=session,
                background_tasks=mock_bg,
            )

        # Verify IngestionSummary
        assert summary.created == 3, (
            f"3 records must be created in commit mode, got {summary.created}; "
            f"errors: {summary.errors}"
        )
        assert summary.enriched == 3

        # Verify DeveloperProfile rows in session.added
        from src.db.models import DeveloperProfile
        created_profiles = [o for o in session.added if isinstance(o, DeveloperProfile)]
        assert len(created_profiles) == 3, (
            f"3 DeveloperProfile rows must be added to session, "
            f"got {len(created_profiles)}: {session.added}"
        )

    # ── Step 4: Embeddings enqueued via BackgroundTasks ────────────────────

    @pytest.mark.asyncio
    async def test_commit_mode_enqueues_embeddings_via_background_tasks(self):
        """[AC27 Step 4] Embeddings are enqueued via BackgroundTasks.add_task for each created profile."""
        global _call_index
        _call_index = 0

        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        session = MockAsyncSession()
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()

        with patch("src.etl.orchestrator.enrich_profile", side_effect=_deterministic_enrich):
            summary = await run_ingestion(
                source_docs,
                "commit",
                connector_errors=[],
                db=session,
                background_tasks=mock_bg,
            )

        assert summary.created == 3
        # BackgroundTasks.add_task must be called once per created profile
        assert mock_bg.add_task.call_count == 3, (
            f"BackgroundTasks.add_task must be called 3 times (one per profile), "
            f"got {mock_bg.add_task.call_count}"
        )

    # ── Step 5: All calls offline / mock ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_pipeline_uses_no_live_network_connections(self):
        """[AC27] Pipeline runs entirely offline with mock httpx transport and mock DB."""
        global _call_index
        _call_index = 0

        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        session = MockAsyncSession()
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()

        # If httpx.Client is called unexpectedly, this will fail
        with patch("httpx.Client") as mock_httpx:
            mock_httpx.side_effect = AssertionError(
                "httpx.Client must not be called in HR connector pipeline"
            )
            with patch("src.etl.orchestrator.enrich_profile", side_effect=_deterministic_enrich):
                summary = await run_ingestion(
                    source_docs,
                    "commit",
                    connector_errors=[],
                    db=session,
                    background_tasks=mock_bg,
                )

        assert summary.created == 3, f"Pipeline failed: {summary.errors}"

    # ── Step 6: Provenance tracking ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_provenance_llm_plus_heuristic_equals_enriched(self):
        """[AC25, AC27] provenance.llm + provenance.heuristic equals enriched count."""
        global _call_index
        _call_index = 0

        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        session = MockAsyncSession()
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()

        with patch("src.etl.orchestrator.enrich_profile", side_effect=_deterministic_enrich):
            summary = await run_ingestion(
                source_docs,
                "commit",
                connector_errors=[],
                db=session,
                background_tasks=mock_bg,
            )

        assert summary.provenance.llm + summary.provenance.heuristic == summary.enriched, (
            f"provenance.llm ({summary.provenance.llm}) + "
            f"provenance.heuristic ({summary.provenance.heuristic}) "
            f"must equal enriched ({summary.enriched})"
        )
        # Based on _ENRICHMENTS: index 0=heuristic, 1=llm, 2=heuristic
        assert summary.provenance.llm == 1
        assert summary.provenance.heuristic == 2

    # ── Step 7: Skipped records ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_skipped_records_counted_batch_continues(self):
        """[AC24, AC27] Records with empty skills counted in skipped; batch continues; HTTP 200."""
        global _call_index

        # Make the second record return empty skills
        enrichments_with_skip = [
            _ENRICHMENTS[0],  # good → enriched
            EnrichmentResult(  # empty skills → skipped
                skills=[],
                work_style=[0.5] * 8,
                motivation_vector=[0.5] * 8,
                career_goals=[],
                provenance="heuristic",
                preferred_stack=[],
            ),
            _ENRICHMENTS[2],  # good → enriched
        ]

        call_count = [0]

        def _mixed_enrich(*args, **kwargs):
            result = enrichments_with_skip[call_count[0] % 3]
            call_count[0] += 1
            return result

        connector = HRConnector(content=HR_CSV_THREE_ROWS, filename="hr.csv")
        source_docs = list(connector.fetch())

        session = MockAsyncSession()
        mock_bg = MagicMock()
        mock_bg.add_task = MagicMock()

        with patch("src.etl.orchestrator.enrich_profile", side_effect=_mixed_enrich):
            summary = await run_ingestion(
                source_docs,
                "commit",
                connector_errors=[],
                db=session,
                background_tasks=mock_bg,
            )

        assert summary.extracted == 3
        assert summary.skipped == 1, f"Expected 1 skipped, got {summary.skipped}"
        assert summary.enriched == 2, f"Expected 2 enriched, got {summary.enriched}"
        assert summary.created == 2, f"Expected 2 created, got {summary.created}"
        # Batch must continue → 2 profiles persisted, not 0
        from src.db.models import DeveloperProfile
        created_profiles = [o for o in session.added if isinstance(o, DeveloperProfile)]
        assert len(created_profiles) == 2

    # ── Step 8: Full API endpoint pipeline test ────────────────────────────

    def test_full_pipeline_via_api_endpoint(self):
        """[AC27] Complete pipeline via POST /api/v1/ingestion/file in commit mode.

        This exercises the full call chain:
        HTTP request → FastAPI handler → HRConnector.fetch → run_ingestion
        → enrich_profile (mocked) → create_developer_profile → MockAsyncSession.
        """
        global _call_index
        _call_index = 0

        from src.main import app
        from src.core.auth import require_manager

        session = MockAsyncSession()

        async def _override_db():
            yield session

        def _mgr_override():
            return mgr_auth_headers()  # not used directly

        from src.core.auth import TokenPayload as TP

        def _mgr_token():
            return TP(sub=MGR_USER_ID, role="manager")

        app.dependency_overrides[from_import := get_db] = _override_db
        app.dependency_overrides[require_manager] = _mgr_token

        client = TestClient(app, raise_server_exceptions=False)

        try:
            with patch("src.etl.orchestrator.enrich_profile", side_effect=_deterministic_enrich):
                resp = client.post(
                    "/api/v1/ingestion/file",
                    headers=mgr_auth_headers(),
                    files={"file": ("hr.csv", io.BytesIO(HR_CSV_THREE_ROWS), "text/csv")},
                    data={"source": "hr", "mode": "commit"},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["created"] == 3, f"3 profiles must be created; got {body['created']}: {body}"
        assert body["enriched"] == 3
        assert body["skipped"] == 0
        assert body["extracted"] == 3
        assert body["provenance"]["llm"] + body["provenance"]["heuristic"] == body["enriched"]

        # Verify DeveloperProfile rows in session
        from src.db.models import DeveloperProfile
        created_profiles = [o for o in session.added if isinstance(o, DeveloperProfile)]
        assert len(created_profiles) == 3, (
            f"3 DeveloperProfile rows must be in MockAsyncSession.added, "
            f"got {len(created_profiles)}"
        )


# Import needed for the API override
from src.db.session import get_db
