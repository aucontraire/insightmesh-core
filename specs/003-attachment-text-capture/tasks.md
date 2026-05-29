---
description: "Task list for Synthesis input hygiene — attachment and pasted text"
---

# Tasks: Synthesis input hygiene — attachment and pasted text

**Input**: Design documents from `/specs/003-attachment-text-capture/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/attachment-rendering.md, quickstart.md

**Tests**: Included. The spec's acceptance scenarios and plan verification enumerate unit + end-to-end tests, and the project standard is pytest with strict typing.

**Organization**: Grouped by user story. US1 (P1) is the MVP and is independently testable at the transcript level; US2 (P2) layers synthesis-side handling on top.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- Most changes cluster in `src/exports.py` and `tests/test_exports.py`, so parallelism is limited by design.

## Path Conventions

Single project: `src/`, `tests/` at repository root.

---

## Phase 1: Setup

**Purpose**: Confirm the environment provides the contract this feature consumes.

- [X] T001 Confirm branch `003-attachment-text-capture` and run `uv sync --all-extras`; verify `echomine>=1.4.0` is installed (it surfaces `Message.metadata["attachments"]`, which this feature reads).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: None. There is no shared prerequisite beyond US1's core projection change; US2 builds directly on it. Proceed to Phase 3.

---

## Phase 3: User Story 1 — Capture pasted and attached text (Priority: P1) MVP

**Goal**: Attachment/pasted extracted text reaches the content synthesis sees, instead of being silently dropped.

**Independent Test**: `extract_conversation` on a Claude conversation containing an attachment with extracted text yields a `ChatTranscript` whose exchanges contain that text (FR-001..FR-004, FR-006, FR-011; SC-001, SC-005).

- [X] T002 [US1] Add `_render_attachments(msg) -> str` helper in `src/exports.py`: read `msg.metadata.get("attachments")`; skip entries whose `extracted_content` is empty/whitespace; render each remaining one as a labeled block (header `file: <name>` when `file_name` is set, else `pasted text`) per `contracts/attachment-rendering.md`; join multiple in original source order with a blank line; return `""` if none.
- [X] T003 [US1] Rewrite `_to_role_content` in `src/exports.py` to harvest attachment text via `_render_attachments` BEFORE the empty-content/category skip and fold it inline: for `content_type_category == "attachment"` (content forced empty) contribute a user turn only if attachment text exists; for `conversational` (default when the field is absent) skip only when both typed text and attachment text are empty, otherwise set content to typed text plus the attachment block(s); leave all other categories excluded; leave messages without attachments unchanged. Update the function docstring to note the ordering rule.
- [X] T004 [US1] Add unit tests in `tests/test_exports.py` (mirroring the existing `TestToRoleContent`/`mk()` style) covering: attachment-only message now surfaces (regression, fails on pre-feature code); conversational message folds typed + attachment text; multiple attachments appear in source order; header reads `file: <name>` vs `pasted text`; empty/whitespace `extracted_content` is ignored (attachment-only stays dropped); non-conversational categories stay dropped even with an `attachments` key; missing `content_type_category` still folds; message with no attachments is unchanged (FR-011); and a ChatGPT-style conversational message (no `attachments` key in metadata) produces identical `_to_role_content` output to the typed text alone (FR-007 / SC-003 no-regression assertion).
- [X] T005 [US1] Add an attachment-bearing conversation to `tests/fixtures/claude_ai_export.json` (one attachment-only message and one conversational-with-attachment message) and update the `len(summaries) == 3` count assertions in `tests/test_exports.py` to `4`.
- [X] T006 [US1] Add an end-to-end test in `tests/test_exports.py`: `extract_conversation` on the new fixture conversation yields a `ChatTranscript` whose exchanges contain the attachment `extracted_content` and the labeled block header.

**Checkpoint**: After T006, the data-loss is fixed and verified at the transcript level. This is a shippable MVP on its own.

---

## Phase 4: User Story 2 — Treat attached content as attributable source material (Priority: P2)

**Goal**: Synthesis treats the folded block as user-provided source material: synthesizes (does not dump verbatim), attributes by filename when present, does not let it dominate the page, and never leaks the delimiter markers.

**Independent Test**: Synthesize a conversation with one named document attachment and one unnamed paste; the page attributes or clearly incorporates the named source, synthesizes both, reproduces neither verbatim, and contains no delimiter markers (FR-008, FR-009, FR-010; US2 acceptance scenarios).

- [X] T007 [P] [US2] Update `.claude/agents/synthesis.md`: add an Input note that a `content` string may contain delimited attached/pasted blocks which are user-provided source material (not the assistant's words), and a Quality Rule that the agent SHOULD attribute by filename when present (FR-008) (for example, "According to the attached `report.pdf`, ..." or an inline named reference), synthesize the substance rather than quoting a large block verbatim and not let it dominate the page (FR-009), never emit the delimiter markers (FR-010), and never invent filenames.
- [ ] T008 [US2] Validate US2 end-to-end per `quickstart.md` against a real Claude export containing a named attachment and a paste (agent behavior, not unit-testable): confirm attribution/incorporation, synthesis-not-verbatim, page not dominated, and no `--- Attached/pasted content ---` markers in the output.

---

## Phase 5: Polish & Cross-Cutting

- [X] T009 Run the full gate and fix any issues: `uv run pytest`, `uv run mypy --strict src/`, `uv run ruff check src/ tests/`, `uv run black --check src/ tests/`.
- [ ] T010 [P] (Optional) Note in `docs/getting-started.md` (or `docs/known-limitations.md`) that pasted/attached text from Claude exports is now captured into synthesis, so users understand the behavior.

---

## Dependencies

- T001 (Setup) before all.
- US1: T002 → T003 → T004 → T005 → T006 (all but T002 touch `src/exports.py` or `tests/test_exports.py`, so sequential).
- US2: T007 depends only on the block format from T002 (different file, can run alongside US1 tests); T008 depends on T003 + T005 + T007 (needs the full path working).
- Polish: T009 after all implementation and tests; T010 anytime after T003.

## Parallel Example

Parallelism is limited because most edits cluster in `src/exports.py` and `tests/test_exports.py`. The genuine parallel pair:

- T007 `[US2]` (`.claude/agents/synthesis.md`) can proceed in parallel with the US1 test tasks (T004/T006 in `tests/test_exports.py`).
- T010 `[P]` (docs) can run alongside T009.

## Implementation Strategy

- **MVP = US1 (T001–T006)**: stops the silent data loss; fully verifiable at the transcript level without any synthesis-prompt change.
- **US2 (T007–T008)** adds synthesis-side handling (attribution, prose synthesis, no leak); its acceptance is validated by manual/agent end-to-end since it is agent behavior.
- Keep the diff minimal (constitution Anti-Slop): no new `src/` module, no new dependency, no transcript-model change. One new helper function in `exports.py`.
