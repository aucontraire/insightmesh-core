"""Tests for `src.checkpoint` — the per-conversation cursor module (Spec 004).

Covers Checkpoint/DigestEntry strict validation, transcript-hash determinism,
atomic load/save behavior, schema_version handling, and the typed exceptions
that drive the orchestrator's resume edge-case branches (FR-002, FR-004,
FR-006, FR-016, FR-017).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.checkpoint import (
    SCHEMA_VERSION,
    Checkpoint,
    CheckpointMalformed,
    CheckpointSchemaVersionMismatch,
    DigestEntry,
    compute_transcript_hash,
    load_checkpoint,
    save_checkpoint,
)
from src.transcript import ChatTranscript, Exchange, Message

_VALID_HASH = "a" * 64


def _make_transcript(content: str = "hello", source: str = "/tmp/x.json") -> ChatTranscript:
    return ChatTranscript(
        source_path=source,
        exchanges=[
            Exchange(
                index=0,
                user_message=Message(role="user", content=content),
                assistant_message=Message(role="assistant", content="hi"),
            )
        ],
    )


def _make_checkpoint(**overrides: object) -> Checkpoint:
    defaults: dict[str, object] = {
        "export_path": Path("/tmp/x.json"),
        "conversation_id": None,
        "transcript_hash": _VALID_HASH,
        "last_processed_exchange_index": 0,
        "checkpoint_number": 1,
        "status": "interrupted",
        "last_error": None,
        "topics_covered_digest": [],
        "meaning_summary": None,
        "updated_at": datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Checkpoint(**defaults)  # type: ignore[arg-type]


class TestCheckpointModel:
    def test_strict_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            Checkpoint(
                export_path=Path("/x"),
                transcript_hash=_VALID_HASH,
                last_processed_exchange_index=0,
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime(2026, 6, 26, tzinfo=UTC),
                rogue_field="not allowed",  # type: ignore[call-arg]
            )

    def test_strict_rejects_wrong_types(self) -> None:
        with pytest.raises(ValidationError):
            Checkpoint(
                export_path=Path("/x"),
                transcript_hash=_VALID_HASH,
                last_processed_exchange_index="zero",  # type: ignore[arg-type]
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime(2026, 6, 26, tzinfo=UTC),
            )

    def test_transcript_hash_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _make_checkpoint(transcript_hash="too-short")
        with pytest.raises(ValidationError):
            _make_checkpoint(transcript_hash="a" * 63)
        with pytest.raises(ValidationError):
            _make_checkpoint(transcript_hash="a" * 65)
        # Exactly 64 is fine.
        _make_checkpoint(transcript_hash="b" * 64)

    def test_status_literal_enforced(self) -> None:
        for ok in ("complete", "interrupted", "failed"):
            _make_checkpoint(status=ok)
        with pytest.raises(ValidationError):
            _make_checkpoint(status="bogus")

    def test_schema_version_defaults_to_one(self) -> None:
        cp = _make_checkpoint()
        assert cp.schema_version == 1
        assert SCHEMA_VERSION == 1

    def test_meaning_summary_defaults_to_none_and_accepts_none_or_str(self) -> None:
        assert _make_checkpoint().meaning_summary is None
        assert _make_checkpoint(meaning_summary="a hint").meaning_summary == "a hint"

    def test_last_processed_exchange_index_ge_zero(self) -> None:
        with pytest.raises(ValidationError):
            _make_checkpoint(last_processed_exchange_index=-1)
        _make_checkpoint(last_processed_exchange_index=0)

    def test_checkpoint_number_ge_one(self) -> None:
        with pytest.raises(ValidationError):
            _make_checkpoint(checkpoint_number=0)
        _make_checkpoint(checkpoint_number=1)


class TestDigestEntry:
    def test_strict_validation(self) -> None:
        de = DigestEntry(page_title="Foo", gist="bar")
        assert de.page_title == "Foo"
        assert de.gist == "bar"

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DigestEntry(page_title="", gist="ok")

    def test_empty_gist_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DigestEntry(page_title="ok", gist="")

    def test_gist_length_cap_500(self) -> None:
        # Raised from 200 to 500 after 2026-06-26 real-data smoke showed
        # Historian-generated gists of ~228 chars for substantive pages.
        DigestEntry(page_title="t", gist="x" * 500)
        with pytest.raises(ValidationError):
            DigestEntry(page_title="t", gist="x" * 501)


class TestComputeTranscriptHash:
    def test_determinism(self) -> None:
        t1 = _make_transcript("hello")
        t2 = _make_transcript("hello")
        assert compute_transcript_hash(t1) == compute_transcript_hash(t2)

    def test_sensitivity(self) -> None:
        t1 = _make_transcript("hello")
        t2 = _make_transcript("world")
        assert compute_transcript_hash(t1) != compute_transcript_hash(t2)

    def test_source_path_affects_hash(self) -> None:
        t1 = _make_transcript("same", source="/a.json")
        t2 = _make_transcript("same", source="/b.json")
        assert compute_transcript_hash(t1) != compute_transcript_hash(t2)

    def test_hash_is_64_hex_chars(self) -> None:
        h = compute_transcript_hash(_make_transcript())
        assert len(h) == 64
        int(h, 16)  # must parse as hex


class TestLoadCheckpoint:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert load_checkpoint(tmp_path / "nonexistent.checkpoint.json") is None

    def test_raises_malformed_on_bad_json(self, tmp_path: Path) -> None:
        path = tmp_path / "x.checkpoint.json"
        path.write_text("{this is not json")
        with pytest.raises(CheckpointMalformed):
            load_checkpoint(path)

    def test_raises_malformed_on_schema_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "x.checkpoint.json"
        # Valid JSON, but missing required fields.
        path.write_text('{"schema_version": 1, "status": "interrupted"}')
        with pytest.raises(CheckpointMalformed):
            load_checkpoint(path)

    def test_raises_schema_version_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "x.checkpoint.json"
        # Even a malformed body should NOT be reached when schema_version
        # is wrong — version check fires first.
        path.write_text('{"schema_version": 999, "unrelated": "garbage"}')
        with pytest.raises(CheckpointSchemaVersionMismatch):
            load_checkpoint(path)

    def test_loads_valid_cursor(self, tmp_path: Path) -> None:
        path = tmp_path / "x.checkpoint.json"
        original = _make_checkpoint()
        save_checkpoint(path, original)
        loaded = load_checkpoint(path)
        assert loaded is not None
        assert loaded.transcript_hash == _VALID_HASH
        assert loaded.status == "interrupted"
        assert loaded.checkpoint_number == 1


class TestSaveCheckpoint:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "rt.checkpoint.json"
        cp = _make_checkpoint(
            topics_covered_digest=[DigestEntry(page_title="A", gist="one")],
            checkpoint_number=3,
            status="complete",
            last_processed_exchange_index=42,
        )
        save_checkpoint(path, cp)
        loaded = load_checkpoint(path)
        assert loaded is not None
        assert loaded.status == "complete"
        assert loaded.last_processed_exchange_index == 42
        assert loaded.checkpoint_number == 3
        assert len(loaded.topics_covered_digest) == 1
        assert loaded.topics_covered_digest[0].page_title == "A"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "newdir" / "deeper" / "x.checkpoint.json"
        assert not path.parent.exists()
        save_checkpoint(path, _make_checkpoint())
        assert path.exists()

    def test_atomic_no_tmp_leftover_on_success(self, tmp_path: Path) -> None:
        path = tmp_path / "atomic.checkpoint.json"
        save_checkpoint(path, _make_checkpoint())
        tmp_path_sibling = path.with_name(path.name + ".tmp")
        assert path.exists()
        assert not tmp_path_sibling.exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "o.checkpoint.json"
        save_checkpoint(path, _make_checkpoint(status="interrupted"))
        save_checkpoint(path, _make_checkpoint(status="complete", checkpoint_number=2))
        loaded = load_checkpoint(path)
        assert loaded is not None
        assert loaded.status == "complete"
        assert loaded.checkpoint_number == 2

    def test_written_json_is_pretty_printed(self, tmp_path: Path) -> None:
        path = tmp_path / "p.checkpoint.json"
        save_checkpoint(path, _make_checkpoint())
        contents = path.read_text(encoding="utf-8")
        # indent=2 → newlines present.
        assert "\n" in contents
        # And must parse back as JSON.
        json.loads(contents)
