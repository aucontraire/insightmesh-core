# Implementation Plan: Pre-flight Validation

**Branch**: `002-pre-flight-validation` | **Date**: 2026-05-23 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-pre-flight-validation/spec.md`

## Summary

Add three pre-orchestrator capabilities to the InsightMesh CLI: (1) an `insightmesh list` subcommand that browses Claude.ai and ChatGPT data exports, (2) a `--conversation <id-or-index>` flag on `batch` that selects a single conversation from such an export, and (3) an agent presence check that aborts `batch` with a clear, aggregated error when any required `.claude/agents/` file is missing or unparseable. All three share one architectural surface: validation the CLI performs *before* delegating to the orchestrator.

Technical approach: schema parsing for Claude.ai and ChatGPT exports is delegated to the `echomine` library (PyPI `echomine>=1.3.0,<2.0.0`) per FR-023 — no hand-rolled adapters inside InsightMesh. One new thin module (`src/exports.py`, ~80 LOC) wraps EchoMine's adapters with the two helpers InsightMesh needs (`list_conversations`, `extract_conversation`) and converts EchoMine's `Conversation` model to the internal `{role, content}` shape Spec 001's `transcript.py` already consumes. Pre-flight aggregation lives in `src/cli.py` adjacent to the existing vault validation (extends Spec 001's FR-011 into a unified pre-flight pass). The `EXPECTED_AGENTS` constant is added to `src/orchestrator.py` as the single source of truth for which agents the pipeline depends on. No new orchestration logic, no config files, no logger schema changes.

## Technical Context

**Language/Version**: Python 3.12+ (per constitution §Project Standards)
**Primary Dependencies**: `echomine>=1.3.0,<2.0.0` (PyPI; Claude.ai and ChatGPT export adapters — new direct dep, force-multiplier per constitution); Typer (CLI), Pydantic v2 (InsightMesh-owned models), existing `claude-agent-sdk` orchestrator integration (unchanged by this spec). PyYAML (agent-file frontmatter parsing) and Rich (CLI table rendering) promoted from transitive to direct deps per `research.md` R1 and R4.
**Storage**: Filesystem only. Reads export JSON files and `.claude/agents/*.md` files. No database, no network, no new persistence.
**Testing**: pytest with new `tests/test_exports.py`, additions to `tests/test_cli.py` (or new file), and Claude.ai + ChatGPT fixture exports under `tests/fixtures/`.
**Target Platform**: macOS / Linux CLI (consistent with Spec 001).
**Project Type**: Single Python CLI project (existing `src/` + `tests/` layout from Spec 001).
**Performance Goals**:
- `insightmesh list`: complete output within 5 seconds for exports of up to 5,000 conversations (SC-002)
- Pre-flight agent check: abort within 1 second of invocation (SC-003)

**Constraints**:
- In-memory parsing of full export files (assumption: ≤10,000 conversations)
- Pre-flight errors go to stderr only, never to `.logs/` (Clarification Q1, FR-019)
- Backward compatibility with Spec 001 flat `{role, content}` JSON arrays is mandatory (FR-014)
- No config file mechanism for `EXPECTED_AGENTS` (FR-018, Non-Goal)

**Scale/Scope**:
- Single new src module (`exports.py`), modifications to `cli.py` and `orchestrator.py`
- One new test module + fixture additions
- Documentation: `docs/getting-started.md` updated for the new `list → pick → batch` flow

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Anti-Slop — Minimal-Diff | ✅ PASS | One new src file (`exports.py`); modifications to `cli.py` and `orchestrator.py` are surgical (new function + constant export). No new abstraction layers. |
| I. Anti-Slop — File Budget | ✅ PASS | 1 new src file + 1 new test file + 2 new fixture files. Total new files: 4. Within budget for a multi-story P1 spec. |
| I. Anti-Slop — Rule of Three | ✅ PASS | No premature abstraction. Two export adapters (Claude.ai, ChatGPT) live as parallel functions; if a third arrives, abstraction is justified then. |
| I. Anti-Slop — Dependency Discipline | ✅ PASS | Three new direct deps: `echomine` (single bounded capability: chat-export parsing and normalization), `pyyaml` (YAML parsing), `rich` (terminal table rendering). All three are force-multipliers per constitution §Project Standards spirit; `echomine` in particular replaces ~300 LOC of hand-rolled adapters with a tested library. No architecture-style deps introduced. No Complexity Justification Table entries required. |
| I. Anti-Slop — No Speculative Architecture | ✅ PASS | Explicit non-goals close off the speculative paths (config file, opt-out flag, multi-conversation batch, prefix syntax). |
| II. Incremental Delivery | ✅ PASS | Three independently testable P1 stories. Story 3 (pre-flight check) ships value even if 1 and 2 are not yet wired in production usage. |
| III. Transparency & Intellectual Rigor | ✅ PASS | All pre-flight failures are visible (stderr) and distinguishable from in-pipeline errors (FR-019). Aggregation (FR-022) gives users a complete picture rather than dribbling errors. |

**Result**: PASS. No Complexity Tracking entries required. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/002-pre-flight-validation/
├── plan.md              # This file
├── research.md          # Phase 0: PyYAML vs regex, export schema verification, CLI table lib
├── data-model.md        # Phase 1: ConversationSummary, Conversation, ExportFile, AgentDefinition, PreflightError
├── quickstart.md        # Phase 1: full list → pick → batch walkthrough on a real export
├── contracts/
│   └── cli-commands.md  # Phase 1: list/batch flag schemas, exit codes, stderr/stdout shapes
├── checklists/
│   └── requirements.md  # From /speckit-specify
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
src/
├── __init__.py
├── cli.py              # MODIFIED: add `list` subcommand, add `--conversation` to `batch`, add aggregated pre-flight
├── exports.py          # NEW (~80 LOC): thin EchoMine wrapper — `list_conversations`, `extract_conversation`, adapter detection. Schema parsing itself lives in echomine.
├── logger.py           # UNCHANGED (per Clarification Q1: pre-flight errors do not touch the logger)
├── orchestrator.py     # MODIFIED: add `EXPECTED_AGENTS` constant (single source of truth for pre-flight)
├── transcript.py       # UNCHANGED (flat array format still supported by FR-014)
└── wiki.py             # UNCHANGED

tests/
├── __init__.py
├── fixtures/
│   ├── claude_ai_export.json    # NEW: small fixture of Claude.ai export shape
│   ├── chatgpt_export.json      # NEW: small fixture of ChatGPT export shape
│   ├── single_topic.json        # EXISTING (Spec 001 flat-array regression)
│   ├── multi_topic.json         # EXISTING
│   ├── revisit.json             # EXISTING
│   ├── empty.json               # EXISTING
│   └── malformed.json           # EXISTING
├── test_cli.py         # NEW or MODIFIED: pre-flight aggregation, list output, --conversation behavior
├── test_exports.py     # NEW: adapter parsing, selection helpers, ordering, edge cases
├── test_logger.py      # UNCHANGED
├── test_transcript.py  # UNCHANGED
└── test_wiki.py        # UNCHANGED

docs/
├── getting-started.md  # MODIFIED: replace the "manually extract one conversation with jq" section with the new `list → pick → batch` flow
├── known-limitations.md # MODIFIED: remove (or mark resolved) the multi-conversation export selection entry
├── index.md            # MODIFIED: update the "single conversation only" callout to reflect the resolved limitation
└── ...

README.md               # MODIFIED: update Quick Taste callout and Status table row for export selection
.claude/agents/         # UNCHANGED (this spec validates their presence, does not modify them)
```

**Structure Decision**: Single Python CLI project — continues the layout established in Spec 001. No new top-level directories. The four new files (`exports.py`, `test_exports.py`, and two fixtures) plus modifications to three existing files (`cli.py`, `orchestrator.py`, and the docs touched in Spec 001) keep the diff surgical and the file count well under the spec's anti-slop budget. The new `exports.py` is intentionally thin (~80 LOC) because the substantive parsing work lives upstream in `echomine` per FR-023.

## Complexity Tracking

> No Constitution Check violations require justification. This table is intentionally left empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | — | — |
