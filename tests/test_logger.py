"""Tests for src.logger session logging (T017).

Covers FR-008 (session log written), FR-009 (per-agent independent logs),
FR-014 (EditorDecision reasoning captured), and the SessionLog / AgentOutput
Pydantic models from data-model.md.

Tests are written first per TDD; T018 implements the missing model fields and
`write_session_log()` to make these pass.

NOTE: This file references models that don't all exist yet (SessionLog,
AgentOutput, CrossLinkRecord, SessionError, write_session_log). Import errors
on first run are expected — T018 closes that gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.logger import (
    AgentOutput,
    CrossLinkRecord,
    EditorDecision,
    EditorDecisionSignals,
    EditorOutput,
    HistorianOutput,
    SessionError,
    SessionLog,
    SynthesisOutput,
    write_session_log,
)
from src.wiki import WikiPageDraft, WikiPageResult


def _synthesis_output_fixture() -> SynthesisOutput:
    return SynthesisOutput(
        drafts=[
            WikiPageDraft(
                tentative_title="Speed of Light",
                exchange_indices=[0, 1, 2],
                draft_content="The speed of light is...",
                suggested_tags=["physics", "constants"],
            )
        ]
    )


def _historian_output_fixture() -> HistorianOutput:
    return HistorianOutput(
        augmented_drafts=[
            WikiPageDraft(
                tentative_title="Speed of Light",
                exchange_indices=[0, 1, 2],
                draft_content="The speed of light is...",
                suggested_tags=["physics", "constants"],
                related_pages=["Electromagnetism"],
                crosslink_recommendations=["[[Electromagnetism]]"],
            )
        ]
    )


def _editor_decision_fixture(action: str = "created") -> EditorDecision:
    return EditorDecision(
        draft_title="Speed of Light",
        action=action,  # type: ignore[arg-type]
        candidate_existing_page=None if action == "created" else "Speed of Light",
        signals=EditorDecisionSignals(
            normalized_title_match=action != "created",
            tag_overlap_count=2 if action != "created" else 0,
            tag_overlap_tags=["physics", "constants"] if action != "created" else [],
            content_keyword_overlap="strong" if action != "created" else "none",
        ),
        confidence="high",
        rationale=(
            "No existing page; created fresh."
            if action == "created"
            else "Title and tags match — updating."
        ),
    )


def _editor_output_fixture() -> EditorOutput:
    return EditorOutput(
        results=[
            WikiPageResult(
                file_path="/vault/InsightMesh/Speed of Light.md",
                action="created",
                final_frontmatter={"title": "Speed of Light", "tags": ["insightmesh"]},
                crosslinks_applied=["[[Electromagnetism]]"],
            )
        ],
        decisions=[_editor_decision_fixture("created")],
    )


class TestCrossLinkRecord:
    """Per data-model.md SessionLog.cross_links — typed (not raw dict)."""

    def test_minimal(self) -> None:
        link = CrossLinkRecord(from_page="Speed of Light", to_page="Electromagnetism")
        assert link.from_page == "Speed of Light"
        assert link.to_page == "Electromagnetism"
        assert link.display_text is None

    def test_with_alias(self) -> None:
        link = CrossLinkRecord(
            from_page="Speed of Light",
            to_page="Electromagnetism",
            display_text="electromagnetic theory",
        )
        assert link.display_text == "electromagnetic theory"

    def test_round_trip(self) -> None:
        original = CrossLinkRecord(from_page="A", to_page="B", display_text="b")
        restored = CrossLinkRecord.model_validate_json(original.model_dump_json())
        assert restored == original


class TestSessionError:
    """Per data-model.md SessionLog.errors — typed (not raw dict)."""

    def test_minimal(self) -> None:
        err = SessionError(
            agent="historian",
            error_type="rate_limit",
            message="HTTP 429 returned by upstream",
        )
        assert err.agent == "historian"
        assert err.error_type == "rate_limit"

    def test_round_trip(self) -> None:
        original = SessionError(agent="editor", error_type="parse_error", message="invalid JSON")
        restored = SessionError.model_validate_json(original.model_dump_json())
        assert restored == original


class TestAgentOutputModel:
    """Per-agent captured output (FR-009)."""

    def test_synthesis_agent_output(self) -> None:
        ao = AgentOutput(
            agent_name="synthesis",
            input_summary="20-exchange transcript",
            output=_synthesis_output_fixture(),
            duration_seconds=42.5,
            status="success",
        )
        assert ao.agent_name == "synthesis"
        assert ao.error_detail is None
        assert isinstance(ao.output, SynthesisOutput)

    def test_historian_agent_output(self) -> None:
        ao = AgentOutput(
            agent_name="historian",
            input_summary="3 drafts from synthesis",
            output=_historian_output_fixture(),
            duration_seconds=15.2,
            status="success",
        )
        assert isinstance(ao.output, HistorianOutput)

    def test_editor_agent_output(self) -> None:
        ao = AgentOutput(
            agent_name="editor",
            input_summary="3 augmented drafts",
            output=_editor_output_fixture(),
            duration_seconds=51.0,
            status="success",
        )
        assert isinstance(ao.output, EditorOutput)

    def test_error_status(self) -> None:
        ao = AgentOutput(
            agent_name="historian",
            input_summary="3 drafts",
            output=None,
            duration_seconds=2.1,
            status="error",
            error_detail="MCPVault connection refused",
        )
        assert ao.status == "error"
        assert ao.output is None
        assert ao.error_detail == "MCPVault connection refused"

    def test_invalid_agent_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentOutput.model_validate(
                {
                    "agent_name": "refiner",  # not one of synthesis/historian/editor
                    "input_summary": "x",
                    "output": None,
                    "duration_seconds": 1.0,
                    "status": "success",
                }
            )

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentOutput.model_validate(
                {
                    "agent_name": "synthesis",
                    "input_summary": "x",
                    "output": None,
                    "duration_seconds": 1.0,
                    "status": "warning",  # not success/error
                }
            )


class TestSessionLogModel:
    """SessionLog Pydantic schema."""

    def _full_session_log(self) -> SessionLog:
        return SessionLog(
            session_id="2026-05-16T17:52:40Z-single_topic",
            timestamp="2026-05-16T17:52:40Z",
            source_transcript="/path/to/single_topic.json",
            exchanges_total=20,
            exchanges_processed=20,
            agents={
                "synthesis": AgentOutput(
                    agent_name="synthesis",
                    input_summary="20-exchange transcript",
                    output=_synthesis_output_fixture(),
                    duration_seconds=42.5,
                    status="success",
                ),
                "historian": AgentOutput(
                    agent_name="historian",
                    input_summary="1 draft",
                    output=_historian_output_fixture(),
                    duration_seconds=15.2,
                    status="success",
                ),
                "editor": AgentOutput(
                    agent_name="editor",
                    input_summary="1 augmented draft",
                    output=_editor_output_fixture(),
                    duration_seconds=51.0,
                    status="success",
                ),
            },
            wiki_pages_created=["/vault/InsightMesh/Speed of Light.md"],
            wiki_pages_updated=[],
            cross_links=[CrossLinkRecord(from_page="Speed of Light", to_page="Electromagnetism")],
            status="completed",
            errors=[],
            duration_seconds=108.7,
        )

    def test_full_session_log_constructs(self) -> None:
        log = self._full_session_log()
        assert log.status == "completed"
        assert log.exchanges_total == 20
        assert len(log.agents) == 3
        assert "synthesis" in log.agents

    def test_round_trip(self) -> None:
        original = self._full_session_log()
        restored = SessionLog.model_validate_json(original.model_dump_json())
        assert restored == original

    def test_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            SessionLog.model_validate({})

    def test_status_literal_enforcement(self) -> None:
        with pytest.raises(ValidationError):
            SessionLog.model_validate(
                {
                    "session_id": "x",
                    "timestamp": "2026-05-16T17:52:40Z",
                    "source_transcript": "x",
                    "exchanges_total": 1,
                    "exchanges_processed": 1,
                    "agents": {},
                    "wiki_pages_created": [],
                    "wiki_pages_updated": [],
                    "cross_links": [],
                    "status": "abandoned",  # not completed/partial_failure
                    "errors": [],
                    "duration_seconds": 0.0,
                }
            )

    def test_partial_failure_with_errors(self) -> None:
        log = SessionLog(
            session_id="2026-05-16T18:00:00Z-batch",
            timestamp="2026-05-16T18:00:00Z",
            source_transcript="/path/to/transcript.json",
            exchanges_total=20,
            exchanges_processed=18,
            agents={
                "synthesis": AgentOutput(
                    agent_name="synthesis",
                    input_summary="20-exchange transcript",
                    output=_synthesis_output_fixture(),
                    duration_seconds=40.0,
                    status="success",
                ),
                "historian": AgentOutput(
                    agent_name="historian",
                    input_summary="1 draft",
                    output=None,
                    duration_seconds=2.0,
                    status="error",
                    error_detail="MCPVault connection refused",
                ),
            },
            wiki_pages_created=[],
            wiki_pages_updated=[],
            cross_links=[],
            status="partial_failure",
            errors=[
                SessionError(
                    agent="historian",
                    error_type="connection_refused",
                    message="MCPVault subprocess crashed",
                )
            ],
            duration_seconds=44.0,
        )
        assert log.status == "partial_failure"
        assert len(log.errors) == 1
        assert log.agents["historian"].status == "error"


class TestEditorDecisionCapture:
    """FR-014: per-page decision reasoning is preserved through serialization."""

    def test_editor_decisions_round_trip_via_session_log(self) -> None:
        editor_output = EditorOutput(
            results=[
                WikiPageResult(
                    file_path="/vault/A.md",
                    action="created",
                    final_frontmatter={"title": "A"},
                    crosslinks_applied=[],
                ),
            ],
            decisions=[
                _editor_decision_fixture("created"),
                _editor_decision_fixture("updated"),
                EditorDecision(
                    draft_title="Empty Topic",
                    action="skipped",
                    candidate_existing_page=None,
                    signals=EditorDecisionSignals(
                        normalized_title_match=False,
                        tag_overlap_count=0,
                        tag_overlap_tags=[],
                        content_keyword_overlap="none",
                    ),
                    confidence="low",
                    rationale="Draft content empty after Historian augmentation",
                ),
            ],
        )
        log = SessionLog(
            session_id="x",
            timestamp="2026-05-16T17:52:40Z",
            source_transcript="x",
            exchanges_total=1,
            exchanges_processed=1,
            agents={
                "synthesis": AgentOutput(
                    agent_name="synthesis",
                    input_summary="x",
                    output=_synthesis_output_fixture(),
                    duration_seconds=1.0,
                    status="success",
                ),
                "editor": AgentOutput(
                    agent_name="editor",
                    input_summary="x",
                    output=editor_output,
                    duration_seconds=1.0,
                    status="success",
                ),
            },
            wiki_pages_created=[],
            wiki_pages_updated=[],
            cross_links=[],
            status="completed",
            errors=[],
            duration_seconds=2.0,
        )
        restored = SessionLog.model_validate_json(log.model_dump_json())
        editor_out = restored.agents["editor"].output
        assert isinstance(editor_out, EditorOutput)
        assert len(editor_out.decisions) == 3
        actions = [d.action for d in editor_out.decisions]
        assert actions == ["created", "updated", "skipped"]
        assert editor_out.decisions[2].rationale.startswith("Draft content empty")


class TestWriteSessionLog:
    """write_session_log() file-writing behavior."""

    def _minimal_log(self, session_id: str = "test-session-1") -> SessionLog:
        return SessionLog(
            session_id=session_id,
            timestamp="2026-05-16T17:52:40Z",
            source_transcript="/path/to/x.json",
            exchanges_total=1,
            exchanges_processed=1,
            agents={
                "synthesis": AgentOutput(
                    agent_name="synthesis",
                    input_summary="x",
                    output=_synthesis_output_fixture(),
                    duration_seconds=1.0,
                    status="success",
                ),
            },
            wiki_pages_created=[],
            wiki_pages_updated=[],
            cross_links=[],
            status="completed",
            errors=[],
            duration_seconds=1.0,
        )

    def test_writes_to_logs_dir(self, tmp_path: Path) -> None:
        log = self._minimal_log()
        out = write_session_log(log, tmp_path)
        assert out.exists()
        assert out.parent == tmp_path
        assert out.suffix == ".json"

    def test_filename_includes_session_id(self, tmp_path: Path) -> None:
        log = self._minimal_log(session_id="2026-05-16T17:52:40Z-test")
        out = write_session_log(log, tmp_path)
        assert "2026-05-16T17:52:40Z-test" in out.name

    def test_content_is_parseable_json(self, tmp_path: Path) -> None:
        log = self._minimal_log()
        out = write_session_log(log, tmp_path)
        loaded = json.loads(out.read_text())
        assert loaded["session_id"] == "test-session-1"
        assert loaded["status"] == "completed"

    def test_content_is_valid_session_log(self, tmp_path: Path) -> None:
        log = self._minimal_log()
        out = write_session_log(log, tmp_path)
        restored = SessionLog.model_validate_json(out.read_text())
        assert restored == log

    def test_creates_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "logs" / "subdir"
        # nested does not exist yet
        log = self._minimal_log()
        out = write_session_log(log, nested)
        assert out.exists()
        assert nested.exists()

    def test_multiple_sessions_separate_files(self, tmp_path: Path) -> None:
        log_a = self._minimal_log(session_id="session-A")
        log_b = self._minimal_log(session_id="session-B")
        out_a = write_session_log(log_a, tmp_path)
        out_b = write_session_log(log_b, tmp_path)
        assert out_a != out_b
        assert out_a.exists()
        assert out_b.exists()

    def test_json_is_pretty_printed(self, tmp_path: Path) -> None:
        """Per T018 spec: serialization uses indent=2 for human-readable logs."""
        log = self._minimal_log()
        out = write_session_log(log, tmp_path)
        content = out.read_text()
        # Pretty-printed JSON should contain newlines and indentation
        assert "\n" in content
        assert "  " in content


class TestISO8601Format:
    """All timestamps in the SessionLog must be ISO 8601 strings."""

    def test_session_log_timestamp_format(self) -> None:
        # Just verify the format is accepted — actual format enforcement
        # happens at the orchestrator level (T019) which generates the timestamp.
        log = SessionLog(
            session_id="x",
            timestamp="2026-05-16T17:52:40Z",
            source_transcript="x",
            exchanges_total=0,
            exchanges_processed=0,
            agents={},
            wiki_pages_created=[],
            wiki_pages_updated=[],
            cross_links=[],
            status="completed",
            errors=[],
            duration_seconds=0.0,
        )
        # Should accept ISO 8601 string
        assert log.timestamp.endswith("Z")
        assert "T" in log.timestamp
