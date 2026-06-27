# Long conversations and resume

Goal-oriented guide for working with conversations that span multiple pipeline runs. Introduced in Spec 004 (v0.4.0). For the canonical flag descriptions see the [CLI reference](../reference/cli.md).

---

## Resume a synthesis after interruption

You ran `insightmesh batch` on a long conversation, killed it mid-run, lost network, or otherwise stopped before it completed. You want to pick up where you left off without re-processing the exchanges already done.

```bash
insightmesh batch <input> --conversation <id> --vault <vault>
```

Just run the same command again. The orchestrator auto-detects the cursor file under `<vault>/InsightMesh/.logs/` and resumes from `last_processed_exchange_index + 1`. The already-processed exchanges are skipped entirely — no Synthesis re-invocation, no duplicate pages.

If you want to be strict ("error if there's no cursor — I'm sure I ran this before"), add `--resume`:

```bash
insightmesh batch <input> --conversation <id> --vault <vault> --resume
```

Errors with `CheckpointMissing` if no cursor exists. Useful in scripts where you want to fail loudly on a typo'd conversation id.

---

## Pace a long conversation with `--max-exchanges`

You want to validate the first chunk of a long conversation before committing to the full run, or you only have time for part of it tonight.

```bash
# Process at most 10 exchanges, then stop
insightmesh batch <input> --conversation <id> --vault <vault> --max-exchanges 10
```

The cursor is left at `status: interrupted` with `last_processed_exchange_index` near 10. Inspect the produced wiki pages, then continue:

```bash
# Resume the rest (no flag needed; the cursor is auto-detected)
insightmesh batch <input> --conversation <id> --vault <vault>
```

You can also cap each follow-up run:

```bash
insightmesh batch <input> --conversation <id> --vault <vault> --max-exchanges 10
# Cursor advances another ~10
```

**Soft-cap semantics**: the cap is enforced both at slice-size determination AND between checkpoints. The cursor advances by exactly N when the cap fires (assuming the transcript has ≥ N remaining unprocessed exchanges). Without `--max-exchanges`, the orchestrator picks slice sizes by token budget alone.

---

## Re-process a conversation after transcript changes

You re-exported your Claude.ai or ChatGPT data, the upstream parser added new fields, or you edited the export by hand. The transcript hash recorded with your cursor no longer matches the current transcript.

```bash
insightmesh batch <input> --conversation <id> --vault <vault>
# → error: transcript hash has changed since the cursor at ... was written.
#   Cursor hash:  abc123...
#   Current hash: def456...
```

Two choices:

**Discard the cursor and start fresh** (safer — Editor's FR-007 update path will merge into existing pages where titles match):

```bash
rm <vault>/InsightMesh/.logs/<stem>__<conversation_id>.checkpoint.json
insightmesh batch <input> --conversation <id> --vault <vault>
```

**Continue from the recorded cursor position against the new transcript** (use only when you know the change was additive — indices haven't shifted):

```bash
insightmesh batch <input> --conversation <id> --vault <vault> --force-resume
```

Risk: if the change shifted exchange indices (e.g., upstream added or removed messages mid-conversation), the cursor's `last_processed_exchange_index` may now point at a different exchange than it did when written. You'll get coherent output but possibly skip or duplicate material.

---

## Recover from a failed run

A checkpoint write failed (vault permission error, MCPVault crash, agent timeout). The cursor is now at `status: failed` with `last_error` populated.

```bash
insightmesh batch <input> --conversation <id> --vault <vault>
# → Prior run failed (cursor at ...).
#   last_error: MCPVault write failed: Permission denied
#   Pass --retry to acknowledge and resume from cursor position (index N).
```

The orchestrator refuses to silently retry a failed cursor. Fix the underlying issue (e.g., vault permissions), then acknowledge and retry:

```bash
insightmesh batch <input> --conversation <id> --vault <vault> --retry
```

This proceeds with a fresh checkpoint attempt from the cursor position. If the underlying issue isn't fixed, the run will fail again and the cursor will be updated with the new `last_error`.

---

## Re-process a fully-completed conversation from scratch

You ran a conversation to completion, the cursor is at `status: complete`, and re-running the same command exits immediately with "Already complete." You want to start over.

Delete the cursor file:

```bash
rm <vault>/InsightMesh/.logs/<stem>__<conversation_id>.checkpoint.json
insightmesh batch <input> --conversation <id> --vault <vault>
```

The next run starts fresh from exchange 0. Editor's FR-007 update path will hit the existing wiki pages and decide create-vs-update per its three-signal rule — typically updating same-titled pages rather than duplicating.

There is no `--reset` or `--force-rerun` flag. The delete-and-re-run pattern is the explicit signal that you mean it.

---

## Inspect cursor state

The cursor is a small JSON file you can `cat` or `jq` directly:

```bash
cat <vault>/InsightMesh/.logs/<stem>__<conversation_id>.checkpoint.json | jq '{
  status,
  last_processed_exchange_index,
  checkpoint_number,
  digest_count: (.topics_covered_digest | length),
  last_error
}'
```

See the [CLI reference § Per-conversation cursor](../reference/cli.md#per-conversation-cursor-spec-004) for the full schema.

---

## Compose `--resume` and `--max-exchanges`

The two flags are independent — combine them freely:

```bash
# Require a cursor (error if none) AND cap this invocation at 10 more exchanges
insightmesh batch <input> --conversation <id> --vault <vault> --resume --max-exchanges 10
```

Useful in scripts that pace a long conversation across multiple cron runs, where you want to fail loudly if the cursor went missing between runs (e.g., someone wiped `logs/`).
