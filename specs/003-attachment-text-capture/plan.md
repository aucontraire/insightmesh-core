# Implementation Plan: Synthesis input hygiene — attachment and pasted text

**Branch**: `003-attachment-text-capture` | **Date**: 2026-05-29 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/003-attachment-text-capture/spec.md`

## Summary

Fold Claude attachment and pasted text into the content synthesis receives, so the wiki stops silently dropping user-provided source material. The text already arrives from the parser in `Message.metadata["attachments"][].extracted_content` but is discarded at the projection step. Approach: harvest attachment text **before** the empty-content/category skip in `src/exports.py::_to_role_content`, and fold it **inline** into the owning message's content as a labeled delimiter block (header = filename, or "pasted text" when unnamed). Update the synthesis agent prompt to treat that block as user-provided source material. Text only; images and binaries are out of scope.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Typer, echomine>=1.4.0 (surfaces `metadata["attachments"]`), PyYAML, Rich, claude-agent-sdk
**Storage**: Local filesystem (Obsidian vault markdown + JSON session logs). Not exercised by this feature.
**Testing**: pytest via `uv run`
**Target Platform**: Local CLI (macOS/Linux)
**Project Type**: Single project (CLI + filesystem sub-agents)
**Performance Goals**: N/A. Attachment text is included in full (clarified, no cap); very large inputs fall under the existing documented long-chat token-limit limitation.
**Constraints**: Local-first; strict typing (mypy strict); Minimal-Diff (no new `src/` module, no new dependency).
**Scale/Scope**: One projection-behavior change plus a synthesis prompt edit, with unit tests and one fixture conversation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Anti-Slop Engineering**: PASS. Modifies existing `src/exports.py` and `.claude/agents/synthesis.md`; adds tests and one fixture conversation. No new `src/` module, no new abstraction, no new dependency. The INLINE representation was chosen specifically to avoid a transcript-model change (Minimal-Diff). One new helper function (`_render_attachments`) is local to `exports.py` and removes branching duplication in the rewritten projection.
- **II. Incremental Delivery**: PASS. User Story 1 (capture) is an independently testable MVP; User Story 2 (attribution and prose handling) layers on without blocking P1.
- **III. Transparency and Intellectual Rigor**: PASS. FR-008 attributes attached source material by filename, consistent with the source-attribution principle.
- **Project Standards**: PASS. No new Pydantic models needed (inline folding keeps the existing `Message`/`Exchange`/`ChatTranscript` shapes). uv / mypy strict / Ruff (TID251) / pytest unchanged. No `@dataclass` introduced.
- **Complexity Justification Table**: Not required (no violations).

Post-Phase-1 re-check: still PASS. The design adds no files beyond the test fixture and no new data shapes.

## Project Structure

### Documentation (this feature)

```text
specs/003-attachment-text-capture/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── attachment-rendering.md   # Phase 1 output (internal exports -> synthesis contract)
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
src/
└── exports.py           # _render_attachments (new helper) + _to_role_content rewrite (harvest before skip)

.claude/agents/
└── synthesis.md         # Input note + "attachments are source material" quality rule

tests/
├── test_exports.py      # unit tests for _render_attachments + _to_role_content
└── fixtures/
    └── claude_ai_export.json   # add an attachment-bearing conversation for the end-to-end test
```

**Structure Decision**: Single project; reuse the existing export projection path. No new modules (Minimal-Diff). `src/transcript.py`, `src/orchestrator.py`, and `src/cli.py` are unchanged: the inline approach keeps `model_dump_json()` and the CLI contract identical.

## Complexity Tracking

No constitution violations; table intentionally empty.
