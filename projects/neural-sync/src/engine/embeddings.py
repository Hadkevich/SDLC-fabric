"""Embedding generation worker.

Generates skill and behavioral vector embeddings for DeveloperProfile and
ProjectProfile records. Embeddings are stored in the pgvector tables and
used for ANN-based candidate retrieval and semantic similarity scoring.

Two embedding model backends are supported:
  1. sentence-transformers (local, no API key required) — 384-dim output
  2. OpenAI text-embedding-3-small (API key required) — 1536-dim output

The backend is auto-detected at module import time. If neither is available,
a fallback deterministic random embedding is generated for dev/CI purposes.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import random
from enum import Enum
from typing import Optional

from src.core.settings import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Backend detection
# ─────────────────────────────────────────────────────────────────────────────

class EmbeddingBackend(str, Enum):
    SENTENCE_TRANSFORMERS = "sentence_transformers"
    OPENAI = "openai"
    RANDOM = "random"  # dev/CI fallback


# Native output dimension of each real model backend. The RANDOM fallback is
# dimension-flexible and always emits settings.embedding_dim.
_NATIVE_DIM: dict[EmbeddingBackend, int] = {
    EmbeddingBackend.SENTENCE_TRANSFORMERS: 384,
    EmbeddingBackend.OPENAI: 1536,
}


def _detect_backend() -> EmbeddingBackend:
    """Select an embedding backend whose output dimension matches the configured
    ``settings.embedding_dim``.

    This is the guardrail for the latent dimension bug: the pgvector columns are
    ``VECTOR(settings.embedding_dim)`` (default 1536), so a backend that emits a
    different width (sentence-transformers → 384) would fail every INSERT. We only
    pick such a backend when the configured dim actually equals its native width.
    """
    target = settings.embedding_dim

    # Local, key-free — but only when the target dim matches (i.e. EMBEDDING_DIM=384).
    if _NATIVE_DIM[EmbeddingBackend.SENTENCE_TRANSFORMERS] == target:
        try:
            import sentence_transformers  # noqa: F401
            return EmbeddingBackend.SENTENCE_TRANSFORMERS
        except ImportError:
            pass

    # OpenAI — only when the target dim matches (1536) and a key is configured.
    if _NATIVE_DIM[EmbeddingBackend.OPENAI] == target and os.getenv("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401
            return EmbeddingBackend.OPENAI
        except ImportError:
            pass

    logger.warning(
        "No dimension-compatible embedding backend for dim=%d "
        "(sentence-transformers=384, openai=1536). "
        "Using deterministic random embeddings — suitable for dev/CI only.",
        target,
    )
    return EmbeddingBackend.RANDOM


_BACKEND = _detect_backend()
_ST_MODEL = None  # lazy-loaded sentence-transformers model


# ─────────────────────────────────────────────────────────────────────────────
# Embedding dimension constants
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDING_DIM_MAP: dict[EmbeddingBackend, int] = {
    EmbeddingBackend.SENTENCE_TRANSFORMERS: 384,
    EmbeddingBackend.OPENAI: 1536,
    # RANDOM is dimension-flexible — it mirrors the configured column dimension.
    EmbeddingBackend.RANDOM: settings.embedding_dim,
}

MODEL_NAME_MAP: dict[EmbeddingBackend, str] = {
    EmbeddingBackend.SENTENCE_TRANSFORMERS: "sentence-transformers/all-MiniLM-L6-v2",
    EmbeddingBackend.OPENAI: "text-embedding-3-small",
    EmbeddingBackend.RANDOM: "random-unit-vector-dev",
}

MODEL_VERSION_MAP: dict[EmbeddingBackend, str] = {
    EmbeddingBackend.SENTENCE_TRANSFORMERS: "v1",
    EmbeddingBackend.OPENAI: "3-small-2024-02-15",
    EmbeddingBackend.RANDOM: "dev-1.0",
}


def get_embedding_dim() -> int:
    if _BACKEND == EmbeddingBackend.RANDOM:
        return settings.embedding_dim
    return EMBEDDING_DIM_MAP[_BACKEND]


def get_model_name() -> str:
    return MODEL_NAME_MAP[_BACKEND]


def get_model_version() -> str:
    return MODEL_VERSION_MAP[_BACKEND]


# ─────────────────────────────────────────────────────────────────────────────
# Encoding templates (matching data-model.json vector_embedding_schema)
# ─────────────────────────────────────────────────────────────────────────────

def encode_developer_skill_text(
    skills: list[str],
    preferred_stack: list[str],
    experience_years: int,
    project_history: list,
) -> str:
    """Build the text representation used to generate the skill embedding."""
    history_summary = "; ".join(
        f"{h.get('project_name', 'unknown')} ({h.get('role', '')})"
        for h in (project_history or [])[:5]
        if isinstance(h, dict)
    )
    parts = [
        f"Skills: {', '.join(skills)}.",
        f"Preferred stack: {', '.join(preferred_stack)}.",
        f"Experience: {experience_years} years.",
    ]
    if history_summary:
        parts.append(f"Past projects: {history_summary}.")
    return " ".join(parts)


def encode_developer_behavioral_text(
    work_style_vector: list[float],
    motivation_vector: list[float],
    career_goals: list[str],
) -> str:
    """
    Build the text representation used to generate the behavioral embedding.
    Raw vector values are encoded for embedding purposes only — they are
    NEVER passed to Claude prompts or included in API responses.
    """
    ws_str = ", ".join(f"{v:.3f}" for v in work_style_vector)
    mv_str = ", ".join(f"{v:.3f}" for v in motivation_vector)
    goals_str = "; ".join(career_goals)
    return (
        f"Work style dimensions: {ws_str}. "
        f"Motivation dimensions: {mv_str}. "
        f"Career goals: {goals_str}."
    )


def encode_project_skill_text(
    required_skills: list[str],
    team_structure: object,
    growth_opportunities: list[str],
    innovation_level: float,
) -> str:
    """Build the text representation used to generate the project skill embedding."""
    return (
        f"Required skills: {', '.join(required_skills)}. "
        f"Team: {str(team_structure)}. "
        f"Growth opportunities: {', '.join(growth_opportunities)}. "
        f"Innovation level: {innovation_level:.2f}."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Core embedding function
# ─────────────────────────────────────────────────────────────────────────────

def _random_unit_vector(text: str, dim: int) -> list[float]:
    """Generate a deterministic random unit vector seeded from text hash.
    Used as a fallback for dev/CI environments without a real embedding model."""
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v))
    if norm < 1e-9:
        v = [1.0] + [0.0] * (dim - 1)
        norm = 1.0
    return [x / norm for x in v]


def embed_text(text: str) -> list[float]:
    """
    Embed a text string using the active backend.
    Returns a normalized float vector of length ``get_embedding_dim()``.
    """
    global _ST_MODEL

    if _BACKEND == EmbeddingBackend.RANDOM:
        vector = _random_unit_vector(text, get_embedding_dim())
    elif _BACKEND == EmbeddingBackend.SENTENCE_TRANSFORMERS:
        if _ST_MODEL is None:
            from sentence_transformers import SentenceTransformer
            _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        embedding = _ST_MODEL.encode(text, normalize_embeddings=True)
        vector = [float(x) for x in embedding]
    elif _BACKEND == EmbeddingBackend.OPENAI:
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            input=text,
            model="text-embedding-3-small",
        )
        vector = list(response.data[0].embedding)
    else:
        raise RuntimeError(f"Unknown embedding backend: {_BACKEND}")

    # Fail loud at generation time if a backend's width drifts from the configured
    # column dimension — otherwise the mismatch surfaces as an opaque pgvector
    # INSERT error inside a background task and silently flips embedding_status='failed'.
    expected = get_embedding_dim()
    if len(vector) != expected:
        raise RuntimeError(
            f"Embedding backend {_BACKEND.value} produced dim {len(vector)} "
            f"but column/config expects {expected}. Set EMBEDDING_DIM to match the "
            f"backend, or use a dimension-compatible backend."
        )
    return vector


# ─────────────────────────────────────────────────────────────────────────────
# High-level embedding generators (called by background tasks in API routes)
# ─────────────────────────────────────────────────────────────────────────────

def generate_developer_embeddings(
    developer_id: str,
    skills: list[str],
    preferred_stack: list[str],
    experience_years: int,
    project_history: list,
    work_style_vector: list[float],
    motivation_vector: list[float],
    career_goals: list[str],
) -> dict[str, list[float]]:
    """
    Generate skill and behavioral embeddings for a developer.
    Returns {'skill': [...], 'behavioral': [...]}.
    """
    skill_text = encode_developer_skill_text(
        skills, preferred_stack, experience_years, project_history
    )
    behavioral_text = encode_developer_behavioral_text(
        work_style_vector, motivation_vector, career_goals
    )

    return {
        "skill": embed_text(skill_text),
        "behavioral": embed_text(behavioral_text),
    }


def generate_project_embedding(
    project_id: str,
    required_skills: list[str],
    team_structure: object,
    growth_opportunities: list[str],
    innovation_level: float,
) -> list[float]:
    """Generate a skill embedding for a project."""
    text = encode_project_skill_text(
        required_skills, team_structure, growth_opportunities, innovation_level
    )
    return embed_text(text)
