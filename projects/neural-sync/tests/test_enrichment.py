"""Tests for the profile-enrichment service (src/services/enrichment.py).

Acceptance-Criteria §3.1: raw operator text → structured vectors. These cover the
deterministic heuristic path (no API key / no network), which is what runs in CI.
"""
from __future__ import annotations

import pytest

from src.services.enrichment import enrich_profile, EnrichmentResult


@pytest.fixture(autouse=True)
def _force_heuristic_path(monkeypatch):
    """Pin the no-key precondition the whole file documents.

    These tests assert the deterministic heuristic path. The runtime container may
    carry a live GEMINI_API_KEY, which would route ``enrich_profile`` through Gemini
    and make the assertions depend on live model output / quota (order-dependent
    flakiness). Forcing the key empty keeps every test here deterministic.
    """
    from src.core.settings import settings
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)


SAMPLE_CV = """
Senior backend engineer with 7 years experience. Built async Python (FastAPI) +
PostgreSQL services and led a cross-functional team, mentored juniors, and shipped at a
fast-paced startup. Worked fully remote. Comfortable with Docker, Kubernetes, and AWS.
I want to move to ML and grow into a lead role.
"""

SAMPLE_GIT = "commit: add pytorch training loop; refactor ml pipeline; wrote docs"


def test_enrich_extracts_known_skills_deterministically():
    a = enrich_profile(SAMPLE_CV, SAMPLE_GIT)
    b = enrich_profile(SAMPLE_CV, SAMPLE_GIT)
    assert isinstance(a, EnrichmentResult)
    assert a.skills == b.skills  # deterministic
    # canonical tokens (aligned with the matching alias map)
    for expected in ("python", "fastapi", "postgres", "docker", "k8s", "aws", "ml"):
        assert expected in a.skills, f"missing skill {expected}: {a.skills}"


def test_enrich_returns_valid_8dim_vectors_in_range():
    r = enrich_profile(SAMPLE_CV)
    assert len(r.work_style) == 8
    assert len(r.motivation_vector) == 8
    assert all(0.0 <= x <= 1.0 for x in r.work_style)
    assert all(0.0 <= x <= 1.0 for x in r.motivation_vector)


def test_enrich_provenance_is_heuristic_without_key():
    # No GEMINI_API_KEY in the test env → heuristic path.
    r = enrich_profile(SAMPLE_CV)
    assert r.provenance == "heuristic"


def test_enrich_signals_move_vectors_off_neutral():
    """Remote/async/leadership/learning signals should push the relevant dims up."""
    r = enrich_profile(SAMPLE_CV)
    # remote_preference (index 7) elevated by "remote"/"async"
    assert r.work_style[7] > 0.5
    # collaboration (index 0) elevated by "led"/"mentored"/"cross-functional"
    assert r.work_style[0] > 0.5
    # growth (motivation index 1) elevated by "move to"/"grow"
    assert r.motivation_vector[1] > 0.5


def test_enrich_extracts_career_goals():
    r = enrich_profile(SAMPLE_CV)
    assert r.career_goals  # "move to ML ..." captured
    joined = " ".join(r.career_goals).lower()
    assert "ml" in joined or "lead" in joined


def test_enrich_empty_extra_text_still_valid():
    r = enrich_profile("Python developer, 2 years.", "", "")
    assert "python" in r.skills
    assert len(r.work_style) == 8 and len(r.motivation_vector) == 8


def test_enrich_endpoint_returns_draft():
    """POST /developers/enrich returns a DeveloperProfileCreate-shaped draft."""
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.conftest import dev_auth_headers

    client = TestClient(app)
    resp = client.post(
        "/api/v1/developers/enrich",
        headers=dev_auth_headers(),
        json={
            "cv_text": "Python + FastAPI engineer, 5 years, remote, want to move to ML",
            "timezone": "Europe/Warsaw",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_self_reported"] is False
    assert body["provenance"] in ("llm", "heuristic")
    assert "python" in body["skills"]
    assert body["experience_years"] == 5
    assert len(body["work_style"]) == 8
    assert len(body["motivation_vector"]) == 8
