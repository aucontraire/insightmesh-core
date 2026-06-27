# Data Model: Checkpointed synthesis with wiki-as-carry-over

Phase 1 output. Defines the new and modified data shapes. All new classes are `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)` per constitution Project Standards (no `@dataclass`, no `NamedTuple`).

## New models (in `src/checkpoint.py`)

### `Checkpoint`

The per-conversation state record. Single source of truth for "where did processing leave off on this conversation."

```python
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Checkpoint(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: int = 1                           # bump on incompatible field changes; resume refuses on unknown
    export_path: Path                                 # absolute path of the input file as the CLI saw it
    conversation_id: str | None = None                # None for single-conversation source files
    transcript_hash: str = Field(min_length=64, max_length=64)  # SHA-256 hex of ChatTranscript.model_dump_json()
    last_processed_exchange_index: int = Field(ge=0)  # cursor; next run resumes at this + 1
    checkpoint_number: int = Field(ge=1)              # monotonic; increments per successful checkpoint write
    status: Literal["complete", "interrupted", "failed"]
    last_error: str | None = None                     # populated when status == "failed"
    topics_covered_digest: list["DigestEntry"] = Field(default_factory=list)  # accumulated across checkpoints; passed to Synthesis on checkpoint #2+
    meaning_summary: str | None = None                # forward-compatibility hook (see plan guardrail); null in this spec
    updated_at: datetime
```

**Validation rules** (derived from FRs):
- `last_processed_exchange_index >= 0` (FR-001 / FR-003).
- `transcript_hash` length exactly 64 (SHA-256 hex digest; FR-006).
- `status` exactly one of the three literals (FR-014 / Clarification Q1).
- `last_error` is `None` unless `status == "failed"`. (Soft invariant; not enforced by a validator to keep the model simple, but the orchestrator only sets `last_error` when writing a `failed` status.)
- `topics_covered_digest` is appended-only across the lifetime of a conversation (the orchestrator merges Historian's `topics_covered_increment` into it after each successful checkpoint).
- `meaning_summary` MUST remain `None` in this spec (guardrail in plan.md). Future iterations may populate it from already-existing agent output only.

**State transitions**:
- Initial → write with status `complete` (single-checkpoint run reached end) or `interrupted` (cap reached or manual stop or further checkpoints to come) or `failed` (error).
- `interrupted` → resume → continues; new write replaces prior with the new index, new status, new `updated_at`. `checkpoint_number` increments.
- `failed` → resume → surfaces prior `last_error`, then proceeds as if `interrupted`. `last_error` is cleared on the next successful write.
- `complete` → re-run → no-op (FR-007); the orchestrator detects this and exits without invoking agents.

### `DigestEntry`

One topic-covered entry produced by Historian per draft in a checkpoint. Accumulated into `Checkpoint.topics_covered_digest`.

```python
class DigestEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    page_title: str = Field(min_length=1)
    gist: str = Field(min_length=1, max_length=500)
```

**Validation rules**:
- `page_title` MUST match the `tentative_title` from the corresponding `WikiPageDraft` (provenance: same string).
- `gist` is a brief 1–2 sentence summary (no newlines), 500-char cap. Cap was raised from 200 after the 2026-06-26 real-data smoke showed substantive Historian-generated gists running 200–300 chars; 500 gives headroom while still preventing essay-length entries.

### Custom exceptions

```python
class CheckpointError(Exception):
    """Base for checkpoint-related errors."""


class CheckpointMissing(CheckpointError):
    """--resume requested but no cursor exists for this conversation."""


class CheckpointHashMismatch(CheckpointError):
    """Cursor exists but transcript hash has changed since the cursor was written."""


class CheckpointAlreadyComplete(CheckpointError):
    """Cursor status is 'complete'; rerun the conversation by deleting the cursor."""
```

## Modified models (in `src/logger.py`)

### `HistorianOutput` (extended)

Adds a single optional field. Pre-existing fields unchanged.

```python
class HistorianOutput(BaseModel):
    model_config = ConfigDict(strict=True)

    augmented_drafts: list[AugmentedDraft]              # existing
    topics_covered_increment: list[DigestEntry] | None = None  # NEW: one entry per draft in this checkpoint
```

**Backward compatibility**: `topics_covered_increment` defaults to `None`. Pre-feature Historian outputs (no increment) parse without error. The orchestrator treats `None` and empty list as "nothing to merge."

**Reasoning for None vs empty list**: `None` semantically means "Historian did not emit this field" (older agent prompt); empty list means "Historian emitted no entries this checkpoint" (unlikely but valid). Both result in the same orchestrator behavior.

## Reused models (unchanged)

These models are referenced by the new flow but require no schema changes.

- `src/transcript.py::ChatTranscript` — `source_path`, `exchanges`, `metadata`. Sliced by the orchestrator via standard list slicing (`exchanges[start:stop]`).
- `src/transcript.py::Exchange` — `index`, `user_message`, `assistant_message`. Unchanged.
- `src/transcript.py::Message` — unchanged.
- `src/wiki.py::WikiPageDraft` — `tentative_title`, `exchange_indices`, `draft_content`, `suggested_tags`. Unchanged; the `tentative_title` is the source of `DigestEntry.page_title`.
- `src/wiki.py::AugmentedDraft` — unchanged.
- `src/logger.py::SynthesisOutput` — `drafts`. Unchanged.
- `src/logger.py::EditorOutput` — `results`, `decisions`. Unchanged.
- `src/logger.py::EditorDecision` — `exchange_indices`, `signals`, `confidence`, `rationale`. Unchanged (the cursor is the new state, not the EditorDecision).
- `src/logger.py::SessionLog` — unchanged. SessionLog is per-invocation; the cursor is per-conversation. They coexist.

## Data flow (new)

```text
Invocation N (checkpoint K):
  1. Orchestrator loads Checkpoint (if exists) and validates transcript_hash.
  2. If status == "complete": exit with "already complete" message.
  3. If status == "failed": surface last_error to user; proceed if confirmed.
  4. Slice transcript: exchanges[last_processed_exchange_index + 1 : ...]
  5. Decide checkpoint boundary by token budget; trim exchanges if needed.
  6. Build Synthesis input:
     - Always: new exchanges
     - If checkpoint_number > 1: include topics_covered_digest from cursor
  7. Invoke Synthesis -> drafts.
  8. Invoke Historian -> augmented_drafts + topics_covered_increment.
  9. Invoke Editor -> WikiPageResults.
  10. On Editor success:
      - Compute new last_processed_exchange_index
      - Append topics_covered_increment to cursor.topics_covered_digest
      - Increment checkpoint_number
      - Set status: "complete" if reached end; "interrupted" if more remains
      - Atomically write cursor
  11. On agent/write failure at any step:
      - Set status: "failed", populate last_error
      - Atomically write cursor
      - Exit non-zero
```

## Frontmatter / wiki-page shape

**Unchanged.** This spec deliberately does NOT touch the wiki page frontmatter (per FR-004 and the Out of Scope note on per-page provenance). The cursor is the only new persisted artifact.
