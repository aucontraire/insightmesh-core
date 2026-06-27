"""Per-conversation cursor for checkpointed synthesis (Spec 004).

Defines the `Checkpoint` Pydantic model (the cursor's on-disk shape), the
`DigestEntry` model used to carry "topics already covered" between checkpoints,
plus pure helpers for hashing a transcript and atomically loading/saving the
cursor file.

This module is the single source of truth for "where did processing leave off
on this conversation" (FR-004). The orchestrator reads and writes it; the CLI
derives the cursor's file path; no other code creates Checkpoint instances.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.transcript import ChatTranscript

SCHEMA_VERSION: int = 1
"""Bump on any backward-incompatible change to `Checkpoint` fields. Older
cursors with a mismatching version are rejected by `load_checkpoint` so the
user must explicitly start fresh (FR-016)."""


class DigestEntry(BaseModel):
    """One entry in the topics-covered digest.

    Historian emits one of these per `WikiPageDraft` it augments
    (`topics_covered_increment`). The orchestrator accumulates them into
    `Checkpoint.topics_covered_digest` and passes the accumulated list to
    Synthesis on second-or-later checkpoints so Synthesis can extend rather
    than duplicate prior pages (FR-011).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    page_title: str = Field(min_length=1)
    # 500-char cap raised from 200 after the 2026-06-26 real-data smoke (Spec 004):
    # substantive page summaries naturally run 200-300 chars; 500 gives headroom
    # while still preventing essay-length entries that would defeat the digest's
    # "compact context" purpose.
    gist: str = Field(min_length=1, max_length=500)


class Checkpoint(BaseModel):
    """Per-conversation cursor record.

    One file per `(export_path, conversation_id)` pair under `logs/`. The
    `last_processed_exchange_index` is the cursor; the next invocation resumes
    at index + 1 (FR-003). `transcript_hash` invalidates the cursor cleanly on
    upstream changes (FR-006). `status` distinguishes complete (terminal),
    interrupted (clean stop, resumable silently), and failed (resumable but
    requires `--retry` per FR-014).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    export_path: Path
    conversation_id: str | None = None
    transcript_hash: str = Field(min_length=64, max_length=64)
    last_processed_exchange_index: int = Field(ge=0)
    checkpoint_number: int = Field(ge=1)
    status: Literal["complete", "interrupted", "failed"]
    last_error: str | None = None
    topics_covered_digest: list[DigestEntry] = Field(default_factory=list)
    meaning_summary: str | None = None
    updated_at: datetime


class CheckpointError(Exception):
    """Base for all checkpoint-related errors."""


class CheckpointMissing(CheckpointError):
    """Explicit `--resume` requested but no cursor exists for this conversation."""


class CheckpointHashMismatch(CheckpointError):
    """Cursor's transcript_hash differs from the current transcript's hash."""


class CheckpointAlreadyComplete(CheckpointError):
    """Cursor status is `complete`; rerun by deleting the cursor file."""


class CheckpointSchemaVersionMismatch(CheckpointError):
    """Cursor's schema_version is unknown to this orchestrator version."""


class CheckpointMalformed(CheckpointError):
    """Cursor file exists but is unparseable JSON or fails schema validation."""


class CheckpointIndexOutOfBounds(CheckpointError):
    """Cursor's last_processed_exchange_index exceeds the current transcript length."""


def compute_transcript_hash(transcript: ChatTranscript) -> str:
    """Return the SHA-256 hex digest of the transcript's JSON serialization.

    Pydantic v2's `model_dump_json()` is deterministic for a model that
    contains only ordered primitives + lists (ChatTranscript qualifies), so
    the hash is stable across runs of the same transcript.
    """
    payload = transcript.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_checkpoint(path: Path) -> Checkpoint | None:
    """Load a cursor from disk.

    Returns `None` if the file does not exist (treated as "no cursor yet").
    Raises `CheckpointMalformed` on JSON parse failure or schema validation
    error. Raises `CheckpointSchemaVersionMismatch` when the loaded
    `schema_version` is not the version this orchestrator understands.
    """
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckpointMalformed(f"could not read cursor at {path}: {exc}") from exc

    try:
        parsed_obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckpointMalformed(f"cursor at {path} is not valid JSON: {exc}") from exc

    if isinstance(parsed_obj, dict):
        on_disk_version = parsed_obj.get("schema_version")
        if isinstance(on_disk_version, int) and on_disk_version != SCHEMA_VERSION:
            raise CheckpointSchemaVersionMismatch(
                f"cursor at {path} has schema_version={on_disk_version}, "
                f"but this orchestrator understands schema_version={SCHEMA_VERSION}. "
                f"Delete the cursor to start fresh."
            )

    try:
        return Checkpoint.model_validate_json(text)
    except ValidationError as exc:
        raise CheckpointMalformed(f"cursor at {path} failed schema validation: {exc}") from exc


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    """Atomically write a cursor to disk.

    Writes to `{path}.tmp` and then `os.replace` to `{path}` so a crash mid-write
    cannot leave the cursor in a half-written state (FR-002). Creates the parent
    directory if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    payload = checkpoint.model_dump_json(indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)
