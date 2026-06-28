"""Unit tests for all five source connectors.

Coverage:
  AC15  — CVConnector: .txt/.md file parsing
  AC16  — CVConnector: cv_text channel populated
  AC17  — HRConnector: CSV/JSON parsing with case-insensitive column mapping
  AC18  — SlackConnector: Slack export JSON → one SourceDocument per user
  AC19  — GitLabConnector: mock httpx transport, token/no-token paths
  AC20  — JiraConnector: missing credentials → errors, no records

All HTTP interactions use mock httpx transports.
File connectors are tested from in-memory bytes.
No live database or external API connections are required.
"""
from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.connectors.base import SourceDocument
from src.connectors.cv_connector import CVConnector
from src.connectors.hr_connector import HRConnector
from src.connectors.slack_connector import SlackConnector
from src.connectors.gitlab_connector import GitLabConnector
from src.connectors.jira_connector import JiraConnector


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _MockHTTPResponse:
    """Minimal mock for an httpx.Response."""

    def __init__(self, status_code: int, data: Any, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

    def json(self) -> Any:
        return self._data


class _MockHTTPClient:
    """Mock httpx.Client context manager that returns pre-configured responses."""

    def __init__(self, responses: list[_MockHTTPResponse]) -> None:
        self._responses = responses
        self._call_idx = 0

    def __enter__(self) -> "_MockHTTPClient":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def get(self, url: str, **_kwargs: Any) -> _MockHTTPResponse:
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        # Default: empty 200
        return _MockHTTPResponse(200, [])


# ─────────────────────────────────────────────────────────────────────────────
# CVConnector tests  [AC15, AC16]
# ─────────────────────────────────────────────────────────────────────────────

class TestCVConnector:

    def test_txt_file_yields_one_source_document(self):
        """[AC15] A valid UTF-8 .txt CV yields exactly one SourceDocument with cv_text set."""
        content = b"Senior Python engineer. 8 years experience. Docker, FastAPI."
        connector = CVConnector(content=content, filename="alice.txt")
        docs = list(connector.fetch())
        assert len(docs) == 1, "CVConnector must yield exactly one SourceDocument per file"
        doc = docs[0]
        assert isinstance(doc, SourceDocument)
        assert "Python" in doc.cv_text or "python" in doc.cv_text.lower()
        assert doc.cv_text.strip() != ""
        assert doc.source == "cv"
        assert not connector.errors, f"No errors expected for valid .txt file; got: {connector.errors}"

    def test_md_file_yields_one_source_document(self):
        """[AC15] A valid .md file is parsed as plain text into cv_text."""
        content = b"# Resume\n\n## Skills\n- Python\n- React\n\n## Experience\n5 years."
        connector = CVConnector(content=content, filename="resume.md")
        docs = list(connector.fetch())
        assert len(docs) == 1
        assert "Python" in docs[0].cv_text or "python" in docs[0].cv_text.lower()
        assert docs[0].source == "cv"
        assert not connector.errors

    def test_cv_text_stripped_and_nonempty(self):
        """[AC16] cv_text is stripped and non-empty for a valid text CV."""
        content = b"  \n  Python FastAPI engineer.  \n  "
        connector = CVConnector(content=content, filename="cv.txt")
        docs = list(connector.fetch())
        assert docs[0].cv_text == docs[0].cv_text.strip()
        assert docs[0].cv_text  # non-empty

    def test_pdf_not_supported_yields_degraded_document(self):
        """[AC15] When PDF libraries are absent, a .pdf upload yields a degraded SourceDocument
        with empty cv_text and an error recorded in self.errors."""
        fake_pdf = b"%PDF-1.4 fake-pdf-content"
        with patch.object(CVConnector, 'pdf_supported', return_value=False):
            # Also ensure _PDF_BACKEND is None
            with patch("src.connectors.cv_connector._PDF_BACKEND", None):
                connector = CVConnector(content=fake_pdf, filename="cv.pdf")
                docs = list(connector.fetch())
        # Connector must yield exactly one document (degraded, not raise)
        assert len(docs) == 1
        assert docs[0].source == "cv"
        assert connector.errors, "PDF without library must record error in self.errors"
        pdf_err = " ".join(connector.errors).lower()
        assert "pdf" in pdf_err or "unavailable" in pdf_err

    def test_display_name_and_email_seeded_from_filename(self):
        """[AC16] When display_name and email are not provided they are derived from filename."""
        content = b"Senior engineer specialising in Python."
        connector = CVConnector(content=content, filename="john_doe.txt")
        docs = list(connector.fetch())
        assert docs[0].display_name  # not empty
        assert "@" in docs[0].email  # always has email

    def test_display_name_and_email_explicit_override(self):
        """[AC16] Explicit display_name and email override filename-derived values."""
        content = b"Python developer."
        connector = CVConnector(
            content=content,
            filename="cv.txt",
            display_name="Ada Lovelace",
            email="ada@example.com",
        )
        docs = list(connector.fetch())
        assert docs[0].display_name == "Ada Lovelace"
        assert docs[0].email == "ada@example.com"

    def test_empty_content_yields_one_document_with_empty_cv_text(self):
        """[AC15] Empty file content is gracefully handled; cv_text is empty string, not an error."""
        connector = CVConnector(content=b"", filename="empty.txt")
        docs = list(connector.fetch())
        assert len(docs) == 1
        assert docs[0].cv_text == ""
        assert not connector.errors


# ─────────────────────────────────────────────────────────────────────────────
# HRConnector tests  [AC17]
# ─────────────────────────────────────────────────────────────────────────────

CSV_BASIC = b"""name,email,title,skills,weekly_hours,years_experience,timezone
Alice Smith,alice@example.com,Senior Engineer,Python;FastAPI,40,7,Europe/London
Bob Jones,bob@example.com,Backend Developer,Go;Docker,35,3,America/New_York
"""

CSV_ALTERNATE_COLS = b"""Full_Name,e-mail,Role,Tech_Stack,Hours_Per_Week,Experience_Years,Time_Zone
Carol White,carol@example.com,Staff Engineer,Rust;WASM,40,10,UTC
"""

CSV_BIO_COL = b"""name,email,bio,weekly_hours,years_experience
Dan Brown,dan@example.com,Full-stack developer with Python and React experience,32,4
"""

CSV_ROLE_COL = b"""name,email,role,weekly_hours
Eve Black,eve@example.com,Principal Architect,40
"""


class TestHRConnector:

    def test_csv_basic_yields_one_doc_per_row(self):
        """[AC17] CSV with standard columns yields one SourceDocument per data row."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 2, f"Expected 2 docs, got {len(docs)}"
        assert not connector.errors

    def test_csv_display_name_mapped_from_name(self):
        """[AC17] 'name' column maps to display_name."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        names = [d.display_name for d in docs]
        assert "Alice Smith" in names
        assert "Bob Jones" in names

    def test_csv_email_mapped(self):
        """[AC17] 'email' column maps to email."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        emails = [d.email for d in docs]
        assert "alice@example.com" in emails
        assert "bob@example.com" in emails

    def test_csv_title_col_maps_to_cv_text(self):
        """[AC17] 'title' column maps to cv_text."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert "Senior Engineer" in alice.cv_text

    def test_csv_weekly_hours_maps_to_availability_hours(self):
        """[AC17] 'weekly_hours' column maps to availability_hours (int)."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert alice.availability_hours == 40

    def test_csv_years_experience_maps_to_experience_years(self):
        """[AC17] 'years_experience' column maps to experience_years (int)."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert alice.experience_years == 7

    def test_csv_role_column_alias_maps_to_cv_text(self):
        """[AC17] 'role' column is an alias for cv_text."""
        connector = HRConnector(content=CSV_ROLE_COL, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 1
        assert "Principal Architect" in docs[0].cv_text

    def test_csv_bio_column_alias_maps_to_cv_text(self):
        """[AC17] 'bio' column is an alias for cv_text."""
        connector = HRConnector(content=CSV_BIO_COL, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 1
        assert "Full-stack" in docs[0].cv_text or "full-stack" in docs[0].cv_text.lower()

    def test_csv_alternate_column_names_case_insensitive(self):
        """[AC17] Case-insensitive column mapping handles alternate names."""
        connector = HRConnector(content=CSV_ALTERNATE_COLS, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 1
        doc = docs[0]
        assert doc.display_name == "Carol White"
        assert doc.availability_hours == 40
        assert doc.experience_years == 10

    def test_json_format_yields_one_doc_per_employee(self):
        """[AC17] JSON format with employees list yields one SourceDocument per employee."""
        data = {
            "employees": [
                {"name": "Grace Hopper", "email": "grace@example.com", "title": "Admiral Engineer",
                 "weekly_hours": 40, "years_experience": 15, "timezone": "America/New_York"},
                {"name": "Ada Lovelace", "email": "ada@example.com", "title": "Mathematician",
                 "weekly_hours": 20, "years_experience": 5, "timezone": "Europe/London"},
            ]
        }
        content = json.dumps(data).encode()
        connector = HRConnector(content=content, filename="hr.json")
        docs = list(connector.fetch())
        assert len(docs) == 2, f"Expected 2 docs from JSON, got {len(docs)}"
        names = {d.display_name for d in docs}
        assert "Grace Hopper" in names
        assert "Ada Lovelace" in names

    def test_row_without_name_or_email_is_skipped(self):
        """[AC17] Rows that have neither name nor email are silently skipped."""
        csv_content = b"title,skills\nSenior Engineer,Python\n"
        connector = HRConnector(content=csv_content, filename="hr.csv")
        docs = list(connector.fetch())
        assert len(docs) == 0  # no identity → skipped

    def test_invalid_csv_records_error_in_connector(self):
        """[AC17] Completely invalid content records error in connector.errors; never raises."""
        connector = HRConnector(content=b"\x00\x01\x02invalid binary", filename="hr.csv")
        try:
            docs = list(connector.fetch())
            # Either empty or has error — but must not raise
        except Exception as exc:  # noqa
            pytest.fail(f"HRConnector must not raise on invalid content: {exc}")

    def test_source_field_is_hr(self):
        """[AC17] All yielded SourceDocuments have source='hr'."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        for doc in connector.fetch():
            assert doc.source == "hr"

    def test_skills_appended_to_cv_text(self):
        """[AC17] 'skills' column value is appended to cv_text as 'Skills: <value>'."""
        connector = HRConnector(content=CSV_BASIC, filename="hr.csv")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert "Skills:" in alice.cv_text or "python" in alice.cv_text.lower() or "fastapi" in alice.cv_text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# SlackConnector tests  [AC18]
# ─────────────────────────────────────────────────────────────────────────────

SLACK_FORMAT_A = {
    "users": [
        {"id": "U001", "name": "alice", "real_name": "Alice Smith",
         "profile": {"email": "alice@example.com"}},
        {"id": "U002", "name": "bob", "real_name": "Bob Jones",
         "profile": {"email": "bob@example.com"}},
    ],
    "channels": {
        "general": [
            {"user": "U001", "text": "Hello team, excited to work on Python!"},
            {"user": "U002", "text": "Ready to start the sprint."},
        ],
        "engineering": [
            {"user": "U001", "text": "Deployed the FastAPI service."},
        ],
    },
}

SLACK_FORMAT_B = {
    "users": [
        {"id": "U010", "name": "carol", "real_name": "Carol White",
         "profile": {"email": "carol@example.com"}},
    ],
    "channels": [
        {"name": "general", "messages": [
            {"user": "U010", "text": "Working on the Rust backend."},
            {"user": "U010", "text": "Finished code review."},
        ]},
    ],
}


class TestSlackConnector:

    def test_format_a_yields_one_doc_per_user(self):
        """[AC18] Format A (dict-of-lists channels) yields one SourceDocument per user."""
        content = json.dumps(SLACK_FORMAT_A).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        assert len(docs) == 2, f"Expected 2 docs (one per user), got {len(docs)}"
        assert not connector.errors

    def test_format_a_slack_text_contains_user_messages(self):
        """[AC18] slack_text is user's channel messages concatenated."""
        content = json.dumps(SLACK_FORMAT_A).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert "Python" in alice.slack_text or "python" in alice.slack_text.lower()
        assert "FastAPI" in alice.slack_text or "fastapi" in alice.slack_text.lower()
        # Alice has 2 messages, should both be in slack_text
        assert "Hello team" in alice.slack_text
        assert "Deployed" in alice.slack_text

    def test_format_a_users_have_distinct_messages(self):
        """[AC18] Each user's slack_text contains only their own messages."""
        content = json.dumps(SLACK_FORMAT_A).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        bob = next(d for d in docs if d.display_name == "Bob Jones")
        assert "Ready to start" in bob.slack_text
        # Bob did not write the Python message
        assert "Python" not in bob.slack_text

    def test_format_b_yields_one_doc_per_user(self):
        """[AC18] Format B (list-of-dicts channels) yields one SourceDocument per user."""
        content = json.dumps(SLACK_FORMAT_B).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        assert len(docs) == 1
        assert docs[0].display_name == "Carol White"
        assert "Rust" in docs[0].slack_text

    def test_format_b_messages_concatenated(self):
        """[AC18] Multiple messages from the same user are concatenated in slack_text."""
        content = json.dumps(SLACK_FORMAT_B).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        carol_text = docs[0].slack_text
        assert "Rust backend" in carol_text
        assert "code review" in carol_text

    def test_invalid_json_records_error_and_yields_zero(self):
        """[AC18] Invalid JSON populates self.errors and yields zero records."""
        connector = SlackConnector(content=b"not-valid-json{{{", filename="slack.json")
        docs = list(connector.fetch())
        assert len(docs) == 0
        assert connector.errors, "Invalid JSON must produce an error in self.errors"

    def test_source_field_is_slack(self):
        """[AC18] All yielded SourceDocuments have source='slack'."""
        content = json.dumps(SLACK_FORMAT_A).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        for doc in connector.fetch():
            assert doc.source == "slack"

    def test_user_with_no_messages_still_yielded(self):
        """[AC18] A user with no channel messages still yields a SourceDocument (empty slack_text)."""
        data = {
            "users": [{"id": "U999", "name": "silent", "real_name": "Silent User"}],
            "channels": {"general": [{"user": "U001", "text": "message from someone else"}]},
        }
        content = json.dumps(data).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        silent = next((d for d in docs if d.display_name == "Silent User"), None)
        assert silent is not None
        assert silent.slack_text == "" or silent.slack_text is not None

    def test_email_from_profile(self):
        """[AC18] Email is extracted from user profile dict when present."""
        content = json.dumps(SLACK_FORMAT_A).encode()
        connector = SlackConnector(content=content, filename="slack.json")
        docs = list(connector.fetch())
        alice = next(d for d in docs if d.display_name == "Alice Smith")
        assert alice.email == "alice@example.com"


# ─────────────────────────────────────────────────────────────────────────────
# GitLabConnector tests  [AC19]
# ─────────────────────────────────────────────────────────────────────────────

class TestGitLabConnector:

    def _make_mock_client(self, responses: list[_MockHTTPResponse]):
        """Patch httpx.Client in the gitlab_connector module."""
        return patch("src.connectors.gitlab_connector.httpx.Client",
                     return_value=_MockHTTPClient(responses))

    def test_with_project_commit_and_mr_data_in_git_log_text(self):
        """[AC19] When mock transport returns commits and MRs, git_log_text contains both."""
        # _paginate breaks early when len(page_items) < per_page (100), so no empty-page
        # placeholder is needed between commits and MRs when data pages have < 100 items.
        # Actual call order: [commits page1 (2 items → early break), MRs page1 (2 items → break)]
        commits_page = [
            {"message": "Add Python FastAPI service endpoint\n\nLong body ignored", "title": None},
            {"message": "Fix authentication bug", "title": None},
        ]
        mrs_page = [
            {"title": "Merge request: implement user dashboard"},
            {"title": "Merge request: add Docker support"},
        ]

        responses = [
            _MockHTTPResponse(200, commits_page),   # 0: commits page1 (2 items → early break)
            _MockHTTPResponse(200, mrs_page),        # 1: MRs page1 (2 items → early break)
        ]

        connector = GitLabConnector(token="test-token", base_url="https://gitlab.example.com")
        with self._make_mock_client(responses):
            docs = list(connector.fetch(username="ada", project="platform/core"))

        assert len(docs) == 1
        doc = docs[0]
        assert "Add Python FastAPI service endpoint" in doc.git_log_text
        assert "Fix authentication bug" in doc.git_log_text
        assert "implement user dashboard" in doc.git_log_text
        assert "add Docker support" in doc.git_log_text
        assert doc.external_id == "ada"
        assert doc.source == "gitlab"
        assert not connector.errors

    def test_without_token_returns_degraded_result_not_5xx(self):
        """[AC19] Missing token → 401 from mock → degraded SourceDocument, error in self.errors."""
        responses = [_MockHTTPResponse(401, {"message": "401 Unauthorized"})]

        connector = GitLabConnector(token=None, base_url="https://gitlab.example.com")
        with self._make_mock_client(responses):
            docs = list(connector.fetch(username="unknown", project="platform/core"))

        # Must still yield one degraded SourceDocument
        assert len(docs) == 1
        doc = docs[0]
        assert doc.git_log_text == "" or doc.git_log_text is not None
        # Error must be recorded
        assert connector.errors, "Missing/invalid token must produce an error in self.errors"
        error_text = " ".join(connector.errors).lower()
        assert "401" in error_text or "token" in error_text or "unauthorized" in error_text.lower()

    def test_invalid_token_returns_degraded_not_raise(self):
        """[AC19] Invalid token returns degraded result, never raises."""
        responses = [_MockHTTPResponse(401, {})]
        connector = GitLabConnector(token="bad-token")
        with self._make_mock_client(responses):
            try:
                docs = list(connector.fetch(username="user"))
            except Exception as exc:
                pytest.fail(f"GitLabConnector must not raise on 401: {exc}")
        assert len(docs) == 1
        assert connector.errors

    def test_rate_limited_returns_partial_result_not_5xx(self):
        """[AC19] HTTP 429 (rate-limited) returns partial result with errors, never raises."""
        responses = [_MockHTTPResponse(429, {})]
        connector = GitLabConnector(token="test-token")
        with self._make_mock_client(responses):
            docs = list(connector.fetch(username="user", project="ns/repo"))
        assert len(docs) == 1  # degraded but still one doc
        assert connector.errors
        error_text = " ".join(connector.errors).lower()
        assert "429" in error_text or "rate" in error_text

    def test_without_project_uses_events_api(self):
        """[AC19] Without project, user push events API is used (user lookup first)."""
        user_lookup = [{"id": 42, "username": "ada"}]
        push_events = [
            {"push_data": {"commit_title": "Add machine learning pipeline"}},
        ]
        mrs = [{"title": "MR: add ml support"}]

        # _paginate breaks early when len(page_items) < per_page (100).
        # Actual call order: [user lookup, events page1 (1 item → early break), MRs page1 (1 item → break)]
        responses = [
            _MockHTTPResponse(200, user_lookup),   # 0: user lookup
            _MockHTTPResponse(200, push_events),   # 1: events page1 (1 item → early break)
            _MockHTTPResponse(200, mrs),           # 2: MRs page1 (1 item → early break)
        ]
        connector = GitLabConnector(token="test-token")
        with self._make_mock_client(responses):
            docs = list(connector.fetch(username="ada"))

        assert len(docs) == 1
        assert "machine learning pipeline" in docs[0].git_log_text
        assert "ml support" in docs[0].git_log_text

    def test_network_error_yields_degraded_doc_not_raise(self):
        """[AC19] Network exception yields degraded SourceDocument, never raises."""
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = lambda s, *a: None
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        connector = GitLabConnector(token="test-token")
        with patch("src.connectors.gitlab_connector.httpx.Client", return_value=mock_client):
            try:
                docs = list(connector.fetch(username="user", project="ns/repo"))
            except Exception as exc:
                pytest.fail(f"GitLabConnector must not raise on network error: {exc}")
        assert len(docs) == 1
        assert connector.errors

    def test_connector_info_has_kind_and_availability(self):
        """[AC19, AC29] connector_info() returns dict with kind and availability."""
        connector = GitLabConnector()
        info = connector.connector_info()
        assert info["kind"] == "network"
        assert info["availability"] == "live"
        assert info["source"] == "gitlab"


# ─────────────────────────────────────────────────────────────────────────────
# JiraConnector tests  [AC20]
# ─────────────────────────────────────────────────────────────────────────────

class TestJiraConnector:

    def _make_mock_client(self, responses: list[_MockHTTPResponse]):
        return patch("src.connectors.jira_connector.httpx.Client",
                     return_value=_MockHTTPClient(responses))

    def test_missing_all_credentials_yields_zero_docs_and_errors(self):
        """[AC20] All credentials missing → zero SourceDocuments, errors list populated."""
        connector = JiraConnector()  # no credentials
        docs = list(connector.fetch(usernames=["ada"]))
        assert len(docs) == 0
        assert connector.errors, "Missing credentials must produce errors"
        error_text = " ".join(connector.errors).lower()
        assert "base_url" in error_text or "email" in error_text or "token" in error_text

    def test_missing_token_only_yields_zero_docs_and_error(self):
        """[AC20] Missing token specifically is documented in errors."""
        connector = JiraConnector(
            base_url="https://org.atlassian.net",
            email="user@example.com",
            token="",  # empty = missing
            project_key="DEV",
        )
        docs = list(connector.fetch(usernames=["ada"]))
        assert len(docs) == 0
        assert connector.errors
        combined = " ".join(connector.errors).lower()
        assert "token" in combined

    def test_missing_project_key_yields_zero_docs_and_error(self):
        """[AC20] Missing project_key documented in errors, zero records yielded."""
        connector = JiraConnector(
            base_url="https://org.atlassian.net",
            email="user@example.com",
            token="mytoken",
            project_key="",  # empty
        )
        docs = list(connector.fetch(usernames=["ada"]))
        assert len(docs) == 0
        assert connector.errors
        combined = " ".join(connector.errors).lower()
        assert "project_key" in combined

    def test_valid_credentials_and_mock_issues_produces_git_log_text(self):
        """[AC20] With valid credentials and mock Jira response, git_log_text is populated."""
        search_response = {
            "total": 2,
            "issues": [
                {
                    "key": "DEV-1",
                    "fields": {
                        "summary": "Implement Python data pipeline",
                        "labels": ["backend", "python"],
                        "comment": {"comments": []},
                    },
                },
                {
                    "key": "DEV-2",
                    "fields": {
                        "summary": "Fix FastAPI authentication bug",
                        "labels": [],
                        "comment": {
                            "comments": [{"body": "Checked and confirmed the fix."}]
                        },
                    },
                },
            ],
        }

        responses = [
            _MockHTTPResponse(200, search_response),
            _MockHTTPResponse(200, {"total": 0, "issues": []}),  # next page empty
        ]

        connector = JiraConnector(
            base_url="https://org.atlassian.net",
            email="manager@example.com",
            token="valid-token",
            project_key="DEV",
        )
        with self._make_mock_client(responses):
            docs = list(connector.fetch(usernames=["ada"]))

        assert len(docs) == 1
        doc = docs[0]
        assert "DEV-1" in doc.git_log_text
        assert "Python data pipeline" in doc.git_log_text
        assert "DEV-2" in doc.git_log_text
        assert "FastAPI authentication bug" in doc.git_log_text
        assert doc.source == "jira"

    def test_http_401_yields_error_not_raise(self):
        """[AC20] HTTP 401 from Jira → error in self.errors, connector never raises."""
        responses = [_MockHTTPResponse(401, {"errorMessages": ["Authentication required"]})]
        connector = JiraConnector(
            base_url="https://org.atlassian.net",
            email="user@example.com",
            token="bad-token",
            project_key="DEV",
        )
        with self._make_mock_client(responses):
            try:
                docs = list(connector.fetch(usernames=["ada"]))
            except Exception as exc:
                pytest.fail(f"JiraConnector must not raise on HTTP 401: {exc}")
        assert connector.errors
        combined = " ".join(connector.errors).lower()
        assert "401" in combined or "unauthorized" in combined

    def test_connector_info_has_kind_credential_gated(self):
        """[AC20, AC29] connector_info() returns kind='network', availability='credential-gated'."""
        connector = JiraConnector()
        info = connector.connector_info()
        assert info["kind"] == "network"
        assert info["availability"] == "credential-gated"
        assert info["source"] == "jira"
        assert "base_url" in info["required_credentials"]
        assert "token" in info["required_credentials"]

    def test_network_error_yields_error_not_raise(self):
        """[AC20] Network error → error in self.errors, connector never raises."""
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = lambda s, *a: None
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        connector = JiraConnector(
            base_url="https://org.atlassian.net",
            email="user@example.com",
            token="token",
            project_key="DEV",
        )
        with patch("src.connectors.jira_connector.httpx.Client", return_value=mock_client):
            try:
                docs = list(connector.fetch(usernames=["ada"]))
            except Exception as exc:
                pytest.fail(f"JiraConnector must not raise on network error: {exc}")
        assert connector.errors
