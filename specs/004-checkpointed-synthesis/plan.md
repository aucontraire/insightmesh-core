# Implementation Plan: Checkpointed synthesis with wiki-as-carry-over

**Branch**: `004-checkpointed-synthesis` | **Date**: 2026-06-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/004-checkpointed-synthesis/spec.md`

## Summary

Process chat transcripts in linear forward checkpoints so long real conversations no longer overflow the model's context window in a single pass, and interrupted runs are resumable. After each successful checkpoint, the orchestrator persists a per-conversation cursor (JSON sidecar under `logs/`) keyed on `(export_path, conversation_id)`. On resume, the cursor is the single source of truth for "where did we leave off." Wiki pages produced by prior checkpoints reach subsequent Synthesis calls as a compact topics-covered digest from Historian (titles plus one-line gists), keeping Synthesis input lean. The CLI gains only `--resume` and `--max-exchanges N` flags; non-linear slicing is structurally rejected because it would corrupt the carry-over invariant.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2 (Checkpoint model), Typer (new CLI flags), claude-agent-sdk (agent invocations unchanged), echomine (transcript extraction unchanged), PyYAML (existing), Rich (existing). No new dependencies.
**Storage**: Local filesystem. Cursor JSON sidecars in existing `logs/` directory, alongside `SessionLog` JSON. Obsidian vault remains the wiki output via MCPVault.
**Testing**: pytest via `uv run`
**Target Platform**: Local CLI (macOS/Linux)
**Project Type**: Single project (CLI + filesystem sub-agents)
**Performance Goals**: Each checkpoint's Synthesis input fits within approximately 50% of the underlying model's context window (FR-015). Resume MUST NOT re-invoke any agent for an already-processed exchange (SC-002).
**Constraints**: Local-first; strict typing (mypy strict); Minimal-Diff (only ONE new `src/` file); single-process-per-conversation (concurrency is an Assumption-level non-goal).
**Scale/Scope**: One new `src/checkpoint.py` module; orchestrator slicing + checkpoint write; CLI gains two flags; synthesis and historian prompts gain notes about the topics-covered digest. Tests cover load/save, hash invalidation, resume skipping, soft cap, no-op-on-complete, and an end-to-end real-data smoke run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Anti-Slop Engineering**: PASS (with one justified new file). Modifies `src/orchestrator.py`, `src/cli.py`, `src/logger.py` (extends `HistorianOutput`), `.claude/agents/synthesis.md`, `.claude/agents/historian.md`. Adds ONE new `src/` module (`checkpoint.py`) for the Pydantic `Checkpoint` model + transcript-hash computation + atomic load/save + custom exceptions. Alternative locations were considered (see Complexity Tracking) and rejected. No new dependencies. No new abstractions beyond the cursor data shape itself.
- **II. Incremental Delivery**: PASS. US1 (multi-checkpoint synthesis with auto-resume) is the MVP, independently testable on a long real export. US2 (per-invocation cap) layers on without blocking US1.
- **III. Transparency and Intellectual Rigor**: PASS. The cursor file is human-readable JSON; the three-state status plus `last_error` make failure modes visible; transcript hash detection prevents silent corruption on re-export. The topics-covered digest gives Synthesis explicit awareness of its prior outputs (no hidden state, no opaque memory blob).
- **Project Standards**: PASS. `Checkpoint` and `DigestEntry` are `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)`. All commands run via `uv run`. No `@dataclass`, `NamedTuple`, or `namedtuple` introduced. Ruff `TID251` continues to gate.
- **Architecture Principles**: PASS. Single responsibility (the cursor module owns one concept). Agents remain stateless (the cursor is orchestrator state, not agent state; agents receive explicit input on every invocation).
- **Code Quality Principles**: PASS. Custom exceptions (`CheckpointHashMismatch`, `CheckpointMissing`, `CheckpointAlreadyComplete`) for the resume edge cases per Explicit Error Types principle.

Post-Phase-1 re-check: still PASS. Design adds one Pydantic module, one extension to `HistorianOutput`, no new abstractions, no new dependencies. CLAUDE.md updated to point at this plan.

## Project Structure

### Documentation (this feature)

```text
specs/004-checkpointed-synthesis/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── checkpoint-orchestrator.md   # Phase 1 output: internal contract between orchestrator, checkpoint module, and agents
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
src/
├── checkpoint.py            # NEW: Checkpoint Pydantic model, DigestEntry, compute_hash, load, save, custom exceptions
├── orchestrator.py          # modified: accept checkpoint_path / max_exchanges; slice transcript on resume; budget-based checkpoint sizing; write checkpoint after each successful checkpoint; emit "already complete" / "prior failure" messages; merge HistorianOutput.topics_covered_increment into cursor; pass cursor.topics_covered_digest to Synthesis input on second-or-later checkpoints
├── cli.py                   # modified: add --resume and --max-exchanges N flags; derive default cursor path from logs_dir and (export_path, conversation_id)
├── logger.py                # modified: extend HistorianOutput with topics_covered_increment: list[DigestEntry] | None = None
├── exports.py               # reference only (transcript shape unchanged; existing _to_role_content unchanged)
├── transcript.py            # reference only (ChatTranscript.exchanges supports list slicing today)
└── wiki.py                  # reference only (existing exchange_indices and FR-007 update path unchanged)

.claude/agents/
├── synthesis.md             # modified: input may include a topics_covered_digest from prior checkpoints; extend rather than duplicate; never inline full prior-page bodies
├── historian.md             # modified: output gains topics_covered_increment field (one entry per draft processed: title + one-line gist)
└── editor.md                # reference only (existing FR-007 three-signal update rule handles re-runs idempotently)

tests/
├── test_checkpoint.py       # NEW: unit tests for Checkpoint model, compute_hash determinism, load/save roundtrip, missing/mismatched cursor errors, accumulate digest
├── test_orchestrator.py     # modified or new file: integration tests for resume skipping, soft cap, no-op-on-complete, transcript-hash invalidation, failure-status persistence
└── fixtures/
    └── long_chat_export.json   # NEW: a fixture transcript large enough to span more than one checkpoint at the test token budget
```

**Structure Decision**: Single project; one new `src/` file (`checkpoint.py`) is justified in Complexity Tracking. No new abstraction beyond the cursor data model itself. Orchestrator is the only code that imports and uses the checkpoint module (CLI passes paths through). Agent prompts gain explicit notes about the digest contract; no new agent.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New file `src/checkpoint.py` | Encapsulates the `Checkpoint` Pydantic model, `DigestEntry` model, transcript-hash computation, atomic load/save, and custom exceptions for resume edge cases (`CheckpointHashMismatch`, `CheckpointMissing`, `CheckpointAlreadyComplete`). Imported by the orchestrator and (for path derivation) the CLI. | Placing it in `src/logger.py` would conflate per-session log lifecycle with per-conversation cursor lifecycle (different semantics: session log is one-per-invocation, cursor is one-per-conversation-across-invocations). Placing it in `src/transcript.py` would conflate immutable input shape with mutable run state (transcript is input, cursor is state). Inlining into `src/orchestrator.py` would bloat that module and prevent isolated unit testing of hash/load/save behavior. |
