"""Data ingestion endpoints for NEURAL SYNC (manager role only).

All endpoints are restricted to the manager role via the :func:`require_manager`
dependency.  Developer-role JWTs receive HTTP 403; unauthenticated requests
receive HTTP 401 [AC21].

Endpoints
---------
GET  /ingestion/connectors
    List available connector descriptors for the five source connectors
    (gitlab, hr, slack, cv, jira) [AC14].

POST /ingestion/file
    Ingest from a multipart file upload (file-kind connectors: cv, hr, slack).
    Enforces MAX_UPLOAD_BYTES before any parsing; HTTP 413 on exceed;
    nothing written to disk [AC22].

POST /ingestion/gitlab
    Ingest a developer profile from GitLab commit and MR activity.
    Degraded result (never HTTP 5xx) when token is missing/invalid [AC19].

POST /ingestion/jira
    Ingest developer profiles from Jira issue activity.
    Missing/invalid credentials return HTTP 200 with degraded IngestionSummary
    (errors list) — never HTTP 5xx [AC20].
"""
from __future__ import annotations

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors import (
    CVConnector,
    GitLabConnector,
    HRConnector,
    JiraConnector,
    SlackConnector,
)
from src.core.auth import TokenPayload, require_manager
from src.core.settings import settings
from src.db.session import get_db
from src.etl.orchestrator import IngestionSummary, run_ingestion

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class ConnectorInfo(BaseModel):
    """Descriptor for a single source connector per api-contracts.json [AC29]."""

    source: str
    display_name: str
    kind: str            # 'file' | 'network'
    availability: str    # 'live' | 'credential-gated'
    description: str = ""
    required_credentials: list[str] = Field(default_factory=list)
    accepted_file_types: list[str] = Field(default_factory=list)


class GitlabIngestionRequest(BaseModel):
    """Request body for POST /ingestion/gitlab [AC19]."""

    username: str = Field(..., description="GitLab username whose activity to ingest.")
    project: Optional[str] = Field(
        None,
        description=(
            "Optional project path (namespace/project) to scope commit retrieval. "
            "When omitted, user push events are used."
        ),
    )
    base_url: Optional[str] = Field(
        None,
        description="GitLab instance URL (default: GITLAB_BASE_URL from settings).",
    )
    token: Optional[str] = Field(
        None,
        description=(
            "Optional personal access token for private repository access. "
            "When absent, only public data is readable."
        ),
    )
    mode: Literal["preview", "commit"] = Field(
        "preview",
        description="preview=drafts only (created=0); commit=persist DeveloperProfile rows.",
    )


class JiraIngestionRequest(BaseModel):
    """Request body for POST /ingestion/jira [AC20]."""

    base_url: str = Field(..., description="Jira instance URL (e.g. https://org.atlassian.net).")
    email: str = Field(..., description="Jira account email for Basic Auth.")
    token: str = Field(..., description="Jira API token for Basic Auth.")
    project_key: str = Field(..., description="Jira project key (e.g. DEV).")
    usernames: Optional[List[str]] = Field(
        None,
        description=(
            "Optional list of Jira usernames to filter by. "
            "When omitted, fetches all assignees in the project up to the page cap."
        ),
    )
    mode: Literal["preview", "commit"] = Field(
        "preview",
        description="preview=drafts only (created=0); commit=persist DeveloperProfile rows.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_connector_info_list() -> list[dict]:
    """Build ConnectorInfo-compatible dicts for all five connectors.

    Uses lightweight instantiation (empty content / no credentials) so the
    connector's ``connector_info()`` helper can read class-level attributes
    without performing any I/O.
    """
    gitlab_info = GitLabConnector().connector_info()
    hr_info = HRConnector(content=b"", filename="data.csv").connector_info()
    slack_info = SlackConnector(content=b"", filename="slack_export.json").connector_info()
    cv_info = CVConnector(content=b"", filename="cv.txt").connector_info()
    jira_info = JiraConnector().connector_info()
    return [gitlab_info, hr_info, slack_info, cv_info, jira_info]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/connectors", response_model=list[ConnectorInfo])
async def list_ingestion_connectors(
    _manager: TokenPayload = Depends(require_manager),
) -> list[dict]:
    """List available ingestion connector descriptors [AC14].

    Returns a JSON array of ConnectorInfo objects for all five source
    connectors (gitlab, hr, slack, cv, jira).  The frontend uses this list
    to populate the ConnectorPicker and to disable credential-gated connectors
    with a required-credentials tooltip.

    Secured by ``require_manager``: developer-role JWTs receive HTTP 403;
    unauthenticated requests receive HTTP 401.
    """
    return _build_connector_info_list()


@router.post("/file", response_model=IngestionSummary)
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    source: str = Form(..., description="File-kind connector: cv, hr, or slack."),
    mode: str = Form("preview", description="preview or commit."),
    db: AsyncSession = Depends(get_db),
    _manager: TokenPayload = Depends(require_manager),
) -> IngestionSummary:
    """Ingest developer profiles from a multipart file upload [AC22].

    The file is read entirely into memory and **never written to disk**.
    Payloads exceeding ``MAX_UPLOAD_BYTES`` (default 10 MB) are rejected with
    HTTP 413 before any parsing begins.

    Supported sources: ``cv``, ``hr``, ``slack``.

    - ``mode=preview``: returns :class:`IngestionSummary` with drafts;
      ``created == 0``; nothing persisted.
    - ``mode=commit``: persists :class:`DeveloperProfile` rows via the shared
      create-plus-embed helper; ``created ≥ 1`` on success.
    """
    # Validate mode
    if mode not in ("preview", "commit"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "VALIDATION_ERROR",
                "message": f"mode must be 'preview' or 'commit', got '{mode}'.",
                "request_id": "n/a",
            },
        )

    # Read file content into memory (no disk writes)
    content: bytes = await file.read()

    # Enforce MAX_UPLOAD_BYTES BEFORE any parsing [AC22]
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "PAYLOAD_TOO_LARGE",
                "message": (
                    f"Upload exceeds MAX_UPLOAD_BYTES ({settings.max_upload_bytes} bytes). "
                    "No data was processed."
                ),
                "request_id": "n/a",
            },
        )

    filename: str = file.filename or "upload"

    # Dispatch to the appropriate file-kind connector
    if source == "cv":
        connector = CVConnector(content=content, filename=filename)
    elif source == "hr":
        connector = HRConnector(content=content, filename=filename)
    elif source == "slack":
        connector = SlackConnector(content=content, filename=filename)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "BAD_REQUEST",
                "message": (
                    f"Unsupported source '{source}'. "
                    "Must be one of: cv, hr, slack."
                ),
                "request_id": "n/a",
            },
        )

    # Extract SourceDocuments (entirely in memory)
    source_docs = list(connector.fetch())

    ingestion_mode: Literal["preview", "commit"] = "commit" if mode == "commit" else "preview"

    return await run_ingestion(
        source_docs,
        ingestion_mode,
        connector_errors=connector.errors,
        db=db if ingestion_mode == "commit" else None,
        background_tasks=background_tasks if ingestion_mode == "commit" else None,
    )


@router.post("/gitlab", response_model=IngestionSummary)
async def ingest_gitlab(
    request: GitlabIngestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _manager: TokenPayload = Depends(require_manager),
) -> IngestionSummary:
    """Ingest a developer profile from GitLab activity [AC19].

    Pulls commit messages and merge-request titles for the given username.
    A missing or invalid token (or hitting the GITLAB_MAX_PAGES cap / an HTTP
    429) yields a **degraded** (empty or partial) :class:`IngestionSummary`
    with an ``errors`` entry — never HTTP 5xx.

    Secured by ``require_manager``: developer-role JWTs receive HTTP 403;
    unauthenticated requests receive HTTP 401.
    """
    connector = GitLabConnector(
        token=request.token or settings.gitlab_token or None,
        base_url=request.base_url or settings.gitlab_base_url,
        max_pages=settings.gitlab_max_pages,
    )

    source_docs = list(connector.fetch(username=request.username, project=request.project))

    return await run_ingestion(
        source_docs,
        request.mode,
        connector_errors=connector.errors,
        db=db if request.mode == "commit" else None,
        background_tasks=background_tasks if request.mode == "commit" else None,
    )


@router.post("/jira", response_model=IngestionSummary)
async def ingest_jira(
    request: JiraIngestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _manager: TokenPayload = Depends(require_manager),
) -> IngestionSummary:
    """Ingest developer profiles from Jira issue activity [AC20].

    Reads assigned issues, labels, and comments into ``git_log_text`` via a
    read-only httpx client.  This connector is **credential-gated**: missing
    or invalid credentials return HTTP 200 with an :class:`IngestionSummary`
    whose ``errors`` list documents the missing/invalid credential — never
    HTTP 5xx.

    Secured by ``require_manager``: developer-role JWTs receive HTTP 403;
    unauthenticated requests receive HTTP 401.
    """
    connector = JiraConnector(
        base_url=request.base_url,
        email=request.email,
        token=request.token,
        project_key=request.project_key,
    )

    source_docs = list(connector.fetch(usernames=request.usernames))

    return await run_ingestion(
        source_docs,
        request.mode,
        connector_errors=connector.errors,
        db=db if request.mode == "commit" else None,
        background_tasks=background_tasks if request.mode == "commit" else None,
    )
