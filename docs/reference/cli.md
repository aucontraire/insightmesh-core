# CLI Reference

Canonical reference for the `insightmesh` command-line tool. For walkthroughs see [Getting Started](../getting-started.md); for problem-solving see [How-to guides](../how-to/long-conversations.md) and [Troubleshooting](../how-to/troubleshooting.md).

## Global options

| Flag | Description |
|------|-------------|
| `--version` | Print the InsightMesh version and exit |
| `--help` | Show help and exit |

```bash
insightmesh --version
# insightmesh 0.5.0
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

Pages touched by Spec 005's checkpoint pipeline additionally carry a `provenance:` block (see below).

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

### Per-checkpoint provenance record (Spec 005)

After each successful checkpoint, the orchestrator persists a permanent, queryable record of what happened in three complementary artifacts:

**1. Checkpoint JSON** at `<vault>/InsightMesh/.history/checkpoints/<conv-subdir>/cp-<NNN>.json`, where `<conv-subdir>` is the conversation identifier (filesystem-unsafe characters sanitized to hyphens), OR the literal `_flat` sentinel when the source transcript carries no conversation id (Spec 001 flat-array shape). Records the conversation block (provider, models_used, transcript_hash), per-exchange message identifiers from echomine, per-page Editor decisions including rationale + confidence + the full signals dict, the results summary, and convenience pointers to the session log + cursor. Self-sufficient: provenance queries do not need to traverse those convenience pointers.

```bash
# What did checkpoint cp-002 do?
jq . <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-002.json

# Which pages did Editor touch in this conversation, ranked by frequency?
jq -s '[.[] | .editor.decisions[] | .file] | group_by(.) | map({file: .[0], count: length}) | sort_by(-.count)' \
  <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-*.json
```

**2. Frontmatter `provenance:` block** on every wiki page Editor created or updated. Cumulative across checkpoints — `total_edits` increments, `conversations` accumulates the union, `exchange_count` is the union size of contributing exchange indices.

```yaml
provenance:
  latest_checkpoint: InsightMesh/.history/checkpoints/<conv-id>/cp-002.json
  conversations: [<conv-id>]
  latest_action: updated
  latest_confidence: high
  total_edits: 3
  exchange_count: 7
```

**3. Shadow git repository** at `<vault>/InsightMesh/.history/.git/`, distinct from any git the user runs on their vault root. One commit per successful checkpoint, with a machine-greppable subject and body listing every touched page (action + confidence). Page snapshots live at `.history/pages/<sanitized-slug>.md` so `git log -p pages/<slug>.md` shows the page's evolution across edits.

```bash
git -C <vault>/InsightMesh/.history log --oneline
git -C <vault>/InsightMesh/.history log -p pages/<some-page>.md
git -C <vault>/InsightMesh/.history log --oneline --grep 'checkpoint:cp-002'
```

**Schema versioning**: the JSON files carry `schema_version: 1`. Within v1, evolution is additive (optional fields may be added without bumping); readers tolerate unknown extras and missing optionals.

**Fallback behavior**: provenance failures never fail the run. When `git` is not on `PATH`, the JSON + frontmatter still land and `[provenance] git not on PATH; skipping shadow-repo commit` is logged to stderr. When the commit fails (permissions, disk, hooks), JSON + frontmatter still landed; the next successful commit sweeps up orphaned snapshots. When a page's existing frontmatter is unparseable YAML, that page is skipped with a logged warning and the rest of the work proceeds. Run exit code is determined solely by agent work + the Spec 004 cursor save.

**Companion plugin (beta via BRAT)**: a dedicated read-only Obsidian viewer plugin that renders the JSON + frontmatter + shadow-git diffs into a native side-pane experience is available as a v0.1 beta. Install via [BRAT](https://github.com/TfTHacker/obsidian42-brat):

1. In Obsidian: Settings → Community plugins → install + enable BRAT
2. BRAT settings → "Add Beta Plugin" → paste `aucontraire/insightmesh-obsidian`
3. Enable "InsightMesh Viewer" in Community plugins

Open any wiki page produced by `insightmesh batch`; the viewer side-pane renders the page's provenance with click-through to its source conversation, checkpoint history, and snapshot-to-snapshot diffs. Compatibility: viewer 0.1.x supports core `schema_version=1`. Beta software, expect rough edges; file issues at [aucontraire/insightmesh-obsidian/issues](https://github.com/aucontraire/insightmesh-obsidian/issues). The plugin repo: [aucontraire/insightmesh-obsidian](https://github.com/aucontraire/insightmesh-obsidian).

For users who prefer terminal-only inspection (or pre-plugin-install setup), the shell-tool recipes above remain the path. The spec's `quickstart.md` also documents [obsidian-git](https://github.com/Vinzent03/obsidian-git) as a tactical viewer with documented caveats.

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
