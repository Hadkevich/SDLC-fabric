"""GitLab connector: pull commit messages and MR titles via httpx.

Uses a thin read-only httpx client (not the python-gitlab library) so there
is no heavyweight SDK dependency.  Supports an optional personal access token,
a configurable base URL (default https://gitlab.com), per-request timeout, and
a configurable page cap (``GITLAB_MAX_PAGES``, default 10).

Graceful degradation:
- Missing / invalid token (HTTP 401) → yield a degraded SourceDocument with
  empty ``git_log_text``; record the issue in ``self.errors``.
- Rate-limited (HTTP 429) or page-cap reached → retain pages already fetched;
  record a truncation notice in ``self.errors``; yield partial SourceDocument.
- Any network / timeout error → record in ``self.errors``; yield degraded doc.
- Any other unexpected exception → record in ``self.errors``; yield degraded doc.

No caller of this connector can ever receive an unhandled exception or HTTP 5xx.
"""
from __future__ import annotations

import logging
from typing import Iterator, List, Optional

import httpx

from .base import BaseConnector, SourceDocument

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://gitlab.com"
_DEFAULT_TIMEOUT = 10.0   # seconds per request
_DEFAULT_MAX_PAGES = 10


class GitLabConnector(BaseConnector):
    """Fetch commit messages and merge-request titles for a GitLab username.

    Args:
        token: Optional personal access token.  When omitted the connector
            operates in *unauthenticated* mode (public data only).
        base_url: GitLab instance URL.  Defaults to ``https://gitlab.com``.
        timeout: Per-request HTTP timeout in seconds.
        max_pages: Maximum number of pages to request per paginated endpoint.
            Matches the ``GITLAB_MAX_PAGES`` setting consumed by T-11.
    """

    kind: str = "network"
    availability: str = "live"
    source_name: str = "gitlab"
    display_name_label: str = "GitLab"
    description: str = (
        "Pulls commit messages and merge-request titles from a GitLab user "
        "profile via the GitLab REST API."
    )
    required_credentials: list = []   # 'live' — token is optional, not required
    accepted_file_types: list = []

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        super().__init__()
        self._token = (token or "").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_pages = max_pages

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _request_headers(self) -> dict:
        headers: dict = {"Accept": "application/json"}
        if self._token:
            headers["PRIVATE-TOKEN"] = self._token
        return headers

    def _paginate(
        self,
        client: httpx.Client,
        url: str,
        params: dict,
    ) -> tuple[list, list[str]]:
        """Collect up to ``self._max_pages`` pages from *url*.

        Returns a ``(items, errors)`` tuple.  ``errors`` contains
        human-readable notes about any truncation or failure that occurred;
        the caller should extend ``self.errors`` with them.
        """
        items: list = []
        errors: list[str] = []
        page = 1

        while page <= self._max_pages:
            paged_params = {**params, "page": page, "per_page": 100}
            try:
                resp = client.get(
                    url,
                    params=paged_params,
                    headers=self._request_headers(),
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                errors.append(
                    f"GitLab request timed out on page {page} ({url}): {exc}"
                )
                break
            except httpx.RequestError as exc:
                errors.append(
                    f"GitLab network error on page {page} ({url}): {exc}"
                )
                break

            if resp.status_code == 401:
                errors.append(
                    "GitLab returned HTTP 401: token is missing or invalid. "
                    "Provide a valid GITLAB_TOKEN to access private data. "
                    "Returning partial / empty results."
                )
                break
            if resp.status_code == 429:
                errors.append(
                    f"GitLab rate-limited (HTTP 429) at page {page}; "
                    "partial results returned."
                )
                break
            if resp.status_code == 403:
                errors.append(
                    f"GitLab returned HTTP 403 (forbidden) on page {page}; "
                    "partial results returned."
                )
                break
            if resp.status_code != 200:
                errors.append(
                    f"GitLab returned HTTP {resp.status_code} on page {page} "
                    f"({url}); partial results."
                )
                break

            try:
                page_items = resp.json()
            except Exception:  # noqa: BLE001
                errors.append(f"GitLab returned invalid JSON on page {page}.")
                break

            if not page_items:
                break  # empty page → last page reached

            items.extend(page_items)

            # Check the X-Next-Page header for efficient pagination
            next_page_header = resp.headers.get("X-Next-Page", "")
            if not next_page_header:
                # Fall back to: fewer than per_page items means last page
                if len(page_items) < 100:
                    break
                # Otherwise continue
            elif not next_page_header.strip():
                break  # no next page

            if page >= self._max_pages:
                errors.append(
                    f"GitLab GITLAB_MAX_PAGES cap ({self._max_pages}) reached; "
                    "results truncated."
                )
                break

            page += 1

        return items, errors

    def _resolve_user_id(
        self, client: httpx.Client, username: str
    ) -> tuple[Optional[int], list[str]]:
        """Return the numeric user ID for *username* (needed for events API)."""
        errs: list[str] = []
        try:
            resp = client.get(
                f"{self._base_url}/api/v4/users",
                params={"username": username},
                headers=self._request_headers(),
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                users = resp.json()
                if users and isinstance(users, list):
                    return users[0].get("id"), errs
                errs.append(f"GitLab user '{username}' not found.")
            elif resp.status_code == 401:
                errs.append(
                    "GitLab returned HTTP 401 during user lookup: "
                    "token is missing or invalid."
                )
            else:
                errs.append(
                    f"GitLab user lookup returned HTTP {resp.status_code} "
                    f"for username '{username}'."
                )
        except httpx.RequestError as exc:
            errs.append(f"GitLab user lookup network error: {exc}")
        return None, errs

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def fetch(  # type: ignore[override]
        self,
        username: str,
        project: Optional[str] = None,
        **kwargs,
    ) -> Iterator[SourceDocument]:
        """Yield one :class:`SourceDocument` for *username*.

        ``git_log_text`` is populated with commit messages (prefixed
        ``commit:``) and merge-request titles (prefixed ``merge_request:``).

        If credentials are missing/invalid or the upstream is unreachable the
        method still yields a *degraded* SourceDocument (empty text fields)
        and records the issue in ``self.errors``.  It never raises.

        Args:
            username: GitLab username whose activity to ingest.
            project: Optional project path (``namespace/project``) to scope
                commit retrieval.  When omitted, user push events are used.
        """
        self.errors = []
        git_log_parts: list[str] = []

        try:
            with httpx.Client(timeout=self._timeout) as client:
                # ---- Commits --------------------------------------------------
                if project:
                    # URL-encode the project path (e.g. "group/repo" → "group%2Frepo")
                    encoded_project = project.replace("/", "%2F")
                    commits_url = (
                        f"{self._base_url}/api/v4/projects/"
                        f"{encoded_project}/repository/commits"
                    )
                    commits, c_errs = self._paginate(
                        client,
                        commits_url,
                        {"author": username},
                    )
                    self.errors.extend(c_errs)
                    for commit in commits:
                        msg = (commit.get("message") or commit.get("title") or "").strip()
                        if msg:
                            # Use only the first line of the commit message
                            git_log_parts.append(f"commit: {msg.splitlines()[0]}")
                else:
                    # No project given — use user push events
                    user_id, id_errs = self._resolve_user_id(client, username)
                    self.errors.extend(id_errs)
                    if user_id is not None:
                        events_url = (
                            f"{self._base_url}/api/v4/users/{user_id}/events"
                        )
                        events, e_errs = self._paginate(
                            client,
                            events_url,
                            {"action": "pushed"},
                        )
                        self.errors.extend(e_errs)
                        for event in events:
                            push_data = event.get("push_data") or {}
                            commit_title = (
                                push_data.get("commit_title") or ""
                            ).strip()
                            if commit_title:
                                git_log_parts.append(f"commit: {commit_title}")

                # ---- Merge requests -------------------------------------------
                if project:
                    encoded_project = project.replace("/", "%2F")
                    mrs_url = (
                        f"{self._base_url}/api/v4/projects/"
                        f"{encoded_project}/merge_requests"
                    )
                    mr_params = {"author_username": username, "state": "all"}
                else:
                    mrs_url = f"{self._base_url}/api/v4/merge_requests"
                    mr_params = {
                        "author_username": username,
                        "scope": "all",
                        "state": "all",
                    }

                mrs, mr_errs = self._paginate(client, mrs_url, mr_params)
                self.errors.extend(mr_errs)

                for mr in mrs:
                    title = (mr.get("title") or "").strip()
                    if title:
                        git_log_parts.append(f"merge_request: {title}")

        except Exception as exc:  # noqa: BLE001
            err_msg = f"GitLab connector unexpected error for user '{username}': {exc}"
            self.errors.append(err_msg)
            logger.warning(err_msg)

        yield SourceDocument(
            external_id=username,
            display_name=username,
            email=f"{username}@gitlab.example",
            git_log_text="\n".join(git_log_parts),
            source="gitlab",
        )
