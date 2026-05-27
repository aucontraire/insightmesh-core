# Quickstart: Pre-flight Validation

**Phase 1 output** — end-to-end demonstration of the new `list → pick → batch` workflow on a real Claude.ai or ChatGPT export, plus a deliberate-failure walkthrough of the pre-flight agent check.

Assumes a working Spec 001 install (see `docs/getting-started.md`).

---

## Scenario 1: Synthesize one conversation from a real Claude.ai export

```bash
# 1. You have a fresh Claude.ai data export
ls -lh ~/Downloads/conversations.json
# -rw-r--r--  1 you  staff   4.2M ~/Downloads/conversations.json

# 2. See what's in it
uv run insightmesh list ~/Downloads/conversations.json
```

Expected output (Rich-rendered table to stdout, ids in a footer):

```text
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Index  ┃ Title                          ┃ Created             ┃ Msgs  ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ 0      │ Speed of light, deeper dive    │ 2026-04-12 14:33    │ 42    │
│ 1      │ Refining the EM spectrum chat  │ 2026-04-10 09:17    │ 18    │
│ 2      │ Camera aperture and DoF        │ 2026-04-08 22:01    │ 24    │
│ ...    │ ...                            │ ...                 │ ...   │
└────────┴────────────────────────────────┴─────────────────────┴───────┘

Index 0  →  c4f5b9e0-abc1-...
Index 1  →  9a2d3f88-xyz9-...
Index 2  →  1f7e6d22-ghi5-...
```

```bash
# 3. Pick one — by index (shortest)
uv run insightmesh batch ~/Downloads/conversations.json \
    --conversation 0 \
    --vault ~/Documents/InsightMesh-test-vault
```

Or equivalently by id:

```bash
uv run insightmesh batch ~/Downloads/conversations.json \
    --conversation c4f5b9e0-abc1-... \
    --vault ~/Documents/InsightMesh-test-vault
```

Expected output (unchanged from Spec 001 success path):

```text
Loaded 42 exchanges from /Users/you/Downloads/conversations.json
Vault: /Users/you/Documents/InsightMesh-test-vault
Logs:  /Users/you/Documents/InsightMesh-test-vault/InsightMesh/.logs
Running pipeline...
Pipeline complete: 4 created, 0 updated, 4 editor decisions logged.
```

```bash
# 4. Check the vault
ls ~/Documents/InsightMesh-test-vault/InsightMesh/
# Speed of Light.md       Wave-Particle Duality.md
# Photons in Media.md     Relativity Basics.md
```

**What just happened**:
- `list` parsed the Claude.ai export, projected each conversation to a one-line `ConversationSummary`, rendered the table.
- `batch --conversation 0` walked the export, extracted conversation at index 0, flattened its messages to the internal `{role, content}` shape Spec 001's `transcript.py` already understands, then handed off to the existing pipeline.
- The pre-flight pass (vault writable + 3 expected agents present) ran first and passed silently.

---

## Scenario 2: Same workflow with a ChatGPT export

```bash
# Identical workflow; the adapter detects the format automatically
uv run insightmesh list ~/Downloads/chatgpt_conversations.json
uv run insightmesh batch ~/Downloads/chatgpt_conversations.json \
    --conversation 3 \
    --vault ~/Documents/InsightMesh-test-vault
```

ChatGPT messages are stored as a tree (`mapping` + `current_node`). The adapter walks the linear path from root to `current_node`, dropping any branched/abandoned drafts. The count in the `Msgs` column reflects that linear path, not the total node count.

---

## Scenario 3: Pre-flight catches a missing agent

```bash
# Deliberately remove one of the three required agents
mv .claude/agents/historian.md /tmp/historian.md.backup

# Try to run batch
uv run insightmesh batch tests/fixtures/single_topic.json \
    --vault ~/Documents/InsightMesh-test-vault
```

Expected output (entirely on stderr, exit code 1):

```text
error: pre-flight checks failed:

  Missing agents (expected in .claude/agents/):
    - historian

Aborting before orchestrator invocation. Fix the issues above and re-run.
```

No partial work. No session log written. No call to Claude. Restore the file:

```bash
mv /tmp/historian.md.backup .claude/agents/historian.md
```

Re-run; the pre-flight passes and the pipeline proceeds normally.

---

## Scenario 4: Pre-flight aggregates multiple failures

```bash
# Remove two agents and pass a bad vault path
mv .claude/agents/historian.md /tmp/historian.md.backup
mv .claude/agents/editor.md /tmp/editor.md.backup

uv run insightmesh batch tests/fixtures/single_topic.json \
    --vault /nonexistent/path
```

Expected output (aggregated — FR-022):

```text
error: pre-flight checks failed:

  Vault:
    - vault path does not exist: /nonexistent/path

  Missing agents (expected in .claude/agents/):
    - editor
    - historian

Aborting before orchestrator invocation. Fix the issues above and re-run.
```

One run, all problems surfaced. No fix-and-rerun loop.

---

## Scenario 5: Backward compatibility — Spec 001 inputs still work unchanged

```bash
# A flat {role, content} JSON array from Spec 001 — same command as before
uv run insightmesh batch tests/fixtures/single_topic.json \
    --vault ~/Documents/InsightMesh-test-vault
```

No `--conversation` flag. No new error. The pre-flight passes silently and Spec 001's pipeline runs exactly as before. This is the FR-014 backward-compatibility contract in action.

---

## Failure cases to know

| You did this | You see this |
|--------------|--------------|
| `insightmesh list ~/Downloads/conversations.json` on a non-export file | `error: not a recognized export format ...`, exit 1 |
| `insightmesh batch ~/Downloads/conversations.json --vault ~/vault` (forgot `--conversation`) | `error: <path> is a multi-conversation export. Run 'insightmesh list <path>' ...`, exit 1 |
| `insightmesh batch tests/fixtures/single_topic.json --conversation 0 --vault ~/vault` (used the flag on a flat-array transcript) | `error: --conversation cannot be used with a flat {role, content} transcript ...`, exit 1 |
| `insightmesh batch ~/Downloads/conversations.json --conversation does-not-exist --vault ~/vault` | `error: no conversation matches --conversation 'does-not-exist' ...`, exit 1 |
| `insightmesh list ~/Downloads/empty-export.json` (zero conversations) | `No conversations in export.`, exit 0 |

---

## Implementation note (for contributors)

Under the hood, Claude.ai and ChatGPT schema parsing is delegated to the [`echomine`](https://pypi.org/project/echomine/) library (PyPI `echomine>=1.3.0,<2.0.0`). InsightMesh's `src/exports.py` is a thin wrapper that calls `echomine.ClaudeAdapter` and `echomine.OpenAIAdapter` via their library API, walks the canonical thread via EchoMine's `Conversation.get_thread()`, and converts the result to the flat `{role, content}` shape Spec 001's `transcript.py` consumes. InsightMesh does not implement or fork Claude.ai/ChatGPT parsers itself; when EchoMine adds a new export provider, InsightMesh inherits it by upgrading the dependency. See `research.md` R2, R3, and R7 for the rationale.

---

## Validation criteria

This quickstart succeeds when a new user can:

1. Run `insightmesh list <export.json>` against their own real Claude.ai or ChatGPT export and see a coherent table (SC-001)
2. Pick a conversation by id or index and produce wiki pages without writing any external script (SC-001)
3. Encounter Scenario 3 (missing agent) and immediately understand what's wrong and how to fix it (SC-003)
4. Run any Spec 001-era command unchanged and see no regression (SC-004)
5. Complete the workflow following only `docs/getting-started.md` after the Spec 002 update (SC-005)
