"""HR connector: parse employee CSV or JSON files from in-memory bytes.

Supports both CSV and JSON formats.  Column mapping is case-insensitive and
tolerates common aliases (e.g. ``title``, ``role``, ``bio`` all map to
``cv_text``).  The connector processes data entirely in memory — it never
writes to disk.

Column mapping (case-insensitive, leading/trailing whitespace stripped,
hyphens/spaces normalized to underscores before matching):

    name, full_name, employee_name, display_name  → display_name
    email, email_address, e_mail                   → email
    title, role, bio, description, position,
      job_title                                    → cv_text  (first match wins)
    skills, skill, technologies, tech_stack,
      competencies                                 → appended to cv_text as
                                                     "Skills: <value>"
    weekly_hours, hours_per_week,
      availability_hours, available_hours          → availability_hours (int)
    years_experience, experience_years,
      years_of_experience, seniority_years         → experience_years (int)
    timezone, time_zone, tz                        → timezone

Graceful degradation:
- Individual rows that cannot be mapped or have neither name nor email are
  silently skipped (counted in IngestionSummary.skipped by the ETL layer).
- Parse errors are logged; ``self.errors`` is populated; iteration stops for
  the current file but never raises.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from typing import Iterator, Optional

from .base import BaseConnector, SourceDocument

logger = logging.getLogger(__name__)

# Canonical field name → accepted column-header aliases (all lowercase)
_COL_ALIASES: dict[str, frozenset[str]] = {
    "display_name": frozenset(
        {"name", "full_name", "employee_name", "display_name"}
    ),
    "email": frozenset({"email", "email_address", "e_mail"}),
    "cv_text": frozenset(
        {"title", "role", "bio", "description", "position", "job_title"}
    ),
    "skills": frozenset(
        {"skills", "skill", "technologies", "tech_stack", "competencies"}
    ),
    "availability_hours": frozenset(
        {"weekly_hours", "hours_per_week", "availability_hours", "available_hours"}
    ),
    "experience_years": frozenset(
        {
            "years_experience",
            "experience_years",
            "years_of_experience",
            "seniority_years",
        }
    ),
    "timezone": frozenset({"timezone", "time_zone", "tz"}),
}


def _normalize_key(raw: str) -> str:
    """Lower-case, strip, and collapse separators in a column name."""
    return (
        raw.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _resolve_column(raw: str) -> Optional[str]:
    """Return the canonical field name for a column header, or ``None``."""
    key = _normalize_key(raw)
    for canonical, aliases in _COL_ALIASES.items():
        if key in aliases:
            return canonical
    return None


def _safe_int(value: str) -> Optional[int]:
    """Parse *value* to int; return ``None`` on failure."""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _row_to_doc(row: dict, index: int) -> Optional[SourceDocument]:
    """Convert an arbitrary-key row dict to a :class:`SourceDocument`.

    Returns ``None`` for rows that have neither a name nor an email (they
    will be counted as skipped by the ETL orchestrator).
    """
    mapped: dict[str, str] = {}
    for col, raw_value in row.items():
        if raw_value is None:
            continue
        canonical = _resolve_column(col)
        if canonical is not None:
            # Only take the first match for each canonical field
            if canonical not in mapped:
                mapped[canonical] = str(raw_value).strip()

    display_name = mapped.get("display_name", "")
    email = mapped.get("email", "")

    if not display_name and not email:
        logger.debug("HR row %d has neither name nor email — skipping", index)
        return None

    # Build cv_text: role/title/bio first, then append skills on a new line
    cv_parts: list[str] = []
    if mapped.get("cv_text"):
        cv_parts.append(mapped["cv_text"])
    if mapped.get("skills"):
        cv_parts.append(f"Skills: {mapped['skills']}")
    cv_text = "\n".join(cv_parts)

    return SourceDocument(
        external_id=email or display_name or f"hr-row-{index}",
        display_name=display_name or email,
        email=email,
        cv_text=cv_text,
        timezone=mapped.get("timezone"),
        availability_hours=_safe_int(mapped["availability_hours"])
        if mapped.get("availability_hours")
        else None,
        experience_years=_safe_int(mapped["experience_years"])
        if mapped.get("experience_years")
        else None,
        source="hr",
    )


class HRConnector(BaseConnector):
    """Parse an uploaded HR CSV or JSON file into :class:`SourceDocument` records.

    The file is processed entirely in memory (never written to disk).  Column
    mapping is case-insensitive and tolerant of common aliases (see module
    docstring for the full mapping table).

    Args:
        content: Raw file bytes (UTF-8 encoded; replacement chars used on
            decode error).
        filename: Original filename used to auto-detect format
            (``*.json`` → JSON path; all others → CSV path).
    """

    kind: str = "file"
    availability: str = "live"
    source_name: str = "hr"
    display_name_label: str = "HR Data (CSV / JSON)"
    description: str = (
        "Parses an uploaded employee CSV or JSON file into developer profiles "
        "using case-insensitive, tolerant column mapping."
    )
    required_credentials: list = []
    accepted_file_types: list = [".csv", ".json"]

    def __init__(self, content: bytes, filename: str = "data.csv") -> None:
        super().__init__()
        self._content = content
        self._filename = filename.lower()

    def fetch(self, **kwargs) -> Iterator[SourceDocument]:  # type: ignore[override]
        """Yield one :class:`SourceDocument` per employee row.

        Parse errors are recorded in ``self.errors`` and iteration is
        aborted for the current file, but the method never raises.
        """
        try:
            yield from self._parse()
        except Exception as exc:  # noqa: BLE001
            msg = f"HRConnector failed to parse '{self._filename}': {exc}"
            self.errors.append(msg)
            logger.warning(msg)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _parse(self) -> Iterator[SourceDocument]:
        if self._filename.endswith(".json"):
            yield from self._parse_json()
        else:
            yield from self._parse_csv()

    def _parse_json(self) -> Iterator[SourceDocument]:
        try:
            data = json.loads(self._content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            msg = f"HRConnector JSON decode error: {exc}"
            self.errors.append(msg)
            logger.warning(msg)
            return

        # Unwrap common envelope wrappers
        if isinstance(data, dict):
            for wrapper in ("employees", "data", "records", "items", "rows", "users"):
                if wrapper in data and isinstance(data[wrapper], list):
                    data = data[wrapper]
                    break
            else:
                # Single employee object
                data = [data]

        if not isinstance(data, list):
            msg = (
                f"HRConnector JSON expected a list (or a dict with a list "
                f"wrapper), got {type(data).__name__}."
            )
            self.errors.append(msg)
            logger.warning(msg)
            return

        for i, row in enumerate(data):
            if not isinstance(row, dict):
                logger.debug("HR JSON row %d is not a dict — skipping", i)
                continue
            doc = _row_to_doc(row, i)
            if doc is not None:
                yield doc

    def _parse_csv(self) -> Iterator[SourceDocument]:
        text = self._content.decode("utf-8", errors="replace")
        try:
            reader = csv.DictReader(io.StringIO(text))
            for i, row in enumerate(reader):
                doc = _row_to_doc(dict(row), i)
                if doc is not None:
                    yield doc
        except csv.Error as exc:
            msg = f"HRConnector CSV parse error: {exc}"
            self.errors.append(msg)
            logger.warning(msg)
