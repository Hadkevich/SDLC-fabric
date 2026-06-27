"""Shared test fixtures for the NEURAL SYNC test suite.

All API-level tests use these fixtures to override FastAPI dependencies
with mock implementations, avoiding the need for a live PostgreSQL database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from src.core.auth import TokenPayload, create_access_token

# ─────────────────────────────────────────────────────────────────────────────
# Fixed test identifiers
# ─────────────────────────────────────────────────────────────────────────────

DEV_USER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MGR_USER_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
TEST_DEV_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TEST_PROJ_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
TEST_MATCH_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
TEST_FEEDBACK_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


# ─────────────────────────────────────────────────────────────────────────────
# JWT token helpers
# ─────────────────────────────────────────────────────────────────────────────

def dev_auth_headers() -> dict[str, str]:
    """Returns Authorization header for a developer role user.

    The developer token carries developer_profile_id so the own-profile and
    feedback authorization checks (src/api/developers.py, src/api/feedback.py)
    treat this user as the owner of DEV_USER_ID's profile.
    """
    token = create_access_token(DEV_USER_ID, "developer", DEV_USER_ID)
    return {"Authorization": f"Bearer {token}"}


def mgr_auth_headers() -> dict[str, str]:
    """Returns Authorization header for a manager role user (no developer profile)."""
    token = create_access_token(MGR_USER_ID, "manager", None)
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# Mock DB result objects
# ─────────────────────────────────────────────────────────────────────────────

class MockRow:
    """Mimics a SQLAlchemy result row with named attributes."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockExecuteResult:
    """Mimics the result of db.execute()."""

    def __init__(
        self,
        value: Any = None,
        row: Any = None,
        all_values: list | None = None,
    ) -> None:
        self._value = value
        self._row = row
        self._all = all_values if all_values is not None else (
            [] if value is None else [value]
        )

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar(self) -> Any:
        return self._value

    def scalars(self) -> "MockExecuteResult":
        return self

    def all(self) -> list:
        return self._all

    def one(self) -> Any:
        return self._row


# ─────────────────────────────────────────────────────────────────────────────
# Mock ORM model objects
# ─────────────────────────────────────────────────────────────────────────────

class MockWeightConfig:
    """Mock WeightConfig ORM row (singleton id=1)."""

    def __init__(
        self,
        w1: float = 0.30,
        w2: float = 0.25,
        w3: float = 0.20,
        w4: float = 0.15,
        w5: float = 0.10,
        version: int = 1,
    ) -> None:
        self.id = 1
        self.w1_skill = w1
        self.w2_workstyle = w2
        self.w3_motivation = w3
        self.w4_timezone = w4
        self.w5_growth = w5
        self.version = version
        self.updated_at = datetime.now(timezone.utc)
        self.updated_by = None


class MockDeveloperProfile:
    """Mock DeveloperProfile ORM row."""

    def __init__(self, dev_id: uuid.UUID | None = None) -> None:
        self.id = dev_id or TEST_DEV_ID
        self.skills = ["Python", "FastAPI", "PostgreSQL"]
        self.experience_years = 5
        self.preferred_stack = ["Python", "FastAPI"]
        self.work_style_vector = [0.8] * 8
        self.motivation_vector = [0.8] * 8
        self.timezone = "Europe/Warsaw"
        self.availability_hours = 40
        self.career_goals = ["technical leadership", "distributed systems"]
        self.project_history: list = []
        self.is_behavioral_self_reported = True
        self.embedding_status = "pending"
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class MockProjectProfile:
    """Mock ProjectProfile ORM row."""

    def __init__(self, proj_id: uuid.UUID | None = None) -> None:
        self.id = proj_id or TEST_PROJ_ID
        self.name = "Test Project"
        self.required_skills = ["Python", "FastAPI", "PostgreSQL"]
        self.team_structure = "agile squad"
        self.workload_intensity = 0.7
        self.innovation_level = 0.7
        self.timezone_overlap_required = "UTC+0 to UTC+3"
        self.duration_weeks = 12
        self.growth_opportunities = ["technical leadership", "distributed systems"]
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class MockMatchRecord:
    """Mock MatchRecord ORM row."""

    def __init__(
        self,
        match_id: uuid.UUID | None = None,
        dev_id: uuid.UUID | None = None,
        proj_id: uuid.UUID | None = None,
        score: float = 0.75,
    ) -> None:
        self.id = match_id or TEST_MATCH_ID
        self.developer_id = dev_id or TEST_DEV_ID
        self.project_id = proj_id or TEST_PROJ_ID
        self.match_score = score
        self.skill_score = 0.80
        self.workstyle_score = 0.75
        self.motivation_score = 0.78
        self.timezone_score = 0.90
        self.growth_score = 0.65
        self.explanation = (
            "Skill alignment: Strong match in Python and FastAPI covering core requirements. "
            "Behavioral fit: High collaboration and async work-style aligns with team culture. "
            "Growth potential: Distributed systems and leadership opportunities match career goals."
        )
        self.explanation_source = "stub_pending"
        self.explanation_updated_at = None
        self.risks: list = []
        self.growth_potential = ["Technical leadership", "Distributed systems"]
        self.weights_snapshot = {
            "w1": 0.30, "w2": 0.25, "w3": 0.20, "w4": 0.15, "w5": 0.10,
        }
        self.vector_search_degraded = False
        self.behavioral_data_unavailable = False
        self.timestamp = datetime.now(timezone.utc)


class MockFeedbackRecord:
    """Mock FeedbackRecord ORM row."""

    def __init__(
        self,
        feedback_id: uuid.UUID | None = None,
        dev_id: uuid.UUID | None = None,
        match_id: uuid.UUID | None = None,
        accepted: bool = False,
    ) -> None:
        self.id = feedback_id or TEST_FEEDBACK_ID
        self.developer_id = dev_id or TEST_DEV_ID
        self.match_id = match_id or TEST_MATCH_ID
        self.accepted = accepted
        self.comment = None
        self.timestamp = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Mock async DB session
# ─────────────────────────────────────────────────────────────────────────────

class MockAsyncSession:
    """
    Lightweight mock of SQLAlchemy AsyncSession.

    Usage:
        session = MockAsyncSession()
        session.queue_execute(MockWeightConfig())   # first execute() call
        session.queue_execute(None)                  # second execute() call
        session.set_get("DeveloperProfile", dev_id, MockDeveloperProfile())
    """

    def __init__(self) -> None:
        self._execute_queue: list[MockExecuteResult] = []
        self._execute_idx: int = 0
        self._get_results: dict[tuple, Any] = {}
        self.added: list = []
        self.deleted: list = []

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def queue_execute(
        self,
        value: Any = None,
        row: Any = None,
        all_values: list | None = None,
    ) -> None:
        """Add a result to the FIFO execute queue."""
        self._execute_queue.append(MockExecuteResult(value, row, all_values))

    def set_get(self, cls_name: str, pk: Any, value: Any) -> None:
        """Set the return value for db.get(Model, pk)."""
        self._get_results[(cls_name, str(pk))] = value

    # ── SQLAlchemy interface ──────────────────────────────────────────────────

    async def execute(self, query: Any, *args: Any, **kwargs: Any) -> MockExecuteResult:
        if self._execute_idx < len(self._execute_queue):
            result = self._execute_queue[self._execute_idx]
            self._execute_idx += 1
            return result
        return MockExecuteResult(None)

    async def get(self, cls: type, pk: Any) -> Any:
        return self._get_results.get((cls.__name__, str(pk)))

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        # Simulate SQLAlchemy setting the primary-key default at flush time.
        if hasattr(obj, "id") and obj.id is None:
            obj.id = uuid.uuid4()

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        # Ensure any added objects with a callable default get an id.
        for obj in self.added:
            if hasattr(obj, "id") and obj.id is None:
                obj.id = uuid.uuid4()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# pytest fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_session() -> MockAsyncSession:
    """Return a fresh MockAsyncSession for each test."""
    return MockAsyncSession()


@pytest.fixture
def dev_headers() -> dict[str, str]:
    return dev_auth_headers()


@pytest.fixture
def mgr_headers() -> dict[str, str]:
    return mgr_auth_headers()
