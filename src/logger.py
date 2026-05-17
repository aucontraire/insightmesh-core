"""Typed agent outputs and session logging for InsightMesh.

Defines the Pydantic models that wrap each sub-agent's structured output
(SynthesisOutput, HistorianOutput, EditorOutput, EditorDecision,
EditorDecisionSignals) plus the SessionLog wrapper that captures a full batch
run for FR-008 and FR-009 (per-agent independent logs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.wiki import WikiPageDraft, WikiPageResult


class EditorDecisionSignals(BaseModel):
    """The three FR-007 signals the Editor evaluated for one draft."""

    model_config = ConfigDict(strict=True)

    normalized_title_match: bool
    tag_overlap_count: int
    tag_overlap_tags: list[str]
    content_keyword_overlap: Literal["strong", "partial", "weak", "none"]


class EditorDecision(BaseModel):
    """FR-014: Editor's reasoning for one create-vs-update-vs-skip choice.

    `action="skipped"` indicates the Editor attempted but failed to write this
    specific page (per FR-013 recoverable failure path); the `rationale` field
    holds the skip reason. A skipped page appears in `decisions` but NOT in the
    parent `EditorOutput.results` list.
    """

    model_config = ConfigDict(strict=True)

    draft_title: str
    action: Literal["created", "updated", "skipped"]
    candidate_existing_page: str | None
    signals: EditorDecisionSignals
    confidence: Literal["high", "medium", "low"]
    rationale: str
    exchange_indices: list[int] = Field(default_factory=list)
    """Indices of the source transcript exchanges this decision covered.
    Empty when the Editor doesn't pass them through (back-compat default).
    Used by SessionLog to compute exchanges_processed accurately per FR-010."""


class SynthesisOutput(BaseModel):
    """The Synthesis agent's structured output."""

    model_config = ConfigDict(strict=True)

    drafts: list[WikiPageDraft]


class HistorianOutput(BaseModel):
    """The Historian agent's structured output."""

    model_config = ConfigDict(strict=True)

    augmented_drafts: list[WikiPageDraft]


class EditorOutput(BaseModel):
    """The Editor agent's structured output — final pipeline result."""

    model_config = ConfigDict(strict=True)

    results: list[WikiPageResult]
    decisions: list[EditorDecision]


class CrossLinkRecord(BaseModel):
    """One cross-link relationship captured in the session log.

    Replaces the loose `dict` type the original data-model spec used —
    constitution v1.1.2 §Project Standards requires typed shapes.
    """

    model_config = ConfigDict(strict=True)

    from_page: str
    to_page: str
    display_text: str | None = None


class SessionError(BaseModel):
    """One error captured during a batch run.

    Replaces the loose `dict` type for SessionLog.errors per constitution
    v1.1.2 §Project Standards.
    """

    model_config = ConfigDict(strict=True)

    agent: str
    error_type: str
    message: str


class AgentOutput(BaseModel):
    """Per-agent captured output for the session log (FR-009).

    `output` is None when `status == "error"` (the agent failed before
    producing parseable structured output). Untagged union resolves correctly
    because the three output types have distinct top-level field names
    (`drafts`, `augmented_drafts`, `results`).
    """

    model_config = ConfigDict(strict=True)

    agent_name: Literal["synthesis", "historian", "editor"]
    input_summary: str
    output: SynthesisOutput | HistorianOutput | EditorOutput | None
    duration_seconds: float
    status: Literal["success", "error"]
    error_detail: str | None = None


class SessionLog(BaseModel):
    """Full structured record of one batch run (FR-008).

    Written to disk by `write_session_log()` after each pipeline invocation,
    regardless of success or partial failure. Serves as both evaluation data
    (per-agent quality assessment) and the schema blueprint for the Phase B
    PostgreSQL persistence layer.
    """

    model_config = ConfigDict(strict=True)

    session_id: str
    timestamp: str
    source_transcript: str
    exchanges_total: int
    exchanges_processed: int
    agents: dict[str, AgentOutput]
    wiki_pages_created: list[str]
    wiki_pages_updated: list[str]
    cross_links: list[CrossLinkRecord]
    status: Literal["completed", "partial_failure"]
    errors: list[SessionError]
    duration_seconds: float


def write_session_log(log: SessionLog, logs_dir: Path) -> Path:
    """Serialize a SessionLog to disk as pretty-printed JSON.

    Creates `logs_dir` if it doesn't exist. Filename is `<session_id>.json`
    placed directly in `logs_dir`. Returns the path written.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_path = logs_dir / f"{log.session_id}.json"
    out_path.write_text(log.model_dump_json(indent=2))
    return out_path
