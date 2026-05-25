"""Orchestrator for the InsightMesh batch synthesis pipeline.

Runs the three sub-agents (synthesis → historian → editor) on a parsed
transcript using claude-agent-sdk. Agents are auto-discovered from
.claude/agents/*.md and MCPVault from .mcp.json by setting
`setting_sources=["project"]` on `ClaudeAgentOptions`.

Per-agent invocations are captured by matching `Agent`/`Task` `ToolUseBlock`s
to their corresponding `ToolResultBlock`s in the message stream. T019 will
extend this capture to build a SessionLog.
"""

from __future__ import annotations

import json
import re
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
from src.transcript import ChatTranscript

ParsedAgentOutput = SynthesisOutput | HistorianOutput | EditorOutput

_WIKI_LINK_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")

# Spec 002 FR-018: single source of truth for the agents the pipeline depends on.
# Pre-flight check in cli.py imports this to verify every name has a corresponding
# `.claude/agents/<name>.md` file with matching frontmatter `name:` field.
# When future specs add agents (Critic, Researcher), update this list in one place.
EXPECTED_AGENTS: list[str] = ["synthesis", "historian", "editor"]


class _AgentCall(BaseModel):
    """In-memory record of one sub-agent invocation during a batch."""

    model_config = ConfigDict(strict=True)

    tool_use_id: str
    subagent_type: str
    input_prompt: str
    raw_output: str | None = None
    parsed_output: ParsedAgentOutput | None = None
    error: str | None = None
    start_monotonic: float | None = None  # set when ToolUseBlock seen
    end_monotonic: float | None = None  # set when matching ToolResultBlock seen

    @property
    def duration_seconds(self) -> float:
        if self.start_monotonic is None or self.end_monotonic is None:
            return 0.0
        return self.end_monotonic - self.start_monotonic


def _utc_now_iso() -> str:
    """ISO 8601 UTC datetime with seconds precision, Z-form."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_orchestrator_prompt(transcript: ChatTranscript, vault_path: Path) -> str:
    """Build the prompt that drives the main Claude to run the 3-agent pipeline."""
    transcript_json = transcript.model_dump_json()
    batch_timestamp = _utc_now_iso()
    return f"""You are the InsightMesh batch synthesis orchestrator. Run the chat \
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
for vault search. It returns a JSON object with an `augmented_drafts` array.

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


def _try_extract_json(raw: str) -> str:
    """Pull a JSON object out of mixed agent output.

    Agents return clean JSON most of the time, but two real-world conditions
    break naive extraction, sometimes together:

    - The SDK can append trailing metadata after the JSON (an `agentId:`
      resumption line and a `<usage>` block), so the response no longer ends
      with `}`.
    - The agent's own `draft_content` can contain fenced code blocks or braces
      (e.g., a markdown drum-tab pattern), which fools fence/brace heuristics.

    The robust strategy is to locate the first `{` and let a real JSON parser
    (`json.JSONDecoder().raw_decode`) consume exactly one JSON value, ignoring
    everything after it and tolerating braces/fences inside string values.
    Fence and greedy-brace heuristics are kept only as fallbacks.

    Returns the best-guess JSON string; downstream parsing decides if it's
    actually valid.
    """
    stripped = raw.strip()
    start = stripped.find("{")
    if start != -1:
        try:
            _obj, end = json.JSONDecoder().raw_decode(stripped[start:])
            return stripped[start : start + end]
        except json.JSONDecodeError:
            pass
    # Fallback 1: a fenced code block, but only if it actually wraps JSON
    # (so we don't grab a drum-tab / code fence from inside draft_content).
    fence_match = _FENCED_JSON_RE.search(raw)
    if fence_match and fence_match.group(1).strip().startswith("{"):
        return fence_match.group(1).strip()
    # Fallback 2: greedy first-`{` to last-`}`.
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


async def run_batch(
    transcript: ChatTranscript,
    vault_path: Path,
    logs_dir: Path | None = None,
) -> EditorOutput:
    """Run the Synthesis → Historian → Editor pipeline on a transcript.

    Sub-agents are auto-discovered from `.claude/agents/*.md`. MCPVault is
    auto-discovered from `.mcp.json` (requires VAULT_PATH env var set before
    calling — the CLI does this from the `--vault` flag).

    If `logs_dir` is provided, writes a SessionLog JSON file capturing per-agent
    outputs, timing, cross-links, and any errors (FR-008, FR-009).

    Returns the Editor's final structured output.

    Raises:
        RuntimeError: if the editor agent never runs, errors, or produces
            output that fails to parse as EditorOutput. The session log is
            still written before raising (so partial failures are recorded).
    """
    prompt = _build_orchestrator_prompt(transcript, vault_path)
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
                    call.error = (
                        f"Failed to parse {call.subagent_type} output: {exc}"
                    )

    batch_duration = time.monotonic() - batch_started_monotonic

    # Write the session log BEFORE raising on non-recoverable failures, so
    # partial-batch state is preserved on disk for diagnosis.
    if logs_dir is not None:
        session_log = _build_session_log(
            transcript=transcript,
            agent_calls=agent_calls,
            batch_started_wall=batch_started_wall,
            batch_duration=batch_duration,
        )
        write_session_log(session_log, logs_dir)

    return _finalize_result(agent_calls)


def _first_call(
    calls: dict[str, _AgentCall], subagent_type: str
) -> _AgentCall | None:
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
        from_page = str(result.final_frontmatter.get("title", "")) or Path(
            result.file_path
        ).stem
        for link in result.crosslinks_applied:
            to_page, display = _parse_wiki_link(link)
            records.append(
                CrossLinkRecord(
                    from_page=from_page, to_page=to_page, display_text=display
                )
            )
    return records


def _call_to_agent_output(call: _AgentCall) -> AgentOutput:
    """Convert an internal _AgentCall into the public AgentOutput model."""
    if call.subagent_type not in {"synthesis", "historian", "editor"}:
        # Defensive: orchestrator should never invoke other agents in Phase A.
        raise ValueError(
            f"Unexpected sub-agent in session: {call.subagent_type}"
        )
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
    if isinstance(editor_call, _AgentCall) and isinstance(
        editor_call.parsed_output, EditorOutput
    ):
        wiki_pages_created = [
            r.file_path for r in editor_call.parsed_output.results if r.action == "created"
        ]
        wiki_pages_updated = [
            r.file_path for r in editor_call.parsed_output.results if r.action == "updated"
        ]
        cross_links = _extract_cross_links(editor_call.parsed_output)
        # FR-010 traceability: any exchange whose draft was successfully written
        # (created or updated, NOT skipped) counts as processed.
        for decision in editor_call.parsed_output.decisions:
            if decision.action != "skipped":
                processed_indices.update(decision.exchange_indices)

    status: Literal["completed", "partial_failure"] = (
        "completed" if not errors and editor_call and not editor_call.error else "partial_failure"
    )

    # If Editor didn't populate exchange_indices in its decisions (older agent
    # output, or pure happy path on completed runs), fall back to "all" for
    # completed and "best-effort count of written pages" otherwise.
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
    """Apply FR-013 failure semantics to the captured agent calls and return EditorOutput.

    Distinguishes non-recoverable failures (Synthesis, Editor total failure)
    from recoverable ones (Historian skipped — pipeline still produces output;
    per-page Editor skips are handled inside EditorOutput.decisions).
    """
    synthesis_call = _first_call(agent_calls, "synthesis")
    historian_call = _first_call(agent_calls, "historian")
    editor_call = _first_call(agent_calls, "editor")

    if synthesis_call is None:
        raise RuntimeError(
            "Synthesis agent was never invoked — orchestrator did not follow the pipeline"
        )
    if synthesis_call.error:
        raise RuntimeError(
            f"Synthesis agent failed (non-recoverable per FR-013): {synthesis_call.error}"
        )

    # Historian failure is recoverable: the orchestrator should have proceeded
    # to Editor with the synthesis output. Surface a warning but do not raise.
    if historian_call is not None and historian_call.error:
        # Note: we don't currently have a logger here (T019 adds session logging).
        # For now the warning is implicit — the user sees no cross-links in the
        # output and the editor's decision rationale will reflect this.
        pass

    if editor_call is None:
        raise RuntimeError(
            "Editor agent was not invoked even though Synthesis succeeded — "
            "the orchestrator did not follow the pipeline"
        )
    if editor_call.error:
        raise RuntimeError(
            f"Editor agent failed (non-recoverable per FR-013): {editor_call.error}"
        )
    if not isinstance(editor_call.parsed_output, EditorOutput):
        raise RuntimeError(
            "Editor agent output could not be parsed as EditorOutput"
        )
    return editor_call.parsed_output
