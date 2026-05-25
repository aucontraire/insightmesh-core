# Getting Started

This guide walks you through setting up InsightMesh Core from zero. No prior familiarity with the project is assumed. The complete first-time setup takes **about 30 minutes** including downloads.

By the end you'll have:

- The project installed with all dependencies
- An Obsidian vault configured for InsightMesh
- The three sub-agents registered with Claude Code
- A successful smoke test producing a real wiki page
- A clear sense of what works, what's slow, and what to try next

---

## Prerequisites

Make sure you have these installed first. Each takes a few minutes.

### 1. Python 3.12 or newer

```bash
python3 --version
```

If you see `Python 3.12.x` or higher, you're good. Otherwise:

=== "macOS (Homebrew)"

    ```bash
    brew install python@3.12
    ```

=== "Linux (apt)"

    ```bash
    sudo apt install python3.12
    ```

=== "Other"

    See [python.org/downloads](https://www.python.org/downloads/) or use [pyenv](https://github.com/pyenv/pyenv) for version management.

### 2. uv (Python package + env manager)

InsightMesh uses [uv](https://github.com/astral-sh/uv) for all package management — much faster than pip and gives us reproducible installs.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or: brew install uv
```

Verify:

```bash
uv --version
```

### 3. Claude Code

The pipeline orchestrator uses [Claude Code](https://code.claude.com/) to invoke sub-agents. You need an active subscription and the CLI installed.

```bash
claude --version
```

If not installed: see [code.claude.com/docs](https://code.claude.com/docs).

### 4. Obsidian (recommended but optional)

The generated wiki pages are plain markdown — they work without Obsidian. But Obsidian is what makes them shine: the graph view, backlink panel, and `[[wiki link]]` rendering are the natural way to explore the wiki this tool builds.

Download from [obsidian.md](https://obsidian.md/).

### 5. Node.js (for MCPVault)

InsightMesh uses [MCPVault](https://github.com/bitbonsai/mcpvault), an MCP server distributed via `npx`. You need Node.js installed so `npx` is available.

```bash
node --version
npx --version
```

If not installed: [nodejs.org](https://nodejs.org/) or `brew install node`.

---

## Install InsightMesh

### 1. Clone the repo

```bash
git clone https://github.com/aucontraire/insightmesh-core.git
cd insightmesh-core
```

### 2. Install dependencies via uv

```bash
uv sync --all-extras
```

This creates a `.venv/` in the project, installs all runtime + dev dependencies from `pyproject.toml`, and writes a reproducible `uv.lock`. Takes about 30 seconds.

### 3. Verify the install

```bash
uv run insightmesh --help
```

You should see the `batch` command and its options. If you see "command not found," check that `uv sync` completed successfully.

### 4. Run the test suite

```bash
uv run pytest
```

Expected: **84 tests pass in under a second**. If any fail, something is wrong with the install — file an issue.

---

## Set up your Obsidian vault

You need an Obsidian vault for InsightMesh to write into. **Recommendation: create a dedicated test vault for your first runs**, separate from any personal notes you care about.

### 1. Create the vault directory

```bash
mkdir -p ~/Documents/InsightMesh-test-vault/InsightMesh
```

The `InsightMesh/` subdirectory is where generated wiki pages will live. The `.logs/` subdir (auto-created on first run) holds per-batch session logs.

### 2. Open it in Obsidian (optional but useful)

Launch Obsidian → "Open another vault as folder" → point at `~/Documents/InsightMesh-test-vault`. Obsidian will initialize it as a vault. Leave Obsidian running so you can see new pages appear in real time.

!!! tip "Why a dedicated test vault?"
    During Phase A iteration, agents can occasionally produce unexpected output. A dedicated vault lets you `rm -rf` and start over without risking personal notes. Once you're comfortable, point `--vault` at your real Obsidian vault — the Historian agent cross-links to existing pages, which is much more valuable when those pages are *yours*.

---

## Install the Obsidian Skills plugin in Claude Code

The Editor and Historian agents use [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) for proper wikilink and frontmatter syntax. It's installed as a Claude Code plugin.

Open Claude Code (`claude`), then run these two slash commands:

```text
/plugin marketplace add kepano/obsidian-skills
/plugin install obsidian@obsidian-skills
/reload-plugins
```

Verify:

```text
What skills are available?
```

You should see `obsidian:obsidian-markdown` in the list (plus four others — only `obsidian-markdown` is required by InsightMesh).

---

## Verify MCPVault discovery

The Historian and Editor agents access your vault through the [MCPVault](https://github.com/bitbonsai/mcpvault) MCP server, configured in `.mcp.json` at the repo root.

```bash
claude mcp list
```

You should see a line like:

```
mcpvault: npx -y @bitbonsai/mcpvault@latest ${VAULT_PATH} - ✓ Connected
```

If `mcpvault` doesn't appear: check that `.mcp.json` exists in the repo root and try `npx -y @bitbonsai/mcpvault@latest --help` once to warm the npm cache.

---

## Smoke test (5 minutes)

Run the included single-topic test fixture against your test vault:

```bash
uv run insightmesh batch tests/fixtures/single_topic.json --vault ~/Documents/InsightMesh-test-vault
```

Expected output:

```
Loaded 20 exchanges from /path/to/tests/fixtures/single_topic.json
Vault: /Users/you/Documents/InsightMesh-test-vault
Logs:  /Users/you/Documents/InsightMesh-test-vault/InsightMesh/.logs
Running pipeline...
Pipeline complete: 1 created, 0 updated, 1 editor decisions logged.
```

**This takes ~3-5 minutes.** That's normal for Phase A — see [Known Limitations § SC-001](known-limitations.md#sc-001-timing-2x-over-budget).

### Verify the output

```bash
ls ~/Documents/InsightMesh-test-vault/InsightMesh/
```

You should see `Speed of Light.md`. Open it in Obsidian or your editor — it's a 1,000+ word synthesized wiki page with frontmatter, headings, LaTeX math, and proper structure.

Then check the session log:

```bash
ls ~/Documents/InsightMesh-test-vault/InsightMesh/.logs/
```

A JSON file like `2026-05-17T02:30:19Z-single_topic.json` should be there — full per-agent timing, structured outputs, and EditorDecision reasoning.

### If the smoke test fails

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `error: vault path does not exist` | Wrong path | Verify `~/Documents/InsightMesh-test-vault` exists |
| Hangs > 10 minutes | Claude API rate-limited or hung | `Ctrl+C`, wait, retry |
| `error: pipeline failed: Editor agent failed: Failed to parse...` | LLM returned prose instead of JSON | Usually transient — retry. If persistent, agent prompt may need tightening |
| `mcpvault: ✗ Failed` in `claude mcp list` | npm cache cold or .mcp.json malformed | `npx -y @bitbonsai/mcpvault@latest --help` once, then re-check |
| Synthesis returns "I don't see a transcript" | Agent file out of date | Restart Claude Code session so agent files reload |

---

## Multi-topic test (validates cross-linking)

Now try a transcript covering multiple topics:

```bash
uv run insightmesh batch tests/fixtures/multi_topic.json --vault ~/Documents/InsightMesh-test-vault
```

Expected: **3 new pages** created (about light, lens optics, photography), with `[[wiki links]]` between them AND back to the `Speed of Light` page from the smoke test.

Open the new pages in Obsidian and check the graph view (Cmd-G / Ctrl-G) — you should see them all connected. That's the Historian agent doing real work via MCPVault's BM25 search.

---

## Your First Real Chat ("Free-range" stress test)

The test fixtures are short and synthetic. The real test is feeding the pipeline a real chat export. **This is where you find the rough edges.**

### Step 1: Export a real conversation

!!! tip "New in Spec 002: multi-conversation exports are now first-class"
    Point `insightmesh list` at any Claude.ai or ChatGPT data export — it parses the export (via the [`echomine`](https://pypi.org/project/echomine/) library), prints a table of conversations, then you pass `--conversation <id-or-index>` to `insightmesh batch` to synthesize the one you picked. No `jq` pipelines, no manual reshaping. The Spec 001 flat-array transcript format is still supported unchanged (FR-014 backward compat).

=== "ChatGPT"

    Settings → Data Controls → Export data. You'll get a zip with `conversations.json` — an array of conversation objects, not a flat message array.

    Run `uv run insightmesh list ~/Downloads/conversations.json` to see the table of conversations in your export, then `uv run insightmesh batch ~/Downloads/conversations.json --conversation <id-or-index> --vault <vault>` to synthesize the one you picked. EchoMine handles the schema parsing under the hood.

=== "Claude (web)"

    Settings → Account → Export data. Similar structure to ChatGPT — array of conversation objects, each with its own messages. Same extract-and-reshape workflow as above.

=== "EchoMine or other tools"

    Should already be in the standard `[{"role": "user", "content": "..."}, ...]` shape. If not, reshape it.

### Step 2: Start small

Pick a conversation with **20-50 exchanges** for your first real run. Anything longer will be slow and expensive on your first attempt.

```bash
uv run insightmesh batch ~/Downloads/my-conversation.json --vault ~/Documents/InsightMesh-test-vault
```

### Step 3: Expectations

| Conversation size | Expected runtime | Expected cost |
|-------------------|------------------|---------------|
| ~20 exchanges (smoke test fixture) | 3-5 min | a few cents |
| ~50 exchanges | 5-10 min | ~10-25 cents |
| ~200 exchanges | 15-30 min | ~$1-3 |
| 500+ exchanges | may hit token limits — split into chunks |

!!! warning "Cost is real"
    Every run calls Claude API multiple times. There's no free tier for the Agent SDK in 2026 — usage draws from your Claude plan's Agent SDK credits or pay-per-use. Budget accordingly.

### Step 4: Review the output

After the run:

1. **Read the generated pages.** Do they accurately summarize what you discussed? Do they read as coherent prose, not a transcript dump?
2. **Check the cross-links.** Did the Historian find sensible related pages in your vault?
3. **Read the session log.** The EditorDecision rationale tells you *why* each page was created/updated — invaluable for debugging.

### Common "first real chat" findings

- **Synthesis split a topic you thought was one** — that's usually fine. The agent uses its judgment about topic boundaries.
- **Pages are too short** — your conversation may have been more exploratory than substantive. Synthesis only writes what it has.
- **Cross-links missed something obvious** — Historian uses BM25 keyword search, not semantic search. Phase B will add embeddings.
- **One run took 20 minutes** — the orchestrator overhead is the bottleneck. See [Known Limitations](known-limitations.md).

---

## Where to go from here

- **Browse the [Known Limitations](known-limitations.md)** so you know what to expect and what's coming
- **Read your session logs** — they're rich data for understanding agent behavior
- **Try running the same transcript twice** — see how stable the synthesis is across runs (it's surprisingly consistent)
- **Watch this project for Spec 002** (live inquiry mode) and Spec 003 (Critic + Researcher agents)

---

## Troubleshooting

### `uv: command not found`

Add uv to your shell PATH. The installer prints instructions; usually:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### `claude: command not found`

Install Claude Code per [code.claude.com/docs](https://code.claude.com/docs).

### `ModuleNotFoundError: No module named 'src'`

You forgot `uv run` — running raw `python` or `pytest` won't use the project venv. Always prefix with `uv run`.

### Pipeline hangs forever

There's no built-in timeout on the LLM calls. If a run is silent for >10 minutes, `Ctrl+C` and inspect:

```bash
ps aux | grep -E "insightmesh|mcpvault|claude.*query"
```

Kill stale processes if needed:

```bash
pkill -f mcpvault
```

### MCPVault subprocess crashes

Cold-start `npx` issues are the most common cause. Run once to warm the cache:

```bash
npx -y @bitbonsai/mcpvault@latest --help
```

### "Editor agent failed: Failed to parse editor output"

The LLM returned prose instead of pure JSON. Usually transient — retry. The full raw response is saved to `.specify/scratch/agent_responses/` for diagnosis.

### Need more help

Open an issue: [github.com/aucontraire/insightmesh-core/issues](https://github.com/aucontraire/insightmesh-core/issues)
