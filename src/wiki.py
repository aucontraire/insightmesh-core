"""Wiki data shapes and pure helpers for InsightMesh.

This module contains ONLY Pydantic v2 models and pure functions. Actual file
read/write happens via MCPVault from inside the Editor agent (see plan.md
Decision 3). The orchestrator and agents share these types; this module never
touches the filesystem.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

_LEADING_ARTICLES: tuple[str, ...] = ("the", "a", "an")
_INVALID_FILENAME_CHARS: str = '/\\<>:"|?*'


class WikiPage(BaseModel):
    """An Obsidian-compatible markdown file with frontmatter."""

    model_config = ConfigDict(strict=True)

    title: str
    file_path: str
    content: str
    frontmatter: dict[str, Any]
    cross_links: list[str]
    created_at: str
    updated_at: str


class WikiPageDraft(BaseModel):
    """Pre-write representation: produced by Synthesis, augmented by Historian.

    `related_pages` and `crosslink_recommendations` are None when Synthesis
    first produces the draft; the Historian fills them in.
    """

    model_config = ConfigDict(strict=True)

    tentative_title: str
    exchange_indices: list[int]
    draft_content: str
    suggested_tags: list[str]
    related_pages: list[str] | None = None
    crosslink_recommendations: list[str] | None = None


class WikiPageResult(BaseModel):
    """The Editor's record of one write operation."""

    model_config = ConfigDict(strict=True)

    file_path: str
    action: Literal["created", "updated"]
    final_frontmatter: dict[str, Any]
    crosslinks_applied: list[str]


def normalize_title(title: str) -> str:
    """Normalize a title for same-topic comparison (FR-007 signal a).

    Lowercases and strips a leading article ("the", "a", "an"). Articles
    appearing mid-title are preserved. Whitespace is trimmed.

    >>> normalize_title("The Speed of Light")
    'speed of light'
    >>> normalize_title("Light and the Speed Limit")
    'light and the speed limit'
    """
    s = title.strip().lower()
    for article in _LEADING_ARTICLES:
        prefix = f"{article} "
        if s.startswith(prefix):
            return s[len(prefix) :]
    return s


def sanitize_filename(title: str) -> str:
    """Turn a page title into an Obsidian-friendly `.md` filename.

    Preserves spaces (Obsidian uses spaced filenames), strips characters
    invalid on common filesystems (`/\\<>:"|?*`), trims surrounding
    whitespace, and appends `.md` unless the title already has that suffix.

    >>> sanitize_filename("Speed of Light")
    'Speed of Light.md'
    >>> sanitize_filename("README.md")
    'README.md'
    """
    s = title.strip()
    for char in _INVALID_FILENAME_CHARS:
        s = s.replace(char, "")
    if not s.endswith(".md"):
        s = f"{s}.md"
    return s
