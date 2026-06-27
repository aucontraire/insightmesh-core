# Troubleshooting

Problem-solving guide for issues hit while installing or running InsightMesh. For the install walkthrough see [Getting Started](../getting-started.md); for resume/checkpoint workflows see [Long conversations](long-conversations.md).

---

## Smoke test fails

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `error: vault path does not exist` | Wrong path passed to `--vault` | Verify the vault directory exists (`ls ~/Documents/InsightMesh-test-vault`) |
| Hangs > 10 minutes | Claude API rate-limited or hung | `Ctrl+C`, wait a few minutes, retry. See [pipeline hangs](#pipeline-hangs-forever) for diagnosis |
| `error: pipeline failed: ... Failed to parse ... output` | An agent returned prose instead of JSON | Usually transient — retry. The full raw response is saved to `.specify/scratch/agent_responses/<timestamp>-<agent>-failed.txt`. If persistent, the agent prompt may need tightening |
| `mcpvault: ✗ Failed` in `claude mcp list` | npm cache cold or `.mcp.json` malformed | `npx -y @bitbonsai/mcpvault@latest --help` once to warm the cache, then re-check |
| Synthesis returns "I don't see a transcript" | Agent file out of date | Restart your Claude Code session so agent files reload |

---

## `uv: command not found`

Add uv to your shell PATH. The installer prints instructions; usually:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

---

## `claude: command not found`

Install Claude Code per [code.claude.com/docs](https://code.claude.com/docs).

---

## `ModuleNotFoundError: No module named 'src'`

You forgot `uv run` — running raw `python` or `pytest` won't use the project venv. Always prefix project commands with `uv run`:

```bash
uv run pytest
uv run mypy --strict src/
uv run python -m src.cli --help
```

---

## Pipeline hangs forever

There's no built-in timeout on the LLM calls. If a run is silent for >10 minutes, `Ctrl+C` and inspect:

```bash
ps aux | grep -E "insightmesh|mcpvault|claude.*query"
```

Kill stale processes if needed:

```bash
pkill -f mcpvault
```

When you re-run, the orchestrator will auto-resume from the most recent cursor (Spec 004) — you won't lose the work done before the hang.

---

## MCPVault subprocess crashes

Cold-start `npx` issues are the most common cause. Run once to warm the cache:

```bash
npx -y @bitbonsai/mcpvault@latest --help
```

---

## "Editor agent failed: Failed to parse editor output"

The LLM returned prose instead of pure JSON. Usually transient — retry. The full raw response is saved to `.specify/scratch/agent_responses/<timestamp>-editor-failed.txt` for diagnosis.

Since v0.4.0, the orchestrator also handles Claude Code SDK's `<persisted-output>` envelope (which wraps agent responses larger than ~50KB). If you still hit a parse error on a large response, check whether the scratch file's response shape is genuinely unparseable JSON or just wrapped in that envelope (which would now be handled automatically).

---

## "Already complete" when you wanted to re-run

The cursor's `status` is `complete`. Re-running the same command is a no-op by design. To re-process the conversation from scratch:

```bash
rm <vault>/InsightMesh/.logs/<stem>__<conversation_id>.checkpoint.json
insightmesh batch <input> --conversation <id> --vault <vault>
```

See [Long conversations § Re-process a fully-completed conversation](long-conversations.md#re-process-a-fully-completed-conversation-from-scratch).

---

## "Transcript hash has changed"

The transcript (or the upstream parser that produces it) has changed since the cursor was written. See [Long conversations § Re-process a conversation after transcript changes](long-conversations.md#re-process-a-conversation-after-transcript-changes).

---

## Need more help

Open an issue at [github.com/aucontraire/insightmesh-core/issues](https://github.com/aucontraire/insightmesh-core/issues) with:

- The exact command you ran
- The full stderr output
- The session log (`<vault>/InsightMesh/.logs/<timestamp>-<stem>.json`) and the cursor file (if any)
- Anything from `.specify/scratch/agent_responses/` if an agent parse failed
