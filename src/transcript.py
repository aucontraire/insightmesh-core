"""Transcript parsing for InsightMesh batch synthesis.

Loads a JSON chat transcript (ChatGPT/Claude export format) and pairs the flat
message array into conversational Exchange turns per data-model.md §Pairing Rules.

Exposes Pydantic v2 models (Message, Exchange, ChatTranscript) and a single
public function `parse_transcript()`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KNOWN_ROLES: set[str] = {"user", "assistant"}


class Message(BaseModel):
    """Atomic unit of communication: one message from one role."""

    model_config = ConfigDict(strict=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    timestamp: str | None = None


class Exchange(BaseModel):
    """One conversational turn: a user prompt plus the assistant's response.

    `assistant_message` is None only when the transcript ends with an unanswered
    user message, or when two consecutive user messages produce a standalone
    earlier exchange (data-model.md §Pairing Rules 3 and 5).
    """

    model_config = ConfigDict(strict=True)

    index: int = Field(ge=0)
    user_message: Message
    assistant_message: Message | None = None


class ChatTranscript(BaseModel):
    """Parsed transcript with paired exchanges and source metadata."""

    model_config = ConfigDict(strict=True)

    source_path: str
    exchanges: list[Exchange]
    metadata: dict[str, Any] = Field(default_factory=dict)


def _normalize_messages(raw: list[dict[str, Any]]) -> list[Message]:
    """Validate raw dicts into Message objects, normalizing unknown roles to 'assistant'.

    Per data-model.md §Pairing Rule 6 — system/tool/function/etc. roles map to assistant.
    """
    messages: list[Message] = []
    for item in raw:
        if item.get("role") not in KNOWN_ROLES:
            item = {**item, "role": "assistant"}
        messages.append(Message.model_validate(item))
    return messages


def _pair_messages(messages: list[Message]) -> list[Exchange]:
    """Pair messages into Exchanges per data-model.md §Pairing Rules.

    Rule 2: skip leading orphan assistant messages.
    Rule 3: consecutive user messages → earlier becomes standalone (no response).
    Rule 4: consecutive assistant messages → concatenated into one assistant_message.
    Rule 5: trailing user message → Exchange with assistant_message=None.
    """
    start = 0
    while start < len(messages) and messages[start].role == "assistant":
        start += 1

    exchanges: list[Exchange] = []
    idx = 0
    i = start

    while i < len(messages):
        msg = messages[i]
        if msg.role != "user":
            i += 1
            continue

        user_msg = msg
        j = i + 1
        assistant_parts: list[str] = []
        while j < len(messages) and messages[j].role == "assistant":
            assistant_parts.append(messages[j].content)
            j += 1

        assistant_msg: Message | None
        if assistant_parts:
            assistant_msg = Message(
                role="assistant",
                content="\n\n".join(assistant_parts),
            )
        else:
            assistant_msg = None

        exchanges.append(
            Exchange(
                index=idx,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
        )
        idx += 1
        i = max(j, i + 1)

    return exchanges


def parse_transcript(path: Path) -> ChatTranscript:
    """Load and pair a JSON transcript file into a ChatTranscript.

    Raises:
        FileNotFoundError: if the file does not exist.
        json.JSONDecodeError: if the file is not valid JSON.
        ValueError: if the JSON root is not a list, the transcript is empty,
            or no usable exchanges remain after pairing.
        pydantic.ValidationError: if any message has wrong types or shapes.
    """
    if not path.exists():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    raw = json.loads(path.read_text())

    if not isinstance(raw, list):
        raise ValueError(
            f"Transcript JSON must be a list of messages, got {type(raw).__name__}"
        )

    if len(raw) == 0:
        raise ValueError(f"Transcript is empty: {path}")

    messages = _normalize_messages(raw)
    exchanges = _pair_messages(messages)

    if not exchanges:
        raise ValueError(f"Transcript has no usable exchanges after pairing: {path}")

    return ChatTranscript(source_path=str(path), exchanges=exchanges)
