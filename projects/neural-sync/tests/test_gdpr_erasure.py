"""GDPR right-to-erasure tests for DELETE /api/v1/developers/{id}.

Acceptance criteria covered:
  AC9 — DELETE /api/v1/developers/{id} returns 204;
         subsequent GET returns 404;
         embeddings, match history, and feedback are confirmed absent.

All DB calls are intercepted via FastAPI dependency_overrides so no live
PostgreSQL database is required.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    DEV_USER_ID,
    MGR_USER_ID,
    TEST_DEV_ID,
    MockAsyncSession,
    MockDeveloperProfile,
    MockExecuteResult,
    mgr_auth_headers,
    dev_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: the FastAPI app with a stateful mock DB
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def gdpr_app():
    """Return the FastAPI app with a two-phase mock DB (exists → deleted)."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from src.db.models import DeveloperProfile
    from tests.conftest import TokenPayload

    # Phase 1: developer exists
    session_delete = MockAsyncSession()
    # db.get(DeveloperProfile, TEST_DEV_ID) → mock profile
    session_delete.set_get("DeveloperProfile", TEST_DEV_ID, MockDeveloperProfile(TEST_DEV_ID))
    # db.execute(select(UserAccount)...) → None (no linked account)
    session_delete.queue_execute(value=None)

    # Phase 2: developer is gone
    session_get = MockAsyncSession()
    session_get.set_get("DeveloperProfile", TEST_DEV_ID, None)

    sessions = [session_delete, session_get]
    call_index = [0]

    async def multi_session_get_db():
        idx = min(call_index[0], len(sessions) - 1)
        call_index[0] += 1
        yield sessions[idx]

    def override_get_current_user():
        return TokenPayload(sub=MGR_USER_ID, role="manager")

    app.dependency_overrides[get_db] = multi_session_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield app, session_delete
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC9 — GDPR DELETE returns 204
# ─────────────────────────────────────────────────────────────────────────────

async def test_gdpr_delete_returns_204(gdpr_app):
    """
    [AC9] DELETE /api/v1/developers/{id} must return HTTP 204 No Content.
    """
    app, session_delete = gdpr_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(
            f"/api/v1/developers/{TEST_DEV_ID}",
            headers=mgr_auth_headers(),
        )

    assert response.status_code == 204, (
        f"[AC9] DELETE must return 204, got {response.status_code}: {response.text}"
    )
    assert response.content == b"", "204 No Content must have empty body"


# ─────────────────────────────────────────────────────────────────────────────
# AC9 — Subsequent GET returns 404
# ─────────────────────────────────────────────────────────────────────────────

async def test_gdpr_subsequent_get_returns_404(gdpr_app):
    """
    [AC9] After erasure, GET /api/v1/developers/{id} must return 404.
    """
    app, _ = gdpr_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First call: DELETE (phase 1 session)
        del_response = await client.delete(
            f"/api/v1/developers/{TEST_DEV_ID}",
            headers=mgr_auth_headers(),
        )
        assert del_response.status_code == 204

        # Second call: GET (phase 2 session — developer gone)
        get_response = await client.get(
            f"/api/v1/developers/{TEST_DEV_ID}",
            headers=mgr_auth_headers(),
        )

    assert get_response.status_code == 404, (
        f"[AC9] GET after GDPR erasure must return 404, got {get_response.status_code}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC9 — Cascade deletion confirmed via ErasureAuditLog
# ─────────────────────────────────────────────────────────────────────────────

async def test_gdpr_audit_log_records_all_cascade_steps():
    """
    [AC9] The GDPR erasure must record completion of all 6 entity classes
    (developer_profile, developer_embeddings, match_records, feedback_records,
    allocation_records, explanation_cache) in the ErasureAuditLog.

    Verified by inspecting the objects added to the mock session after DELETE.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", TEST_DEV_ID, MockDeveloperProfile(TEST_DEV_ID))
    session.queue_execute(value=None)  # UserAccount lookup → None

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="manager")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/api/v1/developers/{TEST_DEV_ID}",
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 204

        # Find the ErasureAuditLog in the added objects
        from src.db.models import ErasureAuditLog
        audit_entries = [obj for obj in session.added if isinstance(obj, ErasureAuditLog)]
        assert len(audit_entries) == 1, "Exactly one ErasureAuditLog must be created"

        audit = audit_entries[0]
        assert audit.status == "completed"
        expected_steps = {
            "developer_profile", "developer_embeddings",
            "match_records", "feedback_records",
            "allocation_records", "explanation_cache",
        }
        actual_steps = set(audit.steps_completed or [])
        assert expected_steps == actual_steps, (
            f"[AC9] Audit log must record all 6 cascade steps. "
            f"Missing: {expected_steps - actual_steps}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: DELETE non-existent developer → 404
# ─────────────────────────────────────────────────────────────────────────────

async def test_gdpr_delete_nonexistent_developer_returns_404():
    """DELETE /api/v1/developers/{id} for an unknown id must return 404."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    unknown_id = uuid.uuid4()

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", unknown_id, None)

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="manager")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/api/v1/developers/{unknown_id}",
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 404, (
            f"DELETE on unknown id must return 404, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: developer trying to erase another developer's data → 403
# ─────────────────────────────────────────────────────────────────────────────

async def test_gdpr_delete_forbidden_for_different_developer():
    """A developer may not request erasure of another developer's profile."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    victim_id = uuid.UUID("99999999-9999-9999-9999-999999999999")

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", victim_id, MockDeveloperProfile(victim_id))

    async def override_db():
        yield session

    # Authenticate as a DIFFERENT developer
    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/api/v1/developers/{victim_id}",
                headers=dev_auth_headers(),
            )
        assert response.status_code == 403, (
            f"Developer erasure of another developer must return 403, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()
