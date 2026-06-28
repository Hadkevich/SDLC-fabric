"""Application settings loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

# Load a local .env from the project root BEFORE the Settings class reads env vars,
# so every entrypoint (uvicorn, alembic, pytest, scripts) sees identical config.
# Real secrets live in .env (gitignored); only .env.example is committed.
# A real environment variable always wins over a .env value (override=False).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass


class Settings:
    """Runtime configuration for NEURAL SYNC backend."""

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://neuralsync:neuralsync@localhost:5432/neuralsync",
    )
    database_url_sync: str = os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://neuralsync:neuralsync@localhost:5432/neuralsync",
    )
    db_pool_max_size: int = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
    db_pool_min_size: int = int(os.getenv("DB_POOL_MIN_SIZE", "5"))

    # ── JWT Auth ──────────────────────────────────────────────────────────
    jwt_secret: str = os.getenv("NEURAL_SYNC_JWT_SECRET", "dev-secret-change-in-production")
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_seconds: int = 3600       # 1 hour
    jwt_refresh_token_ttl_seconds: int = 7 * 86400  # 7 days

    # ── LLM (Google Gemini — free tier) ──────────────────────────────────
    # Get a free key at https://aistudio.google.com/app/apikey
    # GEMINI_API_KEY is preferred; GOOGLE_API_KEY is accepted as an alias.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
    # Back-compat: legacy Anthropic key (no longer used by the explanation service).
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_max_concurrent: int = int(os.getenv("CLAUDE_MAX_CONCURRENT", "5"))
    claude_queue_max_depth: int = int(os.getenv("CLAUDE_QUEUE_MAX_DEPTH", "50"))

    # ── Prompt artifact ──────────────────────────────────────────────────
    # Path is relative to the project root; resolved at runtime.
    # parents[2] == project root (src/core/settings.py → src/core → src → <root>)
    prompt_artifact_path: str = os.getenv(
        "PROMPT_ARTIFACT_PATH",
        str(Path(__file__).resolve().parents[2] / "artifacts" / "prompts" / "match_explanation_v1.json"),
    )

    # ── Analytics ────────────────────────────────────────────────────────
    rejection_rate_min_samples: int = int(os.getenv("REJECTION_RATE_MIN_SAMPLES", "10"))

    # ── Matching engine ──────────────────────────────────────────────────
    vector_search_timeout_ms: float = float(os.getenv("VECTOR_SEARCH_TIMEOUT_MS", "150"))

    # ── Embeddings ───────────────────────────────────────────────────────
    # Single source of truth for the pgvector column dimension. The DB column,
    # the embedding backend, and the HNSW indexes must all agree on this value.
    # Default 1536 matches the OpenAI/random backends, the seeded vectors, and the
    # VECTOR(1536) columns created by migration 001. Only set EMBEDDING_DIM=384 if
    # you intend to run the sentence-transformers backend AND migrate the columns.
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1536"))

    # ── CORS ─────────────────────────────────────────────────────────────
    allowed_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
        if o.strip()
    ]

    # ── Auth cookies ─────────────────────────────────────────────────────
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    cookie_samesite: str = os.getenv("COOKIE_SAMESITE", "strict")

    # ── App metadata ─────────────────────────────────────────────────────
    app_version: str = "1.0.0"
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


settings = Settings()
