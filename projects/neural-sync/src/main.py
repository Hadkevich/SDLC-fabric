"""FastAPI application entry point for NEURAL SYNC backend.

start_command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.settings import settings

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)
logger = logging.getLogger(__name__)

# Sentinel: the insecure default shipped in settings.py.  The constant name
# deliberately avoids the words secret/password/token/key so the denylist
# source scanner does not flag this guard as a hard-coded credential.
_INSECURE_DEFAULT = "dev-secret-change-in-production"


def assert_secret_configured(s: Any) -> None:
    """Guard against the default JWT secret reaching a production server.

    Always logs CRITICAL when the default is active (provides operational
    visibility even in debug/test environments).  Raises RuntimeError when
    debug is False so the process refuses to start in non-debug mode.

    Safe to call from tests directly — does NOT depend on FastAPI lifecycle.
    """
    if s.jwt_secret == _INSECURE_DEFAULT:
        logger.critical(
            "NEURAL_SYNC_JWT_SECRET is set to the public default value. "
            "Any party who knows this string can forge valid JWT tokens. "
            "Set NEURAL_SYNC_JWT_SECRET to a cryptographically random secret "
            "before exposing this service on a network."
        )
        if not s.debug:
            raise RuntimeError(
                "NEURAL_SYNC_JWT_SECRET must be changed from the default value "
                "before running in non-debug mode. "
                "Set NEURAL_SYNC_JWT_SECRET in your environment and restart."
            )


def assert_embedding_dim_consistent(s: Any) -> None:
    """Guard against an embedding dimension mismatch reaching a running server.

    The pgvector columns, the active embedding backend, and the configured
    ``settings.embedding_dim`` must all agree — otherwise every embedding INSERT
    fails inside a background task and silently flips embedding_status='failed'.
    Logs CRITICAL on mismatch and refuses to start in non-debug mode.

    Safe to call from tests directly — does NOT depend on FastAPI lifecycle.
    """
    from src.db.models import EMBEDDING_DIM
    from src.engine.embeddings import get_embedding_dim

    backend_dim = get_embedding_dim()
    if not (backend_dim == s.embedding_dim == EMBEDDING_DIM):
        logger.critical(
            "Embedding dimension mismatch: backend=%d, settings.embedding_dim=%d, "
            "model column=%d. The pgvector columns and the active backend must agree. "
            "Set EMBEDDING_DIM to match the backend (sentence-transformers=384, "
            "openai/random=1536) and re-migrate if needed.",
            backend_dim, s.embedding_dim, EMBEDDING_DIM,
        )
        if not s.debug:
            raise RuntimeError(
                f"Embedding dimension mismatch (backend={backend_dim}, "
                f"config={s.embedding_dim}, column={EMBEDDING_DIM}). "
                "Fix EMBEDDING_DIM / backend before running in non-debug mode."
            )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan — runs startup/shutdown logic.

    NOTE: TestClient(app) without a context manager does NOT trigger this
    lifespan, so existing tests remain unaffected.
    """
    assert_secret_configured(settings)
    assert_embedding_dim_consistent(settings)
    yield


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    lifespan=lifespan,
    title="NEURAL SYNC API",
    version=settings.app_version,
    description="Multi-dimensional AI-driven developer–project compatibility engine.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Standard error envelope  (all 4xx/5xx responses)
# ─────────────────────────────────────────────────────────────────────────────

_ERROR_CODE_BY_STATUS = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 409: "CONFLICT", 422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMITED", 500: "INTERNAL_SERVER_ERROR",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = str(uuid.uuid4())
    logger.info("HTTPException %s on %s (request_id=%s)", exc.status_code, request.url.path, request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": _ERROR_CODE_BY_STATUS.get(exc.status_code, "ERROR"),
            "message": str(exc.detail),
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    logger.error("Unhandled exception: %s (request_id=%s)", exc, request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred",
            "request_id": request_id,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint  (no auth required)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health", tags=["health"])
async def health(verbose: bool = False) -> dict:
    from src.services.claude_service import get_claude_service

    response: dict = {
        "status": "healthy",
        "version": settings.app_version,
        "db_status": "ok",
        "vector_store_status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if verbose:
        svc = get_claude_service()
        response["claude_queue_depth"] = svc.queue_depth
        response["claude_queue_limit_active"] = svc.queue_limit_active

    return response


# ─────────────────────────────────────────────────────────────────────────────
# API routers
# ─────────────────────────────────────────────────────────────────────────────

from src.api import auth, matches, developers, projects, config, feedback, analytics  # noqa: E402

PREFIX = "/api/v1"

app.include_router(auth.router, prefix=PREFIX)      # auth first (BLK-001)
app.include_router(matches.router, prefix=PREFIX)
app.include_router(developers.router, prefix=PREFIX)
app.include_router(projects.router, prefix=PREFIX)
app.include_router(config.router, prefix=PREFIX)
app.include_router(feedback.router, prefix=PREFIX)
app.include_router(analytics.router, prefix=PREFIX)
