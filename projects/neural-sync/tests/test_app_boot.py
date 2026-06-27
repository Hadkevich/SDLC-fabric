"""Boot/regression smoke tests — guard the import-time invariants that the
unit/integration suite never exercised (the app is never actually instantiated
there). Covers the 204-no-body crash and the CORS misconfiguration."""
from __future__ import annotations

import pytest


def test_app_imports_without_error():
    """Importing the app must not raise — this is exactly what crashed the
    container at deploy time (204 DELETE with a response body)."""
    from src.main import app
    assert app is not None


def test_both_204_delete_routes_registered():
    """Both GDPR/erasure-class DELETE routes must be registered and 204.

    FastAPI 0.111 stores include_router results as _IncludedRouter objects.
    Each has original_router.routes with the actual APIRoute entries and the
    include_context.prefix for the path prefix. We walk them to find DELETE routes.
    """
    from fastapi.routing import APIRoute
    from src.main import app

    def collect_routes(routes, prefix="", collected=None):
        if collected is None:
            collected = {}
        for r in routes:
            if isinstance(r, APIRoute):
                full_path = prefix + r.path
                if "DELETE" in (r.methods or set()):
                    collected[full_path] = r
            # FastAPI 0.111+: _IncludedRouter with original_router + include_context
            elif hasattr(r, "original_router") and hasattr(r, "include_context"):
                sub_prefix = getattr(r.include_context, "prefix", "")
                collect_routes(r.original_router.routes, sub_prefix, collected)
            elif hasattr(r, "routes"):
                collect_routes(r.routes, prefix, collected)
        return collected

    deletes = collect_routes(app.routes)
    assert "/api/v1/developers/{developer_id}" in deletes, (
        f"DELETE /api/v1/developers/{{developer_id}} not found. Found: {list(deletes.keys())}"
    )
    assert "/api/v1/projects/{project_id}" in deletes
    assert deletes["/api/v1/developers/{developer_id}"].status_code == 204
    assert deletes["/api/v1/projects/{project_id}"].status_code == 204


def test_cors_is_not_wildcard_with_credentials():
    """allow_origins=['*'] + allow_credentials=True is invalid/insecure."""
    from src.core.settings import settings
    assert "*" not in settings.allowed_origins
    assert len(settings.allowed_origins) >= 1


def test_cors_rejects_unlisted_origin():
    """A cross-origin request from an un-allowlisted origin must NOT be
    reflected back as allowed."""
    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/health", headers={"Origin": "http://evil.example.com"})
    allow = resp.headers.get("access-control-allow-origin")
    assert allow != "*"
    assert allow != "http://evil.example.com"


def test_cors_allows_listed_origin():
    """A request from an allow-listed origin IS reflected."""
    from fastapi.testclient import TestClient
    from src.core.settings import settings
    from src.main import app

    origin = settings.allowed_origins[0]
    client = TestClient(app)
    resp = client.get("/api/v1/health", headers={"Origin": origin})
    assert resp.headers.get("access-control-allow-origin") == origin
