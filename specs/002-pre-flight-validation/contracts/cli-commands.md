# CLI Command Contracts: Pre-flight Validation

**Phase 1 output** — the user-visible CLI surface this spec ships. Format is informal (CLI commands don't have an OpenAPI equivalent), but exit codes, stderr/stdout shapes, and flag schemas are concrete enough to drive contract tests.

---

## Command: `insightmesh list`

**New in Spec 002.**

### Synopsis

```text
insightmesh list <export.json>
```

### Inputs

| Position / Flag | Type | Required | Description |
|-----------------|------|----------|-------------|
| `<export.json>` (positional) | `Path` (existing file, readable) | Yes | Path to a multi-conversation export from Claude.ai or ChatGPT |

**Notably absent**: `--vault`. Per Clarification Q5 and FR-001, `list` does not accept any vault-related option.

### Standard output (stdout)

A single Rich-rendered table with these columns, ordered most-recent-first by `created`. The conversation `id` is a column (not a separate footer) so users can match a title to its id and index in one row — either the Index or the ID works with `batch --conversation`:

```text
┏━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Index ┃ ID                                   ┃ Title                      ┃ Msgs ┃ Created          ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ 0     │ c4f5b9e0-abc1-4d11-9f33-…            │ Speed of light, deeper dive │ 42   │ 2026-04-12 14:33 │
│ 1     │ 9a2d3f88-xyz9-4e55-baa0-…            │ Refining the EM spectrum    │ 18   │ 2026-04-10 09:17 │
│ ...   │ ...                                  │ ...                         │ ...  │ ...              │
└───────┴──────────────────────────────────────┴────────────────────────────┴──────┴──────────────────┘
```

**Rationale for the single-table layout**: an earlier design split the id into a separate "Conversation ids:" footer because UUIDs are wide. Real-data testing showed that forced users to scroll between two lists and cross-reference by index to find an id — which led to selecting the wrong conversation. A single table (rendered at width 130, full UUID in a `no_wrap` column, title truncated with ellipsis) keeps everything in one row. Titles are truncated/escaped to preserve column alignment (FR-008).

### Standard error (stderr)

Empty on success.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (one or more rows printed, OR empty-export message printed per FR-006) |
| 1 | File not found, unreadable, or not a recognized export format (FR-007) |
| 2 | Typer/Click usage error (e.g., missing required positional argument) |

### Failure modes

| Trigger | Stdout | Stderr | Exit code |
|---------|--------|--------|-----------|
| Path does not exist | (empty) | `error: file not found: <path>` | 1 |
| File is not valid JSON | (empty) | `error: not valid JSON: <path>` | 1 |
| JSON root is not a list or both EchoMine adapters reject the file | (empty) | `error: not a recognized export format: <path> (tried <adapters>); expected a multi-conversation export from Claude.ai or ChatGPT` | 1 |
| File is a Spec 001 flat-array transcript | (empty) | `error: this looks like a flat {role, content} transcript, not a multi-conversation export. Use 'insightmesh batch <file>' directly.` | 1 |
| Export has zero conversations | `(empty)` (or single-line: `No conversations in export.`) | (empty) | 0 |
| EchoMine raises `ParseError` mid-stream after some rows yielded | (already-collected rows flushed to stdout) | `warning: listing aborted after <N> conversations: <upstream parse error>` | 1 |
| Both `ClaudeAdapter` and `OpenAIAdapter` accept the first conversation (per FR-025, Claude.ai wins) | (table proceeds normally) | `warning: export matched both Claude.ai and ChatGPT adapters; using Claude.ai` | 0 |
| EchoMine `on_skip` fires for a malformed conversation mid-stream (per FR-028) | (table proceeds, skipped row omitted) | `warning: skipped conversation <id-or-position>: <reason>` (one per skip) | 0 |

---

## Command: `insightmesh batch` (modified)

**Modifications in Spec 002:**

1. New optional flag `--conversation <id-or-index>`
2. New behavior when the input file is a multi-conversation export (refuses without `--conversation`)
3. Pre-orchestrator pre-flight pass that aggregates vault validation (existing FR-011) and the new agent presence check (FR-015 to FR-018)

### Synopsis

```text
insightmesh batch <transcript-or-export.json> --vault <path> [--logs <path>] [--conversation <id-or-index>]
```

### Inputs

| Position / Flag | Type | Required | Description |
|-----------------|------|----------|-------------|
| `<transcript-or-export.json>` (positional) | `Path` (existing file, readable) | Yes | Either a Spec 001 flat-array transcript OR a multi-conversation export |
| `--vault` | `Path` (existing, writable directory) | Yes | Obsidian vault root |
| `--logs` | `Path` (directory, will be created if missing) | No | Session log directory; defaults to `<vault>/InsightMesh/.logs` |
| `--conversation` | `str` | Conditional | Required when the input file is a multi-conversation export; forbidden when the input is a flat-array transcript |

**`--conversation` value resolution** (FR-010, Clarification Q3):
- If the value parses as a non-negative integer AND that integer is in range `[0, len(conversations))`, treat as **index**
- Otherwise, treat as **id** and match against `InsightMeshSummary.id` (the projection over `echomine.Conversation.id`)
- No explicit prefix syntax (`id:`, `index:`) is supported or parsed

### Standard output (stdout)

Unchanged from Spec 001:

```text
Pipeline complete: <N> created, <M> updated, <K> editor decisions logged.
```

### Standard error (stderr)

#### Success path

Informational lines unchanged from Spec 001:

```text
Loaded <N> exchanges from <path>
Vault: <vault>
Logs:  <logs>
Running pipeline...
```

#### Pre-flight failure path (NEW)

A single aggregated message before exit (FR-022). Format:

```text
error: pre-flight checks failed:

  Vault:
    - <vault error 1>
    - <vault error 2>

  Missing agents (expected in .claude/agents/):
    - <agent name 1>
    - <agent name 2>

  Malformed agent files:
    - <path 1>: <reason>
    - <path 2>: <reason>

Aborting before orchestrator invocation. Fix the issues above and re-run.
```

Sections with empty lists are omitted (e.g., if only agents are missing, the `Vault:` and `Malformed agent files:` sections do not appear).

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (orchestrator ran to completion) |
| 1 | Pre-flight failure (vault, agent presence, or both per FR-022) OR orchestrator runtime error (unchanged from Spec 001) |
| 2 | Typer/Click usage error |

### Failure modes (new and modified)

| Trigger | Stderr | Exit code |
|---------|--------|-----------|
| Input is a multi-conversation export AND `--conversation` not provided | `error: <path> is a multi-conversation export. Run 'insightmesh list <path>' to see available conversations, then re-run with --conversation <id-or-index>.` | 1 |
| Input is a flat-array transcript AND `--conversation` provided | `error: --conversation cannot be used with a flat {role, content} transcript. Drop the flag, or pass a multi-conversation export.` | 1 |
| `--conversation` value matches no conversation | `error: no conversation matches --conversation '<value>' in <path>. Run 'insightmesh list <path>' to see valid ids.` | 1 |
| Pre-flight: vault path missing/not writable | (aggregated message, Vault section) | 1 |
| Pre-flight: one or more agent files missing | (aggregated message, Missing agents section) | 1 |
| Pre-flight: agent file present but YAML frontmatter unparseable or missing `name:` | (aggregated message, Malformed agent files section) | 1 |
| Pre-flight: multiple categories fail simultaneously | (aggregated message, all relevant sections) | 1 |
| EchoMine raises `ParseError` when reading the export (per FR-027) | `error: cannot parse export file <path>: <upstream message verbatim>` | 1 |
| EchoMine raises `ValidationError` on conversation data (per FR-027) | `error: invalid conversation data in <path>: <upstream message verbatim>` | 1 |
| `--conversation` resolves to a conversation whose canonical thread has no user/assistant messages | `error: conversation '<id>' contains no usable user/assistant messages` | 1 |
| Both adapters accept the first conversation | (proceeds with Claude.ai per FR-025) `warning: export matched both Claude.ai and ChatGPT adapters; using Claude.ai` | 0 (warning) or selected-outcome exit code |
| EchoMine `on_skip` fires on a conversation that is NOT the selected one | `warning: skipped conversation <id-or-position>: <reason>` (continues with selected conversation) | 0 (warning) or selected-outcome exit code |
| User sends SIGINT (Ctrl+C) during list or batch | `interrupted by user` | 130 |

---

## Cross-cutting contracts

### Error message format

Per spec FR-019, error message prefixes follow a three-category convention enabling single-regex matching:

| Error category | Prefix | Source |
|----------------|--------|--------|
| Pre-flight check failure | `error: pre-flight checks failed:` | aggregated per FR-022 |
| Export-handling failure | `error: export `, `error: cannot parse export`, or `error: conversation ` | per-error from `src/exports.py` |
| Orchestrator runtime failure | `error: pipeline failed:` | unchanged from Spec 001 |

The first two categories are stderr-only and never write to `.logs/` (FR-019). Warnings (per FR-028 and Edge Cases) use the `warning:` prefix and never change exit code on their own.

### Backward compatibility (FR-014)

Every Spec 001 invocation that worked before this spec MUST continue to work unchanged:

```bash
# All of these are unchanged-behavior cases:
insightmesh batch tests/fixtures/single_topic.json --vault ~/vault
insightmesh batch tests/fixtures/multi_topic.json --vault ~/vault --logs ./logs
insightmesh batch ~/Downloads/my-extracted-conversation.json --vault ~/vault
```

The pre-flight pass runs in all cases (vault and agent checks), but for valid Spec 001 inputs it passes silently and the orchestrator runs as before.

### Idempotency

- `insightmesh list` is read-only. Running it twice on the same input produces identical output (modulo terminal width effects on truncation).
- `insightmesh batch --conversation <id>` run twice on the same export is idempotent in the same sense Spec 001 already is: Editor's create-vs-update logic (FR-007 of Spec 001) handles repeats. The conversation selection step itself is deterministic.

### Logging

Per Clarification Q1 and FR-019, no pre-flight failure writes to `.logs/` or any other persistent store. Stderr is the only channel. If a user wants a record, they can redirect: `insightmesh batch ... 2> preflight.log`.
