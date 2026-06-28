# Implementation Plan: Per-page provenance with shadow git and structured checkpoint JSON

**Branch**: `005-page-provenance` | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/005-page-provenance/spec.md`

## Summary

Persist the provenance the pipeline already produces. After each successful checkpoint (Spec 004 loop), the orchestrator writes a permanent, queryable record of "what happened" two ways: (a) a structured `cp-<NNN>.json` file under `<vault>/InsightMesh/.history/checkpoints/<conv-id-or-"_flat">/` carrying the conversation block, per-exchange message identifiers, and per-page Editor decisions including rationale, confidence, and the full signals dict; and (b) a cumulative `provenance:` frontmatter block on each touched wiki page summarizing the latest checkpoint, conversation set, action/confidence, total edits, and exchange count. The same orchestrator step also snapshots each touched page into a shadow git repository at `<vault>/InsightMesh/.history/` and commits both the snapshot and the JSON in one machine-greppable commit per checkpoint. All bookkeeping is orchestrator-side; the Editor agent's contract is unchanged, the session log is preserved untouched, and the Spec 004 cursor remains the resume state of record. The JSON schema uses additive forward-compatibility within `schema_version=1`. The on-disk artifacts produced by this spec (`cp-<NNN>.json` files, page frontmatter `provenance:` blocks, shadow-repo commits) are deliberately designed as a stable read contract for future viewers, including a planned dedicated in-Obsidian viewer plugin shipping from a separate repo ([aucontraire/insightmesh-obsidian](https://github.com/aucontraire/insightmesh-obsidian)); no Spec 005 changes are required to enable that plugin.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2 (provenance record models), PyYAML (frontmatter parse/merge; already pinned in Spec 002), `subprocess` from stdlib (shadow-repo git invocations), echomine (transcript extraction; metadata extension only), claude-agent-sdk (unchanged), Typer (unchanged). **No new dependencies.**
**Storage**: Local filesystem. Per-checkpoint JSON files and page snapshots under `<vault>/InsightMesh/.history/`. The shadow git repository lives in the same directory and is wholly separate from any git the user maintains at the vault root. The session log under `<vault>/InsightMesh/.logs/` and the Spec 004 cursor under `<vault>/InsightMesh/.logs/...checkpoint.json` are unchanged.
**Testing**: pytest via `uv run`
**Target Platform**: Local CLI (macOS / Linux); the shadow-repo write path requires only that `git` be on `PATH` (FR-015 documents the fallback when it is not).
**Project Type**: Single project (CLI + filesystem sub-agents).
**Performance Goals**: Provenance bookkeeping completes in under 1 s of wall-clock per checkpoint on a vault with hundreds of pages (SC-006), measured separately from agent work which still dominates total runtime.
**Constraints**: Local-first; strict typing (mypy strict); Minimal-Diff (one new `src/` file justified analogously to Spec 004's `src/checkpoint.py`); single-writer-per-conversation extends from Spec 004; provenance failure MUST NOT block agent work or cursor advancement (FR-016, FR-019).
**Scale/Scope**: One new `src/history.py` module; surgical extensions to `src/orchestrator.py` (one new post-Editor step) and `src/exports.py` (populate `ChatTranscript.metadata` with `provider`, `models_used`, per-message identifiers from echomine 1.5.0). Tests cover schema round-trip + extras tolerance, frontmatter merge math, shadow-repo init idempotency, fallback paths (no git, commit failure), and an end-to-end real-data smoke run after merge.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Anti-Slop Engineering**: PASS (with one justified new file). Modifies `src/orchestrator.py` and `src/exports.py`; adds ONE new `src/` module (`history.py`) for the provenance record Pydantic models + frontmatter merge + shadow-repo helpers + custom exceptions. Alternative locations were considered (see Complexity Tracking) and rejected for the same reasons Spec 004 rejected co-locating its cursor: different lifecycle (per-checkpoint provenance vs per-invocation session log vs per-resume cursor) and isolated testability. No new dependencies. No new abstractions beyond the provenance data shape itself.
- **II. Incremental Delivery**: PASS. US1 (structured JSON + cumulative frontmatter block) is the MVP, independently testable on a fixture transcript; the structured data is the value lever and is shippable without the git layer. US2 (shadow-repo diff history) layers on top and is also independently testable.
- **III. Transparency and Intellectual Rigor**: PASS. Provenance IS this principle made permanent: Editor's rationale, the signals dict that drove each decision, and which exchanges contributed to which pages now live on disk as a queryable system of record rather than disappearing into transient session logs. The shadow repo's `git log -p` view operationalizes "every output must be traceable" for end users.
- **Project Standards**: PASS. `CheckpointRecord`, `EditorDecisionRecord`, `ExchangeRecord`, `ConversationRecord`, and `ProvenanceFrontmatter` are `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)`. The read side uses `ConfigDict(extra="allow")` to honor the additive forward-compatibility rule (FR-002), a deliberate spec-mandated deviation from the default strict-extras posture; documented inline. All commands run via `uv run`. No `@dataclass`, `NamedTuple`, or `namedtuple` introduced. Ruff `TID251` continues to gate.
- **Architecture Principles**: PASS. Single responsibility (the history module owns provenance writes; the cursor module continues to own resume state). Agents remain stateless: provenance is orchestrator-derived from `_AgentCall.parsed_output` and other orchestrator-side state; agents receive no provenance-related input.
- **Code Quality Principles**: PASS. Custom exceptions (`ShadowRepoUnavailable`, `ShadowRepoCommitFailed`, `FrontmatterParseFailed`) for the documented failure paths per Explicit Error Types principle. Errors are caught at the orchestrator seam and logged to stderr; they do NOT propagate to fail the run (FR-015, FR-016).

Post-Phase-1 re-check: still PASS. Phase 1 design adds one Pydantic module, two thin extensions to existing modules, no new abstractions, no new dependencies. CLAUDE.md updated to point at this plan.

## Project Structure

### Documentation (this feature)

```text
specs/005-page-provenance/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── history-orchestrator.md   # Phase 1 output: internal contract between orchestrator, history module, and on-disk artifacts
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
src/
├── history.py                # NEW: CheckpointRecord / EditorDecisionRecord / ExchangeRecord / ConversationRecord / ProvenanceFrontmatter Pydantic models; pure helpers — compute_checkpoint_payload, write_checkpoint_metadata (atomic), merge_page_provenance, snapshot_page, init_shadow_repo (idempotent), commit_checkpoint; custom exceptions (ShadowRepoUnavailable, ShadowRepoCommitFailed, FrontmatterParseFailed); read-side models use ConfigDict(extra="allow") per FR-002
├── orchestrator.py           # modified: after _execute_pipeline succeeds and Editor returned an EditorOutput, call into history.py — write checkpoint JSON, merge frontmatter on every created/updated page, snapshot pages, init shadow repo, commit; happens BEFORE Spec 004 cursor save so cursor advance still gates terminal success; provenance failures are logged to stderr but never re-raised to the run-level
├── exports.py                # modified: in extract_conversation, populate ChatTranscript.metadata with provider ("anthropic" | "openai" | None for flat-array), models_used (from Conversation.models_used; [] for Spec 001 flat-array), and per-message identifiers threaded through into a per-exchange map so the orchestrator can resolve user_message_id and assistant_message_id by exchange index
├── checkpoint.py             # reference only: Spec 004 cursor stays the resume state of record; not touched
├── logger.py                 # reference only: session log stays as operational/diagnostic artifact (FR-018); not touched
├── transcript.py             # reference only: ChatTranscript.metadata: dict[str, Any] already exists; no schema change required
├── cli.py                    # reference only: no new flags this spec; the existing batch command surface is sufficient
└── wiki.py                   # reference only: Editor's FR-007 update path and page-write mechanism unchanged

.claude/agents/
├── editor.md                 # documentation-only addition: noting that the orchestrator owns the provenance: frontmatter block and Editor MUST NOT emit one in drafts (FR-017). No behavior change.
├── synthesis.md              # reference only
└── historian.md              # reference only

tests/
├── test_history.py           # NEW: unit tests for CheckpointRecord/EditorDecisionRecord/ExchangeRecord schema validation (strict rejects extras on write, allows on read), checkpoint JSON round-trip, atomic write semantics, init_shadow_repo idempotency, frontmatter merge cumulative math (new page, existing pages with prior provenance, missing-block fallback, malformed-YAML fallback), commit message format (subject + body)
├── test_orchestrator.py      # extended: integration tests for end-to-end provenance write after a mocked Editor success (one and two checkpoints back-to-back), correct subdirectory layout (per conv-id), correct frontmatter cumulative fields across two checkpoints touching the same page, no-git fallback path, commit-failure fallback path, empty-results no-write path
├── test_exports.py           # extended (if file exists; otherwise covered in test_orchestrator): ChatTranscript.metadata correctly populated for Claude-style and OpenAI-style exports and the Spec 001 flat-array shape
└── fixtures/
    └── provenance_cp_001.json   # NEW: a hand-authored checkpoint JSON fixture used to exercise the read path's forward-compatibility (additional unknown fields tolerated; missing optional fields defaulted)
```

**Structure Decision**: Single project; one new `src/` file (`history.py`) is justified in Complexity Tracking. No new abstraction beyond the provenance data models themselves. The orchestrator is the only module that imports `history.py`. Tests are added next to the existing test suite; no new top-level directories.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New file `src/history.py` | Encapsulates the `CheckpointRecord` / `EditorDecisionRecord` / `ExchangeRecord` / `ConversationRecord` / `ProvenanceFrontmatter` Pydantic models, atomic JSON write, frontmatter merge with cumulative math, shadow-repo init + commit, and custom exceptions for the documented failure paths (`ShadowRepoUnavailable`, `ShadowRepoCommitFailed`, `FrontmatterParseFailed`). Imported only by `src/orchestrator.py`. | Placing it in `src/checkpoint.py` would conflate per-conversation resume state with per-checkpoint provenance record (different lifecycles: cursor mutates across invocations, checkpoint JSONs are immutable and additive). Placing it in `src/logger.py` would conflate per-invocation operational diagnostics with per-checkpoint permanent provenance (FR-018 explicitly keeps the two artifacts distinct). Placing it in `src/wiki.py` would conflate page production with page bookkeeping. Inlining into `src/orchestrator.py` would bloat that module past 1000 lines and prevent isolated unit testing of the frontmatter merge math + shadow-repo helpers. |
