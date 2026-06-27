"""Feedback and rejection-rate analytics tests.

Acceptance criteria covered:
  AC10 — POST /api/v1/matches/feedback stores the feedback record;
          GET /api/v1/analytics/rejection-rate?developer_id={id}
          returns the correct rejection ratio with ≥1 sample.

All DB calls are intercepted via FastAPI dependency_overrides.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    DEV_USER_ID,
    MGR_USER_ID,
    TEST_DEV_ID,
    TEST_FEEDBACK_ID,
    TEST_MATCH_ID,
    MockAsyncSession,
    MockMatchRecord,
    MockExecuteResult,
    MockRow,
    dev_auth_headers,
    mgr_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# AC10 — POST /matches/feedback stores a FeedbackRecord
# ─────────────────────────────────────────────────────────────────────────────

async def test_feedback_stores_record_and_returns_201():
    """
    [AC10] POST /api/v1/matches/feedback must store the feedback record
    and return HTTP 201 with the correct fields.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    mock_match = MockMatchRecord(
        match_id=TEST_MATCH_ID,
        dev_id=uuid.UUID(DEV_USER_ID),
    )

    session = MockAsyncSession()
    # db.get(MatchRecord, match_id) → existing match
    session.set_get("MatchRecord", TEST_MATCH_ID, mock_match)
    # db.execute(select(FeedbackRecord)...) → None (no duplicate)
    session.queue_execute(value=None)

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer", developer_profile_id=DEV_USER_ID)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches/feedback",
                json={
                    "developer_id": DEV_USER_ID,
                    "match_id": str(TEST_MATCH_ID),
                    "accepted": False,
                    "comment": "Not the right project for me",
                },
                headers=dev_auth_headers(),
            )

        assert response.status_code == 201, (
            f"[AC10] POST /matches/feedback must return 201, got {response.status_code}: "
            f"{response.text}"
        )
        body = response.json()
        assert "id" in body, "Response must contain feedback id"
        assert body["developer_id"] == DEV_USER_ID
        assert body["match_id"] == str(TEST_MATCH_ID)
        assert body["accepted"] is False
        assert "feedback_timestamp" in body

        # Verify the FeedbackRecord was added to the session
        from src.db.models import FeedbackRecord
        feedback_objs = [obj for obj in session.added if isinstance(obj, FeedbackRecord)]
        assert len(feedback_objs) == 1, "[AC10] Exactly one FeedbackRecord must be added"
        assert feedback_objs[0].accepted is False
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC10 — GET /analytics/rejection-rate returns correct ratio
# ─────────────────────────────────────────────────────────────────────────────

async def test_rejection_rate_returns_correct_ratio_with_sample():
    """
    [AC10] GET /analytics/rejection-rate?developer_id={id} must return:
      - rejection_ratio = rejected_count / total
      - min_sample_floor_met = True when sample_count ≥ 10
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    dev_id = uuid.UUID(DEV_USER_ID)

    session = MockAsyncSession()
    # analytics query returns: total=15, rejected=6
    session.queue_execute(
        value=None,
        row=MockRow(total=15, rejected=6),
    )

    async def override_db():
        yield session

    def override_user():
        # Manager can query any developer's rejection rate
        return TokenPayload(sub=MGR_USER_ID, role="manager")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/analytics/rejection-rate?developer_id={dev_id}",
                headers=mgr_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC10] GET rejection-rate must return 200, got {response.status_code}: "
            f"{response.text}"
        )
        body = response.json()
        assert body["developer_id"] == str(dev_id)
        assert body["sample_count"] == 15
        assert body["accepted_count"] == 9
        assert body["rejected_count"] == 6
        assert body["min_sample_floor_met"] is True
        assert body["rejection_ratio"] is not None
        assert abs(body["rejection_ratio"] - 6 / 15) < 1e-4, (
            f"[AC10] rejection_ratio should be {6/15:.4f}, got {body['rejection_ratio']}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Rejection rate: null ratio below minimum sample floor
# ─────────────────────────────────────────────────────────────────────────────

async def test_rejection_rate_null_below_min_sample_floor():
    """
    When sample_count < REJECTION_RATE_MIN_SAMPLES (default 10),
    rejection_ratio must be null and min_sample_floor_met must be False.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    dev_id = uuid.UUID(DEV_USER_ID)

    session = MockAsyncSession()
    # Only 3 samples — below the floor of 10
    session.queue_execute(
        value=None,
        row=MockRow(total=3, rejected=1),
    )

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
            response = await client.get(
                f"/api/v1/analytics/rejection-rate?developer_id={dev_id}",
                headers=mgr_auth_headers(),
            )

        assert response.status_code == 200
        body = response.json()
        assert body["min_sample_floor_met"] is False
        assert body["rejection_ratio"] is None, (
            "rejection_ratio must be null when below the minimum sample floor"
        )
        assert body["sample_count"] == 3
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: POST feedback for non-existent match → 404
# ─────────────────────────────────────────────────────────────────────────────

async def test_feedback_nonexistent_match_returns_404():
    """POST /matches/feedback with unknown match_id must return 404."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    nonexistent_match_id = uuid.uuid4()

    session = MockAsyncSession()
    # db.get(MatchRecord, ...) → None (match not found)
    # MockAsyncSession.get returns None by default for unknown keys

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer", developer_profile_id=DEV_USER_ID)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches/feedback",
                json={
                    "developer_id": DEV_USER_ID,
                    "match_id": str(nonexistent_match_id),
                    "accepted": True,
                },
                headers=dev_auth_headers(),
            )
        assert response.status_code == 404, (
            f"Feedback for unknown match must return 404, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: POST feedback with missing required field → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_feedback_missing_accepted_field_returns_422():
    """POST /matches/feedback missing the 'accepted' boolean must return 422."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer", developer_profile_id=DEV_USER_ID)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches/feedback",
                json={
                    "developer_id": DEV_USER_ID,
                    "match_id": str(TEST_MATCH_ID),
                    # "accepted" is intentionally omitted
                },
                headers=dev_auth_headers(),
            )
        assert response.status_code == 422, (
            f"Missing 'accepted' field must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: GET rejection-rate without developer_id → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_rejection_rate_missing_developer_id_returns_422():
    """GET /analytics/rejection-rate without developer_id must return 422."""
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

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
            response = await client.get(
                "/api/v1/analytics/rejection-rate",  # no developer_id param
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 422, (
            f"Missing developer_id must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# BLK-002: manager authz bypass — POST feedback for another developer → 403
# ─────────────────────────────────────────────────────────────────────────────

async def test_feedback_manager_cannot_submit_for_other_developer():
    """
    [BLK-002] POST /matches/feedback: a manager submitting feedback with
    developer_id != their own JWT sub must receive HTTP 403.
    The authz rule applies regardless of role (no role exception).
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        # Manager's user_id is MGR_USER_ID; payload will carry DEV_USER_ID → mismatch
        return TokenPayload(sub=MGR_USER_ID, role="manager")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches/feedback",
                json={
                    "developer_id": DEV_USER_ID,  # different from MGR_USER_ID
                    "match_id": str(TEST_MATCH_ID),
                    "accepted": True,
                },
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 403, (
            f"[BLK-002] Manager submitting feedback for another developer must return 403, "
            f"got {response.status_code}: {response.text}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# BLK-001 fix: GET /teams/{team_id}/risk-summary now returns 200 + TeamRiskSummary
# ─────────────────────────────────────────────────────────────────────────────

async def test_team_risk_summary_returns_200_team_risk_summary():
    """
    GET /api/v1/teams/{team_id}/risk-summary must return HTTP 200 with a
    TeamRiskSummary payload (BLK-001 resolved). Phase 1 fallback: all
    DeveloperProfile records are returned (no Team entity yet).
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    team_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    session = MockAsyncSession()

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
            response = await client.get(
                f"/api/v1/teams/{team_id}/risk-summary",
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 200, (
            f"GET /teams/{{team_id}}/risk-summary must return 200 (BLK-001 fixed), "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "team_id" in body
        assert "member_count" in body
        assert "members" in body
        assert "risk_distribution" in body
        assert body["team_id"] == team_id
    finally:
        app.dependency_overrides.clear()
