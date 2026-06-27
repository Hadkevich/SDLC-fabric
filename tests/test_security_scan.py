"""Regression tests for the reviewer security baseline (orchestrator.validation).

scan_source must flag genuinely dangerous code (eval, real hard-coded secrets in
shipped source) WITHOUT false-positiving on test assertions that merely *mention*
a cookie/token name.

Regression for the NEURAL SYNC redeploy halt: the line

    assert "refresh_token=" in set_cookie

in tests/test_auth.py was wrongly flagged as a hard-coded secret, blocking the
whole pipeline unrecoverably at the code_review gate. The secret regex was
matching the string literal's *closing* quote as a value-opening quote and
capturing the trailing code (" in set_cookie") as the "secret".
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.validation import scan_source


def _tree(root: Path, files: dict) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


# ─── the false positive that halted the run (must NOT be flagged) ──────────────

def test_cookie_name_assertion_in_test_file_is_not_a_secret(tmp_path):
    _tree(tmp_path, {
        "tests/test_auth.py": 'def test_login():\n    assert "refresh_token=" in set_cookie\n',
    })
    assert scan_source(tmp_path) == []


def test_cookie_name_assertion_in_source_is_not_a_secret(tmp_path):
    # Same captured-trailing-code pattern, but in non-test source: the value
    # would start with whitespace (" in set_cookie"), which is never a secret.
    _tree(tmp_path, {
        "src/util.py": 'def has_cookie(set_cookie):\n    return "refresh_token=" in set_cookie\n',
    })
    assert scan_source(tmp_path) == []


def test_secret_fixture_in_test_file_is_skipped(tmp_path):
    # Test fixtures legitimately hardcode fake credentials — the secret heuristic
    # must not block the pipeline on them.
    _tree(tmp_path, {
        "tests/test_creds.py": 'API_KEY = "sk-livedeadbeef12345"\n',
    })
    assert scan_source(tmp_path) == []


# ─── real secrets in shipped source MUST still be flagged ──────────────────────

def test_real_hardcoded_secret_in_source_is_flagged(tmp_path):
    _tree(tmp_path, {"src/config.py": 'API_KEY = "sk-livedeadbeef12345"\n'})
    hits = scan_source(tmp_path)
    assert any("hard-coded secret" in h for h, _ in hits), hits


def test_underscore_prefixed_secret_in_source_is_flagged(tmp_path):
    # Guard against an over-blunt "\b/(?<!\\w)" fix that would also stop matching
    # a real `my_password = "..."` assignment.
    _tree(tmp_path, {"src/config.py": 'my_password = "hunter2value9xx"\n'})
    hits = scan_source(tmp_path)
    assert any("hard-coded secret" in h for h, _ in hits), hits


# ─── code-execution patterns stay flagged everywhere (incl. tests) ─────────────

def test_eval_is_flagged_in_both_source_and_tests(tmp_path):
    _tree(tmp_path, {
        "src/danger.py": "x = eval(user_input)\n",
        "tests/test_danger.py": "y = eval(payload)\n",
    })
    hits = scan_source(tmp_path)
    assert any("danger.py" in h and "eval" in h for h, _ in hits), hits
    assert any("test_danger.py" in h and "eval" in h for h, _ in hits), hits
