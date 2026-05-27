# Research: Pre-flight Validation

**Phase 0 output** — resolves the open technical decisions raised in `plan.md` Technical Context. No `[NEEDS CLARIFICATION]` markers remain after this document.

> **Revision note (2026-05-24)**: R2, R3, and R7 were revised after the discovery that `echomine` (PyPI v1.3.0) already provides production-ready Claude.ai and ChatGPT adapters. Pre-revision text proposed hand-rolled adapters inside `src/exports.py`; the current text correctly delegates to `echomine` per FR-023 and the Session 2026-05-24 clarification.

---

## R1. YAML frontmatter parsing for `.claude/agents/*.md`

**Decision**: Use `PyYAML` directly with `yaml.safe_load`. Add it as an explicit direct dependency in `pyproject.toml`.

**Rationale**:
- `PyYAML` is already installed transitively via MkDocs (`uv tree` confirms `pyyaml v6.0.3` in the environment), so the install is zero-cost. Declaring it as a direct dependency is bookkeeping that makes the intent visible per constitution §Dependency Discipline.
- The agent-file frontmatter is delimited by `---` lines; splitting the file and passing the YAML block to `yaml.safe_load` is roughly 5 lines of code and handles edge cases (quoted strings, multiline values, comments) for free.
- The pre-flight check only needs the `name:` field. Even so, PyYAML is the lowest-cognitive-load way to get there.

**Alternatives considered**:
- **Regex extraction of the `name:` line**: rejected. Would handle the 95% case but break on quoted names, multiline values, or unusual whitespace. Adds bug surface for no real savings.
- **`python-frontmatter` library**: rejected. It is a wrapper around PyYAML plus a small Markdown body parser. We don't care about the body, so the wrapper is dead weight.

**Constitution alignment**: PyYAML is a force-multiplier dep (single bounded capability: YAML parsing). Pre-justified under §Project Standards spirit even though not explicitly listed.

---

## R2. Claude.ai export schema

**Decision**: Delegate to `echomine.ClaudeAdapter` (PyPI `echomine>=1.3.0,<2.0.0`). InsightMesh does not implement a Claude.ai parser. This is the FR-023 path.

**Rationale**:
- EchoMine v1.3.0 ships a tested, beta-stable Claude.ai adapter (`tests/unit/test_claude_adapter.py` plus a dedicated coverage suite).
- Schema-drift tolerance and content-shape edge cases (legacy `text` string vs current `content` blocks) are EchoMine's concern, not ours. EchoMine raises `SchemaVersionError` when it detects a structurally incompatible export, which gives us a clean exception to catch.
- Maintaining a parallel adapter inside InsightMesh would duplicate work and risk drift between the two implementations.

**Alternatives considered**:
- **Implement our own Claude.ai adapter**: rejected. Constitution §Anti-Slop forbids reinventing capabilities a force-multiplier dep already provides. Pre-revision drafts of this research had detailed Pydantic models for `ClaudeAiExport` and `ClaudeAiConversation`; those were discarded once EchoMine was confirmed as a dependency.
- **Fork EchoMine's adapter into InsightMesh**: rejected. Same problem as a hand-rolled parser, plus the maintenance burden of a fork.

---

## R3. ChatGPT export schema

**Decision**: Delegate to `echomine.OpenAIAdapter` (same dependency as R2).

**Rationale**:
- EchoMine v1.3.0 ships a tested OpenAI/ChatGPT adapter (`tests/unit/test_openai_adapter.py`).
- The non-trivial tree-walk required for ChatGPT's `mapping` + `current_node` is handled by EchoMine's `Conversation` model via `get_thread()` and `get_all_threads()` helpers. Our integration walks the canonical thread (root → `current_node`-equivalent) and converts each user/assistant message to the internal `{role, content}` shape; we do not deal with the raw tree.
- ChatGPT's branching-edit feature (multiple paths through the message tree) is correctly handled upstream — we get the user's last-active thread, not abandoned drafts.

**Alternatives considered**: same as R2.

---

## R4. CLI table rendering for `insightmesh list`

**Decision**: Use `Rich`'s `Table` class. It is already installed transitively (`rich v15.0.0`) as a Typer dependency and as an `echomine` dependency. Add it as an explicit direct dependency in `pyproject.toml`.

**Rationale**:
- Rich handles column alignment, automatic truncation (`overflow="ellipsis"`), and unicode width correctly (FR-008).
- Typer already imports Rich for `--help` rendering, so cognitive load is zero.
- Output is plain ANSI when piped to a file or non-TTY, plus styled in a terminal — both behaviors are useful and free.
- InsightMesh renders its OWN `list` table even though EchoMine also has a `list` command — our CLI surface is part of our contract (per `contracts/cli-commands.md`), independent of EchoMine's CLI presentation.

**Alternatives considered**:
- **Shell out to `echomine list`**: rejected. Loses type safety, loses control over output formatting, adds subprocess overhead. The library API path is more aligned with R7.
- **`prettytable` or `tabulate`**: rejected. Equivalent functionality with a new dep that duplicates what Rich already provides.

---

## R5. Pre-flight error aggregation and exception design

**Decision**: One custom exception class — `PreflightError` (Pydantic v2 `BaseModel`-backed via `model_validate` for the diagnostic payload, but the exception itself is a plain `Exception` subclass per Python's convention that exceptions are not Pydantic models). Categorized lists carry the diagnostic details. The CLI catches it at one boundary in `cli.py`, formats one aggregated stderr message, and exits non-zero.

**Sketch**:

```python
class PreflightDiagnostic(BaseModel):
    model_config = ConfigDict(strict=True)
    vault_errors: list[str] = Field(default_factory=list)
    missing_agents: list[str] = Field(default_factory=list)
    malformed_agents: list[MalformedAgent] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vault_errors or self.missing_agents or self.malformed_agents)

class PreflightError(Exception):
    def __init__(self, diagnostic: PreflightDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(self._format())

    def _format(self) -> str:
        ...  # one aggregated message
```

**Rationale**:
- One exception type means one catch site in `cli.py` and one rendering function. Aligns with Anti-Slop minimal-diff (the existing vault-validation code path raises `typer.Exit`; this generalizes it).
- `PreflightDiagnostic` as a Pydantic model satisfies the constitution's "all data shapes are Pydantic" rule and gives us structured testability (assert against fields, not against parsed strings).
- Aggregation logic (FR-022) lives in the diagnostic builder, not scattered across vault and agent checks. Each check appends to the diagnostic; the CLI raises once at the end if `not diagnostic.is_empty()`.

**Alternatives considered**:
- **Multiple exception types** (`VaultMissingError`, `AgentMissingError`, etc.): rejected. Would require either nested try/except or a catch-all `Exception` handler, both of which spread aggregation logic across the CLI. Violates FR-022's "run both, aggregate" intent.
- **Return-value-based error collection** (no exceptions, return `PreflightDiagnostic` and let the caller decide): rejected. Easy to forget to check at the call site; exceptions force the boundary handler in `cli.py` to deal with it.

---

## R6. `EXPECTED_AGENTS` constant location and shape

**Decision**: Add `EXPECTED_AGENTS: list[str] = ["synthesis", "historian", "editor"]` as a module-level constant in `src/orchestrator.py`, imported by `src/cli.py` for the pre-flight check. The agents declared here MUST match the agents the orchestrator's prompt actually invokes (FR-018).

**Rationale**:
- Single source of truth; one place to update when Spec 003 adds Critic and Researcher.
- Lives in `orchestrator.py` because the orchestrator module already owns the knowledge of which agents the pipeline calls. The pre-flight check is a consumer of that knowledge, not its owner.
- A plain `list[str]` is the minimum data shape. No Pydantic model needed — this is configuration-as-code, not a runtime entity. Constitution's Pydantic rule applies to "classes that group fields together"; a flat list of strings is not that.

**Alternatives considered**:
- **In a new `src/constants.py`**: rejected. Adds a file for one constant. The constant naturally belongs with the orchestrator that uses it.
- **As a Pydantic model with per-agent metadata** (name, required, ...): rejected. Speculative architecture. The pre-flight only needs the names; metadata is YAGNI.
- **In `pyproject.toml` under `[tool.insightmesh]`**: rejected. Reintroduces the config-file mechanism that the spec's non-goals explicitly close off (FR-018).

---

## R7. EchoMine integration approach

**Decision**: Use EchoMine via its public library API (`from echomine import ClaudeAdapter, OpenAIAdapter, Conversation, Message, ConversationProvider, EchomineError, ParseError, ValidationError, SchemaVersionError`). Install via standard PyPI `uv add echomine>=1.3.0,<2.0.0`. Pin a minimum version, allow non-breaking upgrades.

**Rationale**:
- Library API preserves end-to-end type safety: Pydantic objects flow through the boundary, mypy `--strict` can verify the integration.
- PyPI install is the standard path; no git URL or editable-path complications.
- EchoMine is beta-stable v1.3.0 with comprehensive tests under `tests/unit/test_claude_adapter*.py` and `test_openai_adapter.py`, including a `cognivault_integration.py` example that demonstrates the exact integration pattern we adopt: stream conversations, convert to downstream format, hand off.
- EchoMine's frozen Pydantic models compose cleanly with InsightMesh's strict-typing requirements.

**Integration shape**:

`src/exports.py` exposes two thin helpers; everything heavy (parsing, tree-walking, schema validation) lives upstream in EchoMine:

```python
# Conceptual sketch (final names settled in /speckit-tasks)
def list_conversations(path: Path) -> list[InsightMeshSummary]:
    """List conversations across both supported providers.

    Tries adapters in turn (Claude first, then OpenAI). Projects each
    Conversation to a small (id, title, created_at, message_count) tuple
    sorted most-recent-first for the `insightmesh list` CLI.
    """
    ...

def extract_conversation(path: Path, selector: str) -> ChatTranscript:
    """Resolve `--conversation` (id-or-index per FR-010) and convert one
    EchoMine Conversation to a Spec 001 ChatTranscript.

    Walks the canonical thread (root → current_node) for ChatGPT;
    trivial for Claude.ai. Calls .get_thread() on EchoMine's model;
    we do not implement tree-walking ourselves.
    """
    ...
```

Adapter selection is automatic: try `ClaudeAdapter` first, fall back to `OpenAIAdapter`, raise our `UnrecognizedExportFormat` error (mapped to the CLI's FR-007 message) if neither claims the file. Detection delegated to EchoMine's adapters; we just catch their `SchemaVersionError` / `ParseError` and translate.

Both helpers delegate exception handling to a small private translator at the boundary (sketch: `_translate_echomine_error(exc, path) -> Exception`) that implements the FR-027 translation table and ensures cause chaining via `raise ... from echomine_exc`.

**Alternatives considered**:
- **CLI shell-out** (`subprocess.run(["echomine", "list", ...])`): rejected. Loses type safety, serializes/deserializes objects crossing the process boundary, adds subprocess overhead per call, complicates streaming semantics. Library API is the correct integration for two Python projects that both ship Pydantic-typed APIs.
- **Local editable install** (`uv add ../echomine`): rejected. Breaks for anyone cloning InsightMesh without also having `../echomine` on disk.
- **Git URL pin** (`uv add git+https://...`): rejected. EchoMine is on PyPI; the additional indirection isn't needed.

**Constitution alignment**: EchoMine is a force-multiplier dep — single bounded capability (AI chat-export parsing and normalization), replaces ~300 LOC of hand-rolled adapters, single-purpose library with a clean public API and its own tests. Pre-justified per §Project Standards spirit. No Complexity Justification Table entry required.

---

## Summary

| Item | Decision | Net effect on dependencies |
|------|----------|---------------------------|
| R1 — YAML parsing | PyYAML (already transitive; promote to direct dep) | +1 direct dep declaration |
| R2 — Claude.ai schema | Delegate to `echomine.ClaudeAdapter` | covered by R7 |
| R3 — ChatGPT schema | Delegate to `echomine.OpenAIAdapter` | covered by R7 |
| R4 — CLI table | Rich (already transitive via Typer and EchoMine; promote to direct dep) | +1 direct dep declaration |
| R5 — Pre-flight error | One `PreflightError` exception + `PreflightDiagnostic` Pydantic model | none |
| R6 — `EXPECTED_AGENTS` | Module-level `list[str]` in `orchestrator.py` | none |
| R7 — EchoMine integration | Library API via `from echomine import ...`; PyPI install | +1 direct dep (`echomine>=1.3.0,<2.0.0`) |

**Pyproject changes anticipated** (Phase 1 of implementation):
- Add `echomine>=1.3.0,<2.0.0` to runtime dependencies (force-multiplier; single bounded capability — chat export parsing and normalization)
- Add `pyyaml>=6.0` to runtime dependencies (force-multiplier; single bounded capability — YAML parsing)
- Add `rich>=15.0` to runtime dependencies (force-multiplier; single bounded capability — terminal table rendering)

All three are pre-justified by the constitution's force-multiplier criterion. No Complexity Justification Table entries required.
