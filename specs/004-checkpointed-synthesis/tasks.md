---
description: "Task list for Checkpointed synthesis with wiki-as-carry-over"
---

# Tasks: Checkpointed synthesis with wiki-as-carry-over

**Input**: Design documents from `/specs/004-checkpointed-synthesis/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/checkpoint-orchestrator.md, quickstart.md

**Tests**: Included. Spec acceptance scenarios and Success Criteria (SC-001..SC-007) enumerate unit and integration tests; project standard is pytest with strict typing.

**Organization**: Grouped by user story. US1 (P1) is the MVP and is independently testable on a long fixture transcript; US2 (P2) layers the per-invocation cap on top.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- All paths are repository-relative; single project layout (`src/`, `tests/` at repo root)

---

## Phase 1: Setup

**Purpose**: Confirm the environment is ready; this spec adds no new runtime dependencies.

- [X] T001 Confirm branch `004-checkpointed-synthesis`; run `uv sync --all-extras`; verify `pyproject.toml` requires no new dependencies for this feature (Pydantic v2, Typer, claude-agent-sdk, echomine all already present).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the new `src/checkpoint.py` module that both user stories depend on (cursor model, hash, atomic load/save, exceptions). No user story work can begin until this phase is complete.

- [X] T002 Create `src/checkpoint.py` with the `Checkpoint` Pydantic v2 model (per `data-model.md`): fields `schema_version: int = 1`, `export_path: Path`, `conversation_id: str | None = None`, `transcript_hash: str = Field(min_length=64, max_length=64)`, `last_processed_exchange_index: int = Field(ge=0)`, `checkpoint_number: int = Field(ge=1)`, `status: Literal["complete", "interrupted", "failed"]`, `last_error: str | None = None`, `topics_covered_digest: list["DigestEntry"] = Field(default_factory=list)`, `meaning_summary: str | None = None`, `updated_at: datetime`. Plus the `DigestEntry` model: `page_title: str = Field(min_length=1)`, `gist: str = Field(min_length=1, max_length=200)`. Both use `ConfigDict(strict=True)`.

- [X] T003 In `src/checkpoint.py`, define the custom exception hierarchy: `CheckpointError(Exception)` base, plus `CheckpointMissing`, `CheckpointHashMismatch`, `CheckpointAlreadyComplete`, `CheckpointSchemaVersionMismatch`, `CheckpointMalformed`, `CheckpointIndexOutOfBounds`. All inherit from `CheckpointError`.

- [X] T004 In `src/checkpoint.py`, implement `compute_transcript_hash(transcript: ChatTranscript) -> str` returning the SHA-256 hex digest of `transcript.model_dump_json()` (per Research Decision 1 and FR-006).

- [X] T005 In `src/checkpoint.py`, implement `load_checkpoint(path: Path) -> Checkpoint | None`: returns `None` if the file does not exist; raises `CheckpointMalformed` on JSON parse failure or Pydantic ValidationError (wrap the underlying exception in the message); raises `CheckpointSchemaVersionMismatch` if the loaded `schema_version` differs from the version the module understands (currently 1).

- [X] T006 In `src/checkpoint.py`, implement `save_checkpoint(path: Path, checkpoint: Checkpoint) -> None` using the write-temp-then-rename atomic pattern (per Research Decision 3 and FR-002): write to `{path}.tmp` then `os.replace` to `{path}`. Create parent directory if missing. Propagate disk errors to the caller.

- [X] T007 [P] Create `tests/test_checkpoint.py` with unit tests covering: (a) `Checkpoint` strict validation rejects extra fields and wrong types; (b) `transcript_hash` length-64 enforced; (c) `status` Literal enforced; (d) `schema_version` defaults to 1; (e) `meaning_summary` defaults to None and accepts only None or str; (f) `compute_transcript_hash` determinism (same transcript → same hash) and sensitivity (different transcript → different hash); (g) `load_checkpoint` returns None for a missing file; (h) `load_checkpoint` raises `CheckpointMalformed` on broken JSON; (i) `load_checkpoint` raises `CheckpointSchemaVersionMismatch` when loaded `schema_version` is not 1; (j) `save_checkpoint` round-trips via `load_checkpoint` (write a Checkpoint, load it, compare); (k) `save_checkpoint` creates the parent directory if missing; (l) `save_checkpoint` writes atomically (the target file never appears in a partially-written state — assertable by checking that `{path}.tmp` does not exist after a successful write).

**Checkpoint**: After T007, the checkpoint module is complete and unit-tested. US1 and US2 implementation can now begin.

---

## Phase 3: User Story 1 — Synthesize a long chat across multiple checkpoints (Priority: P1) 🎯 MVP

**Goal**: A long Claude.ai conversation that previously could not be synthesized in one shot now processes to completion across multiple checkpoints, with auto-resume on re-invocation and no-op on already-complete.

**Independent Test**: Run `insightmesh batch long_chat_fixture.json` on a fixture transcript large enough to need more than one checkpoint at the test token budget; assert the wiki contains pages covering every exchange and the cursor reaches end-of-transcript with `status: complete`. Re-run the same command; assert the system reports "already complete" and exits without invoking agents.

### Implementation for User Story 1

- [X] T008 [P] [US1] In `src/logger.py`, extend `HistorianOutput` with the new optional field `topics_covered_increment: list[DigestEntry] | None = None` (import `DigestEntry` from `src.checkpoint`). Maintain backward compatibility: `None` and empty list both mean "nothing to merge."

- [X] T009 [P] [US1] Update `.claude/agents/historian.md`: add a Quality Rule and an Output schema addition specifying that for each `augmented_draft` Historian processes, it MUST also append one entry to `topics_covered_increment` (`page_title` = the draft's `tentative_title` exactly; `gist` = a one-line summary, no newlines, 200-char cap, derived from the draft's title and first paragraph). This is metadata for the orchestrator's checkpoint cursor; it does not affect cross-link recommendations.

- [X] T010 [P] [US1] Update `.claude/agents/synthesis.md`: add an Input note specifying that for second-or-later checkpoints of a conversation, the input includes a `topics_covered_digest` field (list of `{page_title, gist}`) listing pages produced by prior checkpoints. Synthesis SHOULD extend or cross-reference those prior pages rather than producing duplicate drafts. Synthesis MUST NOT inline digest entries into draft prose; the digest is LLM context, not source material.

- [X] T011 [P] [US1] Create `tests/fixtures/long_chat_export.json`: a Claude-style export containing one conversation with enough exchanges (and enough content per exchange) that the test token budget triggers more than one checkpoint. Keep the fixture as small as possible while still spanning multiple checkpoints (consider using a low test-time token budget rather than a huge fixture).

- [X] T012 [US1] In `src/cli.py`, add four new flags to the existing `batch` command (Typer): `--resume` (bool flag), `--max-exchanges` (int, optional, default None), `--force-resume` (bool flag), `--retry` (bool flag). Validate `--max-exchanges <= 0` errors before any agent invocation (FR-008). Derive the cursor file path per FR-005: for single-conversation source files, `logs/{stem}.checkpoint.json`; for a multi-conversation export with a `--conversation` argument, `logs/{stem}__{conversation_id}.checkpoint.json` (sanitize filesystem-unsafe characters in `conversation_id` to hyphens — replace `/` and `:` at minimum). Pass `checkpoint_path`, `max_exchanges`, `require_resume`, `force_resume`, `retry` to `run_batch`. (See Dependencies: must land alongside or after T013 to avoid a runtime `TypeError`.)

- [X] T013 [US1] In `src/orchestrator.py`, modify the `run_batch` signature to accept new keyword-only parameters: `checkpoint_path: Path | None = None`, `max_exchanges: int | None = None`, `require_resume: bool = False`, `force_resume: bool = False`, `retry: bool = False`, `token_budget: int | None = None`. The `token_budget` defaults to a sensible Sonnet-aware value (approximately 50% of the configured model's context window per FR-015; pick a constant for Sonnet, e.g., 100_000 tokens, or compute from a model registry).

- [X] T014 [US1] In `src/orchestrator.py`, implement the cursor entry logic at the start of `run_batch`: if `checkpoint_path` is set, call `load_checkpoint(checkpoint_path)`. If None and `require_resume` is True, raise `CheckpointMissing`. If a cursor exists: (a) compute `compute_transcript_hash(transcript)`; if it differs from `cursor.transcript_hash` and `force_resume` is False, raise `CheckpointHashMismatch` (FR-006); (b) if `cursor.last_processed_exchange_index >= len(transcript.exchanges)`, raise `CheckpointIndexOutOfBounds` (Edge Case from /spec-gaps); (c) if `cursor.status == "complete"`, print "already complete; delete the cursor at <path> to re-run" and return a no-op SessionLog (FR-007); (d) if `cursor.status == "failed"` and `retry` is False, print `cursor.last_error` to stderr and exit with code 1 (FR-014).

- [X] T015 [US1] In `src/orchestrator.py`, implement the checkpoint loop body. Extract the slice-picking logic into a pure, testable helper: `pick_checkpoint_slice(exchanges: list[Exchange], start_index: int, token_budget: int, accumulated_digest: list[DigestEntry] | None = None) -> list[Exchange]` that walks forward from `start_index` and sums approximate token cost via a char-based heuristic (`len(rendered_text) // 3.5`) until adding the next exchange would exceed `token_budget`; emits at least one exchange. The main loop calls this helper, then: (a) builds the Synthesis input as `_to_role_content(slice)` plus, for `checkpoint_number > 1`, the cursor's accumulated `topics_covered_digest`; (b) invokes Synthesis → Historian → Editor as today; (c) on Editor success, extends the accumulated digest with `HistorianOutput.topics_covered_increment or []`, computes the new `last_processed_exchange_index`, sets `status` to `complete` if reached end-of-transcript else `interrupted`, calls `save_checkpoint`, then advances `start_index`.

- [X] T016 [US1] In `src/orchestrator.py`, implement the failure path: wrap each agent invocation and the Editor write in a try/except that on any exception calls `save_checkpoint` with `status="failed"`, `last_error=str(e)`, the current `last_processed_exchange_index` (last successful checkpoint's index, or whatever the cursor had on entry), `checkpoint_number` unchanged from entry, and `last_error` populated. Re-raise the original exception so the CLI surfaces it. FR-014 invariant: `last_error` is null when `status != "failed"`.

### Tests for User Story 1

- [X] T017 [US1] Create or extend `tests/test_orchestrator.py` with US1 **happy-path** integration tests: (a) **Multi-checkpoint completion**: Fresh run on `long_chat_export.json` with a small test token budget spans more than one checkpoint; final cursor has `status="complete"` and `last_processed_exchange_index == len(exchanges) - 1`; wiki contains pages whose `exchange_indices` collectively cover every exchange (US1 AS-1; SC-001, SC-005). (b) **Resume skips processed exchanges**: Start the run, interrupt mid-way (simulate by raising an exception in a test double), restart; the second invocation's Synthesis input contains ONLY exchanges from cursor+1 onward (assert via session log `input_summary`); zero re-invocations of Synthesis for already-processed exchanges (US1 AS-2; SC-002). (c) **No-op on complete**: Run to completion; second invocation reports "already complete," exits without invoking any agent, and writes no new session log (US1 AS-3; SC-003). (d) **Digest carry-over**: Second-or-later checkpoint's Synthesis `input_summary` includes the accumulated `topics_covered_digest` (US1 AS-4; FR-011). (e) **FR-013 no-regression**: For a small fixture that fits in a single checkpoint, the produced wiki is identical to running without any checkpoint code path (same number of pages, same content) (SC-004).

- [X] T018 [US1] Add US1 **hash/index/schema/malformed error-path** integration tests to `tests/test_orchestrator.py`: (a) **Hash mismatch refuse**: Mutate the transcript, attempt resume without `--force-resume`; assert `CheckpointHashMismatch` and non-zero exit (FR-006; SC-007). (b) **Hash mismatch override**: Same mutation with `--force-resume`; assert resume proceeds. (c) **Cursor index out of bounds**: Build a cursor whose `last_processed_exchange_index` exceeds the transcript length; attempt resume; assert `CheckpointIndexOutOfBounds` (Edge Case). (d) **Schema-version mismatch**: Hand-write a cursor with `schema_version=999`; attempt resume; assert `CheckpointSchemaVersionMismatch` (FR-016). (e) **Malformed cursor**: Write garbage JSON to the cursor path; attempt resume; assert `CheckpointMalformed` and a friendly error naming the path (Edge Case).

- [X] T019 [US1] Add US1 **resume error-path** integration tests to `tests/test_orchestrator.py`: (a) **--resume on missing cursor**: Pass `--resume` with no existing cursor; assert `CheckpointMissing` with a friendly message naming the expected path (FR-010). (b) **Failed cursor refuses without --retry**: Write a cursor with `status="failed"`, `last_error="vault error"`; attempt resume without `--retry`; assert stderr contains "vault error" and exit code 1, no agent invoked (FR-014). (c) **Failed cursor with --retry proceeds**: Same setup, pass `--retry`; assert a fresh checkpoint attempt runs from the cursor position.

- [X] T020 [US1] Add an **FR-012 absence test** to `tests/test_orchestrator.py` (or a small `tests/test_cli.py` if you prefer): invoke the `batch` command with `--from 0`, `--to 10`, and `--from-percent 0.5`; assert Typer rejects each with "no such option" error and exit code 2. Verifies that non-linear slicing flags are not present in the CLI surface (FR-012).

- [X] T021 [US1] Add an **FR-015 token-budget unit test** in `tests/test_orchestrator.py` covering the `pick_checkpoint_slice` helper extracted in T015: for several `(exchanges, token_budget)` pairs, assert that the returned slice's rendered char-count (computed via the same heuristic the helper uses) is at most `token_budget * 3.5`; also assert that adding the next exchange beyond the slice would exceed the budget (the helper greedily packs up to the limit); also assert that an empty `exchanges` input or a `start_index` past the end returns an empty list cleanly. Verifies FR-015 directly rather than only via the indirect "multiple checkpoints span" assertion in T017(a).

**Checkpoint**: After T021, US1 is fully functional. A long chat completes; resume works; no-op works; the digest carries over; all FR-006/014/016/Edge-Case error paths are tested; the token-budget boundary is verified at the unit level; the FR-012 "no slice flags" invariant is verified. This is the MVP and is independently shippable.

---

## Phase 4: User Story 2 — Cap a single invocation's work (Priority: P2)

**Goal**: The `--max-exchanges N` flag caps how many exchanges one invocation processes, with the cursor persisting normally so the next invocation continues forward.

**Independent Test**: Run `insightmesh batch long_chat_fixture.json --max-exchanges 5` on the fixture; assert the cursor advances by approximately 5 exchanges (soft cap, checked between checkpoints; in-flight checkpoint may push slightly past) and `status="interrupted"`. Re-run with the same cap; assert the cursor advances another ~5.

### Implementation for User Story 2

- [X] T022 [US2] In `src/orchestrator.py`, implement the soft-cap behavior inside the checkpoint loop from T015: track `exchanges_processed_this_invocation` as a counter; after each successful checkpoint, if the counter has reached or exceeded `max_exchanges` (and `max_exchanges is not None`), break out of the loop with `status="interrupted"`. Do NOT interrupt an in-flight checkpoint: the cap is evaluated between checkpoints (per refined FR-009, SC-006). The cursor's resting point is always a checkpoint boundary.

### Tests for User Story 2

- [X] T023 [US2] Add integration tests to `tests/test_orchestrator.py`: (a) **Cap stops processing**: Run with `--max-exchanges 5` on the long fixture; assert the cursor's `last_processed_exchange_index` advanced by at most `5 + size_of_most_recent_checkpoint` (per refined FR-009/SC-006), `status="interrupted"`, exit code 0 (US2 AS-1). (b) **Cap composes across invocations**: Run twice with `--max-exchanges 5`; assert cumulative advancement is roughly 2N, no duplicate work (US2 AS-2). (c) **Cap exceeds remaining**: Run with `--max-exchanges 10000` on a transcript with few exchanges; assert processing reaches end-of-transcript and `status="complete"` (US2 AS-3). (d) **--max-exchanges 0 errors**: Pass `--max-exchanges 0`; assert error before any agent runs, exit code 2 (FR-008). (e) **--max-exchanges -1 errors**: Same with negative value. (f) **--resume + --max-exchanges composes**: With an existing cursor, run `--resume --max-exchanges 3`; assert the cursor advances by ~3 from its prior position (Edge Case).

**Checkpoint**: After T023, US2 is fully functional and tested. Both stories are independently shippable.

---

## Phase 5: Polish & Cross-Cutting

- [X] T024 Run the full constitution-mandated gate and fix any issues: `uv run pytest`, `uv run mypy --strict src/`, `uv run ruff check src/ tests/`, `uv run black --check src/ tests/`. Confirm ruff `TID251` does not flag the new `src/checkpoint.py` (Pydantic BaseModel only, no `@dataclass`/`NamedTuple`).

- [ ] T025 [P] Run the real-data end-to-end smoke test per `quickstart.md` Scenarios B, C, E against the user's actual long Claude exports (the motivating SC-001 case). Confirm: (1) a previously-overflow conversation now completes across multiple checkpoints; (2) `--max-exchanges` paces work and the cursor advances correctly; (3) a simulated failure → `--retry` recovers cleanly. Document outcomes in a session log or short note.

- [X] T026 [P] (Optional) Note in `docs/getting-started.md` (or a new `docs/known-limitations.md`) that long conversations are now processed across checkpoints, that the cursor lives under `logs/{stem}[__{conversation_id}].checkpoint.json`, and that users can use `--max-exchanges N` to pace work or `--retry` to resume past a recorded failure.

---

## Dependencies

- **T001 (Setup)** before all.
- **Foundational (T002–T007)**: T002 → T003 → T004 → T005 → T006 are sequential (same file `src/checkpoint.py`). T007 [P] can technically start once T002–T006 are merged (tests need the API to exist).
- **US1 implementation**:
  - T008, T009, T010, T011 are all [P] (different files: `src/logger.py`, `.claude/agents/historian.md`, `.claude/agents/synthesis.md`, `tests/fixtures/long_chat_export.json`). All depend only on Foundational.
  - **T012 and T013 are co-dependent**: T012 (CLI) calls `run_batch` with new params; T013 adds those params to `run_batch`. **Develop together in the same commit, OR land T013 first.** Calling `run_batch` from T012 before T013 lands will TypeError at runtime. (Surfaced by /speckit-analyze finding O1.)
  - T013 depends on T002, T008. T014 → T015 → T016 are sequential (same file `src/orchestrator.py`). T015 depends on T008 + T009 (the digest data shape).
- **US1 tests (T017–T021)**: all live in `tests/test_orchestrator.py` (and possibly T020 in `tests/test_cli.py`); develop sequentially within that file. All depend on T011 (fixture), T012 (CLI flags), T015/T016 (orchestrator paths), T009/T010 (agent prompts for the digest carry-over assertion in T017(d)).
  - T021 specifically depends on T015 having extracted `pick_checkpoint_slice` as a pure helper.
- **US2 implementation**: T022 layers on T015 (extends the orchestrator's checkpoint loop). T023 depends on T022.
- **Polish**: T024 after all implementation and tests. T025 after T024 (real-data smoke needs the gate green). T026 anytime after T012/T017.

## Parallel Example

Genuine parallel opportunities after Foundational (T002–T007) completes:

```text
# All four can run concurrently — different files, no cross-dependency:
T008 [P] [US1]   src/logger.py — extend HistorianOutput
T009 [P] [US1]   .claude/agents/historian.md — emit topics_covered_increment
T010 [P] [US1]   .claude/agents/synthesis.md — consume topics_covered_digest
T011 [P] [US1]   tests/fixtures/long_chat_export.json — multi-checkpoint fixture
```

Polish parallelism:

```text
T025 [P]   real-data smoke
T026 [P]   docs note
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (T001).
2. Complete Phase 2 (T002–T007). The checkpoint module is the foundation.
3. Complete Phase 3 (T008–T021). At the end of T021, multi-checkpoint synthesis is working end-to-end on a fixture, with all failure paths tested, the token-budget heuristic verified at the unit level, and the FR-012 "no slice flags" invariant guarded.
4. **STOP and VALIDATE**: Run the real-data smoke (an early version of T025) against a real long Claude export. This is the SC-001 validation.
5. Optionally ship US1 as a v0.4.0 release here.

### Incremental Delivery

1. Setup + Foundational → checkpoint module exists and is tested.
2. US1 → multi-checkpoint synthesis + auto-resume + no-op-on-complete + all error paths + budget + flag-rejection. Ship.
3. US2 → per-invocation cap. Ship.
4. Polish → gate, real-data smoke, docs note.

### Notes on file conflicts

- `src/checkpoint.py` is touched only in Phase 2 (T002–T006). After Phase 2 it is stable; T007 only reads it (tests). US1/US2 import from it but do not modify it.
- `src/orchestrator.py` accumulates changes through T013 → T014 → T015 → T016 → T022. Sequential because all touch the same file.
- `src/logger.py` is touched only in T008. US2 does not modify it.
- `tests/test_orchestrator.py` accumulates assertions across T017, T018, T019, T020, T021, T023. Disjoint test functions; sequential commits.
- `tests/test_checkpoint.py` is created only in T007.
- Agent prompts (`.claude/agents/historian.md`, `.claude/agents/synthesis.md`) are each touched once (T009, T010 respectively).

## Notes

- Constitution: One new `src/` file (`src/checkpoint.py`) is justified in `plan.md` Complexity Tracking. No new dependencies. All new data shapes are Pydantic v2 `BaseModel` with `ConfigDict(strict=True)`.
- Per FR-013, the orchestrator path for a small single-checkpoint conversation must be observationally identical to the pre-feature pipeline — verify this with T017(e) before declaring US1 done.
- Per SC-002, the resume test must verify "zero re-invocations of Synthesis for any already-processed exchange" from session log data, not from agent prompts alone — see T017(b).
- Real-data smoke (T025) is the SC-001 validation. Reserve a real long Claude export for this; document the conversation ID and pre/post behavior so the result is reproducible.
- Tests are first-class deliverables here, not optional. The spec's seventeen FRs (FR-001..FR-017) and seven SCs each map to at least one test assertion.
- The split of US1 tests across T017 (happy paths), T018 (hash/index/schema/malformed errors), T019 (resume errors), T020 (FR-012 absence), and T021 (FR-015 budget) gives finer per-PR commit granularity than a single mega-task and isolates failures during implementation.
