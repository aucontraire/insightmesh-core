"""Tests for src.transcript (T010).

Covers FR-001 (JSON transcript input), FR-012 (clear errors for malformed),
and the Message / Exchange / ChatTranscript Pydantic v2 models from
data-model.md.

Per data-model.md §Pairing Rules:
- Exchange = one user message + the assistant's response (one conversational turn)
- Parser pairs consecutive user→assistant messages from the flat JSON array
- Edge cases: leading orphan assistants skipped, consecutive same-role messages
  concatenated or treated as standalone Exchanges, trailing user message becomes
  Exchange with assistant_message=None

Tests are written first per TDD; T012 implements src/transcript.py to make
them pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.transcript import ChatTranscript, Exchange, Message, parse_transcript

FIXTURES: Path = Path(__file__).parent / "fixtures"


class TestParseValidFixtures:
    """parse_transcript() should successfully load all valid fixtures."""

    def test_parses_single_topic(self) -> None:
        transcript = parse_transcript(FIXTURES / "single_topic.json")
        assert isinstance(transcript, ChatTranscript)
        assert len(transcript.exchanges) > 0

    def test_parses_multi_topic(self) -> None:
        transcript = parse_transcript(FIXTURES / "multi_topic.json")
        assert isinstance(transcript, ChatTranscript)
        assert len(transcript.exchanges) > 0

    def test_parses_revisit(self) -> None:
        transcript = parse_transcript(FIXTURES / "revisit.json")
        assert isinstance(transcript, ChatTranscript)
        assert len(transcript.exchanges) > 0

    def test_source_path_recorded(self) -> None:
        path = FIXTURES / "single_topic.json"
        transcript = parse_transcript(path)
        assert transcript.source_path == str(path)

    def test_single_topic_pair_count(self) -> None:
        """single_topic.json has 40 messages = 20 paired exchanges (SC-001 baseline)."""
        transcript = parse_transcript(FIXTURES / "single_topic.json")
        assert len(transcript.exchanges) == 20


class TestErrorHandling:
    """FR-012: clear errors for empty or malformed transcript files."""

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises((json.JSONDecodeError, ValueError, ValidationError)):
            parse_transcript(FIXTURES / "malformed.json")

    def test_rejects_empty_transcript(self) -> None:
        with pytest.raises(ValueError, match="(?i)empty"):
            parse_transcript(FIXTURES / "empty.json")

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_transcript(tmp_path / "does_not_exist.json")

    def test_rejects_non_array_root(self, tmp_path: Path) -> None:
        path = tmp_path / "not_array.json"
        path.write_text('{"role": "user", "content": "wrapped in object"}')
        with pytest.raises((ValueError, ValidationError)):
            parse_transcript(path)


class TestExchangePairing:
    """data-model.md §Pairing Rules — flat messages become user/assistant pairs."""

    def test_standard_alternating_pairs_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "alt.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                ]
            )
        )
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 2
        assert transcript.exchanges[0].user_message.content == "Q1"
        assert transcript.exchanges[0].assistant_message is not None
        assert transcript.exchanges[0].assistant_message.content == "A1"
        assert transcript.exchanges[1].user_message.content == "Q2"

    def test_zero_indexed(self) -> None:
        transcript = parse_transcript(FIXTURES / "single_topic.json")
        for i, exchange in enumerate(transcript.exchanges):
            assert exchange.index == i

    def test_leading_assistant_messages_skipped(self, tmp_path: Path) -> None:
        """Rule 2: orphan leading assistants have no triggering prompt."""
        path = tmp_path / "lead_asst.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "assistant", "content": "orphan greeting"},
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                ]
            )
        )
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 1
        assert transcript.exchanges[0].user_message.content == "Q1"

    def test_consecutive_assistant_messages_concatenated(self, tmp_path: Path) -> None:
        """Rule 4: consecutive assistants concat into one assistant_message."""
        path = tmp_path / "double_asst.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "Part 1."},
                    {"role": "assistant", "content": "Part 2."},
                ]
            )
        )
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 1
        ex = transcript.exchanges[0]
        assert ex.assistant_message is not None
        assert "Part 1." in ex.assistant_message.content
        assert "Part 2." in ex.assistant_message.content

    def test_consecutive_user_messages_split(self, tmp_path: Path) -> None:
        """Rule 3: consecutive users → earlier is standalone (no response)."""
        path = tmp_path / "double_user.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Q1 (no response)"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                ]
            )
        )
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 2
        assert transcript.exchanges[0].user_message.content == "Q1 (no response)"
        assert transcript.exchanges[0].assistant_message is None
        assert transcript.exchanges[1].user_message.content == "Q2"
        assert transcript.exchanges[1].assistant_message is not None

    def test_trailing_user_message(self, tmp_path: Path) -> None:
        """Rule 5: trailing user becomes Exchange with assistant_message=None."""
        path = tmp_path / "trailing_user.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2 unanswered"},
                ]
            )
        )
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 2
        assert transcript.exchanges[1].assistant_message is None


class TestRoleNormalization:
    """Rule 6: unknown roles normalize to 'assistant' before pairing."""

    def test_user_role_preserved(self) -> None:
        transcript = parse_transcript(FIXTURES / "single_topic.json")
        # Every exchange should have a user_message with role="user"
        for ex in transcript.exchanges:
            assert ex.user_message.role == "user"

    def test_assistant_role_preserved(self) -> None:
        transcript = parse_transcript(FIXTURES / "single_topic.json")
        # Most exchanges have assistant_message; those that do should be role="assistant"
        for ex in transcript.exchanges:
            if ex.assistant_message is not None:
                assert ex.assistant_message.role == "assistant"

    def test_system_role_normalized_to_assistant(self, tmp_path: Path) -> None:
        path = tmp_path / "system_msg.json"
        path.write_text(
            json.dumps(
                [
                    {"role": "system", "content": "system context"},
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                ]
            )
        )
        # System normalizes to assistant → becomes orphan leading assistant → skipped
        transcript = parse_transcript(path)
        assert len(transcript.exchanges) == 1
        assert transcript.exchanges[0].user_message.content == "Q1"


class TestPydanticModels:
    """Strict typing on Message, Exchange, and ChatTranscript Pydantic v2 models."""

    def test_message_requires_role(self) -> None:
        with pytest.raises(ValidationError):
            Message.model_validate({"content": "missing role"})

    def test_message_requires_content(self) -> None:
        with pytest.raises(ValidationError):
            Message.model_validate({"role": "user"})

    def test_message_content_must_be_string(self) -> None:
        with pytest.raises(ValidationError):
            Message.model_validate({"role": "user", "content": 12345})

    def test_message_round_trip(self) -> None:
        msg = Message(role="user", content="hello")
        json_str = msg.model_dump_json()
        restored = Message.model_validate_json(json_str)
        assert restored == msg

    def test_exchange_requires_user_message(self) -> None:
        with pytest.raises(ValidationError):
            Exchange.model_validate({"index": 0})

    def test_exchange_allows_none_assistant_message(self) -> None:
        ex = Exchange(
            index=0,
            user_message=Message(role="user", content="hi"),
            assistant_message=None,
        )
        assert ex.assistant_message is None

    def test_exchange_round_trip(self) -> None:
        ex = Exchange(
            index=3,
            user_message=Message(role="user", content="Q"),
            assistant_message=Message(role="assistant", content="A"),
        )
        json_str = ex.model_dump_json()
        restored = Exchange.model_validate_json(json_str)
        assert restored == ex

    def test_chat_transcript_round_trip(self) -> None:
        original = parse_transcript(FIXTURES / "single_topic.json")
        json_str = original.model_dump_json()
        restored = ChatTranscript.model_validate_json(json_str)
        assert len(restored.exchanges) == len(original.exchanges)
        assert restored.exchanges[0] == original.exchanges[0]

    def test_chat_transcript_rejects_wrong_types(self) -> None:
        with pytest.raises(ValidationError):
            ChatTranscript.model_validate({"source_path": "/path", "exchanges": "not a list"})
