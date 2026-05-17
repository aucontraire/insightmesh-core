---
description: "Task list for Chat-to-Wiki Batch Synthesis (Spec 001)"
---

# Tasks: Chat-to-Wiki Batch Synthesis

**Input**: Design documents from `/specs/001-chat-to-wiki-batch/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md, constitution.md (v1.1.0)

**Tests**: Included per plan.md ("Tests before implementation where practical").

**Organization**: Tasks are grouped by user story (US1, US2). US1 is the MVP — pipeline that produces wiki pages from a transcript. US2 adds the JSON session log instrumentation on top.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2)
- **[MANUAL STOP]**: Task requires user action; implementation MUST halt and wait for confirmation
- File paths are absolute or relative to repository root

## Path Conventions

Single project — `src/`, `tests/`, `.claude/agents/` at repository root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency configuration, fixture creation

- [X] T001 Create project directory structure: `src/`, `tests/`, `tests/fixtures/`, `.claude/agents/`, `.specify/scratch/agent_prompts/`
- [X] T002 Create `pyproject.toml` with:
  - **Runtime deps**: `claude-agent-sdk`, `pydantic>=2.0`
  - **Dev deps**: `pytest`, `mypy`, `ruff`, `black`
  - **Tool configs**: `[tool.mypy]` strict=true, `[tool.ruff]` (sane defaults), `[tool.black]` (line-length=100), `[tool.pytest.ini_options]` (testpaths, asyncio_mode)
  - Pre-justified by constitution v1.1.0 §Project Standards — no Complexity Justification entry needed
- [X] T003 Create `.mcp.json` at repository root with the MCPVault server entry: `{"mcpServers": {"mcpvault": {"command": "npx", "args": ["-y", "@bitbonsai/mcpvault@latest", "${VAULT_PATH}"]}}}`. The `${VAULT_PATH}` is interpolated from the `VAULT_PATH` env var, which the CLI (T015) sets from the `--vault` flag before invoking the orchestrator. Each agent's `.md` file references this server by name (`mcpServers: [mcpvault]`) — no inline config needed.
- [X] T004 [P] Create fixture `tests/fixtures/single_topic.json`: 20-exchange JSON about one topic (e.g., speed of light), matching ChatGPT/Claude export format `[{"role": "user", "content": "..."}, ...]`. Tests SC-001 timing + basic synthesis.
- [X] T005 [P] Create fixture `tests/fixtures/multi_topic.json`: ~30-exchange JSON covering 3 distinct topics (e.g., light → optics → photography). Tests FR-003 separate-page creation and SC-002 cross-links between topics.
- [X] T006 [P] Create fixture `tests/fixtures/revisit.json`: Conversation where exchanges 1-10 cover topic X at surface level, exchanges 11-20 revisit X with new depth. Tests FR-007 update-existing-page behavior and US1 acceptance scenario 2.
- [X] T007 [P] Create fixture `tests/fixtures/malformed.json`: Intentionally broken JSON (truncated, invalid structure). Tests FR-012 error handling.
- [X] T008 [P] Create fixture `tests/fixtures/empty.json`: Valid JSON, empty array `[]`. Tests FR-012 edge case.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: This project has no shared infrastructure beyond setup. Pydantic models live in their natural modules (per anti-slop: no speculative `models/` package).

*No foundational tasks. Proceed directly to user stories.*

**Checkpoint**: Ready for user story implementation.

---

## Phase 3: User Story 1 - Chat-to-Wiki Batch Synthesis (Priority: P1) 🎯 MVP

**Goal**: Feed a JSON chat transcript into the system, and have it produce organized, cross-linked Obsidian wiki pages in the vault using the Synthesis → Historian → Editor sub-agent pipeline.

**Independent Test**: Run `python -m src.cli batch tests/fixtures/single_topic.json --vault /tmp/test-vault`. Verify wiki pages appear in `/tmp/test-vault/InsightMesh/` with synthesized content, frontmatter, and `[[wiki links]]` between related pages.

### Agent Creation (MANUAL via `/agents` wizard)

- [X] T009 [MANUAL STOP] [US1] Create all three sub-agents via Claude Code's `/agents` wizard. Implementation MUST halt at the STOP step and wait for user confirmation.

  **Step 1 — Pre-flight (automated)**:
  1. Run `claude mcp list` and verify `mcpvault` appears in the output.
  2. If `mcpvault` is NOT listed, validate `.mcp.json` syntax (from T003) and warm the npx cache by running `npx -y @bitbonsai/mcpvault@latest --help` once.
  3. If `mcpvault` still does not appear, STOP and surface the issue to the user before proceeding to Step 2.

  **Step 2 — Compose prompt bodies (automated)**:
  For each agent below, generate the system prompt body and write it to `.specify/scratch/agent_prompts/<name>.txt`. The prompt body must:
  - Reflect the agent's responsibilities, inputs, and outputs per spec.md §Agent Contracts
  - Reference the typed output schemas from data-model.md (`SynthesisOutput`, `HistorianOutput`, `EditorOutput`)
  - For the Editor: encode the FR-007 three-signal create-vs-update rule + FR-014 decision logging requirement
  - Reference kepano/obsidian-skills `obsidian-markdown` skill knowledge (preloaded via the `skills` frontmatter field) — agents should rely on this for wikilink, frontmatter, and tag syntax rather than re-deriving it

  **Step 3 — Present wizard input set (display to user)**:
  Show the user this table per agent, plus the location of the prompt body file:

  | Field | Synthesis | Historian | Editor |
  |-------|-----------|-----------|--------|
  | `name` | `synthesis` | `historian` | `editor` |
  | `description` | (from spec §Agent Contracts) | (from spec §Agent Contracts) | (from spec §Agent Contracts) |
  | `model` | `sonnet` | `sonnet` | `sonnet` |
  | `tools` | `Read` | `Read, Grep, Glob` | `Read, Write, Edit` |
  | `skills` | `obsidian-markdown` | `obsidian-markdown` | `obsidian-markdown` |
  | `mcpServers` | (none) | `mcpvault` | `mcpvault` |
  | `memory` | (none — default) | (none — default) | (none — default) |
  | `permissionMode` | (default) | (default) | (default) |
  | Prompt body | `.specify/scratch/agent_prompts/synthesis.txt` | `.specify/scratch/agent_prompts/historian.txt` | `.specify/scratch/agent_prompts/editor.txt` |

  **Step 4 — STOP and WAIT FOR USER**:
  Display these exact instructions to the user and halt:
  1. Open Claude Code interactively (`claude`)
  2. Run the `/agents` slash command
  3. Choose "Create new agent" and enter the values from the table above for **synthesis**, pasting the prompt body from `synthesis.txt`
  4. Repeat for **historian** and **editor**
  5. Confirm to the implementation when all three agents are created

  **Step 5 — Verify (automated, after user confirms)**:
  - Check `.claude/agents/synthesis.md`, `.claude/agents/historian.md`, `.claude/agents/editor.md` all exist
  - Parse YAML frontmatter and confirm required fields per the table (`name`, `description`, `model`, `tools`, `skills`, and `mcpServers` where expected)
  - Report any missing or unexpected fields to the user
  - DO NOT auto-correct — surface discrepancies and ask the user to re-edit via the wizard

### Tests for User Story 1

- [X] T010 [P] [US1] Write tests for transcript parser in `tests/test_transcript.py`: valid JSON parsing into Pydantic models, malformed JSON rejection (`ValidationError`), empty transcript rejection, role normalization, exchange indexing. Strict typing throughout.
- [X] T011 [P] [US1] Write tests for wiki models in `tests/test_wiki.py`: Pydantic `WikiPage`/`WikiPageDraft`/`WikiPageResult` round-trip (model_dump_json → model_validate_json), frontmatter YAML generation, title normalization helper (for FR-007 signal a), filename sanitization.

### Implementation for User Story 1

- [X] T012 [US1] Implement `src/transcript.py`: `Message`, `Exchange`, and `ChatTranscript` Pydantic v2 `BaseModel` classes + `parse_transcript(path: Path) -> ChatTranscript` function. Parser flow: (1) load JSON, (2) validate each message into `Message` (normalizing unknown roles to "assistant"), (3) pair consecutive user→assistant messages into `Exchange` objects per data-model.md §Pairing Rules (handle leading orphan assistants, consecutive same-role messages, trailing unanswered user). Raise clear errors for empty/malformed input (FR-001, FR-012).
- [X] T013 [US1] Implement `src/wiki.py`: Pydantic v2 `BaseModel` classes for `WikiPage`, `WikiPageDraft`, `WikiPageResult` + pure functions for `normalize_title()` (FR-007 signal a) and `sanitize_filename()`. Note: actual file I/O happens via MCPVault from inside agents — this module only defines the data shapes and a few pure helpers. (FR-004, FR-005, FR-006 are realized by the agents using these models + MCPVault.)
- [X] T014 [US1] Implement `src/orchestrator.py`: async pipeline function using `claude_agent_sdk.query()`. The SDK auto-discovers `.claude/agents/*.md` (agent definitions) and `.mcp.json` (MCPVault config) — no programmatic `AgentDefinition` construction needed. Per plan.md Decision 7. Pattern: `async for message in query(prompt="Use the synthesis agent... then historian... then editor...", options=ClaudeAgentOptions(allowed_tools=["Agent", "Read", "Write", "Edit"]))`. Capture per-agent outputs by matching `tool_use` blocks where `name in ("Task", "Agent")` and tracking `parent_tool_use_id` for sub-agent message attribution. Return final `EditorOutput`.
- [X] T015 [US1] Implement `src/cli.py`: Typer entry point for `batch <transcript> --vault <path> [--logs <path>]` (per constitution v1.1.3). Validates vault exists and is writable (FR-011). Sets `VAULT_PATH` env var from `--vault` flag (consumed by `.mcp.json` MCPVault config — see T003). Loads transcript via `src.transcript.parse_transcript`. Runs orchestrator via `asyncio.run(...)`. Prints summary.
- [X] T016 [US1] Add agent failure handling in `src/orchestrator.py` per FR-013: Synthesis failure aborts batch, Historian failure proceeds without cross-links, Editor failure on a single page skips that page and continues. Treat LLM rate-limit responses (429 / SDK rate_limit error) as recoverable agent failures — apply the same Historian/Editor fallback paths.

**Checkpoint**: At this point, US1 is fully functional. Running the CLI on `single_topic.json` produces wiki pages in the vault. No session log yet — that's US2.

---

## Phase 4: User Story 2 - Inquiry Session Logging (Priority: P1)

**Goal**: Every batch run produces a structured JSON session log capturing per-agent outputs, wiki pages affected, Editor decision reasoning, and any failures.

**Independent Test**: After completing US1, run a batch synthesis. Verify a timestamped JSON file appears in the logs directory with sections for `synthesis`, `historian`, `editor` outputs (including per-decision reasoning), plus `wiki_pages_created`, `wiki_pages_updated`, `status`, and `duration_seconds`. Validate the file parses as JSON.

### Tests for User Story 2

- [X] T017 [P] [US2] Write tests for session logger in `tests/test_logger.py`: log file written with correct Pydantic schema, timestamps formatted as ISO 8601, per-agent sections present, `EditorDecision` reasoning captured per FR-014, partial-failure log captures error details, multiple sessions produce separate files.

### Implementation for User Story 2

- [X] T018 [US2] Implement `src/logger.py`: Pydantic v2 `BaseModel` classes for `SessionLog`, `AgentOutput`, `SynthesisOutput`, `HistorianOutput`, `EditorOutput`, `EditorDecision`, `EditorDecisionSignals`, `CrossLinkRecord`, `SessionError` per data-model.md + `write_session_log(log: SessionLog, logs_dir: Path) -> Path` function using `SessionLog.model_dump_json(indent=2)` for serialization to timestamped JSON file.
- [X] T019 [US2] Integrate logger into `src/orchestrator.py`: as the `query()` message stream is consumed, build a `SessionLog` from the per-agent attribution already captured in T014 (via `Agent`/`Task` tool_use blocks and `parent_tool_use_id`). Each completed sub-agent span produces an `AgentOutput` entry (input prompt, typed output, duration measured between Agent tool_use start and corresponding tool_result, status). Call `logger.write_session_log()` after orchestrator returns (FR-008, FR-009).
- [X] T020 [US2] Extend orchestrator failure handling (T016) to populate `SessionLog.errors` and `SessionLog.status` for partial failures per FR-010 and FR-013. Capture `EditorDecision` objects from the Editor agent's output per FR-014. Ensure log is written even when batch fails partway.

**Checkpoint**: Both US1 and US2 functional. Pipeline produces wiki pages AND a per-session JSON log with full reasoning.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Validation, cleanup, constitution compliance

- [X] T021 [P] Run `quickstart.md` validation: execute the workflow against each P1-critical fixture (`single_topic.json`, `multi_topic.json`, `revisit.json`), verify all expected artifacts appear (wiki pages with frontmatter, cross-links, session log with `EditorDecision` reasoning), AND verify `SessionLog.duration_seconds < 120` per SC-001. Manual acceptance: read each generated wiki page and confirm it reads as synthesized narrative, not a verbatim dump of the source exchanges (FR-002 is LLM-judged and requires human review at this stage). **Result: All 3 fixtures pass except SC-001 timing (260-294s vs 120s target). Documented as known limitation for Spec 002+ optimization.**
- [X] T022 [P] Constitution compliance audit (v1.1.3): count source files (target ≤ 18 per plan.md structure decision), verify all runtime deps are within constitution §Project Standards (Pydantic v2, claude-agent-sdk, Typer) — no Complexity Justification entries needed, confirm no speculative abstractions, confirm mypy strict passes with no `Any` in public APIs. **Result: 20 files (2 over budget due to __init__.py — plan.md docs gap, not real violation). Deps clean. No dataclass/NamedTuple usage (TID251 rule confirms). No speculative abstractions. mypy strict clean. 84 tests pass. Borderline: normalize_title/sanitize_filename are exposed but only called by tests — left in place as documented FR-007 reference impl.**
- [X] T023 Naming consistency pass across spec/data-model/plan/code: lowercase `synthesis`/`historian`/`editor` for code, JSON keys, and `AgentDefinition` keys; capitalized in prose per spec.md §Agent Contracts. **Result: Fully consistent. Agent files lowercase. Python class names PascalCase per language convention. All lowercase usages in spec docs are inside code/JSON contexts (backticks, f-strings, JSON examples). All prose uses capitalized.**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Empty for this project
- **User Story 1 (Phase 3)**: Depends on Setup; T009 (manual stop) blocks T012/T014 (orchestrator needs the agents)
- **User Story 2 (Phase 4)**: Depends on US1 completion (logger wraps the orchestrator built in US1)
- **Polish (Phase 5)**: Depends on US1 and US2 completion

### User Story Dependencies

- **US1 (Chat-to-Wiki Batch Synthesis)**: The MVP. Standalone — produces wiki pages without logs.
- **US2 (Inquiry Session Logging)**: Layers on US1. Requires US1's orchestrator to exist so logger can wrap agent calls. Documented in spec.md §US2 "Relationship to User Story 1".

### Within Each User Story

- T009 (agent creation) is a manual user-driven step — implementation halts here
- T010 and T011 (tests) can run in parallel with each other and BEFORE T009 (tests don't depend on agents existing)
- Implementation tasks run in module order: `transcript.py` → `wiki.py` → `orchestrator.py` → `cli.py`
- Models live inside their natural module (no separate models layer)

### Parallel Opportunities

- **Phase 1**: T004–T008 (5 fixtures) all in parallel
- **US1 tests**: T010 and T011 in parallel
- **US1 implementation**: T012 and T013 can run in parallel; T014 depends on both + T009 verification; T015 depends on T014
- **Phase 5**: T021 and T022 in parallel

---

## Parallel Example: Fixtures (Phase 1)

```bash
# Launch all five fixtures together:
Task: "Create tests/fixtures/single_topic.json"
Task: "Create tests/fixtures/multi_topic.json"
Task: "Create tests/fixtures/revisit.json"
Task: "Create tests/fixtures/malformed.json"
Task: "Create tests/fixtures/empty.json"
```

## Parallel Example: User Story 1 Tests

```bash
# Launch transcript and wiki tests together (different test files):
Task: "Write tests for transcript parser in tests/test_transcript.py"
Task: "Write tests for wiki models in tests/test_wiki.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T008)
2. Run Phase 3 tests first (T010, T011) — they don't depend on agents
3. Execute T009: implementation composes prompts + presents wizard inputs, then STOPS for manual agent creation. User creates the three agents via `/agents`. Implementation verifies and continues.
4. Complete Phase 3 implementation (T012 → T016)
5. **STOP and VALIDATE**: Run the CLI against `single_topic.json`. Verify wiki pages appear in the vault with proper synthesis, frontmatter, and cross-links.
6. Demo if ready — you have the core value proposition working without logs.

### Incremental Delivery

1. Setup → ready
2. US1 → MVP demo (wiki pages from transcripts)
3. US2 → instrumented MVP (wiki pages + per-session JSON logs with Editor reasoning)
4. Polish → constitution compliance, end-to-end validation across all fixtures

### Anti-Slop Discipline

- **File budget**: Target ~18 files (3 agents + 5 source + 3 tests + 5 fixtures + 2 config). Re-validate at T022.
- **No premature abstraction**: Pydantic models live in their natural module. No `models/` package.
- **Dependencies**: All runtime deps within constitution v1.1.0 §Project Standards (Pydantic v2, claude-agent-sdk). No new deps needed.
- **Rule of Three**: If the same pattern appears across 3 agents or 3 modules, extract. Otherwise, leave duplicated.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- [MANUAL STOP] tasks pause implementation for user action — do not auto-bypass
- Tests should fail before implementation (write tests first, then make them pass)
- Commit after each task or logical group
- Stop at the US1 checkpoint to validate the MVP independently before starting US2
- Sub-agent invocation: orchestrator.py uses `claude-agent-sdk` Python package (see plan.md Decision 7)
- Sub-agent creation uses Claude Code's `/agents` wizard, NOT direct file writes — the wizard knows the current field schema and validates input
- All data models are Pydantic v2 `BaseModel` subclasses (per constitution v1.1.0 §Project Standards)
- Obsidian markdown knowledge (wikilinks, frontmatter, tags) comes from kepano/obsidian-skills `obsidian-markdown` skill preloaded into each agent
- Vault I/O (read/write/search) happens from agents via MCPVault MCP server — not from Python
