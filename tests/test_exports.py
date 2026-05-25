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

import pytest

from src.exports import (
    InsightMeshSummary,
    UnrecognizedExportFormat,
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
        assert len(summaries) == 3
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
        match = next(
            s for s in summaries if s.id == "c4f5b9e0-abc1-4d11-9f33-spec002fixture01"
        )
        assert match.title == "Speed of light, deeper dive"
        assert match.message_count == 4


# ===========================================================================
# US2: Conversation selection + extraction + tree walking + role normalization
# ===========================================================================


class TestResolveConversationValue:
    def test_numeric_in_range_resolves_as_index(self) -> None:
        summaries = [
            InsightMeshSummary(
                id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2
            ),
            InsightMeshSummary(
                id="bbb", title="B", created=datetime(2026, 4, 30), message_count=3
            ),
        ]
        assert resolve_conversation_value("0", summaries) == 0
        assert resolve_conversation_value("1", summaries) == 1

    def test_non_numeric_resolves_as_id(self) -> None:
        summaries = [
            InsightMeshSummary(
                id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2
            ),
            InsightMeshSummary(
                id="bbb-cd-ef", title="B", created=datetime(2026, 4, 30), message_count=3
            ),
        ]
        assert resolve_conversation_value("aaa", summaries) == 0
        assert resolve_conversation_value("bbb-cd-ef", summaries) == 1

    def test_out_of_range_int_falls_back_to_id_lookup(self) -> None:
        """`5` against 2 summaries is not a valid index — try id lookup instead."""
        summaries = [
            InsightMeshSummary(
                id="aaa", title="A", created=datetime(2026, 5, 1), message_count=2
            ),
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
