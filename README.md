# InsightMesh Core

> A cognitive knowledge engine that compounds understanding over time through multi-agent investigative inquiry, persisted as an evolving Obsidian wiki.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Types: mypy strict](https://img.shields.io/badge/types-mypy%20strict-blue)](https://mypy-lang.org/)
[![License: TBD](https://img.shields.io/badge/license-TBD-lightgrey.svg)](#license)

InsightMesh turns your AI chat history into a **growing wiki you actually own**. Local-first, cross-linked, transparent about what it knows.

---

## What it does

You spend hours having intellectually rich conversations with Claude or ChatGPT — and then lose all of that context the moment the session ends. InsightMesh fixes that by reading your transcripts and synthesizing them into organized, cross-linked Obsidian wiki pages.

- **Sub-agent pipeline**: Synthesis → Historian → Editor, each with a single responsibility, all coordinated via the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview).
- **Cross-linking that compounds**: the Historian agent searches your vault (via [MCPVault](https://github.com/bitbonsai/mcpvault)) and weaves `[[wiki links]]` between new and existing pages.
- **Honest reasoning trail**: every page write decision is logged with full rationale — what signals matched, what got skipped, why.
- **Local-first**: data stays on your machine in your Obsidian vault. No cloud, no accounts.

## How it differs from NotebookLM, Perplexity, etc.

- **Knowledge compounds**: not a one-shot research tool — inquiry #50 is richer than inquiry #1 because prior pages get pulled into the synthesis.
- **You own the data**: markdown files in your Obsidian vault, version-controlled, portable.
- **Intellectual transparency**: the multi-agent process is visible in the output. Every decision has a rationale.

## Quick taste

```bash
# After install (see Getting Started)
uv run insightmesh batch my-conversation.json --vault ~/Obsidian/MyVault
```

```
Loaded 20 exchanges from my-conversation.json
Vault: /Users/you/Obsidian/MyVault
Logs:  /Users/you/Obsidian/MyVault/InsightMesh/.logs
Running pipeline...
Pipeline complete: 3 created, 0 updated, 3 editor decisions logged.
```

Three new wiki pages appear in `~/Obsidian/MyVault/InsightMesh/`, cross-linked via `[[wiki links]]`, with full session log in `.logs/`.

> 🚀 **Spec 002 lands export support**: run `insightmesh list ~/Downloads/conversations.json` to browse a Claude.ai or ChatGPT data export, then `insightmesh batch <export.json> --conversation <id-or-index> --vault ~/Obsidian/MyVault` to synthesize the one you picked. The Spec 001 flat `{role, content}` transcript format is still supported unchanged (FR-014 backward compat).

## Getting started

See **[Getting Started](docs/getting-started.md)** for a complete beginner walkthrough: prerequisites, install, Obsidian vault setup, Claude Code plugin install, smoke test, and your first real chat run.

## Architecture (Phase A)

```
chat transcript (JSON)
        │
        ▼
   ┌────────────┐
   │ Synthesis  │  reads exchanges, drafts wiki pages by topic
   └─────┬──────┘
         │ WikiPageDraft[]
         ▼
   ┌────────────┐
   │ Historian  │  searches vault for related pages, adds cross-link recommendations
   └─────┬──────┘  (uses MCPVault BM25)
         │ augmented WikiPageDraft[]
         ▼
   ┌────────────┐
   │   Editor   │  writes final pages via MCPVault, decides create-vs-update
   └─────┬──────┘
         │ EditorOutput (results + decisions)
         ▼
   Obsidian vault + session log JSON
```

Three sub-agents defined as markdown files in `.claude/agents/`, orchestrated through `claude-agent-sdk`. The Editor agent uses the [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) `obsidian-markdown` skill for proper wikilink and frontmatter syntax.

Phase B (planned in Spec 002+) will migrate orchestration to LangGraph for deterministic execution.

## Status

| Feature | Status |
|---------|--------|
| Chat-to-wiki batch synthesis | ✅ Spec 001 — working |
| Multi-page cross-linking | ✅ |
| Session logging + decision rationale | ✅ |
| Same-topic update detection | ✅ |
| Multi-conversation export selection (pick a chat from a Claude.ai/ChatGPT export) | ✅ Spec 002 |
| Live inquiry (ask questions, refine, synthesize) | 🟡 Spec 002 — planned |
| Bias/assumption checking (Critic agent) | 🟡 Spec 003 — planned |
| Web research (Researcher agent) | 🟡 Spec 003 — planned |

See **[Known Limitations](docs/known-limitations.md)** for the honest list of what doesn't work yet.

## Documentation

- **[Getting Started](docs/getting-started.md)** — install + first run walkthrough
- **[Known Limitations](docs/known-limitations.md)** — what's not done, what's slow, what's planned

Local docs preview: `uv run mkdocs serve`

## License

TBD.
