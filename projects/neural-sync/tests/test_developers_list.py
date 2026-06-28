"""Tests for the paginated developer roster endpoint GET /developers (WS-B5)."""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from tests.conftest import MGR_USER_ID, MockAsyncSession, MockDeveloperProfile, dev_auth_headers


def _roster_dev(i: int) -> MockDeveloperProfile:
    d = MockDeveloperProfile(dev_id=uuid.uuid4())
    d.display_name = f"Dev {i}"
    d.burnout_risk_badge = "high" if i == 0 else "low"
    d.bench_risk_badge = "low"
    d.embedding_status = "ready"
    return d


async def _get(session, path, role="manager"):
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user, TokenPayload

    async def override_db():
        yield session

    def override_user():
        if role == "manager":
            return TokenPayload(sub=MGR_USER_ID, role="manager", developer_profile_id=None)
        return TokenPayload(sub="cccccccc-cccc-cccc-cccc-cccccccccccc", role="developer",
                            developer_profile_id="cccccccc-cccc-cccc-cccc-cccccccccccc")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path, headers=dev_auth_headers())
    finally:
        app.dependency_overrides.clear()


async def test_list_returns_paginated_roster():
    session = MockAsyncSession()
    session.queue_execute(value=7)                                  # count(*)
    session.queue_execute(all_values=[_roster_dev(0), _roster_dev(1)])  # page rows
    resp = await _get(session, "/api/v1/developers?limit=2&offset=0")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 7
    assert body["limit"] == 2
    assert body["next_offset"] == 2          # more pages remain
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert "display_name" in item and "burnout_risk_badge" in item
    # AC8: no raw behavioral vectors in the roster payload
    assert "work_style" not in item and "motivation_vector" not in item


async def test_list_last_page_has_no_next_offset():
    session = MockAsyncSession()
    session.queue_execute(value=2)                                  # count(*)
    session.queue_execute(all_values=[_roster_dev(0), _roster_dev(1)])
    resp = await _get(session, "/api/v1/developers?limit=50&offset=0")
    assert resp.status_code == 200, resp.text
    assert resp.json()["next_offset"] is None


async def test_list_forbidden_for_developer_role():
    session = MockAsyncSession()
    resp = await _get(session, "/api/v1/developers", role="developer")
    assert resp.status_code == 403
