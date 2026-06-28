"""Tests for GET /projects list endpoint (Admin View project manager, Task04 §2.2/§6)."""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    MGR_USER_ID,
    MockAsyncSession,
    MockProjectProfile,
    mgr_auth_headers,
)

PROJ_A = uuid.UUID("22222222-2222-2222-2222-222222222222")
PROJ_B = uuid.UUID("22222222-2222-2222-2222-222222222223")


async def _get(session, path, role="manager"):
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
            return await client.get(path, headers=mgr_auth_headers())
    finally:
        app.dependency_overrides.clear()


async def test_manager_lists_projects():
    session = MockAsyncSession()
    session.queue_execute(all_values=[MockProjectProfile(PROJ_A), MockProjectProfile(PROJ_B)])

    resp = await _get(session, "/api/v1/projects", role="manager")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {p["id"] for p in body} == {str(PROJ_A), str(PROJ_B)}
    # AC8: no raw behavioral/embedding vectors leak through the project list
    assert "vector" not in body[0]
    assert "embedding" not in body[0]


async def test_admin_lists_projects():
    session = MockAsyncSession()
    session.queue_execute(all_values=[MockProjectProfile(PROJ_A)])
    resp = await _get(session, "/api/v1/projects", role="admin")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


async def test_developer_forbidden_from_listing_projects():
    session = MockAsyncSession()
    resp = await _get(session, "/api/v1/projects", role="developer")
    assert resp.status_code == 403


async def test_empty_project_list():
    session = MockAsyncSession()
    session.queue_execute(all_values=[])
    resp = await _get(session, "/api/v1/projects", role="admin")
    assert resp.status_code == 200
    assert resp.json() == []
