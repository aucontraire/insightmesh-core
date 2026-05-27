# Feature Specification: Pre-flight Validation

**Feature Branch**: `002-pre-flight-validation`  
**Created**: 2026-05-23  
**Status**: Draft  
**Input**: User description: "Pre-flight validation for InsightMesh CLI: list and select conversations from Claude.ai/ChatGPT exports, and verify required sub-agents exist before invoking the orchestrator."

## Clarifications

### Session 2026-05-23

- Q: Where do pre-flight failures get recorded? → A: stderr only; no `.logs/` write and no `SessionLog` schema change.
- Q: How strict should export adapters be about unrecognized fields? → A: silent ignore (Pydantic `extra="ignore"` default); no warning, no refusal.
- Q: Should `--conversation` support explicit `id:`/`index:` prefixes? → A: no; use the implicit numeric-in-range rule only.
- Q: When the agent presence check runs alongside the existing vault validation, in what order do errors surface? → A: run both; aggregate all pre-flight failures (vault + agents) into one consolidated error.
- Q: Does `insightmesh list` take `--vault`? → A: no; `list` is a pure read on the export file and accepts only the export path.

### Session 2026-05-24

- Q: Where does export schema parsing live? → A: external dependency on the `echomine` library (PyPI, `echomine>=1.3.0`), consumed via its public library API (`from echomine import ClaudeAdapter, OpenAIAdapter`). InsightMesh does not implement, fork, or wrap Claude.ai or ChatGPT parsers itself; new providers become available by upgrading the `echomine` dependency.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - List Conversations in a Multi-Conversation Export (Priority: P1)

A user has downloaded their data from Claude.ai or ChatGPT and wants to see what conversations are in the export so they can choose one to synthesize. They run `insightmesh list <export.json>` and get a table of conversations showing id, title, creation date, and message count.

**Why this priority**: Today the CLI accepts only a flat `{role, content}` JSON array. Pointing it at a real Claude.ai or ChatGPT export silently fails or misinterprets the file. This is the only thing currently blocking real-data usage. Users cannot make the first interesting choice (which conversation to process) without inspecting the export by hand. The list subcommand is the entry point that makes the whole pipeline approachable on actual user data.

**Independent Test**: Run `insightmesh list` against a fixture file in each supported format (Claude.ai export, ChatGPT export). Verify the output table contains the correct number of rows, displays human-readable titles, and orders rows in a predictable way (most recent first). Fixtures: `tests/fixtures/claude_ai_export.json` (3-conversation Claude.ai sample) and `tests/fixtures/chatgpt_export.json` (3-conversation ChatGPT sample including a branched mapping). Expected `list` output is captured as a golden file under `tests/fixtures/expected/` and compared verbatim.

**Acceptance Scenarios**:

1. **Given** a valid Claude.ai export with N conversations, **When** the user runs `insightmesh list export.json`, **Then** the CLI prints a table with N rows showing id, title, created date, and message count for each conversation
2. **Given** a valid ChatGPT export with N conversations, **When** the user runs `insightmesh list export.json`, **Then** the CLI produces equivalent output using ChatGPT's schema fields mapped to the same columns
3. **Given** an export file with zero conversations, **When** the user runs `insightmesh list export.json`, **Then** the CLI prints a clear message indicating zero conversations and exits cleanly with code 0
4. **Given** a file that is not a recognized export format (e.g., the flat `{role, content}` array from Spec 001), **When** the user runs `insightmesh list export.json`, **Then** the CLI reports a clear error explaining the expected format and exits with a non-zero code
5. **Given** an export with very long titles or unusual characters, **When** the CLI prints the table, **Then** titles are truncated or escaped without breaking column alignment

---

### User Story 2 - Process a Selected Conversation from an Export (Priority: P1)

A user has identified a conversation in their export (via `insightmesh list` or by knowing the conversation id). They want to run the batch synthesis pipeline on just that conversation without manually extracting it with `jq`. They pass `--conversation <id>` or `--conversation <index>` to `insightmesh batch` along with the export file path.

**Why this priority**: This is the complement to Story 1 and the actual unblock for real-data usage. Without it, users still have to write `jq` pipelines to extract conversations before running batch. With it, the full workflow becomes `list → pick → batch --conversation`.

**Relationship to User Story 1**: Story 1 produces the information users need to make the choice; Story 2 acts on that choice. They ship together as a coherent CLI flow but are independently testable: Story 1 can be validated by output inspection, Story 2 by end-to-end pipeline runs against pre-known conversation ids.

**Independent Test**: Given a fixture export, run `insightmesh batch export.json --conversation <id>` against a test vault. Verify the pipeline runs end-to-end and produces wiki pages for exactly that conversation, identical to what Spec 001 would produce if the conversation had been extracted manually. Fixtures: same as User Story 1. Verify that `batch ... --conversation 0` and `batch ... --conversation <id-of-first>` produce identical wiki output (one resolves by index, one by id, both target the same conversation).

**Acceptance Scenarios**:

1. **Given** an export file and a valid `--conversation <id>`, **When** the user runs `insightmesh batch export.json --conversation abc123 --vault ~/vault`, **Then** the pipeline processes only the messages of that conversation and writes wiki pages to the vault
2. **Given** an export file and a valid `--conversation <index>` (zero-indexed integer), **When** the user runs the batch command, **Then** the pipeline processes the conversation at that index (where index matches the row order from `insightmesh list`)
3. **Given** an export file and a `--conversation` value that does not match any conversation, **When** the user runs the batch command, **Then** the CLI reports a clear error naming the invalid id or index and exits with a non-zero code before invoking the orchestrator
4. **Given** an export file with no `--conversation` flag specified, **When** the user runs the batch command, **Then** the CLI reports an error explaining that exports require explicit conversation selection and suggests running `insightmesh list` first
5. **Given** the existing flat `{role, content}` JSON array format from Spec 001, **When** the user runs `insightmesh batch transcript.json --vault ~/vault` without `--conversation`, **Then** Spec 001's existing behavior is preserved (flat arrays continue to work without the new flag)

---

### User Story 3 - Pre-flight Agent Presence Check (Priority: P1)

A user runs `insightmesh batch ...` but one of the three required sub-agent files (`synthesis.md`, `historian.md`, `editor.md`) is missing from `.claude/agents/`. Today, the orchestrator silently degrades: it runs whatever agents do exist and returns success. The user has no way to know they got a reduced result. With this story, the CLI checks for the expected agents before invoking the orchestrator and aborts with a clear error if any are missing.

**Why this priority**: This is the second silent-degradation failure mode caught in Spec 001 validation. Users get a degraded result without being told. The fix is small (a pre-batch directory listing) but the payoff is high (catches the most common configuration error). It ships in this spec because all three stories share the same architectural surface: checks the CLI performs before delegating to the orchestrator.

**Independent Test**: Run `insightmesh batch` with one of the agent files deleted from `.claude/agents/`. Verify the CLI prints a clear error naming the missing agent and aborts with a non-zero exit code. This test is independent of Stories 1 and 2 because it does not require an export file at all (any valid transcript path works).

**Acceptance Scenarios**:

1. **Given** all three expected agent files exist with valid frontmatter, **When** the user runs `insightmesh batch ...`, **Then** the pre-flight check passes silently and the pipeline proceeds normally
2. **Given** one of the three expected agent files is missing, **When** the user runs `insightmesh batch ...`, **Then** the CLI prints a clear error naming the missing agent and aborts before invoking the orchestrator, exiting with a non-zero code
3. **Given** two or three agent files are missing, **When** the user runs the batch command, **Then** the CLI lists all missing agents in one error rather than reporting them one at a time
4. **Given** an agent file exists but has malformed YAML frontmatter (no `name:` field or unparseable), **When** the user runs the batch command, **Then** the CLI reports which file is malformed and what it expected

---

### Edge Cases

- What happens when an export file is structurally valid JSON but has the wrong root type (e.g., an object at the root instead of an array)? Treated as unrecognized format per Story 1 acceptance scenario 4.
- What happens when a conversation in the export contains zero messages? The `list` output includes a 0 in the message-count column and remains selectable; `batch` then fails with the same empty-transcript error Spec 001 already raises.
- What happens when two conversations share the same `id` (defensive concern, should not occur in real exports)? The first occurrence is selected when `--conversation <id>` matches; both rows display in `list` output.
- What happens when `--conversation 0` is passed against an empty export? Reported as an out-of-range index error before any orchestrator work.
- What happens when the user passes a value that could be both an id and an index (e.g., `--conversation "0"` where `"0"` matches both the first index and a literal id)? The CLI resolves index first when the value parses cleanly as an integer in range; otherwise it falls back to id matching. This rule is documented in `--help`.
- What happens when `.claude/agents/` directory is itself missing or unreadable? The pre-flight check treats this as "all expected agents missing" and aborts with one consolidated error message naming all of them plus the directory-level cause.
- What happens when an agent file exists with a `name:` field that does not match the filename (e.g., file `synthesis.md` declares `name: foo`)? The pre-flight resolves expected agents by the `name:` field in frontmatter, not by filename. This case appears as the expected agent being missing.
- What happens when a long-running orchestrator invocation has already started and then a `.claude/agents/` file is deleted mid-run? Out of scope; pre-flight is a pre-batch snapshot only.
- What happens when `echomine` is not installed at all (e.g., the user forgot `uv sync` after pulling)? The CLI MUST fail at module-import time with a clear `ModuleNotFoundError` naming `echomine` and suggesting `uv sync`. This surfaces before any pre-flight pass runs.
- What happens when `echomine` is installed at a version that does not satisfy `>=1.3.0,<2.0.0`? `uv sync` refuses to complete. If the user has bypassed that and a too-old or too-new version is present, an import-time `ImportError` (for missing API symbols) or `AttributeError` (for changed signatures) surfaces — InsightMesh does not attempt to detect or mask the version mismatch.
- What happens when `batch --conversation <id>` resolves to a conversation whose canonical thread is empty (e.g., all messages are `system`/`tool` roles, or the ChatGPT tree has no `current_node`)? The CLI MUST report `error: conversation '<id>' contains no usable user/assistant messages` and exit non-zero before orchestrator invocation. This is distinct from FR-012 (the conversation does match — it just has no usable content).
- What happens when EchoMine raises `ParseError` mid-stream during `insightmesh list` after yielding some valid conversations? The CLI MUST flush the rows already collected to stdout, append `warning: listing aborted after <N> conversations: <upstream parse error>` to stderr, and exit with code 1. Partial output is preserved so users know what was processable.
- What happens when both `ClaudeAdapter` and `OpenAIAdapter` succeed in parsing the first conversation of an export (hand-crafted file with signatures of both)? Per FR-025 (Claude first), `ClaudeAdapter` wins. The CLI MUST emit `warning: export matched both Claude.ai and ChatGPT adapters; using Claude.ai` to stderr but proceed.
- What happens when the user has both a PyPI `echomine` install and a local editable install? `uv` resolves to the most specific declaration in `pyproject.toml`. Developers using a local editable install MUST pin via `[tool.uv.sources]` override and ensure the editable version still satisfies the version constraint. No CLI behavior change.
- What happens when the user sends `SIGINT` (Ctrl+C) during a long `list` or `batch` invocation? `KeyboardInterrupt` propagates through EchoMine's generator (closing it cleanly), the CLI catches at the boundary, prints `interrupted by user` to stderr, and exits with code 130. No partial wiki pages or session logs are persisted. (Note: this is distinct from the earlier mid-orchestrator-run scenario above, which addresses file-system mutations rather than user-initiated interruption.)

## Requirements *(mandatory)*

### Functional Requirements

**List subcommand (Story 1)**

- **FR-001**: System MUST provide a `list` subcommand that accepts a single multi-conversation export file path as its only positional argument and prints a tabular summary to standard output. The `list` subcommand MUST NOT accept `--vault` or any other vault-related option, because it does not read from or write to the vault.
- **FR-002**: System MUST recognize and parse the Claude.ai export schema (array of conversation objects with metadata plus a nested ordered message collection)
- **FR-003**: System MUST recognize and parse the ChatGPT export schema (array of conversation objects, using ChatGPT's field naming for id, title, creation timestamp, and message collection)
- **FR-004**: Each row of `list` output MUST display the conversation's id, a human-readable title, its creation date, and a count of messages
- **FR-005**: Output rows MUST be ordered most-recent-first by creation date by default
- **FR-006**: System MUST handle an export containing zero conversations by printing a clear empty-state message and exiting with code 0
- **FR-007**: System MUST reject any file that does not match a supported export schema (including the Spec 001 flat `{role, content}` array shape) with a clear error message naming the expected formats, and exit with a non-zero code
- **FR-008**: System MUST truncate or escape long titles and unusual characters in `list` output to preserve column alignment in a typical terminal

**Conversation selection (Story 2)**

- **FR-009**: System MUST add a `--conversation <id-or-index>` option to the `batch` command for selecting a single conversation from a multi-conversation export
- **FR-010**: System MUST accept either the conversation's id (string) or its zero-indexed position (integer matching the row order from `list` output) as the `--conversation` value. The CLI MUST use the implicit disambiguation rule documented in Edge Cases (numeric value in valid index range resolves as index; otherwise resolves as id). Explicit prefix syntax (e.g., `id:abc123`, `index:0`) is not supported and MUST NOT be parsed.
- **FR-011**: System MUST extract the selected conversation's messages and normalize them to the internal `{role, content}` representation before invoking the orchestrator, so downstream Spec 001 behavior is unchanged
- **FR-012**: When `--conversation` references a value that does not match any conversation in the export, the system MUST report a clear error naming the invalid value and exit with a non-zero code before any orchestrator invocation
- **FR-013**: When a multi-conversation export file is passed to `batch` without `--conversation`, the system MUST refuse the run with an error that explains the requirement and recommends `insightmesh list` to discover available ids
- **FR-014**: When the input file is the Spec 001 flat `{role, content}` JSON array, the system MUST continue to process it without requiring `--conversation`, preserving backward compatibility

**Pre-flight agent check (Story 3)**

- **FR-015**: Before invoking the orchestrator, the `batch` command MUST verify that every name in the expected-agent set is present and parseable in `.claude/agents/`
- **FR-016**: When one or more expected agents are missing or unparseable, the CLI MUST abort with an error that lists all problems in a single message and exit with a non-zero code, before any orchestrator invocation
- **FR-017**: When an expected agent file exists but its YAML frontmatter is missing the `name:` field or is otherwise unparseable, the CLI MUST report which file is malformed and what it expected
- **FR-018**: The pre-flight check MUST source the expected-agent set from a single `EXPECTED_AGENTS` constant exported by the orchestrator module, so the list updates in exactly one place when future specs add agents

**Cross-cutting**

- **FR-019**: All pre-flight errors (Stories 1, 2, and 3) MUST be visibly distinguishable from in-pipeline orchestrator errors, so users can tell whether the failure happened before or during agent execution. Error message prefixes MUST follow this convention across all CLI exits, enabling single-regex matching by downstream tooling:

  | Error category | Prefix | Emitted by |
  |----------------|--------|-----------|
  | Pre-flight check failure (vault, agent presence) | `error: pre-flight checks failed:` | aggregated message per FR-022 |
  | Export-handling failure (EchoMine boundary errors, conversation selection) | `error: export `, `error: cannot parse export`, or `error: conversation ` | per-error from `src/exports.py` and conversation selection logic |
  | Orchestrator runtime failure | `error: pipeline failed:` | unchanged from Spec 001 |

  All errors in the first two categories MUST be written to stderr only; they MUST NOT be persisted to the `.logs/` directory and MUST NOT extend the existing `SessionLog` schema. The "stderr only" rule applies to every CLI error raised before the orchestrator's session log opens; errors raised after orchestrator invocation continue to follow Spec 001's logging.
- **FR-020**: All new functionality MUST be exposed via the existing Typer-based CLI surface, preserving the current `insightmesh <command>` invocation style established in Spec 001
- **FR-021**: Export adapters MUST silently ignore fields in the input that are not part of the adapter's known schema. They MUST NOT warn or refuse based on extra fields. Failures are reserved for missing or unparseable *required* fields.
- **FR-022**: All pre-flight checks (Spec 001's vault validation per FR-011, this spec's agent presence check per FR-015 to FR-018, and any future pre-flight check) MUST run together, and any failures detected MUST be aggregated into a single consolidated error message reported to stderr. The CLI MUST NOT abort on the first failure and skip subsequent pre-flight checks.
- **FR-023**: System MUST delegate Claude.ai and ChatGPT export schema parsing to the `echomine` library (PyPI; `echomine>=1.3.0,<2.0.0`) via its public library API. InsightMesh MUST NOT implement, fork, or duplicate adapter logic for these schemas. When `echomine` adds support for a new export provider in a future minor version, InsightMesh inherits that support by upgrading the dependency. Compliance with this delegation MUST be enforceable via static inspection: `src/exports.py` MUST NOT import the `json` standard-library module directly for adapter-style parsing and MUST NOT import from `echomine`'s internal submodules. The implementation phase is responsible for the concrete check (a linting rule or a test that inspects `src/exports.py`'s import list).
- **FR-024**: System MUST consume only the following `echomine` public-API symbols: `ClaudeAdapter`, `OpenAIAdapter`, `Conversation`, `Message`, `ConversationProvider`, `EchomineError`, `ParseError`, `ValidationError`, `SchemaVersionError`. Imports from EchoMine's internal submodules (`echomine.adapters.*`, `echomine.models.*`, etc.) are prohibited.
- **FR-025**: When opening a multi-conversation export, the CLI MUST attempt adapters in order: `ClaudeAdapter` first, then `OpenAIAdapter`. The first adapter that parses the first conversation without raising `SchemaVersionError` is used for the remainder of the file. If both raise `SchemaVersionError`, the CLI raises `UnrecognizedExportFormat` (per FR-007).
- **FR-026**: When converting an EchoMine `Conversation` to the internal `{role, content}` representation Spec 001's `transcript.py` consumes, InsightMesh MUST: (a) walk the canonical thread via `Conversation.get_thread(Conversation.current_node)` for ChatGPT, or the linear `messages` list for Claude.ai; (b) for each `Message` whose `role` is `"user"` or `"assistant"`, emit `{"role": msg.role, "content": msg.content}`; (c) skip messages with other roles (`"system"`, `"tool"`, `"function"`, and any future roles), consistent with `transcript.py`'s normalization.
- **FR-027** *(Error translation contract)*: When an `echomine.EchomineError` (or subclass) surfaces at the integration boundary in `src/exports.py`, InsightMesh MUST translate per this table and chain the original cause via `raise ... from echomine_exc`:

  | EchoMine exception | InsightMesh action | User-facing message format |
  |--------------------|--------------------|----------------------------|
  | `SchemaVersionError` (on first conversation) | raise `UnrecognizedExportFormat` | `error: not a recognized export format: <path> (tried <adapters>); expected a multi-conversation export from Claude.ai or ChatGPT` |
  | `ParseError` | translate to a CLI error | `error: cannot parse export file <path>: <upstream message verbatim>` |
  | `ValidationError` | translate to a CLI error | `error: invalid conversation data in <path>: <upstream message verbatim>` |
  | Unrecognized `EchomineError` subclass | re-raise unchanged | original Python traceback (treated as unexpected upstream error) |

  EchoMine's exception messages MUST be propagated verbatim — no summarization or truncation — so diagnostic detail (JSON line numbers, conversation ids) survives translation.
- **FR-028**: When EchoMine's `stream_conversations` invokes an `on_skip` callback for a malformed conversation mid-stream, InsightMesh MUST: (a) in `list`, omit the skipped conversation from the output table and append a stderr line of the form `warning: skipped conversation <id-or-position>: <reason>`; (b) in `batch`, if the skipped conversation matches the user's `--conversation` value, treat as a no-match error (FR-012); otherwise emit the same stderr warning and continue. Skip warnings MUST NOT change the exit code on their own — they are informational, not errors.

### Key Entities

- **Export File**: A user-downloaded JSON file from Claude.ai or ChatGPT containing many conversations. Root is an array of conversation objects.
- **Conversation**: One element of an export file. Has its own id, title, creation timestamp, and ordered collection of messages.
- **Conversation Listing Row**: One row of `insightmesh list` output. Carries the four display fields (id, title, created date, message count) for a single conversation.
- **Expected-Agent Set**: The collection of sub-agent names the orchestrator depends on. Declared once in the orchestrator module as the `EXPECTED_AGENTS` constant.
- **Pre-flight Result**: The outcome of CLI validation that runs before the orchestrator is invoked. Either passes silently or fails with a typed, named error.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user who has downloaded an unmodified Claude.ai or ChatGPT data export can identify and synthesize a single chosen conversation without writing any external script (no `jq`, no `awk`, no custom Python)
- **SC-002**: A synthetic 5,000-conversation Claude.ai export fixture processed by `insightmesh list` MUST complete the table print within 5 seconds on a developer-class machine (8GB RAM, SSD, single-core measurement). EchoMine's streaming parser is the binding constraint. If the budget cannot be met under reasonable optimization, the budget is revised in a follow-up spec patch — InsightMesh MUST NOT implement a parallel parser to meet it.
- **SC-003**: When any required sub-agent file is missing or unparseable, the `batch` command aborts with a clear error in under 1 second of invocation, naming every affected agent in one message
- **SC-004**: All existing Spec 001 flat-array transcripts continue to work without supplying any new flag (zero regression on existing input format)
- **SC-005**: A new user can complete the full `list → select → batch` workflow on their own real export by following only the `docs/getting-started.md` page, without reading source code or opening an issue

## Assumptions

- Spec 001's existing pipeline, agent definitions, MCPVault wiring, and CLI surface remain in place; this spec extends them and does not replace them
- Users have already exported their data from Claude.ai or ChatGPT through the official UI; this spec does not orchestrate the export step itself
- EchoMine streams the export file with O(1) memory complexity per conversation (via `ijson`). InsightMesh's `list` collects projections into an in-memory list of `InsightMeshSummary` records — bounded by the number of conversations, not the total export size (~150 bytes per conversation). Exports with up to ~10,000 conversations comfortably fit InsightMesh's projection on a developer-class machine. Larger exports stream through EchoMine but accumulate the projection list in memory; if that becomes a bottleneck, paging is its own spec patch.
- Conversation ids in the supported export formats are non-numeric (UUID-like) strings, so the disambiguation rule for `--conversation` (numeric-in-range resolves as index; otherwise as id) does not produce ambiguity in practice
- The `echomine` library (PyPI `echomine>=1.3.0`) is the source of truth for Claude.ai and ChatGPT export schema parsing. InsightMesh trusts EchoMine's adapters to handle schema drift, encoding edge cases, and message-tree flattening (ChatGPT's `mapping` + `current_node` thread walk is provided by EchoMine's `Conversation.get_thread()` helper). InsightMesh's role is limited to converting EchoMine's `Conversation` model into the internal `{role, content}` shape Spec 001's pipeline already expects. Breaking schema changes upstream are addressed by upstream EchoMine releases, not by InsightMesh patches
- The `EXPECTED_AGENTS` constant lives in the same Python module that owns orchestrator coordination, so the new pre-flight check can import it without introducing circular dependencies
- Pre-flight validation runs synchronously before orchestrator invocation and adds negligible latency relative to the orchestrator's runtime cost
- Users who deliberately want to run a reduced agent set will modify or fork the `EXPECTED_AGENTS` constant; this is acceptable for Phase A and removes the need for an opt-out flag
- The `docs/getting-started.md` page from Spec 001 will be updated as part of this spec's implementation to reflect the new `list → select → batch` workflow
- **EchoMine PyPI availability**: EchoMine is available on PyPI under the package name `echomine` (`https://pypi.org/project/echomine/`). The standard `uv add echomine>=1.3.0,<2.0.0` install path applies; no git URL, editable path, or private index is required.
- **EchoMine schema-drift definition**: The Claude.ai and ChatGPT export schemas evolve in two ways. **Non-breaking schema additions** — new fields appearing alongside existing ones, without removing or renaming any field InsightMesh or EchoMine read — are tolerated silently (FR-021). **Breaking schema changes** — renamed, removed, or restructured required fields — surface as `echomine.SchemaVersionError` and are addressed by upgrading EchoMine, not by patches inside InsightMesh.
- **EchoMine silent-ignore verification**: EchoMine's adapters silently ignore unknown fields in source exports by default. This assumption MUST be verified during implementation by inspecting EchoMine's `Conversation` and `Message` model configurations and ratified via a test fixture containing extra fields.
- **EchoMine bug-handling policy**: When EchoMine bugs surface during InsightMesh integration, the resolution path is to (a) file an upstream issue against the `echomine` repository, (b) pin around the broken version in `pyproject.toml` if blocking, and (c) avoid working around the bug inside InsightMesh. Forking or patching EchoMine inside InsightMesh is explicitly disallowed (consistent with FR-023).
- **EchoMine transitive deps**: EchoMine's transitive dependencies (`ijson`, `structlog`, `python-slugify`, `python-dateutil`) are compatible with InsightMesh's existing tree at `echomine>=1.3.0`. Compatibility MUST be re-verified on any EchoMine version bump.
- **EchoMine major-version policy**: InsightMesh does not handle transient compatibility windows between EchoMine major versions. The pin `>=1.3.0,<2.0.0` defines the supported range; EchoMine 2.x adoption is its own spec patch.

## Non-Goals

The following are explicitly out of scope for this spec:

- **Conversation filtering or search within an export** (e.g., `insightmesh list --since 2025-01-01` or `--containing "topic"`). Listing is unfiltered in Spec 002; filtering can be added in a future spec only when users demonstrate need.
- **Batch-processing multiple conversations from one export in a single run.** This spec processes exactly one conversation per `batch` invocation. Multi-conversation batch (and the cross-conversation cross-linking question it raises) is its own design problem and belongs in a later spec.
- **Export-format support beyond what `echomine` provides.** Today that covers Claude.ai and ChatGPT exports. New providers added in future `echomine` versions become available to InsightMesh by upgrading the dependency. Independent adapter implementations for additional export formats inside InsightMesh are out of scope; if a needed provider is missing, the right path is to contribute it upstream to `echomine`. Users with unsupported formats can still use the original Spec 001 flat `{role, content}` path.
- **Conversation editing or pre-processing.** This spec does not provide ways to redact, trim, merge, or transform conversations before synthesis. Users who need that can pre-process with their own tools.
- **Checkpointed or resumable batch processing for long conversations.** Wiki-as-carry-over checkpointing is its own spec (planned next in the roadmap), even though it shares the "real-data usage" motivation with this one.
- **Live conversation streaming from Claude.ai or ChatGPT APIs.** This spec is still batch-only on already-exported files.
- **Opt-out flag for the pre-flight agent check** (e.g., `--allow-missing-agents` or similar). Abort on missing agent is the only behavior; no escape hatch is introduced. If a future use case demands running with a reduced agent set, that capability can be added through a separate, deliberately scoped spec.
- **Agent dependency resolution or auto-install.** If an agent is missing, the pre-flight check reports it; it does not attempt to fetch, install, or restore agent files.
- **Agent schema validation beyond presence and basic frontmatter parseability.** The pre-flight check does not verify that the agent's prompt is correct, that its tools are available, or that its `skills` or `mcpServers` references resolve. Those failures surface at orchestrator runtime as they do today.
- **Configurable expected-agent set via a config file or YAML manifest.** Spec 002 sources the expected agent list from a single `EXPECTED_AGENTS` constant exported by the orchestrator module, consumed by both the orchestrator and the pre-flight check. When future specs add agents (Critic, Researcher, etc.), the constant is updated in one place. A config-file mechanism is explicitly not introduced.
- **Telemetry or usage tracking** of how often each pre-flight check catches a real issue. Useful for product evolution but premature.
- **GUI or web interface** for browsing conversations. CLI only.
- **Performance optimization** of the `list` subcommand for very large exports (10,000 plus conversations). Assume exports fit reasonably in memory.
