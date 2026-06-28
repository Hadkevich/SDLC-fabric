"""Auth boundary tests for all /api/v1/ingestion/* endpoints.

Coverage:
  AC21 — All ingestion endpoints return HTTP 403 for developer-role JWT,
          HTTP 401 for unauthenticated request, and HTTP 2xx for manager-role JWT.

Every test verifies that the require_manager dependency correctly enforces
role-based access control on each ingestion route.
"""
from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core.auth import TokenPayload, create_access_token
from src.db.session import get_db
from src.services.enrichment import EnrichmentResult
from tests.conftest import (
    DEV_USER_ID,
    MGR_USER_ID,
    MockAsyncSession,
    dev_auth_headers,
    mgr_auth_headers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────

_GOOD_ENRICHMENT = EnrichmentResult(
    skills=["python", "fastapi"],
    work_style=[0.5] * 8,
    motivation_vector=[0.5] * 8,
    career_goals=["engineering"],
    provenance="heuristic",
    preferred_stack=["python"],
)

_MINIMAL_SLACK = json.dumps({
    "users": [{"id": "U001", "name": "alice", "real_name": "Alice",
               "profile": {"email": "alice@example.com"}}],
    "channels": {"general": [{"user": "U001", "text": "Hello Python world!"}]},
}).encode()

_MINIMAL_CV = b"Senior Python engineer with 5 years of experience."
_MINIMAL_HR_CSV = b"name,email,title\nAlice Smith,alice@example.com,Engineer\n"


class _FakeApp:
    """Context manager that sets up the app with a mock DB session."""

    def __init__(self) -> None:
        from src.main import app
        self.app = app
        self._session = MockAsyncSession()

    def __enter__(self) -> "TestClient":
        async def _override_db():
            yield self._session

        self.app.dependency_overrides[get_db] = _override_db
        return TestClient(self.app, raise_server_exceptions=False)

    def __exit__(self, *_: Any) -> None:
        self.app.dependency_overrides.clear()


def _plain_client() -> TestClient:
    """Return a TestClient with no dependency overrides (real auth enforcement)."""
    from src.main import app
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# GET /ingestion/connectors
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectorsAuthBoundary:

    def test_manager_jwt_returns_200(self):
        """[AC21] Manager-role JWT accepted for GET /ingestion/connectors."""
        with _FakeApp() as client:
            resp = client.get("/api/v1/ingestion/connectors", headers=mgr_auth_headers())
        assert resp.status_code == 200, f"Manager must get 200; got {resp.status_code}: {resp.text}"

    def test_developer_jwt_returns_403(self):
        """[AC21] Developer-role JWT returns HTTP 403 for GET /ingestion/connectors."""
        client = _plain_client()
        resp = client.get("/api/v1/ingestion/connectors", headers=dev_auth_headers())
        assert resp.status_code == 403, f"Developer must get 403; got {resp.status_code}"

    def test_no_auth_returns_401(self):
        """[AC21] Unauthenticated request returns HTTP 401 for GET /ingestion/connectors."""
        client = _plain_client()
        resp = client.get("/api/v1/ingestion/connectors")
        assert resp.status_code == 401, f"No auth must get 401; got {resp.status_code}"

    def test_invalid_token_returns_401(self):
        """[AC21] Malformed/expired JWT returns HTTP 401."""
        client = _plain_client()
        resp = client.get(
            "/api/v1/ingestion/connectors",
            headers={"Authorization": "Bearer totally.invalid.jwt"},
        )
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /ingestion/file
# ─────────────────────────────────────────────────────────────────────────────

class TestFileIngestionAuthBoundary:

    def test_manager_jwt_accepted(self):
        """[AC21] Manager-role JWT accepted for POST /ingestion/file."""
        with _FakeApp() as client:
            with patch("src.etl.orchestrator.enrich_profile", return_value=_GOOD_ENRICHMENT):
                resp = client.post(
                    "/api/v1/ingestion/file",
                    headers=mgr_auth_headers(),
                    files={"file": ("cv.txt", io.BytesIO(_MINIMAL_CV), "text/plain")},
                    data={"source": "cv", "mode": "preview"},
                )
        assert resp.status_code == 200, f"Manager must get 200; got {resp.status_code}: {resp.text}"

    def test_developer_jwt_returns_403(self):
        """[AC21] Developer-role JWT returns HTTP 403 for POST /ingestion/file."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/file",
            headers=dev_auth_headers(),
            files={"file": ("cv.txt", io.BytesIO(_MINIMAL_CV), "text/plain")},
            data={"source": "cv", "mode": "preview"},
        )
        assert resp.status_code == 403, f"Developer must get 403; got {resp.status_code}"

    def test_no_auth_returns_401(self):
        """[AC21] Unauthenticated request returns HTTP 401 for POST /ingestion/file."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/file",
            files={"file": ("cv.txt", io.BytesIO(_MINIMAL_CV), "text/plain")},
            data={"source": "cv", "mode": "preview"},
        )
        assert resp.status_code == 401, f"No auth must get 401; got {resp.status_code}"

    def test_developer_jwt_returns_403_for_hr_source(self):
        """[AC21] Developer role gets 403 regardless of source parameter."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/file",
            headers=dev_auth_headers(),
            files={"file": ("hr.csv", io.BytesIO(_MINIMAL_HR_CSV), "text/csv")},
            data={"source": "hr", "mode": "preview"},
        )
        assert resp.status_code == 403

    def test_developer_jwt_returns_403_for_slack_source(self):
        """[AC21] Developer role gets 403 for slack source."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/file",
            headers=dev_auth_headers(),
            files={"file": ("slack.json", io.BytesIO(_MINIMAL_SLACK), "application/json")},
            data={"source": "slack", "mode": "preview"},
        )
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# POST /ingestion/gitlab
# ─────────────────────────────────────────────────────────────────────────────

class _MockHTTPResponse:
    def __init__(self, status_code: int, data: Any) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = {}

    def json(self) -> Any:
        return self._data


class _MockHTTPClient:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "_MockHTTPClient":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def get(self, *_args: Any, **_kwargs: Any) -> _MockHTTPResponse:
        return _MockHTTPResponse(401, {})


class TestGitlabIngestionAuthBoundary:

    def test_manager_jwt_accepted(self):
        """[AC21] Manager-role JWT accepted for POST /ingestion/gitlab."""
        with _FakeApp() as client:
            with patch(
                "src.connectors.gitlab_connector.httpx.Client",
                return_value=_MockHTTPClient(),
            ):
                with patch("src.etl.orchestrator.enrich_profile", return_value=_GOOD_ENRICHMENT):
                    resp = client.post(
                        "/api/v1/ingestion/gitlab",
                        headers=mgr_auth_headers(),
                        json={"username": "ada", "mode": "preview"},
                    )
        # Either 200 (with degraded result from 401 mock) is acceptable
        assert resp.status_code == 200, f"Manager must get 2xx; got {resp.status_code}: {resp.text}"

    def test_developer_jwt_returns_403(self):
        """[AC21] Developer-role JWT returns HTTP 403 for POST /ingestion/gitlab."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/gitlab",
            headers=dev_auth_headers(),
            json={"username": "ada", "mode": "preview"},
        )
        assert resp.status_code == 403, f"Developer must get 403; got {resp.status_code}"

    def test_no_auth_returns_401(self):
        """[AC21] Unauthenticated request returns HTTP 401 for POST /ingestion/gitlab."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/gitlab",
            json={"username": "ada", "mode": "preview"},
        )
        assert resp.status_code == 401, f"No auth must get 401; got {resp.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# POST /ingestion/jira
# ─────────────────────────────────────────────────────────────────────────────

_JIRA_PAYLOAD = {
    "base_url": "https://org.atlassian.net",
    "email": "manager@example.com",
    "token": "",
    "project_key": "DEV",
    "mode": "preview",
}


class TestJiraIngestionAuthBoundary:

    def test_manager_jwt_accepted(self):
        """[AC21] Manager-role JWT accepted for POST /ingestion/jira (degraded result on empty token)."""
        with _FakeApp() as client:
            resp = client.post(
                "/api/v1/ingestion/jira",
                headers=mgr_auth_headers(),
                json=_JIRA_PAYLOAD,
            )
        # Empty token → credentials error → HTTP 200 with degraded summary
        assert resp.status_code == 200, f"Manager must get 200; got {resp.status_code}: {resp.text}"

    def test_developer_jwt_returns_403(self):
        """[AC21] Developer-role JWT returns HTTP 403 for POST /ingestion/jira."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/jira",
            headers=dev_auth_headers(),
            json=_JIRA_PAYLOAD,
        )
        assert resp.status_code == 403, f"Developer must get 403; got {resp.status_code}"

    def test_no_auth_returns_401(self):
        """[AC21] Unauthenticated request returns HTTP 401 for POST /ingestion/jira."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/jira",
            json=_JIRA_PAYLOAD,
        )
        assert resp.status_code == 401, f"No auth must get 401; got {resp.status_code}"

    def test_invalid_token_returns_401(self):
        """[AC21] Malformed Bearer token returns HTTP 401."""
        client = _plain_client()
        resp = client.post(
            "/api/v1/ingestion/jira",
            headers={"Authorization": "Bearer x.y.z"},
            json=_JIRA_PAYLOAD,
        )
        assert resp.status_code == 401
