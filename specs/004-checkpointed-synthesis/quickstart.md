# Quickstart: Checkpointed synthesis with wiki-as-carry-over

Phase 1 output. Demonstrates the three scenarios this spec adds, from a user's perspective.

## Prerequisites

- InsightMesh installed and on `004-checkpointed-synthesis` branch
- `uv sync --all-extras` succeeded
- An Obsidian vault path set via `--vault` (or env var)
- A Claude.ai export `chat.json` available (single conversation extracted via the existing Spec 002 path)

## Scenario A: Small conversation, no change (FR-013 regression check)

A conversation small enough to fit in a single checkpoint should behave exactly as today.

```bash
uv run python -m src.cli batch chat.json --vault ~/Documents/InsightMesh-test-vault
```

Expected:
- Wiki pages produced as before.
- A new file appears at `logs/<stem>.checkpoint.json` with `status: "complete"`, `checkpoint_number: 1`, `last_processed_exchange_index: <len(exchanges) - 1>`.
- Re-running the same command exits immediately with "already complete; delete the cursor to re-run." No agents invoked.

## Scenario B: Long conversation, multiple checkpoints (US1, SC-001)

A conversation whose flattened transcript exceeds the per-checkpoint Synthesis budget (default ~50% of model context window).

```bash
uv run python -m src.cli batch long_chat.json --vault ~/Documents/InsightMesh-test-vault
```

Expected:
- Multiple checkpoints fire; `logs/<stem>.checkpoint.json` is rewritten after each one with an incremented `checkpoint_number` and an advanced `last_processed_exchange_index`.
- After every checkpoint, `Checkpoint.topics_covered_digest` accumulates new entries (one per draft produced).
- Each checkpoint after the first passes the accumulated digest to Synthesis as input context (visible in the session log's Synthesis `input_summary`).
- Final cursor: `status: "complete"`, `last_processed_exchange_index == len(exchanges) - 1`.
- Wiki contains pages covering every exchange.

To simulate an interruption mid-run:

```bash
# Start the run, then Ctrl-C during a checkpoint
uv run python -m src.cli batch long_chat.json --vault ~/Documents/InsightMesh-test-vault
^C

# Resume
uv run python -m src.cli batch long_chat.json --vault ~/Documents/InsightMesh-test-vault
```

Expected on resume (SC-002):
- The orchestrator reads the cursor and skips already-processed exchanges.
- The Synthesis `input_summary` in the next session log contains ONLY the new exchanges (verifies zero re-invocation for processed exchanges).
- No duplicate wiki pages (Editor's FR-007 update rule handles repeats idempotently if any boundary effect occurred).

Explicit resume intent:

```bash
uv run python -m src.cli batch long_chat.json --resume --vault ~/Documents/InsightMesh-test-vault
```

Same behavior as default, but errors with `CheckpointMissing` if no cursor exists.

## Scenario C: Per-invocation cap (US2)

Process at most N exchanges this invocation. The cursor persists, and the next invocation continues forward.

```bash
# First invocation: process up to 10 exchanges
uv run python -m src.cli batch long_chat.json --max-exchanges 10 --vault ~/Documents/InsightMesh-test-vault

# Inspect the cursor
cat logs/long_chat.checkpoint.json | jq '{status, last_processed_exchange_index, checkpoint_number}'
# → status: "interrupted", last_processed_exchange_index: ~10, checkpoint_number: 1

# Second invocation: another 10
uv run python -m src.cli batch long_chat.json --max-exchanges 10 --vault ~/Documents/InsightMesh-test-vault

# Cursor now advanced
cat logs/long_chat.checkpoint.json | jq '{status, last_processed_exchange_index, checkpoint_number}'
# → status: "interrupted", last_processed_exchange_index: ~20, checkpoint_number: 2
```

Edge case: cap exceeds remaining work.

```bash
uv run python -m src.cli batch short_chat.json --max-exchanges 1000 --vault ~/Documents/InsightMesh-test-vault
# Processes all remaining exchanges, cursor reaches end-of-transcript with status: "complete"
```

Edge case: invalid cap.

```bash
uv run python -m src.cli batch chat.json --max-exchanges 0
# Errors before any agent runs (FR-008)
```

## Scenario D: Transcript changed between runs (FR-006)

If the source transcript changes (re-extracted, upstream parser added fields, etc.), the cursor invalidates.

```bash
# Initial run leaves cursor at exchange 15
uv run python -m src.cli batch chat.json --max-exchanges 15 --vault ...

# Source export edited or re-extracted; transcript hash now differs
uv run python -m src.cli batch chat.json --resume --vault ...
# Errors with CheckpointHashMismatch
# Message: "Transcript hash has changed since the cursor was written.
#           Cursor hash: abc123...  Current hash: def456...
#           Re-run without --resume to discard the cursor, or use --force-resume to continue anyway."

# Discard cursor and start fresh
rm logs/chat.checkpoint.json
uv run python -m src.cli batch chat.json --vault ...
```

## Scenario E: Failure during a checkpoint (FR-014)

Simulate a vault error (e.g., make the vault directory read-only mid-run). The orchestrator records the failure in the cursor.

```bash
uv run python -m src.cli batch long_chat.json --vault /readonly/vault
# Exits with non-zero status

cat logs/long_chat.checkpoint.json | jq '{status, last_error}'
# → status: "failed", last_error: "MCPVault write failed: Permission denied"

# Fix the vault permissions, then try to resume normally — refused, prior failure must be acknowledged
uv run python -m src.cli batch long_chat.json --vault /readonly/vault
# Prints prior last_error to stderr; exits 1 without re-invoking agents

# Acknowledge and retry: pass --retry to proceed past the prior failure
uv run python -m src.cli batch long_chat.json --vault /readonly/vault --retry
# Proceeds with a fresh checkpoint attempt from the cursor position
```

## Verification commands (developer)

```bash
# Unit tests for the checkpoint module
uv run pytest tests/test_checkpoint.py -q

# Integration tests for orchestrator resume / cap / no-op
uv run pytest tests/test_orchestrator.py -q

# Full gate
uv run pytest
uv run mypy --strict src/
uv run ruff check src/ tests/
uv run black --check src/ tests/
```

Manual end-to-end on a real long Claude export (the motivating SC-001 case):

```bash
uv run python -m src.cli batch ~/Downloads/claude_export_long.json \
  --conversation <conversation-id> \
  --vault ~/Documents/InsightMesh-test-vault
```

Confirm: the run completes across multiple checkpoints (visible by tailing `logs/`), the final cursor has `status: "complete"`, and the wiki reflects every topic from the conversation.
