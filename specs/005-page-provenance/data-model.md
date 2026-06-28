# Phase 1 Data Model: Per-page provenance

All models live in `src/history.py` and are `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)`. Write-side models use `extra="forbid"` (default project posture); the read-side subclass uses `extra="allow"` per R5 / FR-002.

## Conventions

- Field names match the on-disk JSON exactly; Pydantic v2 does the serialization.
- All datetimes are timezone-aware UTC, serialized as ISO 8601 with `Z` suffix (mirroring Spec 004).
- All paths in JSON are vault-relative POSIX strings (forward slashes), even on platforms with backslash separators, so checkpoint JSONs are portable across OS.
- `schema_version` is an integer starting at `1`. Additive evolution (new optional fields) stays within `schema_version=1`; major bumps are deferred indefinitely per FR-002.

---

## Write-side models (used by orchestrator)

### `ConversationRecord`

Represents the source conversation for one checkpoint.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str \| None` | yes | Conversation identifier from echomine, or `None` for Spec 001 flat-array transcripts that carry no id. |
| `export_path` | `str` | yes | Absolute path of the export file as the orchestrator saw it (matches `Cursor.export_path` from Spec 004). |
| `provider` | `Literal["anthropic", "openai"] \| None` | yes | Tagged at `src/exports.py:detect_adapter` per R3. `None` for flat-array transcripts. |
| `models_used` | `list[str]` | yes | From `Conversation.models_used` (echomine 1.5.0). Empty list when the upstream parser does not surface model identifiers (Claude exports today; flat-array always). |
| `transcript_hash` | `str` | yes | Same SHA-256 hex digest used by Spec 004 cursor. Sourced from the cursor at write time so the two artifacts agree by construction. |

### `ExchangeRecord`

One entry per exchange processed in this checkpoint.

| Field | Type | Required | Notes |
|---|---|---|---|
| `index` | `int` (≥ 0) | yes | Matches the exchange index Spec 004 uses for cursor positioning. |
| `user_message_id` | `str \| None` | yes | Looked up from `ChatTranscript.metadata["exchange_message_ids"]` per R4. `None` for flat-array transcripts. |
| `assistant_message_id` | `str \| None` | yes | Same source as above; `None` when missing. |

### `EditorDecisionRecord`

One entry per page Editor touched in this checkpoint.

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | `str` | yes | Page filename relative to the vault root (e.g., `InsightMesh/Capitalism's Origins.md`). |
| `action` | `Literal["created", "updated", "skipped"]` | yes | Editor's action for this page (sourced from `EditorDecision.action` in `src/logger.py`). |
| `confidence` | `Literal["high", "medium", "low"]` | yes | Editor's confidence (sourced from `EditorDecision.confidence`). |
| `rationale` | `str` | yes | Free-text rationale Editor produced (sourced from `EditorDecision.rationale`). |
| `exchange_indices` | `list[int]` | yes | Per-exchange contribution list from `EditorDecision.exchange_indices`. May be empty if Editor's parse was recoverable-failure. |
| `signals` | `dict[str, Any]` | yes | The full signals dict Editor used. Sourced from `EditorDecision.signals` and serialized as-is. Pydantic's `Any` is permitted here exclusively because the signals dict is opaque pass-through; the orchestrator does not interpret it. Marked with `# noqa` plus an inline comment in code so reviewers understand the deviation from "no Any in public APIs". |

### `ResultsRecord`

Mirrors the Spec 004 `EditorOutput.results` categories.

| Field | Type | Required | Notes |
|---|---|---|---|
| `pages_created` | `list[str]` | yes | Vault-relative page filenames. |
| `pages_updated` | `list[str]` | yes | Same. |
| `pages_skipped` | `list[str]` | yes | Same. |

### `LinksRecord`

Convenience pointers to sibling artifacts; reads MUST not depend on these.

| Field | Type | Required | Notes |
|---|---|---|---|
| `session_log` | `str` | yes | Vault-relative POSIX path to the session log JSON written by Spec 001. |
| `cursor` | `str` | yes | Vault-relative POSIX path to the Spec 004 cursor file. |

### `CheckpointRecord`

The top-level write-side model.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `int` (`= 1`) | yes | Always `1` in this spec; bumps require migration tooling. |
| `checkpoint_id` | `str` | yes | Format `cp-<NNN>` zero-padded to 3 digits (e.g., `cp-002`). Derived from `checkpoint_number`. |
| `checkpoint_number` | `int` (≥ 1) | yes | Mirrors Spec 004 cursor's `checkpoint_number`. |
| `timestamp` | `datetime` (UTC) | yes | Write time. |
| `conversation` | `ConversationRecord` | yes | |
| `exchanges` | `list[ExchangeRecord]` | yes | At least one entry. |
| `editor` | object with `decisions: list[EditorDecisionRecord]` | yes | The `editor.decisions` shape follows the spec example structure (one extra level of nesting under `editor` reserves room for future fields like `editor.errors` without a schema bump). |
| `results` | `ResultsRecord` | yes | |
| `links` | `LinksRecord` | yes | |

`model_config = ConfigDict(strict=True, extra="forbid")`.

### `ProvenanceFrontmatter`

The cumulative `provenance:` block written into a wiki page's YAML frontmatter.

| Field | Type | Required | Notes |
|---|---|---|---|
| `latest_checkpoint` | `str` | yes | Vault-relative POSIX path to the just-written `cp-<NNN>.json` (e.g., `InsightMesh/.history/checkpoints/d126dc13-…/cp-002.json`). |
| `conversations` | `list[str]` | yes | Cumulative union of conversation ids that have ever touched this page. Empty list permitted when no identifier exists across any contributing conversation. |
| `latest_action` | `Literal["created", "updated"]` | yes | From this checkpoint's `EditorDecisionRecord.action` (never `"skipped"` because skipped pages do not get frontmatter updates). |
| `latest_confidence` | `Literal["high", "medium", "low"]` | yes | From this checkpoint's `EditorDecisionRecord.confidence`. |
| `total_edits` | `int` (≥ 1) | yes | Cumulative count of distinct checkpoints in which Editor touched this page. |
| `exchange_count` | `int` (≥ 0) | yes | Size of the cumulative union of distinct `exchange_indices` across all contributing checkpoints. |

`model_config = ConfigDict(strict=True, extra="forbid")`.

### `ExchangeMessageIds` (internal scaffolding)

Internal helper for ferrying message identifiers from `src/exports.py` to the orchestrator's write step. Stored in `ChatTranscript.metadata["exchange_message_ids"]` as `dict[int, ExchangeMessageIds]` keyed by exchange index.

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_message_id` | `str \| None` | yes | |
| `assistant_message_id` | `str \| None` | yes | |

`model_config = ConfigDict(strict=True, extra="forbid")`. Not serialized to disk; lives only in process memory.

---

## Read-side variant

```python
class CheckpointRecordRead(CheckpointRecord):
    model_config = ConfigDict(strict=True, extra="allow")
```

Used by tests today and by any future Phase B migration tool. Tolerates unknown top-level fields and unknown sub-fields per FR-002. The same approach is applied per-sub-model where forward-compatibility matters (e.g., `EditorDecisionRecordRead`, `ConversationRecordRead`). Sub-model read variants are introduced only as needed; the test fixture (R11) exercises one extra top-level field plus one extra sub-field to anchor the contract.

---

## State transitions

### `ProvenanceFrontmatter` cumulative merge

When the orchestrator processes a touched page for this checkpoint, the merge follows FR-008 / FR-009. Pseudocode:

```text
def merge(prior: ProvenanceFrontmatter | None, this: ProvenanceFrontmatter) -> ProvenanceFrontmatter:
    if prior is None:
        return this  # first time this page got a provenance block
    return ProvenanceFrontmatter(
        latest_checkpoint   = this.latest_checkpoint,
        latest_action       = this.latest_action,
        latest_confidence   = this.latest_confidence,
        conversations       = sorted(set(prior.conversations) | set(this.conversations)),
        total_edits         = prior.total_edits + 1,
        exchange_count      = len(set(prior_indices) | set(this_indices)),
    )
```

Where `prior_indices` is recovered indirectly: the orchestrator does NOT round-trip the full `exchange_indices` history through the page (that would bloat frontmatter unbounded); instead, when merging, it reads the prior `latest_checkpoint` pointer, opens the prior `cp-<NNN>.json`, and reads the page's prior `editor.decisions[*].exchange_indices` for the previous edit. If the prior pointer is missing or unparseable, `exchange_count` falls back to `prior.exchange_count + len(this_indices)` (an upper bound that may overcount but never undercount; documented).

### `CheckpointRecord` is immutable once written

The checkpoint JSON is never updated in place. Two scenarios that might suggest mutation are explicitly NOT supported:

1. Re-running a checkpoint with the same `checkpoint_number`: the existing Spec 004 cursor refuses to advance past a `complete` cursor and the `--retry` flow only applies to `failed` status. If a write somehow lands at an occupied path, the atomic write helper raises (the temp file rename fails) and the orchestrator logs to stderr per FR-016.
2. Patching an old checkpoint with new information: out of scope (rejected design alternative). Future fields land on future checkpoints.

### Shadow repo

State machine:

```
[ no .history dir ]  --(first successful non-empty checkpoint)-->  [ init'd git repo, 1 commit ]
[ init'd git repo, N commits ]  --(next successful non-empty checkpoint)-->  [ init'd git repo, N+1 commits ]
[ init'd git repo, N commits ]  --(empty-result checkpoint per R10)-->  [ unchanged ]
[ init'd git repo, N commits ]  --(git command fails per FR-016)-->  [ unchanged + stderr warning ]
```

---

## Validation rules

- `CheckpointRecord.checkpoint_id` MUST match `f"cp-{checkpoint_number:03d}"` (Pydantic `model_validator` enforces, per FR-001 single derivation rule).
- `CheckpointRecord.exchanges` MUST be non-empty (a checkpoint MUST cover at least one exchange).
- `ExchangeRecord.index` values within one `CheckpointRecord` MUST be unique.
- `ProvenanceFrontmatter.total_edits` MUST be ≥ 1 (a page with zero edits has no frontmatter block at all). Counts only `created` / `updated` actions; `skipped` actions do not increment per FR-009.
- `ProvenanceFrontmatter.exchange_count` MUST be ≥ 0 (zero is technically possible if every contributing decision had an empty `exchange_indices` due to recoverable parse failure).
- `ProvenanceFrontmatter.conversations` entries MUST be unique.

## Serialization rules (FR-001b deterministic ordering)

Pydantic's default `model_dump_json` does not sort lists. The write-side helper in `src/history.py` MUST sort ordered collections before serialization per FR-001b:

- `CheckpointRecord.exchanges` — preserve insertion order (the order processed by the orchestrator).
- `CheckpointRecord.editor.decisions` — preserve insertion order.
- `ResultsRecord.pages_created`, `pages_updated`, `pages_skipped` — sorted ascending (strings).
- `EditorDecisionRecord.exchange_indices` — sorted ascending (integers).
- `ProvenanceFrontmatter.conversations` — sorted ascending (strings).
- `ProvenanceFrontmatter.exchange_count` is a scalar derived from a set; no list ordering applies.

Deterministic ordering makes `cp-<NNN>.json` files diff-friendly across runs and lets `jq` queries assume stable iteration order.

## Immutability rules (FR-001a)

Once a `cp-<NNN>.json` file is written, the orchestrator MUST NOT overwrite, patch, or delete it. `write_checkpoint_metadata` enforces this by checking `os.path.exists(target)` before the atomic rename and raising `FileExistsError` on collision; the orchestrator catches this and logs to stderr per FR-016 / FR-016a without failing the run.

---

## Cross-references to spec

| Model / Field | Spec source |
|---|---|
| `CheckpointRecord` shape | FR-001, FR-002, FR-005, FR-006, FR-007 |
| `CheckpointRecord` immutability rule | FR-001a |
| `CheckpointRecord` serialization ordering | FR-001b |
| `ConversationRecord` shape | FR-003 |
| `ExchangeRecord` shape | FR-004 |
| `EditorDecisionRecord` shape | FR-005 (incl. `signals` dict) |
| `signals` JSON-serializability rule | FR-005 expanded clause |
| `ProvenanceFrontmatter` shape and merge math | FR-008, FR-009, FR-010, FR-011 |
| `ProvenanceFrontmatter.latest_checkpoint` POSIX vault-relative path | FR-008 expanded clause |
| `ProvenanceFrontmatter.total_edits` `skipped`-action rule | FR-009 expanded clause |
| `ProvenanceFrontmatter.exchange_count` prior-lookup fallback | FR-009 expanded clause |
| Frontmatter merge atomicity | FR-011 expanded clause |
| `extra="allow"` posture (read-side) | FR-002 |
| Per-conversation subdirectory layout in `latest_checkpoint` paths | Clarifications session 2026-06-28 |
| Atomic write semantics (write-temp + rename) | FR-001 expanded clause (matched to Spec 004 cursor) |
| Timestamp serialization (ISO 8601 UTC with `Z`) | FR-001 expanded clause |
| Page snapshot mechanism | FR-013 |
| Shadow-repo commit message format | FR-014 |
| Three-state shadow-repo init | FR-012 |
| Process-kill recovery contract | FR-021 |
| Optional orphan detection on startup | FR-022 |
| stderr message format (`[provenance] ` prefix) | FR-016a |
