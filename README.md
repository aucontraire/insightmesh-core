# InsightMesh Core

> A cognitive knowledge engine that compounds understanding over time through multi-agent investigative inquiry, persisted as an evolving Obsidian wiki.

[![Docs](https://img.shields.io/badge/docs-aucontraire.github.io-1976d2.svg)](https://aucontraire.github.io/insightmesh-core/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Types: mypy strict](https://img.shields.io/badge/types-mypy%20strict-blue)](https://mypy-lang.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

**📚 Docs: [aucontraire.github.io/insightmesh-core](https://aucontraire.github.io/insightmesh-core/)** — install walkthrough, how-to guides, CLI reference.

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

> 🚀 **Latest (v0.5.0)**: Spec 005 adds per-page provenance — every wiki page carries a cumulative `provenance:` frontmatter block (latest checkpoint, contributing conversations, action, confidence, total edits, exchange count), backed by a permanent per-checkpoint JSON record at `<vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-<NNN>.json` and a shadow git repository for `git log -p` style diff history of every page's evolution. All three transcript shapes (Claude.ai / ChatGPT / Spec 001 flat-array) validated on real data. See the [CLI reference](https://aucontraire.github.io/insightmesh-core/reference/cli/#per-checkpoint-provenance-record-spec-005) for the on-disk layout. A dedicated in-Obsidian viewer plugin ([insightmesh-obsidian](https://github.com/aucontraire/insightmesh-obsidian)) is now in the [Obsidian community plugin browser](https://community.obsidian.md/plugins/insightmesh-viewer) — in Obsidian: Settings → Community plugins → Browse, search for "InsightMesh Viewer", and Install. Pre-release builds remain available via [BRAT](https://github.com/TfTHacker/obsidian42-brat) (`Add Beta Plugin → aucontraire/insightmesh-obsidian`).
>
> Prior milestones: Spec 002 added Claude.ai / ChatGPT export support (`insightmesh list <export.json>` + `--conversation <id-or-index>`); Spec 003 added attachment and pasted-text synthesis for Claude exports. The Spec 001 flat `{role, content}` transcript format is still supported unchanged (FR-014 backward compat).

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
| Chat-to-wiki batch synthesis | ✅ Spec 001 |
| Multi-page cross-linking | ✅ |
| Session logging + decision rationale | ✅ |
| Same-topic update detection | ✅ |
| Multi-conversation export selection (Claude.ai / ChatGPT) | ✅ Spec 002 |
| Pre-flight validation (vault + agent presence) | ✅ Spec 002 |
| Attachment and pasted-text synthesis (Claude exports) | ✅ Spec 003 |
| Long-chat checkpointing + auto-resume + per-invocation cap | ✅ Spec 004 |
| Per-page provenance (checkpoint JSON + frontmatter + shadow-git diff history) | ✅ Spec 005 |
| Live inquiry (ask questions, refine, synthesize) | 🟡 planned |
| Bias/assumption checking (Critic agent) | 🟡 planned |
| Web research (Researcher agent) | 🟡 planned |

See **[Known Limitations](docs/known-limitations.md)** for the honest list of what doesn't work yet.

## Documentation

**Published docs: [aucontraire.github.io/insightmesh-core](https://aucontraire.github.io/insightmesh-core/)**

- **[Getting Started](https://aucontraire.github.io/insightmesh-core/getting-started/)** — install + first-run walkthrough
- **[How-to: Long conversations](https://aucontraire.github.io/insightmesh-core/how-to/long-conversations/)** — resume, pace, recover, re-process
- **[How-to: Troubleshooting](https://aucontraire.github.io/insightmesh-core/how-to/troubleshooting/)** — install errors, hung pipelines, MCPVault crashes, agent parse failures
- **[Reference: CLI](https://aucontraire.github.io/insightmesh-core/reference/cli/)** — every command, every flag, exit codes, cursor schema
- **[Known Limitations](https://aucontraire.github.io/insightmesh-core/known-limitations/)** — what's not done, what's slow, what's planned

Local docs preview: `uv run mkdocs serve`

## License

[AGPL-3.0](LICENSE). If you run a modified version as a network service, the AGPL requires you to make your changes available to its users.
