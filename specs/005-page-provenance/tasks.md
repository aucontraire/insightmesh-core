---
description: "Task list for Per-page provenance with shadow git and structured checkpoint JSON"
---

# Tasks: Per-page provenance with shadow git and structured checkpoint JSON

**Input**: Design documents from `/specs/005-page-provenance/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/history-orchestrator.md, quickstart.md

**Tests**: Included. The spec's 25 FRs, 7 SCs, and ten acceptance scenarios are testable; project standard is pytest with strict typing.

**Organization**: Grouped by user story. US1 (P1) is the MVP — structured `cp-<NNN>.json` + cumulative `provenance:` frontmatter block — and is independently testable on a fixture transcript without `git` being available at all. US2 (P2) layers the shadow-repo init + page snapshots + commit on top.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- All paths are repository-relative; single project layout (`src/`, `tests/` at repo root)

---

## Phase 1: Setup

**Purpose**: Confirm the environment is ready; this spec adds no new runtime dependencies.

- [X] T001 Confirm branch `005-page-provenance`; run `uv sync --all-extras`; verify `pyproject.toml` requires no new dependencies for this feature (Pydantic v2, PyYAML, echomine, Typer, claude-agent-sdk all already present; `subprocess` for `git` invocation is stdlib per Research Decision R1).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Land the data models, custom exceptions, and the `src/exports.py` metadata extension that both user stories depend on. No user story work can begin until this phase is complete.

- [X] T002 Create `src/history.py` with the Pydantic v2 models per `data-model.md`: `ConversationRecord` (id, export_path, provider as `Literal["anthropic", "openai"] | None`, models_used, transcript_hash), `ExchangeRecord` (index, user_message_id, assistant_message_id), `EditorDecisionRecord` (file, action as `Literal["created", "updated", "skipped"]`, confidence as `Literal["high", "medium", "low"]`, rationale, exchange_indices, signals as `dict[str, Any]` with an inline `# noqa` comment explaining the Any-deviation per data-model.md), `ResultsRecord` (pages_created, pages_updated, pages_skipped), `LinksRecord` (session_log, cursor), `CheckpointRecord` (schema_version=1 with `Literal[1]`, checkpoint_id, checkpoint_number, timestamp, conversation, exchanges, editor wrapping a `decisions` list, results, links; `model_validator(mode="after")` enforces `checkpoint_id == f"cp-{checkpoint_number:03d}"` per FR-001), `ProvenanceFrontmatter` (latest_checkpoint, conversations, latest_action as `Literal["created", "updated"]`, latest_confidence, total_edits, exchange_count), `ExchangeMessageIds` (user_message_id, assistant_message_id; internal scaffolding for metadata ferrying). All write-side models use `ConfigDict(strict=True, extra="forbid")`.

- [X] T003 In `src/history.py`, define the read-side subclass `CheckpointRecordRead(CheckpointRecord)` with `model_config = ConfigDict(strict=True, extra="allow")` per Research Decision R5 / FR-002. Document the deviation from the project-default strict-extras posture inline (a docstring noting "permanent records must outlive readers; this is spec-mandated by FR-002 and is intentional"). Define the custom exception hierarchy: `HistoryError(Exception)` base, plus `ShadowRepoUnavailable`, `ShadowRepoCommitFailed`, `FrontmatterParseFailed`. All inherit from `HistoryError`.

- [X] T004 [P] In `src/exports.py`, modify `extract_conversation` to populate `ChatTranscript.metadata` with three keys per Research Decisions R3 + R4: `metadata["provider"]` = `"anthropic"` | `"openai"` | `None` (tagged at the adapter-selection branch in `detect_adapter`; threaded through `extract_conversation` as an explicit parameter rather than re-running detection); `metadata["models_used"]` = `Conversation.models_used` from echomine when available, else `[]`; `metadata["exchange_message_ids"]` = a `dict[int, ExchangeMessageIds]` keyed by exchange index, where each value carries `user_message_id` and `assistant_message_id` from echomine's `Message.id`. For the Spec 001 flat-array path in `_load_flat_array_transcript` (or equivalent helper), set `provider=None`, `models_used=[]`, and an empty `exchange_message_ids` map. Existing `ChatTranscript.metadata: dict[str, Any]` already accommodates the new keys; no schema change to `ChatTranscript` is required (Minimal-Diff per constitution).

- [X] T005 [P] Create `tests/test_history.py` with unit tests covering: (a) `CheckpointRecord` strict validation rejects extra fields and wrong types; (b) `checkpoint_id` `model_validator` enforces `f"cp-{checkpoint_number:03d}"` (rejects mismatched id, accepts matching); (c) `schema_version` `Literal[1]` rejects any other value on write-side; (d) `ProvenanceFrontmatter.total_edits >= 1` and `exchange_count >= 0` constraints; (e) `EditorDecisionRecord.signals` accepts arbitrary JSON-serializable dicts; (f) `ExchangeRecord.index >= 0`; (g) `ConversationRecord.provider` accepts the three Literal values plus None; (h) `ExchangeMessageIds` strict validation; (i) `CheckpointRecordRead` tolerates unknown top-level extras and unknown sub-fields (forward-compatibility per FR-002, exercised against the fixture from T012); (j) the exception classes inherit from `HistoryError`; (k) **FR-020 negative invariant**: assert that the JSON serialization of a representative `CheckpointRecord` instance does NOT contain `"sha"`, `"commit_sha"`, `"git_sha"`, or any similarly-named field; assert these keys are not in `CheckpointRecord.model_fields`. Belt-and-suspenders for FR-020: Pydantic's `extra="forbid"` on the write-side model already prevents accidental insertion, but this test makes the structural negative-invariant explicit and would catch any future schema drift that introduced one.

**Checkpoint**: After T005, the data shapes and `ChatTranscript` metadata are ready. US1 and US2 can begin.

---

## Phase 3: User Story 1 — Structured provenance per page and per checkpoint (Priority: P1) 🎯 MVP

**Goal**: After every successful checkpoint, a structured `cp-<NNN>.json` file lands under `<vault>/InsightMesh/.history/checkpoints/<conv-id>/`, AND each touched wiki page carries a cumulative `provenance:` frontmatter block. All without requiring `git` (US1 ships even when git is absent).

**Independent Test**: Run `insightmesh batch <fixture-export.json> --conversation <id> --vault <test-vault>` with `git` uninstalled (or stubbed via patching `is_git_available` to return False). Assert `<vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-001.json` exists, validates against the `CheckpointRecord` schema, contains the expected conversation block + per-exchange ids + per-page editor decisions with rationale + signals dict. Assert each touched page has a frontmatter `provenance:` block with `total_edits=1` and `exchange_count` matching the contributing indices.

### Implementation for User Story 1

- [X] T006 [US1] In `src/history.py`, implement `compute_checkpoint_payload(*, checkpoint_number, transcript, exchanges_processed, editor_output, session_log_path, cursor_path, vault_root) -> CheckpointRecord` per `contracts/history-orchestrator.md`. Pure function: builds `ConversationRecord` from `transcript.metadata["provider"]`, `transcript.metadata["models_used"]`, transcript's source path, and cursor's `transcript_hash`; builds `ExchangeRecord` entries by looking up message ids from `transcript.metadata["exchange_message_ids"]` by exchange index; builds `EditorDecisionRecord` entries from `EditorOutput.decisions[*]` (action, confidence, rationale, exchange_indices, signals); builds `ResultsRecord` from `EditorOutput.pages_created`/`pages_updated`/`pages_skipped`; builds `LinksRecord` with vault-relative POSIX paths to the session log and cursor; constructs `CheckpointRecord` with timestamp = `datetime.now(timezone.utc)`. Per FR-005 expanded clause, before serializing the `signals` dict, traverse it and coerce any non-JSON-serializable value via `repr()`, emitting an `import sys; print(f"[provenance] signal value not JSON-serializable: {key}; coerced via repr()", file=sys.stderr)` warning naming the offending key.

- [X] T007 [US1] In `src/history.py`, implement `write_checkpoint_metadata(*, history_dir, conversation_subdir, record) -> Path`. Builds the target path `<history_dir>/checkpoints/<conversation_subdir>/<record.checkpoint_id>.json`; creates the per-conversation subdirectory via `mkdir(parents=True, exist_ok=True)` per FR-001. Enforces immutability per FR-001a: if the target path already exists, raise `FileExistsError(f"checkpoint already exists at {target}")`. Otherwise serialize the record via a custom dump function that sorts lists per FR-001b: `conversations` and `pages_*` ascending (strings), `exchange_indices` ascending (integers), preserves insertion order for `exchanges` and `editor.decisions`. Datetimes serialize as ISO 8601 UTC with `Z` suffix (use a Pydantic `field_serializer` or post-process the JSON string). Atomic write: `tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".tmp")` + `fsync` + `os.replace(tmp, target)`. Returns the absolute target path.

- [X] T008 [US1] In `src/history.py`, implement `merge_page_provenance(*, page_path, incoming) -> Path` per FR-008/FR-009/FR-010/FR-011 plus the page-disappeared edge case. Add a private helper `_split_frontmatter(text: str) -> tuple[dict, str]` that scans for the first two `---` markers at line boundaries (per Research Decision R2; reject ambiguous mid-body `---` by requiring the opening `---` on line 1) and returns the parsed YAML dict + the body. Open the page (raise `FileNotFoundError` if missing; orchestrator catches and logs page-disappeared edge case); if frontmatter parse fails, raise `FrontmatterParseFailed(f"yaml error in {page_path}: {exc}")`; otherwise look up the existing `provenance:` block (if any) and apply the cumulative merge: `total_edits = prior.total_edits + 1`, `conversations = sorted(set(prior.conversations) | set(incoming.conversations))`, `exchange_count = len(set(prior_indices) | set(this_indices))` where `prior_indices` is recovered by opening the page's prior `latest_checkpoint` JSON and reading the matching `editor.decisions[*].exchange_indices` for this page (per FR-009 expanded clause), and falling back to `prior.exchange_count + len(this_indices)` with a stderr warning when the prior pointer is missing/unparseable/dangling. Reassemble `---\n<merged-yaml-dump>\n---\n<body>` and write atomically via the same tempfile+os.replace pattern (FR-011 atomicity).

- [X] T009 [US1] In `src/orchestrator.py`, add a private helper `_sanitize_conversation_subdir(conv_id: str | None) -> str` that returns the conversation id with filesystem-unsafe characters replaced by `-` (reuse or factor the same logic as `_cursor_path_for`'s sanitization) when conv_id is non-None, OR the literal `"_flat"` sentinel when conv_id is None. Then add `_write_provenance(*, vault_root, transcript, exchanges_processed, editor_output, session_log_path, cursor_path, checkpoint_number) -> None` that performs steps 1 + 2 of the FR-017 ordered bookkeeping: (1) call `history.compute_checkpoint_payload(...)` then `history.write_checkpoint_metadata(...)`; (2) if `record.results.pages_created` + `record.results.pages_updated` is empty, return per Research Decision R10 (no frontmatter to update, no git in US2 to invoke); otherwise for each decision in `record.editor.decisions` where `action != "skipped"`, build a `ProvenanceFrontmatter` from this checkpoint, call `history.merge_page_provenance(...)` per page, catch `FrontmatterParseFailed` per page and log `[provenance] frontmatter parse failed for <page>: <err>` to stderr, catch `FileNotFoundError` per page and log `[provenance] page disappeared before snapshot: <page>` to stderr. Catch `FileExistsError` from the JSON write and log `[provenance] checkpoint already exists: <path>` to stderr per FR-001a. All exceptions inside `_write_provenance` MUST be caught at the function boundary by a top-level `try / except Exception as exc: print(f"[provenance] write failed: {exc}", file=sys.stderr)` so no provenance failure can propagate to fail the run per FR-019. Every stderr message uses the `[provenance] ` prefix per FR-016a.

- [X] T010 [US1] In `src/orchestrator.py`, wire `_write_provenance(...)` into the existing checkpoint loop in `run_batch`. The call MUST happen AFTER `_execute_pipeline` returns a successful `EditorOutput` AND BEFORE the existing Spec 004 cursor write, per FR-017. Compute `checkpoint_number` (the value the cursor is about to advance to), `vault_root` (from the existing CLI-resolved vault path), `session_log_path` (already determined before session log is written), `cursor_path` (from existing `_cursor_path_for(...)`), and `exchanges_processed` (the slice for this checkpoint). Wrap the call in `try: _write_provenance(...); except Exception as exc: print(f"[provenance] write failed: {exc}", file=sys.stderr)` as a belt-and-suspenders catch even though `_write_provenance` already swallows its own errors per FR-019. Document inline with a `# FR-017: provenance bookkeeping before cursor save` comment.

- [X] T011 [P] [US1] In `.claude/agents/editor.md`, add a documentation-only note under the existing Output schema section: "The orchestrator owns the `provenance:` frontmatter block per Spec 005 FR-017; Editor MUST NOT emit a `provenance:` block in drafts. The orchestrator will add or merge the block after Editor returns, using Editor's `action`/`confidence`/`rationale`/`exchange_indices`/`signals` from this checkpoint's `EditorDecision`." No behavior change in the agent's prompt; this is a contract clarification for any future Editor prompt edits.

- [X] T012 [P] [US1] Create `tests/fixtures/provenance_cp_001.json`: a hand-authored checkpoint JSON file conforming to `schema_version=1` with every required field populated by realistic values (a synthetic conversation id, two `exchanges` entries, one `editor.decisions` entry, a `results` block, a `links` block). Inject one unknown top-level field (e.g., `"future_field_x": "ignored"`) and one unknown sub-field inside `editor.decisions[0]` (e.g., `"future_subfield_y": 42`). Used by T005(i) to verify `CheckpointRecordRead` tolerates extras and by US1 integration tests that exercise the read path.

### Tests for User Story 1

- [X] T013 [US1] Add US1 integration tests to `tests/test_orchestrator.py` (and extend `tests/test_history.py` where the assertion is module-local). Cover all five US1 acceptance scenarios plus the relevant edge cases: (a) **End-to-end provenance write** (US1 AS-1, AS-2): mock `_execute_pipeline` to return a known `EditorOutput`; run one checkpoint; assert `<vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-001.json` exists, parses against `CheckpointRecord`, populates `conversation` from `transcript.metadata`, populates per-exchange `user_message_id`/`assistant_message_id`, populates `editor.decisions` with rationale + signals dict + exchange_indices, and each touched page has a `provenance:` block with `total_edits=1` and `exchange_count` = size of the contributing index set. (b) **Cumulative merge across two checkpoints** (US1 AS-3, AS-5; SC-002): run two checkpoints back-to-back on the same conversation where the second updates a page from the first; assert the page's `provenance:` block shows `total_edits=2`, `conversations` is unchanged (single conversation), `exchange_count` is the union of contributing indices (not the sum), `latest_checkpoint` advances to the new file. (c) **FR-001a immutability** (collision case): pre-populate `cp-001.json`; run the orchestrator with `checkpoint_number=1`; assert the write refuses, a `[provenance] checkpoint already exists` line is on stderr, the cursor still advances. (d) **FR-009 prior-pointer fallback warning**: pre-populate a page's `provenance:` block whose `latest_checkpoint` points to a non-existent file; run a second checkpoint touching that page; assert the stderr warning fires, `exchange_count` is computed via the upper-bound fallback. (e) **FR-010 malformed YAML**: pre-populate a page with malformed YAML frontmatter; run a checkpoint; assert the page's frontmatter is unchanged, the stderr warning fires naming the page, other pages and the JSON write are unaffected. (f) **FR-011 frontmatter atomicity** (testable via inspection): assert no `.tmp` file remains in the page's parent directory after a successful merge. (g) **Recoverable Editor parse failure** (US1 AS-4): mock Editor to return an `EditorOutput` with empty `decisions[]` (per Spec 004 FR-013 recoverable path); assert the JSON still writes, with empty `editor.decisions[]`. (h) **Empty checkpoint** (Research Decision R10): mock Editor to return zero created and zero updated pages; assert the JSON is still written, no frontmatter writes happen, no git work attempted. (i) **Page disappeared edge** (Edge Case G12): delete the wiki page between the mocked Editor return and the orchestrator's frontmatter merge; assert `[provenance] page disappeared before snapshot: <path>` on stderr, the JSON still records the decision, run exits 0. (j) **_flat sentinel + flat-array transcript**: run on a Spec 001 flat-array transcript (no conversation id); assert checkpoints land under `.history/checkpoints/_flat/cp-001.json`, conversation.id is null, models_used is `[]`. (k) **FR-019 provenance failure does not fail run** (`test_provenance_failure_does_not_fail_run`): patch `history.write_checkpoint_metadata` to raise `OSError("disk full")`; run a checkpoint; assert the run exits 0, the cursor still advances, a `[provenance] write failed:` line is on stderr. (l) **FR-005 signals coercion**: build an `EditorOutput` whose `signals` dict contains a non-JSON-serializable value (e.g., a `Path` object); assert the JSON write succeeds with the value coerced via `repr()` and a stderr warning fires. (m) **FR-018 session log untouched**: assert the session log written by Spec 001 logic for this checkpoint contains no `provenance:` reference and is byte-identical to a no-provenance baseline (use a small fixture comparison or compute a hash). (n) **FR-021 process-kill resilience**: simulate a process kill mid-provenance-write by patching `history.write_checkpoint_metadata`'s internal `os.replace` call to raise `KeyboardInterrupt` after the temp file is written but before the rename completes. Run the orchestrator; catch the resulting exception. Then re-run the orchestrator without intervention or recovery flags; assert (i) the next checkpoint advances to the same `checkpoint_number` the killed run was attempting (cursor was not advanced by the killed run per FR-017's terminal-cursor rule), (ii) the resulting `cp-<NNN>.json` is well-formed and parses against `CheckpointRecord`, (iii) no special-case recovery scan ran on startup (verify by asserting `init_shadow_repo` and `is_git_available` are called via the normal `_write_provenance` path, not via any pre-loop recovery code), (iv) the leftover `.tmp` file from the killed write does not block the re-run's write (the re-run gets a new tempfile name; verify the directory contains the final `cp-<NNN>.json` and no orphan `*.tmp` file remains after the re-run completes).

**Checkpoint**: After T013, US1 is fully functional. Structured checkpoint JSON lands, cumulative frontmatter math works, all documented failure paths are testable, and the run never fails on provenance errors. This is the MVP and ships even when `git` is absent.

---

## Phase 4: User Story 2 — Diff history via shadow git repository (Priority: P2)

**Goal**: A shadow git repository at `<vault>/InsightMesh/.history/` records one commit per successful checkpoint, snapshotting each touched page so `git -C .history log -p pages/<slug>.md` shows the page's evolution across edits.

**Independent Test**: Run two checkpoints back-to-back on the same conversation where the second updates a page from the first. Assert `<vault>/InsightMesh/.history/` is a git repository, contains two commits both greppable by their `checkpoint_id`, and `git -C .history log -p pages/<a-page>.md` shows the actual diff between the two versions of the page.

### Implementation for User Story 2

- [X] T014 [US2] In `src/history.py`, implement `is_git_available() -> bool` per Research Decision R1: invoke `subprocess.run(["git", "--version"], capture_output=True, timeout=2.0, check=False)` and return `True` on exit-0, `False` on `FileNotFoundError` or non-zero exit or `subprocess.TimeoutExpired`. Cache the result at module scope (`_GIT_AVAILABLE: bool | None = None`) so subsequent calls are free. Then implement `init_shadow_repo(history_dir: Path) -> None` per FR-012's three-state contract: (a) directory doesn't exist → `history_dir.mkdir(parents=True, exist_ok=True)`, then `subprocess.run(["git", "-C", str(history_dir), "init"], ...)`; (b) directory exists AND `<history_dir>/.git/` exists → return immediately; (c) directory exists but `<history_dir>/.git/` does not → run `git init` to re-initialize (git init is non-destructive on existing files). MUST NOT reset / reconfigure existing repo configuration in case (b). Raises `ShadowRepoUnavailable` if `git --version` fails or if any `git init` invocation returns non-zero (include captured stderr in the exception message).

- [X] T015 [US2] In `src/history.py`, implement `snapshot_page(*, source_page, history_dir, sanitized_slug) -> Path` per Research Decisions R8 + R9. Compute the destination `<history_dir>/pages/<sanitized_slug>.md`; create the parent directory with `mkdir(parents=True, exist_ok=True)`; copy via `shutil.copy2(source_page, dest)` to preserve mtime. Returns the destination absolute path. Raises `FileNotFoundError` if source has been deleted (orchestrator catches and logs page-disappeared per Edge Case G12). `sanitized_slug` is computed by the caller using the existing `src/wiki.py` slug helper (do not re-implement sanitization).

- [X] T016 [US2] In `src/history.py`, implement `commit_checkpoint(*, history_dir, checkpoint_id, conversation_id, conversation_subdir, decisions, created, updated) -> None` per FR-014's machine-greppable format. Build the subject line: `f"[InsightMesh checkpoint:{checkpoint_id} conversation:{conversation_id or '_flat'}] {len(updated)} pages updated, {len(created)} created"`. Build the body: `f"Metadata: checkpoints/{conversation_subdir}/{checkpoint_id}.json\nPages touched:\n"` + one line per decision (skipped excluded): `f"  - {decision.file} ({decision.action}, confidence:{decision.confidence})\n"`. Stage explicitly via `subprocess.run(["git", "-C", str(history_dir), "add", "--", *touched_paths], ...)` where `touched_paths` lists each `pages/<slug>.md` and the `checkpoints/<conversation_subdir>/<checkpoint_id>.json` relative to `history_dir`. Commit via `subprocess.run(["git", "-C", str(history_dir), "-c", "user.email=insightmesh@local", "-c", "user.name=InsightMesh", "commit", "-m", subject, "-m", body], ...)` so the orchestrator never reads or writes the user's global git config (per Research Decision R1). Raises `ShadowRepoCommitFailed` with captured stderr on any non-zero exit.

- [X] T017 [US2] In `src/orchestrator.py`, extend `_write_provenance(...)` (introduced in T009) in-place to cover full FR-017 scope by adding steps 3+4 after the existing steps 1+2. No rename or sibling helper; the function grows from US1-only to US1+US2 scope within the same definition. After steps 1+2 complete (and if there were any touched pages), execute steps 3+4: (3) for each touched page, call `history.snapshot_page(...)` using `wiki.sanitize_slug(decision.file)` for the sanitized slug; catch `FileNotFoundError` per page and log `[provenance] page disappeared before snapshot: <page>` (already logged by the merge step for the same page, but the snapshot step is a separate failure surface so handle it defensively); (4) if `not history.is_git_available()`, log `[provenance] git not on PATH; skipping shadow-repo commit` to stderr and return; otherwise call `history.init_shadow_repo(history_dir)`, catch `ShadowRepoUnavailable` and log `[provenance] shadow repo unavailable: <err>`; then call `history.commit_checkpoint(...)`, catch `ShadowRepoCommitFailed` and log `[provenance] commit failed: <git stderr>`. All stderr messages MUST use the `[provenance] ` prefix per FR-016a. FR-016 invariant: every git-side failure leaves the JSON + frontmatter from US1 already on disk; the run still exits 0.

### Tests for User Story 2

- [X] T018 [US2] Add US2 integration tests to `tests/test_history.py` (for the pure helpers) and `tests/test_orchestrator.py` (for end-to-end flow). Cover all five US2 acceptance scenarios plus the FR-012 three-state init: (a) **Single-commit checkpoint** (US2 AS-1): run one checkpoint; assert `.history/.git/` exists, `git -C .history log --oneline` shows exactly one commit, the subject contains `[InsightMesh checkpoint:cp-001 conversation:<id>]`, the body contains `Metadata: checkpoints/<conv-id>/cp-001.json` and the per-page list with action+confidence (FR-014). (b) **Two-checkpoint diff history** (US2 AS-2, AS-3; SC-003): run two checkpoints touching the same page; assert `git log` shows two commits with monotonically increasing checkpoint ids both greppable; `git -C .history log -p pages/<slug>.md` produces a diff between the two versions. (c) **Init idempotency, state (b)** (US2 AS-5): pre-create `.history/.git/`; run a checkpoint; assert init is a no-op (no `[init]` reconfiguration, repo unchanged), commit lands cleanly. (d) **Init from state (c)** (FR-012 expanded): pre-create `.history/` with content but no `.git/`; run a checkpoint; assert `git init` runs, existing files in `.history/` are preserved, commit lands. (e) **No-git fallback** (US2 AS-4; SC-005): patch `is_git_available` to return False; run a checkpoint; assert `.history/checkpoints/<conv-id>/cp-001.json` and the page frontmatter still landed, no commit happened, stderr contains `[provenance] git not on PATH`, run exits 0. (f) **Commit-failure fallback** (FR-016): patch `commit_checkpoint` to raise `ShadowRepoCommitFailed("permission denied")`; run a checkpoint; assert JSON + frontmatter still landed, stderr contains `[provenance] commit failed:`, run exits 0; then run a second checkpoint that DOES succeed; assert the second commit sweeps up both checkpoints' snapshots cleanly. (g) **Empty checkpoint no-commit** (Research Decision R10): run a checkpoint with zero created and zero updated pages; assert no init happens (no `.history/` directory created), no commit happens, but the JSON file still writes per US1. (h) **User-modified `.history/` non-destructive** (Edge Case): pre-create `.history/.git/` with a manual commit by a synthetic user.name; run an InsightMesh checkpoint; assert the orchestrator's commit lands on top of the user's, the user's commit is untouched (git log shows both), no error. (i) **Vault with populated `.history/`** (SC-004): pre-populate `.history/checkpoints/<conv-id>/cp-001.json` and a matching shadow-repo commit; run a checkpoint that produces `cp-002.json`; assert numbering advances monotonically, no overwrite, second commit lands on top, both `cp-001.json` and `cp-002.json` are present. (j) **Commit message format** (FR-014 standalone unit test in `test_history.py`): construct a `commit_checkpoint` invocation against a temp git repo; capture the committed message via `git log -1 --pretty=%B`; assert subject + body match the spec exactly.

**Checkpoint**: After T018, US2 is fully functional and tested. Both stories are independently shippable.

---

## Phase 5: Polish & Cross-Cutting

- [X] T019 Run the full constitution-mandated gate and fix any issues: `uv run pytest`, `uv run mypy --strict src/`, `uv run ruff check src/ tests/`, `uv run black --check src/ tests/`. Confirm ruff `TID251` does not flag the new `src/history.py` (Pydantic BaseModel only, no `@dataclass`/`NamedTuple`). Confirm the `Any` use in `EditorDecisionRecord.signals` is the only `Any` introduced and is marked with an inline `# noqa` plus a justifying comment per data-model.md.

- [X] T020 [P] Real-data end-to-end smoke test per `quickstart.md`. Run a real Claude conversation (a small one is sufficient for shape verification): inspect the on-disk artifacts (JSON parses, has `provider="anthropic"`, `models_used=[]`, exchange ids populated; page frontmatter has the new `provenance:` block; `git -C .history log` shows one commit with the FR-014 subject). Then run a real ChatGPT conversation (if available): same checks plus `models_used` is non-empty. Then a Spec 001 flat-array transcript: assert `_flat` subdirectory, conversation.id null, exchange message ids null. Measure provenance-bookkeeping wall-clock per checkpoint and confirm under 1 s per SC-006 (instrument with `time.perf_counter()` around the `_write_provenance` call). For reproducibility, the smoke run targets a vault populated to at least 50 wiki pages via prior InsightMesh runs (3+ fixture conversations of ~10-15 exchanges each is a reliable way to reach this floor). Document the actual page count, the per-checkpoint provenance-bookkeeping timing (mean + max across all checkpoints in the run), and the vault path used in `project_spec005_findings.md`. Do not tighten SC-006's "hundreds of pages" wording in spec.md; that is the broader success criterion, and this task pins the verification conditions only.

- [X] T021 [P] Update `docs/known-limitations.md` (move long-chat info aside if needed) to note that provenance now persists per-checkpoint to `.history/checkpoints/<conv-id>/cp-<NNN>.json` plus a `provenance:` frontmatter block on every touched page, plus a shadow-git diff view at `.history/`. Cross-link to the new `provenance:` examples in `quickstart.md`. Optionally add a short paragraph to `docs/getting-started.md` pointing at the provenance artifacts under a new "What gets created on disk" section. NO new top-level docs files needed (constitution Anti-Slop §Minimal-Diff).

---

## Dependencies

- **T001 (Setup)** before all.
- **Foundational (T002–T005)**:
  - T002 → T003 sequential (same file `src/history.py`).
  - T004 [P] (different file `src/exports.py`); independent of T002/T003.
  - T005 [P] (different file `tests/test_history.py`); depends on T002/T003 for the model imports, and on T012 for fixture (cross-phase dependency; in practice T012 lands before T005's fixture-based subtests pass — order the commits accordingly).
- **US1 implementation**:
  - T006 → T007 → T008 sequential (same file `src/history.py`).
  - T009 → T010 sequential (same file `src/orchestrator.py`); T009 depends on T002/T003/T006/T007/T008.
  - T011 [P] (different file `.claude/agents/editor.md`); independent.
  - T012 [P] (different file, fixture); independent.
  - T013 depends on T006–T010 (the entire US1 implementation), T011 (no — T011 is doc-only), T012 (fixture for read-tolerance test).
- **US2 implementation**:
  - T014 → T015 → T016 sequential (same file `src/history.py`); depend on T002/T003.
  - T017 depends on T010 (extends `_write_provenance` in-place per F1 resolution) and T014/T015/T016.
  - T018 depends on T014–T017.
- **Polish**:
  - T019 after all implementation and tests.
  - T020 after T019 (smoke needs the gate green).
  - T021 anytime after T013 / T017.

## Parallel Example

Genuine parallel opportunities after T002/T003 land in Phase 2:

```text
# All three can run concurrently — different files, no cross-dependency:
T004 [P]         src/exports.py — populate ChatTranscript.metadata
T005 [P]         tests/test_history.py — model unit tests (fixture-dependent subtests gate on T012)
T011 [P] [US1]   .claude/agents/editor.md — doc-only contract note
T012 [P] [US1]   tests/fixtures/provenance_cp_001.json — forward-compat fixture
```

Polish parallelism:

```text
T020 [P]   real-data smoke
T021 [P]   docs update
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (T001).
2. Complete Phase 2 (T002–T005). Models + exports metadata + unit tests ready.
3. Complete Phase 3 (T006–T013). At the end of T013, structured `cp-<NNN>.json` writes work, cumulative frontmatter math works, all documented failure paths are tested, and the run never fails on provenance errors. Ships without requiring `git`.
4. **STOP and VALIDATE**: Run an early version of T020 against a real Claude conversation to confirm the JSON and frontmatter shapes look right end-to-end.
5. Optionally ship US1 as a v0.5.0 release here.

### Incremental Delivery

1. Setup + Foundational → models + exports metadata exist and are tested.
2. US1 → structured JSON + cumulative frontmatter block + page provenance. Ship.
3. US2 → shadow-repo diff history layered on top. Ship.
4. Polish → gate, real-data smoke, docs update.

### Notes on file conflicts

- `src/history.py` accumulates changes through T002 → T003 → T006 → T007 → T008 → T014 → T015 → T016. All sequential because they touch the same file.
- `src/orchestrator.py` accumulates changes through T009 → T010 → T017. Sequential.
- `src/exports.py` is touched only in T004. US2 does not modify it.
- `tests/test_history.py` accumulates assertions across T005, T013 (partial), T018 (partial); disjoint test functions; sequential commits within the file.
- `tests/test_orchestrator.py` accumulates assertions across T013, T018. Disjoint test functions; sequential commits.
- `tests/fixtures/provenance_cp_001.json` is created only in T012.
- `.claude/agents/editor.md` is touched once in T011 (doc-only).
- Docs files (`docs/known-limitations.md`, `docs/getting-started.md`) are touched only in T021.

## Notes

- Constitution: ONE new `src/` file (`src/history.py`) is justified in `plan.md` Complexity Tracking. No new dependencies. All new data shapes are Pydantic v2 `BaseModel` with `ConfigDict(strict=True, extra="forbid")` for write-side, with one documented `extra="allow"` deviation on the read-side `CheckpointRecordRead` subclass per FR-002 / Research Decision R5 / R12.
- Per FR-019, provenance failure MUST NEVER fail the run. T013(k) `test_provenance_failure_does_not_fail_run` is the load-bearing test for this invariant; if it ever fails, the spec's whole "best-effort" posture is broken.
- Per FR-017, the JSON write must happen BEFORE frontmatter merges (which must happen before snapshots, which must happen before the git commit). T017 enforces this ordering in `_write_provenance`.
- Per SC-001, a user opening `cp-<NNN>.json` alone must be able to answer the spec's observability questions without the session log. T013(a) verifies the JSON shape covers all required fields.
- Per SC-006, provenance bookkeeping must add under 1 s of wall-clock per checkpoint. T020 measures this on real data; if regressed, profile the frontmatter merge (most likely culprit if a page has hundreds of prior-checkpoint dereferences).
- Real-data smoke (T020) covers Claude + ChatGPT + flat-array. Reserve one of each export type for this; document the conversation IDs and pre/post behavior so the result is reproducible.
- Tests are first-class deliverables here. The spec's 25 FRs and 7 SCs each map to at least one test assertion across T005, T013, and T018.
- FR-022 (optional orphan detection on startup) is intentionally NOT implemented in this spec; the requirement uses MAY, not MUST. If a future spec needs it, the contract is already pinned in spec.md and `contracts/history-orchestrator.md`.
