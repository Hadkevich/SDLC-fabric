"""Full pipeline integration tests.

Acceptance criteria covered:
  AC1  — POST /api/v1/matches returns response schema (match_score, explanation ≥50 chars,
          risks list, growth_potential list) within 500ms p95
  AC7  — Developer dashboard: GET /developers/{id}/matches returns ≥1 match card
          with match_score and explanation (data powering the developer dashboard)
  AC8  — Manager dashboard: GET /developers/{id}/risk and GET /developers/{id}
          never expose raw work_style or motivation_vector fields in the response
  AC13 — Integration test: full match pipeline (profile ingest → score → explain → response)
          executes against a mocked environment and passes

All DB and Claude API calls are intercepted via dependency_overrides and mock patching.
No live PostgreSQL or external API connections are required.
"""
from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import (
    DEV_USER_ID,
    MGR_USER_ID,
    TEST_DEV_ID,
    TEST_MATCH_ID,
    TEST_PROJ_ID,
    MockAsyncSession,
    MockDeveloperProfile,
    MockMatchRecord,
    MockWeightConfig,
    dev_auth_headers,
    mgr_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared test payload — valid complete developer + project profiles
# ─────────────────────────────────────────────────────────────────────────────

GOOD_MATCH_REQUEST = {
    "developer_profile": {
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "experience_years": 5,
        "preferred_stack": ["Python", "FastAPI"],
        "work_style": [0.8, 0.7, 0.9, 0.6, 0.8, 0.7, 0.9, 0.8],
        "motivation_vector": [0.8, 0.7, 0.8, 0.9, 0.7, 0.8, 0.9, 0.7],
        "timezone": "Europe/Warsaw",
        "availability_hours": 40,
        "career_goals": ["technical leadership", "distributed systems"],
    },
    "project_profile": {
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        "team_structure": "agile",
        "workload_intensity": 0.7,
        "innovation_level": 0.7,
        "timezone_overlap_required": "UTC+1 to UTC+3",
        "duration_weeks": 12,
        "growth_opportunities": ["technical leadership", "distributed systems"],
    },
}


def _make_match_session() -> MockAsyncSession:
    """
    Build a MockAsyncSession pre-loaded with the execute queue needed for
    POST /api/v1/matches (no pre-existing profiles, no ExplanationCache hit).

    Execute call order in create_match:
      1. _load_weights           → WeightConfig.scalar_one_or_none
      2. _get_or_create_developer → DeveloperProfile.scalar_one_or_none → None (create)
      3. _get_or_create_project   → ProjectProfile.scalar_one_or_none  → None (create)
      4. ExplanationCache check   → ExplanationCache.scalar_one_or_none → None (no hit)
    """
    session = MockAsyncSession()
    session.queue_execute(value=MockWeightConfig())  # 1. weight config
    session.queue_execute(value=None)                # 2. developer not found → create
    session.queue_execute(value=None)                # 3. project not found → create
    session.queue_execute(value=None)                # 4. no explanation cache
    return session


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — POST /matches returns valid response schema within 500ms
# ─────────────────────────────────────────────────────────────────────────────

async def test_match_response_schema_valid_ac1():
    """
    [AC1] POST /api/v1/matches with a valid DeveloperProfile + ProjectProfile
    must return:
      - HTTP 201
      - match_score: float in [0.0, 1.0]
      - explanation: non-empty string with len ≥ 50 chars
      - risks: list (may be empty)
      - growth_potential: list (may be empty)
      - component_scores: object with five sub-scores
      - weights_snapshot: object with w1–w5
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = _make_match_session()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch(
            "src.api.matches._async_generate_explanation",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/matches",
                    json=GOOD_MATCH_REQUEST,
                    headers=dev_auth_headers(),
                )

        assert response.status_code == 201, (
            f"[AC1] POST /matches must return 201, got {response.status_code}: {response.text}"
        )
        body = response.json()

        # match_score
        assert "match_score" in body, "[AC1] Response must contain match_score"
        score = body["match_score"]
        assert isinstance(score, (int, float)), "[AC1] match_score must be a float"
        assert 0.0 <= score <= 1.0, f"[AC1] match_score {score} must be in [0.0, 1.0]"

        # explanation
        assert "explanation" in body, "[AC1] Response must contain explanation"
        explanation = body["explanation"]
        assert isinstance(explanation, str) and len(explanation) >= 50, (
            f"[AC1] explanation must be a non-empty string with ≥ 50 chars, "
            f"got len={len(explanation)}: {explanation!r}"
        )

        # risks
        assert "risks" in body, "[AC1] Response must contain risks"
        assert isinstance(body["risks"], list), "[AC1] risks must be a list"

        # growth_potential
        assert "growth_potential" in body, "[AC1] Response must contain growth_potential"
        assert isinstance(body["growth_potential"], list), "[AC1] growth_potential must be a list"

        # component_scores
        assert "component_scores" in body, "[AC1] Response must contain component_scores"
        cs = body["component_scores"]
        for dim in ("skill_score", "workstyle_score", "motivation_score", "timezone_score", "growth_score"):
            assert dim in cs, f"[AC1] component_scores must contain {dim}"
            assert 0.0 <= cs[dim] <= 1.0, f"[AC1] {dim} must be in [0.0, 1.0]"

        # weights_snapshot
        assert "weights_snapshot" in body, "[AC1] Response must contain weights_snapshot"
        ws = body["weights_snapshot"]
        for w in ("w1", "w2", "w3", "w4", "w5"):
            assert w in ws, f"[AC1] weights_snapshot must contain {w}"

        # match_id, developer_id, project_id
        assert "match_id" in body
        assert "developer_id" in body
        assert "project_id" in body
        assert "created_at" in body
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — Latency: match computation completes within 500ms p95
# ─────────────────────────────────────────────────────────────────────────────

async def test_match_endpoint_latency_under_500ms_ac1():
    """
    [AC1] The synchronous matching computation must complete within 500ms.
    Tests the algorithmic portion (no Claude, no live DB) via mock session.
    Runs 10 iterations and verifies the p95 is under 500ms.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    latencies_ms: list[float] = []

    for _ in range(10):
        session = _make_match_session()

        async def override_db():
            yield session

        def override_user():
            return TokenPayload(sub=DEV_USER_ID, role="developer")

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        with patch(
            "src.api.matches._async_generate_explanation",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                t0 = time.perf_counter()
                response = await client.post(
                    "/api/v1/matches",
                    json=GOOD_MATCH_REQUEST,
                    headers=dev_auth_headers(),
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000

        assert response.status_code == 201, (
            f"Match failed during latency test: {response.status_code} {response.text}"
        )
        latencies_ms.append(elapsed_ms)
        app.dependency_overrides.clear()

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]

    assert p95_ms < 500, (
        f"[AC1] p95 latency {p95_ms:.1f}ms exceeds 500ms SLA. "
        f"All latencies: {[f'{x:.1f}' for x in latencies_ms]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC7 — Developer dashboard: GET /developers/{id}/matches returns ≥1 project card
# ─────────────────────────────────────────────────────────────────────────────

async def test_developer_dashboard_returns_at_least_one_match_card_ac7():
    """
    [AC7] GET /api/v1/developers/{id}/matches must return at least one match
    record when the developer has matches.  Each match must include:
      - match_score (float 0.0–1.0)
      - explanation (string)
      - risks (list)
      - growth_potential (list)
    These fields power the ProjectCard component on the developer dashboard.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    mock_match = MockMatchRecord(
        match_id=TEST_MATCH_ID,
        dev_id=TEST_DEV_ID,
        proj_id=TEST_PROJ_ID,
        score=0.85,
    )

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", TEST_DEV_ID, MockDeveloperProfile(TEST_DEV_ID))
    # First execute: total count query → scalars().all() → [match]
    session.queue_execute(all_values=[mock_match])
    # Second execute: filtered+limited query → scalars().all() → [match]
    session.queue_execute(all_values=[mock_match])

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=str(TEST_DEV_ID), role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/developers/{TEST_DEV_ID}/matches",
                headers=dev_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC7] GET /developers/{TEST_DEV_ID}/matches must return 200, "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "matches" in body, "[AC7] Response must contain matches list"
        matches = body["matches"]
        assert len(matches) >= 1, (
            f"[AC7] Developer dashboard must show ≥1 match card, got {len(matches)}"
        )

        # Verify each match has the fields needed to render a ProjectCard
        for match in matches:
            assert "match_score" in match, "[AC7] Match card must have match_score"
            assert 0.0 <= match["match_score"] <= 1.0
            assert "explanation" in match, "[AC7] Match card must have explanation"
            assert "risks" in match, "[AC7] Match card must have risks"
            assert isinstance(match["risks"], list)
            assert "growth_potential" in match, "[AC7] Match card must have growth_potential"
            assert isinstance(match["growth_potential"], list)
            assert "match_id" in match
            assert "developer_id" in match
            assert "project_id" in match
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC8 — Manager dashboard: GET /developers/{id}/risk exposes no raw vectors
# ─────────────────────────────────────────────────────────────────────────────

async def test_manager_risk_endpoint_excludes_raw_vectors_ac8():
    """
    [AC8] GET /api/v1/developers/{id}/risk must return risk badges and scores
    WITHOUT exposing work_style_vector, motivation_vector, or any raw
    behavioral data that should not reach the Manager Dashboard.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", TEST_DEV_ID, MockDeveloperProfile(TEST_DEV_ID))
    # execute(select(AllocationRecord)) → scalars().all() → []
    session.queue_execute(all_values=[])

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
                f"/api/v1/developers/{TEST_DEV_ID}/risk",
                headers=mgr_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC8] GET /developers/{TEST_DEV_ID}/risk must return 200, "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()

        # Required risk fields must be present
        assert "burnout_risk_score" in body, "[AC8] Risk response must include burnout_risk_score"
        assert "bench_risk_score" in body, "[AC8] Risk response must include bench_risk_score"
        assert "burnout_risk_badge" in body, "[AC8] Risk response must include burnout_risk_badge"
        assert "bench_risk_badge" in body, "[AC8] Risk response must include bench_risk_badge"
        assert body["burnout_risk_badge"] in ("low", "medium", "high")
        assert body["bench_risk_badge"] in ("low", "medium", "high")

        # Raw behavioral vectors must NOT be present
        forbidden_keys = {"work_style_vector", "motivation_vector", "work_style", "motivation"}
        actual_keys = set(body.keys())
        leaked_keys = forbidden_keys & actual_keys
        assert not leaked_keys, (
            f"[AC8] Risk response must NOT expose raw vectors. Leaked: {leaked_keys}"
        )
    finally:
        app.dependency_overrides.clear()


async def test_developer_profile_response_excludes_raw_vectors_ac8():
    """
    [AC8] GET /api/v1/developers/{id} response (DeveloperProfileResponse) must
    NOT include work_style_vector or motivation_vector fields.
    The raw vectors are stored in the DB but never returned in API responses.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()
    session.set_get("DeveloperProfile", TEST_DEV_ID, MockDeveloperProfile(TEST_DEV_ID))

    async def override_db():
        yield session

    def override_user():
        # Developer accessing their own profile
        return TokenPayload(sub=str(TEST_DEV_ID), role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/developers/{TEST_DEV_ID}",
                headers=dev_auth_headers(),
            )

        assert response.status_code == 200, (
            f"[AC8] GET /developers/{TEST_DEV_ID} must return 200, "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()

        # Required profile fields must be present
        assert "id" in body
        assert "skills" in body
        assert "experience_years" in body
        assert "timezone" in body
        assert "embedding_status" in body

        # Raw behavioral vectors must NOT be exposed
        forbidden_keys = {"work_style_vector", "motivation_vector", "work_style", "motivation"}
        actual_keys = set(body.keys())
        leaked_keys = forbidden_keys & actual_keys
        assert not leaked_keys, (
            f"[AC8] Developer profile response must NOT expose raw vectors. Leaked: {leaked_keys}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC13 — Full pipeline end-to-end: profile ingest → score → explain → response
# ─────────────────────────────────────────────────────────────────────────────

async def test_full_pipeline_end_to_end_ac13():
    """
    [AC13] Integration test: POST /api/v1/matches with complete developer and
    project profiles must execute the full matching pipeline:
      1. Developer profile ingestion (upsert to mock DB)
      2. Project profile ingestion (upsert to mock DB)
      3. Five-dimension score computation (skill, workstyle, motivation, timezone, growth)
      4. Stub explanation generation (synchronous, ≥50 chars)
      5. MatchRecord persistence (mock DB)
      6. HTTP 201 response with all required fields

    Claude explanation is generated asynchronously (mocked) and does not block response.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = _make_match_session()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch(
            "src.api.matches._async_generate_explanation",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/matches",
                    json=GOOD_MATCH_REQUEST,
                    headers=dev_auth_headers(),
                )

        # ── Step 1: HTTP status ────────────────────────────────────────────
        assert response.status_code == 201, (
            f"[AC13] Full pipeline must return 201, got {response.status_code}: {response.text}"
        )
        body = response.json()

        # ── Step 2: Score in valid range ──────────────────────────────────
        score = body["match_score"]
        assert 0.0 <= score <= 1.0, f"[AC13] match_score {score} out of [0.0, 1.0]"

        # ── Step 3: Explanation present ───────────────────────────────────
        explanation = body["explanation"]
        assert len(explanation) >= 50, (
            f"[AC13] explanation must be ≥50 chars, got {len(explanation)}: {explanation!r}"
        )

        # ── Step 4: All component scores present ──────────────────────────
        cs = body["component_scores"]
        for dim in ("skill_score", "workstyle_score", "motivation_score", "timezone_score", "growth_score"):
            assert 0.0 <= cs[dim] <= 1.0, f"[AC13] {dim} out of range: {cs[dim]}"

        # ── Step 5: Persistence (MatchRecord added to mock session) ───────
        from src.db.models import MatchRecord
        match_objs = [obj for obj in session.added if isinstance(obj, MatchRecord)]
        assert len(match_objs) >= 1, "[AC13] At least one MatchRecord must be persisted"
        match_record = match_objs[0]
        assert match_record.match_score == pytest.approx(score, abs=1e-6)
        assert match_record.explanation == explanation
        assert match_record.explanation_source == "stub_pending"

        # ── Step 6: No raw vectors in response ────────────────────────────
        forbidden = {"work_style_vector", "motivation_vector", "work_style", "motivation"}
        assert not (forbidden & set(body.keys())), (
            "[AC13] Raw behavioral vectors must not appear in match response"
        )

        # ── Step 7: Explanation source is stub (no Claude call) ───────────
        assert body["explanation_source"] == "stub_pending", (
            "[AC13] Synchronous response must use stub explanation "
            "(Claude explanation is enqueued async)"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC13 — End-to-end: create match → submit feedback → verify feedback stored
# ─────────────────────────────────────────────────────────────────────────────

async def test_full_pipeline_match_then_feedback_ac13():
    """
    [AC13] Extended pipeline: POST /matches (create match) followed by
    POST /matches/feedback (submit developer response) must:
      1. Return 201 for the match with a match_id
      2. Store the feedback record linked to that match_id
      3. Return 201 for the feedback with the correct fields
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    # ── Phase 1: Create match ─────────────────────────────────────────────
    match_session = _make_match_session()

    async def override_db_match():
        yield match_session

    def override_user_dev():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db_match
    app.dependency_overrides[get_current_user] = override_user_dev

    with patch(
        "src.api.matches._async_generate_explanation",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            match_response = await client.post(
                "/api/v1/matches",
                json=GOOD_MATCH_REQUEST,
                headers=dev_auth_headers(),
            )

    assert match_response.status_code == 201, (
        f"[AC13] Match creation failed: {match_response.status_code} {match_response.text}"
    )
    match_id = match_response.json()["match_id"]
    match_id_uuid = uuid.UUID(match_id)

    # ── Phase 2: Submit feedback for that match ───────────────────────────
    from src.db.models import MatchRecord
    # Simulate an existing MatchRecord with the returned match_id
    stored_match = next(
        obj for obj in match_session.added if isinstance(obj, MatchRecord)
    )

    feedback_session = MockAsyncSession()
    feedback_session.set_get("MatchRecord", match_id_uuid, stored_match)
    # Duplicate check: no existing feedback
    feedback_session.queue_execute(value=None)

    async def override_db_feedback():
        yield feedback_session

    app.dependency_overrides[get_db] = override_db_feedback
    app.dependency_overrides[get_current_user] = override_user_dev

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        feedback_response = await client.post(
            "/api/v1/matches/feedback",
            json={
                "developer_id": DEV_USER_ID,
                "match_id": match_id,
                "accepted": True,
                "comment": "Excellent project fit!",
            },
            headers=dev_auth_headers(),
        )

    assert feedback_response.status_code == 201, (
        f"[AC13] Feedback submission failed: {feedback_response.status_code} "
        f"{feedback_response.text}"
    )
    fb_body = feedback_response.json()
    assert fb_body["match_id"] == match_id
    assert fb_body["accepted"] is True
    assert "id" in fb_body
    assert "feedback_timestamp" in fb_body

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — Negative: POST /matches with missing required field → 422
# ─────────────────────────────────────────────────────────────────────────────

async def test_match_missing_required_field_returns_422():
    """
    [AC1] POST /matches with an incomplete developer profile (missing skills)
    must return 422 Unprocessable Entity.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches",
                json={
                    "developer_profile": {
                        # "skills" intentionally omitted
                        "experience_years": 5,
                        "preferred_stack": ["Python"],
                        "work_style": [0.5] * 8,
                        "motivation_vector": [0.5] * 8,
                        "timezone": "UTC",
                        "availability_hours": 40,
                        "career_goals": ["growth"],
                    },
                    "project_profile": {
                        "required_skills": ["Python"],
                        "team_structure": "agile",
                        "workload_intensity": 0.7,
                        "innovation_level": 0.7,
                        "timezone_overlap_required": "UTC",
                        "duration_weeks": 12,
                    },
                },
                headers=dev_auth_headers(),
            )
        assert response.status_code == 422, (
            f"[AC1] Missing 'skills' must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


async def test_match_work_style_wrong_length_returns_422():
    """
    [AC1] POST /matches with work_style vector of wrong length (≠ 8) returns 422.
    """
    from src.main import app
    from src.db.session import get_db
    from src.core.auth import get_current_user
    from tests.conftest import TokenPayload

    session = MockAsyncSession()

    async def override_db():
        yield session

    def override_user():
        return TokenPayload(sub=DEV_USER_ID, role="developer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        bad_request = {k: v for k, v in GOOD_MATCH_REQUEST.items()}
        bad_dev = dict(GOOD_MATCH_REQUEST["developer_profile"])
        bad_dev["work_style"] = [0.5] * 5  # wrong length (5 instead of 8)
        bad_request = {"developer_profile": bad_dev, "project_profile": GOOD_MATCH_REQUEST["project_profile"]}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/matches",
                json=bad_request,
                headers=dev_auth_headers(),
            )
        assert response.status_code == 422, (
            f"[AC1] work_style of length 5 must return 422, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.clear()
