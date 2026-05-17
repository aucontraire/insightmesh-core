# InsightMesh Core Constitution

## Mission Statement

A personal knowledge engine that compounds understanding over time through multi-agent investigative inquiry, persisted as an evolving Obsidian wiki. Local-first, transparent, intellectually rigorous.

**Product Stance:**
- Personal tool: for individual users building and retaining knowledge
- Local-first: all data stays on your machine (Obsidian vault), never transmitted
- Compounding knowledge: every inquiry enriches future inquiries
- Intellectual rigor: bias detection, source attribution, transparent reasoning
- Not a SaaS: no cloud hosting, no accounts, no data collection

## Core Principles

### I. Anti-Slop Engineering (NON-NEGOTIABLE)

AI-generated code tends toward over-engineering, speculative abstraction, and unnecessary complexity. This project was born from that exact failure (CogniVault: 167 files for 4 agents). Every principle here exists to prevent repeating that mistake.

**Requirements:**
- **Minimal-Diff Principle**: Prefer modifying existing code over adding new files. Every new file MUST be justified. Features MUST aim for the smallest diff that satisfies acceptance criteria.
- **File and Abstraction Budgets**: Each feature branch MUST introduce as few new files as possible. No new abstraction unless it removes duplication across multiple call sites.
- **The Rule of Three**: Don't extract a pattern until you've seen it three times.
- **Dependency Discipline**: Distinguish force-multiplier dependencies from architecture dependencies.
  - ✅ **Welcome**: Dependencies that reduce code volume (validation, serialization, parsing primitives), catch errors at the type level (typed models, static analysis), or provide a single bounded capability (one API client, one test runner).
  - ❌ **Reject**: Dependencies that add abstraction layers (orchestration frameworks, ORMs over a single DB, service meshes), enable speculative capability (multi-provider abstractions before multiple providers exist), or solve problems not yet measured.
  - New dependencies outside the Project Standards section MUST be added to the Complexity Justification Table with rationale.
- **No Speculative Architecture**: Build for what you need now, not what you might need later. YAGNI is law.

**Rationale**: CogniVault had 167 files, 20+ modules, and complex factory hierarchies for a 4-agent system. The patterns were good but buried under accidental complexity. A new developer needed weeks to understand it. InsightMesh targets 30-40 focused files where a new developer is productive in hours. The dependency distinction matters: CogniVault didn't fail because it had dependencies — it failed because it had *abstraction* dependencies (LangGraph wrappers, repository patterns) that hid simple operations behind generic interfaces.

### II. Incremental Delivery (NON-NEGOTIABLE)

Every feature must be delivered as a working, independently testable slice. No "phase 1 of 5 that only works when all 5 are done."

**Requirements:**
- Each user story is independently testable and delivers standalone value
- P1 stories form a working MVP without P2 or P3
- Sub-agent prototypes validate behavior before production infrastructure is built
- Build the simplest thing that works, then iterate based on real usage

**Rationale**: Two prior attempts (CogniVault, insightmesh) produced extensive planning documents and architecture but no working product. Incremental delivery forces working software at every step.

### III. Transparency and Intellectual Rigor

Every output must be traceable, attributed, and honest about its limitations.

**Requirements:**
- All synthesized content includes source attribution (LLM knowledge, web sources, or prior wiki pages)
- Bias and assumptions are surfaced explicitly, not hidden
- The multi-agent process is visible in the output — what each agent contributed
- Limitations and confidence levels are stated, not glossed over

**Rationale**: This is what differentiates InsightMesh from NotebookLM and generic chatbots. The user trusts the system because it shows its work and acknowledges what it doesn't know.

## Project Standards

Baseline toolchain for all Python work in this project. These dependencies are pre-justified and do not require entries in the Complexity Justification Table.

**Runtime:**
- Python 3.12+
- **Pydantic v2** — data models, validation, JSON serialization (force multiplier: replaces manual `to_dict`, `from_dict`, validators)
- **claude-agent-sdk** — programmatic invocation of Claude Code sub-agents (single bounded capability)
- **Typer** — CLI parsing driven by type hints (force multiplier: replaces argparse boilerplate; plays well with Pydantic)

**Development:**
- **uv** — Python package and environment manager (single bounded capability: replaces pip + venv + pip-tools)
- **mypy** in strict mode — static type checking
- **Ruff** — linting (replaces flake8, isort, pyupgrade)
- **Black** — code formatting (or `ruff format` equivalent)
- **pytest** — testing primitive
- **MkDocs Material** — documentation site generator (de facto standard for Python projects in 2026; FastAPI, Pydantic, Typer, Ruff, etc. all use it)

All commands run via `uv run <cmd>` to use the project venv automatically. `uv.lock` is committed for reproducibility.

**Conventions:**
- Strict typing throughout source and tests (no untyped `dict` / `Any` in public APIs)
- **All Python classes that group fields together — runtime models, internal containers, response shapes, in-memory buffers, agent records, anything with attributes — MUST be `pydantic.BaseModel` subclasses with `ConfigDict(strict=True)`. The `@dataclass` decorator, `typing.NamedTuple`, and `collections.namedtuple` are PROHIBITED for new data shapes in `src/` and `tests/`.** Exception: third-party libraries that require dataclass-typed inputs — document in the Complexity Justification Table. Enforced mechanically via the ruff `TID251` rule (see `pyproject.toml` `[tool.ruff.lint.flake8-tidy-imports.banned-api]`).
- All async functions explicitly typed for return values
- Test fixtures use Pydantic models for input validation

## Architecture Principles

### Single Responsibility

Each class/function MUST have one clear purpose. This prevents god classes and enables testability. CogniVault's Synthesis agent was overloaded — InsightMesh splits it into Synthesis + Editor.

### Dependency Inversion

Depend on abstractions, not concretions. Use interfaces for services where appropriate. But don't create abstractions speculatively — only when you have 2+ implementations.

### Stateless Agents

Each agent receives explicit input and returns explicit output. No shared mutable state between agents. No relying on conversation context. This maps cleanly from Claude Code sub-agents to LangGraph nodes.

## Code Quality Principles

### Clarity Over Cleverness

Adopt idiomatic code. Favor clarity over cleverness. If code requires comments to explain what it does, consider rewriting it.

### Explicit Error Types

Create custom exceptions for domain-specific errors. Use structured error handling with explicit types.

### Structured Logging

Use consistent log levels and include relevant context. Never log sensitive data.

## Complexity Justification Table

When violating Anti-Slop principles, the following table MUST be included in PR description:

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New file added | [specific reason] | [why existing file insufficient] |
| New dependency | [capability needed] | [why existing deps insufficient] |
| New abstraction | [pattern emerging] | [why direct code insufficient] |

## Governance

### Constitution Authority

This constitution supersedes all other practices. When conflict arises, constitutional principles take precedence.

- All PRs MUST verify compliance before merge
- AI assistants MUST follow these rules unconditionally
- Violations require explicit justification in PR description
- CLAUDE.md provides operational guidance; constitution provides law

### Amendment Procedure

To amend this constitution:
1. Create ADR documenting the proposed change
2. Provide rationale with concrete examples
3. Show migration plan for affected code
4. Obtain stakeholder approval
5. Update constitution.md with new version number

### Versioning Policy

Constitution version follows semantic versioning:
- **MAJOR**: Backward-incompatible governance/principle removals or redefinitions
- **MINOR**: New principle/section added or materially expanded guidance
- **PATCH**: Clarifications, wording, typo fixes, non-semantic refinements

**Version**: 1.1.4 | **Ratified**: 2026-05-16 | **Last Amended**: 2026-05-16

### Changelog

- **1.1.4** (2026-05-16): Added `MkDocs Material` to Project Standards Development list for project documentation. Single-bounded-capability dep (documentation rendering only). Justified by 2026-standard Python ecosystem convention (FastAPI, Pydantic, Typer, Ruff all use it) and "building in public" content strategy benefits from a real docs site early.
- **1.1.3** (2026-05-16): Added `Typer` to Project Standards Runtime list. Justified as force-multiplier dep (type-hint-driven CLI parsing, replaces argparse boilerplate). Single bounded capability (CLI parsing). Aligns with the project's broader pattern of letting type hints drive behavior (Pydantic for data, mypy strict for checking, Typer for CLI).
- **1.1.2** (2026-05-16): Sharpened the Pydantic-vs-dataclass rule in §Project Standards Conventions to remove ambiguity ("all data models" → "all Python classes that group fields together"). Explicitly prohibited `@dataclass`, `typing.NamedTuple`, `collections.namedtuple` in `src/` and `tests/`. Added mechanical enforcement via ruff `TID251` (`flake8-tidy-imports.banned-api`). Root cause: implementation slipped a `@dataclass` for an internal container because the old "data models" wording was ambiguous and the rule had no automated enforcement.
- **1.1.1** (2026-05-16): Added `uv` to Project Standards. Justified as single-bounded-capability dep (env + package management). `uv.lock` committed for reproducibility. All commands now run via `uv run`.
- **1.1.0** (2026-05-16): Refined Dependency Discipline to distinguish force-multiplier deps from architecture deps. Added Project Standards section codifying baseline toolchain (Pydantic v2, claude-agent-sdk, mypy strict, Ruff, Black, pytest) so future specs don't re-justify.
- **1.0.0** (2026-05-16): Initial constitution with Anti-Slop, Incremental Delivery, and Transparency principles.
