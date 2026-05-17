# Implementation Plan: Chat-to-Wiki Batch Synthesis

**Branch**: `001-chat-to-wiki-batch` | **Date**: 2026-05-16 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/001-chat-to-wiki-batch/spec.md`

## Summary

Process existing chat transcripts (JSON format) into organized Obsidian wiki pages using three Claude Code sub-agents (Synthesis, Historian, Editor). A thin Python CLI orchestrates the pipeline: parse transcript → fan out to agents → write wiki pages → log session as JSON. This is Phase A (sub-agent prototyping) — no production infrastructure, no database, no web framework.

## Technical Context

**Language/Version**: Python 3.12 (strict typing, per constitution v1.1.0 Project Standards)  
**Primary Dependencies**:
- `claude-agent-sdk` — programmatic Claude Code sub-agent invocation (replaces subprocess approach)
- `pydantic` v2 — data models, validation, JSON serialization
- `typer` — CLI parsing via type hints (per constitution v1.1.3)
- Standard library: `json`, `pathlib`, `datetime`, `asyncio`, `os`

**External integrations (Phase A)**:
- **kepano/obsidian-skills** ([github.com/kepano/obsidian-skills](https://github.com/kepano/obsidian-skills)) — official Obsidian markdown knowledge skill (preloaded into each agent via `AgentDefinition.skills`)
- **MCPVault** ([github.com/bitbonsai/mcpvault](https://github.com/bitbonsai/mcpvault)) — MCP server for vault read/write/search (attached per-agent via `AgentDefinition.mcpServers`)

**Dev tooling**: mypy strict, Ruff, Black, pytest (per constitution Project Standards)  
**Storage**: Obsidian vault (local filesystem markdown files via MCPVault) + JSON log files  
**Testing**: pytest with strict typing  
**Target Platform**: macOS (local development)  
**Project Type**: CLI tool  
**Performance Goals**: 20-exchange transcript processed in under 2 minutes  
**Constraints**: Local-only, no cloud storage, no database. LLM calls via Claude Code sub-agents.  
**Scale/Scope**: Single user, transcripts up to ~500 exchanges (larger deferred per spec assumptions)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Anti-Slop Engineering (NON-NEGOTIABLE)**:
- File budget: Target ~18 files total (3 agents + 5 source + 3 tests + 5 fixtures + 2 config)
- No abstractions for single-use code — agents are standalone markdown prompts
- Dependencies: All within constitution Project Standards (Pydantic v2, claude-agent-sdk) — pre-justified. No additional deps needed for Spec 001.
- External integrations (Obsidian Skills, MCPVault) are configuration-level, not abstractions
- No speculative architecture — only 3 agents, only batch mode, only JSON input
- **PASS**

**Incremental Delivery (NON-NEGOTIABLE)**:
- Story 1 (batch synthesis) independently testable with a sample transcript
- Story 2 (logging) independently testable by checking JSON output
- Sub-agents validate behavior before any production infrastructure
- **PASS**

**Transparency and Intellectual Rigor**:
- Session logs capture each agent's output independently for evaluation
- Wiki pages attribute content to source transcript
- Agent process visible via logging (which agent produced what)
- **PASS**

**Architecture Principles**:
- Single responsibility: Synthesis synthesizes, Historian searches, Editor writes
- Stateless agents: explicit input in, structured output out, no shared state
- **PASS**

**Initial Constitution Check: PASS** — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/001-chat-to-wiki-batch/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks command)
```

### Source Code (repository root)

```text
.claude/agents/
├── synthesis.md               # Synthesis sub-agent prompt (also loaded via AgentDefinition)
├── historian.md               # Historian sub-agent prompt
└── editor.md                  # Editor sub-agent prompt

pyproject.toml                 # Project config: deps, ruff, black, mypy, pytest
.mcp.json                      # MCPVault MCP server config

src/
├── cli.py                     # CLI entry point (Typer, asyncio runner)
├── transcript.py              # Pydantic models + JSON transcript parser
├── orchestrator.py            # Agent coordination via claude-agent-sdk
├── wiki.py                    # Pydantic models for WikiPage entities (file I/O happens via MCPVault from agents)
└── logger.py                  # Pydantic models + Session JSON logger

tests/
├── test_transcript.py         # Transcript parsing tests
├── test_wiki.py               # Wiki page model tests
├── test_logger.py             # Session logging tests
└── fixtures/
    ├── single_topic.json      # 20-exchange, one topic (MVP fixture)
    ├── multi_topic.json       # 30-exchange, 3+ distinct topics
    ├── revisit.json           # Topic revisit / deepening scenario
    ├── malformed.json         # Invalid JSON for error path
    └── empty.json             # Empty transcript for error path
```

**Structure Decision**: Single project (Option 1). CLI tool with flat `src/` layout. 5 Python source + 3 agent definitions + 3 test files + 5 fixtures + 2 config (pyproject.toml, .mcp.json) = 18 files total. Within the 30-40 file target from the Goldilocks Strategy.

**Data model location**: Pydantic v2 `BaseModel` subclasses defined in `data-model.md` live inside their natural module rather than a separate `models/` package (per anti-slop principle):
- `Message`, `Exchange`, `ChatTranscript` → `src/transcript.py`
- `WikiPage`, `WikiPageDraft`, `WikiPageResult` → `src/wiki.py`
- `SessionLog`, `AgentOutput`, `SynthesisOutput`, `HistorianOutput`, `EditorOutput`, `EditorDecision`, `EditorDecisionSignals`, `CrossLinkRecord`, `SessionError` → `src/logger.py`

## Phase 0: Research

### Decision 1: Sub-Agent Orchestration Pattern
- **Decision**: Sequential pipeline with file-based state passing (Synthesis → Historian → Editor)
- **Rationale**: Each agent runs once per batch on the full set of drafts. Synthesis emits WikiPageDraft objects; Historian augments them with cross-link recommendations; Editor writes pages to vault. Intermediate outputs are persisted to the session log for per-agent evaluation. See spec.md §Agent Contracts and §Pipeline Coordination for the canonical contracts.
- **Alternatives considered**: (a) All-in-one single agent — rejected because it violates single responsibility and makes per-agent evaluation impossible. (b) Prompt-injection data passing — rejected because transcript content can be large, and we need logged intermediate outputs anyway.

### Decision 2: Transcript Parsing Strategy
- **Decision**: Parse JSON array of `{role, content}` objects using Python standard library `json` module
- **Rationale**: ChatGPT and Claude exports use this format. Standard library `json` is sufficient — no third-party parser needed. Group exchanges into logical conversation turns (user prompt + assistant response = 1 exchange).
- **Alternatives considered**: (a) Support multiple formats from day one — rejected per clarification (JSON only for now). (b) Use pandas or structured parsing library — rejected, overkill for simple JSON arrays.

### Decision 3: Wiki Page Generation
- **Decision**: Agents write wiki pages to the vault via **MCPVault** MCP server. The Editor agent has MCPVault attached via `AgentDefinition.mcpServers` and uses its `write`/`patch` tools. Obsidian-specific knowledge (wikilink syntax, frontmatter format) comes from the **kepano/obsidian-skills** `obsidian-markdown` skill preloaded into each agent via `AgentDefinition.skills`.
- **Rationale**: MCPVault provides safe atomic file operations and AST-aware frontmatter editing — much safer than naive Python file writes. kepano's official Obsidian Skills give all three agents authoritative syntax knowledge without re-deriving it in each prompt.
- **Alternatives considered**:
  - Direct Python `pathlib` writes from orchestrator: rejected — bypasses MCPVault's safety guarantees and forces the orchestrator (not the agent) to know Obsidian syntax
  - Obsidian REST API: rejected — requires Obsidian running, adds heavier dependency than MCPVault

### Decision 4: Agent Definition Format
- **Decision**: Markdown files in `.claude/agents/` with YAML frontmatter (name, description, model)
- **Rationale**: This is the standard Claude Code pattern, already used in chronovista and brokered-intros projects. Each agent file defines the prompt, expected input/output format, and behavioral constraints.
- **Alternatives considered**: None — this is the canonical Claude Code approach.

### Decision 5: Session Logging Format
- **Decision**: One JSON file per session in a configurable logs directory, with per-agent output sections
- **Rationale**: JSON is parseable, inspectable, and becomes the schema blueprint for the Phase B PostgreSQL database. Per-agent sections enable independent quality evaluation. Timestamped filenames prevent collisions.
- **Alternatives considered**: (a) SQLite — adds a dependency for minimal benefit at this stage. (b) Append to single log file — harder to parse and evaluate individual sessions.

### Decision 6: Vault Search (Historian)
- **Decision**: Historian uses **MCPVault's BM25 search** against the vault (via `AgentDefinition.mcpServers`).
- **Rationale**: BM25 provides relevance ranking on title + content matches — dramatically better than naive substring matching for finding "related" pages. MCPVault is already attached to the Historian for vault access (Decision 3), so search is a free capability with no additional infrastructure.
- **Alternatives considered**:
  - Python `glob` + string matching: rejected — poor relevance, no ranking, requires writing search code that MCPVault already provides
  - pgvector: deferred to Phase B (requires PostgreSQL, semantic embeddings — premature for sub-agent prototyping)

### Decision 7: Sub-Agent Invocation Mechanism
- **Decision**: Python orchestrator uses **`claude-agent-sdk`** with **filesystem-defined agents** (`.claude/agents/*.md`) and **filesystem-defined MCP servers** (`.mcp.json`) — both auto-discovered by the SDK in non-bare mode. The `.md` files contain the complete agent config (name, description, model, tools, skills, mcpServers, plus prompt body in markdown). The orchestrator never constructs `AgentDefinition` programmatically in Phase A.
- **Rationale**:
  - Filesystem `.md` frontmatter supports the same field set as programmatic `AgentDefinition` (verified at code.claude.com/docs/en/sub-agents §Supported frontmatter fields): name, description, model, tools, disallowedTools, skills, mcpServers, memory, maxTurns, effort, permissionMode, hooks, background, isolation, color
  - Single source of truth: each agent's config lives in one place
  - Human-readable, version-controllable, easy to iterate without changing Python code
  - SDK auto-discovers `.claude/agents/` and `.mcp.json` automatically when not in `--bare` mode
  - No Python-side agent config to drift from `.md` content
- **Justification under constitution Project Standards**: `claude-agent-sdk` is a single-bounded-capability dependency. Pre-justified by constitution v1.1.0.
- **Alternatives considered**:
  - Programmatic `AgentDefinition` + `--bare` mode: more deterministic across environments (skips auto-discovery of CLAUDE.md, hooks, plugins) but adds Python-side config that duplicates the `.md` files. Right for Phase B production; overkill for Phase A prototyping.
  - `subprocess` to `claude -p --json-schema`: works but loses native per-agent message attribution
- **Phase B migration note**: When this becomes a production pipeline (likely on LangGraph), revisit this decision. `--bare` mode + programmatic `AgentDefinition` (or `--agents <json>`) is recommended by the SDK for scripted/CI calls because it eliminates environmental variance.
- **Orchestrator pattern**:
  ```python
  from claude_agent_sdk import query, ClaudeAgentOptions

  async for message in query(
      prompt=f"Process this transcript: {transcript_path}. Use the synthesis agent first to identify topics and draft pages, then the historian agent to find related vault pages, then the editor agent to write final pages to {vault_path}.",
      options=ClaudeAgentOptions(allowed_tools=["Agent", "Read", "Write", "Edit"]),
  ):
      # SDK auto-discovers .claude/agents/ and .mcp.json
      # capture per-agent attribution via Agent/Task tool_use blocks
      # and parent_tool_use_id, build SessionLog (T019)
      ...
  ```

**Agent definition layering**: Each `.md` file is the **complete** definition of one agent. YAML frontmatter specifies runtime config (`tools`, `skills`, `mcpServers`, `model`); the markdown body is the system prompt. The orchestrator does not read these files manually — the SDK loads them automatically via auto-discovery. To iterate on an agent's behavior, edit its `.md` file and restart the orchestrator.

## Phase 1: Design

### Data Model
See [data-model.md](data-model.md) for full entity definitions.

### Contracts
This is a CLI tool with no external API. The contract is the CLI interface:
```
insightmesh batch <transcript.json> --vault <path> [--logs <path>]
```

See [quickstart.md](quickstart.md) for usage.

### Agent Context
Agent definitions in `.claude/agents/` serve as the agent context for this project.

## Phase 2: Task Planning Approach

**Task Generation Strategy**:
- Setup tasks: project structure, pyproject.toml, .mcp.json, 5 strategic fixtures
- Agent creation: single MANUAL STOP task driving the user through `/agents` wizard for all three agents (pre-flight + compose prompts + present wizard inputs + STOP + verify)
- Core implementation tasks: transcript parser, wiki utilities, session logger, orchestrator, CLI
- Test tasks: one per module
- Integration task: end-to-end batch pipeline test via quickstart validation

**Ordering Strategy**:
- Tests before implementation (TDD where practical)
- Transcript parser first (input), then orchestrator + wiki/logger (output)
- Agent creation is a manual user-driven step (T009 with [MANUAL STOP] marker) — implementation halts for user to run `/agents` wizard
- Fixture creation (5 files) can be parallel

**Actual Output**: 23 numbered tasks (T001–T023) across 5 phases

**IMPORTANT**: This phase is executed by `/speckit.tasks`, NOT by `/speckit.plan`

## Post-Design Constitution Re-Check

**Anti-Slop**: 18 files target (3 agents + 5 source + 3 tests + 5 fixtures + 2 config) — within the 30-40 Goldilocks budget. Runtime deps (Pydantic v2, claude-agent-sdk) within constitution v1.1.0 §Project Standards — pre-justified, no Complexity Justification entries needed. No speculative abstractions. **PASS**  
**Incremental Delivery**: Each component independently testable; T009 (manual stop) cleanly separates wizard-driven setup from automated implementation. **PASS**  
**Transparency**: Per-agent logging (FR-009) plus Editor decision reasoning (FR-014) enables full pipeline evaluation. **PASS**  
**Post-Design Constitution Check: PASS**

## Complexity Tracking

No violations. No entries needed.
