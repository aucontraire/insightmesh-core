# CLI Reference

Canonical reference for the `insightmesh` command-line tool. For walkthroughs see [Getting Started](../getting-started.md); for problem-solving see [How-to guides](../how-to/long-conversations.md) and [Troubleshooting](../how-to/troubleshooting.md).

## Global options

| Flag | Description |
|------|-------------|
| `--version` | Print the InsightMesh version and exit |
| `--help` | Show help and exit |

```bash
insightmesh --version
# insightmesh 0.4.0
```

---

## `insightmesh list <export>`

Browse the conversations in a Claude.ai or ChatGPT data export. Pure read — does NOT touch the vault, does NOT invoke any agent.

### Arguments

| Name | Type | Description |
|------|------|-------------|
| `export` | path | Path to a multi-conversation export file (e.g., `conversations.json` from Claude.ai or ChatGPT) |

### Example

```bash
insightmesh list ~/Downloads/conversations.json
```

Output: a Rich-rendered table with Index, Title, Created, Msgs columns, followed by an id-by-index footer. Use either the index or the full conversation id with `insightmesh batch --conversation`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Listing rendered successfully |
| `1` | Unrecognized export format, or upstream parse/validation error |

---

## `insightmesh batch <input> --vault <path>`

Run the Synthesis → Historian → Editor pipeline on a transcript and write wiki pages into an Obsidian vault.

### Arguments

| Name | Type | Description |
|------|------|-------------|
| `input` | path | Either a Spec 001 flat `[{role, content}]` JSON array, or a Claude.ai / ChatGPT multi-conversation export. Auto-detected. |

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--vault PATH` | path | (required) | Path to the Obsidian vault root directory. Pages are written under `<vault>/InsightMesh/`. |
| `--logs PATH` | path | `<vault>/InsightMesh/.logs` | Directory for session JSON logs and per-conversation cursor files. |
| `--conversation ID` | string | none | **Required** when `input` is a multi-conversation export. **Forbidden** when `input` is a flat-array transcript. Accepts the conversation id or its zero-indexed position from `insightmesh list`. |
| `--resume` | flag | off | Explicit-intent resume. Errors if no cursor exists for this conversation. Without this flag, the orchestrator still auto-resumes from any cursor it finds — `--resume` is just stricter (catches typos and wrong-file mistakes). |
| `--max-exchanges N` | int | none | Soft cap on exchanges processed this invocation. Must be > 0. Constrains both slice size and between-checkpoint progression so the cap actually fires on small transcripts. Cursor advances by exactly N when the cap fires (assuming ≥ N remaining unprocessed exchanges). |
| `--force-resume` | flag | off | Override for transcript-hash mismatch. Use only when you know the transcript changed and you accept that prior cursor indices may now point at different exchanges. |
| `--retry` | flag | off | Required to resume past a cursor with `status: failed`. Acknowledges the prior `last_error` and runs a fresh checkpoint attempt from the cursor position. |

### Examples

Spec 001 flat-array transcript:

```bash
insightmesh batch my-transcript.json --vault ~/Obsidian/MyVault
```

Multi-conversation export (Claude.ai / ChatGPT):

```bash
# First list to find the conversation
insightmesh list ~/Downloads/conversations.json

# Then synthesize a specific one
insightmesh batch ~/Downloads/conversations.json \
  --conversation d126dc13-ab72-4657-939d-b1d1ecc0fd33 \
  --vault ~/Obsidian/MyVault
```

Pace a long conversation across multiple invocations:

```bash
insightmesh batch chat.json --conversation <id> --vault <vault> --max-exchanges 10
# Inspect cursor, then continue:
insightmesh batch chat.json --conversation <id> --vault <vault>
```

See [How-to: Long conversations](../how-to/long-conversations.md) for the full resume + cap workflow.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Pipeline completed successfully, or no-op on already-complete cursor |
| `1` | Agent or vault error during the pipeline (cursor written with `status: failed`), OR resume against a `failed` cursor without `--retry` |
| `2` | Misuse: `--resume` with no cursor, `--max-exchanges <= 0`, transcript-hash mismatch without `--force-resume`, `schema_version` mismatch, cursor index out-of-bounds, malformed cursor JSON on disk |

---

## On-disk artifacts

### Vault output

`<vault>/InsightMesh/*.md` — wiki pages produced by Editor. Each has YAML frontmatter (`title`, `created`, `updated`, `source`, `tags`).

### Session logs

`<vault>/InsightMesh/.logs/<timestamp>-<stem>.json` — one per pipeline invocation. Records per-agent input summaries, parsed outputs, durations, status, cross-links, exchanges processed.

### Per-conversation cursor (Spec 004)

`<vault>/InsightMesh/.logs/<stem>.checkpoint.json` for single-conversation source files.

`<vault>/InsightMesh/.logs/<stem>__<conversation_id>.checkpoint.json` for multi-conversation exports (filesystem-unsafe characters in `conversation_id` are sanitized to hyphens).

Cursor schema (Pydantic v2, strict, `extra=forbid`):

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Currently `1`. Unknown versions are refused on resume. |
| `export_path` | path | Absolute path of the input file. |
| `conversation_id` | str \| null | Null for single-conversation source files. |
| `transcript_hash` | str (SHA-256 hex, 64 chars) | Of `ChatTranscript.model_dump_json()`. Mismatch refuses resume unless `--force-resume`. |
| `last_processed_exchange_index` | int (≥ 0) | The cursor. Next run resumes at this + 1. |
| `checkpoint_number` | int (≥ 1) | Monotonic, increments per successful checkpoint write. |
| `status` | `"complete"` \| `"interrupted"` \| `"failed"` | `complete` is terminal; delete the cursor to re-process. |
| `last_error` | str \| null | Populated when `status == "failed"`. Surfaced on next resume. |
| `topics_covered_digest` | list of `{page_title, gist}` | Accumulated across checkpoints. Passed to Synthesis on second-or-later checkpoints. |
| `meaning_summary` | str \| null | Forward-compatibility hook. Always null in this spec. |
| `updated_at` | datetime (UTC) | Wall-clock of the last successful write. |

### Agent failure scratch

`.specify/scratch/agent_responses/<timestamp>-<agent>-failed.txt` — raw agent responses that failed parsing. Inspect when an agent error appears in stderr or in a session log's `error_detail`.

---

## Environment variables

| Variable | Set by | Description |
|----------|--------|-------------|
| `VAULT_PATH` | CLI (from `--vault`) | Resolved before invoking the orchestrator. Read by MCPVault's `.mcp.json` configuration. Do not set manually unless calling `run_batch` from Python outside the CLI. |

---

## Pre-flight validation

Before any agent runs, `insightmesh batch` checks:

- Vault path exists, is a directory, and is writable
- Every expected agent in `.claude/agents/` exists with parseable frontmatter (`name:` field present)

Failures are reported to stderr with all issues aggregated into one message; the command exits `1` without invoking any agent. See [Known Limitations](../known-limitations.md) for context on what this catches.
