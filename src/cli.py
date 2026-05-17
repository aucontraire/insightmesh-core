"""Command-line entry point for InsightMesh batch synthesis.

Usage:
    insightmesh batch <transcript.json> --vault <path> [--logs <path>]

CLI parsing is driven by Typer (type hints become arguments/options). The
batch command:
1. Validates the vault path exists and is writable (FR-011)
2. Sets the `VAULT_PATH` env var so `.mcp.json`'s MCPVault config can use it
3. Loads the transcript via `parse_transcript` (FR-001, FR-012)
4. Runs the orchestrator pipeline asynchronously
5. Prints a one-line summary

Exit codes:
    0 - success
    1 - validation or runtime error
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer

from src.orchestrator import run_batch
from src.transcript import parse_transcript

app = typer.Typer(
    name="insightmesh",
    help="Run the InsightMesh chat-to-wiki batch synthesis pipeline.",
    no_args_is_help=True,
)


@app.callback()
def _root_callback() -> None:
    """Force subcommand-style invocation (`insightmesh batch ...`).

    Without this callback, Typer collapses single-command apps so the command
    name is omitted. We want stable `insightmesh <command> ...` syntax now
    so future commands (Spec 002 live inquiry, etc.) don't break the interface.
    """


def _validate_vault(vault: Path) -> None:
    """FR-011: vault must exist and be writable before processing."""
    if not vault.exists():
        typer.echo(f"error: vault path does not exist: {vault}", err=True)
        raise typer.Exit(code=1)
    if not vault.is_dir():
        typer.echo(f"error: vault path is not a directory: {vault}", err=True)
        raise typer.Exit(code=1)
    if not os.access(vault, os.W_OK):
        typer.echo(f"error: vault path is not writable: {vault}", err=True)
        raise typer.Exit(code=1)


async def _run_pipeline(transcript_path: Path, vault: Path, logs_dir: Path) -> int:
    """Async pipeline driver. Returns process exit code."""
    try:
        transcript = parse_transcript(transcript_path)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        return 1
    except ValueError as exc:
        typer.echo(f"error: invalid transcript: {exc}", err=True)
        return 1

    typer.echo(
        f"Loaded {len(transcript.exchanges)} exchanges from {transcript_path}",
        err=True,
    )
    typer.echo(f"Vault: {vault}", err=True)
    typer.echo(f"Logs:  {logs_dir}", err=True)
    typer.echo("Running pipeline...", err=True)

    try:
        result = await run_batch(
            transcript=transcript, vault_path=vault, logs_dir=logs_dir
        )
    except RuntimeError as exc:
        typer.echo(f"error: pipeline failed: {exc}", err=True)
        typer.echo(f"(session log written to {logs_dir})", err=True)
        return 1

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
            help="Path to a JSON chat transcript (ChatGPT/Claude export format).",
        ),
    ],
    vault: Annotated[
        Path,
        typer.Option(
            "--vault",
            help="Path to the Obsidian vault root directory.",
        ),
    ],
    logs: Annotated[
        Path | None,
        typer.Option(
            "--logs",
            help="Directory for session JSON logs (default: <vault>/InsightMesh/.logs).",
        ),
    ] = None,
) -> None:
    """Process a chat transcript into Obsidian wiki pages."""
    vault_resolved = vault.expanduser().resolve()
    _validate_vault(vault_resolved)

    logs_dir = (logs or (vault_resolved / "InsightMesh" / ".logs")).expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)

    # MCPVault's .mcp.json config reads ${VAULT_PATH}; set before query() runs.
    os.environ["VAULT_PATH"] = str(vault_resolved)

    transcript_resolved = transcript.expanduser().resolve()
    exit_code = asyncio.run(_run_pipeline(transcript_resolved, vault_resolved, logs_dir))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def main() -> None:
    """Entry point exposed via pyproject.toml `[project.scripts]`."""
    app()


if __name__ == "__main__":
    main()
