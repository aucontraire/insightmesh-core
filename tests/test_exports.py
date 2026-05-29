"""Tests for src.exports (Spec 002 US1 + US2).

Covers FR-002, FR-003, FR-005, FR-007, FR-010, FR-012, FR-021, FR-025, FR-026,
FR-027, FR-028 by exercising the thin EchoMine wrapper in src/exports.py.

Per Spec 002 plan.md, EchoMine itself owns adapter correctness (its own tests
cover Claude.ai and ChatGPT schema parsing); these tests verify InsightMesh's
boundary behavior — projection, ordering, error translation, conversation
selection, canonical-thread flattening.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from echomine import Conversation

from src.exports import (
    InsightMeshSummary,
    UnrecognizedExportFormat,
    _conversational_count,
    _render_attachments,
    _to_role_content,
    detect_adapter,
    extract_conversation,
    list_conversations,
    render_list_table,
    resolve_conversation_value,
)

FIXTURES: Path = Path(__file__).parent / "fixtures"
CLAUDE_AI = FIXTURES / "claude_ai_export.json"
CHATGPT = FIXTURES / "chatgpt_export.json"


# ===========================================================================
# US1: Adapter detection and list_conversations
# ===========================================================================


class TestAdapterDetection:
    def test_detect_claude_ai_export_format(self) -> None:
        from echomine import ClaudeAdapter

        adapter = detect_adapter(CLAUDE_AI)
        assert isinstance(adapter, ClaudeAdapter)

    def test_detect_chatgpt_export_format(self) -> None:
        from echomine import OpenAIAdapter

        adapter = detect_adapter(CHATGPT)
        assert isinstance(adapter, OpenAIAdapter)

    def test_unrecognized_format_raises_unrecognized_export_format_for_flat_array(
        self, tmp_path: Path
    ) -> None:
        # Spec 001 flat {role, content} array shape — not a multi-conversation export.
        path = tmp_path / "flat.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ]
            )
        )
        with pytest.raises(UnrecognizedExportFormat):
            detect_adapter(path)

    def test_unrecognized_format_raises_for_object_at_root(self, tmp_path: Path) -> None:
        path = tmp_path / "obj.json"
        path.write_text('{"not": "an array"}')
        with pytest.raises(UnrecognizedExportFormat):
            detect_adapter(path)


class TestListConversations:
    def test_list_returns_summaries_for_claude_ai(self) -> None:
        summaries = list_conversations(CLAUDE_AI)
        assert len(summaries) == 4
        assert all(isinstance(s, InsightMeshSummary) for s in summaries)

    def test_list_returns_summaries_for_chatgpt(self) -> None:
        summaries = list_conversations(CHATGPT)
        assert len(summaries) == 3

    def test_list_conversations_orders_most_recent_first(self) -> None:
        summaries = list_conversations(CLAUDE_AI)
        # Fixture timestamps: 2026-04-12, 2026-04-10, 2026-04-08
        for i in range(len(summaries) - 1):
            assert summaries[i].created >= summaries[i + 1].created

    def test_list_summary_fields_populated(self) -> None:
        summaries = list_conversations(CLAUDE_AI)
        most_recent = summaries[0]
        assert most_recent.id == "c4f5b9e0-abc1-4d11-9f33-spec002fixture01"
        assert most_recent.title == "Speed of light, deeper dive"
        assert isinstance(most_recent.created, datetime)
        assert most_recent.message_count == 4

    def test_list_returns_empty_for_zero_conversation_export(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("[]")
        with pytest.raises(UnrecognizedExportFormat):
            # Empty array can't be identified as either format; that's fine — both adapters
            # fail to peek a first conversation. The user-facing message is the same.
            list_conversations(path)

    def test_list_chatgpt_message_count_excludes_root_node(self) -> None:
        """ChatGPT export root nodes have message=null; should not be counted."""
        summaries = list_conversations(CHATGPT)
        # The first conversation has 4 real messages (msg-1, msg-2, msg-3, msg-4); node-3b is on
        # an abandoned branch and shouldn't affect total count via message_count.
        # EchoMine's Conversation.message_count counts non-null messages in mapping.
        assert summaries[0].message_count >= 4


class TestSilentIgnoreUnknownFields:
    def test_adapter_silently_ignores_unknown_fields_per_FR021(self) -> None:
        """FR-021: extras at top-level and message-level are silently ignored.

        Conversation #1 in claude_ai_export.json has `future_metadata`,
        `experimental_tag` (top-level extras) and `extra_msg_field` (message extra).
        Parsing must succeed and return correct summary fields without warning.
        """
        summaries = list_conversations(CLAUDE_AI)
        # Find conversation #1 (the one with extras).
        match = next(s for s in summaries if s.id == "c4f5b9e0-abc1-4d11-9f33-spec002fixture01")
        assert match.title == "Speed of light, deeper dive"
        assert match.message_count == 4


# ===========================================================================
# US2: Conversation selection + extraction + tree walking + role normalization
# ===========================================================================


class TestResolveConversationValue:
    def test_numeric_in_range_resolves_as_index(self) -> None:
        summaries = [
            InsightMeshSummary(id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2),
            InsightMeshSummary(id="bbb", title="B", created=datetime(2026, 4, 30), message_count=3),
        ]
        assert resolve_conversation_value("0", summaries) == 0
        assert resolve_conversation_value("1", summaries) == 1

    def test_non_numeric_resolves_as_id(self) -> None:
        summaries = [
            InsightMeshSummary(id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2),
            InsightMeshSummary(
                id="bbb-cd-ef", title="B", created=datetime(2026, 4, 30), message_count=3
            ),
        ]
        assert resolve_conversation_value("aaa", summaries) == 0
        assert resolve_conversation_value("bbb-cd-ef", summaries) == 1

    def test_out_of_range_int_falls_back_to_id_lookup(self) -> None:
        """`5` against 2 summaries is not a valid index — try id lookup instead."""
        summaries = [
            InsightMeshSummary(id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2),
        ]
        with pytest.raises(KeyError):
            resolve_conversation_value("5", summaries)

    def test_no_match_raises_key_error(self) -> None:
        summaries = [
            InsightMeshSummary(
                id="real-id", title="A", created=datetime(2026, 5, 1), message_count=2
            ),
        ]
        with pytest.raises(KeyError):
            resolve_conversation_value("does-not-exist", summaries)


class TestExtractConversation:
    def test_extract_by_id_claude_ai(self) -> None:
        target_id = "c4f5b9e0-abc1-4d11-9f33-spec002fixture01"
        transcript = extract_conversation(CLAUDE_AI, target_id)
        assert transcript.source_path == str(CLAUDE_AI)
        assert len(transcript.exchanges) == 2  # 4 messages = 2 user/assistant pairs

    def test_extract_by_index_chatgpt_walks_canonical_thread(self) -> None:
        # ChatGPT fixture: conv-chatgpt-001 has a branched mapping; only the
        # canonical thread (root → current_node = node-4) should be flattened.
        # Index 0 may not be conv-chatgpt-001 due to most-recent-first ordering;
        # find by id.
        summaries = list_conversations(CHATGPT)
        idx = next(i for i, s in enumerate(summaries) if s.id == "conv-chatgpt-001")
        transcript = extract_conversation(CHATGPT, str(idx))
        # Canonical thread: msg-1 (user), msg-2 (assistant), msg-3 (user), msg-4 (assistant)
        # = 2 paired exchanges. The "abandoned branch" message msg-3b-abandoned must NOT appear.
        assert len(transcript.exchanges) == 2
        for ex in transcript.exchanges:
            if ex.assistant_message is not None:
                assert "[abandoned branch" not in ex.assistant_message.content
            assert "[abandoned branch" not in ex.user_message.content

    def test_extract_normalizes_to_role_content_via_transcript(self) -> None:
        """Result is a ChatTranscript whose Exchanges have user/assistant Messages."""
        summaries = list_conversations(CLAUDE_AI)
        transcript = extract_conversation(CLAUDE_AI, summaries[0].id)
        for ex in transcript.exchanges:
            assert ex.user_message.role == "user"
            if ex.assistant_message is not None:
                assert ex.assistant_message.role == "assistant"

    def test_extract_invalid_selector_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            extract_conversation(CLAUDE_AI, "does-not-exist")


class TestToRoleContent:
    def test_skips_system_role_per_FR026c(self) -> None:
        """FR-026 (c): only user/assistant roles emit; system/tool/function skipped.

        EchoMine's Message.role Literal is currently user/assistant/system, so
        we exercise the system-skip branch directly. The tool/function case is
        covered by the role-membership check (`role in {"user", "assistant"}`).
        """
        from echomine import Message as EMessage

        msgs = [
            EMessage(
                id="m1",
                content="hello",
                role="user",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id=None,
            ),
            EMessage(
                id="m2",
                content="system prompt - should be skipped",
                role="system",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id="m1",
            ),
            EMessage(
                id="m3",
                content="hi",
                role="assistant",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id="m2",
            ),
        ]
        result = _to_role_content(msgs)
        assert result == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_skips_empty_and_whitespace_only_content(self) -> None:
        """Real ChatGPT exports include empty-content nodes (tool-call turns,
        blank assistant placeholders). These must be dropped so the flattened
        transcript never carries an empty-string message into Spec 001's
        Message model (which requires min_length=1)."""
        from echomine import Message as EMessage

        msgs = [
            EMessage(
                id="m1",
                content="real question",
                role="user",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id=None,
            ),
            EMessage(
                id="m2",
                content="",  # empty assistant turn (e.g. tool call, no text)
                role="assistant",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id="m1",
            ),
            EMessage(
                id="m3",
                content="   ",  # whitespace-only
                role="assistant",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id="m2",
            ),
            EMessage(
                id="m4",
                content="real answer",
                role="assistant",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id="m3",
            ),
        ]
        result = _to_role_content(msgs)
        assert result == [
            {"role": "user", "content": "real question"},
            {"role": "assistant", "content": "real answer"},
        ]

    def test_drops_non_conversational_categories_even_with_content(self) -> None:
        """echomine>=1.4.0 tags each message with content_type_category. Only
        'conversational' turns reach synthesis; reasoning/tool_io/system/media
        are dropped even if they arrive with non-empty content (e.g. the media
        '[Image]' placeholder or a Claude text_field fallback)."""
        from echomine import Message as EMessage

        def mk(
            mid: str, content: str, role: Literal["user", "assistant"], category: str
        ) -> EMessage:
            return EMessage(
                id=mid,
                content=content,
                role=role,
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id=None,
                metadata={"content_type_category": category},
            )

        msgs = [
            mk("m1", "real question", "user", "conversational"),
            mk("m2", "[user_editable_context]", "user", "system"),
            mk("m3", "let me think...", "assistant", "reasoning"),
            mk("m4", "print(1)", "assistant", "tool_io"),
            mk("m5", "[Image]", "user", "media"),
            mk("m6", "real answer", "assistant", "conversational"),
        ]
        result = _to_role_content(msgs)
        assert result == [
            {"role": "user", "content": "real question"},
            {"role": "assistant", "content": "real answer"},
        ]

    def test_missing_category_defaults_to_conversational(self) -> None:
        """Pre-1.4.0 echomine doesn't populate content_type_category; absence
        must degrade to the prior content-only behavior, not drop everything."""
        from echomine import Message as EMessage

        msgs = [
            EMessage(
                id="m1",
                content="no category metadata here",
                role="user",
                timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                parent_id=None,
            ),
        ]
        assert _to_role_content(msgs) == [{"role": "user", "content": "no category metadata here"}]


# ===========================================================================
# Spec 003 US1: Attachment / pasted text capture
# ===========================================================================


class TestAttachmentCapture:
    """Spec 003: harvest attachment extracted text BEFORE the empty/category skip
    and fold it inline into the owning message's content (FR-001..FR-011)."""

    @staticmethod
    def _mk(
        role: Literal["user", "assistant"],
        content: str,
        *,
        category: str | None = "conversational",
        attachments: list[dict[str, object]] | None = None,
    ) -> object:
        from echomine import Message as EMessage

        meta: dict[str, object] = {}
        if category is not None:
            meta["content_type_category"] = category
        if attachments is not None:
            meta["attachments"] = attachments
        return EMessage(
            id="m",
            content=content,
            role=role,
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            parent_id=None,
            metadata=meta,
        )

    def test_attachment_only_message_now_surfaces(self) -> None:
        """Regression for FR-002: an attachment-only message (echomine sets
        category='attachment' and content='') used to be dropped before its
        metadata was read. It now contributes a user turn carrying the
        rendered block."""
        msgs = [
            self._mk(
                "user",
                "",
                category="attachment",
                attachments=[
                    {"file_name": "", "file_type": "txt", "extracted_content": "PASTED BODY"}
                ],
            )
        ]
        result = _to_role_content(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "PASTED BODY" in result[0]["content"]
        assert "pasted text" in result[0]["content"]  # header for unnamed

    def test_conversational_message_folds_typed_and_attachment_text(self) -> None:
        """FR-003: typed text plus attachment text both contribute, demarcated
        by a labeled block appended after the typed text."""
        msgs = [
            self._mk(
                "user",
                "Here is the doc, thoughts?",
                attachments=[
                    {
                        "file_name": "report.pdf",
                        "file_type": "pdf",
                        "extracted_content": "DOC BODY",
                    }
                ],
            )
        ]
        out = _to_role_content(msgs)[0]["content"]
        assert "Here is the doc, thoughts?" in out
        assert "DOC BODY" in out
        assert "file: report.pdf" in out  # FR-008 attribution header
        # Typed text appears BEFORE the attachment block (FR-003).
        assert out.index("Here is the doc") < out.index("DOC BODY")

    def test_multiple_attachments_in_source_order(self) -> None:
        """US1 AC4: multiple attachments included in their original source order."""
        msgs = [
            self._mk(
                "user",
                "",
                category="attachment",
                attachments=[
                    {"file_name": "first.txt", "extracted_content": "FIRST_BODY"},
                    {"file_name": "second.txt", "extracted_content": "SECOND_BODY"},
                ],
            )
        ]
        out = _to_role_content(msgs)[0]["content"]
        assert out.index("FIRST_BODY") < out.index("SECOND_BODY")
        assert "file: first.txt" in out and "file: second.txt" in out

    def test_header_distinguishes_named_from_pasted(self) -> None:
        """FR-008 + Clarifications: header reads `file: <name>` for a named
        attachment and `pasted text` for an unnamed paste."""
        named = self._mk(
            "user",
            "",
            category="attachment",
            attachments=[{"file_name": "x.md", "extracted_content": "BODY"}],
        )
        unnamed = self._mk(
            "user",
            "",
            category="attachment",
            attachments=[{"file_name": "", "extracted_content": "BODY"}],
        )
        assert "file: x.md" in _to_role_content([named])[0]["content"]
        assert "pasted text" in _to_role_content([unnamed])[0]["content"]

    def test_empty_or_whitespace_extracted_content_is_ignored(self) -> None:
        """FR-004: an attachment with empty or whitespace extracted_content
        produces no block. An attachment-only message whose only attachment is
        empty stays dropped (no placeholder turn)."""
        empty_only = self._mk(
            "user",
            "",
            category="attachment",
            attachments=[{"file_name": "", "extracted_content": ""}],
        )
        whitespace_only = self._mk(
            "user",
            "",
            category="attachment",
            attachments=[{"file_name": "", "extracted_content": "   \n  "}],
        )
        # Both messages produce no turns at all.
        assert _to_role_content([empty_only]) == []
        assert _to_role_content([whitespace_only]) == []
        # On a conversational message with typed text plus an empty attachment,
        # the typed text still contributes; no bare delimiter appears.
        with_typed = self._mk(
            "user",
            "real question",
            attachments=[{"file_name": "x", "extracted_content": ""}],
        )
        out = _to_role_content([with_typed])[0]["content"]
        assert out == "real question"
        assert "Attached/pasted content" not in out

    def test_non_conversational_categories_dropped_even_with_attachments(self) -> None:
        """FR-005: reasoning / tool_io / system / media / unknown stay excluded
        even if (pathologically) they carry an `attachments` key."""
        for cat in ("reasoning", "tool_io", "system", "media", "unknown"):
            m = self._mk(
                "assistant",
                "should not appear",
                category=cat,
                attachments=[{"file_name": "x", "extracted_content": "SHOULD_NOT_APPEAR"}],
            )
            assert _to_role_content([m]) == [], f"category {cat} leaked"

    def test_missing_category_with_attachments_still_folds(self) -> None:
        """FR-006: when `content_type_category` is absent (pre-1.4.0 echomine),
        the conversational default still applies and attachment text folds."""
        m = self._mk(
            "user",
            "typed",
            category=None,
            attachments=[{"file_name": "n.md", "extracted_content": "ATTACH_BODY"}],
        )
        out = _to_role_content([m])[0]["content"]
        assert "typed" in out and "ATTACH_BODY" in out

    def test_message_without_attachments_unchanged(self) -> None:
        """FR-011: a conversational message that carries no attachments is
        emitted identically to its typed content (no folding, no markup)."""
        m = self._mk("user", "just typed text", attachments=None)
        out = _to_role_content([m])
        assert out == [{"role": "user", "content": "just typed text"}]

    def test_chatgpt_style_no_attachment_key_no_regression(self) -> None:
        """FR-007 / SC-003: a conversational message with no `attachments` key
        in metadata (the ChatGPT shape, where pasted text is already inline)
        produces output identical to its typed content alone."""
        from echomine import Message as EMessage

        m = EMessage(
            id="m",
            content="ChatGPT-style typed body with an inline paste embedded.",
            role="user",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            parent_id=None,
            metadata={"content_type_category": "conversational"},
        )
        assert _to_role_content([m]) == [
            {
                "role": "user",
                "content": "ChatGPT-style typed body with an inline paste embedded.",
            }
        ]


class TestAttachmentFixtureEndToEnd:
    """Spec 003 e2e: `extract_conversation` on the attachment-bearing Claude
    fixture conversation surfaces both pasted text and uploaded-document text
    in the resulting `ChatTranscript` (US1 independent test)."""

    FIXTURE_CONV_ID = "d3a7c4e1-spec3-4e88-9aff-spec003fixture04"

    def test_extract_includes_pasted_text_and_named_attachment(self) -> None:
        transcript = extract_conversation(CLAUDE_AI, self.FIXTURE_CONV_ID)
        # Two paired exchanges: attachment-only -> assistant, then mixed -> assistant.
        assert len(transcript.exchanges) == 2

        joined_user = "\n".join(ex.user_message.content for ex in transcript.exchanges)

        # Attachment-only message contributes its extracted text + "pasted text" header.
        assert "PASTED_LOG_BODY: request_count=42; errors=3" in joined_user
        assert "pasted text" in joined_user

        # Mixed message contributes BOTH typed text and named-attachment text +
        # filename header (FR-003, FR-008).
        assert "Here is the doc, what do you think?" in joined_user
        assert "DOC_BODY: Q3 revenue up 12 percent." in joined_user
        assert "file: report.pdf" in joined_user


class TestRenderAttachments:
    """Direct unit tests for the `_render_attachments` helper."""

    @staticmethod
    def _msg(attachments: list[dict[str, object]] | None) -> object:
        from echomine import Message as EMessage

        meta: dict[str, object] = {}
        if attachments is not None:
            meta["attachments"] = attachments
        return EMessage(
            id="m",
            content="x",
            role="user",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            parent_id=None,
            metadata=meta,
        )

    def test_returns_empty_when_no_attachments_key(self) -> None:
        assert _render_attachments(self._msg(None)) == ""

    def test_returns_empty_when_attachments_list_is_empty(self) -> None:
        assert _render_attachments(self._msg([])) == ""

    def test_returns_empty_when_only_empty_extracted_content(self) -> None:
        assert _render_attachments(self._msg([{"file_name": "x", "extracted_content": ""}])) == ""

    def test_block_format_for_unnamed_paste(self) -> None:
        out = _render_attachments(self._msg([{"file_name": "", "extracted_content": "HELLO"}]))
        assert out == (
            "--- Attached/pasted content (pasted text) ---\nHELLO\n--- End attached content ---"
        )

    def test_block_format_for_named_attachment(self) -> None:
        out = _render_attachments(
            self._msg([{"file_name": "notes.md", "extracted_content": "BODY"}])
        )
        assert out == (
            "--- Attached/pasted content (file: notes.md) ---\nBODY\n--- End attached content ---"
        )


# ===========================================================================
# US1: Rendering
# ===========================================================================


class TestRenderListTable:
    def test_renders_single_table_with_all_columns(self) -> None:
        summaries = list_conversations(CLAUDE_AI)
        out = render_list_table(summaries)
        for header in ("Index", "ID", "Title", "Msgs", "Created"):
            assert header in out

    def test_ids_appear_inline_in_the_table_not_a_separate_footer(self) -> None:
        """Each conversation's id is in the same view as its title (single table),
        so there is no separate 'Conversation ids:' footer to cross-reference."""
        summaries = list_conversations(CLAUDE_AI)
        out = render_list_table(summaries)
        assert "Conversation ids:" not in out
        for s in summaries:
            # Full id must be present (UUIDs are 36 chars; no_wrap keeps them intact).
            assert s.id in out

    def test_empty_summaries_produces_empty_state_message(self) -> None:
        out = render_list_table([])
        assert "No conversations" in out


# ===========================================================================
# US1: Conversational-turn count (Msgs column matches the source app)
# ===========================================================================


class TestConversationalCount:
    @staticmethod
    def _conv(categories: list[str | None]) -> Conversation:
        from echomine import Message as EMessage

        msgs = []
        for i, cat in enumerate(categories):
            meta = {"content_type_category": cat} if cat is not None else {}
            msgs.append(
                EMessage(
                    id=f"m{i}",
                    content="x",
                    role="user",  # role is immaterial: _conversational_count keys on category
                    timestamp=datetime(2026, 5, 1, tzinfo=UTC),
                    parent_id=None,
                    metadata=meta,
                )
            )
        return Conversation(
            id="conv-1",
            title="T",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
            messages=msgs,
        )

    def test_counts_only_conversational_category(self) -> None:
        """Non-conversational nodes (system/tool_io/reasoning) must not inflate
        the count the way echomine's len(messages)-based message_count does."""
        conv = self._conv(["conversational", "system", "conversational", "tool_io", "reasoning"])
        assert conv.message_count == 5  # echomine counts all nodes
        assert _conversational_count(conv) == 2  # we count only conversational

    def test_falls_back_to_message_count_when_no_category_present(self) -> None:
        """Pre-1.4.0 echomine: no message carries content_type_category, so we
        defer to echomine's own count rather than reporting zero."""
        conv = self._conv([None, None, None])
        assert _conversational_count(conv) == conv.message_count == 3
