"""BLK-001: all HTTPException responses must use the error envelope.

Contract (artifacts/api-contracts.json ErrorResponse):
  { error_code: str, message: str, request_id: str }

FastAPI's default HTTPException handler returns {"detail": ...} which violates
the contract.  The fix adds an explicit StarletteHTTPException handler.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_missing_auth_returns_error_envelope():
    """
    [BLK-001] POST /api/v1/auth/refresh with no cookie must return HTTP 401
    with error envelope {error_code, message, request_id} and must NOT
    contain a 'detail' key.
    """
    from src.main import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/v1/auth/refresh")  # no cookie → 401
    assert resp.status_code == 401, (
        f"[BLK-001] Request without auth must return 401, got {resp.status_code}"
    )
    body = resp.json()
    assert "error_code" in body, (
        f"[BLK-001] Response must contain 'error_code', got: {body}"
    )
    assert "message" in body, (
        f"[BLK-001] Response must contain 'message', got: {body}"
    )
    assert "request_id" in body, (
        f"[BLK-001] Response must contain 'request_id', got: {body}"
    )
    assert "detail" not in body, (
        f"[BLK-001] Response must NOT contain 'detail', got: {body}"
    )
