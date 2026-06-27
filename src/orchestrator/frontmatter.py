"""Tiny shared reader for the leading ``---``…``---`` YAML-frontmatter block of an
agent ``.md`` definition.

Both the cost reporter (reads the ``model:`` field) and the agent runner (reads the
``tools:`` field) scan this block; sharing one parser keeps them from drifting.
"""
from __future__ import annotations

from pathlib import Path


def frontmatter_lines(text: str) -> list[str]:
    """Return the stripped lines inside a leading ``---``…``---`` block ([] if none)."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    return [line.strip() for line in text[3:end].splitlines()]


def read_frontmatter_lines(path: Path) -> list[str]:
    """``frontmatter_lines`` for a file path; returns [] on a missing file / read error."""
    try:
        return frontmatter_lines(Path(path).read_text())
    except OSError:
        return []
