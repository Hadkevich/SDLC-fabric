"""Jira connector: read assigned issues, labels, and comments via httpx.

This connector is **credential-gated** — it requires four credentials:
``base_url``, ``email``, ``token``, and ``project_key``.  When any of these
is absent or invalid the connector:

1. Populates ``self.errors`` with a human-readable list of *exactly* which
   credentials are required.
2. Yields *zero* :class:`~.base.SourceDocument` records.
3. Never raises an exception or causes an HTTP 5xx response.

Jira API usage
--------------
Authentication uses HTTP Basic Auth with ``email:token`` (Jira Cloud API
token model).  Issues are fetched via the Jira REST API v3 search endpoint::

    GET {base_url}/rest/api/3/search?jql=project={project_key}+AND+assignee={username}
        &fields=summary,labels,comment&maxResults=100&startAt=...

For each issue the connector appends:
- ``issue: {key} - {summary}`` to the git_log_text parts
- ``labels: {label1, label2}`` (when labels are present)
- Up to a configurable number of comment bodies

One :class:`~.base.SourceDocument` per username is yielded with the
aggregated Jira activity in ``git_log_text``.

Graceful degradation
--------------------
- Missing / empty credentials → ``self.errors`` populated; zero records yielded.
- HTTP 401 / 403 → treated as invalid credentials; degraded result returned.
- HTTP 429 (rate-limit) → partial results retained; truncation noted.
- Network / timeout errors → partial results retained; error noted.
- Any other unexpected exception → ``self.errors`` populated; no raise.
"""
from __future__ import annotations

import logging
from typing import Iterator, List, Optional

import httpx

from .base import BaseConnector, SourceDocument

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0   # seconds per Jira API request
_DEFAULT_MAX_RESULTS = 100  # Jira pagination page size
_DEFAULT_MAX_PAGES = 10    # maximum pages per user


class JiraConnector(BaseConnector):
    """Fetch assigned Jira issues, labels, and comments for a list of usernames.

    Required credentials (all four must be non-empty):
    - ``base_url``:    Jira instance URL, e.g. ``https://myorg.atlassian.net``
    - ``email``:       Jira account email for Basic Auth
    - ``token``:       Jira API token for Basic Auth
    - ``project_key``: Jira project key to scope the issue query (e.g. ``DEV``)

    Optional:
    - ``usernames``: List of Jira account usernames / user-keys to filter by.
      When ``None`` all assignees in the project are fetched.
    - ``timeout``:   Per-request HTTP timeout in seconds (default 15).
    - ``max_pages``: Maximum pages per user (default 10).

    Args:
        base_url: Jira instance URL.
        email: Jira account email.
        token: Jira API token.
        project_key: Jira project key.
        timeout: Per-request timeout in seconds.
        max_pages: Page cap to prevent runaway pagination.
    """

    kind: str = "network"
    availability: str = "credential-gated"
    source_name: str = "jira"
    display_name_label: str = "Jira"
    description: str = (
        "Reads assigned issues, labels, and comments from the Jira REST API "
        "and populates git_log_text."
    )
    required_credentials: list = ["base_url", "email", "token", "project_key"]
    accepted_file_types: list = []

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        token: Optional[str] = None,
        project_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        super().__init__()
        self._base_url = (base_url or "").rstrip("/")
        self._email = (email or "").strip()
        self._token = (token or "").strip()
        self._project_key = (project_key or "").strip()
        self._timeout = timeout
        self._max_pages = max_pages

    # ------------------------------------------------------------------ #
    #  Credential validation                                               #
    # ------------------------------------------------------------------ #

    def _validate_credentials(self) -> list[str]:
        """Return a list of error strings for missing / empty credentials."""
        missing: list[str] = []
        if not self._base_url:
            missing.append(
                "Missing required credential 'base_url': "
                "provide the Jira instance URL (e.g. https://myorg.atlassian.net)."
            )
        if not self._email:
            missing.append(
                "Missing required credential 'email': "
                "provide the Jira account email for Basic Auth."
            )
        if not self._token:
            missing.append(
                "Missing required credential 'token': "
                "provide a Jira API token for Basic Auth authentication."
            )
        if not self._project_key:
            missing.append(
                "Missing required credential 'project_key': "
                "provide the Jira project key to scope issue retrieval (e.g. 'DEV')."
            )
        return missing

    # ------------------------------------------------------------------ #
    #  Internal HTTP helpers                                               #
    # ------------------------------------------------------------------ #

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(username=self._email, password=self._token)

    def _search_issues(
        self,
        client: httpx.Client,
        jql: str,
    ) -> tuple[list[dict], list[str]]:
        """Paginate through Jira search results for *jql*.

        Returns ``(issues, errors)``.
        """
        issues: list[dict] = []
        errors: list[str] = []
        start_at = 0
        pages_fetched = 0

        while pages_fetched < self._max_pages:
            params = {
                "jql": jql,
                "fields": "summary,labels,comment,assignee",
                "maxResults": _DEFAULT_MAX_RESULTS,
                "startAt": start_at,
            }
            try:
                resp = client.get(
                    f"{self._base_url}/rest/api/3/search",
                    params=params,
                    auth=self._auth(),
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                errors.append(
                    f"Jira request timed out (startAt={start_at}): {exc}"
                )
                break
            except httpx.RequestError as exc:
                errors.append(
                    f"Jira network error (startAt={start_at}): {exc}"
                )
                break

            if resp.status_code == 401:
                errors.append(
                    "Jira returned HTTP 401 (Unauthorized): email or token is "
                    "missing or invalid.  Provide valid Jira credentials."
                )
                break
            if resp.status_code == 403:
                errors.append(
                    "Jira returned HTTP 403 (Forbidden): the authenticated user "
                    "does not have permission to read the specified project."
                )
                break
            if resp.status_code == 429:
                errors.append(
                    f"Jira rate-limited (HTTP 429) at page {pages_fetched + 1}; "
                    "partial results returned."
                )
                break
            if resp.status_code != 200:
                errors.append(
                    f"Jira returned HTTP {resp.status_code} "
                    f"(startAt={start_at}); partial results."
                )
                break

            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                errors.append(
                    f"Jira returned invalid JSON (startAt={start_at})."
                )
                break

            page_issues: list[dict] = body.get("issues", [])
            issues.extend(page_issues)
            pages_fetched += 1

            total: int = body.get("total", 0)
            start_at += len(page_issues)

            if start_at >= total or not page_issues:
                break  # all pages consumed

            if pages_fetched >= self._max_pages:
                errors.append(
                    f"Jira page cap ({self._max_pages}) reached; "
                    "results truncated."
                )
                break

        return issues, errors

    @staticmethod
    def _issue_to_text(issue: dict) -> str:
        """Convert a Jira issue dict to a human-readable text snippet."""
        key: str = issue.get("key", "")
        fields: dict = issue.get("fields") or {}
        summary: str = (fields.get("summary") or "").strip()
        labels: list[str] = fields.get("labels") or []
        comment_data: dict = fields.get("comment") or {}
        comments: list[dict] = comment_data.get("comments") or []

        parts: list[str] = []
        parts.append(f"issue: {key} - {summary}" if key else f"issue: {summary}")

        if labels:
            parts.append(f"labels: {', '.join(labels)}")

        # Include up to 5 comment bodies
        for comment in comments[:5]:
            body: str = (
                (comment.get("body") or {}).get("text", "")
                if isinstance(comment.get("body"), dict)
                else str(comment.get("body") or "")
            ).strip()
            if body:
                # Truncate very long comments
                body = body[:500] + ("..." if len(body) > 500 else "")
                parts.append(f"comment: {body}")

        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def fetch(  # type: ignore[override]
        self,
        usernames: Optional[List[str]] = None,
        **kwargs,
    ) -> Iterator[SourceDocument]:
        """Yield one :class:`~.base.SourceDocument` per username.

        ``git_log_text`` is populated from the assignee's Jira issues (key,
        summary, labels, and up to 5 comments per issue).

        Missing credentials are reported via ``self.errors`` and zero records
        are yielded — the method never raises.

        Args:
            usernames: Optional list of Jira usernames / account IDs to
                filter by.  When ``None`` the connector fetches all assignees
                in ``project_key`` up to the page cap.
        """
        self.errors = []

        # Validate credentials before touching the network
        cred_errors = self._validate_credentials()
        if cred_errors:
            self.errors.extend(cred_errors)
            logger.warning(
                "JiraConnector: %d missing credential(s); yielding no records.",
                len(cred_errors),
            )
            return  # zero records — caller reads self.errors

        try:
            yield from self._fetch_for_usernames(usernames)
        except Exception as exc:  # noqa: BLE001
            msg = f"JiraConnector unexpected error: {exc}"
            self.errors.append(msg)
            logger.warning(msg)

    def _fetch_for_usernames(
        self,
        usernames: Optional[List[str]],
    ) -> Iterator[SourceDocument]:
        """Core fetch logic (called only after credential validation)."""
        with httpx.Client(timeout=self._timeout) as client:
            if usernames:
                # Yield one SourceDocument per requested username
                for username in usernames:
                    yield from self._fetch_one_user(client, username)
            else:
                # Fetch all assignees in the project; aggregate by user key
                yield from self._fetch_all_assignees(client)

    def _fetch_one_user(
        self,
        client: httpx.Client,
        username: str,
    ) -> Iterator[SourceDocument]:
        jql = (
            f"project = {self._project_key} "
            f"AND assignee = \"{username}\" "
            f"ORDER BY created DESC"
        )
        issues, errs = self._search_issues(client, jql)
        self.errors.extend(errs)

        git_log_parts = [self._issue_to_text(issue) for issue in issues]
        yield SourceDocument(
            external_id=username,
            display_name=username,
            email=f"{username.lower()}@jira.example",
            git_log_text="\n\n".join(filter(None, git_log_parts)),
            source="jira",
        )

    def _fetch_all_assignees(
        self,
        client: httpx.Client,
    ) -> Iterator[SourceDocument]:
        """Fetch all issues in the project and group by assignee."""
        jql = f"project = {self._project_key} ORDER BY created DESC"
        issues, errs = self._search_issues(client, jql)
        self.errors.extend(errs)

        # Group issues by assignee account ID / display name
        by_user: dict[str, tuple[str, list[str]]] = {}
        for issue in issues:
            fields: dict = issue.get("fields") or {}
            assignee: dict = fields.get("assignee") or {}
            if not assignee:
                continue
            uid: str = (
                assignee.get("accountId")
                or assignee.get("key")
                or assignee.get("name")
                or ""
            )
            if not uid:
                continue
            display: str = (
                assignee.get("displayName")
                or assignee.get("name")
                or uid
            )
            email: str = (
                assignee.get("emailAddress")
                or f"{uid.lower()}@jira.example"
            )
            text = self._issue_to_text(issue)
            if uid not in by_user:
                by_user[uid] = (display, [])
            if text:
                by_user[uid][1].append(text)

        for uid, (display, texts) in by_user.items():
            yield SourceDocument(
                external_id=uid,
                display_name=display,
                email=f"{uid.lower()}@jira.example",
                git_log_text="\n\n".join(texts),
                source="jira",
            )
