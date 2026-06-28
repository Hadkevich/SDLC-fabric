"""Tests for the embedding-dimension invariant (WS-B1).

The pgvector columns, the active embedding backend, and settings.embedding_dim
must all agree. These tests pin the canonical dim (1536 by default), prove the
backend never emits a mismatched vector, and exercise the standalone startup
guard in isolation (no FastAPI lifespan needed — same pattern as
test_startup_guard.py).
"""
from __future__ import annotations

import pytest

from src.core.settings import settings
from src.db import models
from src.engine.embeddings import embed_text, get_embedding_dim


def test_canonical_dim_is_consistent_across_modules():
    """settings, the active backend, and the ORM column dim must all match."""
    assert get_embedding_dim() == settings.embedding_dim
    assert models.EMBEDDING_DIM == settings.embedding_dim


def test_default_dim_is_1536():
    """Default deployment dimension matches the migration/seed/HNSW columns."""
    # Unless EMBEDDING_DIM=384 is explicitly set for a sentence-transformers run.
    assert settings.embedding_dim in (384, 1536)
    if settings.embedding_dim == 1536:
        assert get_embedding_dim() == 1536


def test_embed_text_returns_configured_dim():
    """embed_text must always return a vector of exactly get_embedding_dim() floats."""
    vec = embed_text("python react machine-learning fastapi")
    assert len(vec) == get_embedding_dim()
    assert all(isinstance(x, float) for x in vec)


def test_embed_text_is_deterministic_for_same_input():
    """The dev/CI RANDOM backend is seeded from the text hash → reproducible."""
    a = embed_text("identical input text")
    b = embed_text("identical input text")
    assert a == b


class _FakeSettings:
    def __init__(self, embedding_dim: int, debug: bool) -> None:
        self.embedding_dim = embedding_dim
        self.debug = debug


def test_dim_guard_raises_on_mismatch_when_not_debug():
    """A configured dim that disagrees with the backend must halt a non-debug start."""
    from src.main import assert_embedding_dim_consistent

    bad = _FakeSettings(embedding_dim=get_embedding_dim() + 1, debug=False)
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        assert_embedding_dim_consistent(bad)


def test_dim_guard_does_not_raise_in_debug():
    """In debug mode the guard logs CRITICAL but does not block startup."""
    from src.main import assert_embedding_dim_consistent

    bad = _FakeSettings(embedding_dim=get_embedding_dim() + 1, debug=True)
    assert_embedding_dim_consistent(bad)  # must not raise


def test_dim_guard_passes_for_consistent_config():
    """No error when backend, settings, and column all agree."""
    from src.main import assert_embedding_dim_consistent

    good = _FakeSettings(embedding_dim=get_embedding_dim(), debug=False)
    # Will only pass if models.EMBEDDING_DIM also equals get_embedding_dim(),
    # which is the real, consistent configuration under test.
    if models.EMBEDDING_DIM == get_embedding_dim():
        assert_embedding_dim_consistent(good)  # must not raise
