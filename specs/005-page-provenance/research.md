# Phase 0 Research: Per-page provenance with shadow git and structured checkpoint JSON

The spec settled the architectural questions (per-conversation subdirectory, additive schema, checkpoint_id as join key, separation from session log, single-writer assumption). This document resolves the remaining implementation-level questions called out in the Technical Context so Phase 1 (data-model.md, contracts/, quickstart.md) has no `NEEDS CLARIFICATION` left.

---

## R1. Git invocation: `subprocess` vs GitPython

**Decision**: `subprocess` from stdlib via `subprocess.run([...], check=False, capture_output=True, text=True)`.

**Rationale**: Constitution Anti-Slop §Dependency Discipline rejects architecture dependencies. We need exactly four git operations (`git init`, `git -C add`, `git -C commit`, optionally `git --version` for the availability check). Each is one subprocess call; the surface area is tiny. GitPython adds a ~1 MB package, a Python-level object model around git, and a maintenance dependency the project does not otherwise have. Subprocess is already used elsewhere in the test toolchain and is well-understood. No dependency added.

**Alternatives considered**:
- `GitPython` (`pip install GitPython`): rejected. Adds a new dependency that exists to abstract git, when we only need four shell commands. Would also require justifying as an architecture dep in the Complexity Justification Table.
- `pygit2` (libgit2 bindings): rejected. Requires a C extension and external library, far heavier than the use case warrants.
- Don't invoke git; write our own minimal versioning (`cp -a` into timestamped directories): rejected. Re-implements git poorly; loses `git log -p` and `git blame` semantics, which ARE the user value of US2.

**Implementation note**: Per-call git committer identity passed via `-c user.email=insightmesh@local -c user.name=InsightMesh` so the orchestrator never reads or writes the user's global git config. Working directory pinned via `-C <vault>/InsightMesh/.history/` to avoid surprising operations on whatever cwd the CLI happened to be invoked from.

---

## R2. Frontmatter parse + merge: PyYAML direct vs `python-frontmatter`

**Decision**: PyYAML (already pinned via Spec 002) used directly, with a ~30-line splitter helper inside `src/history.py`.

**Rationale**: The operation we need is mechanical: read a page, split the `---\n...\n---\n` block from the body, parse the block as YAML into a dict, merge the `provenance:` key cumulatively, dump the dict back to YAML, reassemble the file, write atomically. `python-frontmatter` is a thin wrapper around `PyYAML` that provides a `Post` object model and a stream-based `loads/dumps` API; it solves "easier API" not "missing capability." For a single internal call site, the wrapper does not pay for its added dependency footprint per Anti-Slop §Rule of Three (we have one call site, not three). PyYAML's `safe_load` + `safe_dump` covers our needs.

**Alternatives considered**:
- `python-frontmatter`: rejected as discussed. Would be a re-evaluation if we later add a second call site (e.g., a viewer that has to read provenance back) and the wrapper's `Post` ergonomics start to pay off.
- Hand-rolled regex for the `---` block: considered, slightly simpler than the split helper but more fragile (multi-line YAML with `---` in body content can fool naive regexes). Use a small explicit splitter that scans only the first two `---` markers at line boundaries.
- AST-aware Markdown editor (e.g., MCPVault internally): not applicable here. MCPVault is an agent-side write path. This is post-Editor orchestrator Python; calling MCPVault from Python would re-invoke the SDK and inflate runtime without benefit.

**Implementation note**: Write the merged file atomically using `tempfile.NamedTemporaryFile(dir=<page-parent>, delete=False)` + `os.replace`, mirroring the Spec 004 cursor write pattern. This prevents a half-written page if the process dies mid-write.

---

## R3. Provider derivation from the echomine adapter

**Decision**: Tag `provider` at the point the adapter is selected in `src/exports.py:detect_adapter`. The function already chooses between echomine's Anthropic and OpenAI adapter classes; thread the chosen provider tag through `extract_conversation` and stash it in `ChatTranscript.metadata["provider"]`. For Spec 001 flat-array transcripts (no echomine adapter involved), `provider` is `None`.

**Rationale**: We already know the provider at adapter-selection time. Adding a tag at the source is one line of code per branch; deriving it later would require either re-running adapter selection or pattern-matching the conversation shape downstream. The check is already centralized in `detect_adapter`, so the tag stays local to one function.

**Alternatives considered**:
- Inspect `Conversation.provider` (if echomine exposes one): considered, but not relied on. echomine 1.5.0 may or may not surface a typed provider field across both adapters; depending on it introduces version coupling. Tag at adapter selection so the source of truth is in our code.
- Sniff `models_used` strings (`claude-*` vs `gpt-*`): rejected as fragile, and breaks for Claude exports where `models_used == []`.

**Implementation note**: The orchestrator does NOT introduce a `Provider` Enum; the value is a string literal `"anthropic"` | `"openai"` | `None`. Pydantic v2 `Literal[...] | None` typing on the model field gives compile-time exhaustiveness without an Enum class.

---

## R4. Per-message identifiers from echomine 1.5.0

**Decision**: `extract_conversation` builds an in-memory `dict[int, ExchangeMessageIds]` keyed by the exchange index used downstream by Spec 004, where `ExchangeMessageIds` is a small Pydantic record carrying `user_message_id: str | None` and `assistant_message_id: str | None`. Stored as `ChatTranscript.metadata["exchange_message_ids"]`. The orchestrator's post-Editor step looks up entries by `exchange_indices` to populate `CheckpointRecord.exchanges[*].user_message_id` and `assistant_message_id`.

**Rationale**: `ChatTranscript.exchanges` already exists as the orchestrator's per-exchange surface (with `index` already computed). Putting the per-message id map under `metadata["exchange_message_ids"]` keeps the existing `Exchange` Pydantic model unchanged (Minimal-Diff per constitution), and the orchestrator just dereferences the map at write time. For Spec 001 flat-array transcripts that lack `Message.id`, the map is empty and the per-exchange record's identifiers are written as `null`.

**Alternatives considered**:
- Add `user_message_id` and `assistant_message_id` fields to the `Exchange` model itself: rejected for Minimal-Diff. Per-message ids are only consumed by provenance writes; surfacing them on every exchange in memory inflates the shared shape without payoff.
- Resolve identifiers lazily by re-walking the echomine `Conversation` at write time: rejected. Would require re-reading the source export, defeating the streaming-then-process flow Spec 003/004 established and adding I/O per checkpoint.

**Implementation note**: A trivial Pydantic record `ExchangeMessageIds(BaseModel)` with `ConfigDict(strict=True)` is defined inside `src/history.py` alongside the other models. It is internal scaffolding, not a public API.

---

## R5. Pydantic schema posture: strict on write, permissive on read

**Decision**: Two model families for the checkpoint JSON. The write-side models (`CheckpointRecord`, `EditorDecisionRecord`, `ExchangeRecord`, `ConversationRecord`) use `ConfigDict(strict=True, extra="forbid")` matching the rest of the codebase. The read-side variants (used by tests today, by Phase B migration tools tomorrow) use `ConfigDict(strict=True, extra="allow")` so checkpoint JSON files written by a future version with additional optional fields still parse against the current model.

**Rationale**: FR-002 mandates additive forward-compatibility within `schema_version=1`. Strict-extras on write keeps our own code honest (a typo in a field name fails fast); permissive-extras on read honors the spec rule that permanent records must outlive readers. The same Pydantic model class can be subclassed with overridden `model_config` to express both postures without duplicating field definitions.

**Alternatives considered**:
- One permissive model used for both read and write: rejected. Write-side `extra="allow"` would silently accept typos on the orchestrator side, defeating the strict-typing principle elsewhere in the codebase.
- One strict model used for both: rejected. Would refuse to read a future v1+ JSON with an added optional field, which is exactly the spec's anti-pattern (Spec 004 cursor's strict-refuse posture is appropriate THERE because the cursor is regeneratable; the checkpoint JSON is permanent and IS NOT).

**Implementation note**: Pattern is:

```python
class CheckpointRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    # ... fields ...

class CheckpointRecordRead(CheckpointRecord):
    model_config = ConfigDict(strict=True, extra="allow")
```

Tests cover both postures; production write path uses `CheckpointRecord`, the fixture-based forward-compat test uses `CheckpointRecordRead`.

---

## R6. Atomic write pattern

**Decision**: Reuse Spec 004's atomic write pattern verbatim, factored as a small helper in `src/history.py`: write to `tempfile.NamedTemporaryFile(dir=<target-parent>, delete=False, suffix=".tmp")`, fsync, close, then `os.replace(tmp_path, target_path)`. Target parent directory is created with `mkdir(parents=True, exist_ok=True)` before the temp file is opened.

**Rationale**: Already proven in Spec 004 for the cursor file. Same correctness properties (crash mid-write leaves either old contents or new contents on disk, never a half-written file). Keeps the pattern consistent across modules; a reader can verify either module by reading the same helper shape.

**Alternatives considered**:
- Write directly to the target path: rejected for the same reasons Spec 004 rejected it (half-written JSON would be unparseable on next read).
- File locking via `fcntl`: not needed under the single-writer assumption (Assumptions section); deferred to a hypothetical future spec that lifts that assumption.

---

## R7. Shadow repo coexistence with a user-managed git at the vault root

**Decision**: Do nothing to the user's vault-level git. The orchestrator's writes are confined to `<vault>/InsightMesh/.history/`. If the user maintains their own git at the vault root, the shadow repo at `.history/.git/` appears to that outer git as a nested directory containing a `.git/` of its own; git treats it as untracked content with an embedded repo. The user can decide whether to add `InsightMesh/.history/` to their vault-level `.gitignore`. Documented in `quickstart.md` and the README.

**Rationale**: Constitution Architecture Principles §Single Responsibility plus Anti-Slop §Surgical Changes both argue against modifying user-owned files (`.gitignore` at the vault root is user-owned). Two coexisting git repos at different levels is a well-understood git pattern (it is the same situation as a submodule that hasn't been registered). The orchestrator's commit step pins `-C <vault>/InsightMesh/.history/` so it never accidentally stages user-vault files into the shadow repo.

**Alternatives considered**:
- Write `<vault>/.gitignore` with `InsightMesh/.history/`: rejected as touching user files outside the InsightMesh namespace.
- Write `<vault>/InsightMesh/.gitignore` to keep the user's vault git from picking up `.history/`: same concern, plus it's redundant — `.history/.git/` already prevents the outer git from descending.
- Refuse to init the shadow repo if a vault-level git is detected: rejected as user-hostile. The two layers can coexist cleanly.

---

## R8. Page snapshot mechanism

**Decision**: `shutil.copy2(source, dest)` from `<vault>/InsightMesh/<page>.md` to `<vault>/InsightMesh/.history/pages/<sanitized-slug>.md`. `shutil.copy2` preserves mtime and most metadata, so the snapshot reflects the page as Editor produced it (not as the orchestrator stat'd it later).

**Rationale**: Simpler than open/read/write, preserves mtime which is occasionally useful for forensics, and is a single stdlib call. No Pydantic models needed; pure filesystem operation.

**Alternatives considered**:
- Read + atomic write: not necessary; the destination is a new file in a directory the orchestrator owns. Atomicity at the page-snapshot granularity is provided by `os.replace` semantics of the underlying `copy2` call, plus the wrapping git commit's all-or-nothing posture (a half-staged copy is discarded if `git commit` fails).
- Hard-link the snapshot to save disk: rejected. Subsequent Editor updates to the source page would silently mutate the historical snapshot. Provenance fidelity requires an independent copy.

---

## R9. Sanitization of page slugs in `.history/pages/`

**Decision**: Reuse the existing slug-sanitization helper from `src/wiki.py` (the same function Editor uses to derive on-disk page filenames). The orchestrator gets a vault-relative file path from `EditorDecision.file` and snapshots to `<sanitized-slug>.md` under `.history/pages/`. If the spec edge case "filesystem-unsafe characters" applies, the existing helper already addresses it for the source page; the snapshot inherits that sanitization for free.

**Rationale**: Existing path is already filesystem-safe (or it would have failed at Editor's write step). Reusing the helper avoids drift between source and snapshot naming. No new code.

**Alternatives considered**:
- Re-sanitize defensively in `src/history.py`: rejected as duplicative; if the helper changes, both call sites must update in lockstep, which is exactly the kind of accidental coupling Anti-Slop §Surgical Changes warns against.

---

## R10. Empty-checkpoint behavior

**Decision**: When `EditorOutput.results` contains no created/updated pages (Synthesis emitted nothing useful, or every draft was a `skipped` decision targeting non-existent pages), still write the checkpoint JSON (so `cp-<NNN>.json` exists and downstream queries can answer "this checkpoint produced nothing"), but DO NOT init/commit the shadow repo and DO NOT update any frontmatter (no pages to update). Init is also skipped on empty-result so we don't leave an empty `.history/` repo dangling on a never-productive run.

**Rationale**: The JSON is the permanent record; recording "this checkpoint produced no pages" is useful for observability. The git commit only makes sense if there are pages to snapshot, and an empty commit would pollute the history view. This matches the edge case behavior described in the spec.

**Alternatives considered**:
- Skip writing the JSON entirely on empty result: rejected. The JSON's role as the system of record means downstream tools should be able to scan `cp-*.json` files and reconstruct what every checkpoint did, including "nothing." A skipped write would leave a hole in the numbering.
- Always commit, with an empty commit on no-results: rejected. Pollutes `git log` with non-informative entries.

---

## R11. Test fixtures for forward-compatibility

**Decision**: A single hand-authored fixture at `tests/fixtures/provenance_cp_001.json` that includes (a) every required field for `schema_version=1`, (b) an extra unknown top-level field `"future_field_x": "ignored"`, and (c) an extra unknown sub-field inside `editor.decisions[0]`. The test parses this fixture with `CheckpointRecordRead` and asserts that parsing succeeds, the known fields are populated, and the unknown fields are accessible via `model_extra` (per Pydantic v2 semantics).

**Rationale**: Verifies FR-002 (additive forward-compatibility) with a single concrete artifact that future-us cannot delete without also visibly breaking the test suite. Minimal fixture scope (one file) per Minimal-Diff.

**Alternatives considered**:
- Property-based test that randomly injects extra fields: rejected as overkill for a contract that has exactly one rule ("tolerate extras"). One concrete fixture is more debuggable.

---

## R12. Constitution-Standards alignment on the `extra="allow"` deviation

**Decision**: Document the deviation inline in `src/history.py` and in the data-model.md (Phase 1). No Complexity Justification Table entry needed because the deviation is spec-mandated (FR-002), narrow (one specific subclass of one model family), and tested.

**Rationale**: Constitution v1.1.4 §Project Standards mandates `ConfigDict(strict=True)` but does NOT mandate `extra="forbid"` specifically; it mandates strict typing. `extra="allow"` is a permissive-on-read posture distinct from strict typing. The codebase elsewhere defaults to `extra="forbid"` as good hygiene (and Spec 004 enforces it on the cursor), but the spec's forward-compatibility rule supersedes that default for this artifact. Calling this out in the model docstring keeps future readers from "fixing" it.

---

## Summary of decisions

| ID | Topic | Decision |
|----|-------|----------|
| R1 | Git invocation | `subprocess.run`; no new dep |
| R2 | Frontmatter parse/merge | PyYAML direct; ~30-line splitter |
| R3 | Provider derivation | Tag at `detect_adapter`; threaded into `ChatTranscript.metadata` |
| R4 | Per-message identifiers | `ExchangeMessageIds` map under `metadata`; per-exchange lookup at write time |
| R5 | Pydantic posture | Strict-extras on write, allow-extras on read (subclass) |
| R6 | Atomic write | Reuse Spec 004 helper pattern (tempfile + os.replace) |
| R7 | Vault-level git | Do not modify user files; document coexistence |
| R8 | Page snapshot | `shutil.copy2`; no hard-link |
| R9 | Slug sanitization | Reuse existing `src/wiki.py` helper |
| R10 | Empty checkpoint | Write JSON; skip shadow-repo init and commit |
| R11 | Forward-compat fixture | One hand-authored fixture with extras |
| R12 | `extra="allow"` deviation | Documented inline; no Complexity Table entry needed |

All decisions are reflected in `data-model.md` (Phase 1) and `contracts/history-orchestrator.md` (Phase 1).
