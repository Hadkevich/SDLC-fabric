"""Weight configuration API tests.

Acceptance criteria covered:
  AC6 — PUT /api/v1/config/weights with a valid weight map causes subsequent
         POST /api/v1/matches calls to reflect the new weights (verified by a
         deterministic unit test with known inputs and expected score deltas);
         invalid sum returns 422; negative weight returns 422.

All DB calls are intercepted via FastAPI dependency_overrides.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    DEV_USER_ID,
    MGR_USER_ID,
    MockAsyncSession,
    MockWeightConfig,
    dev_auth_headers,
    mgr_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — GET /config/weights returns the active weight configuration
# ─────────────────────────────────────────────────────────────────────────────

async def test_get_weights_returns_defaults():
    """
    [AC6] GET /api/v1/config/weights must return the current singleton
    WeightConfig with all five weights and version.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()
    # _get_or_create_weight_config: execute → scalar_one_or_none → MockWeightConfig
    session.queue_execute(value=MockWeightConfig())

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="admin")  # weight tuning = Admin View (§6)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/config/weights",
                headers=mgr_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC6] GET /config/weights must return 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "w1" in body
        assert "w2" in body
        assert "w3" in body
        assert "w4" in body
        assert "w5" in body
        assert "version" in body
        assert "updated_at" in body
        assert abs(body["w1"] - 0.30) < 1e-6
        assert abs(body["w2"] - 0.25) < 1e-6
        assert abs(body["w3"] - 0.20) < 1e-6
        assert abs(body["w4"] - 0.15) < 1e-6
        assert abs(body["w5"] - 0.10) < 1e-6
        assert body["version"] == 1
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — PUT /config/weights with valid weights returns 200 and updates config
# ─────────────────────────────────────────────────────────────────────────────

async def test_update_weights_valid_returns_200_and_applies_change():
    """
    [AC6] PUT /api/v1/config/weights with a weight map summing to 1.0
    must return HTTP 200 and the updated weight values.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    mock_config = MockWeightConfig()
    session = MockAsyncSession()
    # _get_or_create_weight_config: execute → scalar_one_or_none → existing config
    session.queue_execute(value=mock_config)

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="admin")  # weight tuning = Admin View (§6)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/config/weights",
                json={
                    "w1": 0.40,
                    "w2": 0.25,
                    "w3": 0.20,
                    "w4": 0.10,
                    "w5": 0.05,
                },
                headers=mgr_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC6] PUT /config/weights must return 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert abs(body["w1"] - 0.40) < 1e-6, (
            f"[AC6] Updated w1 should be 0.40, got {body['w1']}"
        )
        assert abs(body["w2"] - 0.25) < 1e-6
        assert abs(body["w3"] - 0.20) < 1e-6
        assert abs(body["w4"] - 0.10) < 1e-6
        assert abs(body["w5"] - 0.05) < 1e-6
        assert body["version"] == 2, (
            f"[AC6] Version must increment to 2 after update, got {body['version']}"
        )

        # Verify the session received the weight update
        assert abs(mock_config.w1_skill - 0.40) < 1e-6
        assert abs(mock_config.w5_growth - 0.05) < 1e-6
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — Weight change causes deterministic score delta (unit test)
# ─────────────────────────────────────────────────────────────────────────────

def test_weight_change_causes_deterministic_score_delta():
    """
    [AC6] Changing weights from default to skill-heavy must produce a
    deterministic and measurable score delta on identical profile pairs.

    Default weights (w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10).
    Skill-heavy weights (w1=0.70, w2=0.10, w3=0.10, w4=0.05, w5=0.05).

    A developer with strong skill overlap but weak behavioral match will score
    higher under skill-heavy weights. The delta must be ≥ 0.04.
    """
    from src.engine.matching import compute_match_score

    # Component scores for a developer with high skill fit but low behavioral fit
    skill_score = 0.90       # Strong skill match
    workstyle_score = 0.30   # Weak behavioral match
    motivation_score = 0.35  # Weak motivation match
    timezone_score = 0.80    # Good timezone match
    growth_score = 0.50      # Moderate growth match

    # Score under default weights
    score_default = compute_match_score(
        w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10,
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        motivation_score=motivation_score,
        timezone_score=timezone_score,
        growth_score=growth_score,
    )

    # Score under skill-heavy weights
    score_skill_heavy = compute_match_score(
        w1=0.70, w2=0.10, w3=0.10, w4=0.05, w5=0.05,
        skill_score=skill_score,
        workstyle_score=workstyle_score,
        motivation_score=motivation_score,
        timezone_score=timezone_score,
        growth_score=growth_score,
    )

    delta = score_skill_heavy - score_default

    assert delta >= 0.04, (
        f"[AC6] Weight change from default to skill-heavy must produce a delta ≥ 0.04. "
        f"Got delta={delta:.4f} (default={score_default:.4f}, skill_heavy={score_skill_heavy:.4f})"
    )

    # Verify both scores are in [0, 1]
    assert 0.0 <= score_default <= 1.0
    assert 0.0 <= score_skill_heavy <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Negative: PUT with invalid sum → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_update_weights_invalid_sum_returns_422():
    """
    [AC6] PUT /config/weights with weights that do not sum to 1.0 (±0.001)
    must return HTTP 422 (Pydantic model_validator rejects the payload).
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="admin")  # weight tuning = Admin View (§6)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/config/weights",
                # Sum = 0.30 + 0.30 + 0.20 + 0.15 + 0.10 = 1.05 ≠ 1.0
                json={
                    "w1": 0.30,
                    "w2": 0.30,
                    "w3": 0.20,
                    "w4": 0.15,
                    "w5": 0.10,
                },
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 422, (
            f"[AC6] Weights summing to 1.05 must return 422, got {response.status_code}: "
            f"{response.text}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: PUT with negative weight → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_update_weights_negative_weight_returns_422():
    """
    [AC6] PUT /config/weights with any negative weight (Field ge=0.0 constraint)
    must return HTTP 422.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="admin")  # weight tuning = Admin View (§6)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/config/weights",
                json={
                    "w1": -0.10,  # negative — must fail Field(ge=0.0)
                    "w2": 0.50,
                    "w3": 0.30,
                    "w4": 0.20,
                    "w5": 0.10,
                },
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 422, (
            f"[AC6] Negative weight must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: PUT by non-manager (developer role) → 403
# ─────────────────────────────────────────────────────────────────────────────

async def test_update_weights_by_developer_returns_403():
    """
    [AC6] Only managers may update weights. A developer role must receive 403.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()
    # Note: the role check happens BEFORE the DB call, so no queue needed
    # but we still need a session for the dependency
    session.queue_execute(value=MockWeightConfig())

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")  # developer, not manager

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/config/weights",
                json={
                    "w1": 0.30,
                    "w2": 0.25,
                    "w3": 0.20,
                    "w4": 0.15,
                    "w5": 0.10,
                },
                headers=dev_auth_headers(),
            )
        assert response.status_code == 403, (
            f"[AC6] Developer updating weights must return 403, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Negative: PUT with weight > 1.0 → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_update_weights_exceeds_max_returns_422():
    """
    PUT /config/weights with any weight > 1.0 must return HTTP 422.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=MGR_USER_ID, role="admin")  # weight tuning = Admin View (§6)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/config/weights",
                json={
                    "w1": 1.10,  # exceeds le=1.0 constraint
                    "w2": 0.0,
                    "w3": 0.0,
                    "w4": 0.0,
                    "w5": 0.0,
                },
                headers=mgr_auth_headers(),
            )
        assert response.status_code == 422, (
            f"Weight > 1.0 must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()
