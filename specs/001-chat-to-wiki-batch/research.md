# Research: Chat-to-Wiki Batch Synthesis

**Feature**: `001-chat-to-wiki-batch` | **Date**: 2026-05-16

## Research Summary

All technical decisions resolved. No NEEDS CLARIFICATION items remain.

## Decision 1: Sub-Agent Orchestration Pattern

**Decision**: Sequential pipeline with file-based state passing  
**Rationale**: Synthesis → Historian → Editor. Each agent writes intermediate results to a working directory. Next agent reads those results plus its own inputs. This avoids prompt context bloat for large transcripts and ensures every intermediate output is logged for evaluation.  
**Alternatives considered**:
- All-in-one single agent: Violates single responsibility, makes per-agent evaluation impossible
- Prompt-injection data passing: Transcript content can be large; intermediate outputs need logging anyway

## Decision 2: Transcript Parsing

**Decision**: Python `json` module parsing JSON arrays of `{role, content}` objects  
**Rationale**: ChatGPT exports use `[{role: "user", content: "..."}, {role: "assistant", content: "..."}]`. Claude exports follow the same pattern. Standard library is sufficient.  
**Alternatives considered**:
- Multi-format support: Deferred per spec clarification (JSON only for Spec 001)
- Third-party parsing libraries: Overkill for simple JSON arrays

## Decision 3: Wiki Page Generation

**Decision**: Agents write wiki pages via **MCPVault** MCP server (attached per-agent via `AgentDefinition.mcpServers`). Obsidian-specific knowledge (wikilinks, frontmatter, tags) comes from **kepano/obsidian-skills** `obsidian-markdown` preloaded into each agent via `AgentDefinition.skills`.  
**Rationale**: MCPVault provides safe atomic file operations and AST-aware frontmatter editing — safer than naive Python writes. kepano's official Obsidian Skills give all three agents authoritative syntax knowledge without re-deriving it in each prompt. Python's `wiki.py` module only defines Pydantic models + pure helpers (e.g., `normalize_title`); actual file I/O happens from inside agents.  
**Alternatives considered**:
- Direct Python `pathlib` from orchestrator: rejected — bypasses MCPVault safety and forces orchestrator to know Obsidian syntax
- Obsidian REST API: rejected — requires Obsidian running, heavier dependency than MCPVault

## Decision 4: Vault Search (Historian)

**Decision**: Historian uses **MCPVault's BM25 search** against the vault (via `AgentDefinition.mcpServers`). Searches the full vault (pages from prior sessions AND pages created earlier in the current batch — see spec.md §FR-005).  
**Rationale**: BM25 provides relevance ranking on title + content matches — dramatically better than naive substring matching. MCPVault is already attached to the Historian (Decision 3), so search is a free capability. Semantic search via pgvector is a Phase B enhancement.  
**Alternatives considered**:
- Python `glob` + string matching: rejected — poor relevance, no ranking, requires custom search code MCPVault provides natively
- pgvector: deferred to Phase B (requires PostgreSQL, premature for sub-agent prototyping)

## Decision 5: Session Logging

**Decision**: One JSON file per session, timestamped filename, per-agent output sections  
**Rationale**: Enables per-agent quality evaluation. JSON is parseable and becomes schema blueprint for Phase B PostgreSQL. Each session is self-contained and independently inspectable.  
**Alternatives considered**:
- SQLite: Adds dependency for minimal benefit at this stage
- Single append log: Harder to parse individual sessions

## Decision 6: Agent Definition Format

**Decision**: Filesystem `.md` files in `.claude/agents/` with **full YAML frontmatter** (name, description, model, tools, skills, mcpServers) plus prompt body. Auto-discovered by the SDK in non-bare mode.  
**Rationale**: Verified at code.claude.com/docs/en/sub-agents §Supported frontmatter fields — filesystem `.md` supports the same complete field set as programmatic `AgentDefinition`. Single source of truth per agent. No Python-side config duplication.  
**Alternatives considered**:
- Programmatic `AgentDefinition` + `--bare` mode: more deterministic but duplicates config. Phase B target.
- Filesystem `.md` for prompt only, Python config for skills/tools: rejected — splits one config across two artifacts unnecessarily

## Decision 7: Sub-Agent Invocation Mechanism

**Decision**: Python orchestrator uses the **`claude-agent-sdk`** Python package (`query()` + `AgentDefinition`). Verified via official docs at `code.claude.com/docs/en/agent-sdk/subagents`.  
**Rationale**:
- Native Python API, no subprocess parsing
- `AgentDefinition.skills` field preloads kepano/obsidian-skills per agent
- `AgentDefinition.mcpServers` field attaches MCPVault per agent
- `AgentDefinition.tools` enforces least-privilege per agent
- Native Pydantic integration for structured output
- Per-agent context isolation (parent doesn't bloat with sub-agent intermediates)
- Justified under constitution v1.1.0 Project Standards (single-bounded-capability dep)

**Alternatives considered**:
- `subprocess` to `claude -p --json-schema`: works but requires manual JSON parsing and forfeits per-agent isolation tracking that the SDK provides natively
- Reframe orchestrator as a Claude Code skill: viable but breaks the CLI interface promised in quickstart.md
