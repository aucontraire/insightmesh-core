# Tasks: Pre-flight Validation

**Input**: Design documents from `/specs/002-pre-flight-validation/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/cli-commands.md`, `quickstart.md`

**Tests**: Spec 001 established a tests-first (TDD) workflow with 84 passing tests at ship; Spec 002 continues that convention. Test tasks are included throughout and written *before* the implementation that makes them pass.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and validated independently as an incremental delivery.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- File paths are exact

## Path Conventions

Single Python project: `src/`, `tests/` at repository root (Spec 001 layout, continued).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new direct dependencies and confirm baseline tooling still passes before any code changes.

- [x] T001 Add three new runtime dependencies to `pyproject.toml` under `[project] dependencies`: `echomine>=1.3.0,<2.0.0`, `pyyaml>=6.0`, `rich>=15.0` (per `research.md` R1, R4, R7)
- [x] T002 Run `uv sync --all-extras` to install the new dependencies and update `uv.lock`
- [x] T003 Run baseline checks (`uv run mypy --strict src/`, `uv run ruff check src/ tests/`, `uv run pytest`) and confirm all 84 Spec 001 tests still pass before any code changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared types, fixtures, and the `EXPECTED_AGENTS` constant that all three user stories depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Add `EXPECTED_AGENTS: list[str] = ["synthesis", "historian", "editor"]` constant to `src/orchestrator.py` as a module-level export, with a comment referencing FR-018 (single source of truth for the pre-flight check)
- [x] T005 Create `src/exports.py` module with module docstring referencing `research.md` R7 and an empty `__all__` list to be filled as types and helpers are added
- [x] T006 [P] Create test fixture `tests/fixtures/claude_ai_export.json` — 3 conversations in Claude.ai shape (`uuid`, `name`, `created_at`, `chat_messages` with mixed `text` and `content`-block message shapes per `research.md` R2). Conversation #1 MUST include two extra unrecognized top-level fields (e.g., `future_metadata`, `experimental_tag`) and one extra unrecognized message-level field, so the FR-021 silent-ignore behavior can be verified by test (see T011 test list)
- [x] T007 [P] Create test fixture `tests/fixtures/chatgpt_export.json` — 3 conversations in ChatGPT shape (`id`, `title`, `create_time` Unix timestamp, `mapping` tree with `current_node`, including one branched-edit conversation to verify canonical-thread walking per `research.md` R3)
- [x] T008 [P] Define `InsightMeshSummary` Pydantic v2 model in `src/exports.py` per `data-model.md` § InsightMeshSummary (`id: str`, `title: str`, `created: datetime`, `message_count: int`, `frozen=True`, strict)
- [x] T009 [P] Define `UnrecognizedExportFormat` exception in `src/exports.py` per `data-model.md` § UnrecognizedExportFormat with the message format from FR-027 (`not a recognized export format: <path> (tried <adapters>); expected a multi-conversation export from Claude.ai or ChatGPT`)
- [x] T010 Define `AgentDefinition`, `PreflightDiagnostic`, and `MalformedAgent` in `src/cli.py` as Pydantic v2 `BaseModel` subclasses with `ConfigDict(strict=True)`, plus `PreflightError` as a plain `Exception` subclass carrying a `PreflightDiagnostic` payload — all per `data-model.md`.

**Checkpoint**: Foundation ready — user story implementation can begin in parallel.

---

## Phase 3: User Story 1 — List Conversations in a Multi-Conversation Export (Priority: P1) 🎯 MVP candidate

**Goal**: A user runs `insightmesh list <export.json>` against a Claude.ai or ChatGPT export and gets a most-recent-first table of conversations with id, title, created, message_count, plus an id-by-index footer.

**Independent Test**: Run `insightmesh list tests/fixtures/claude_ai_export.json` and `insightmesh list tests/fixtures/chatgpt_export.json`. Verify output matches the golden files in `tests/fixtures/expected/` and exit code is 0. Reproducible from `spec.md` § User Story 1 Independent Test.

### Tests for User Story 1 (TDD: write before implementation)

- [x] T011 [US1] Write adapter-detection and listing tests in `tests/test_exports.py`: `test_detect_claude_ai_export_format`, `test_detect_chatgpt_export_format`, `test_unrecognized_format_raises_UnrecognizedExportFormat` (against Spec 001 flat array, against malformed JSON, against non-array root), `test_list_conversations_orders_most_recent_first`, `test_list_conversations_returns_empty_for_zero_conversation_export`, `test_both_adapters_match_warns_and_uses_claude` (per FR-025), `test_adapter_silently_ignores_unknown_fields` (per FR-021 and the silent-ignore verification assumption — exercises the extra fields added to T006's fixture)
- [x] T012 [P] [US1] Write `list` CLI tests in `tests/test_cli.py`: `test_insightmesh_list_renders_table_for_claude_ai`, `test_insightmesh_list_renders_table_for_chatgpt`, `test_insightmesh_list_zero_conversations_exits_zero_with_message` (per FR-006), `test_insightmesh_list_unrecognized_format_exits_one` (per FR-007), `test_insightmesh_list_does_not_accept_vault_flag` (per FR-001), `test_insightmesh_list_truncates_long_titles_preserving_columns` (per FR-008)
- [x] T013 [P] [US1] Create golden output file `tests/fixtures/expected/claude_ai_list.txt` containing the expected stdout of `insightmesh list tests/fixtures/claude_ai_export.json` (header + 3 rows + id footer)
- [x] T014 [P] [US1] Create golden output file `tests/fixtures/expected/chatgpt_list.txt` containing the expected stdout of `insightmesh list tests/fixtures/chatgpt_export.json`

### Implementation for User Story 1

- [x] T015 [US1] In `src/exports.py`, implement EchoMine exception translation helpers: catch `echomine.SchemaVersionError` and translate to `UnrecognizedExportFormat` with original cause chained via `raise ... from echomine_exc` (per FR-027). Helper function `_translate_echomine_error(exc: echomine.EchomineError, path: Path) -> Exception`. All `echomine` imports in this module MUST be limited to the public-API symbols enumerated in FR-024; no imports from `echomine.adapters.*` or `echomine.models.*` internal submodules.
- [x] T016 [US1] In `src/exports.py`, implement `detect_adapter(path: Path) -> echomine.ConversationProvider` per FR-025: instantiate `ClaudeAdapter` first; attempt to parse the first conversation; on `SchemaVersionError`, try `OpenAIAdapter`; on second `SchemaVersionError`, raise `UnrecognizedExportFormat([..., "ClaudeAdapter", "OpenAIAdapter", ...])`. Emit stderr warning `warning: export matched both Claude.ai and ChatGPT adapters; using Claude.ai` if both succeed.
- [x] T017 [US1] In `src/exports.py`, implement `list_conversations(path: Path) -> list[InsightMeshSummary]` per FR-005: detect adapter, call `adapter.stream_conversations(path, on_skip=...)`, project each `echomine.Conversation` to an `InsightMeshSummary`, accumulate, sort by `created` descending, return. Wire `on_skip` callback to emit `warning: skipped conversation <id-or-position>: <reason>` to stderr per FR-028.
- [x] T018 [US1] In `src/exports.py`, implement `render_list_table(summaries: list[InsightMeshSummary]) -> str` using `rich.table.Table`: columns Index / Title / Created / Msgs, ordered as input, with `overflow="ellipsis"` truncation for titles per FR-008. Followed by id-by-index footer per `contracts/cli-commands.md`.
- [x] T019 [US1] In `src/cli.py`, add a `list` Typer subcommand: single positional argument `export: Path` (must exist), NO `--vault` flag (per FR-001), prints `render_list_table(list_conversations(export))` to stdout. On empty result, print `No conversations in export.` and exit 0 (per FR-006).
- [x] T020 [US1] In `src/cli.py` `list` handler: catch `UnrecognizedExportFormat` and `echomine.ParseError`; write the FR-007 / FR-027 error message to stderr; exit 1.
- [x] T021 [US1] Wire ParseError mid-stream behavior per the Edge Case: if EchoMine raises `ParseError` after some rows have been collected, flush the already-collected rows to stdout, append `warning: listing aborted after <N> conversations: <upstream parse error>` to stderr, exit 1.
- [x] T022 [US1] Run `uv run pytest tests/test_exports.py tests/test_cli.py -k list` and iterate until all US1 tests pass. Update golden files if intentional rendering changes occur.

**Checkpoint**: User Story 1 fully functional. `insightmesh list` works end-to-end on real Claude.ai or ChatGPT exports. SC-001 partially satisfied (user can see conversations); SC-002 measurable on a 5,000-conversation synthetic fixture if one is generated.

---

## Phase 4: User Story 2 — Process a Selected Conversation from an Export (Priority: P1)

**Goal**: A user runs `insightmesh batch <export.json> --conversation <id-or-index> --vault <path>` and the orchestrator processes exactly that conversation as Spec 001 would have processed a manually-extracted transcript. Spec 001 flat-array inputs continue to work unchanged.

**Independent Test**: Run `insightmesh batch tests/fixtures/claude_ai_export.json --conversation 0 --vault <tmp>` and `--conversation <id-of-first>` — both produce identical wiki output. Run `insightmesh batch tests/fixtures/single_topic.json --vault <tmp>` (Spec 001 fixture) — zero regression. Reproducible from `spec.md` § User Story 2 Independent Test.

### Tests for User Story 2 (TDD)

- [x] T023 [US2] Write extraction/resolution tests in `tests/test_exports.py`: `test_extract_conversation_by_id` (Claude.ai), `test_extract_conversation_by_index` (ChatGPT, exercises tree-walk via `get_thread()`), `test_extract_conversation_skips_system_and_tool_roles` (per FR-026 (c)), `test_extract_conversation_normalizes_to_role_content_shape` (per FR-026 (b)), `test_extract_conversation_empty_canonical_thread_raises` (per Edge Case), `test_resolve_conversation_value_numeric_in_range_is_index` (per FR-010), `test_resolve_conversation_value_non_numeric_is_id` (per FR-010), `test_resolve_conversation_value_no_match_raises` (per FR-012)
- [x] T024 [P] [US2] Write `batch --conversation` CLI tests in `tests/test_cli.py`: `test_batch_with_export_and_conversation_by_id_runs_pipeline` (mocks orchestrator), `test_batch_with_export_and_conversation_by_index_runs_pipeline`, `test_batch_with_export_without_conversation_flag_errors_with_list_suggestion` (per FR-013), `test_batch_with_flat_array_and_conversation_flag_errors` (per `contracts/cli-commands.md`), `test_batch_with_invalid_conversation_value_errors` (per FR-012), `test_batch_with_parse_error_in_export_translates_per_FR027`, `test_batch_with_validation_error_in_export_translates_per_FR027`, `test_batch_on_skip_for_selected_conversation_raises_no_match` (per FR-028 (b) first clause), `test_batch_on_skip_for_other_conversation_emits_warning_and_continues` (per FR-028 (b) second clause)
- [x] T025 [P] [US2] Write backward-compatibility regression test in `tests/test_cli.py`: `test_batch_with_spec001_flat_array_works_unchanged` — runs against `tests/fixtures/single_topic.json` and asserts the pipeline behaves exactly as Spec 001 (per FR-014 and SC-004)

### Implementation for User Story 2

- [x] T026 [US2] In `src/exports.py`, implement `_walk_canonical_thread(conv: echomine.Conversation) -> list[echomine.Message]` per FR-026 (a): for ChatGPT-shaped conversations (with `current_node` set), use `conv.get_thread(conv.current_node)`; for Claude.ai (linear), use `conv.messages` as-is. Returns ordered list of messages.
- [x] T027 [US2] In `src/exports.py`, implement `_to_role_content(messages: list[echomine.Message]) -> list[dict[str, str]]` per FR-026 (b)(c): emit `{"role": msg.role, "content": msg.content}` only for `role in {"user", "assistant"}`; skip all other roles.
- [x] T028 [US2] In `src/exports.py`, implement `resolve_conversation_value(value: str, summaries: list[InsightMeshSummary]) -> int` per FR-010: if `value` parses as `int` AND that integer is in `[0, len(summaries))`, return it as index; otherwise return the index whose `summaries[i].id == value`. Raise `KeyError` (caught at CLI boundary) when no match.
- [x] T029 [US2] In `src/exports.py`, implement `extract_conversation(path: Path, selector: str) -> ChatTranscript` per FR-026: list summaries via `list_conversations(path)`, resolve selector to index, re-open with `detect_adapter(path)` and stream to the indexed conversation, walk canonical thread, convert to role/content, build and return `ChatTranscript` (from `src/transcript.py`). Empty canonical thread raises a dedicated error caught at CLI boundary per the Edge Case. Wire `on_skip` per FR-028 (b): if the skipped conversation's id (or stream-position-derived index) matches the user's selector, raise the FR-012 no-match error; otherwise emit `warning: skipped conversation <id-or-position>: <reason>` to stderr and continue streaming toward the selected conversation.
- [x] T030 [US2] In `src/exports.py`, extend exception translation: `echomine.ParseError` → `error: cannot parse export file <path>: <upstream message verbatim>` (per FR-027), `echomine.ValidationError` → `error: invalid conversation data in <path>: <upstream message verbatim>`. Unrecognized `EchomineError` subclasses re-raise unchanged.
- [x] T031 [US2] In `src/cli.py`, add `--conversation <str>` option to the existing `batch` command (Typer `Option`, default `None`) per FR-009. Update its help text to reference `insightmesh list` per FR-013's suggestion.
- [x] T032 [US2] In `src/cli.py` `batch` handler: implement input-shape detection — peek at the JSON root. If it's a list whose first element looks like a Spec 001 message (`{"role", "content"}` keys), route to existing Spec 001 path (FR-014 backward compat). Otherwise treat as multi-conversation export and require `--conversation`.
- [x] T033 [US2] In `src/cli.py` `batch` handler error cases: (a) multi-conv export without `--conversation` → `error: <path> is a multi-conversation export. Run 'insightmesh list <path>' to see available conversations...`, exit 1 (FR-013); (b) flat-array with `--conversation` → `error: --conversation cannot be used with a flat {role, content} transcript...`, exit 1; (c) invalid `--conversation` value → `error: no conversation matches --conversation '<value>' in <path>...`, exit 1 (FR-012); (d) empty canonical thread → `error: conversation '<id>' contains no usable user/assistant messages`, exit 1 (Edge Case).
- [x] T034 [US2] In `src/cli.py` `batch` handler success path: when `--conversation` resolves cleanly, call `extract_conversation(path, selector)` and feed the resulting `ChatTranscript` into Spec 001's existing orchestrator pipeline unchanged. Print the same `Loaded N exchanges from <path>` informational line as Spec 001.
- [x] T035 [US2] Run `uv run pytest tests/test_exports.py tests/test_cli.py -k batch` and iterate until all US2 tests pass. Manually verify SC-004 by running the existing Spec 001 fixtures (`tests/fixtures/single_topic.json`, `multi_topic.json`, `revisit.json`) without the `--conversation` flag and confirming identical behavior to Spec 001.

**Checkpoint**: User Story 2 fully functional. The `list → pick → batch` workflow works end-to-end. SC-001 fully satisfied. SC-004 (zero regression) verified.

---

## Phase 5: User Story 3 — Pre-flight Agent Presence Check (Priority: P1)

**Goal**: Before invoking the orchestrator, the CLI verifies every name in `EXPECTED_AGENTS` exists and parses cleanly in `.claude/agents/`. Failures abort with one aggregated stderr message per FR-022 and never write to `.logs/` per FR-019. Vault validation is folded into the same aggregated pass.

**Independent Test**: With one agent file deleted, `insightmesh batch <any-transcript> --vault <tmp>` aborts within 1 second with a clear error naming the missing agent (per SC-003). With multiple agents missing AND a bad vault path, one aggregated error message lists all problems (per FR-022 and quickstart.md Scenario 4). Reproducible from `spec.md` § User Story 3 Independent Test and quickstart.md Scenarios 3-4.

### Tests for User Story 3 (TDD)

- [x] T036 [US3] Write pre-flight tests in `tests/test_cli.py`: `test_preflight_all_agents_present_passes_silently` (per AS-1), `test_preflight_missing_one_agent_aborts_with_named_error` (per AS-2), `test_preflight_missing_multiple_agents_aggregates_in_one_message` (per AS-3 and FR-022), `test_preflight_malformed_frontmatter_reports_path_and_reason` (per AS-4), `test_preflight_unknown_extra_agent_does_not_fail` (extra agents in `.claude/agents/` don't trigger failure; only missing expected ones), `test_preflight_agents_dir_missing_treated_as_all_missing` (per Edge Case)
- [x] T037 [P] [US3] Write aggregation tests in `tests/test_cli.py`: `test_preflight_aggregates_vault_and_agent_failures_in_one_message` (per FR-022), `test_preflight_failure_writes_to_stderr_only` (per FR-019: assert nothing in `.logs/` after a pre-flight failure), `test_preflight_aggregated_message_uses_FR019_prefix` (asserts the message begins with `error: pre-flight checks failed:` exactly), `test_preflight_passes_when_extra_agent_file_with_unrecognized_name_present` (an agent file whose `name:` is not in `EXPECTED_AGENTS` does not cause failure)

### Implementation for User Story 3

- [x] T038 [US3] In `src/cli.py`, implement `_parse_agent_frontmatter(path: Path) -> AgentDefinition | MalformedAgent` per `research.md` R1: read file, split on `---` lines, `yaml.safe_load` the YAML block, extract `name:` field. Return `AgentDefinition(name=...)` on success; `MalformedAgent(file_path=str(path), reason=...)` on missing `name:` or YAML parse error (per FR-017).
- [x] T039 [US3] In `src/cli.py`, implement `_inspect_agents_directory(agents_dir: Path, expected: list[str]) -> tuple[list[str], list[MalformedAgent]]`: scan `agents_dir` for `*.md` files; for each, call `_parse_agent_frontmatter`; build a `name → AgentDefinition` map for valid ones and a list of `MalformedAgent`; compute `missing = [n for n in expected if n not in valid_names]`; return `(missing, malformed)`. If `agents_dir` does not exist or is unreadable, return `(expected[:], [])` (treats as all-missing per Edge Case). Implements FR-015.
- [x] T040 [US3] In `src/cli.py`, refactor the existing `_validate_vault(vault)` from `cli.py` (Spec 001's FR-011 implementation) into a contributor to the new unified pre-flight pass — return a list of error strings rather than calling `typer.Exit` directly.
- [x] T041 [US3] In `src/cli.py`, implement `_run_preflight(vault: Path, agents_dir: Path) -> PreflightDiagnostic` per FR-022: import `EXPECTED_AGENTS` from `src.orchestrator`; call vault contributor (T040) and `_inspect_agents_directory`; populate `PreflightDiagnostic.vault_errors`, `.missing_agents`, `.malformed_agents`; return the diagnostic without raising.
- [x] T042 [US3] In `src/cli.py`, implement `PreflightError.__init__` and `PreflightError._format()`: render the diagnostic into the FR-019 / FR-022 stderr format — `error: pre-flight checks failed:`, sections per non-empty list, footer `Aborting before orchestrator invocation. Fix the issues above and re-run.` Sections with empty lists are omitted (per quickstart.md Scenario 3-4 patterns). Implements FR-016 (single aggregated message).
- [x] T043 [US3] In `src/cli.py` `batch` handler: call `_run_preflight(vault, Path('.claude/agents'))` immediately after argument parsing (BEFORE any export parsing or orchestrator invocation). If `not diagnostic.is_empty()`, raise `PreflightError(diagnostic)`.
- [x] T044 [US3] In `src/cli.py`, add a `PreflightError` catch at the `batch` command boundary: write `exc._format()` (or `str(exc)`) to stderr ONLY, exit with code 1. Verify (by inspection) that no `.logs/` write occurs in this path per FR-019.
- [x] T045 [US3] Run `uv run pytest tests/test_cli.py -k preflight` and iterate until all US3 tests pass.

**Checkpoint**: User Story 3 fully functional. All three stories complete. SC-003 satisfied (pre-flight aborts in <1s with named errors).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation updates, quality gates, regression verification, and content for the project's memory.

- [x] T046 [P] Update `docs/getting-started.md`: replace the "Single-conversation only today" warning callout and the manual-jq workflow in *Your First Real Chat* Step 1 with the new `list → pick → batch` flow. Adapt content from `quickstart.md` Scenarios 1 and 2.
- [x] T047 [P] Update `docs/known-limitations.md`: remove the "No multi-conversation export selection" entry (or mark it as resolved with a pointer to Spec 002). Add a brief mention if anything from Spec 002 is itself deferred (none expected).
- [x] T048 [P] Update `docs/index.md`: change the export-selection row in the Status table from `🟡 Spec 002 — planned` to `:material-check:`. Remove the `!!! info "Input today is a single conversation"` callout near the top.
- [x] T049 [P] Update `README.md`: change the export-selection row in the Status table from `🟡 Spec 002 — planned` to `✅`. Remove or revise the `⚠️ Input is one conversation` callout under Quick Taste.
- [x] T050 Run `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`; fix any issues.
- [x] T051 Run `uv run mypy --strict src/`; fix any type errors. Special attention to the EchoMine boundary in `src/exports.py` (verify `Conversation`, `Message`, adapter types flow through correctly).
- [x] T052 Run full test suite (`uv run pytest`) and verify all tests pass: Spec 001's 84 tests + new US1/US2/US3 tests. Total target: ~120+ tests.
- [ ] T053 Manually execute `quickstart.md` Scenarios 1-5 against either real exports (preferred) or synthetic fixtures. Verify each scenario behaves as documented. Record any discrepancies in `specs/002-pre-flight-validation/scratch/quickstart-run-notes.md`.
- [x] T054 Static-inspection compliance check for FR-023: confirm `src/exports.py` imports nothing from `echomine`'s internal submodules and does not import `json` for adapter-style parsing. Either add a `tests/test_no_handrolled_adapters.py` AST check or note in the PR that a manual inspection was performed.
- [x] T055 Save a Spec 002 completion memory file at `~/.claude/projects/-Users-omarcontreras-PycharmProjects-insightmesh-core/memory/project_spec002_findings.md`: what worked, what didn't, what's deferred, performance against SC-002, anything for Spec 003 to know.
- [x] T056 Update `MEMORY.md` index with a one-line pointer to the Spec 002 findings file.
- [ ] T057 Open a PR using the format from Spec 001's PR (#1). Title: "Spec 002: pre-flight validation + EchoMine integration". Body covers Summary, What's in this PR, Verified end-to-end, Known limitations (if any), Quality gates, Test plan.
- [ ] T058 [P] Generate `tests/fixtures/large_synthetic_claude_ai_export.json` (programmatically, 5,000 conversations with minimal message content per conversation, ~5 MB on disk) via a small helper script committed at `tests/fixtures/generate_large_export.py`. Add benchmark test `tests/test_exports.py::test_list_5k_conversations_under_5_seconds` that invokes `list_conversations()` against the fixture and asserts wall-clock under 5 seconds per SC-002. Decorate with `pytest.mark.skipif(not fixture.exists())` so contributors without the fixture can still run the main suite.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion. BLOCKS all user stories.
- **User Stories (Phase 3-5)**: All depend on Foundational completion. Stories US1 and US3 are mutually independent and can proceed in parallel (different files: `exports.py` vs `cli.py` pre-flight functions). US2 depends on US1's `detect_adapter` and `list_conversations` (it imports them for `extract_conversation`).
- **Polish (Phase 6)**: Depends on all three user stories complete.

### User Story Dependencies

- **US1 (P1, MVP)**: Can start after Phase 2. No story dependencies.
- **US2 (P1)**: Can start after Phase 2 but consumes US1's `detect_adapter` and `list_conversations` — practically should not begin implementation tasks until T016 and T017 land. Tests for US2 (T023-T025) can be written before T016/T017 finish, since they will simply fail until implementation arrives.
- **US3 (P1)**: Can start after Phase 2. No dependency on US1 or US2. Different file (`cli.py` pre-flight functions vs `exports.py` for US1/US2 adapters).

### Within Each User Story

- Tests written BEFORE implementation (TDD, per Spec 001 convention)
- Within `src/exports.py`: `detect_adapter` → `list_conversations` → `extract_conversation` (sequential due to shared file)
- Within `src/cli.py`: pre-flight helpers → pre-flight runner → `PreflightError` rendering → wire to `batch` command (sequential due to shared file)
- CLI wiring is the last step in each story (depends on the helpers existing)

### Parallel Opportunities

- All Setup tasks except T002 (which waits for T001) can run in parallel.
- Within Foundational: T004 (orchestrator), T005 (exports.py skeleton), T006/T007 (fixtures), T008/T009 (types in exports.py), T010 (types in cli.py) — many marked [P]. T008 and T009 are sequential within exports.py but parallel with everything else.
- US1 tests in `test_exports.py` (T011) vs US1 tests in `test_cli.py` (T012) vs golden files (T013, T014) can all run in parallel.
- US1 implementation is mostly sequential within `exports.py` but the CLI wiring (T019-T021 in `cli.py`) can begin once T015-T018 land.
- US1 and US3 implementations are entirely parallel (different files).
- US2 cannot fully overlap with US1 implementation (consumes its outputs) but US2 tests (T023-T025) can be written in parallel with US1 implementation.
- Polish phase: T046-T049 (different docs files) all [P]; quality gates T050-T052 sequential.

---

## Parallel Example: User Story 1 + User Story 3 in parallel

```bash
# After Phase 2 checkpoint, two developers (or two terminal sessions) can work concurrently:

# Developer A: User Story 1 (touches src/exports.py)
T011 → T015 → T016 → T017 → T018 → T019 → T020 → T021 → T022

# Developer B: User Story 3 (touches src/cli.py pre-flight helpers only)
T036 → T038 → T039 → T040 → T041 → T042 → T043 → T044 → T045

# Fixture creation is independent and can run in parallel with both:
T006 [P], T007 [P], T013 [P], T014 [P]
```

User Story 2 picks up after US1 lands (specifically after T017: `list_conversations` exists).

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003).
2. Complete Phase 2: Foundational (T004-T010).
3. Complete Phase 3: User Story 1 (T011-T022).
4. **STOP and VALIDATE**: `insightmesh list <real-export>` produces a coherent table. Demoable.
5. Optional intermediate ship: this alone unblocks "see what's in my export" without enabling synthesis.

### Incremental Delivery

1. MVP (US1) ships → demo `insightmesh list`.
2. Add US2 → demo `insightmesh batch ... --conversation` against a real export → the headline workflow lands.
3. Add US3 → ship the pre-flight guard → silent-degradation failure mode closed.
4. Polish → docs updates + PR.

### Parallel Team Strategy (or two terminal sessions)

After Phase 2 completes, US1 and US3 can be developed in parallel because they touch different files. US2 waits for US1's `list_conversations` to exist (otherwise its tests have nothing to validate). Polish is sequential at the end.

---

## Notes

- [P] markers indicate strictly different files with no incomplete dependencies. Multiple test functions in the *same* file are sequential (one Edit pass), but the file as a whole can be developed in parallel with tasks in other files.
- TDD discipline (write test, see it fail, write implementation, see it pass) is preserved from Spec 001. Spec 001 shipped with 84 passing tests; Spec 002 target is +35-50 new tests, total ~120-135.
- The `[Story]` label maps each task back to a user story in `spec.md` for traceability.
- All commands run via `uv run <cmd>` per the constitution.
- FR-023 compliance (no hand-rolled adapters) is verified via T054.
- SC-001 (real-data usage without external scripts) is verified via T053 quickstart run.
- SC-004 (zero regression on Spec 001) is verified via T025 + T052.
- Spec 002 has no `[NEEDS CLARIFICATION]` markers; all design decisions are resolved in `spec.md` Clarifications + research.md.
