"""Tests for src.cli (Spec 002 US1, US2, US3 CLI surface).

Covers:
- `insightmesh list` (FR-001, FR-006, FR-007, FR-008, US1 acceptance scenarios)
- `insightmesh batch --conversation` (FR-009, FR-012, FR-013, FR-014, US2 ASs)
- Pre-flight aggregation (FR-015 to FR-022, FR-027, US3 ASs)

Pipeline-level batch tests that would hit the live orchestrator are mocked at
the `src.cli.run_batch` boundary so these tests stay fast and offline.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from src.cli import (
    AgentDefinition,
    MalformedAgent,
    PreflightDiagnostic,
    PreflightError,
    _inspect_agents_directory,
    _parse_agent_frontmatter,
    _run_preflight,
    app,
)

FIXTURES: Path = Path(__file__).parent / "fixtures"
CLAUDE_AI = FIXTURES / "claude_ai_export.json"
CHATGPT = FIXTURES / "chatgpt_export.json"
FLAT_ARRAY = FIXTURES / "single_topic.json"  # Spec 001 fixture for backward compat


runner = CliRunner()


# ===========================================================================
# US1: list subcommand
# ===========================================================================


class TestListCommand:
    def test_insightmesh_list_renders_table_for_claude_ai(self) -> None:
        result = runner.invoke(app, ["list", str(CLAUDE_AI)])
        assert result.exit_code == 0
        assert "Speed of light" in result.stdout
        assert "Refining the EM spectrum chat" in result.stdout
        assert "Camera aperture" in result.stdout

    def test_insightmesh_list_renders_table_for_chatgpt(self) -> None:
        result = runner.invoke(app, ["list", str(CHATGPT)])
        assert result.exit_code == 0
        assert "Async Python patterns" in result.stdout

    def test_insightmesh_list_unrecognized_format_exits_one(self) -> None:
        result = runner.invoke(app, ["list", str(FLAT_ARRAY)])
        assert result.exit_code == 1
        assert "not a recognized export format" in result.stderr

    def test_insightmesh_list_does_not_accept_vault_flag(self) -> None:
        # Typer should reject --vault on the list command (not declared).
        result = runner.invoke(app, ["list", str(CLAUDE_AI), "--vault", "/tmp"])
        assert result.exit_code != 0


class TestListCommandEmptyExport:
    def test_insightmesh_list_zero_conversations_treated_as_unrecognized(
        self, tmp_path: Path
    ) -> None:
        # An empty array can't be identified as either schema; current behavior
        # surfaces it as "not a recognized export format". This matches the
        # contracts table — empty arrays of unknown provenance are rejected.
        empty = tmp_path / "empty.json"
        empty.write_text("[]")
        result = runner.invoke(app, ["list", str(empty)])
        assert result.exit_code == 1


# ===========================================================================
# US2: batch --conversation
# ===========================================================================


class TestBatchWithExport:
    def test_batch_with_export_without_conversation_flag_errors_with_list_suggestion(
        self, tmp_path: Path
    ) -> None:
        # Create a writable vault and minimal .claude/agents/ so pre-flight passes.
        vault = tmp_path / "vault"
        vault.mkdir()
        _setup_agents_dir(tmp_path)
        with _chdir(tmp_path):
            result = runner.invoke(app, ["batch", str(CLAUDE_AI), "--vault", str(vault)])
        assert result.exit_code == 1
        assert "multi-conversation export" in result.stderr
        assert "insightmesh list" in result.stderr

    def test_batch_with_flat_array_and_conversation_flag_errors(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        _setup_agents_dir(tmp_path)
        with _chdir(tmp_path):
            result = runner.invoke(
                app,
                ["batch", str(FLAT_ARRAY), "--vault", str(vault), "--conversation", "0"],
            )
        assert result.exit_code == 1
        assert "--conversation cannot be used with a flat" in result.stderr

    def test_batch_with_invalid_conversation_value_errors(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        _setup_agents_dir(tmp_path)
        with _chdir(tmp_path), patch("src.cli.run_batch") as mock_run:
            mock_run.side_effect = AssertionError("should not invoke orchestrator")
            result = runner.invoke(
                app,
                [
                    "batch",
                    str(CLAUDE_AI),
                    "--vault",
                    str(vault),
                    "--conversation",
                    "does-not-exist",
                ],
            )
        assert result.exit_code == 1
        assert "no conversation matches" in result.stderr


class TestBatchBackwardCompat:
    def test_batch_with_flat_array_preserves_spec001_path(self, tmp_path: Path) -> None:
        """FR-014: existing Spec 001 invocation works unchanged.

        We mock run_batch to verify the flat-array path is taken (no exception
        before invocation) without actually hitting the orchestrator.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        _setup_agents_dir(tmp_path)

        async def _fake_run_batch(**kwargs: object) -> object:  # type: ignore[no-untyped-def]
            return _FakeResult()

        with _chdir(tmp_path), patch("src.cli.run_batch", side_effect=_fake_run_batch) as mock_run:
            result = runner.invoke(app, ["batch", str(FLAT_ARRAY), "--vault", str(vault)])
        # Either the mock got called (preserved path) OR the runner errored cleanly with
        # a recognizable Spec 001 path message.
        assert mock_run.called, f"expected Spec 001 path to invoke run_batch; got: {result.output}"


class _FakeResult:
    """Stand-in for BatchResult to satisfy cli.py's success-path printing."""

    @property
    def results(self) -> list[object]:
        return []

    @property
    def decisions(self) -> list[object]:
        return []


# ===========================================================================
# US3: pre-flight agent presence check
# ===========================================================================


class TestParseAgentFrontmatter:
    def test_valid_frontmatter_returns_agent_definition(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("---\nname: synthesis\ndescription: foo\n---\nPrompt body.\n")
        result = _parse_agent_frontmatter(f)
        assert isinstance(result, AgentDefinition)
        assert result.name == "synthesis"

    def test_missing_name_returns_malformed(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("---\ndescription: no name\n---\nbody\n")
        result = _parse_agent_frontmatter(f)
        assert isinstance(result, MalformedAgent)
        assert "name" in result.reason.lower()

    def test_invalid_yaml_returns_malformed(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("---\nname: [unclosed list\n---\nbody\n")
        result = _parse_agent_frontmatter(f)
        assert isinstance(result, MalformedAgent)
        assert "yaml" in result.reason.lower() or "parse" in result.reason.lower()

    def test_no_frontmatter_returns_malformed(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("just markdown body, no frontmatter.\n")
        result = _parse_agent_frontmatter(f)
        assert isinstance(result, MalformedAgent)


class TestInspectAgentsDirectory:
    def test_all_expected_present_returns_no_missing(self, tmp_path: Path) -> None:
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("synthesis", "historian", "editor"):
            (agents / f"{name}.md").write_text(f"---\nname: {name}\n---\nPrompt.\n")
        missing, malformed = _inspect_agents_directory(agents, ["synthesis", "historian", "editor"])
        assert missing == []
        assert malformed == []

    def test_one_missing_reports_that_one(self, tmp_path: Path) -> None:
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("synthesis", "editor"):
            (agents / f"{name}.md").write_text(f"---\nname: {name}\n---\n")
        missing, malformed = _inspect_agents_directory(agents, ["synthesis", "historian", "editor"])
        assert missing == ["historian"]
        assert malformed == []

    def test_multiple_missing_reports_all(self, tmp_path: Path) -> None:
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "synthesis.md").write_text("---\nname: synthesis\n---\n")
        missing, malformed = _inspect_agents_directory(agents, ["synthesis", "historian", "editor"])
        assert sorted(missing) == ["editor", "historian"]

    def test_dir_missing_treats_as_all_missing(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no-such-dir"
        missing, malformed = _inspect_agents_directory(
            nonexistent, ["synthesis", "historian", "editor"]
        )
        assert sorted(missing) == ["editor", "historian", "synthesis"]

    def test_extra_unknown_agent_does_not_fail(self, tmp_path: Path) -> None:
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("synthesis", "historian", "editor", "experimental_extra"):
            (agents / f"{name}.md").write_text(f"---\nname: {name}\n---\n")
        missing, malformed = _inspect_agents_directory(agents, ["synthesis", "historian", "editor"])
        assert missing == []
        assert malformed == []


class TestRunPreflight:
    def test_all_clear_returns_empty_diagnostic(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("synthesis", "historian", "editor"):
            (agents / f"{name}.md").write_text(f"---\nname: {name}\n---\n")
        diag = _run_preflight(vault, agents)
        assert diag.is_empty()

    def test_vault_and_agents_both_fail_aggregates(self, tmp_path: Path) -> None:
        bad_vault = tmp_path / "no-such-vault"
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "synthesis.md").write_text("---\nname: synthesis\n---\n")
        # historian + editor missing
        diag = _run_preflight(bad_vault, agents)
        assert not diag.is_empty()
        assert diag.vault_errors
        assert sorted(diag.missing_agents) == ["editor", "historian"]

    def test_preflight_error_format_uses_FR019_prefix(self, tmp_path: Path) -> None:
        diag = PreflightDiagnostic(missing_agents=["synthesis"])
        err = PreflightError(diag)
        msg = str(err)
        assert msg.startswith("error: pre-flight checks failed:")
        assert "synthesis" in msg

    def test_preflight_error_omits_empty_sections(self) -> None:
        diag = PreflightDiagnostic(missing_agents=["historian"])
        err = PreflightError(diag)
        msg = str(err)
        assert "Vault:" not in msg
        assert "Malformed agent files:" not in msg
        assert "Missing agents" in msg


class TestPreflightIntegratedWithBatch:
    def test_batch_with_missing_agent_aborts_before_orchestrator(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        # Only synthesis + editor in agents dir; historian missing.
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "synthesis.md").write_text("---\nname: synthesis\n---\n")
        (agents / "editor.md").write_text("---\nname: editor\n---\n")
        with _chdir(tmp_path), patch("src.cli.run_batch") as mock_run:
            mock_run.side_effect = AssertionError("orchestrator must not be invoked")
            result = runner.invoke(app, ["batch", str(FLAT_ARRAY), "--vault", str(vault)])
        assert result.exit_code == 1
        assert "pre-flight checks failed" in result.stderr
        assert "historian" in result.stderr
        # mock should not have been called (assertion would have surfaced).
        assert not mock_run.called

    def test_batch_with_bad_vault_and_missing_agents_aggregates(self, tmp_path: Path) -> None:
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "synthesis.md").write_text("---\nname: synthesis\n---\n")
        with _chdir(tmp_path):
            result = runner.invoke(
                app,
                ["batch", str(FLAT_ARRAY), "--vault", "/nonexistent/vault/path"],
            )
        assert result.exit_code == 1
        out = result.stderr
        assert "pre-flight checks failed" in out
        assert "Vault:" in out
        assert "Missing agents" in out
        # Both editor and historian should appear (FR-022 aggregation).
        assert "editor" in out
        assert "historian" in out

    def test_batch_preflight_does_not_write_to_logs(self, tmp_path: Path) -> None:
        """FR-019: pre-flight failures must not touch the .logs/ directory."""
        vault = tmp_path / "vault"
        vault.mkdir()
        logs_dir = vault / "InsightMesh" / ".logs"
        # No agents directory at all.
        with _chdir(tmp_path):
            runner.invoke(app, ["batch", str(FLAT_ARRAY), "--vault", str(vault)])
        # Logs dir should NOT have been created by the pre-flight failure path
        # (Spec 001's success path creates it; pre-flight should not).
        assert (
            not logs_dir.exists()
        ), f"pre-flight failure should not have created {logs_dir} per FR-019"


# ===========================================================================
# Helpers
# ===========================================================================


def _setup_agents_dir(parent: Path) -> Path:
    """Create a complete .claude/agents/ under `parent` so pre-flight passes."""
    agents = parent / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in ("synthesis", "historian", "editor"):
        (agents / f"{name}.md").write_text(f"---\nname: {name}\n---\nPrompt body.\n")
    return agents


class _chdir:
    """Context manager: temporarily change cwd. Python 3.11 contextlib.chdir is
    available but we keep a tiny shim for clarity."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.old: str | None = None

    def __enter__(self) -> None:
        self.old = os.getcwd()
        os.chdir(self.path)

    def __exit__(self, *args: object) -> None:
        if self.old is not None:
            os.chdir(self.old)
