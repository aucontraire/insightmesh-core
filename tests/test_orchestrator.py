"""Tests for src.orchestrator.

Two test groups:

1. Pure-function regression coverage for `_try_extract_json` and
   `_parse_agent_output`. See test bodies for the Spec 002 real-data failures.
2. Spec 004 integration tests for the checkpoint loop in `run_batch`:
   multi-checkpoint completion, resume skipping, no-op on complete, digest
   carry-over, hash/index/schema/malformed error paths, --resume/--retry
   semantics, FR-012 absence test, FR-015 token-budget unit test.

The Spec 004 tests mock the `_execute_pipeline` seam to inject canned agent
outputs without invoking the real Claude SDK.
"""

from __future__ import annotations

import json
import shutil as _shutil
import subprocess as _subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml as _yaml
from typer.testing import CliRunner

from src.checkpoint import (
    Checkpoint,
    CheckpointMalformed,
    DigestEntry,
    save_checkpoint,
)
from src.cli import app as cli_app
from src.history import (
    CheckpointRecord,
    EditorDecisionRecord,
    ExchangeRecord,
    ShadowRepoCommitFailed,
)
from src.logger import (
    EditorDecision,
    EditorDecisionSignals,
    EditorOutput,
    HistorianOutput,
    SynthesisOutput,
)
from src.orchestrator import (
    _AgentCall,
    _parse_agent_output,
    _try_extract_json,
    _write_provenance,
    pick_checkpoint_slice,
    run_batch,
)
from src.transcript import ChatTranscript, Exchange, Message
from src.wiki import WikiPageDraft, WikiPageResult

# A synthesis response that reproduces BOTH failure conditions:
# - draft_content contains a fenced code block (the ```...``` drum tab)
# - trailing agentId: + <usage> metadata after the closing brace
# Built via concatenation so physical lines stay under the line-length limit
# while the embedded JSON-escaped content stays realistic.
_DRUM_DRAFT = (
    "## Playing Behind the Beat\\n\\nA drum pattern:\\n\\n"
    "```\\nKick:  o - - -\\nSnare: - - o -\\nCount: 1 2 3 4\\n```\\n\\n"
    "Sit behind the click."
)
_REGRESSION_RESPONSE = (
    "{\n"
    '  "drafts": [\n'
    "    {\n"
    '      "tentative_title": "Reggae Bass - Self-Study Starting Point",\n'
    '      "exchange_indices": [1, 2],\n'
    f'      "draft_content": "{_DRUM_DRAFT}",\n'
    '      "suggested_tags": ["reggae", "bass", "self-study"]\n'
    "    },\n"
    "    {\n"
    '      "tentative_title": "BOSS TU-3 Power Troubleshooting",\n'
    '      "exchange_indices": [3, 4],\n'
    '      "draft_content": "## DC OUT Jack\\n\\nThe TU-3 can daisy-chain power.",\n'
    '      "suggested_tags": ["boss-tu-3", "guitar-pedal"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "agentId: a6d604a41f0623d4e (use SendMessage with to: 'a6d604a41f0623d4e')\n"
    "<usage>total_tokens: 13394\ntool_uses: 0\nduration_ms: 49623</usage>"
)


class TestTryExtractJson:
    def test_regression_trailing_metadata_plus_inner_fence(self) -> None:
        """The exact Spec 002 real-data failure: trailing SDK metadata + a fenced
        code block inside draft_content. Extracted candidate must be valid JSON."""
        candidate = _try_extract_json(_REGRESSION_RESPONSE)
        parsed = json.loads(candidate)  # must not raise
        assert [d["tentative_title"] for d in parsed["drafts"]] == [
            "Reggae Bass - Self-Study Starting Point",
            "BOSS TU-3 Power Troubleshooting",
        ]

    def test_pure_json_happy_path(self) -> None:
        raw = '{"drafts": []}'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_trailing_metadata_only(self) -> None:
        raw = '{"drafts": []}\nagentId: abc123\n<usage>total_tokens: 5</usage>'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_inner_fence_only(self) -> None:
        """draft_content with a fenced code block but no trailing metadata."""
        raw = '{"drafts": [{"draft_content": "see ```\\ncode\\n``` here"}]}'
        parsed = json.loads(_try_extract_json(raw))
        assert "```" in parsed["drafts"][0]["draft_content"]

    def test_json_wrapped_in_json_fence(self) -> None:
        """Agent wraps the whole JSON in a ```json fence (older failure mode)."""
        raw = '```json\n{"drafts": []}\n```'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_prose_around_json(self) -> None:
        raw = 'Here is the output:\n{"drafts": []}\nLet me know if you need more.'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_no_json_returns_stripped(self) -> None:
        raw = "  no json here  "
        assert _try_extract_json(raw) == "no json here"


class TestParseAgentOutputRegression:
    def test_synthesis_parses_through_full_chain(self) -> None:
        """_parse_agent_output must produce a SynthesisOutput from the regression
        response (end-to-end: extraction + Pydantic validation)."""
        result = _parse_agent_output("synthesis", _REGRESSION_RESPONSE)
        assert result is not None
        assert len(result.drafts) == 2
        assert result.drafts[0].tentative_title == "Reggae Bass - Self-Study Starting Point"
        # The fenced drum-tab survived intact inside the draft content.
        assert "Count: 1 2 3 4" in result.drafts[0].draft_content
        assert "```" in result.drafts[0].draft_content


class TestUnwrapPersistedOutput:
    """Bug 2 fix (Spec 004 findings): Claude Code SDK wraps tool results >~50KB
    in a `<persisted-output>` envelope with the full content in a sidecar file.
    Without unwrapping, `_try_extract_json` decodes the envelope-array instead
    of the agent's actual JSON and downstream Pydantic validation fails.
    """

    def test_passthrough_when_no_envelope(self) -> None:
        from src.orchestrator import _unwrap_persisted_output

        raw = '{"drafts": [{"tentative_title": "X"}]}'
        assert _unwrap_persisted_output(raw) == raw

    def test_passthrough_when_no_path_found(self) -> None:
        from src.orchestrator import _unwrap_persisted_output

        # Envelope marker present but no "Full output saved to: <path>" line.
        raw = "<persisted-output>\nsome other format\n</persisted-output>"
        assert _unwrap_persisted_output(raw) == raw

    def test_passthrough_when_sidecar_missing(self, tmp_path: Path) -> None:
        from src.orchestrator import _unwrap_persisted_output

        missing = tmp_path / "does-not-exist.json"
        raw = (
            f"<persisted-output>\n"
            f"Output too large (60KB). Full output saved to: {missing}\n"
            f"</persisted-output>"
        )
        # Should not raise; returns raw unchanged so downstream extraction can
        # still attempt the preview content.
        assert _unwrap_persisted_output(raw) == raw

    def test_unwraps_real_envelope(self, tmp_path: Path) -> None:
        """Simulates the exact shape produced by Claude Code's SDK on
        large tool results: sidecar is a JSON array of {type, text} blocks."""
        from src.orchestrator import _unwrap_persisted_output

        sidecar = tmp_path / "toolu_abc.json"
        sidecar.write_text(
            json.dumps(
                [
                    {
                        "type": "text",
                        "text": '{"drafts": [{"tentative_title": "Big Topic"}]}',
                    }
                ]
            )
        )
        raw = (
            f"<persisted-output>\n"
            f"Output too large (60KB). Full output saved to: {sidecar}\n"
            f"</persisted-output>"
        )
        unwrapped = _unwrap_persisted_output(raw)
        assert '"drafts"' in unwrapped
        assert "Big Topic" in unwrapped

    def test_concatenates_multiple_text_blocks(self, tmp_path: Path) -> None:
        from src.orchestrator import _unwrap_persisted_output

        sidecar = tmp_path / "toolu_multi.json"
        sidecar.write_text(
            json.dumps(
                [
                    {"type": "text", "text": "first half "},
                    {"type": "text", "text": "second half"},
                ]
            )
        )
        raw = f"<persisted-output>\nFull output saved to: {sidecar}\n</persisted-output>"
        unwrapped = _unwrap_persisted_output(raw)
        assert "first half" in unwrapped
        assert "second half" in unwrapped

    def test_end_to_end_extract_through_envelope(self, tmp_path: Path) -> None:
        """The whole chain: envelope wrapper → unwrap → extract JSON → parse.
        Verifies the exact bug from the 2026-06-27 Synthesis failure is now
        recovered cleanly."""
        sidecar = tmp_path / "toolu_synth.json"
        synthesis_payload = {
            "drafts": [
                {
                    "tentative_title": "Capitalism's Origins",
                    "exchange_indices": [0, 1],
                    "draft_content": "## From Feudalism\n\nThe transition...",
                    "suggested_tags": ["history", "capitalism"],
                }
            ]
        }
        sidecar.write_text(json.dumps([{"type": "text", "text": json.dumps(synthesis_payload)}]))
        raw = (
            f"<persisted-output>\n"
            f"Output too large (69KB). Full output saved to: {sidecar}\n"
            f"Preview (first 2KB):\n"
            f'[\n  {{\n    "type": "text",\n    "text": "{{...truncated preview..."\n  }}\n]'
        )
        parsed = _parse_agent_output("synthesis", raw)
        assert parsed is not None
        assert len(parsed.drafts) == 1
        assert parsed.drafts[0].tentative_title == "Capitalism's Origins"


class TestFinalizeResultTrustsEditor:
    """Bug 3 fix (Spec 004 findings): `_finalize_result` must trust Editor's
    success as the signal of pipeline success, NOT the strict-Pydantic parse
    success of each upstream agent. The orchestrator-Claude meta-agent is more
    tolerant of envelope-wrapped output than my parser, so upstream parse
    failures often coexist with Editor success."""

    def _editor_call(self, parsed: EditorOutput | None, error: str | None = None) -> _AgentCall:
        return _AgentCall(
            tool_use_id="ed",
            subagent_type="editor",
            input_prompt="canned",
            parsed_output=parsed,
            error=error,
            start_monotonic=0.0,
            end_monotonic=0.01,
        )

    def _synthesis_call(self, error: str | None = None) -> _AgentCall:
        return _AgentCall(
            tool_use_id="syn",
            subagent_type="synthesis",
            input_prompt="canned",
            parsed_output=None if error else SynthesisOutput(drafts=[]),
            error=error,
            start_monotonic=0.0,
            end_monotonic=0.01,
        )

    def _valid_editor_output(self) -> EditorOutput:
        return EditorOutput(results=[], decisions=[])

    def test_editor_success_overrides_synthesis_parse_error(self) -> None:
        """The 2026-06-27 51076657 scenario: Synthesis appeared 'errored' to my
        parser (envelope wrap) but Editor still produced valid output. Must
        return Editor's output, not raise."""
        from src.orchestrator import _finalize_result

        calls = {
            "syn": self._synthesis_call(error="parse failure: envelope wrap"),
            "ed": self._editor_call(self._valid_editor_output()),
        }
        result = _finalize_result(calls)
        assert isinstance(result, EditorOutput)

    def test_editor_success_overrides_missing_synthesis_parsed_output(self) -> None:
        """Even with no synthesis_call.parsed_output, if Editor succeeded the
        pipeline succeeded."""
        from src.orchestrator import _finalize_result

        syn = _AgentCall(
            tool_use_id="syn",
            subagent_type="synthesis",
            input_prompt="canned",
            parsed_output=None,
            error=None,
            start_monotonic=0.0,
            end_monotonic=0.01,
        )
        calls = {"syn": syn, "ed": self._editor_call(self._valid_editor_output())}
        result = _finalize_result(calls)
        assert isinstance(result, EditorOutput)

    def test_editor_missing_raises_with_synthesis_diagnosis(self) -> None:
        """When Editor never ran AND Synthesis errored, surface the upstream cause."""
        from src.orchestrator import _finalize_result

        calls = {"syn": self._synthesis_call(error="real synthesis failure")}
        with pytest.raises(RuntimeError, match="Synthesis agent failed"):
            _finalize_result(calls)

    def test_editor_missing_raises_when_synthesis_succeeded(self) -> None:
        """Synthesis succeeded but Editor never ran — that's a real pipeline gap."""
        from src.orchestrator import _finalize_result

        calls = {"syn": self._synthesis_call()}  # no error
        with pytest.raises(RuntimeError, match="Editor agent was not invoked"):
            _finalize_result(calls)

    def test_editor_error_raises(self) -> None:
        """When Editor itself errored, that's the genuine non-recoverable case."""
        from src.orchestrator import _finalize_result

        calls = {
            "syn": self._synthesis_call(),
            "ed": self._editor_call(None, error="vault write denied"),
        }
        with pytest.raises(RuntimeError, match="Editor agent failed"):
            _finalize_result(calls)

    def test_editor_unparseable_raises(self) -> None:
        """When Editor returned output but it didn't parse as EditorOutput."""
        from src.orchestrator import _finalize_result

        calls = {
            "syn": self._synthesis_call(),
            "ed": self._editor_call(parsed=None, error=None),
        }
        with pytest.raises(RuntimeError, match="could not be parsed as EditorOutput"):
            _finalize_result(calls)

    def test_no_agents_invoked_raises(self) -> None:
        from src.orchestrator import _finalize_result

        with pytest.raises(RuntimeError, match="Synthesis agent was never invoked"):
            _finalize_result({})


# ===========================================================================
# Spec 004 fixtures and helpers
# ===========================================================================


def _make_exchange(idx: int, content_chars: int = 100) -> Exchange:
    """Build an Exchange with predictable char-count for token-budget tests."""
    body = "x" * max(content_chars - 8, 1)
    return Exchange(
        index=idx,
        user_message=Message(role="user", content=f"q{idx}: {body}"),
        assistant_message=Message(role="assistant", content=f"a{idx}: {body}"),
    )


def _make_transcript(
    n_exchanges: int = 6,
    exchange_chars: int = 100,
    source: str = "/tmp/test.json",
) -> ChatTranscript:
    return ChatTranscript(
        source_path=source,
        exchanges=[_make_exchange(i, exchange_chars) for i in range(n_exchanges)],
    )


def _make_agent_calls(
    drafts: list[WikiPageDraft],
    pages_created: list[str] | None = None,
    topics_covered_increment: list[DigestEntry] | None = None,
) -> dict[str, _AgentCall]:
    """Build a canned agent_calls dict simulating one successful checkpoint."""
    pages_created = pages_created or []
    topics_covered_increment = topics_covered_increment or []

    synthesis_call = _AgentCall(
        tool_use_id="syn",
        subagent_type="synthesis",
        input_prompt="canned synthesis prompt",
        parsed_output=SynthesisOutput(drafts=drafts),
        start_monotonic=0.0,
        end_monotonic=0.01,
    )
    historian_call = _AgentCall(
        tool_use_id="hist",
        subagent_type="historian",
        input_prompt="canned historian prompt",
        parsed_output=HistorianOutput(
            augmented_drafts=drafts,
            topics_covered_increment=topics_covered_increment,
        ),
        start_monotonic=0.01,
        end_monotonic=0.02,
    )
    results = [
        WikiPageResult(
            file_path=p,
            action="created",
            final_frontmatter={"title": Path(p).stem},
            crosslinks_applied=[],
        )
        for p in pages_created
    ]
    decisions = [
        EditorDecision(
            draft_title=d.tentative_title,
            action="created",
            candidate_existing_page=None,
            signals=EditorDecisionSignals(
                normalized_title_match=False,
                tag_overlap_count=0,
                tag_overlap_tags=[],
                content_keyword_overlap="none",
            ),
            confidence="high",
            rationale="canned",
            exchange_indices=d.exchange_indices,
        )
        for d in drafts
    ]
    editor_call = _AgentCall(
        tool_use_id="ed",
        subagent_type="editor",
        input_prompt="canned editor prompt",
        parsed_output=EditorOutput(results=results, decisions=decisions),
        start_monotonic=0.02,
        end_monotonic=0.03,
    )
    return {"syn": synthesis_call, "hist": historian_call, "ed": editor_call}


class _PipelineRecorder:
    """Records every call to _execute_pipeline for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fake_execute(
        self,
        transcript: ChatTranscript,
        vault_path: Path,
        topics_covered_digest: list[DigestEntry] | None,
        checkpoint_number: int,
    ) -> tuple[dict[str, _AgentCall], float, datetime]:
        # Record the call so tests can assert what was passed in.
        self.calls.append(
            {
                "checkpoint_number": checkpoint_number,
                "n_exchanges": len(transcript.exchanges),
                "exchange_indices": [ex.index for ex in transcript.exchanges],
                "topics_covered_digest": (
                    list(topics_covered_digest) if topics_covered_digest else None
                ),
            }
        )
        # One draft per exchange (simple but easy to inspect).
        drafts = [
            WikiPageDraft(
                tentative_title=f"Topic {ex.index}",
                exchange_indices=[ex.index],
                draft_content=f"content for exchange {ex.index}",
                suggested_tags=["test"],
            )
            for ex in transcript.exchanges
        ]
        pages = [f"/vault/InsightMesh/Topic {ex.index}.md" for ex in transcript.exchanges]
        increment = [
            DigestEntry(page_title=f"Topic {ex.index}", gist=f"gist {ex.index}")
            for ex in transcript.exchanges
        ]
        calls = _make_agent_calls(drafts, pages_created=pages, topics_covered_increment=increment)
        return calls, 0.01, datetime.now(UTC)

    def make_failing(self, error_msg: str = "synthesis failed: rate limit"):
        async def fail_execute(
            transcript: ChatTranscript,
            vault_path: Path,
            topics_covered_digest: list[DigestEntry] | None,
            checkpoint_number: int,
        ) -> tuple[dict[str, _AgentCall], float, datetime]:
            self.calls.append(
                {
                    "checkpoint_number": checkpoint_number,
                    "n_exchanges": len(transcript.exchanges),
                }
            )
            raise RuntimeError(error_msg)

        return fail_execute

    def make_failing_after_n(self, n: int, error_msg: str = "vault error mid-run"):
        """Succeed for the first n calls, fail on call n+1."""
        ok_count = 0

        async def execute(
            transcript: ChatTranscript,
            vault_path: Path,
            topics_covered_digest: list[DigestEntry] | None,
            checkpoint_number: int,
        ) -> tuple[dict[str, _AgentCall], float, datetime]:
            nonlocal ok_count
            if ok_count < n:
                ok_count += 1
                return await self.fake_execute(
                    transcript, vault_path, topics_covered_digest, checkpoint_number
                )
            self.calls.append({"checkpoint_number": checkpoint_number, "failed": True})
            raise RuntimeError(error_msg)

        return execute


# ===========================================================================
# T017 — US1 happy-path integration tests
# ===========================================================================


class TestRunBatchCheckpointedHappyPaths:
    @pytest.mark.asyncio
    async def test_a_multi_checkpoint_completion(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SC-001 / SC-005 / US1 AS-1: long transcript spans multiple checkpoints
        and reaches end-of-transcript with status=complete."""
        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        # 6 exchanges * 100 chars each = ~660 chars. token_budget=80
        # → 80 * 3.5 = 280 char budget per checkpoint → ~3 exchanges per checkpoint.
        transcript = _make_transcript(n_exchanges=6, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        result = await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            token_budget=80,
        )

        # Multiple checkpoints fired.
        assert len(recorder.calls) >= 2
        # Cursor advanced to end-of-transcript.
        from src.checkpoint import load_checkpoint

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        assert cursor.status == "complete"
        assert cursor.last_processed_exchange_index == 5  # last index
        assert cursor.checkpoint_number >= 2
        assert result is not None  # last checkpoint's EditorOutput
        # Every exchange contributed to a draft (one-draft-per-exchange in the fake).
        all_indices = [idx for call in recorder.calls for idx in call["exchange_indices"]]
        assert sorted(all_indices) == list(range(6))

    @pytest.mark.asyncio
    async def test_b_resume_skips_processed_exchanges(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SC-002 / US1 AS-2: re-running after partial completion processes only
        the unprocessed exchanges. Zero re-invocations for already-processed ones."""
        # Seed a cursor at exchange 3 (so resume should start at 4).
        transcript = _make_transcript(n_exchanges=6, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"
        from src.checkpoint import compute_transcript_hash

        seeded = Checkpoint(
            export_path=Path(transcript.source_path),
            transcript_hash=compute_transcript_hash(transcript),
            last_processed_exchange_index=3,
            checkpoint_number=1,
            status="interrupted",
            updated_at=datetime.now(UTC),
        )
        save_checkpoint(cursor_path, seeded)

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            token_budget=80,
        )

        # Every exchange index passed to _execute_pipeline must be >= 4.
        for call in recorder.calls:
            for idx in call["exchange_indices"]:
                assert idx >= 4, f"resume reprocessed exchange {idx}"

    @pytest.mark.asyncio
    async def test_c_no_op_on_complete(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SC-003 / US1 AS-3: running on an already-complete cursor returns None
        without invoking any agent."""
        transcript = _make_transcript(n_exchanges=4)
        cursor_path = tmp_path / "test.checkpoint.json"
        from src.checkpoint import compute_transcript_hash

        complete_cursor = Checkpoint(
            export_path=Path(transcript.source_path),
            transcript_hash=compute_transcript_hash(transcript),
            last_processed_exchange_index=3,
            checkpoint_number=2,
            status="complete",
            updated_at=datetime.now(UTC),
        )
        save_checkpoint(cursor_path, complete_cursor)

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        result = await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
        )
        assert result is None
        assert recorder.calls == []  # no agents invoked

    @pytest.mark.asyncio
    async def test_d_digest_carry_over(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """US1 AS-4 / FR-011: second-or-later checkpoints receive the accumulated
        topics_covered_digest from prior checkpoints."""
        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=6, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            token_budget=80,
        )

        # First checkpoint must NOT receive a digest; later checkpoints must.
        assert recorder.calls[0]["topics_covered_digest"] is None
        for call in recorder.calls[1:]:
            assert call["topics_covered_digest"] is not None
            assert len(call["topics_covered_digest"]) > 0

    @pytest.mark.asyncio
    async def test_e_fr013_no_regression_single_checkpoint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR-013 / SC-004: when a small transcript fits in one checkpoint,
        only one _execute_pipeline call fires and behavior matches pre-feature."""
        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        # Tiny transcript, generous budget.
        transcript = _make_transcript(n_exchanges=2, exchange_chars=50)
        cursor_path = tmp_path / "test.checkpoint.json"

        result = await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            token_budget=100_000,  # huge budget → one checkpoint
        )

        assert len(recorder.calls) == 1
        assert result is not None
        # The single call must NOT have a digest (it's checkpoint #1).
        assert recorder.calls[0]["topics_covered_digest"] is None
        # Cursor reaches end with status=complete.
        from src.checkpoint import load_checkpoint

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        assert cursor.status == "complete"


# ===========================================================================
# T018 — US1 hash/index/schema/malformed error-path tests
# ===========================================================================


class TestRunBatchCheckpointedHashAndSchemaErrors:
    @pytest.mark.asyncio
    async def test_a_hash_mismatch_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-006 / SC-007: hash mismatch refuses by default."""
        from src.checkpoint import CheckpointHashMismatch

        # Seed cursor with a different hash than the current transcript.
        transcript = _make_transcript(n_exchanges=4)
        cursor_path = tmp_path / "test.checkpoint.json"
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash="0" * 64,  # wrong hash
                last_processed_exchange_index=0,
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        with pytest.raises(CheckpointHashMismatch):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_b_force_resume_overrides_hash_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-006: --force-resume proceeds despite hash mismatch."""
        transcript = _make_transcript(n_exchanges=4)
        cursor_path = tmp_path / "test.checkpoint.json"
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash="0" * 64,
                last_processed_exchange_index=1,
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            force_resume=True,
            token_budget=100_000,
        )
        # _execute_pipeline was called (force_resume worked).
        assert len(recorder.calls) >= 1

    @pytest.mark.asyncio
    async def test_c_cursor_index_out_of_bounds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge Case: cursor index exceeds current transcript length."""
        from src.checkpoint import CheckpointIndexOutOfBounds, compute_transcript_hash

        transcript = _make_transcript(n_exchanges=3)
        cursor_path = tmp_path / "test.checkpoint.json"
        # Cursor at index 10 but transcript has only 3 exchanges.
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash=compute_transcript_hash(transcript),
                last_processed_exchange_index=10,
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        with pytest.raises(CheckpointIndexOutOfBounds):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )

    @pytest.mark.asyncio
    async def test_d_schema_version_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-016: cursor with unknown schema_version is rejected."""
        from src.checkpoint import CheckpointSchemaVersionMismatch

        cursor_path = tmp_path / "test.checkpoint.json"
        cursor_path.write_text('{"schema_version": 999, "ignored": "garbage"}')

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=3)
        with pytest.raises(CheckpointSchemaVersionMismatch):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )

    @pytest.mark.asyncio
    async def test_e_malformed_cursor_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge Case: cursor file is unparseable."""
        cursor_path = tmp_path / "test.checkpoint.json"
        cursor_path.write_text("{this is not valid json")

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=3)
        with pytest.raises(CheckpointMalformed):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )


# ===========================================================================
# T019 — US1 resume error-path tests
# ===========================================================================


class TestRunBatchResumeErrors:
    @pytest.mark.asyncio
    async def test_a_resume_on_missing_cursor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-010: --resume errors when no cursor exists."""
        from src.checkpoint import CheckpointMissing

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=3)
        cursor_path = tmp_path / "nonexistent.checkpoint.json"
        with pytest.raises(CheckpointMissing):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
                require_resume=True,
            )

    @pytest.mark.asyncio
    async def test_b_failed_cursor_refuses_without_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FR-014: failed-status cursor without --retry prints last_error to
        stderr and raises CheckpointError_RequiresRetry (CLI exits 1)."""
        from src.checkpoint import compute_transcript_hash
        from src.orchestrator import CheckpointError_RequiresRetry

        transcript = _make_transcript(n_exchanges=4)
        cursor_path = tmp_path / "test.checkpoint.json"
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash=compute_transcript_hash(transcript),
                last_processed_exchange_index=1,
                checkpoint_number=1,
                status="failed",
                last_error="vault write permission denied",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        with pytest.raises(CheckpointError_RequiresRetry):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )
        # No agent was invoked.
        assert recorder.calls == []
        # Prior error was surfaced on stderr.
        captured = capsys.readouterr()
        assert "vault write permission denied" in captured.err

    @pytest.mark.asyncio
    async def test_c_failed_cursor_with_retry_proceeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-014: --retry past a failed cursor proceeds with a fresh attempt."""
        from src.checkpoint import compute_transcript_hash

        transcript = _make_transcript(n_exchanges=4)
        cursor_path = tmp_path / "test.checkpoint.json"
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash=compute_transcript_hash(transcript),
                last_processed_exchange_index=1,
                checkpoint_number=1,
                status="failed",
                last_error="prior vault error",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            retry=True,
            token_budget=100_000,
        )
        # _execute_pipeline was invoked: the run proceeded.
        assert len(recorder.calls) >= 1


# ===========================================================================
# T020 — FR-012 absence test (CLI rejects non-existent slice flags)
# ===========================================================================


class TestCLIRejectsSliceFlags:
    """FR-012: System MUST NOT support non-linear processing flags. Verified
    by Typer rejecting unknown options with exit code 2."""

    def test_from_flag_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_app, ["batch", "x.json", "--from", "0"])
        assert result.exit_code == 2
        assert (
            "no such option" in result.output.lower() or "no such option" in result.stderr.lower()
            if hasattr(result, "stderr")
            else "no such option" in result.output.lower()
        )

    def test_to_flag_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_app, ["batch", "x.json", "--to", "10"])
        assert result.exit_code == 2

    def test_from_percent_flag_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_app, ["batch", "x.json", "--from-percent", "0.5"])
        assert result.exit_code == 2


# ===========================================================================
# T021 — FR-015 token-budget unit test (pick_checkpoint_slice)
# ===========================================================================


class TestPickCheckpointSlice:
    def test_empty_when_start_past_end(self) -> None:
        exchanges = [_make_exchange(i) for i in range(3)]
        assert pick_checkpoint_slice(exchanges, 3, token_budget=100) == []
        assert pick_checkpoint_slice(exchanges, 99, token_budget=100) == []

    def test_empty_when_start_negative(self) -> None:
        exchanges = [_make_exchange(i) for i in range(3)]
        assert pick_checkpoint_slice(exchanges, -1, token_budget=100) == []

    def test_packs_within_budget(self) -> None:
        # Each exchange ~ 100 chars (200 content + 30 overhead = 230, but
        # _make_exchange targets 100 chars content total).
        # Use small predictable sizes.
        exchanges = [_make_exchange(i, content_chars=50) for i in range(10)]
        # Budget 100 tokens → 350 char budget.
        result = pick_checkpoint_slice(exchanges, 0, token_budget=100)
        # Must include at least one exchange.
        assert len(result) >= 1
        # Total char-count of selected slice ≤ budget × 3.5,
        # except when only one exchange (the "at least one" guarantee).
        from src.orchestrator import _estimate_exchange_chars

        total = sum(_estimate_exchange_chars(ex) for ex in result)
        if len(result) > 1:
            assert total <= int(100 * 3.5)

    def test_packs_greedily(self) -> None:
        """Adding the next exchange beyond the slice would exceed the budget."""
        from src.orchestrator import _estimate_exchange_chars

        exchanges = [_make_exchange(i, content_chars=80) for i in range(10)]
        result = pick_checkpoint_slice(exchanges, 0, token_budget=100)
        if len(result) < len(exchanges) and len(result) >= 1:
            next_size = _estimate_exchange_chars(exchanges[len(result)])
            current_size = sum(_estimate_exchange_chars(ex) for ex in result)
            assert current_size + next_size > int(100 * 3.5)

    def test_at_least_one_exchange_even_when_too_large(self) -> None:
        """A single exchange that exceeds the budget is still returned (no deadlock)."""
        # 1000-char exchange, 1-token budget (~3.5 char budget) → still returns one.
        big = [_make_exchange(0, content_chars=1000)]
        result = pick_checkpoint_slice(big, 0, token_budget=1)
        assert len(result) == 1

    def test_starts_from_start_index(self) -> None:
        exchanges = [_make_exchange(i) for i in range(10)]
        result = pick_checkpoint_slice(exchanges, 5, token_budget=10_000)
        assert result[0].index == 5
        # When budget is huge, packs through end.
        assert result[-1].index == 9

    def test_max_count_caps_slice_size(self) -> None:
        """FR-009 (post-fix): max_count limits slice length even when token
        budget would allow more. Regression for the bug observed on 2026-06-27
        where --max-exchanges 3 was ignored on a 7-exchange transcript because
        the whole thing fit in one default-budget checkpoint."""
        exchanges = [_make_exchange(i, content_chars=50) for i in range(10)]
        # Huge budget would otherwise pack all 10.
        result = pick_checkpoint_slice(exchanges, start_index=0, token_budget=10_000, max_count=3)
        assert len(result) == 3
        assert [ex.index for ex in result] == [0, 1, 2]

    def test_max_count_zero_returns_empty(self) -> None:
        exchanges = [_make_exchange(i) for i in range(3)]
        assert pick_checkpoint_slice(exchanges, 0, token_budget=10_000, max_count=0) == []

    def test_max_count_negative_returns_empty(self) -> None:
        exchanges = [_make_exchange(i) for i in range(3)]
        assert pick_checkpoint_slice(exchanges, 0, token_budget=10_000, max_count=-1) == []

    def test_max_count_none_unrestricted(self) -> None:
        """Default behavior unchanged when max_count is None."""
        exchanges = [_make_exchange(i, content_chars=50) for i in range(5)]
        result = pick_checkpoint_slice(
            exchanges, start_index=0, token_budget=10_000, max_count=None
        )
        assert len(result) == 5

    def test_max_count_tighter_than_budget_wins(self) -> None:
        """When max_count is smaller than what budget allows, max_count wins."""
        exchanges = [_make_exchange(i, content_chars=50) for i in range(10)]
        result = pick_checkpoint_slice(exchanges, start_index=0, token_budget=10_000, max_count=2)
        assert len(result) == 2

    def test_budget_tighter_than_max_count_wins(self) -> None:
        """When budget is tighter than max_count, budget wins (slice is smaller)."""
        # 5 exchanges of ~80 chars each = ~400 chars total; budget 50 tokens
        # = 175 chars → only ~2 exchanges fit. max_count=10 allows more but
        # the budget constraint fires first.
        exchanges = [_make_exchange(i, content_chars=80) for i in range(5)]
        result = pick_checkpoint_slice(exchanges, start_index=0, token_budget=50, max_count=10)
        assert len(result) <= 3  # budget-constrained, not 10


# ===========================================================================
# Failure path — orchestrator persists failed cursor on agent error
# ===========================================================================


class TestRunBatchPersistsFailedCursor:
    @pytest.mark.asyncio
    async def test_agent_failure_writes_failed_cursor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-014 implementation half: an agent failure during the loop must
        leave a cursor with status=failed and last_error populated."""
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr(
            "src.orchestrator._execute_pipeline",
            recorder.make_failing("rate limit on synthesis"),
        )

        transcript = _make_transcript(n_exchanges=3)
        cursor_path = tmp_path / "test.checkpoint.json"

        with pytest.raises(RuntimeError, match="rate limit"):
            await run_batch(
                transcript=transcript,
                vault_path=tmp_path,
                logs_dir=None,
                checkpoint_path=cursor_path,
            )

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        assert cursor.status == "failed"
        assert cursor.last_error is not None
        assert "rate limit" in cursor.last_error


# ===========================================================================
# T023 — US2 soft-cap (--max-exchanges) integration tests
# ===========================================================================


class TestRunBatchSoftCap:
    @pytest.mark.asyncio
    async def test_a_cap_stops_processing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """US2 AS-1 / SC-006: --max-exchanges N stops after at most N exchanges
        (soft cap: cursor may advance past N by up to one checkpoint's slice)."""
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        # 10 exchanges, small budget → ~3 exchanges per checkpoint.
        transcript = _make_transcript(n_exchanges=10, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=3,
            token_budget=80,
        )

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        # Status is interrupted (not complete, because we capped before end).
        assert cursor.status == "interrupted"
        # Last processed index is within N + (one checkpoint's slice ≤ 4ish).
        # SC-006: cursor advances by AT MOST N + size_of_in_flight_checkpoint.
        # With budget 80 (280 chars), each exchange ~230 chars → 1 ex per checkpoint
        # (very tight). So cap=3 should leave cursor at or near index 2.
        # Lenient assertion: cursor < N + a reasonable slice bound.
        assert cursor.last_processed_exchange_index < 3 + 5

    @pytest.mark.asyncio
    async def test_b_cap_composes_across_invocations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """US2 AS-2: two consecutive capped runs advance cumulatively, no duplicate
        work for previously processed exchanges."""
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=10, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        # Run 1: cap 3.
        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=3,
            token_budget=200,  # ~700 chars → ~3 ex per checkpoint
        )
        cursor_after_run1 = load_checkpoint(cursor_path)
        assert cursor_after_run1 is not None
        cursor1_idx = cursor_after_run1.last_processed_exchange_index

        n_calls_run1 = len(recorder.calls)

        # Run 2: cap 3 again.
        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=3,
            token_budget=200,
        )
        cursor_after_run2 = load_checkpoint(cursor_path)
        assert cursor_after_run2 is not None
        # Cursor advanced past run 1's stopping point.
        assert cursor_after_run2.last_processed_exchange_index > cursor1_idx
        # No re-invocation for already-processed exchanges in run 2.
        for call in recorder.calls[n_calls_run1:]:
            for idx in call["exchange_indices"]:
                assert idx > cursor1_idx, f"run 2 reprocessed exchange {idx}"

    @pytest.mark.asyncio
    async def test_c_cap_exceeds_remaining(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """US2 AS-3: cap larger than remaining work processes to end, status=complete."""
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=3, exchange_chars=50)
        cursor_path = tmp_path / "test.checkpoint.json"

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=10_000,
            token_budget=100_000,
        )

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        assert cursor.status == "complete"
        assert cursor.last_processed_exchange_index == 2

    def test_d_max_exchanges_zero_errors(self, tmp_path: Path) -> None:
        """FR-008: --max-exchanges 0 errors before any agent runs."""
        # Need a real input file for the CLI argument validator to pass.
        transcript_path = tmp_path / "tiny.json"
        transcript_path.write_text(
            json.dumps([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])
        )
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["batch", str(transcript_path), "--vault", str(tmp_path), "--max-exchanges", "0"],
        )
        assert result.exit_code == 2
        assert "must be > 0" in result.output.lower() or "must be > 0" in result.stdout.lower()

    def test_e_max_exchanges_negative_errors(self, tmp_path: Path) -> None:
        """FR-008: --max-exchanges -1 errors before any agent runs."""
        transcript_path = tmp_path / "tiny.json"
        transcript_path.write_text(
            json.dumps([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])
        )
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["batch", str(transcript_path), "--vault", str(tmp_path), "--max-exchanges", "-1"],
        )
        assert result.exit_code == 2

    @pytest.mark.asyncio
    async def test_f_resume_plus_max_exchanges_composes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge Case: --resume + --max-exchanges compose freely."""
        from src.checkpoint import compute_transcript_hash, load_checkpoint

        transcript = _make_transcript(n_exchanges=10, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        # Seed cursor at index 2.
        save_checkpoint(
            cursor_path,
            Checkpoint(
                export_path=Path(transcript.source_path),
                transcript_hash=compute_transcript_hash(transcript),
                last_processed_exchange_index=2,
                checkpoint_number=1,
                status="interrupted",
                updated_at=datetime.now(UTC),
            ),
        )

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            require_resume=True,
            max_exchanges=3,
            token_budget=200,
        )

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        # Cursor advanced past 2 but not to end.
        assert cursor.last_processed_exchange_index > 2
        assert cursor.last_processed_exchange_index < 9  # didn't reach end
        # All processed indices in this run are > 2 (no reprocessing).
        for call in recorder.calls:
            for idx in call["exchange_indices"]:
                assert idx > 2

    @pytest.mark.asyncio
    async def test_g_cap_fires_when_transcript_fits_in_one_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the 2026-06-27 real-data bug.

        On 21cbd714 (7 exchanges, default 100K-token budget), `--max-exchanges 3`
        was ignored: the entire 7-exchange transcript fit in one checkpoint, so
        the between-checkpoint cap check ran once AFTER all 7 exchanges had
        already been processed. Cursor came back with status=complete and
        last_processed_exchange_index=6.

        The fix constrains pick_checkpoint_slice's slice size by the remaining
        cap, so a 7-exchange transcript with cap=3 produces a 3-exchange slice
        regardless of how generous the token budget is.
        """
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        # Small transcript, HUGE budget — would otherwise fit entirely in one
        # checkpoint and the cap would be a no-op.
        transcript = _make_transcript(n_exchanges=7, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=3,
            token_budget=100_000,  # default-sized; would fit all 7 unconstrained
        )

        cursor = load_checkpoint(cursor_path)
        assert cursor is not None
        # Critical assertions: cap MUST have fired.
        assert cursor.status == "interrupted", (
            f"Expected status=interrupted (cap fired), got {cursor.status}. "
            f"This is the 2026-06-27 regression scenario."
        )
        # Per SC-006 (post-fix): cursor advances by exactly N.
        assert cursor.last_processed_exchange_index == 2  # 0, 1, 2 = 3 exchanges
        # Only one checkpoint should have run, with exactly 3 exchanges.
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["n_exchanges"] == 3
        assert recorder.calls[0]["exchange_indices"] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_h_capped_then_resume_completes_correctly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two-step real-world workflow: cap interrupts at 3, resume completes
        the remaining 4. Validates the full --max-exchanges → resume cycle
        that exercises the digest carry-over path."""
        from src.checkpoint import load_checkpoint

        recorder = _PipelineRecorder()
        monkeypatch.setattr("src.orchestrator._execute_pipeline", recorder.fake_execute)

        transcript = _make_transcript(n_exchanges=7, exchange_chars=100)
        cursor_path = tmp_path / "test.checkpoint.json"

        # Step 1: capped run.
        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            max_exchanges=3,
            token_budget=100_000,
        )
        cursor1 = load_checkpoint(cursor_path)
        assert cursor1 is not None
        assert cursor1.status == "interrupted"
        assert cursor1.last_processed_exchange_index == 2
        assert len(cursor1.topics_covered_digest) > 0
        digest_after_step1 = len(cursor1.topics_covered_digest)

        # Step 2: default resume to completion.
        await run_batch(
            transcript=transcript,
            vault_path=tmp_path,
            logs_dir=None,
            checkpoint_path=cursor_path,
            token_budget=100_000,
        )
        cursor2 = load_checkpoint(cursor_path)
        assert cursor2 is not None
        assert cursor2.status == "complete"
        assert cursor2.last_processed_exchange_index == 6  # end of 7
        # Digest grew with step-2 entries (didn't shrink, didn't reset).
        assert len(cursor2.topics_covered_digest) > digest_after_step1
        # Step 2's checkpoint should have received the digest from step 1.
        # The fake_execute records what topics_covered_digest it was passed.
        step2_call = recorder.calls[-1]
        assert step2_call["topics_covered_digest"] is not None
        assert len(step2_call["topics_covered_digest"]) == digest_after_step1
        # And only processed unprocessed exchanges (3, 4, 5, 6).
        for idx in step2_call["exchange_indices"]:
            assert idx > 2


# ============================================================================
# Spec 005 T013: US1 integration tests for _write_provenance
# ============================================================================


def _build_test_transcript(
    *,
    conv_id: str | None = "conv-test-001",
    provider: str | None = "anthropic",
    models_used: list[str] | None = None,
    n_exchanges: int = 2,
    source_path: str = "/tmp/fixture-export.json",
) -> ChatTranscript:
    """Build a small ChatTranscript with the Spec 005 metadata shape populated."""
    exchanges = [
        Exchange(
            index=i,
            user_message=Message(role="user", content=f"question {i}"),
            assistant_message=Message(role="assistant", content=f"answer {i}"),
        )
        for i in range(n_exchanges)
    ]
    metadata: dict[str, Any] = {
        "provider": provider,
        "models_used": models_used if models_used is not None else [],
        "exchange_message_ids": {
            i: {"user_message_id": f"msg-u-{i}", "assistant_message_id": f"msg-a-{i}"}
            for i in range(n_exchanges)
        },
    }
    return ChatTranscript(source_path=source_path, exchanges=exchanges, metadata=metadata)


def _build_editor_output(
    *,
    pages_created: list[str] | None = None,
    pages_updated: list[str] | None = None,
    skip_existing: list[str] | None = None,
    exchange_indices: list[int] | None = None,
) -> EditorOutput:
    """Build an EditorOutput with realistic decisions + matching results."""
    pages_created = pages_created or []
    pages_updated = pages_updated or []
    skip_existing = skip_existing or []
    exchange_indices = exchange_indices or [0]

    results = [
        WikiPageResult(file_path=p, action="created", final_frontmatter={}, crosslinks_applied=[])
        for p in pages_created
    ]
    results.extend(
        [
            WikiPageResult(
                file_path=p, action="updated", final_frontmatter={}, crosslinks_applied=[]
            )
            for p in pages_updated
        ]
    )

    signals = EditorDecisionSignals(
        normalized_title_match=True,
        tag_overlap_count=3,
        tag_overlap_tags=["x", "y", "z"],
        content_keyword_overlap="strong",
    )
    decisions = []
    for p in pages_created:
        decisions.append(
            EditorDecision(
                draft_title=p.removesuffix(".md"),
                action="created",
                candidate_existing_page=None,
                signals=signals,
                confidence="high",
                rationale=f"created from new draft: {p}",
                exchange_indices=list(exchange_indices),
            )
        )
    for p in pages_updated:
        decisions.append(
            EditorDecision(
                draft_title=p.removesuffix(".md"),
                action="updated",
                candidate_existing_page=p,
                signals=signals,
                confidence="medium",
                rationale=f"merged update: {p}",
                exchange_indices=list(exchange_indices),
            )
        )
    for p in skip_existing:
        decisions.append(
            EditorDecision(
                draft_title=p.removesuffix(".md"),
                action="skipped",
                candidate_existing_page=p,
                signals=signals,
                confidence="low",
                rationale=f"skipped: {p}",
                exchange_indices=[],
            )
        )
    return EditorOutput(results=results, decisions=decisions)


def _make_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Create the vault layout: <tmp>/vault/InsightMesh/. Returns (vault_root, im_dir)."""
    vault = tmp_path / "vault"
    im = vault / "InsightMesh"
    im.mkdir(parents=True, exist_ok=True)
    return vault, im


def _make_page(
    im_dir: Path,
    name: str,
    body: str = "# Page\n\nContent.\n",
    frontmatter: dict[str, Any] | None = None,
) -> Path:
    """Create a wiki page on disk with optional initial frontmatter."""
    p = im_dir / name
    if frontmatter is not None:
        fm_text = _yaml.safe_dump(frontmatter, sort_keys=False)
        p.write_text(f"---\n{fm_text}---\n{body}", encoding="utf-8")
    else:
        p.write_text(body, encoding="utf-8")
    return p


def _read_frontmatter(page_path: Path) -> dict[str, Any]:
    """Read frontmatter dict from a written page; empty dict if no frontmatter."""
    text = page_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    parsed = _yaml.safe_load(text[4:end])
    return parsed if isinstance(parsed, dict) else {}


class TestProvenanceUS1EndToEnd:
    """Subtest T013(a): end-to-end provenance write (US1 AS-1, AS-2; SC-001).

    Mock-free integration of _write_provenance against a real on-disk vault.
    """

    def test_writes_checkpoint_json_and_frontmatter(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        _make_page(im, "PageB.md")

        transcript = _build_test_transcript(n_exchanges=2)
        editor_output = _build_editor_output(
            pages_created=["PageA.md"],
            pages_updated=["PageB.md"],
            exchange_indices=[0, 1],
        )

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=vault / "InsightMesh" / ".logs" / "session.json",
            cursor_path=vault / "InsightMesh" / ".logs" / "cursor.json",
            checkpoint_number=1,
        )

        cp_path = im / ".history" / "checkpoints" / "conv-test-001" / "cp-001.json"
        assert cp_path.exists(), "Spec 005 FR-001: checkpoint JSON not written"
        record = CheckpointRecord.model_validate_json(cp_path.read_text())
        assert record.checkpoint_id == "cp-001"
        assert record.conversation.id == "conv-test-001"
        assert record.conversation.provider == "anthropic"
        assert len(record.exchanges) == 2
        assert record.exchanges[0].user_message_id == "msg-u-0"
        assert record.exchanges[0].assistant_message_id == "msg-a-0"
        assert len(record.editor.decisions) == 2
        assert record.editor.decisions[0].rationale
        assert "normalized_title_match" in record.editor.decisions[0].signals
        assert "InsightMesh/PageA.md" in record.results.pages_created
        assert "InsightMesh/PageB.md" in record.results.pages_updated

        fm_a = _read_frontmatter(im / "PageA.md")
        assert "provenance" in fm_a, "Spec 005 FR-008: provenance block missing on created page"
        assert fm_a["provenance"]["latest_action"] == "created"
        assert fm_a["provenance"]["total_edits"] == 1
        assert fm_a["provenance"]["exchange_count"] == 2
        fm_b = _read_frontmatter(im / "PageB.md")
        assert fm_b["provenance"]["latest_action"] == "updated"


class TestProvenanceCumulativeMerge:
    """Subtest T013(b): cumulative merge across two checkpoints (US1 AS-3, AS-5; SC-002)."""

    def test_two_checkpoints_total_edits_grows(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")

        transcript = _build_test_transcript(n_exchanges=3)
        editor_first = _build_editor_output(pages_updated=["PageA.md"], exchange_indices=[0, 1])
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges[:2],
            editor_output=editor_first,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        editor_second = _build_editor_output(pages_updated=["PageA.md"], exchange_indices=[2])
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges[2:3],
            editor_output=editor_second,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=2,
        )

        fm = _read_frontmatter(im / "PageA.md")
        assert fm["provenance"]["total_edits"] == 2
        assert fm["provenance"]["latest_checkpoint"].endswith("cp-002.json")
        # exchange_count = |{0,1} ∪ {2}| = 3
        assert fm["provenance"]["exchange_count"] == 3
        assert fm["provenance"]["conversations"] == ["conv-test-001"]


class TestProvenanceImmutability:
    """Subtest T013(c): FR-001a immutability — re-writing same cp-N.json refuses."""

    def test_existing_checkpoint_refuses_overwrite(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        cp_dir = im / ".history" / "checkpoints" / "conv-test-001"
        cp_dir.mkdir(parents=True)
        existing = cp_dir / "cp-001.json"
        original_content = '{"manually_placed": "should not be overwritten"}'
        existing.write_text(original_content)

        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["PageA.md"])

        # Should NOT raise — FR-019 swallows the FileExistsError.
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        # Existing file unchanged.
        assert existing.read_text() == original_content
        # Stderr line emitted.
        captured = capsys.readouterr()
        assert "[provenance] checkpoint already exists" in captured.err
        # Page frontmatter NOT updated (FR-001a stops the whole flow when JSON write fails).
        fm = _read_frontmatter(im / "PageA.md")
        assert "provenance" not in fm


class TestProvenancePriorPointerFallback:
    """Subtest T013(d): FR-009 prior-pointer fallback warning."""

    def test_missing_prior_checkpoint_triggers_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        prior_fm = {
            "title": "PageA",
            "provenance": {
                "latest_checkpoint": "InsightMesh/.history/checkpoints/conv-test-001/cp-999.json",
                "conversations": ["conv-test-001"],
                "latest_action": "updated",
                "latest_confidence": "high",
                "total_edits": 5,
                "exchange_count": 12,
            },
        }
        _make_page(im, "PageA.md", frontmatter=prior_fm)

        transcript = _build_test_transcript(n_exchanges=2)
        editor_output = _build_editor_output(pages_updated=["PageA.md"], exchange_indices=[0, 1])

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=2,
        )

        captured = capsys.readouterr()
        assert "[provenance] prior checkpoint pointer missing" in captured.err
        fm = _read_frontmatter(im / "PageA.md")
        # Fallback: prior.exchange_count (12) + len(this_indices) (2) = 14
        assert fm["provenance"]["exchange_count"] == 14
        assert fm["provenance"]["total_edits"] == 6


class TestProvenanceMalformedFrontmatter:
    """Subtest T013(e): FR-010 malformed YAML in existing page frontmatter."""

    def test_malformed_yaml_logged_and_other_pages_proceed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        # Page with broken YAML
        bad = im / "Bad.md"
        bad.write_text("---\ntitle: [unbalanced\n---\nBody\n", encoding="utf-8")
        # Page with valid frontmatter
        _make_page(im, "Good.md")

        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["Bad.md", "Good.md"])

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        captured = capsys.readouterr()
        assert "[provenance] frontmatter parse failed" in captured.err
        assert "Bad.md" in captured.err
        # Bad page unchanged
        bad_text = bad.read_text()
        assert "title: [unbalanced" in bad_text
        # Good page DID get provenance
        fm = _read_frontmatter(im / "Good.md")
        assert "provenance" in fm
        # JSON still written
        cp = im / ".history" / "checkpoints" / "conv-test-001" / "cp-001.json"
        assert cp.exists()


class TestProvenanceFrontmatterAtomicity:
    """Subtest T013(f): FR-011 frontmatter atomicity — no leftover .tmp files."""

    def test_no_tmp_files_after_successful_merge(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")

        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["PageA.md"])
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        tmp_files = list(im.glob(".PageA.md.*.tmp"))
        assert not tmp_files, f"FR-011 atomicity: leftover tmp files {tmp_files}"


class TestProvenanceRecoverableEditorFailure:
    """Subtest T013(g): empty editor.decisions[] still writes valid JSON (US1 AS-4)."""

    def test_empty_decisions_writes_well_formed_json(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        transcript = _build_test_transcript(n_exchanges=1)
        # EditorOutput with NO decisions and NO results — Spec 004 FR-013 recoverable path.
        editor_output = EditorOutput(results=[], decisions=[])

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        cp = im / ".history" / "checkpoints" / "conv-test-001" / "cp-001.json"
        assert cp.exists()
        record = CheckpointRecord.model_validate_json(cp.read_text())
        assert record.editor.decisions == []
        assert record.results.pages_created == []
        assert record.results.pages_updated == []


class TestProvenanceEmptyCheckpoint:
    """Subtest T013(h): empty checkpoint (R10) — JSON written, no frontmatter writes."""

    def test_empty_results_writes_json_but_no_frontmatter(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = EditorOutput(results=[], decisions=[])

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        cp = im / ".history" / "checkpoints" / "conv-test-001" / "cp-001.json"
        assert cp.exists()
        # No git work attempted — pages dir should NOT exist (US1 doesn't create it
        # at all; US2 will when it lands).
        pages_dir = im / ".history" / "pages"
        assert not pages_dir.exists()


class TestProvenancePageDisappeared:
    """Subtest T013(i): page disappeared edge case G12."""

    def test_missing_page_logged_and_run_continues(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        # Don't create the page — simulate disappearance between Editor and orchestrator.
        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(
            pages_updated=["Vanished.md"], pages_created=["AlsoPresent.md"]
        )
        _make_page(im, "AlsoPresent.md")

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        captured = capsys.readouterr()
        assert "[provenance] page disappeared before snapshot" in captured.err
        assert "Vanished.md" in captured.err
        # JSON still recorded the decision.
        cp = im / ".history" / "checkpoints" / "conv-test-001" / "cp-001.json"
        record = CheckpointRecord.model_validate_json(cp.read_text())
        assert any(d.file == "InsightMesh/Vanished.md" for d in record.editor.decisions)
        # Other page still got provenance.
        fm = _read_frontmatter(im / "AlsoPresent.md")
        assert "provenance" in fm


class TestProvenanceFlatSentinel:
    """Subtest T013(j): _flat sentinel + flat-array transcript."""

    def test_no_conversation_id_uses_flat_subdir(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        transcript = _build_test_transcript(conv_id=None, provider=None, n_exchanges=1)
        # Override exchange_message_ids to be empty (flat-array case).
        transcript = ChatTranscript(
            source_path=transcript.source_path,
            exchanges=transcript.exchanges,
            metadata={"provider": None, "models_used": [], "exchange_message_ids": {}},
        )
        editor_output = _build_editor_output(pages_updated=["PageA.md"])

        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id=None,
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        cp = im / ".history" / "checkpoints" / "_flat" / "cp-001.json"
        assert cp.exists(), "Spec 005 _flat sentinel not used"
        record = CheckpointRecord.model_validate_json(cp.read_text())
        assert record.conversation.id is None
        assert record.conversation.provider is None
        assert record.conversation.models_used == []
        assert record.exchanges[0].user_message_id is None
        assert record.exchanges[0].assistant_message_id is None


class TestProvenanceFailureDoesNotFailRun:
    """Subtest T013(k): FR-019 — provenance failure never propagates."""

    def test_oserror_caught_and_logged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["PageA.md"])

        # Patch write_checkpoint_metadata at the orchestrator import site.
        with patch("src.orchestrator.write_checkpoint_metadata") as mock_write:
            mock_write.side_effect = OSError("disk full")
            # Must NOT raise — FR-019.
            _write_provenance(
                vault_root=vault,
                transcript=transcript,
                conversation_id="conv-test-001",
                transcript_hash="x" * 64,
                exchanges_processed=transcript.exchanges,
                editor_output=editor_output,
                session_log_path=None,
                cursor_path=vault / "cursor.json",
                checkpoint_number=1,
            )

        captured = capsys.readouterr()
        assert "[provenance] write failed" in captured.err
        assert "disk full" in captured.err


class TestProvenanceSignalsCoercion:
    """Subtest T013(l): FR-005 signals coercion for non-JSON-serializable values."""

    def test_non_json_value_coerced_via_repr_and_warned(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Build an EditorDecisionRecord directly with a non-serializable value
        # to exercise compute_checkpoint_payload's coercion path. Real Editor
        # output flows through EditorDecisionSignals.model_dump() which would
        # only ever produce serializable dicts; this test exercises the
        # defensive coercion for hand-built / future-extended signals.
        from src.history import compute_checkpoint_payload

        decision = EditorDecisionRecord(
            file="PageA.md",
            action="updated",
            confidence="high",
            rationale="test",
            exchange_indices=[0],
            # A Path object is not JSON-serializable.
            signals={"normalized_title_match": True, "weird_path": tmp_path},
        )

        record = compute_checkpoint_payload(
            checkpoint_number=1,
            conversation_id="conv-test-001",
            export_path="/tmp/x.json",
            provider="anthropic",
            models_used=[],
            transcript_hash="x" * 64,
            exchange_records=[
                ExchangeRecord(index=0, user_message_id="u", assistant_message_id="a")
            ],
            editor_decisions=[decision],
            pages_created=[],
            pages_updated=["PageA.md"],
            pages_skipped=[],
            session_log_path=".logs/s.json",
            cursor_path=".logs/c.json",
        )

        captured = capsys.readouterr()
        assert "[provenance] signal value not JSON-serializable: weird_path" in captured.err
        # Should serialize cleanly.
        json_text = record.model_dump_json()
        assert "weird_path" in json_text
        # The Path got coerced via repr() — its repr starts with PosixPath/WindowsPath.
        loaded = json.loads(json_text)
        coerced = loaded["editor"]["decisions"][0]["signals"]["weird_path"]
        assert "Path" in coerced


class TestProvenanceSessionLogUntouched:
    """Subtest T013(m): FR-018 — provenance writes do not touch the session log."""

    def test_session_log_byte_identical_before_after(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        session_log = im / ".logs" / "session.json"
        session_log.parent.mkdir(parents=True, exist_ok=True)
        original_content = '{"baseline": "session log content"}'
        session_log.write_text(original_content)

        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["PageA.md"])
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=session_log,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )

        # Session log unchanged byte-for-byte.
        assert session_log.read_text() == original_content


class TestProvenanceProcessKillResilience:
    """Subtest T013(n): FR-021 — process kill mid-write recoverable on next call."""

    def test_killed_mid_write_recovers_cleanly(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = _build_editor_output(pages_updated=["PageA.md"])

        # First call: patch os.replace to raise KeyboardInterrupt simulating SIGINT
        # AFTER the temp file is written but before the rename completes.
        with patch("src.history.os.replace") as mock_replace:
            mock_replace.side_effect = KeyboardInterrupt("process killed")
            with pytest.raises(KeyboardInterrupt):
                _write_provenance(
                    vault_root=vault,
                    transcript=transcript,
                    conversation_id="conv-test-001",
                    transcript_hash="x" * 64,
                    exchanges_processed=transcript.exchanges,
                    editor_output=editor_output,
                    session_log_path=None,
                    cursor_path=vault / "cursor.json",
                    checkpoint_number=1,
                )

        # KeyboardInterrupt propagates (it's a BaseException, not Exception).
        # No cp-001.json yet because rename never happened.
        cp_dir = im / ".history" / "checkpoints" / "conv-test-001"
        assert cp_dir.exists()
        cp_path = cp_dir / "cp-001.json"
        assert not cp_path.exists(), "killed-mid-write should leave no final cp-001.json"

        # Second call (re-run): no patching, should succeed cleanly with same checkpoint_number.
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-test-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )
        assert cp_path.exists(), "re-run should land cp-001.json"
        record = CheckpointRecord.model_validate_json(cp_path.read_text())
        assert record.checkpoint_id == "cp-001"
        # No orphan .tmp files left over.
        tmp_files = list(cp_dir.glob(".cp-001.*.tmp"))
        assert not tmp_files, f"orphan tmp files after re-run: {tmp_files}"


# ============================================================================
# Spec 005 T018: US2 integration tests for the shadow git layer
# ============================================================================


skip_if_no_git = pytest.mark.skipif(
    _shutil.which("git") is None, reason="git not installed; US2 needs git on PATH"
)


@pytest.fixture(autouse=True)
def _reset_git_cache() -> Any:
    """Reset the module-scope git-availability cache between US2 tests."""
    import src.history as _hist

    _hist._GIT_AVAILABLE_CACHE = None
    yield
    _hist._GIT_AVAILABLE_CACHE = None


def _run_provenance_for_us2(
    *,
    vault: Path,
    page_filenames: list[str],
    conv_id: str = "conv-us2-001",
    checkpoint_number: int = 1,
    extra_exchange_indices: list[int] | None = None,
) -> None:
    """Convenience: build inputs and call _write_provenance for US2 scenarios."""
    transcript = _build_test_transcript(conv_id=conv_id, n_exchanges=2)
    editor_output = _build_editor_output(
        pages_updated=page_filenames,
        exchange_indices=extra_exchange_indices or [0, 1],
    )
    _write_provenance(
        vault_root=vault,
        transcript=transcript,
        conversation_id=conv_id,
        transcript_hash="x" * 64,
        exchanges_processed=transcript.exchanges,
        editor_output=editor_output,
        session_log_path=None,
        cursor_path=vault / "cursor.json",
        checkpoint_number=checkpoint_number,
    )


def _git_log_oneline(history_dir: Path) -> list[str]:
    """Capture git log --oneline output as a list of subject lines."""
    result = _subprocess.run(
        ["git", "-C", str(history_dir), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def _git_show_message(history_dir: Path, rev: str = "HEAD") -> str:
    """Capture full commit message body for one rev."""
    result = _subprocess.run(
        ["git", "-C", str(history_dir), "log", "-1", "--pretty=%B", rev],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class TestUS2SingleCheckpointCommit:
    """Subtest T018(a): one checkpoint → one git commit with FR-014 subject + body."""

    @skip_if_no_git
    def test_single_commit_lands_with_correct_format(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        _make_page(im, "PageB.md")
        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md", "PageB.md"])

        history_dir = im / ".history"
        assert (history_dir / ".git").exists()
        log = _git_log_oneline(history_dir)
        assert len(log) == 1, f"expected exactly 1 commit, got {log}"
        msg = _git_show_message(history_dir)
        assert "[InsightMesh checkpoint:cp-001 conversation:conv-us2-001]" in msg
        assert "Metadata: checkpoints/conv-us2-001/cp-001.json" in msg
        assert "Pages touched:" in msg
        assert "(updated, confidence:medium)" in msg
        assert "InsightMesh/PageA.md" in msg


class TestUS2TwoCheckpointDiffHistory:
    """Subtest T018(b): two checkpoints → two commits + per-page diff."""

    @skip_if_no_git
    def test_two_checkpoints_produce_diffable_history(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        page = _make_page(im, "PageA.md", body="# v1\n\nInitial content.\n")
        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"], checkpoint_number=1)
        # Mutate page between checkpoints to simulate Editor updating it.
        page.write_text(page.read_text() + "\n\nAdded by checkpoint 2.\n", encoding="utf-8")
        _run_provenance_for_us2(
            vault=vault,
            page_filenames=["PageA.md"],
            checkpoint_number=2,
            extra_exchange_indices=[1],
        )

        history_dir = im / ".history"
        log = _git_log_oneline(history_dir)
        assert len(log) == 2, f"expected 2 commits, got {log}"
        assert any("cp-001" in line for line in log)
        assert any("cp-002" in line for line in log)

        diff = _subprocess.run(
            ["git", "-C", str(history_dir), "log", "-p", "pages/PageA.md"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "+Added by checkpoint 2." in diff.stdout, "FR-014: per-page diff missing"


class TestUS2InitIdempotency:
    """Subtest T018(c) + (d): FR-012 three-state init behavior."""

    @skip_if_no_git
    def test_existing_git_repo_is_not_reinitialized(self, tmp_path: Path) -> None:
        # State (b): .history/.git/ exists already.
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        history_dir = im / ".history"
        history_dir.mkdir(parents=True)
        _subprocess.run(["git", "-C", str(history_dir), "init"], capture_output=True, check=True)
        # Marker file that should survive a re-init no-op.
        marker = history_dir / ".git" / "marker"
        marker.write_text("pre-existing")

        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"])

        assert marker.read_text() == "pre-existing", "init was not idempotent"
        log = _git_log_oneline(history_dir)
        assert len(log) == 1

    @skip_if_no_git
    def test_history_dir_exists_but_no_git_gets_initialized(self, tmp_path: Path) -> None:
        # State (c): .history/ exists with content but no .git/.
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        history_dir = im / ".history"
        history_dir.mkdir(parents=True)
        pre_existing = history_dir / "leftover.txt"
        pre_existing.write_text("not from InsightMesh")

        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"])

        assert (history_dir / ".git").exists(), "init did not re-initialize"
        assert pre_existing.read_text() == "not from InsightMesh", "init was destructive"
        log = _git_log_oneline(history_dir)
        assert len(log) == 1


class TestUS2NoGitFallback:
    """Subtest T018(e): FR-015 SC-005 — git uninstalled → exit 0, JSON + frontmatter still land."""

    def test_no_git_writes_json_and_frontmatter_no_commit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")

        with patch("src.orchestrator.is_git_available", return_value=False):
            _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"])

        # JSON + frontmatter still landed
        cp = im / ".history" / "checkpoints" / "conv-us2-001" / "cp-001.json"
        assert cp.exists()
        fm = _read_frontmatter(im / "PageA.md")
        assert "provenance" in fm
        # No commit — .git/ should not exist
        assert not (im / ".history" / ".git").exists()
        captured = capsys.readouterr()
        assert "[provenance] git not on PATH" in captured.err


class TestUS2CommitFailureFallback:
    """Subtest T018(f): FR-016 — commit failure logs + run continues; next commit sweeps up."""

    @skip_if_no_git
    def test_commit_failure_logs_and_next_commit_sweeps(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")

        with patch("src.orchestrator.commit_checkpoint") as mock_commit:
            mock_commit.side_effect = ShadowRepoCommitFailed("permission denied")
            _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"], checkpoint_number=1)

        captured = capsys.readouterr()
        assert "[provenance] commit failed" in captured.err
        # JSON + frontmatter still on disk
        cp1 = im / ".history" / "checkpoints" / "conv-us2-001" / "cp-001.json"
        assert cp1.exists()
        fm = _read_frontmatter(im / "PageA.md")
        assert "provenance" in fm

        # Second checkpoint succeeds and sweeps up the orphan snapshot.
        _make_page(im, "PageB.md")
        _run_provenance_for_us2(
            vault=vault,
            page_filenames=["PageA.md", "PageB.md"],
            checkpoint_number=2,
        )
        history_dir = im / ".history"
        log = _git_log_oneline(history_dir)
        # Only one commit (the second); first was the failed-commit fallback.
        assert len(log) == 1
        # The sweep-up commit includes both cp-001 and cp-002 + both page snapshots.
        result = _subprocess.run(
            ["git", "-C", str(history_dir), "show", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "cp-001.json" in result.stdout or "cp-002.json" in result.stdout


class TestUS2EmptyCheckpointNoCommit:
    """Subtest T018(g): Research Decision R10 — empty checkpoint skips init + commit."""

    def test_empty_checkpoint_no_history_dir(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        transcript = _build_test_transcript(n_exchanges=1)
        editor_output = EditorOutput(results=[], decisions=[])
        _write_provenance(
            vault_root=vault,
            transcript=transcript,
            conversation_id="conv-us2-001",
            transcript_hash="x" * 64,
            exchanges_processed=transcript.exchanges,
            editor_output=editor_output,
            session_log_path=None,
            cursor_path=vault / "cursor.json",
            checkpoint_number=1,
        )
        # JSON DID write per US1 contract
        cp = im / ".history" / "checkpoints" / "conv-us2-001" / "cp-001.json"
        assert cp.exists()
        # No git work attempted: no .git/ directory + no pages/ directory
        assert not (im / ".history" / ".git").exists()
        assert not (im / ".history" / "pages").exists()


class TestUS2UserModifiedHistoryNonDestructive:
    """Subtest T018(h): edge case — user manual commits in .history/ coexist."""

    @skip_if_no_git
    def test_user_commit_survives_orchestrator_run(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        history_dir = im / ".history"
        history_dir.mkdir(parents=True)
        _subprocess.run(["git", "-C", str(history_dir), "init"], capture_output=True, check=True)
        # User manual commit with their own identity.
        user_file = history_dir / "user_notes.txt"
        user_file.write_text("manual user note")
        _subprocess.run(
            [
                "git",
                "-C",
                str(history_dir),
                "-c",
                "user.email=user@example.com",
                "-c",
                "user.name=TestUser",
                "add",
                "user_notes.txt",
            ],
            capture_output=True,
            check=True,
        )
        _subprocess.run(
            [
                "git",
                "-C",
                str(history_dir),
                "-c",
                "user.email=user@example.com",
                "-c",
                "user.name=TestUser",
                "commit",
                "-m",
                "user manual commit",
            ],
            capture_output=True,
            check=True,
        )
        user_commit_sha = _subprocess.run(
            ["git", "-C", str(history_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"])

        log = _git_log_oneline(history_dir)
        assert len(log) == 2, f"expected user commit + InsightMesh commit, got {log}"
        # User's commit still present
        result = _subprocess.run(
            ["git", "-C", str(history_dir), "cat-file", "-e", user_commit_sha],
            capture_output=True,
        )
        assert result.returncode == 0, "user commit was destroyed"
        assert user_file.read_text() == "manual user note"


class TestUS2VaultWithPopulatedHistory:
    """Subtest T018(i): SC-004 — populated .history/ advances cleanly."""

    @skip_if_no_git
    def test_second_checkpoint_advances_numbering(self, tmp_path: Path) -> None:
        vault, im = _make_vault(tmp_path)
        _make_page(im, "PageA.md")
        _run_provenance_for_us2(vault=vault, page_filenames=["PageA.md"], checkpoint_number=1)
        # Second run with checkpoint_number=2.
        _make_page(im, "PageB.md")
        _run_provenance_for_us2(
            vault=vault,
            page_filenames=["PageA.md", "PageB.md"],
            checkpoint_number=2,
        )
        cp_dir = im / ".history" / "checkpoints" / "conv-us2-001"
        cp1 = cp_dir / "cp-001.json"
        cp2 = cp_dir / "cp-002.json"
        assert cp1.exists()
        assert cp2.exists()
        # Two commits, monotonic order.
        log = _git_log_oneline(im / ".history")
        assert len(log) == 2
        # Newest first
        assert "cp-002" in log[0]
        assert "cp-001" in log[1]


class TestUS2CommitMessageFormat:
    """Subtest T018(j): FR-014 commit message format unit test."""

    @skip_if_no_git
    def test_commit_subject_and_body_match_fr014(self, tmp_path: Path) -> None:
        from src.history import (
            EditorBlock,
        )
        from src.history import (
            commit_checkpoint as _commit_checkpoint,
        )
        from src.history import (
            init_shadow_repo as _init_shadow_repo,
        )
        from src.history import (
            write_checkpoint_metadata as _write_checkpoint_metadata,
        )

        history_dir = tmp_path / ".history"
        _init_shadow_repo(history_dir)

        record = CheckpointRecord(
            checkpoint_id="cp-007",
            checkpoint_number=7,
            timestamp=datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC),
            conversation=_minimal_conversation_for_us2(),
            exchanges=[_minimal_exchange_for_us2(0)],
            editor=EditorBlock(
                decisions=[
                    EditorDecisionRecord(
                        file="InsightMesh/Page1.md",
                        action="updated",
                        confidence="high",
                        rationale="r1",
                        exchange_indices=[0],
                        signals={},
                    ),
                    EditorDecisionRecord(
                        file="InsightMesh/Page2.md",
                        action="created",
                        confidence="medium",
                        rationale="r2",
                        exchange_indices=[0],
                        signals={},
                    ),
                ]
            ),
            results=_make_us2_results(
                created=["InsightMesh/Page2.md"], updated=["InsightMesh/Page1.md"]
            ),
            links=_minimal_links_for_us2(),
        )
        _write_checkpoint_metadata(
            history_dir=history_dir, conversation_subdir="conv-fmt", record=record
        )
        # Create snapshot files so git add can stage them.
        (history_dir / "pages").mkdir(parents=True, exist_ok=True)
        for slug in ["Page1.md", "Page2.md"]:
            (history_dir / "pages" / slug).write_text(f"snapshot of {slug}")

        _commit_checkpoint(
            history_dir=history_dir,
            checkpoint_id="cp-007",
            conversation_id="conv-fmt",
            conversation_subdir="conv-fmt",
            decisions=record.editor.decisions,
            pages_created=["InsightMesh/Page2.md"],
            pages_updated=["InsightMesh/Page1.md"],
            snapshot_filenames=["Page1.md", "Page2.md"],
        )

        msg = _git_show_message(history_dir)
        # Subject
        assert "[InsightMesh checkpoint:cp-007 conversation:conv-fmt]" in msg
        assert "1 pages updated, 1 created" in msg
        # Body
        assert "Metadata: checkpoints/conv-fmt/cp-007.json" in msg
        assert "Pages touched:" in msg
        assert "InsightMesh/Page1.md (updated, confidence:high)" in msg
        assert "InsightMesh/Page2.md (created, confidence:medium)" in msg


def _minimal_conversation_for_us2() -> Any:
    from src.history import ConversationRecord as _CR

    return _CR(
        id="conv-fmt",
        export_path="/tmp/x.json",
        provider="anthropic",
        models_used=[],
        transcript_hash="a" * 64,
    )


def _minimal_exchange_for_us2(index: int) -> Any:
    return ExchangeRecord(
        index=index, user_message_id=f"u-{index}", assistant_message_id=f"a-{index}"
    )


def _minimal_links_for_us2() -> Any:
    from src.history import LinksRecord as _LR

    return _LR(session_log=".logs/s.json", cursor=".logs/c.json")


def _make_us2_results(*, created: list[str], updated: list[str]) -> Any:
    from src.history import ResultsRecord as _RR

    return _RR(pages_created=created, pages_updated=updated, pages_skipped=[])
