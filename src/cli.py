"""Command-line entry point for InsightMesh.

Commands:
    insightmesh list <export.json>
    insightmesh batch <transcript-or-export.json> --vault <path>
        [--logs <path>] [--conversation <id-or-index>]

Spec 001 established the `batch` command for flat `{role, content}` JSON arrays.
Spec 002 adds the `list` subcommand, the `--conversation` option for multi-conversation
exports (Claude.ai / ChatGPT via the `echomine` library), and a unified pre-flight
validation pass (vault + agent presence) per FR-019 / FR-022.

Pre-flight errors are always written to stderr only, never to `.logs/` (FR-019).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.checkpoint import (
    CheckpointHashMismatch,
    CheckpointIndexOutOfBounds,
    CheckpointMalformed,
    CheckpointMissing,
    CheckpointSchemaVersionMismatch,
)
from src.exports import (
    EmptyConversationError,
    UnrecognizedExportFormat,
    extract_conversation,
    list_conversations,
    render_list_table,
)
from src.orchestrator import (
    EXPECTED_AGENTS,
    CheckpointError_RequiresRetry,
    _cursor_path_for,
    run_batch,
)
from src.transcript import parse_transcript

app = typer.Typer(
    name="insightmesh",
    help=(
        "Multi-agent chat-to-wiki synthesis. "
        "Use 'list' to browse an export and 'batch' to synthesize."
    ),
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"insightmesh {version('insightmesh-core')}")
        raise typer.Exit()


@app.callback()
def _root_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the InsightMesh version and exit.",
        ),
    ] = False,
) -> None:
    """Force subcommand-style invocation (`insightmesh batch ...` / `insightmesh list ...`)."""


# ===========================================================================
# Pre-flight types (T010; data-model.md § PreflightDiagnostic / MalformedAgent /
# PreflightError / AgentDefinition)
# ===========================================================================


class AgentDefinition(BaseModel):
    """The minimal frontmatter shape the pre-flight check cares about."""

    model_config = ConfigDict(strict=True, extra="ignore")

    name: str = Field(min_length=1)


class MalformedAgent(BaseModel):
    """One agent file that exists but cannot be parsed enough to extract `name:`."""

    model_config = ConfigDict(strict=True)

    file_path: str
    reason: str


class PreflightDiagnostic(BaseModel):
    """Aggregated findings from one pre-flight pass (per FR-022)."""

    model_config = ConfigDict(strict=True)

    vault_errors: list[str] = Field(default_factory=list)
    missing_agents: list[str] = Field(default_factory=list)
    malformed_agents: list[MalformedAgent] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vault_errors or self.missing_agents or self.malformed_agents)


class PreflightError(Exception):
    """Raised when `_run_preflight` returns a non-empty diagnostic.

    Carries the structured diagnostic; `_format()` renders the FR-019 / FR-022
    aggregated stderr message. Caller (the batch handler) catches and exits 1.
    """

    def __init__(self, diagnostic: PreflightDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(self._format())

    def _format(self) -> str:
        lines: list[str] = ["error: pre-flight checks failed:", ""]
        if self.diagnostic.vault_errors:
            lines.append("  Vault:")
            for err in self.diagnostic.vault_errors:
                lines.append(f"    - {err}")
            lines.append("")
        if self.diagnostic.missing_agents:
            lines.append("  Missing agents (expected in .claude/agents/):")
            for name in self.diagnostic.missing_agents:
                lines.append(f"    - {name}")
            lines.append("")
        if self.diagnostic.malformed_agents:
            lines.append("  Malformed agent files:")
            for ma in self.diagnostic.malformed_agents:
                lines.append(f"    - {ma.file_path}: {ma.reason}")
            lines.append("")
        lines.append("Aborting before orchestrator invocation. Fix the issues above and re-run.")
        return "\n".join(lines)


# ===========================================================================
# Pre-flight helpers (T038, T039, T040, T041)
# ===========================================================================


def _validate_vault_to_errors(vault: Path) -> list[str]:
    """Refactored from Spec 001's `_validate_vault` to contribute to the unified
    pre-flight pass (T040). Returns list of error strings rather than calling Exit."""
    errors: list[str] = []
    if not vault.exists():
        errors.append(f"vault path does not exist: {vault}")
        return errors  # later checks would fail anyway
    if not vault.is_dir():
        errors.append(f"vault path is not a directory: {vault}")
        return errors
    if not os.access(vault, os.W_OK):
        errors.append(f"vault path is not writable: {vault}")
    return errors


def _parse_agent_frontmatter(path: Path) -> AgentDefinition | MalformedAgent:
    """Parse YAML frontmatter from a `.claude/agents/*.md` file (FR-017, T038).

    Returns AgentDefinition on success; MalformedAgent on missing `name:` or
    YAML parse error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return MalformedAgent(file_path=str(path), reason=f"unreadable: {exc}")

    # Frontmatter is delimited by --- on its own line.
    if not text.startswith("---"):
        return MalformedAgent(
            file_path=str(path), reason="missing YAML frontmatter (file does not start with '---')"
        )
    parts = text.split("---", 2)
    if len(parts) < 3:
        return MalformedAgent(
            file_path=str(path), reason="incomplete YAML frontmatter (no closing '---')"
        )
    yaml_block = parts[1]
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        return MalformedAgent(file_path=str(path), reason=f"YAML parse error: {exc}")

    if not isinstance(parsed, dict):
        return MalformedAgent(file_path=str(path), reason="YAML frontmatter is not a mapping")
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        return MalformedAgent(
            file_path=str(path), reason="missing or empty `name:` field in frontmatter"
        )
    return AgentDefinition(name=name.strip())


def _inspect_agents_directory(
    agents_dir: Path, expected: list[str]
) -> tuple[list[str], list[MalformedAgent]]:
    """Scan `.claude/agents/` and report (missing_expected, malformed) per FR-015 (T039).

    If the directory itself does not exist or is unreadable, returns
    `(expected[:], [])` — treats as all expected agents missing per the Edge Case.
    """
    if not agents_dir.exists() or not agents_dir.is_dir():
        return list(expected), []

    try:
        files = sorted(agents_dir.glob("*.md"))
    except OSError:
        return list(expected), []

    valid_names: set[str] = set()
    malformed: list[MalformedAgent] = []
    for f in files:
        result = _parse_agent_frontmatter(f)
        if isinstance(result, AgentDefinition):
            valid_names.add(result.name)
        else:
            malformed.append(result)

    missing = [n for n in expected if n not in valid_names]
    return missing, malformed


def _run_preflight(vault: Path, agents_dir: Path) -> PreflightDiagnostic:
    """Run all pre-flight checks and return aggregated diagnostic (FR-022, T041).

    Does not raise. Caller is responsible for raising PreflightError if
    `not diagnostic.is_empty()`.
    """
    missing, malformed = _inspect_agents_directory(agents_dir, EXPECTED_AGENTS)
    return PreflightDiagnostic(
        vault_errors=_validate_vault_to_errors(vault),
        missing_agents=missing,
        malformed_agents=malformed,
    )


# ===========================================================================
# Input-shape detection for `batch` (T032)
# ===========================================================================


def _looks_like_spec001_flat_array(path: Path) -> bool:
    """Return True if the file's JSON root is a list of {role, content} dicts.

    Used to route the batch command between Spec 001's existing transcript path
    (FR-014 backward compat) and Spec 002's multi-conversation export path.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, list) or not data:
        return False
    first = data[0]
    if not isinstance(first, dict):
        return False
    return "role" in first and "content" in first


# ===========================================================================
# `list` subcommand (T019, T020, T021)
# ===========================================================================


@app.command(name="list")
def list_cmd(
    export: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to a multi-conversation export from Claude.ai or ChatGPT.",
        ),
    ],
) -> None:
    """Browse conversations in a Claude.ai or ChatGPT export.

    Prints a Rich-rendered table (Index, Title, Created, Msgs) plus id-by-index
    footer. Pure read; does NOT touch the vault (FR-001).
    """
    export_resolved = export.expanduser().resolve()
    try:
        summaries = list_conversations(export_resolved)
    except UnrecognizedExportFormat as exc:
        typer.echo(f"error: export {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        # Translated echomine.ParseError or echomine.ValidationError per FR-027.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(render_list_table(summaries), nl=False)


# ===========================================================================
# `batch` command (Spec 001 + Spec 002 extensions: T031, T032, T033, T034, T043, T044)
# ===========================================================================


async def _run_pipeline(
    transcript_path: Path,
    vault: Path,
    logs_dir: Path,
    *,
    checkpoint_path: Path | None = None,
    max_exchanges: int | None = None,
    require_resume: bool = False,
    force_resume: bool = False,
    retry: bool = False,
) -> int:
    """Async pipeline driver for flat-array transcripts (Spec 001 path)."""
    try:
        transcript = parse_transcript(transcript_path)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        return 1
    except ValueError as exc:
        typer.echo(f"error: invalid transcript: {exc}", err=True)
        return 1

    typer.echo(f"Loaded {len(transcript.exchanges)} exchanges from {transcript_path}", err=True)
    typer.echo(f"Vault: {vault}", err=True)
    typer.echo(f"Logs:  {logs_dir}", err=True)
    if checkpoint_path is not None:
        typer.echo(f"Cursor: {checkpoint_path}", err=True)
    typer.echo("Running pipeline...", err=True)

    try:
        result = await run_batch(
            transcript=transcript,
            vault_path=vault,
            logs_dir=logs_dir,
            checkpoint_path=checkpoint_path,
            conversation_id=None,
            max_exchanges=max_exchanges,
            require_resume=require_resume,
            force_resume=force_resume,
            retry=retry,
        )
    except (
        CheckpointMissing,
        CheckpointHashMismatch,
        CheckpointIndexOutOfBounds,
        CheckpointMalformed,
        CheckpointSchemaVersionMismatch,
    ) as exc:
        typer.echo(f"error: {exc}", err=True)
        return 2
    except CheckpointError_RequiresRetry:
        # Diagnostic already printed to stderr by orchestrator.
        return 1
    except RuntimeError as exc:
        typer.echo(f"error: pipeline failed: {exc}", err=True)
        typer.echo(f"(session log written to {logs_dir})", err=True)
        return 1

    if result is None:
        # No-op (status=complete). Message already printed by orchestrator.
        return 0

    created = sum(1 for r in result.results if r.action == "created")
    updated = sum(1 for r in result.results if r.action == "updated")
    typer.echo(
        f"Pipeline complete: {created} created, {updated} updated, "
        f"{len(result.decisions)} editor decisions logged."
    )
    return 0


async def _run_pipeline_from_export(
    export_path: Path,
    selector: str,
    vault: Path,
    logs_dir: Path,
    *,
    checkpoint_path: Path | None = None,
    max_exchanges: int | None = None,
    require_resume: bool = False,
    force_resume: bool = False,
    retry: bool = False,
) -> int:
    """Async pipeline driver for multi-conversation exports (Spec 002 path)."""
    try:
        transcript = extract_conversation(export_path, selector)
    except UnrecognizedExportFormat as exc:
        typer.echo(f"error: export {exc}", err=True)
        return 1
    except EmptyConversationError as exc:
        typer.echo(f"error: conversation {exc}", err=True)
        return 1
    except KeyError as exc:
        typer.echo(f"error: conversation {exc.args[0]}", err=True)
        return 1
    except RuntimeError as exc:
        # Translated echomine.ParseError or echomine.ValidationError per FR-027.
        typer.echo(str(exc), err=True)
        return 1

    typer.echo(f"Loaded {len(transcript.exchanges)} exchanges from {export_path}", err=True)
    typer.echo(f"Vault: {vault}", err=True)
    typer.echo(f"Logs:  {logs_dir}", err=True)
    if checkpoint_path is not None:
        typer.echo(f"Cursor: {checkpoint_path}", err=True)
    typer.echo("Running pipeline...", err=True)

    try:
        result = await run_batch(
            transcript=transcript,
            vault_path=vault,
            logs_dir=logs_dir,
            checkpoint_path=checkpoint_path,
            conversation_id=selector,
            max_exchanges=max_exchanges,
            require_resume=require_resume,
            force_resume=force_resume,
            retry=retry,
        )
    except (
        CheckpointMissing,
        CheckpointHashMismatch,
        CheckpointIndexOutOfBounds,
        CheckpointMalformed,
        CheckpointSchemaVersionMismatch,
    ) as exc:
        typer.echo(f"error: {exc}", err=True)
        return 2
    except CheckpointError_RequiresRetry:
        return 1
    except RuntimeError as exc:
        typer.echo(f"error: pipeline failed: {exc}", err=True)
        typer.echo(f"(session log written to {logs_dir})", err=True)
        return 1

    if result is None:
        return 0

    created = sum(1 for r in result.results if r.action == "created")
    updated = sum(1 for r in result.results if r.action == "updated")
    typer.echo(
        f"Pipeline complete: {created} created, {updated} updated, "
        f"{len(result.decisions)} editor decisions logged."
    )
    return 0


@app.command()
def batch(
    transcript: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to either a Spec 001 flat-array transcript or a Claude.ai/ChatGPT export.",
        ),
    ],
    vault: Annotated[
        Path,
        typer.Option("--vault", help="Path to the Obsidian vault root directory."),
    ],
    logs: Annotated[
        Path | None,
        typer.Option(
            "--logs",
            help="Directory for session JSON logs (default: <vault>/InsightMesh/.logs).",
        ),
    ] = None,
    conversation: Annotated[
        str | None,
        typer.Option(
            "--conversation",
            help=(
                "Required when input is a multi-conversation export from Claude.ai or ChatGPT. "
                "Accepts either the conversation id (string) or its zero-indexed position from "
                "`insightmesh list`. Forbidden when input is a Spec 001 flat-array transcript. "
                "Run `insightmesh list <export.json>` to discover ids."
            ),
        ),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume",
            help=(
                "Explicit-intent flag for resuming a prior checkpoint. Errors if no "
                "cursor exists for this conversation. Without this flag, the orchestrator "
                "still auto-resumes from any cursor it finds (Spec 004 FR-003 / FR-010)."
            ),
        ),
    ] = False,
    max_exchanges: Annotated[
        int | None,
        typer.Option(
            "--max-exchanges",
            help=(
                "Soft cap on exchanges processed this invocation (Spec 004 FR-009). "
                "Cap is checked between checkpoints; the cursor may advance past N by "
                "up to one checkpoint's worth of exchanges. Must be > 0."
            ),
        ),
    ] = None,
    force_resume: Annotated[
        bool,
        typer.Option(
            "--force-resume",
            help=(
                "Override for transcript-hash mismatch (Spec 004 FR-006). Use only when "
                "you know the transcript changed and you accept that prior cursor indices "
                "may now point at different exchanges."
            ),
        ),
    ] = False,
    retry: Annotated[
        bool,
        typer.Option(
            "--retry",
            help=(
                "Required to resume past a cursor with status=failed (Spec 004 FR-014). "
                "Acknowledges the prior failure and runs a fresh checkpoint attempt from "
                "the cursor position."
            ),
        ),
    ] = False,
) -> None:
    """Process a chat transcript or one conversation from an export into Obsidian wiki pages."""
    # FR-008: reject non-positive caps before any pre-flight or agent work.
    if max_exchanges is not None and max_exchanges <= 0:
        typer.echo(
            f"error: --max-exchanges must be > 0 (got {max_exchanges})",
            err=True,
        )
        raise typer.Exit(code=2)

    vault_resolved = vault.expanduser().resolve()

    # Pre-flight pass: aggregate vault + agent checks before any export parsing
    # or orchestrator invocation (FR-019, FR-022).
    agents_dir = Path.cwd() / ".claude" / "agents"
    diagnostic = _run_preflight(vault_resolved, agents_dir)
    if not diagnostic.is_empty():
        err = PreflightError(diagnostic)
        print(str(err), file=sys.stderr)
        raise typer.Exit(code=1)

    logs_dir = (logs or (vault_resolved / "InsightMesh" / ".logs")).expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)

    # MCPVault's .mcp.json config reads ${VAULT_PATH}; set before query() runs.
    os.environ["VAULT_PATH"] = str(vault_resolved)

    transcript_resolved = transcript.expanduser().resolve()

    # Route to Spec 001 path (flat array) or Spec 002 path (multi-conversation export)
    # per FR-014 backward compat.
    is_flat = _looks_like_spec001_flat_array(transcript_resolved)

    if is_flat and conversation is not None:
        typer.echo(
            "error: --conversation cannot be used with a flat {role, content} transcript. "
            "Drop the flag, or pass a multi-conversation export.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not is_flat and conversation is None:
        typer.echo(
            f"error: {transcript_resolved} is a multi-conversation export. "
            f"Run 'insightmesh list {transcript_resolved}' to see available conversations, "
            f"then re-run with --conversation <id-or-index>.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Spec 004 FR-005: derive the per-conversation cursor path under logs/.
    checkpoint_path = _cursor_path_for(logs_dir, transcript_resolved, conversation)

    if is_flat:
        exit_code = asyncio.run(
            _run_pipeline(
                transcript_resolved,
                vault_resolved,
                logs_dir,
                checkpoint_path=checkpoint_path,
                max_exchanges=max_exchanges,
                require_resume=resume,
                force_resume=force_resume,
                retry=retry,
            )
        )
    else:
        assert conversation is not None  # narrowed by above checks
        exit_code = asyncio.run(
            _run_pipeline_from_export(
                transcript_resolved,
                conversation,
                vault_resolved,
                logs_dir,
                checkpoint_path=checkpoint_path,
                max_exchanges=max_exchanges,
                require_resume=resume,
                force_resume=force_resume,
                retry=retry,
            )
        )

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def main() -> None:
    """Entry point exposed via pyproject.toml `[project.scripts]`."""
    app()


if __name__ == "__main__":
    main()
