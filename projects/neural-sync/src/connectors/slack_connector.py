"""Slack connector: parse a Slack export JSON into SourceDocument records.

The connector accepts in-memory bytes representing a Slack workspace export
in one of two supported JSON formats:

**Format A** (dict with flat channel map)::

    {
        "users": [{"id": "U123", "name": "alice", "real_name": "Alice", ...}],
        "channels": {
            "general": [
                {"user": "U123", "text": "Hello!", "ts": "1700000000.000001"}
            ]
        }
    }

**Format B** (dict with channel list)::

    {
        "users": [...],
        "channels": [
            {"name": "general", "messages": [...]}
        ]
    }

A top-level ``"messages"`` key is also checked as a fallback.

Messages from all channels are aggregated per user into ``slack_text``.
One :class:`SourceDocument` is yielded per user found in the ``users`` list
(or per user inferred from message authors if no ``users`` section is
present).

Graceful degradation:
- JSON decode errors → ``self.errors`` populated; zero records yielded.
- Individual malformed messages/users → silently skipped; batch continues.
- Any unexpected exception → ``self.errors`` populated; method returns.
"""
from __future__ import annotations

import json
import logging
from typing import Iterator

from .base import BaseConnector, SourceDocument

logger = logging.getLogger(__name__)


class SlackConnector(BaseConnector):
    """Parse a Slack workspace export JSON into :class:`SourceDocument` records.

    Args:
        content: Raw JSON bytes representing the Slack export.
        filename: Original filename (informational; used only in log messages).
    """

    kind: str = "file"
    availability: str = "live"
    source_name: str = "slack"
    display_name_label: str = "Slack Export (JSON)"
    description: str = (
        "Parses a Slack workspace export JSON and aggregates each user's "
        "channel messages into slack_text."
    )
    required_credentials: list = []
    accepted_file_types: list = [".json"]

    def __init__(self, content: bytes, filename: str = "slack_export.json") -> None:
        super().__init__()
        self._content = content
        self._filename = filename

    def fetch(self, **kwargs) -> Iterator[SourceDocument]:  # type: ignore[override]
        """Yield one :class:`SourceDocument` per user with aggregated ``slack_text``.

        JSON decode errors and unexpected exceptions are captured in
        ``self.errors``; the method never raises.
        """
        try:
            yield from self._parse()
        except Exception as exc:  # noqa: BLE001
            msg = f"SlackConnector failed to parse '{self._filename}': {exc}"
            self.errors.append(msg)
            logger.warning(msg)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _parse(self) -> Iterator[SourceDocument]:
        try:
            data = json.loads(self._content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            msg = f"SlackConnector JSON decode error in '{self._filename}': {exc}"
            self.errors.append(msg)
            logger.warning(msg)
            return

        if not isinstance(data, dict):
            msg = (
                f"SlackConnector expected a JSON object at top level, "
                f"got {type(data).__name__}."
            )
            self.errors.append(msg)
            logger.warning(msg)
            return

        # ---- Parse users section (optional) ----------------------------------
        users_by_id: dict[str, dict] = {}
        for user in data.get("users", []):
            if not isinstance(user, dict):
                continue
            uid = user.get("id") or user.get("user_id") or ""
            if uid:
                users_by_id[uid] = user

        # ---- Collect messages per user ID ------------------------------------
        user_messages: dict[str, list[str]] = {}

        channels_raw = data.get("channels", {})

        if isinstance(channels_raw, dict):
            # Format A: {"channel_name": [messages...], ...}
            for channel_name, messages in channels_raw.items():
                if isinstance(messages, list):
                    _collect_messages(messages, user_messages)
        elif isinstance(channels_raw, list):
            # Format B: [{"name": "...", "messages": [...]}, ...]
            for channel in channels_raw:
                if not isinstance(channel, dict):
                    continue
                messages = channel.get("messages", [])
                if isinstance(messages, list):
                    _collect_messages(messages, user_messages)

        # Fallback: top-level "messages" key
        if "messages" in data and isinstance(data["messages"], list):
            _collect_messages(data["messages"], user_messages)

        # ---- If no users section, infer users from message authors -----------
        if not users_by_id:
            for uid in user_messages:
                users_by_id[uid] = {"id": uid, "name": uid}

        # ---- Yield one SourceDocument per user -------------------------------
        for uid, user_info in users_by_id.items():
            profile: dict = (
                user_info.get("profile")
                if isinstance(user_info.get("profile"), dict)
                else {}
            )
            email: str = (
                profile.get("email")
                or user_info.get("email")
                or f"{uid.lower()}@slack.example"
            )
            real_name: str = (
                user_info.get("real_name")
                or profile.get("real_name")
                or profile.get("display_name")
                or user_info.get("name")
                or uid
            )
            slack_text = "\n".join(user_messages.get(uid, []))

            yield SourceDocument(
                external_id=uid,
                display_name=real_name,
                email=email,
                slack_text=slack_text,
                source="slack",
            )


def _collect_messages(
    messages: list,
    user_messages: dict[str, list[str]],
) -> None:
    """Append non-empty message texts to *user_messages* keyed by user ID."""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        uid = (
            msg.get("user")
            or msg.get("user_id")
            or msg.get("author")
            or ""
        )
        text = (
            msg.get("text")
            or msg.get("message")
            or msg.get("content")
            or ""
        ).strip()
        if uid and text:
            user_messages.setdefault(uid, []).append(text)
