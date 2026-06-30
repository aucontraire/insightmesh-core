# CLAUDE.md — InsightMesh Core

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at `specs/005-page-provenance/plan.md`.
<!-- SPECKIT END -->

**Governance note**: This file provides operational guidance for AI assistants working on this project. The constitution (`.specify/memory/constitution.md`) provides project law — architectural principles, quality gates, governance. When in conflict, the constitution takes precedence.

---

## 0a. Python environment — always use `uv run`

This project uses **uv** for Python env and dependency management (per constitution v1.1.1 §Project Standards). All Python commands MUST run via `uv run` so the project venv is used automatically:

- `uv run pytest` (not `pytest` or `.venv/bin/pytest`)
- `uv run mypy --strict src/` (not bare `mypy`)
- `uv run python -m src.cli ...` (not `python3 ...`)
- `uv run ruff check .`, `uv run black .`

Adding a dependency: edit `pyproject.toml` then `uv sync` (or `uv add <pkg>` for runtime, `uv add --dev <pkg>` for dev).

If `.venv` is missing or out of sync: `uv sync --all-extras` rebuilds it from `pyproject.toml` + `uv.lock`.

**Do NOT** use bare `pip`, `python3 -m pip`, or `.venv/bin/...` invocations. The `uv.lock` file is the source of truth for reproducible installs and is committed to git.

## 0b. Delegate to claude-code-guide for Claude Code self-knowledge

Before making any architectural decision that depends on Claude Code's own surface — CLI flags, `AgentDefinition` fields, MCP integration, hooks, skills, headless mode, `claude-agent-sdk` API — invoke the `claude-code-guide` agent. Do not speculate from training.

This is especially important for InsightMesh: the entire Phase A architecture (orchestrator, agents, MCPVault attachment, Obsidian Skills preloading) depends on the Claude Agent SDK surface staying accurate. A wrong assumption here propagates through every spec and task.

Pattern: `Agent(subagent_type="claude-code-guide", description="...", prompt="What is the exact ... ?")` — use its answer as authoritative.

(This rule is also in `~/.claude/CLAUDE.md` for cross-project enforcement.)

## 0c. Class Registry — consult before authoring or refactoring classes

`.claude/class-registry.json` — AST-derived index of every class under `src/` (gitignored; regenerated automatically). For any class you're about to author, import, instantiate, subclass, or rename: consult the registry first. Do not grep + guess; do not assume a field exists because it would be reasonable. The codebase is small (~50 classes today, no duplicate names) but the hallucination guard is highest-value before duplicates appear, when the cost of "picking the wrong real class" goes from theoretical to silent breakage.

Helpers in `.claude/tools/`:
- `generate_class_registry.py` — rebuild the registry (runs automatically; call manually if stale: `uv run python .claude/tools/generate_class_registry.py`)
- `analyze_class_usage.py <ClassName> [--json]` — every import / inheritance / instantiation / type-annotation / reference of a class, with file:line. Use BEFORE any rename.
- `validate_class_conflicts.py [--stats | --suggest <Name>]` — reports duplicate-name conflicts; suggests renames informed by module stem + class type.

Auto-refresh: pre-commit hook (`./.pre-commit-config.yaml`) on Python file changes under `src/`, plus a Claude Code PostToolUse hook (`.claude/settings.json` → `_hook_regen_registry.py`) with a 15-second mtime debounce so back-to-back edits don't thrash. Failures are silent — a broken tool never blocks edits; failures surface at pre-commit time.

---

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
- Read implementation files before writing code that uses existing classes. Never assume field names, method signatures, or parameters.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- No docstrings, comments, or type annotations on code you didn't change.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## Active Technologies
- Python 3.12 with strict typing (mypy strict mode)
- **Pydantic v2** for all data models (per constitution v1.1.3 Project Standards)
- **claude-agent-sdk** for orchestrating sub-agents programmatically
- **Typer** for CLI parsing driven by type hints
- **kepano/obsidian-skills** (obsidian-markdown) preloaded per agent via `AgentDefinition.skills`
- **MCPVault** MCP server for vault read/write/search/frontmatter (attached per agent via `AgentDefinition.mcpServers`)
- Dev tooling: Ruff, Black, pytest
- Obsidian vault (local filesystem markdown files via MCPVault) + JSON session log files

## Recent Changes
- 001-chat-to-wiki-batch: Replaced subprocess approach with claude-agent-sdk (verified via official docs). MCPVault and Obsidian Skills attached per-agent. Pydantic v2 adopted as project-wide standard. Constitution amended to v1.1.0 with refined Dependency Discipline + Project Standards section.
