# Data Model: Pre-flight Validation

**Phase 1 output** — concrete Pydantic v2 shapes for the entities named in `spec.md` §Key Entities, plus the internal types the implementation needs.

All InsightMesh-defined models inherit from `pydantic.BaseModel` with `ConfigDict(strict=True)` per constitution §Project Standards. External types from `echomine` are listed by name and link to upstream.

> **Revision note (2026-05-24)**: The original draft of this document defined `ClaudeAiExport`, `ChatGptExport`, `ExportFile` (discriminated union), `ConversationSummary`, and `Conversation` as InsightMesh-owned Pydantic models. That entire surface has been removed: those types now come from the `echomine` library (PyPI `echomine>=1.3.0,<2.0.0`) per FR-023, R2, R3, and R7. This document defines only what InsightMesh still owns (agent inspection, pre-flight diagnostics) plus the small projection types we use at our CLI boundary.

---

## External types from `echomine`

These come in via `from echomine import ...` and are used as-is. We do not subclass, wrap, or re-export them.

| Import | Role in InsightMesh |
|--------|---------------------|
| `echomine.Conversation` | The full conversation read by an adapter. Frozen Pydantic v2 model with id, title, timestamps, tree-structured messages, and tree-navigation helpers (`get_root_messages()`, `get_thread()`, `get_all_threads()`, etc.). We project from this for our `list` output and walk its canonical thread for `batch`. |
| `echomine.Message` | One message inside a `Conversation`. Has `id`, `role`, `content`, `timestamp`, `parent_id`. We read `role` and `content` when flattening to the internal `{role, content}` shape Spec 001's `transcript.py` consumes. |
| `echomine.ClaudeAdapter` | Adapter for Claude.ai exports. Yields `Conversation` instances via `stream_conversations(path, ...)`. |
| `echomine.OpenAIAdapter` | Adapter for ChatGPT/OpenAI exports. Same interface as `ClaudeAdapter`. |
| `echomine.ConversationProvider` | Structural typing protocol that both adapters satisfy. Useful when we want to write code that handles "any echomine adapter." |
| `echomine.EchomineError` | Base of EchoMine's exception hierarchy. We catch this at the boundary in `src/exports.py` and translate to our own user-facing errors. |
| `echomine.ParseError`, `echomine.ValidationError`, `echomine.SchemaVersionError` | More specific exceptions. Translation per spec FR-027 (Error translation contract): `SchemaVersionError` on the first conversation → `UnrecognizedExportFormat` (matches FR-007 message); `ParseError` → `error: cannot parse export file <path>: <upstream message verbatim>`; `ValidationError` → `error: invalid conversation data in <path>: <upstream message verbatim>`. Original cause is chained via `raise ... from echomine_exc`. |

No InsightMesh code imports or relies on `echomine`'s internal module paths (`echomine.adapters.claude`, `echomine.models.conversation`, etc.). Only the public top-level imports listed above.

---

## InsightMesh-owned types

### `InsightMeshSummary` — projection used for `insightmesh list` output

A small projection over EchoMine's `Conversation` capturing only the four fields we render. Keeps the list rendering decoupled from EchoMine's full model and gives us a stable contract for `contracts/cli-commands.md` regardless of upstream evolution.

```python
class InsightMeshSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    id: str                           # = echomine.Conversation.id
    title: str                        # = echomine.Conversation.title (may be empty)
    created: datetime                 # = echomine.Conversation.created_at, tz-aware UTC
    message_count: int = Field(ge=0)  # = echomine.Conversation.message_count
```

**Validation rules**:
- `id` MUST be non-empty (EchoMine guarantees this; we re-assert at our boundary)
- `title` MAY be empty (untitled conversations are allowed by both providers)
- `created` MUST be tz-aware; conversion from EchoMine's `datetime` (already tz-aware per its docs) is a passthrough

**Where used**: produced by `src/exports.py:list_conversations()`; consumed by the `insightmesh list` Rich table renderer and by `--conversation <index>` resolution (the integer index points into the most-recent-first ordered list).

**Why a projection instead of using `echomine.ConversationSummary` directly**: EchoMine's `ConversationSummary` (used in `echomine.statistics`) carries only `id`, `title`, `message_count` — no `created_at`. We need creation timestamp for the most-recent-first ordering required by FR-005, so we project from the full `Conversation` instead.

---

### `AgentDefinition` — what the pre-flight check reads from each `.claude/agents/*.md` file

```python
class AgentDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    name: str                         # the `name:` field from YAML frontmatter
    # other frontmatter fields exist (description, model, tools, skills, mcpServers)
    # but pre-flight only requires `name:` so we accept-and-ignore the rest
```

**Validation rules**:
- `name` MUST be non-empty and MUST equal one of the strings in `EXPECTED_AGENTS` to be considered "present"
- Filename-to-name correspondence is NOT enforced (per Edge Cases: pre-flight resolves by frontmatter `name:`, not by filename)

**Where used**: produced by a small helper in `src/cli.py` (or a dedicated `src/preflight.py` if file count permits; final placement decided in `/speckit-tasks`); consumed by the pre-flight check to decide whether each entry in `EXPECTED_AGENTS` has a corresponding file with matching `name:`.

---

### `PreflightDiagnostic` — aggregated pre-flight findings

Carries all problems detected by the pre-flight pass; raised inside `PreflightError`.

```python
class PreflightDiagnostic(BaseModel):
    model_config = ConfigDict(strict=True)

    vault_errors: list[str] = Field(default_factory=list)
    missing_agents: list[str] = Field(default_factory=list)        # names from EXPECTED_AGENTS not found
    malformed_agents: list[MalformedAgent] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vault_errors or self.missing_agents or self.malformed_agents)


class MalformedAgent(BaseModel):
    model_config = ConfigDict(strict=True)

    file_path: str          # absolute or repo-relative path to the offending .claude/agents/*.md
    reason: str             # human-readable explanation (e.g., "missing `name:` field", "YAML parse error: ...")
```

**Validation rules**:
- All lists default to empty (a passing pre-flight has `is_empty() is True`)
- `malformed_agents[*].reason` is a plain string, suitable for direct stderr display

**Where used**: built up by `src/cli.py` pre-flight pass; rendered into the single aggregated stderr message when `not is_empty()`; carried inside `PreflightError`.

---

### `PreflightError` — the exception type the pre-flight raises

Plain `Exception` subclass (not a `BaseModel`; per Python convention). Carries the diagnostic payload.

```python
class PreflightError(Exception):
    def __init__(self, diagnostic: PreflightDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(self._format())

    def _format(self) -> str:
        # One aggregated message with sections per non-empty list
        ...
```

**Where used**: raised by `cli.py:_run_preflight()` when `not diagnostic.is_empty()`; caught at the top of the `batch` command and translated to `typer.Exit(code=1)` after writing the message to stderr.

---

### `UnrecognizedExportFormat` — boundary error for non-export inputs

When `list_conversations()` or `extract_conversation()` is called with a file that neither EchoMine adapter accepts, we map EchoMine's `SchemaVersionError` (or the lack of a successful adapter match) to our own boundary error.

```python
class UnrecognizedExportFormat(Exception):
    """Neither echomine.ClaudeAdapter nor echomine.OpenAIAdapter recognized the file."""
    def __init__(self, path: Path, attempted: list[str]) -> None:
        self.path = path
        self.attempted = attempted
        super().__init__(
            f"not a recognized export format: {path} (tried {', '.join(attempted)}); expected a multi-conversation export from Claude.ai or ChatGPT"
        )
```

**Where used**: raised by `src/exports.py:list_conversations()` and `extract_conversation()`; caught by `src/cli.py` and translated to the FR-007 stderr message and exit code 1.

---

## Module-level configuration

### `EXPECTED_AGENTS` — single source of truth for which agents the pipeline depends on

```python
# in src/orchestrator.py
EXPECTED_AGENTS: list[str] = ["synthesis", "historian", "editor"]
```

**Update discipline**: when Spec 003 (or later) adds agents, this constant is updated *in this one place* and both the orchestrator's invocation code and the pre-flight check pick up the change (FR-018, R6).

---

## Relationships

```text
Export file (.json on disk)
    │
    │  read by  ─────────▶  echomine.ClaudeAdapter | echomine.OpenAIAdapter
    │                          │
    │                          │ .stream_conversations(path) yields...
    │                          ▼
    │                       echomine.Conversation   (frozen, tree of echomine.Message)
    │                          │
    │      ┌───────────────────┴───────────────────┐
    │      │                                       │
    │      │ project to                            │ flatten via .get_thread()
    │      ▼                                       ▼
    │   InsightMeshSummary                    list[(role, content)] tuples
    │   (id, title, created,                       │
    │    message_count)                            │ wrap into
    │      │                                       ▼
    │      │ render with Rich                  ChatTranscript / Exchange
    │      ▼                                   (from src/transcript.py)
    │   stdout (`insightmesh list`)                │
    │                                              ▼
    │                                          Orchestrator pipeline (Spec 001)
    │
    │
EXPECTED_AGENTS (src/orchestrator.py)
    │
    │ consumed by  ─────────▶  PreflightDiagnostic.missing_agents / malformed_agents
    │                              │
    │                              ▼
    │                          PreflightError  ──▶  cli.py boundary  ──▶  stderr + exit 1
```

## State transitions

None of the InsightMesh-defined entities have lifecycle state. `InsightMeshSummary`, `AgentDefinition`, `PreflightDiagnostic`, and `MalformedAgent` are immutable value objects (Pydantic models with no mutating methods). The pre-flight pass produces a `PreflightDiagnostic` once per `batch` invocation and discards it after rendering — no persistence (Clarification Q1, FR-019). EchoMine's `Conversation` and `Message` are explicitly `frozen=True` on EchoMine's side, so we do not need to enforce immutability ourselves.

## Out of scope

- Defining InsightMesh-owned Pydantic models for the Claude.ai or ChatGPT export schemas. EchoMine owns these (FR-023, R2, R3).
- Persisting `PreflightDiagnostic` to `.logs/` (closed off by Clarification Q1, FR-019).
- Storing `EXPECTED_AGENTS` in a config file (closed off by FR-018 and Non-Goals).
- Generic `ExportProvider` abstraction registered inside InsightMesh. EchoMine's `ConversationProvider` protocol is the relevant abstraction; we depend on it but do not extend it. If we need a third provider, the right move is contributing it upstream to EchoMine, not building a parallel registry here.
- Wrapping or subclassing `echomine.Conversation` or `echomine.Message`. We use them directly; projection happens once at the CLI boundary (`InsightMeshSummary`) or at the transcript boundary (flat `{role, content}`).
