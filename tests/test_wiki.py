"""Tests for src.wiki (T011).

Covers the Pydantic models defined in data-model.md (WikiPage, WikiPageDraft,
WikiPageResult) plus two pure helpers required by FR-007:

- normalize_title() — FR-007 signal (a): lowercase + strip articles for
  same-topic comparison
- sanitize_filename() — turn a page title into an Obsidian-friendly filename

Note: src/wiki.py contains ONLY data shapes and pure helpers. Actual file
read/write happens via MCPVault from inside the Editor agent (see plan.md
Decision 3). These tests do not touch the filesystem.

Tests are written first per TDD; T013 implements src/wiki.py to make them pass.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.wiki import (
    WikiPage,
    WikiPageDraft,
    WikiPageResult,
    normalize_title,
    sanitize_filename,
)


class TestWikiPageModel:
    """WikiPage data shape — what the Editor agent produces and the vault stores."""

    def test_minimal_required_fields(self) -> None:
        page = WikiPage(
            title="Speed of Light",
            file_path="/vault/InsightMesh/Speed of Light.md",
            content="# Speed of Light\n\nThe speed of light is...",
            frontmatter={
                "title": "Speed of Light",
                "date": "2026-05-16",
                "source": "transcript.json",
                "tags": ["insightmesh", "physics"],
            },
            cross_links=[],
            created_at="2026-05-16T10:30:00Z",
            updated_at="2026-05-16T10:30:00Z",
        )
        assert page.title == "Speed of Light"
        assert page.frontmatter["tags"] == ["insightmesh", "physics"]

    def test_requires_title(self) -> None:
        with pytest.raises(ValidationError):
            WikiPage.model_validate(
                {
                    "file_path": "/x.md",
                    "content": "",
                    "frontmatter": {},
                    "cross_links": [],
                    "created_at": "2026-05-16T10:30:00Z",
                    "updated_at": "2026-05-16T10:30:00Z",
                }
            )

    def test_requires_file_path(self) -> None:
        with pytest.raises(ValidationError):
            WikiPage.model_validate(
                {
                    "title": "X",
                    "content": "",
                    "frontmatter": {},
                    "cross_links": [],
                    "created_at": "2026-05-16T10:30:00Z",
                    "updated_at": "2026-05-16T10:30:00Z",
                }
            )

    def test_round_trip(self) -> None:
        original = WikiPage(
            title="Lens Optics",
            file_path="/vault/InsightMesh/Lens Optics.md",
            content="...",
            frontmatter={"title": "Lens Optics", "tags": ["optics"]},
            cross_links=["Speed of Light", "Camera Aperture"],
            created_at="2026-05-16T10:30:00Z",
            updated_at="2026-05-16T10:30:00Z",
        )
        json_str = original.model_dump_json()
        restored = WikiPage.model_validate_json(json_str)
        assert restored == original

    def test_cross_links_default_empty(self) -> None:
        """A page with no cross-links is valid; cross_links can be empty list."""
        page = WikiPage(
            title="Standalone",
            file_path="/vault/Standalone.md",
            content="...",
            frontmatter={"title": "Standalone"},
            cross_links=[],
            created_at="2026-05-16T10:30:00Z",
            updated_at="2026-05-16T10:30:00Z",
        )
        assert page.cross_links == []


class TestWikiPageDraftModel:
    """WikiPageDraft — Synthesis output, optionally augmented by Historian."""

    def test_synthesis_output_minimal(self) -> None:
        """Synthesis produces a draft without Historian augmentation fields."""
        draft = WikiPageDraft(
            tentative_title="Speed of Light",
            exchange_indices=[0, 1, 2, 3, 4],
            draft_content="The speed of light...",
            suggested_tags=["physics", "electromagnetism"],
        )
        assert draft.tentative_title == "Speed of Light"
        assert draft.related_pages is None or draft.related_pages == []
        assert (
            draft.crosslink_recommendations is None
            or draft.crosslink_recommendations == []
        )

    def test_historian_augmented(self) -> None:
        """Historian adds related_pages and crosslink_recommendations to the same draft."""
        draft = WikiPageDraft(
            tentative_title="Camera Aperture",
            exchange_indices=[10, 11, 12],
            draft_content="Aperture controls...",
            suggested_tags=["photography", "optics"],
            related_pages=["Lens Optics", "Depth of Field"],
            crosslink_recommendations=["[[Lens Optics]]", "[[Depth of Field|DoF]]"],
        )
        assert draft.related_pages == ["Lens Optics", "Depth of Field"]
        assert "[[Lens Optics]]" in draft.crosslink_recommendations

    def test_requires_tentative_title(self) -> None:
        with pytest.raises(ValidationError):
            WikiPageDraft.model_validate(
                {
                    "exchange_indices": [0],
                    "draft_content": "...",
                    "suggested_tags": [],
                }
            )

    def test_requires_draft_content(self) -> None:
        with pytest.raises(ValidationError):
            WikiPageDraft.model_validate(
                {
                    "tentative_title": "X",
                    "exchange_indices": [0],
                    "suggested_tags": [],
                }
            )

    def test_exchange_indices_are_ints(self) -> None:
        with pytest.raises(ValidationError):
            WikiPageDraft.model_validate(
                {
                    "tentative_title": "X",
                    "exchange_indices": ["not", "ints"],
                    "draft_content": "...",
                    "suggested_tags": [],
                }
            )

    def test_round_trip_minimal(self) -> None:
        draft = WikiPageDraft(
            tentative_title="X",
            exchange_indices=[0, 1],
            draft_content="content",
            suggested_tags=["tag1"],
        )
        json_str = draft.model_dump_json()
        restored = WikiPageDraft.model_validate_json(json_str)
        assert restored == draft

    def test_round_trip_augmented(self) -> None:
        draft = WikiPageDraft(
            tentative_title="X",
            exchange_indices=[0, 1],
            draft_content="content",
            suggested_tags=["tag1"],
            related_pages=["Other Page"],
            crosslink_recommendations=["[[Other Page]]"],
        )
        json_str = draft.model_dump_json()
        restored = WikiPageDraft.model_validate_json(json_str)
        assert restored == draft


class TestWikiPageResultModel:
    """WikiPageResult — Editor's record of one write operation."""

    def test_created_action(self) -> None:
        result = WikiPageResult(
            file_path="/vault/InsightMesh/Speed of Light.md",
            action="created",
            final_frontmatter={"title": "Speed of Light", "tags": ["insightmesh"]},
            crosslinks_applied=[],
        )
        assert result.action == "created"

    def test_updated_action(self) -> None:
        result = WikiPageResult(
            file_path="/vault/InsightMesh/Lens Optics.md",
            action="updated",
            final_frontmatter={"title": "Lens Optics"},
            crosslinks_applied=["[[Camera Aperture]]"],
        )
        assert result.action == "updated"

    def test_rejects_invalid_action(self) -> None:
        with pytest.raises(ValidationError):
            WikiPageResult.model_validate(
                {
                    "file_path": "/x.md",
                    "action": "deleted",  # not in {created, updated}
                    "final_frontmatter": {},
                    "crosslinks_applied": [],
                }
            )

    def test_round_trip(self) -> None:
        result = WikiPageResult(
            file_path="/vault/X.md",
            action="created",
            final_frontmatter={"title": "X", "tags": ["a", "b"]},
            crosslinks_applied=["[[Y]]", "[[Z|alt]]"],
        )
        json_str = result.model_dump_json()
        restored = WikiPageResult.model_validate_json(json_str)
        assert restored == result


class TestNormalizeTitle:
    """FR-007 signal (a): normalized title match.

    Rule: lowercase both titles, strip articles ("the", "a", "an"), compare.
    """

    def test_lowercases(self) -> None:
        assert normalize_title("Speed of Light") == "speed of light"

    def test_strips_leading_the(self) -> None:
        assert normalize_title("The Speed of Light") == "speed of light"

    def test_strips_leading_a(self) -> None:
        assert normalize_title("A Brief History") == "brief history"

    def test_strips_leading_an(self) -> None:
        assert normalize_title("An Introduction") == "introduction"

    def test_does_not_strip_article_mid_title(self) -> None:
        """Article-stripping is leading-only — 'The' in the middle stays."""
        assert "the" in normalize_title("Light and the Speed Limit")

    def test_match_collapses_capitalization_and_articles(self) -> None:
        """The actual use case: two titles that refer to the same topic should match."""
        assert normalize_title("Speed of Light") == normalize_title("The Speed of Light")
        assert normalize_title("the speed of light") == normalize_title(
            "Speed of Light"
        )

    def test_distinct_titles_remain_distinct(self) -> None:
        assert normalize_title("Speed of Light") != normalize_title("Speed of Sound")

    def test_trims_whitespace(self) -> None:
        assert normalize_title("  Speed of Light  ") == "speed of light"


class TestSanitizeFilename:
    """sanitize_filename: turn a title into an Obsidian-friendly .md filename.

    Per Editor agent prompt: spaces preserved (Obsidian-friendly), strip
    characters that are invalid in filenames, append .md.
    """

    def test_simple_title(self) -> None:
        assert sanitize_filename("Speed of Light") == "Speed of Light.md"

    def test_preserves_spaces(self) -> None:
        """Obsidian uses spaces in filenames; do NOT replace with underscores."""
        result = sanitize_filename("Camera Aperture")
        assert " " in result
        assert "_" not in result

    def test_strips_path_separators(self) -> None:
        """Forward and back slashes are invalid in filenames."""
        result = sanitize_filename("OS/2 Architecture")
        assert "/" not in result
        assert "\\" not in result

    def test_strips_reserved_windows_chars(self) -> None:
        """Characters invalid on Windows: < > : " | ? *"""
        result = sanitize_filename('What is "Light"?')
        for c in '<>:"|?*':
            assert c not in result

    def test_appends_md_extension(self) -> None:
        assert sanitize_filename("Anything").endswith(".md")

    def test_does_not_double_append_md(self) -> None:
        """If title already ends in .md, don't add another."""
        assert sanitize_filename("README.md") == "README.md"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert sanitize_filename("  Light  ") == "Light.md"

    def test_handles_unicode(self) -> None:
        """Unicode characters (accents, em-dashes) are allowed in Obsidian filenames."""
        result = sanitize_filename("Schrödinger's Cat")
        assert "Schrödinger" in result
        assert result.endswith(".md")
