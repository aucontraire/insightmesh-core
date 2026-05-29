# Data Model: Synthesis input hygiene — attachment and pasted text

This feature introduces **no new persisted data models**. The inline representation reuses the existing transcript shapes unchanged. This document records the entities involved and the one new in-memory transformation.

## Reused models (unchanged)

- `src/transcript.py::Message` — `role: Literal["user","assistant"]`, `content: str = Field(min_length=1)`, `timestamp: str | None`. The `min_length=1` constraint is why the inline approach is safe: a folded attachment-only message is non-empty.
- `src/transcript.py::Exchange` — `index`, `user_message`, `assistant_message | None`. Pairing logic unchanged.
- `src/transcript.py::ChatTranscript` — `source_path`, `exchanges`, `metadata`. Serialized to the orchestrator via `model_dump_json()`; shape unchanged.

No fields are added or removed. No new Pydantic submodel is created (this is the deliberate consequence of the inline decision; a structured `attachments` model is explicitly deferred, see research Decision 1).

## Source entity (read-only, from the parser)

- **Attachment** (from `echomine.Message.metadata["attachments"]`): a list of dicts with `file_name: str`, `file_type: str`, `file_size: int`, `extracted_content: str`. Read-only input; not persisted by InsightMesh. `file_name == ""` denotes pasted text; a non-empty `file_name` denotes an uploaded document.

## New transformation (in-memory only)

- `_render_attachments(msg) -> str` (new, in `src/exports.py`): pure function. Reads `msg.metadata.get("attachments")`, skips entries whose `extracted_content` is empty/whitespace, and renders each remaining one as a labeled block (see `contracts/attachment-rendering.md`). Returns the joined blocks, or `""` if none.
- `_to_role_content(messages)` continues to emit `list[dict[str, str]]` of `{"role", "content"}` — the same output type as today. The only change is that `content` may now include the rendered attachment block(s).

## Validation rules (from FRs)

- An attachment with empty/whitespace `extracted_content` contributes nothing (FR-004); it never produces a `{role, content}` entry on its own.
- Non-conversational categories (`reasoning`/`tool_io`/`system`/`media`/`unknown`) are still excluded even if they carry an `attachments` key (FR-005).
- Absent `content_type_category` defaults to conversational, preserving pre-1.4.0 behavior (FR-006).
- Multiple attachments on one message render in their original source order (US1 AC4).
- Messages carrying no attachments pass through unchanged (FR-011).
