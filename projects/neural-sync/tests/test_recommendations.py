"""Tests for on-demand ANN recommendations and similar-developer search (WS-B4).

All DB + ANN calls are intercepted via FastAPI dependency_overrides and the
MockAsyncSession FIFO execute queue — no live PostgreSQL/pgvector required.
The deterministic matching engine is exercised for real (it is pure), so the
recommendation scores are genuine; only the candidate retrieval is mocked.
"""
from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    DEV_USER_ID,
    MockAsyncSession,
    MockDeveloperProfile,
    MockProjectProfile,
    MockWeightConfig,
    dev_auth_headers,
)

DEV_UUID = uuid.UUID(DEV_USER_ID)
PROJ_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
PEER_ID = uuid.UUID("99999999-9999-9999-9999-999999999999")


def _override_dev_user():
    from src.core.auth import TokenPayload
    return TokenPayload(sub=DEV_USER_ID, role="developer", developer_profile_id=DEV_USER_ID)


async def _client_post(session, path, json=None):
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = _override_dev_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=json or {}, headers=dev_auth_headers())
    finally:
        app.dependency_overrides.clear()


async def _client_get(session, path):
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = _override_dev_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path, headers=dev_auth_headers())
    finally:
        app.dependency_overrides.clear()


# ── POST /developers/{id}/recommendations ────────────────────────────────────

async def test_recommendations_ann_path_ranks_and_persists():
    """ANN returns a candidate → engine scores it → a new MatchRecord is persisted."""
    from src.db.models import MatchRecord

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_UUID, MockDeveloperProfile(dev_id=DEV_UUID))
    session.queue_execute(value=MockWeightConfig())          # _load_weights
    session.queue_execute(all_values=[(PROJ_ID,)])           # ANN candidate project ids
    session.queue_execute(all_values=[MockProjectProfile(proj_id=PROJ_ID)])  # load projects
    session.queue_execute(all_values=[])                     # existing matches (none)

    resp = await _client_post(session, f"/api/v1/developers/{DEV_USER_ID}/recommendations",
                              {"top_k": 5, "candidate_pool": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert len(body["matches"]) == 1
    m = body["matches"][0]
    assert 0.0 <= m["match_score"] <= 1.0
    assert m["project_id"] == str(PROJ_ID)
    assert m["vector_search_degraded"] is False
    # a MatchRecord was actually persisted (upsert-insert path)
    assert sum(isinstance(o, MatchRecord) for o in session.added) == 1


async def test_recommendations_falls_back_when_ann_degraded():
    """Empty ANN result → relational fallback → records flagged vector_search_degraded."""
    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_UUID, MockDeveloperProfile(dev_id=DEV_UUID))
    session.queue_execute(value=MockWeightConfig())          # _load_weights
    session.queue_execute(all_values=[])                     # ANN → empty → VectorSearchDegraded
    session.queue_execute(all_values=[(PROJ_ID,)])           # fallback: select ProjectProfile.id
    session.queue_execute(all_values=[MockProjectProfile(proj_id=PROJ_ID)])  # load projects
    session.queue_execute(all_values=[])                     # existing matches (none)

    resp = await _client_post(session, f"/api/v1/developers/{DEV_USER_ID}/recommendations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["matches"][0]["vector_search_degraded"] is True


async def test_recommendations_forbidden_for_other_developer():
    """A developer may not request another developer's recommendations (AC8 spirit)."""
    session = MockAsyncSession()
    other = uuid.UUID("12121212-1212-1212-1212-121212121212")
    resp = await _client_post(session, f"/api/v1/developers/{other}/recommendations")
    assert resp.status_code == 403


# ── GET /developers/{id}/similar ─────────────────────────────────────────────

async def test_similar_returns_peers_without_raw_vectors():
    """ANN over developer embeddings returns peers; response carries no raw vectors (AC8)."""
    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_UUID, MockDeveloperProfile(dev_id=DEV_UUID))
    session.queue_execute(all_values=[(PEER_ID,)])                       # ANN similar dev ids
    session.queue_execute(all_values=[MockDeveloperProfile(dev_id=PEER_ID)])  # load peer profiles

    resp = await _client_get(session, f"/api/v1/developers/{DEV_USER_ID}/similar?top_k=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vector_search_degraded"] is False
    assert len(body["similar"]) == 1
    peer = body["similar"][0]
    assert peer["developer_id"] == str(PEER_ID)
    # AC8: raw behavioral vectors must never appear in the response
    assert "work_style" not in peer and "motivation_vector" not in peer


async def test_similar_degraded_returns_empty():
    """No ANN neighbours (empty) → empty list, not an error."""
    session = MockAsyncSession()
    session.set_get("DeveloperProfile", DEV_UUID, MockDeveloperProfile(dev_id=DEV_UUID))
    session.queue_execute(all_values=[])   # ANN returns nothing

    resp = await _client_get(session, f"/api/v1/developers/{DEV_USER_ID}/similar")
    assert resp.status_code == 200, resp.text
    assert resp.json()["similar"] == []
