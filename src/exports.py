"""Multi-conversation export handling for the InsightMesh CLI.

This module is a thin wrapper around the `echomine` library (PyPI
`echomine>=1.3.0,<2.0.0`). InsightMesh delegates Claude.ai and ChatGPT
export schema parsing to echomine per spec FR-023; this module exposes
the two helpers the CLI needs (`list_conversations`, `extract_conversation`)
plus the small projection model and boundary error type.

Per FR-024, only the listed echomine public-API symbols are imported.
Per FR-027, echomine exceptions are translated at the boundary and chained.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from echomine import (
    ClaudeAdapter,
    Conversation,
    ConversationProvider,
    EchomineError,
    Message,
    OpenAIAdapter,
    ParseError,
    SchemaVersionError,
    ValidationError,
)
from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console
from rich.table import Table

from src.transcript import ChatTranscript, Exchange
from src.transcript import Message as InternalMessage

__all__ = [
    "InsightMeshSummary",
    "UnrecognizedExportFormat",
    "EmptyConversationError",
    "list_conversations",
    "extract_conversation",
    "render_list_table",
    "resolve_conversation_value",
]


# ---------------------------------------------------------------------------
# Projection type (InsightMesh-owned)
# ---------------------------------------------------------------------------


class InsightMeshSummary(BaseModel):
    """One row of `insightmesh list` output.

    Projection over `echomine.Conversation` carrying only the four fields
    InsightMesh renders. Keeps the list-rendering contract stable regardless
    of EchoMine's full model evolution (per data-model.md § InsightMeshSummary).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    id: str
    title: str
    created: datetime
    message_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Boundary errors (InsightMesh-owned)
# ---------------------------------------------------------------------------


class UnrecognizedExportFormat(Exception):
    """Neither echomine.ClaudeAdapter nor echomine.OpenAIAdapter recognized the file.

    Raised by `detect_adapter` and `list_conversations`/`extract_conversation`
    when both adapters reject an input file. Message format matches spec FR-027
    and contracts/cli-commands.md.
    """

    def __init__(self, path: Path, attempted: list[str]) -> None:
        self.path = path
        self.attempted = attempted
        super().__init__(
            f"not a recognized export format: {path} "
            f"(tried {', '.join(attempted)}); "
            f"expected a multi-conversation export from Claude.ai or ChatGPT"
        )


class EmptyConversationError(Exception):
    """The selected conversation has no usable user/assistant messages.

    Raised by `extract_conversation` per the Edge Case: a conversation whose
    canonical thread contains only `system` / `tool` roles, or whose ChatGPT
    tree has no `current_node`.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(
            f"conversation '{conversation_id}' contains no usable user/assistant messages"
        )


# ---------------------------------------------------------------------------
# Adapter detection
# ---------------------------------------------------------------------------


def _translate_echomine_error(exc: EchomineError, path: Path) -> Exception:
    """Boundary translator per FR-027.

    Maps echomine exception types to InsightMesh-owned error messages.
    Caller is responsible for chaining via `raise ... from echomine_exc`.
    """
    if isinstance(exc, SchemaVersionError):
        # Caller usually wraps this as UnrecognizedExportFormat after both adapters fail;
        # passing through for unusual cases.
        return RuntimeError(f"error: schema version not supported in {path}: {exc}")
    if isinstance(exc, ParseError):
        return RuntimeError(f"error: cannot parse export file {path}: {exc}")
    if isinstance(exc, ValidationError):
        return RuntimeError(f"error: invalid conversation data in {path}: {exc}")
    # Unrecognized EchomineError subclass: re-raise unchanged.
    return exc


_ATTEMPTED_ADAPTERS = ["ClaudeAdapter", "OpenAIAdapter"]


def detect_adapter(path: Path) -> ConversationProvider[Conversation]:
    """Pick adapter by structural markers in the export JSON (per FR-025).

    ClaudeAdapter wins when the first conversation has a `chat_messages` key.
    OpenAIAdapter wins when the first conversation has a `mapping` key.
    Emits a stderr warning + returns ClaudeAdapter if both markers are present
    (defensive — should not happen in real exports). Raises
    `UnrecognizedExportFormat` otherwise.

    Rationale: probing each adapter via streaming was unreliable because
    `ClaudeAdapter` silently skips non-Claude conversations instead of raising
    `SchemaVersionError`. Structural detection on the first conversation root
    keys is fast (single `json.load`) and discriminating in practice.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise UnrecognizedExportFormat(path, _ATTEMPTED_ADAPTERS) from exc

    if not isinstance(data, list) or not data:
        raise UnrecognizedExportFormat(path, _ATTEMPTED_ADAPTERS)

    first = data[0]
    if not isinstance(first, dict):
        raise UnrecognizedExportFormat(path, _ATTEMPTED_ADAPTERS)

    has_claude_marker = "chat_messages" in first
    has_openai_marker = "mapping" in first

    if has_claude_marker and has_openai_marker:
        print(
            "warning: export matched both Claude.ai and ChatGPT adapters; using Claude.ai",
            file=sys.stderr,
        )
        return ClaudeAdapter()
    if has_claude_marker:
        return ClaudeAdapter()
    if has_openai_marker:
        return OpenAIAdapter()

    raise UnrecognizedExportFormat(path, _ATTEMPTED_ADAPTERS)


# ---------------------------------------------------------------------------
# List and extract
# ---------------------------------------------------------------------------


def _on_skip_warning(conversation_id: str, reason: str) -> None:
    """`on_skip` callback for list streaming (FR-028 (a))."""
    print(f"warning: skipped conversation {conversation_id}: {reason}", file=sys.stderr)


def list_conversations(path: Path) -> list[InsightMeshSummary]:
    """Stream the export via the detected adapter, project to summaries, sort newest-first.

    Per FR-005, results are ordered most-recent-first by `created` timestamp.
    Per FR-028 (a), `on_skip` warnings are emitted to stderr for malformed
    conversations encountered mid-stream.
    """
    adapter = detect_adapter(path)
    summaries: list[InsightMeshSummary] = []
    collected_before_error: list[InsightMeshSummary] = []

    try:
        # echomine adapters accept on_skip kwarg (per cognivault_integration example).
        for conv in adapter.stream_conversations(path, on_skip=_on_skip_warning):
            summaries.append(_project_summary(conv))
            collected_before_error = summaries
    except ParseError as exc:
        # FR-021/Edge Case: flush what we have, warn, exit non-zero (caller handles exit).
        if collected_before_error:
            print(
                f"warning: listing aborted after {len(collected_before_error)} "
                f"conversations: {exc}",
                file=sys.stderr,
            )
        raise _translate_echomine_error(exc, path) from exc
    except ValidationError as exc:
        raise _translate_echomine_error(exc, path) from exc

    summaries.sort(key=lambda s: s.created, reverse=True)
    return summaries


def _conversational_count(conv: Conversation) -> int:
    """Count conversational-category turns, matching what the source app shows.

    echomine's `Conversation.message_count` is `len(messages)`, which includes
    non-conversational nodes (system, tool I/O, reasoning) — inflating the count
    versus the human-visible turn count. We count only messages whose
    `content_type_category` is "conversational". Falls back to `message_count`
    for pre-1.4.0 echomine that doesn't populate the category field.
    """
    if not any("content_type_category" in m.metadata for m in conv.messages):
        return conv.message_count
    return sum(
        1 for m in conv.messages if m.metadata.get("content_type_category") == "conversational"
    )


def _project_summary(conv: Conversation) -> InsightMeshSummary:
    """Project an echomine.Conversation to an InsightMeshSummary."""
    return InsightMeshSummary(
        id=conv.id,
        title=conv.title,
        created=conv.created_at,
        message_count=_conversational_count(conv),
    )


def resolve_conversation_value(value: str, summaries: list[InsightMeshSummary]) -> int:
    """Resolve `--conversation` value to a summary index (per FR-010).

    Rule: if `value` parses as a non-negative int AND is in range `[0, len(summaries))`,
    treat as index; otherwise treat as id and look up in `summaries`. Raises
    `KeyError` (caught at CLI boundary) when no match.
    """
    try:
        as_int = int(value)
        if 0 <= as_int < len(summaries):
            return as_int
    except ValueError:
        pass
    # Fall through to id matching.
    for i, s in enumerate(summaries):
        if s.id == value:
            return i
    raise KeyError(value)


def extract_conversation(path: Path, selector: str) -> ChatTranscript:
    """Resolve `--conversation` and convert the selected echomine.Conversation to ChatTranscript.

    Per FR-026: walks canonical thread (via `get_thread(current_node)` for ChatGPT,
    linear `messages` for Claude.ai); emits `{role, content}` only for user/assistant
    messages; skips system/tool roles. Per FR-028 (b): on_skip during streaming
    raises no-match if the skipped conversation matches selector; otherwise emits
    stderr warning and continues.
    """
    summaries = list_conversations(path)
    try:
        idx = resolve_conversation_value(selector, summaries)
    except KeyError as exc:
        raise KeyError(
            f"no conversation matches --conversation '{selector}' in {path}. "
            f"Run 'insightmesh list {path}' to see valid ids."
        ) from exc

    selected_id = summaries[idx].id

    # Re-open and stream to the selected conversation.
    adapter = detect_adapter(path)
    skipped_selected: list[str] = []

    def _on_skip_extract(conversation_id: str, reason: str) -> None:
        if conversation_id == selected_id:
            skipped_selected.append(reason)
        else:
            print(
                f"warning: skipped conversation {conversation_id}: {reason}",
                file=sys.stderr,
            )

    target: Conversation | None = None
    try:
        for conv in adapter.stream_conversations(path, on_skip=_on_skip_extract):
            if conv.id == selected_id:
                target = conv
                break
    except ParseError as exc:
        raise _translate_echomine_error(exc, path) from exc
    except ValidationError as exc:
        raise _translate_echomine_error(exc, path) from exc

    if skipped_selected:
        raise KeyError(
            f"no conversation matches --conversation '{selector}' in {path} "
            f"(echomine skipped it: {skipped_selected[0]}). "
            f"Run 'insightmesh list {path}' to see valid ids."
        )
    if target is None:
        # Should not happen since selector resolved; defensive.
        raise KeyError(f"no conversation matches --conversation '{selector}' in {path}.")

    messages = _walk_canonical_thread(target)
    role_content = _to_role_content(messages)
    if not role_content:
        raise EmptyConversationError(selected_id)

    # Build a ChatTranscript using src/transcript.py's existing pairing logic indirectly:
    # we construct paired Exchanges from the role/content sequence.
    exchanges: list[Exchange] = []
    idx_counter = 0
    i = 0
    while i < len(role_content):
        msg = role_content[i]
        if msg["role"] != "user":
            i += 1
            continue
        user_msg = InternalMessage(role="user", content=msg["content"])
        j = i + 1
        assistant_parts: list[str] = []
        while j < len(role_content) and role_content[j]["role"] == "assistant":
            assistant_parts.append(role_content[j]["content"])
            j += 1
        asst_msg: InternalMessage | None
        if assistant_parts:
            asst_msg = InternalMessage(role="assistant", content="\n\n".join(assistant_parts))
        else:
            asst_msg = None
        exchanges.append(
            Exchange(index=idx_counter, user_message=user_msg, assistant_message=asst_msg)
        )
        idx_counter += 1
        i = max(j, i + 1)

    if not exchanges:
        raise EmptyConversationError(selected_id)

    return ChatTranscript(source_path=str(path), exchanges=exchanges)


def _walk_canonical_thread(conv: Conversation) -> list[Message]:
    """Walk the conversation's canonical thread (per FR-026 (a)).

    For ChatGPT-shaped conversations, `current_node` is stored in
    `conv.metadata['current_node']` by EchoMine's OpenAI adapter; we look it up
    and call `get_thread(current_node)` to get the root-to-leaf canonical path
    (this drops abandoned-branch messages from edited conversations).

    For Claude.ai (linear conversation), `conv.messages` is already the
    canonical thread in order.
    """
    metadata = getattr(conv, "metadata", None) or {}
    current_node = metadata.get("current_node") if isinstance(metadata, dict) else None
    if isinstance(current_node, str) and current_node:
        # ChatGPT path: walk root → current_node.
        thread = conv.get_thread(current_node)
        if thread:
            return list(thread)
    # Claude.ai path (or fallback when current_node lookup fails): messages list is linear.
    return list(conv.messages)


def _render_attachments(msg: Message) -> str:
    """Render echomine attachment text as labeled block(s) for inline folding (Spec 003).

    Reads `msg.metadata["attachments"]` (per `contracts/attachment-rendering.md`),
    skips entries whose `extracted_content` is empty or whitespace, and emits a
    labeled block per attachment whose header identifies the source: `file: <name>`
    when a filename is present, or `pasted text` when unnamed. Multiple blocks
    join in original source order, separated by a blank line. Returns "" when
    nothing renderable is found.
    """
    raw = msg.metadata.get("attachments")
    if not isinstance(raw, list):
        return ""
    blocks: list[str] = []
    for att in raw:
        if not isinstance(att, dict):
            continue
        text = att.get("extracted_content")
        if not isinstance(text, str) or not text.strip():
            continue
        name = att.get("file_name")
        header = f"file: {name}" if isinstance(name, str) and name else "pasted text"
        blocks.append(
            f"--- Attached/pasted content ({header}) ---\n{text}\n--- End attached content ---"
        )
    return "\n\n".join(blocks)


def _to_role_content(messages: list[Message]) -> list[dict[str, str]]:
    """Convert echomine.Message list to flat {role, content} per Spec 002 (FR-026) and Spec 003.

    For each user/assistant message:
    - Render any attachment extracted text via `_render_attachments` FIRST (the
      harvest-before-skip ordering rule from Spec 003): attachment-only messages
      arrive with `content_type_category == "attachment"` and `content == ""`,
      and would otherwise be dropped before their metadata could be read.
    - `category == "attachment"`: contribute a turn only when attachment text is
      present; the rendered block becomes the message content.
    - `category == "conversational"` (default when the field is absent,
      preserving pre-1.4.0 echomine behavior): contribute the typed content;
      when attachment text is also present, append it as a labeled block after
      the typed content.
    - All other categories (reasoning/tool_io/system/media/unknown) are
      excluded, even if they carry an attachments key.
    - Messages with no attachments are unchanged from prior behavior
      (Spec 003 FR-007 / FR-011 no-regression).
    """
    out: list[dict[str, str]] = []
    for msg in messages:
        if msg.role not in {"user", "assistant"}:
            continue
        category = msg.metadata.get("content_type_category", "conversational")
        attachment_text = _render_attachments(msg)
        base = msg.content.strip()

        if category == "attachment":
            if not attachment_text:
                continue
            content = attachment_text
        elif category == "conversational":
            if not base and not attachment_text:
                continue
            if attachment_text and base:
                content = f"{msg.content}\n\n{attachment_text}"
            elif attachment_text:
                content = attachment_text
            else:
                content = msg.content
        else:
            continue

        out.append({"role": msg.role, "content": content})
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_list_table(summaries: list[InsightMeshSummary]) -> str:
    """Render summaries as a Rich table + id-by-index footer (per FR-004, FR-008).

    Returns the rendered string. Caller writes to stdout. Empty input returns
    a single-line empty-state message per FR-006.
    """
    if not summaries:
        return "No conversations in export.\n"

    import io

    # Single table with every field per row (Index, ID, Title, Msgs, Created)
    # so users can match a title to its id/index without cross-referencing a
    # separate footer. Either Index or ID works with `batch --conversation`.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=130)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Index", justify="right", style="cyan", no_wrap=True)
    table.add_column("ID", no_wrap=True, style="dim")
    table.add_column("Title", overflow="ellipsis", max_width=44)
    table.add_column("Msgs", justify="right")
    table.add_column("Created", style="dim", no_wrap=True)

    for i, summary in enumerate(summaries):
        table.add_row(
            str(i),
            summary.id,
            summary.title or "(untitled)",
            str(summary.message_count),
            summary.created.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
    return buf.getvalue()
