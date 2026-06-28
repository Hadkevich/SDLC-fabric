"""Tests for admin/manager allocation override endpoints (WS-E2, Task04 §6)."""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    MGR_USER_ID,
    MockAsyncSession,
    MockDeveloperProfile,
    MockProjectProfile,
    mgr_auth_headers,
)

DEV_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJ_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


async def _post(session, path, json, role="manager"):
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user, TokenPayload

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role=role, developer_profile_id=None)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=json, headers=mgr_auth_headers())
    finally:
        app.dependency_overrides.clear()


def _alloc_body(**over):
    body = {
        "developer_id": str(DEV_ID),
        "project_id": str(PROJ_ID),
        "start_date": "2026-01-01",
        "end_date": "2026-06-01",
        "workload_intensity": 0.7,
        "is_active": True,
    }
    body.update(over)
    return body


async def test_admin_creates_allocation():
    from src.db.models import AllocationRecord

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_ID, MockDeveloperProfile(dev_id=DEV_ID))
    session.set_get("ProjectProfile", PROJ_ID, MockProjectProfile(proj_id=PROJ_ID))

    resp = await _post(session, "/api/v1/admin/allocations", _alloc_body(), role="admin")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["developer_id"] == str(DEV_ID)
    assert body["project_id"] == str(PROJ_ID)
    assert body["workload_intensity"] == 0.7
    assert sum(isinstance(o, AllocationRecord) for o in session.added) == 1


async def test_manager_may_also_create_allocation():
    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_ID, MockDeveloperProfile(dev_id=DEV_ID))
    session.set_get("ProjectProfile", PROJ_ID, MockProjectProfile(proj_id=PROJ_ID))
    resp = await _post(session, "/api/v1/admin/allocations", _alloc_body(), role="manager")
    assert resp.status_code == 201, resp.text


async def test_developer_forbidden():
    session = MockAsyncSession()
    resp = await _post(session, "/api/v1/admin/allocations", _alloc_body(), role="developer")
    assert resp.status_code == 403


async def test_end_before_start_rejected():
    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_ID, MockDeveloperProfile(dev_id=DEV_ID))
    resp = await _post(
        session, "/api/v1/admin/allocations",
        _alloc_body(start_date="2026-06-01", end_date="2026-01-01"), role="admin",
    )
    assert resp.status_code == 422


async def test_unknown_developer_404():
    session = MockAsyncSession()  # no set_get → db.get returns None
    resp = await _post(session, "/api/v1/admin/allocations", _alloc_body(), role="admin")
    assert resp.status_code == 404
