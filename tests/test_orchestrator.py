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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from src.checkpoint import (
    Checkpoint,
    CheckpointMalformed,
    DigestEntry,
    save_checkpoint,
)
from src.cli import app as cli_app
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
