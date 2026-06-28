"""Connector abstraction: SourceDocument dataclass and BaseConnector ABC.

All source connectors yield :class:`SourceDocument` records.  The three text
channels (``cv_text``, ``git_log_text``, ``slack_text``) are consumed by the
downstream ``enrich_profile`` transform to derive skills and behavioral
signals.

Every concrete connector MUST:
- Override class attributes ``kind`` and ``availability``.
- Implement :meth:`BaseConnector.fetch` as a generator/iterator.
- Never raise an unhandled exception or cause an HTTP 5xx response — degrade
  gracefully instead and populate ``self.errors`` with a human-readable note.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional


@dataclass
class SourceDocument:
    """A normalized record extracted from a source connector.

    Fields
    ------
    external_id
        Identifier for the person within the originating system
        (e.g. GitLab username, Jira user key, email address from an HR CSV).
    display_name
        Human-readable name for display and DeveloperProfile seeding.
    email
        Email address (used to deduplicate profiles across connectors).
    cv_text
        Resume / bio text channel consumed by ``enrich_profile``.
    git_log_text
        Commit message / MR title / Jira issue text channel consumed by
        ``enrich_profile``.
    slack_text
        Aggregated chat messages text channel consumed by ``enrich_profile``.
    timezone
        Optional IANA timezone string (e.g. "America/New_York").
    availability_hours
        Optional weekly available hours (integer).
    experience_years
        Optional years of professional experience (integer).
    source
        Connector identifier that produced this record
        (``'gitlab'``, ``'hr'``, ``'slack'``, ``'cv'``, ``'jira'``).
    """

    # Mandatory identity fields
    external_id: str
    display_name: str
    email: str

    # Text channels consumed by enrich_profile
    cv_text: str = ""
    git_log_text: str = ""
    slack_text: str = ""

    # Optional profile metadata (used when available from the source)
    timezone: Optional[str] = None
    availability_hours: Optional[int] = None
    experience_years: Optional[int] = None
    source: Optional[str] = None


class BaseConnector(abc.ABC):
    """Abstract base class for all source connectors.

    Subclasses declare class-level attributes:

    - ``kind``: ``'file'`` for in-memory file parsers, ``'network'`` for
      connectors that call an external HTTP API.
    - ``availability``: ``'live'`` for credential-free connectors, or
      ``'credential-gated'`` when credentials must be configured before use.

    After calling :meth:`fetch`, inspect ``connector.errors`` for any
    degradation notices that should surface in ``IngestionSummary.errors``.
    """

    #: Must be set as a class-level attribute in every subclass.
    kind: Literal["file", "network"]
    availability: Literal["live", "credential-gated"]

    def __init__(self) -> None:
        #: Populated during :meth:`fetch` with human-readable degradation
        #: notices.  The ETL orchestrator reads this list and forwards each
        #: entry to ``IngestionSummary.errors``.
        self.errors: list[str] = []

    @abc.abstractmethod
    def fetch(self, **kwargs) -> Iterator[SourceDocument]:
        """Yield :class:`SourceDocument` records from the source.

        Implementations MUST:
        - Never raise on connectivity / credential failures.
        - Record every degradation as a human-readable string in
          ``self.errors`` rather than raising.
        - Yield zero records (not raise) when credentials are absent or the
          upstream service is unreachable.
        """
        raise NotImplementedError  # pragma: no cover

    def connector_info(self) -> dict:
        """Return a ``ConnectorInfo``-compatible descriptor dict.

        The returned dict conforms to the ``ConnectorInfo`` schema defined in
        ``artifacts/api-contracts.json`` and is used by
        ``GET /api/v1/ingestion/connectors``.
        """
        return {
            "source": getattr(self, "source_name", self.__class__.__name__.lower()),
            "display_name": getattr(self, "display_name_label", self.__class__.__name__),
            "kind": self.kind,
            "availability": self.availability,
            "description": getattr(self, "description", ""),
            "required_credentials": getattr(self, "required_credentials", []),
            "accepted_file_types": getattr(self, "accepted_file_types", []),
        }
