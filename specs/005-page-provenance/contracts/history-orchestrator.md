# Contract: Orchestrator ↔ `src/history.py` ↔ on-disk artifacts

This is an internal contract. There is no public API or wire protocol introduced; the only external surfaces are the on-disk JSON files and the wiki-page frontmatter, both of which are specified in `data-model.md`. This document pins down the function shapes, error semantics, and call sequence the orchestrator uses to invoke the history module.

## Module surface (`src/history.py`)

### Custom exceptions

```python
class ShadowRepoUnavailable(Exception):
    """`git` not found on PATH, or `git --version` failed.
    Non-fatal at the run level — orchestrator logs and continues per FR-015.
    """

class ShadowRepoCommitFailed(Exception):
    """`git add` or `git commit` returned non-zero in the shadow repo.
    Non-fatal at the run level — orchestrator logs and continues per FR-016.
    Includes the captured stderr in the exception message for diagnostics.
    """

class FrontmatterParseFailed(Exception):
    """Existing page frontmatter is not parseable YAML.
    Non-fatal at the run level — orchestrator logs (naming the page) and continues per FR-010.
    """
```

### Public functions

All functions are synchronous (no I/O bound work big enough to justify async) and side-effect-explicit (every write call returns the path it wrote so the caller can log it).

```python
def compute_checkpoint_payload(
    *,
    checkpoint_number: int,
    transcript: ChatTranscript,
    exchanges_processed: list[Exchange],
    editor_output: EditorOutput,
    session_log_path: Path,
    cursor_path: Path,
    vault_root: Path,
) -> CheckpointRecord:
    """Build the CheckpointRecord from in-memory orchestrator state.
    Pure function — no I/O. Raises ValidationError if inputs violate model invariants.
    """
```

```python
def write_checkpoint_metadata(
    *,
    history_dir: Path,
    conversation_subdir: str,  # the conv-id-or-"_flat" segment
    record: CheckpointRecord,
) -> Path:
    """Atomically write `<history_dir>/checkpoints/<conversation_subdir>/<record.checkpoint_id>.json`.
    Creates parent directories as needed. Returns the absolute path of the written file.
    Atomic write semantics per R6 (tempfile + os.replace).
    Immutability per FR-001a: if the target path already contains a fully-written
    cp-<NNN>.json, the rename fails (os.replace would not, but we explicitly check
    via os.path.exists before the rename and raise FileExistsError when occupied);
    orchestrator catches and logs the collision per FR-016 without failing the run.
    Determines deterministic JSON ordering per FR-001b: serialization sorts
    lists-of-strings (conversations, pages_*) ascending, sorts lists-of-integers
    (exchange_indices) ascending, and preserves insertion order for exchanges
    and editor.decisions.
    Datetime fields serialize as ISO 8601 UTC with the `Z` suffix per FR-001.
    """
```

```python
def merge_page_provenance(
    *,
    page_path: Path,  # absolute path to the wiki page on disk
    incoming: ProvenanceFrontmatter,
) -> Path:
    """Merge the `provenance:` block into the page's YAML frontmatter using the cumulative rules from data-model.md.
    Preserves all other frontmatter keys verbatim (FR-011).
    Returns the page path on success.
    Raises FrontmatterParseFailed if the existing frontmatter is unparseable YAML;
    orchestrator catches and logs per FR-010.
    Raises FileNotFoundError if the page has been deleted between Editor's write
    and this call (edge case from spec); orchestrator catches and logs the
    "[provenance] page disappeared before snapshot: <path>" line per the
    page-disappeared edge case.
    Write is atomic at the page-file granularity per FR-011: uses
    tempfile.NamedTemporaryFile in the page's parent directory + os.replace
    so no half-merged page is observable to a concurrent reader or crash recovery.
    """
```

```python
def snapshot_page(
    *,
    source_page: Path,        # absolute path under <vault>/InsightMesh/
    history_dir: Path,        # absolute path to <vault>/InsightMesh/.history/
    sanitized_slug: str,      # from the existing wiki.py helper (R9)
) -> Path:
    """Copy the page to `<history_dir>/pages/<sanitized_slug>.md` via shutil.copy2.
    Returns the destination absolute path.
    """
```

```python
def init_shadow_repo(history_dir: Path) -> None:
    """Idempotent. Handles all three states defined by FR-012:
      (a) history_dir does not exist → mkdir -p, then `git -C <history_dir> init`
      (b) history_dir exists AND <history_dir>/.git/ exists → return immediately
      (c) history_dir exists AND <history_dir>/.git/ does NOT exist (e.g., user
          deleted .git/ manually) → run `git -C <history_dir> init` to re-init;
          existing files are preserved (git init is non-destructive)
    MUST NOT reset, reconfigure, or otherwise modify an existing repository's configuration.
    Raises ShadowRepoUnavailable if `git` is not on PATH or the init command returns non-zero.
    """
```

```python
def commit_checkpoint(
    *,
    history_dir: Path,
    checkpoint_id: str,           # "cp-002"
    conversation_id: str | None,  # None becomes the literal "_flat" in subject
    conversation_subdir: str,
    decisions: list[EditorDecisionRecord],
    created: list[str],
    updated: list[str],
) -> None:
    """Stage and commit the new checkpoint JSON plus all touched page snapshots.
    Builds the commit message per FR-014:
        subject: [InsightMesh checkpoint:<checkpoint_id> conversation:<conv_id_or_"_flat">] <N> pages updated, <M> created
        body:    Metadata: checkpoints/<conversation_subdir>/<checkpoint_id>.json
                 Pages touched:
                   - <file> (<action>, confidence:<confidence>)
                   - ...
    Invokes git via subprocess.run with -c user.email=insightmesh@local -c user.name=InsightMesh.
    Raises ShadowRepoCommitFailed on non-zero exit; orchestrator catches and logs per FR-016.
    """
```

```python
def is_git_available() -> bool:
    """Probe by running `git --version` once with a short timeout.
    Cached at module scope after the first call (per-process).
    """
```

## Call sequence inside `src/orchestrator.py:run_batch`

The provenance step lives in `run_batch`, after `_execute_pipeline` succeeds and Editor returned a non-failed `EditorOutput`, and BEFORE the existing Spec 004 cursor save. Pseudocode (omitting unrelated lines):

```python
# ... existing _execute_pipeline call, returns editor_output ...

if editor_output is not None and not editor_output_failed(editor_output):
    try:
        _write_provenance(
            vault_root=vault_root,
            transcript=transcript,
            exchanges_processed=slice_,
            editor_output=editor_output,
            session_log_path=session_log_path,
            cursor_path=cursor_path,
            checkpoint_number=next_checkpoint_number,
        )
    except Exception as exc:
        # Provenance is best-effort. Log to stderr; never re-raise.
        print(f"[provenance] write failed: {exc}", file=sys.stderr)

# ... existing cursor write ...
```

`_write_provenance` (private helper in `src/orchestrator.py`, ~30 lines):

```python
def _write_provenance(
    *,
    vault_root: Path,
    transcript: ChatTranscript,
    exchanges_processed: list[Exchange],
    editor_output: EditorOutput,
    session_log_path: Path,
    cursor_path: Path,
    checkpoint_number: int,
) -> None:
    history_dir = vault_root / "InsightMesh" / ".history"
    conv_subdir = _sanitize_conversation_subdir(transcript.metadata.get("conversation_id"))

    record = history.compute_checkpoint_payload(
        checkpoint_number=checkpoint_number,
        transcript=transcript,
        exchanges_processed=exchanges_processed,
        editor_output=editor_output,
        session_log_path=session_log_path,
        cursor_path=cursor_path,
        vault_root=vault_root,
    )

    history.write_checkpoint_metadata(
        history_dir=history_dir,
        conversation_subdir=conv_subdir,
        record=record,
    )

    if not record.results.pages_created and not record.results.pages_updated:
        return  # R10: empty checkpoint — skip frontmatter + git work

    for decision in record.editor.decisions:
        if decision.action == "skipped":
            continue
        page_path = vault_root / decision.file
        try:
            incoming = _build_provenance_for(decision, record, conv_subdir)
            history.merge_page_provenance(page_path=page_path, incoming=incoming)
        except history.FrontmatterParseFailed as exc:
            print(f"[provenance] frontmatter parse failed for {page_path}: {exc}",
                  file=sys.stderr)
            continue
        history.snapshot_page(
            source_page=page_path,
            history_dir=history_dir,
            sanitized_slug=wiki.sanitize_slug(decision.file),
        )

    if not history.is_git_available():
        print("[provenance] git not on PATH; skipping shadow-repo commit", file=sys.stderr)
        return

    try:
        history.init_shadow_repo(history_dir)
        history.commit_checkpoint(
            history_dir=history_dir,
            checkpoint_id=record.checkpoint_id,
            conversation_id=record.conversation.id,
            conversation_subdir=conv_subdir,
            decisions=record.editor.decisions,
            created=record.results.pages_created,
            updated=record.results.pages_updated,
        )
    except history.ShadowRepoUnavailable as exc:
        print(f"[provenance] shadow repo unavailable: {exc}", file=sys.stderr)
    except history.ShadowRepoCommitFailed as exc:
        print(f"[provenance] commit failed: {exc}", file=sys.stderr)
```

`_sanitize_conversation_subdir(conv_id_or_none)` returns the conversation id with filesystem-unsafe characters replaced by `-` (reusing the Spec 004 cursor's sanitization), OR the literal `_flat` when conv_id is None.

`_build_provenance_for(decision, record, conv_subdir)` constructs a `ProvenanceFrontmatter` from this checkpoint's decision plus the prior frontmatter state read from the page (see `merge_page_provenance` docstring for the prior-state lookup).

## Error semantics summary

All stderr lines follow the `[provenance] ` prefix contract from FR-016a (single-line, machine-greppable).

| Error | Source | Run-level effect | Logging |
|---|---|---|---|
| `ValidationError` | `compute_checkpoint_payload` | Caught at `_write_provenance`; logged; provenance skipped; cursor still advances. | stderr: `[provenance] payload validation failed: ...` |
| `OSError` (disk full, permissions) on JSON write | `write_checkpoint_metadata` | Caught at `_write_provenance`; logged with affected path; provenance skipped; cursor still advances. Covered by FR-015 expanded clause. | stderr: `[provenance] write failed: <path>: <os error>` |
| `FileExistsError` (cp-NNN.json already at target path) | `write_checkpoint_metadata` per FR-001a | Caught at `_write_provenance`; logged with checkpoint id; provenance skipped; cursor still advances. | stderr: `[provenance] checkpoint already exists: <path>` |
| `FrontmatterParseFailed` | `merge_page_provenance` per-page | Caught per-page inside `_write_provenance`; logged with page name; that page's frontmatter unchanged; loop continues. | stderr: `[provenance] frontmatter parse failed for <page>: <yaml error>` |
| `FileNotFoundError` (page disappeared) | `merge_page_provenance` or `snapshot_page` per-page | Caught per-page inside `_write_provenance`; logged with page name; that page's frontmatter + snapshot are skipped; checkpoint JSON still records Editor's decision for that page; loop continues. | stderr: `[provenance] page disappeared before snapshot: <page>` |
| `ShadowRepoUnavailable` | `init_shadow_repo` or git-availability probe | Caught at `_write_provenance`; logged; JSON + frontmatter still landed; cursor still advances. | stderr: `[provenance] git not on PATH; skipping shadow-repo commit` |
| `ShadowRepoCommitFailed` | `commit_checkpoint` | Caught at `_write_provenance`; logged with captured git stderr; JSON + frontmatter still landed; cursor still advances. Next successful commit sweeps up orphaned snapshots per FR-016. | stderr: `[provenance] commit failed: <git stderr>` |
| Non-JSON-serializable signal value | `compute_checkpoint_payload` per FR-005 | Coerced inline via `repr()`; record still written; logged with offending key. | stderr: `[provenance] signal value not JSON-serializable: <key>; coerced via repr()` |
| Bare `Exception` | anywhere unexpected | Top-level `except Exception` in the `try/except` wrapper around `_write_provenance` catches it. | stderr: `[provenance] write failed: ...` |

No error in this code path can escape `_write_provenance` to fail the run. The run's exit code is determined solely by the agent pipeline and the Spec 004 cursor write; a provenance failure NEVER changes the exit code (FR-015 expanded clause). This contract is enforced by the top-level `try / except Exception` wrapper, asserted by a unit test (`test_orchestrator.test_provenance_failure_does_not_fail_run`).

## Step ordering (FR-017)

The four bookkeeping steps execute in a fixed order; per-page failures isolate to that page without aborting the rest:

```
1. write_checkpoint_metadata(...)                       # one atomic write of cp-<NNN>.json
2. for each touched page:
       merge_page_provenance(page_path, incoming)       # per-page; isolated failure
3. for each touched page:
       snapshot_page(source_page, history_dir, slug)    # per-page; isolated failure
4. if is_git_available():
       init_shadow_repo(history_dir)                    # idempotent across all three states (FR-012)
       commit_checkpoint(...)                            # single commit; FR-016 fallback if fails
```

Process kill (SIGTERM/SIGINT) at any boundary leaves the on-disk state consistent at the file granularity per FR-021. No special recovery scan is required on next startup; the next run re-processes the unflushed checkpoint and re-writes the same `cp-<NNN>.json` (refused by FR-001a if already fully written).

Optional orphan detection on startup is permitted per FR-022 — implementations MAY scan `.history/pages/` for uncommitted snapshots and emit a single `[provenance] ` informational line summarizing what was found, but MUST NOT block or alter behavior based on the result.

## Inputs the orchestrator already has

| Need | Source already in scope |
|---|---|
| `ChatTranscript` | already loaded for Synthesis input |
| `exchanges_processed` | already computed by `pick_checkpoint_slice` for this checkpoint |
| `EditorOutput` (action, confidence, rationale, exchange_indices, signals per page) | already returned by `_execute_pipeline`, available in `_AgentCall.parsed_output` for Editor |
| `session_log_path` | already determined before the session log is written |
| `cursor_path` | already determined by `_cursor_path_for(...)` (existing helper) |
| `vault_root` | already determined from `VAULT_PATH` / CLI `--vault` |
| `checkpoint_number` | already computed (the value the cursor is about to advance to) |
| per-exchange `user_message_id` / `assistant_message_id` | added to `ChatTranscript.metadata["exchange_message_ids"]` by the `src/exports.py` modification |
| `provider` | added to `ChatTranscript.metadata["provider"]` by the `src/exports.py` modification |
| `models_used` | added to `ChatTranscript.metadata["models_used"]` by the `src/exports.py` modification |

No new orchestrator state is introduced; the provenance step is a pure read from existing state plus the write helpers above.

## Tests that verify this contract

(Full list in `tasks.md`; key contract tests called out here.)

- `tests/test_history.py::test_compute_checkpoint_payload_round_trip` — round-trips a `CheckpointRecord` through write + read, asserts equality.
- `tests/test_history.py::test_merge_page_provenance_cumulative` — drives the merge twice on the same page and asserts the cumulative math on a fixture page file.
- `tests/test_history.py::test_init_shadow_repo_idempotent` — calls `init_shadow_repo` twice; asserts the second call is a no-op.
- `tests/test_history.py::test_commit_message_format` — asserts the subject + body match FR-014.
- `tests/test_history.py::test_forward_compat_read_tolerates_extras` — uses the fixture from R11; asserts `CheckpointRecordRead` parses successfully.
- `tests/test_orchestrator.py::test_provenance_writes_after_successful_checkpoint` — end-to-end mock pipeline; asserts the on-disk artifacts exist and match expectations.
- `tests/test_orchestrator.py::test_provenance_failure_does_not_fail_run` — patches `history.write_checkpoint_metadata` to raise; asserts the run still exits 0 and cursor advances.
- `tests/test_orchestrator.py::test_no_git_fallback_path` — patches `history.is_git_available` to return False; asserts the JSON + frontmatter still landed, no commit attempted, exit 0.
