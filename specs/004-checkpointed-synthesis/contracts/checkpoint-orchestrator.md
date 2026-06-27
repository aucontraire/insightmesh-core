# Contract: Orchestrator ↔ Checkpoint Module ↔ Agents

Phase 1 output. Defines the internal interface boundaries this feature introduces. Pure code-level contract (no HTTP / no IPC); the "contract" is the function signatures, JSON shapes flowing through agents, and CLI flag semantics.

## 1. CLI surface (`src/cli.py`)

### Flag additions to the existing `batch` command

| Flag | Type | Default | Semantics |
|------|------|---------|-----------|
| `--resume` | flag (no value) | absent | Explicit-intent: require an existing cursor for this conversation, error with `CheckpointMissing` if none. When the cursor exists, behavior is the same as default (continue from cursor). Composes freely with `--max-exchanges` (per Edge Cases). |
| `--max-exchanges N` | int | `None` (no cap) | Soft cap: process at most N additional exchanges this invocation. Negative or zero values error before any agent runs (FR-008). |
| `--force-resume` | flag (no value) | absent | Override for transcript-hash mismatch (FR-006). When the loaded cursor's `transcript_hash` differs from the current transcript's hash, default behavior is to refuse with `CheckpointHashMismatch` (exit code 2); `--force-resume` proceeds from the cursor's recorded index against the new transcript. User assumes the risk that indices may now point at different exchanges. |
| `--retry` | flag (no value) | absent | Required to resume against a cursor with `status == failed` (FR-014). Without this flag, the orchestrator prints the prior `last_error` to stderr and exits 1. With this flag, the orchestrator proceeds with a fresh checkpoint attempt from the cursor position. |

### Resolved cursor path

The CLI derives the cursor path from the input file path and the optional `--conversation` flag (existing from Spec 002):

```python
def cursor_path_for(logs_dir: Path, export_path: Path, conversation_id: str | None) -> Path:
    if conversation_id is None:
        return logs_dir / f"{export_path.stem}.checkpoint.json"
    safe_id = conversation_id.replace("/", "-").replace(":", "-")
    return logs_dir / f"{export_path.stem}__{safe_id}.checkpoint.json"
```

The CLI passes the resolved path to the orchestrator; no other code derives it.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Run completed cleanly (status `complete`, `interrupted` after cap, or no-op on `complete`) |
| 1 | Agent or vault error → cursor written with status `failed`. Also: resume against a `failed` cursor without `--retry` (prints prior `last_error` to stderr, exits 1 without re-invoking agents). |
| 2 | Misuse: `--resume` with no cursor, `--max-exchanges 0` or negative, hash mismatch without `--force-resume`, `schema_version` mismatch (FR-016), cursor index out-of-bounds vs current transcript length (Edge Case), or malformed cursor JSON on disk (Edge Case). |

## 2. Checkpoint module API (`src/checkpoint.py`)

### Pure functions

```python
def compute_transcript_hash(transcript: ChatTranscript) -> str:
    """SHA-256 hex of transcript.model_dump_json(). 64-char string."""


def load_checkpoint(path: Path) -> Checkpoint | None:
    """Read the cursor at `path`. Returns None if the file does not exist.
       Raises ValidationError on malformed JSON or schema mismatch."""


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    """Atomic write: temp file + os.replace. Creates parent dir if missing."""
```

### Exceptions

Defined in §Data Model. Raised by the orchestrator (not by the checkpoint module itself, which is pure I/O):

- `CheckpointMissing` — `--resume` passed but `load_checkpoint` returned None
- `CheckpointHashMismatch` — loaded cursor's `transcript_hash` differs from the current transcript's hash
- `CheckpointAlreadyComplete` — loaded cursor has `status == "complete"` (not strictly an error in spec terms; surfaced via friendly message and exit code 0, not an exception)

## 3. Orchestrator entry point (`src/orchestrator.py`)

### Signature changes to `run_batch`

```python
def run_batch(
    transcript: ChatTranscript,
    *,
    # existing params unchanged...
    checkpoint_path: Path | None = None,            # NEW: full cursor path resolved by the CLI
    max_exchanges: int | None = None,               # NEW: soft cap (FR-009)
    require_resume: bool = False,                   # NEW: True when --resume was explicitly passed
    token_budget: int | None = None,                # NEW: per-checkpoint Synthesis input budget; None → default (50% of model context window)
) -> SessionLog:
    ...
```

### Pseudocode (decision tree)

```text
1. If checkpoint_path is provided:
     cursor = load_checkpoint(checkpoint_path)
     if cursor is None:
       if require_resume: raise CheckpointMissing
       cursor = None  # start fresh
     else:
       transcript_hash_now = compute_transcript_hash(transcript)
       if cursor.transcript_hash != transcript_hash_now:
         raise CheckpointHashMismatch
       if cursor.status == "complete":
         print "already complete; delete cursor to re-run"
         return existing SessionLog (or a no-op stub)
       if cursor.status == "failed":
         print prior last_error and prompt to continue

2. start_index = (cursor.last_processed_exchange_index + 1) if cursor else 0
   accumulated_digest = cursor.topics_covered_digest if cursor else []

3. Loop checkpoint_index in 1, 2, ...:
     remaining = transcript.exchanges[start_index:]
     if not remaining: break

     # Decide this checkpoint's slice by token budget
     checkpoint_slice = pick_slice_by_budget(remaining, token_budget, accumulated_digest, max_exchanges)
     if not checkpoint_slice: break

     # Build Synthesis input
     synthesis_input = {
       "messages": _to_role_content(checkpoint_slice),
       **({"topics_covered_digest": accumulated_digest} if accumulated_digest else {}),
     }

     try:
       drafts = invoke_synthesis(synthesis_input)
       augmented = invoke_historian(drafts)   # returns HistorianOutput with topics_covered_increment
       editor_result = invoke_editor(augmented)
     except Exception as e:
       write_failed_cursor(cursor_path, last_error=str(e))
       raise

     # Persist successful checkpoint
     last_idx = max(checkpoint_slice[-1].index, start_index)
     accumulated_digest.extend(augmented.topics_covered_increment or [])
     status = "complete" if last_idx == len(transcript.exchanges) - 1 else "interrupted"
     save_checkpoint(checkpoint_path, Checkpoint(
       export_path=transcript.source_path,
       conversation_id=conversation_id_from_transcript_or_args,
       transcript_hash=transcript_hash_now,
       last_processed_exchange_index=last_idx,
       checkpoint_number=cursor.checkpoint_number + 1 if cursor else 1,
       status=status,
       last_error=None,
       topics_covered_digest=accumulated_digest,
       updated_at=now_utc(),
     ))

     # Advance and check stop conditions
     start_index = last_idx + 1
     if status == "complete": break
     if max_exchanges reached: break
```

## 4. Agent contract changes

### Synthesis (`/.claude/agents/synthesis.md`)

**Input addition** (only present for checkpoint #2+):

```json
{
  "messages": [...existing...],
  "topics_covered_digest": [
    {"page_title": "...", "gist": "..."},
    ...
  ]
}
```

**Prompt note (to add)**: For second-or-later checkpoints of a conversation, the input includes a `topics_covered_digest` listing pages produced by prior checkpoints. Use it to extend or cross-reference prior pages rather than producing duplicate drafts. Do NOT inline the digest into draft prose; it is context for the LLM, not source material to quote.

### Historian (`/.claude/agents/historian.md`)

**Output addition**:

```json
{
  "augmented_drafts": [...existing...],
  "topics_covered_increment": [
    {"page_title": "<draft tentative_title>", "gist": "<one-line summary, 200 chars max>"},
    ...
  ]
}
```

**Prompt note (to add)**: For each draft you augment, also produce one `DigestEntry` in `topics_covered_increment`: `page_title` = the draft's `tentative_title` exactly; `gist` = a one-line summary (no newlines, 200-char cap) of what the draft is about, suitable as a reminder for a future synthesis pass. This is metadata for the orchestrator's checkpoint cursor; it does not affect cross-link recommendations.

### Editor (`/.claude/agents/editor.md`)

**No change.** Editor's existing three-signal FR-007 update rule handles checkpoint re-runs idempotently. The Editor does not see the cursor or the digest.

## 5. Idempotency guarantees

- `save_checkpoint` overwrites atomically. Concurrent writers are out of scope per spec.
- Re-running a completed checkpoint (`status == "complete"`) is a no-op (FR-007).
- Re-running a checkpoint after `status == "failed"` re-invokes the agents (FR-014 path). Editor's FR-007 update rule prevents duplicate pages for drafts that succeeded before the failure point.
- `compute_transcript_hash` is deterministic: same transcript → same hash. (Verified by unit test.)

## 6. Observability

`SessionLog` continues to record per-agent inputs/outputs per invocation. Each invocation produces a session log; multiple session logs accumulate per conversation across checkpoints. The cursor file is the cross-invocation continuity. No new log format is introduced.

The Synthesis `input_summary` will reflect the inclusion of `topics_covered_digest` (visible as the first 500 chars of the input). The Historian `output` will include the `topics_covered_increment` JSON. Both are useful for debugging and validating SC-002 (no re-invocation of agents for already-processed exchanges).
