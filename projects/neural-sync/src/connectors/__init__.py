"""Source connectors for the NEURAL SYNC data ingestion pipeline.

This package provides the five source connectors and the shared abstractions
consumed by the ETL orchestrator (``src/etl/orchestrator.py``).

Public API
----------
.. rubric:: Data model

- :class:`SourceDocument` — normalized record yielded by every connector;
  carries the three text channels (``cv_text``, ``git_log_text``,
  ``slack_text``) consumed by ``enrich_profile``, plus optional profile
  metadata (``timezone``, ``availability_hours``, ``experience_years``).

.. rubric:: Abstraction

- :class:`BaseConnector` — ABC that every connector implements; declares
  ``kind`` (``'file'`` | ``'network'``), ``availability``
  (``'live'`` | ``'credential-gated'``), and the ``fetch()`` generator.

.. rubric:: Connectors

- :class:`GitLabConnector` — pulls commit messages and MR titles for a
  GitLab username via httpx (``kind='network'``, ``availability='live'``).
- :class:`HRConnector` — parses an employee CSV or JSON file from in-memory
  bytes (``kind='file'``, ``availability='live'``).
- :class:`SlackConnector` — parses a Slack workspace export JSON from
  in-memory bytes (``kind='file'``, ``availability='live'``).
- :class:`CVConnector` — parses a single ``.txt``/``.md`` (optionally
  ``.pdf``) CV file from in-memory bytes
  (``kind='file'``, ``availability='live'``).
- :class:`JiraConnector` — reads assigned issues, labels, and comments from
  the Jira REST API via httpx
  (``kind='network'``, ``availability='credential-gated'``).
"""
from .base import BaseConnector, SourceDocument
from .cv_connector import CVConnector
from .gitlab_connector import GitLabConnector
from .hr_connector import HRConnector
from .jira_connector import JiraConnector
from .slack_connector import SlackConnector

__all__ = [
    # Shared abstractions
    "SourceDocument",
    "BaseConnector",
    # File connectors
    "CVConnector",
    "HRConnector",
    "SlackConnector",
    # Network connectors
    "GitLabConnector",
    "JiraConnector",
]
