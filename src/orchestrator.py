"""Orchestrator for the InsightMesh batch synthesis pipeline.

Runs the three sub-agents (synthesis → historian → editor) on a parsed
transcript using claude-agent-sdk. Agents are auto-discovered from
.claude/agents/*.md and MCPVault from .mcp.json by setting
`setting_sources=["project"]` on `ClaudeAgentOptions`.

Per-agent invocations are captured by matching `Agent`/`Task` `ToolUseBlock`s
to their corresponding `ToolResultBlock`s in the message stream.

Spec 004 layered checkpointed processing on top: when called with
`checkpoint_path`, `run_batch` slices the transcript into multiple checkpoints
(token-budget-driven), persists a per-conversation cursor after each one, and
supports `--resume` / `--max-exchanges` / `--force-resume` / `--retry`. The
per-checkpoint pipeline execution lives in `_execute_pipeline`; the checkpoint
loop lives in `run_batch`.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from pydantic import BaseModel, ConfigDict

from src.checkpoint import (
    Checkpoint,
    CheckpointHashMismatch,
    CheckpointIndexOutOfBounds,
    CheckpointMissing,
    DigestEntry,
    compute_transcript_hash,
    load_checkpoint,
    save_checkpoint,
)
from src.logger import (
    AgentOutput,
    CrossLinkRecord,
    EditorOutput,
    HistorianOutput,
    SessionError,
    SessionLog,
    SynthesisOutput,
    write_session_log,
)
from src.transcript import ChatTranscript, Exchange

ParsedAgentOutput = SynthesisOutput | HistorianOutput | EditorOutput

_WIKI_LINK_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")

# Spec 002 FR-018: single source of truth for the agents the pipeline depends on.
EXPECTED_AGENTS: list[str] = ["synthesis", "historian", "editor"]

# Spec 004 FR-015: per-checkpoint Synthesis input target ~50% of Sonnet's 200K
# context window. Char-based budget (≈ tokens * 3.5) is the heuristic used by
# `pick_checkpoint_slice`. See research.md Decision 2.
DEFAULT_TOKEN_BUDGET: int = 100_000
"""Default token budget per checkpoint when --token-budget is not configured."""

_CHARS_PER_TOKEN: float = 3.5
"""Char-per-token heuristic (English text). See research.md Decision 2."""


class _AgentCall(BaseModel):
    """In-memory record of one sub-agent invocation during a batch."""

    model_config = ConfigDict(strict=True)

    tool_use_id: str
    subagent_type: str
    input_prompt: str
    raw_output: str | None = None
    parsed_output: ParsedAgentOutput | None = None
    error: str | None = None
    start_monotonic: float | None = None
    end_monotonic: float | None = None

    @property
    def duration_seconds(self) -> float:
        if self.start_monotonic is None or self.end_monotonic is None:
            return 0.0
        return self.end_monotonic - self.start_monotonic


def _utc_now_iso() -> str:
    """ISO 8601 UTC datetime with seconds precision, Z-form."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _estimate_exchange_chars(ex: Exchange) -> int:
    """Rough char-count estimate for one exchange's rendered size.

    Counts user + assistant content plus ~30 chars JSON envelope overhead.
    Used by `pick_checkpoint_slice` to size checkpoint slices against the
    token budget via the char/`_CHARS_PER_TOKEN` heuristic (research Decision 2).
    """
    n = len(ex.user_message.content)
    if ex.assistant_message is not None:
        n += len(ex.assistant_message.content)
    return n + 30


def pick_checkpoint_slice(
    exchanges: list[Exchange],
    start_index: int,
    token_budget: int,
) -> list[Exchange]:
    """Greedy walk forward from `start_index`, packing exchanges until adding
    the next would exceed `token_budget` (estimated via char/`_CHARS_PER_TOKEN`).

    Always returns at least one exchange when `start_index` is in range, even
    if that exchange alone exceeds the budget — better to oversize one
    checkpoint than to deadlock the loop. Returns an empty list when
    `start_index >= len(exchanges)` (terminal condition for the loop).

    Verifies FR-015 at the unit level (T021): for any (exchanges, budget), the
    returned slice's rendered char-count is at most `token_budget * _CHARS_PER_TOKEN`
    UNLESS the slice contains exactly one too-large exchange (the "at least one"
    guarantee).
    """
    if start_index < 0 or start_index >= len(exchanges):
        return []

    budget_chars = int(token_budget * _CHARS_PER_TOKEN)

    selected: list[Exchange] = []
    char_count = 0
    for i in range(start_index, len(exchanges)):
        ex = exchanges[i]
        ex_chars = _estimate_exchange_chars(ex)
        if selected and (char_count + ex_chars > budget_chars):
            break
        selected.append(ex)
        char_count += ex_chars

    return selected


def _build_orchestrator_prompt(
    transcript: ChatTranscript,
    vault_path: Path,
    topics_covered_digest: list[DigestEntry] | None = None,
    checkpoint_number: int = 1,
) -> str:
    """Build the prompt that drives the main Claude to run the 3-agent pipeline.

    When `topics_covered_digest` is non-empty (Spec 004, second-or-later
    checkpoints), appends an instruction telling the orchestrator agent to
    prepend a "Topics already covered" preamble to the Synthesis prompt so
    Synthesis extends rather than duplicates prior-checkpoint pages.
    """
    transcript_json = transcript.model_dump_json()
    batch_timestamp = _utc_now_iso()
    base = f"""You are the InsightMesh batch synthesis orchestrator. Run the chat \
transcript below through three sub-agents in strict sequence and return the final \
editor output. Do not synthesize content yourself; your only job is orchestration.

Vault path: {vault_path}
Source transcript: {transcript.source_path}
Batch timestamp (UTC): {batch_timestamp}

## Pipeline (run in order, do not skip or parallelize)

**Step 1 — Synthesis**
Invoke the `synthesis` sub-agent (via the Agent tool with `subagent_type="synthesis"`). \
Pass it this exact JSON transcript:

```json
{transcript_json}
```

It returns a JSON object with a `drafts` array.

**Step 2 — Historian**
Invoke the `historian` sub-agent. Pass it the COMPLETE Synthesis output JSON from \
Step 1 (do not summarize, do not omit fields). The Historian has MCPVault attached \
for vault search. It returns a JSON object with an `augmented_drafts` array AND a \
`topics_covered_increment` array (one entry per draft).

**Step 3 — Editor**
Invoke the `editor` sub-agent. Pass it the COMPLETE Historian output JSON from \
Step 2. The Editor has MCPVault attached and writes pages to the vault at \
`{vault_path}`. **In the same prompt to the Editor, also include these three \
required parameters that the Editor MUST use when generating frontmatter:**

- `source_path`: `{transcript.source_path}` (use exactly this string for the \
  frontmatter `source:` field of every page — do not leave it blank)
- `vault_path`: `{vault_path}` (the absolute root where pages should be written, \
  under the `InsightMesh/` subdirectory)
- `batch_timestamp`: `{batch_timestamp}` (ISO 8601 UTC datetime — use for the \
  frontmatter `created:` field on new pages, and the `updated:` field on every \
  page including updates; for updates, preserve the existing `created:` value)

The Editor returns a JSON object with `results` and `decisions` arrays — this is \
the final pipeline output.

## Failure Handling (FR-013)

Sub-agents can fail (return an error tool_result, hit a rate limit, or return \
output that doesn't parse). Apply these rules:

- **Synthesis fails (non-recoverable)**: ABORT the pipeline. Do NOT invoke \
  Historian or Editor. Respond with a one-line failure message naming the \
  failure (e.g., "Synthesis failed: <reason>"). The Python orchestrator will \
  surface this to the user.

- **Historian fails (recoverable)**: Skip the Historian step. Transform the \
  Synthesis output into the shape Editor expects by wrapping each draft as an \
  augmented draft with empty `related_pages` and empty `crosslink_recommendations` \
  arrays. Pass this transformed structure to the Editor. Note in your final \
  one-line response that Historian was skipped.

- **Editor fails entirely (non-recoverable)**: ABORT. Respond with a one-line \
  failure message.

- **Editor fails on a SPECIFIC page (recoverable)**: The Editor itself handles \
  per-page failures by marking that page with `action: "skipped"` in its \
  `decisions` array (the page is omitted from `results`). You should NOT \
  re-invoke the Editor; trust its partial output.

- **Rate limit responses (HTTP 429 or SDK rate_limit error)**:
  - On Synthesis: treat as Synthesis failure → ABORT.
  - On Historian or Editor: treat per the recoverable rules above.

## After Step 3 (or after abort)

Respond with a brief one-line summary. Examples:
- Success: "Pipeline complete: N pages processed."
- Historian skipped: "Pipeline complete: N pages (Historian skipped, no cross-links)."
- Synthesis failed: "Synthesis failed: <one-line reason>."
- Editor failed: "Editor failed: <one-line reason>."

Do NOT restate the editor's full JSON output."""

    if topics_covered_digest:
        items = "\n".join(f'- "{e.page_title}": {e.gist}' for e in topics_covered_digest)
        digest_block = f"""

## Checkpoint context (checkpoint #{checkpoint_number} of this conversation)

This is NOT the first checkpoint. Wiki pages have already been created in prior \
checkpoints of this same conversation. When you invoke Synthesis in Step 1, \
PREPEND the following "Topics already covered" preamble to the JSON transcript \
in the prompt you pass to the Agent tool (so Synthesis sees the preamble first, \
then the transcript):

```
## Topics already covered (from prior checkpoints of this conversation)
{items}

(Extend or cross-reference these topics; do not produce duplicate drafts. \
Do NOT inline this block into draft content.)
```

The Historian and Editor steps are unchanged; only Synthesis sees the preamble."""
        return base + digest_block
    return base


def _extract_text_content(content: str | list[dict[str, Any]] | None) -> str:
    """Coerce a ToolResultBlock.content payload to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if "text" in item:
            parts.append(str(item["text"]))
        else:
            parts.append(str(item))
    return "\n".join(parts)


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.+?)\n?```", re.DOTALL)

_PERSISTED_PATH_RE = re.compile(r"Full output saved to:\s*(\S+)")


def _unwrap_persisted_output(raw: str) -> str:
    """Unwrap Claude Code SDK's `<persisted-output>` envelope when present.

    When a tool result exceeds ~50KB, the SDK wraps it in
    `[{"type": "text", "text": "<full content>"}]` and writes the full output
    to a sidecar JSON file under `~/.claude/projects/<session>/tool-results/`.
    The orchestrator receives a short preview with the file path.

    Without unwrapping, the preview's leading `[` defeats `_try_extract_json`:
    it decodes the envelope-array as JSON and Pydantic validation fails because
    the shape is `[{type, text}]`, not the expected `SynthesisOutput` /
    `HistorianOutput` / `EditorOutput`.

    Returns the concatenated `text` field(s) from the sidecar when the envelope
    is detected and readable, or `raw` unchanged otherwise. Failures (missing
    file, unparseable sidecar, unexpected shape) fall back to `raw` so the
    downstream extraction can still attempt the preview.
    """
    if "<persisted-output>" not in raw:
        return raw
    match = _PERSISTED_PATH_RE.search(raw)
    if not match:
        return raw
    persist_path = Path(match.group(1))
    try:
        wrapped = json.loads(persist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return raw
    if not isinstance(wrapped, list):
        return raw
    parts = [str(item.get("text", "")) for item in wrapped if isinstance(item, dict)]
    return "\n".join(parts) if parts else raw


def _try_extract_json(raw: str) -> str:
    """Pull a JSON object out of mixed agent output.

    Agents return clean JSON most of the time, but real-world conditions
    break naive extraction:

    - The SDK can append trailing metadata after the JSON (an `agentId:`
      resumption line and a `<usage>` block), so the response no longer ends
      with `}`.
    - The agent's own `draft_content` can contain fenced code blocks or braces
      (e.g., a markdown drum-tab pattern), which fools fence/brace heuristics.
    - The SDK wraps tool results > ~50KB in a `<persisted-output>` envelope
      (a JSON array of `{type, text}` blocks) and writes the full output to a
      sidecar file. `_unwrap_persisted_output` handles this; see its docstring.

    The robust strategy after unwrapping: locate the first `{` and let a real
    JSON parser (`json.JSONDecoder().raw_decode`) consume exactly one JSON
    value, ignoring everything after it and tolerating braces/fences inside
    string values. Fence and greedy-brace heuristics are kept only as fallbacks.

    Returns the best-guess JSON string; downstream parsing decides if it's
    actually valid.
    """
    raw = _unwrap_persisted_output(raw)
    stripped = raw.strip()
    start = stripped.find("{")
    if start != -1:
        try:
            _obj, end = json.JSONDecoder().raw_decode(stripped[start:])
            return stripped[start : start + end]
        except json.JSONDecodeError:
            pass
    fence_match = _FENCED_JSON_RE.search(raw)
    if fence_match and fence_match.group(1).strip().startswith("{"):
        return fence_match.group(1).strip()
    last_brace = raw.rfind("}")
    if start != -1 and last_brace > start:
        return raw[start : last_brace + 1]
    return stripped


def _save_failed_response(subagent_type: str, raw: str) -> Path:
    """Layer 3: dump the raw agent response so it can be diagnosed."""
    scratch_dir = Path(".specify/scratch/agent_responses")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = scratch_dir / f"{stamp}-{subagent_type}-failed.txt"
    path.write_text(raw)
    return path


def _parse_agent_output(subagent_type: str, raw: str) -> ParsedAgentOutput | None:
    """Parse an agent's raw output into the appropriate typed model.

    Returns None for unknown subagent types. Raises ValueError with a preview
    of the raw response (and a path to the full saved response) on parse
    failure so the orchestrator caller can surface useful diagnostics.
    """
    if subagent_type not in {"synthesis", "historian", "editor"}:
        return None

    candidate = _try_extract_json(raw)
    try:
        if subagent_type == "synthesis":
            return SynthesisOutput.model_validate_json(candidate)
        if subagent_type == "historian":
            return HistorianOutput.model_validate_json(candidate)
        return EditorOutput.model_validate_json(candidate)
    except Exception as exc:
        saved = _save_failed_response(subagent_type, raw)
        preview = raw.strip()[:1500]
        raise ValueError(
            f"{subagent_type} agent did not return parseable JSON. "
            f"Full response saved to: {saved}\n"
            f"Preview (first 1500 chars):\n{preview}"
        ) from exc


async def _execute_pipeline(
    transcript: ChatTranscript,
    vault_path: Path,
    topics_covered_digest: list[DigestEntry] | None,
    checkpoint_number: int,
) -> tuple[dict[str, _AgentCall], float, datetime]:
    """Run the 3-agent pipeline against `transcript` (possibly a slice).

    Returns the per-call records dict plus timing/wall-clock metadata. The
    caller (`run_batch`) decides how to interpret the calls — write a session
    log, extract EditorOutput, accumulate the topics-covered increment, etc.

    This is the seam tests mock to inject canned agent outputs without
    invoking the real Claude SDK.
    """
    prompt = _build_orchestrator_prompt(
        transcript,
        vault_path,
        topics_covered_digest=topics_covered_digest,
        checkpoint_number=checkpoint_number,
    )
    agent_calls: dict[str, _AgentCall] = {}
    batch_started_wall = datetime.now(UTC)
    batch_started_monotonic = time.monotonic()

    options = ClaudeAgentOptions(
        allowed_tools=["Agent", "Read", "Write", "Edit"],
        setting_sources=["project"],
        cwd=str(Path.cwd()),
        max_turns=10,
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name in ("Agent", "Task"):
                    subagent_type = str(block.input.get("subagent_type", ""))
                    input_prompt = str(block.input.get("prompt", ""))
                    agent_calls[block.id] = _AgentCall(
                        tool_use_id=block.id,
                        subagent_type=subagent_type,
                        input_prompt=input_prompt,
                        start_monotonic=time.monotonic(),
                    )
        elif isinstance(message, UserMessage):
            content = message.content
            if isinstance(content, str):
                continue
            for block in content:
                if not isinstance(block, ToolResultBlock):
                    continue
                call = agent_calls.get(block.tool_use_id)
                if call is None:
                    continue
                call.end_monotonic = time.monotonic()
                raw = _extract_text_content(block.content)
                call.raw_output = raw
                if block.is_error:
                    call.error = raw
                    continue
                try:
                    call.parsed_output = _parse_agent_output(call.subagent_type, raw)
                except Exception as exc:
                    call.error = f"Failed to parse {call.subagent_type} output: {exc}"

    batch_duration = time.monotonic() - batch_started_monotonic
    return agent_calls, batch_duration, batch_started_wall


def _cursor_path_for(logs_dir: Path, transcript_source: Path, conversation_id: str | None) -> Path:
    """Derive the cursor file path per Spec 004 FR-005.

    For single-conversation source files: `logs/{stem}.checkpoint.json`.
    For multi-conversation exports with a conversation identifier:
    `logs/{stem}__{conversation_id}.checkpoint.json` (with filesystem-unsafe
    characters in conversation_id sanitized to hyphens).
    """
    stem = transcript_source.stem
    if conversation_id is None:
        return logs_dir / f"{stem}.checkpoint.json"
    safe_id = conversation_id.replace("/", "-").replace(":", "-")
    return logs_dir / f"{stem}__{safe_id}.checkpoint.json"


def _save_failed_cursor(
    path: Path,
    transcript: ChatTranscript,
    transcript_hash: str,
    conversation_id: str | None,
    prior_cursor: Checkpoint | None,
    last_error: str,
) -> None:
    """Persist a failed cursor after an agent or pipeline error."""
    last_index = prior_cursor.last_processed_exchange_index if prior_cursor is not None else 0
    checkpoint_number = prior_cursor.checkpoint_number if prior_cursor is not None else 1
    topics_covered_digest = (
        list(prior_cursor.topics_covered_digest) if prior_cursor is not None else []
    )
    failed = Checkpoint(
        export_path=Path(transcript.source_path),
        conversation_id=conversation_id,
        transcript_hash=transcript_hash,
        last_processed_exchange_index=last_index,
        checkpoint_number=checkpoint_number,
        status="failed",
        last_error=last_error,
        topics_covered_digest=topics_covered_digest,
        updated_at=datetime.now(UTC),
    )
    save_checkpoint(path, failed)


def _handle_cursor_entry(
    cursor: Checkpoint | None,
    cursor_path: Path,
    current_hash: str,
    transcript_length: int,
    require_resume: bool,
    force_resume: bool,
    retry: bool,
) -> Literal["proceed_fresh", "proceed_resume", "no_op_complete"]:
    """Validate the cursor against the current transcript and the CLI flags.

    Raises typed CheckpointError subclasses for the spec's edge cases; returns
    a directive the caller (`run_batch`) uses to decide what to do next.
    Side effect: prints a friendly note to stderr when a prior failure is
    being acknowledged via `--retry`.
    """
    if cursor is None:
        if require_resume:
            raise CheckpointMissing(
                f"no cursor found at {cursor_path}; run without --resume to start fresh"
            )
        return "proceed_fresh"

    # FR-006: hash mismatch refuses unless --force-resume.
    if cursor.transcript_hash != current_hash and not force_resume:
        raise CheckpointHashMismatch(
            f"transcript hash has changed since the cursor at {cursor_path} was written.\n"
            f"  Cursor hash:  {cursor.transcript_hash}\n"
            f"  Current hash: {current_hash}\n"
            f"Re-run without --resume to discard the cursor, or pass --force-resume "
            f"to continue against the new transcript "
            f"(indices may now point at different exchanges)."
        )

    # Edge Case: cursor index exceeds the current transcript length (truncation
    # that coincidentally preserved the hash, or schema drift).
    if cursor.last_processed_exchange_index >= transcript_length:
        raise CheckpointIndexOutOfBounds(
            f"cursor at {cursor_path} points at exchange index "
            f"{cursor.last_processed_exchange_index}, but the current transcript has only "
            f"{transcript_length} exchanges. Delete the cursor to start fresh."
        )

    # FR-007: complete is terminal.
    if cursor.status == "complete":
        return "no_op_complete"

    # FR-014: failed cursor requires --retry to proceed.
    if cursor.status == "failed":
        if not retry:
            print(
                f"\nPrior run failed (cursor at {cursor_path}).\n"
                f"  last_error: {cursor.last_error}\n"
                f"Pass --retry to acknowledge and resume from cursor position "
                f"(index {cursor.last_processed_exchange_index}).",
                file=sys.stderr,
            )
            raise CheckpointError_RequiresRetry(cursor_path)
        print(
            f"\n[--retry] Resuming past prior failure (last_error: {cursor.last_error})\n",
            file=sys.stderr,
        )

    return "proceed_resume"


class CheckpointError_RequiresRetry(Exception):
    """Signal that the cursor's status is 'failed' and the user did not pass --retry.

    The CLI catches this and exits with code 1 (per FR-014) without producing
    a stack trace; the actual diagnostic was already printed to stderr.
    """

    def __init__(self, cursor_path: Path) -> None:
        self.cursor_path = cursor_path
        super().__init__(f"cursor at {cursor_path} requires --retry")


async def run_batch(
    transcript: ChatTranscript,
    vault_path: Path,
    logs_dir: Path | None = None,
    *,
    checkpoint_path: Path | None = None,
    conversation_id: str | None = None,
    max_exchanges: int | None = None,
    require_resume: bool = False,
    force_resume: bool = False,
    retry: bool = False,
    token_budget: int | None = None,
) -> EditorOutput | None:
    """Run the Synthesis → Historian → Editor pipeline on a transcript.

    Spec 001-003 behavior (no checkpointing) is preserved when
    `checkpoint_path` is None: the full transcript flows through the pipeline
    in one invocation and the resulting EditorOutput is returned.

    Spec 004 behavior (checkpointed) is active when `checkpoint_path` is set:
    the transcript is sliced into multiple checkpoints by token budget, a
    per-conversation cursor is loaded/written between checkpoints, and the
    accumulated topics-covered digest is carried forward to second-or-later
    Synthesis invocations. Returns `None` when the cursor reports the
    conversation is already complete (no-op).

    Sub-agents are auto-discovered from `.claude/agents/*.md`. MCPVault is
    auto-discovered from `.mcp.json` (requires VAULT_PATH env var set before
    calling — the CLI does this from the `--vault` flag).

    Raises:
        RuntimeError: when an agent fails non-recoverably (and, in checkpointed
            mode, after persisting a `failed` cursor).
        CheckpointMissing, CheckpointHashMismatch, CheckpointIndexOutOfBounds:
            per-spec resume-edge-case errors when checkpoint_path is set.
        CheckpointError_RequiresRetry: when a `failed` cursor is loaded without
            --retry; the CLI handles this and exits 1.
    """
    budget = token_budget if token_budget is not None else DEFAULT_TOKEN_BUDGET

    # Spec 001-003 path: no checkpointing, single-shot.
    if checkpoint_path is None:
        agent_calls, batch_duration, batch_started_wall = await _execute_pipeline(
            transcript=transcript,
            vault_path=vault_path,
            topics_covered_digest=None,
            checkpoint_number=1,
        )
        if logs_dir is not None:
            session_log = _build_session_log(
                transcript=transcript,
                agent_calls=agent_calls,
                batch_started_wall=batch_started_wall,
                batch_duration=batch_duration,
            )
            write_session_log(session_log, logs_dir)
        return _finalize_result(agent_calls)

    # Spec 004 path: checkpointed processing.
    cursor = load_checkpoint(checkpoint_path)
    current_hash = compute_transcript_hash(transcript)
    directive = _handle_cursor_entry(
        cursor=cursor,
        cursor_path=checkpoint_path,
        current_hash=current_hash,
        transcript_length=len(transcript.exchanges),
        require_resume=require_resume,
        force_resume=force_resume,
        retry=retry,
    )

    if directive == "no_op_complete":
        assert cursor is not None
        print(
            f"Already complete (cursor at {checkpoint_path}). "
            f"Delete the cursor file to re-process this conversation from scratch.",
            file=sys.stderr,
        )
        return None

    if cursor is None:
        start_index = 0
        accumulated_digest: list[DigestEntry] = []
        next_checkpoint_number = 1
    else:
        start_index = cursor.last_processed_exchange_index + 1
        accumulated_digest = list(cursor.topics_covered_digest)
        next_checkpoint_number = cursor.checkpoint_number + 1

    processed_count = 0
    last_editor_output: EditorOutput | None = None

    while start_index < len(transcript.exchanges):
        slice_exchanges = pick_checkpoint_slice(transcript.exchanges, start_index, budget)
        if not slice_exchanges:
            break

        sliced = ChatTranscript(
            source_path=transcript.source_path,
            exchanges=slice_exchanges,
            metadata=transcript.metadata,
        )

        try:
            agent_calls, batch_duration, batch_started_wall = await _execute_pipeline(
                transcript=sliced,
                vault_path=vault_path,
                topics_covered_digest=accumulated_digest if next_checkpoint_number > 1 else None,
                checkpoint_number=next_checkpoint_number,
            )
            if logs_dir is not None:
                session_log = _build_session_log(
                    transcript=sliced,
                    agent_calls=agent_calls,
                    batch_started_wall=batch_started_wall,
                    batch_duration=batch_duration,
                )
                write_session_log(session_log, logs_dir)
            editor_output = _finalize_result(agent_calls)
        except Exception as exc:
            _save_failed_cursor(
                path=checkpoint_path,
                transcript=transcript,
                transcript_hash=current_hash,
                conversation_id=conversation_id,
                prior_cursor=cursor,
                last_error=str(exc),
            )
            raise

        # Merge Historian's topics_covered_increment into the accumulator.
        historian_call = _first_call(agent_calls, "historian")
        if historian_call is not None and isinstance(historian_call.parsed_output, HistorianOutput):
            increment = historian_call.parsed_output.topics_covered_increment
            if increment:
                accumulated_digest.extend(increment)

        last_idx = slice_exchanges[-1].index
        processed_count += len(slice_exchanges)
        is_complete = last_idx == len(transcript.exchanges) - 1
        is_cap_reached = max_exchanges is not None and processed_count >= max_exchanges

        status: Literal["complete", "interrupted"] = "complete" if is_complete else "interrupted"
        save_checkpoint(
            checkpoint_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                conversation_id=conversation_id,
                transcript_hash=current_hash,
                last_processed_exchange_index=last_idx,
                checkpoint_number=next_checkpoint_number,
                status=status,
                topics_covered_digest=accumulated_digest,
                updated_at=datetime.now(UTC),
            ),
        )

        last_editor_output = editor_output

        if is_complete or is_cap_reached:
            break

        start_index = last_idx + 1
        next_checkpoint_number += 1

    return last_editor_output


def _first_call(calls: dict[str, _AgentCall], subagent_type: str) -> _AgentCall | None:
    """Return the first call to a given sub-agent (None if not invoked)."""
    return next((c for c in calls.values() if c.subagent_type == subagent_type), None)


def _parse_wiki_link(link: str) -> tuple[str, str | None]:
    """Parse `[[Target]]` or `[[Target|alias]]` into (to_page, display_text)."""
    match = _WIKI_LINK_RE.search(link)
    if not match:
        return (link, None)
    return (match.group(1).strip(), (match.group(2) or "").strip() or None)


def _extract_cross_links(editor_output: EditorOutput) -> list[CrossLinkRecord]:
    """Pull CrossLinkRecord entries out of EditorOutput.results.crosslinks_applied."""
    records: list[CrossLinkRecord] = []
    for result in editor_output.results:
        from_page = str(result.final_frontmatter.get("title", "")) or Path(result.file_path).stem
        for link in result.crosslinks_applied:
            to_page, display = _parse_wiki_link(link)
            records.append(
                CrossLinkRecord(from_page=from_page, to_page=to_page, display_text=display)
            )
    return records


def _call_to_agent_output(call: _AgentCall) -> AgentOutput:
    """Convert an internal _AgentCall into the public AgentOutput model."""
    if call.subagent_type not in {"synthesis", "historian", "editor"}:
        raise ValueError(f"Unexpected sub-agent in session: {call.subagent_type}")
    return AgentOutput(
        agent_name=call.subagent_type,  # type: ignore[arg-type]
        input_summary=call.input_prompt[:500],
        output=call.parsed_output,
        duration_seconds=call.duration_seconds,
        status="error" if call.error else "success",
        error_detail=call.error,
    )


def _build_session_log(
    transcript: ChatTranscript,
    agent_calls: dict[str, _AgentCall],
    batch_started_wall: datetime,
    batch_duration: float,
) -> SessionLog:
    """Assemble a SessionLog from per-agent capture + batch metadata."""
    timestamp = batch_started_wall.strftime("%Y-%m-%dT%H:%M:%SZ")
    transcript_stem = Path(transcript.source_path).stem
    session_id = f"{timestamp}-{transcript_stem}"

    agents: dict[str, AgentOutput] = {}
    errors: list[SessionError] = []
    for call in agent_calls.values():
        if call.subagent_type not in {"synthesis", "historian", "editor"}:
            continue
        agents[call.subagent_type] = _call_to_agent_output(call)
        if call.error:
            errors.append(
                SessionError(
                    agent=call.subagent_type,
                    error_type="agent_failure",
                    message=call.error,
                )
            )

    editor_call = _first_call(agent_calls, "editor")
    wiki_pages_created: list[str] = []
    wiki_pages_updated: list[str] = []
    cross_links: list[CrossLinkRecord] = []
    processed_indices: set[int] = set()
    if isinstance(editor_call, _AgentCall) and isinstance(editor_call.parsed_output, EditorOutput):
        wiki_pages_created = [
            r.file_path for r in editor_call.parsed_output.results if r.action == "created"
        ]
        wiki_pages_updated = [
            r.file_path for r in editor_call.parsed_output.results if r.action == "updated"
        ]
        cross_links = _extract_cross_links(editor_call.parsed_output)
        for decision in editor_call.parsed_output.decisions:
            if decision.action != "skipped":
                processed_indices.update(decision.exchange_indices)

    status: Literal["completed", "partial_failure"] = (
        "completed" if not errors and editor_call and not editor_call.error else "partial_failure"
    )

    if not processed_indices and status == "completed":
        exchanges_processed = len(transcript.exchanges)
    else:
        exchanges_processed = len(processed_indices)

    return SessionLog(
        session_id=session_id,
        timestamp=timestamp,
        source_transcript=transcript.source_path,
        exchanges_total=len(transcript.exchanges),
        exchanges_processed=exchanges_processed,
        agents=agents,
        wiki_pages_created=wiki_pages_created,
        wiki_pages_updated=wiki_pages_updated,
        cross_links=cross_links,
        status=status,
        errors=errors,
        duration_seconds=batch_duration,
    )


def _finalize_result(agent_calls: dict[str, _AgentCall]) -> EditorOutput:
    """Decide pipeline outcome from the captured agent calls and return EditorOutput.

    The orchestrator-as-meta-agent pattern (Spec 001-003) means upstream agent
    output flows through TWO parsers: the meta-agent's lenient JSON-string-level
    interpretation, and this Python orchestrator's strict Pydantic validation.
    When they disagree (e.g., the meta-agent successfully passed Synthesis's
    wrapped output to Editor, but my parser couldn't unwrap it for the session
    log), the real signal of pipeline success is Editor's output — not whether
    each upstream agent's response happened to be parseable on my side.

    Revised after 2026-06-27 real-data smoke (Spec 004 findings, Bug 3):
    Editor's parsed_output is the source of truth. If Editor produced a valid
    EditorOutput, the pipeline succeeded regardless of upstream parse errors.
    Only walk upstream when Editor itself never ran or failed — those are the
    cases where something genuinely blocked the pipeline.

    See `project_spec004_findings.md` for the failure pattern that motivated
    this relaxation.
    """
    editor_call = _first_call(agent_calls, "editor")

    if editor_call is not None and isinstance(editor_call.parsed_output, EditorOutput):
        # Pipeline succeeded end-to-end. Upstream per-agent parse errors (if any)
        # are recorded in the session log but do not override Editor's success.
        return editor_call.parsed_output

    # Editor was missing or unusable — diagnose what blocked the pipeline.
    synthesis_call = _first_call(agent_calls, "synthesis")
    if synthesis_call is None:
        raise RuntimeError(
            "Synthesis agent was never invoked — orchestrator did not follow the pipeline"
        )
    if synthesis_call.error:
        raise RuntimeError(
            f"Synthesis agent failed (non-recoverable per FR-013): {synthesis_call.error}"
        )

    if editor_call is None:
        raise RuntimeError(
            "Editor agent was not invoked even though Synthesis succeeded — "
            "the orchestrator did not follow the pipeline"
        )
    if editor_call.error:
        raise RuntimeError(f"Editor agent failed (non-recoverable per FR-013): {editor_call.error}")
    raise RuntimeError("Editor agent output could not be parsed as EditorOutput")
