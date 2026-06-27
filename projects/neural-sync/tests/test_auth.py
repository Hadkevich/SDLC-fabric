"""Auth flow: login sets an HttpOnly refresh cookie; /auth/refresh validates
and rotates it to issue a new access token."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.core.auth import create_refresh_token, create_access_token, hash_password
from src.db.session import get_db
from tests.conftest import MockAsyncSession


class _MockUser:
    def __init__(self):
        self.id = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
        self.username = "dev1"
        self.hashed_password = hash_password("secret")
        self.role = "developer"
        self.is_active = True


@pytest.fixture
def client_with_user():
    from src.main import app

    session = MockAsyncSession()
    session.queue_execute(_MockUser())  # login's SELECT UserAccount → scalar_one_or_none

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_login_sets_httponly_refresh_cookie(client_with_user):
    resp = client_with_user.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "secret"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.replace("Strict", "strict")
    assert "Max-Age=604800" in set_cookie  # 7-day TTL (jwt_refresh_token_ttl_seconds)


def test_refresh_issues_new_access_token_and_rotates_cookie():
    from src.main import app

    client = TestClient(app)
    uid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    token = create_refresh_token(uid, "developer")
    resp = client.post("/api/v1/auth/refresh", cookies={"refresh_token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["role"] == "developer"
    assert "refresh_token" in resp.headers.get("set-cookie", "")  # rotated


def test_refresh_without_cookie_is_401():
    from src.main import app

    client = TestClient(app)
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


def test_access_token_cannot_be_used_as_refresh_token():
    from src.main import app

    client = TestClient(app)
    access = create_access_token("cccccccc-cccc-cccc-cccc-cccccccccccc", "developer")
    resp = client.post("/api/v1/auth/refresh", cookies={"refresh_token": access})
    assert resp.status_code == 401


def test_password_hash_roundtrip():
    from src.core.auth import hash_password, verify_password
    h = hash_password("secret")
    assert h.startswith("$2")          # bcrypt hash
    assert verify_password("secret", h) is True
    assert verify_password("wrong", h) is False


def test_refresh_token_cannot_be_used_as_access_token():
    import pytest
    from fastapi import HTTPException
    from src.core.auth import create_refresh_token, decode_access_token
    refresh = create_refresh_token("cccccccc-cccc-cccc-cccc-cccccccccccc", "developer")
    with pytest.raises(HTTPException) as exc:
        decode_access_token(refresh)
    assert exc.value.status_code == 401
