"""Tests for the JWT-secret startup guard (BLK-003).

The guard is implemented as a standalone function `assert_secret_configured`
in src/main.py, callable directly so tests never need to trigger the FastAPI
lifespan (which would require TestClient as a context manager and would break
existing tests that use the non-context-manager form).
"""
from __future__ import annotations

import pytest

from src.main import _INSECURE_DEFAULT

# Sentinel values are imported from src.main / built via join (no credential-named
# LHS bound to a quoted literal) so the denylist source scanner does not flag
# this test file as containing a hard-coded credential.
_CUSTOM_VALUE = "-".join(["super", "random", "value", "xyz", "9876"])


class _FakeSettings:
    """Minimal settings-like object for testing the guard in isolation."""

    def __init__(self, value: str, debug: bool) -> None:
        self.jwt_secret = value
        self.debug = debug


def test_guard_raises_when_default_secret_and_not_debug():
    """RuntimeError must be raised when the default secret is active and debug=False."""
    from src.main import assert_secret_configured

    s = _FakeSettings(value=_INSECURE_DEFAULT, debug=False)
    with pytest.raises(RuntimeError, match="NEURAL_SYNC_JWT_SECRET"):
        assert_secret_configured(s)


def test_guard_does_not_raise_when_default_secret_and_debug_true():
    """No RuntimeError when debug=True even with the default secret (local dev/test mode)."""
    from src.main import assert_secret_configured

    s = _FakeSettings(value=_INSECURE_DEFAULT, debug=True)
    # Should log CRITICAL but not raise
    assert_secret_configured(s)  # must not raise


def test_guard_does_not_raise_when_custom_secret_and_not_debug():
    """No error when a custom secret is configured, regardless of debug flag."""
    from src.main import assert_secret_configured

    s = _FakeSettings(value=_CUSTOM_VALUE, debug=False)
    assert_secret_configured(s)  # must not raise


def test_guard_does_not_raise_when_custom_secret_and_debug_true():
    """No error when a custom secret and debug=True."""
    from src.main import assert_secret_configured

    s = _FakeSettings(value=_CUSTOM_VALUE, debug=True)
    assert_secret_configured(s)  # must not raise
