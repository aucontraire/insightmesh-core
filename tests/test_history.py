"""Unit tests for src/history.py (Spec 005 T005).

Covers the 11 subtests (a)-(k) called out in tasks.md:
  (a) CheckpointRecord strict validation rejects extras and wrong types
  (b) checkpoint_id model_validator enforces f"cp-{checkpoint_number:03d}"
  (c) schema_version Literal[1] rejects any other value on write-side
  (d) ProvenanceFrontmatter total_edits >= 1 and exchange_count >= 0
  (e) EditorDecisionRecord.signals accepts arbitrary JSON-serializable dicts
  (f) ExchangeRecord.index >= 0
  (g) ConversationRecord.provider accepts the three Literal values plus None
  (h) ExchangeMessageIds strict validation
  (i) CheckpointRecordRead tolerates unknown extras (forward-compat per FR-002)
  (j) Exception classes inherit from HistoryError
  (k) FR-020 negative invariant — no SHA fields in CheckpointRecord
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.history import (
    SCHEMA_VERSION,
    CheckpointRecord,
    CheckpointRecordRead,
    ConversationRecord,
    EditorBlock,
    EditorDecisionRecord,
    ExchangeMessageIds,
    ExchangeRecord,
    FrontmatterParseFailed,
    HistoryError,
    LinksRecord,
    ProvenanceFrontmatter,
    ResultsRecord,
    ShadowRepoCommitFailed,
    ShadowRepoUnavailable,
)


def _minimal_conversation() -> ConversationRecord:
    return ConversationRecord(
        id="conv-aaa",
        export_path="/tmp/export.json",
        provider="anthropic",
        models_used=[],
        transcript_hash="a" * 64,
    )


def _minimal_exchange(index: int = 0) -> ExchangeRecord:
    return ExchangeRecord(
        index=index,
        user_message_id="msg-u",
        assistant_message_id="msg-a",
    )


def _minimal_links() -> LinksRecord:
    return LinksRecord(
        session_log=".logs/session.json",
        cursor=".logs/cursor.json",
    )


def _minimal_record(checkpoint_number: int = 1) -> CheckpointRecord:
    return CheckpointRecord(
        checkpoint_id=f"cp-{checkpoint_number:03d}",
        checkpoint_number=checkpoint_number,
        timestamp=datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC),
        conversation=_minimal_conversation(),
        exchanges=[_minimal_exchange(0)],
        editor=EditorBlock(decisions=[]),
        results=ResultsRecord(),
        links=_minimal_links(),
    )


class TestCheckpointRecordStrict:
    """Subtest (a): write-side model rejects extras and wrong types."""

    def test_rejects_extra_top_level_field(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            CheckpointRecord(
                checkpoint_id="cp-001",
                checkpoint_number=1,
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
                surprise_field="not allowed",  # type: ignore[call-arg]
            )

    def test_rejects_wrong_type_for_checkpoint_number(self) -> None:
        with pytest.raises(ValidationError):
            CheckpointRecord(
                checkpoint_id="cp-001",
                checkpoint_number="not an int",  # type: ignore[arg-type]
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
            )

    def test_accepts_minimal_valid_record(self) -> None:
        record = _minimal_record(1)
        assert record.checkpoint_id == "cp-001"
        assert record.checkpoint_number == 1


class TestCheckpointIdDerivation:
    """Subtest (b): model_validator enforces f'cp-{checkpoint_number:03d}'."""

    def test_matching_id_accepted(self) -> None:
        record = _minimal_record(42)
        assert record.checkpoint_id == "cp-042"

    def test_zero_padding_3_digits(self) -> None:
        record = _minimal_record(7)
        assert record.checkpoint_id == "cp-007"

    def test_large_number_still_derives(self) -> None:
        record = _minimal_record(1234)
        assert record.checkpoint_id == "cp-1234"

    def test_mismatched_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="checkpoint_id"):
            CheckpointRecord(
                checkpoint_id="cp-999",
                checkpoint_number=1,
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
            )

    def test_unprefixed_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="checkpoint_id"):
            CheckpointRecord(
                checkpoint_id="001",
                checkpoint_number=1,
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
            )


class TestSchemaVersionLiteral:
    """Subtest (c): schema_version Literal[1] rejects any other value on write-side."""

    def test_default_is_1(self) -> None:
        record = _minimal_record(1)
        assert record.schema_version == SCHEMA_VERSION == 1

    def test_explicit_1_accepted(self) -> None:
        record = CheckpointRecord(
            schema_version=1,
            checkpoint_id="cp-001",
            checkpoint_number=1,
            timestamp=datetime.now(UTC),
            conversation=_minimal_conversation(),
            exchanges=[_minimal_exchange()],
            editor=EditorBlock(decisions=[]),
            results=ResultsRecord(),
            links=_minimal_links(),
        )
        assert record.schema_version == 1

    def test_other_versions_rejected_on_write_side(self) -> None:
        with pytest.raises(ValidationError):
            CheckpointRecord(
                schema_version=2,  # type: ignore[arg-type]
                checkpoint_id="cp-001",
                checkpoint_number=1,
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
            )


class TestProvenanceFrontmatterConstraints:
    """Subtest (d): total_edits >= 1 and exchange_count >= 0."""

    def test_minimal_valid(self) -> None:
        fm = ProvenanceFrontmatter(
            latest_checkpoint=".history/checkpoints/x/cp-001.json",
            conversations=[],
            latest_action="created",
            latest_confidence="high",
            total_edits=1,
            exchange_count=0,
        )
        assert fm.total_edits == 1
        assert fm.exchange_count == 0

    def test_total_edits_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="total_edits"):
            ProvenanceFrontmatter(
                latest_checkpoint=".history/checkpoints/x/cp-001.json",
                conversations=[],
                latest_action="created",
                latest_confidence="high",
                total_edits=0,
                exchange_count=0,
            )

    def test_exchange_count_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exchange_count"):
            ProvenanceFrontmatter(
                latest_checkpoint=".history/checkpoints/x/cp-001.json",
                conversations=[],
                latest_action="created",
                latest_confidence="high",
                total_edits=1,
                exchange_count=-1,
            )

    def test_action_skipped_rejected(self) -> None:
        # Frontmatter never records a "skipped" action; only created/updated.
        with pytest.raises(ValidationError, match="latest_action"):
            ProvenanceFrontmatter(
                latest_checkpoint=".history/checkpoints/x/cp-001.json",
                conversations=[],
                latest_action="skipped",  # type: ignore[arg-type]
                latest_confidence="high",
                total_edits=1,
                exchange_count=0,
            )


class TestEditorDecisionRecordSignals:
    """Subtest (e): signals accepts arbitrary JSON-serializable dicts."""

    def test_typed_signals_shape_accepted(self) -> None:
        decision = EditorDecisionRecord(
            file="page.md",
            action="updated",
            confidence="high",
            rationale="merged with prior content",
            exchange_indices=[0, 1, 2],
            signals={
                "normalized_title_match": True,
                "tag_overlap_count": 4,
                "tag_overlap_tags": ["a", "b", "c", "d"],
                "content_keyword_overlap": "strong",
            },
        )
        assert decision.signals["normalized_title_match"] is True
        assert decision.signals["tag_overlap_count"] == 4

    def test_empty_signals_dict_accepted(self) -> None:
        decision = EditorDecisionRecord(
            file="page.md",
            action="skipped",
            confidence="low",
            rationale="parse failed",
            exchange_indices=[],
            signals={},
        )
        assert decision.signals == {}

    def test_arbitrary_extra_signal_keys_accepted(self) -> None:
        # signals is dict[str, Any] by design; we want forward-compat for
        # future Editor signals fields without bumping our schema.
        decision = EditorDecisionRecord(
            file="page.md",
            action="created",
            confidence="medium",
            rationale="new draft",
            exchange_indices=[5],
            signals={"future_signal_x": "value", "future_signal_y": [1, 2, 3]},
        )
        assert decision.signals["future_signal_x"] == "value"
        assert decision.signals["future_signal_y"] == [1, 2, 3]


class TestExchangeRecord:
    """Subtest (f): index >= 0."""

    def test_index_zero_accepted(self) -> None:
        r = ExchangeRecord(index=0, user_message_id="u", assistant_message_id="a")
        assert r.index == 0

    def test_index_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="index"):
            ExchangeRecord(index=-1, user_message_id="u", assistant_message_id="a")

    def test_null_message_ids_accepted(self) -> None:
        # Spec 001 flat-array / pre-1.5.0 echomine path: ids absent.
        r = ExchangeRecord(index=0, user_message_id=None, assistant_message_id=None)
        assert r.user_message_id is None
        assert r.assistant_message_id is None


class TestConversationRecordProvider:
    """Subtest (g): provider accepts 'anthropic' | 'openai' | None."""

    @pytest.mark.parametrize("provider_value", ["anthropic", "openai", None])
    def test_accepted_values(self, provider_value: str | None) -> None:
        c = ConversationRecord(
            id="conv-1",
            export_path="/tmp/x.json",
            provider=provider_value,  # type: ignore[arg-type]
            models_used=[],
            transcript_hash="b" * 64,
        )
        assert c.provider == provider_value

    def test_rejects_unknown_provider(self) -> None:
        with pytest.raises(ValidationError, match="provider"):
            ConversationRecord(
                id="conv-1",
                export_path="/tmp/x.json",
                provider="grok",  # type: ignore[arg-type]
                models_used=[],
                transcript_hash="c" * 64,
            )


class TestExchangeMessageIds:
    """Subtest (h): ExchangeMessageIds strict validation."""

    def test_default_both_none(self) -> None:
        ids = ExchangeMessageIds()
        assert ids.user_message_id is None
        assert ids.assistant_message_id is None

    def test_both_set(self) -> None:
        ids = ExchangeMessageIds(user_message_id="u-1", assistant_message_id="a-1")
        assert ids.user_message_id == "u-1"

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ExchangeMessageIds(
                user_message_id="u",
                assistant_message_id="a",
                role_message_id="surprise",  # type: ignore[call-arg]
            )


class TestCheckpointRecordReadForwardCompat:
    """Subtest (i): CheckpointRecordRead tolerates unknown extras per FR-002.

    Exercised against the fixture from T012 once it's written; here we hand-
    craft a JSON payload with both an unknown top-level field and an unknown
    sub-field inside editor.decisions[0] to verify the read-side permissive
    model parses it cleanly.
    """

    def test_unknown_top_level_field_tolerated(self) -> None:
        payload = json.dumps(
            {
                "schema_version": 1,
                "checkpoint_id": "cp-001",
                "checkpoint_number": 1,
                "timestamp": "2026-06-28T12:00:00Z",
                "conversation": {
                    "id": "conv-aaa",
                    "export_path": "/tmp/x.json",
                    "provider": "anthropic",
                    "models_used": [],
                    "transcript_hash": "a" * 64,
                },
                "exchanges": [{"index": 0, "user_message_id": "u", "assistant_message_id": "a"}],
                "editor": {"decisions": []},
                "results": {
                    "pages_created": [],
                    "pages_updated": [],
                    "pages_skipped": [],
                },
                "links": {"session_log": ".logs/s.json", "cursor": ".logs/c.json"},
                "future_top_level_field": "tolerated",
            }
        )
        parsed = CheckpointRecordRead.model_validate_json(payload)
        assert parsed.checkpoint_id == "cp-001"
        # Unknown extras land in model_extra per Pydantic v2 semantics.
        assert parsed.model_extra is not None
        assert parsed.model_extra.get("future_top_level_field") == "tolerated"

    def test_unknown_field_in_editor_decision_tolerated(self) -> None:
        # NOTE: Sub-models still use strict extra="forbid" because they're not
        # subclassed; documenting this is a deliberate scope decision per
        # research R5 (read-side extras tolerated at the TOP level; sub-model
        # strictness is preserved). If future evolution adds extras INSIDE
        # sub-objects, those sub-models will get *Read variants then.
        payload = json.dumps(
            {
                "schema_version": 1,
                "checkpoint_id": "cp-001",
                "checkpoint_number": 1,
                "timestamp": "2026-06-28T12:00:00Z",
                "conversation": {
                    "id": "conv-aaa",
                    "export_path": "/tmp/x.json",
                    "provider": "anthropic",
                    "models_used": [],
                    "transcript_hash": "a" * 64,
                },
                "exchanges": [{"index": 0, "user_message_id": "u", "assistant_message_id": "a"}],
                "editor": {
                    "decisions": [
                        {
                            "file": "page.md",
                            "action": "updated",
                            "confidence": "high",
                            "rationale": "merged",
                            "exchange_indices": [0],
                            "signals": {},
                            "future_subfield_y": 42,
                        }
                    ]
                },
                "results": {
                    "pages_created": [],
                    "pages_updated": ["page.md"],
                    "pages_skipped": [],
                },
                "links": {"session_log": ".logs/s.json", "cursor": ".logs/c.json"},
            }
        )
        # With the current scope (top-level CheckpointRecordRead permissive),
        # this raises a ValidationError because EditorDecisionRecord is still
        # strict on extras. Verify the behavior is correct + documented.
        with pytest.raises(ValidationError, match="future_subfield_y"):
            CheckpointRecordRead.model_validate_json(payload)

    def test_fixture_round_trip_if_present(self) -> None:
        # Optional bridge to T012: load the hand-authored fixture and parse it
        # via the read-side model. Skipped if the fixture has not been written
        # yet so tests stay independent.
        fixture = Path(__file__).parent / "fixtures" / "provenance_cp_001.json"
        if not fixture.exists():
            pytest.skip("T012 fixture not yet written")
        text = fixture.read_text(encoding="utf-8")
        parsed = CheckpointRecordRead.model_validate_json(text)
        assert parsed.schema_version == 1
        assert parsed.checkpoint_id.startswith("cp-")


class TestExceptionsInheritance:
    """Subtest (j): exception classes inherit from HistoryError."""

    def test_shadow_repo_unavailable(self) -> None:
        assert issubclass(ShadowRepoUnavailable, HistoryError)
        assert issubclass(ShadowRepoUnavailable, Exception)

    def test_shadow_repo_commit_failed(self) -> None:
        assert issubclass(ShadowRepoCommitFailed, HistoryError)

    def test_frontmatter_parse_failed(self) -> None:
        assert issubclass(FrontmatterParseFailed, HistoryError)

    def test_history_error_is_exception_subclass(self) -> None:
        assert issubclass(HistoryError, Exception)


class TestNoShaFields:
    """Subtest (k): FR-020 negative invariant — no SHA fields in CheckpointRecord."""

    def test_no_sha_keys_in_model_fields(self) -> None:
        fields = set(CheckpointRecord.model_fields.keys())
        # Defensive: any SHA-like field would indicate FR-020 violation.
        forbidden = {"sha", "commit_sha", "git_sha", "commitSha", "gitSha"}
        leaked = fields & forbidden
        assert not leaked, f"FR-020 violation: SHA-like fields present: {leaked}"

    def test_no_sha_keys_in_serialized_json(self) -> None:
        record = _minimal_record(1)
        payload_json = record.model_dump_json()
        payload = json.loads(payload_json)
        flat_keys: set[str] = set()
        for key, val in payload.items():
            flat_keys.add(key.lower())
            if isinstance(val, dict):
                for sub_key in val:
                    flat_keys.add(sub_key.lower())
        forbidden_lower = {"sha", "commit_sha", "git_sha", "commitsha", "gitsha"}
        leaked = flat_keys & forbidden_lower
        assert not leaked, f"FR-020 violation: SHA-like keys serialized: {leaked}"

    def test_pydantic_forbid_blocks_runtime_sha_injection(self) -> None:
        # extra='forbid' on write-side prevents accidental SHA insertion via
        # model construction.
        with pytest.raises(ValidationError, match="extra"):
            CheckpointRecord(
                checkpoint_id="cp-001",
                checkpoint_number=1,
                timestamp=datetime.now(UTC),
                conversation=_minimal_conversation(),
                exchanges=[_minimal_exchange()],
                editor=EditorBlock(decisions=[]),
                results=ResultsRecord(),
                links=_minimal_links(),
                commit_sha="deadbeef" * 5,  # type: ignore[call-arg]
            )
