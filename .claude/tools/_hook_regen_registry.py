#!/usr/bin/env python3
"""
PostToolUse hook wrapper — fires after Edit/Write/MultiEdit, decides whether to
regenerate .claude/class-registry.json.

Wired in via `.claude/settings.json` hooks.PostToolUse entry. Claude Code pipes
the tool-call JSON to stdin; we parse it, check whether the edited file is a
Python file under `src/`, and apply a 15-second mtime debounce so back-to-back
edits don't thrash on regeneration.

Why a wrapper script instead of an inline bash command in settings.json:
  - Robust to Claude Code hook-schema drift (we only depend on the
    stdin-JSON contract, which is the documented baseline).
  - Testable: `echo '{...}' | python .claude/tools/_hook_regen_registry.py`.
  - Filter logic + debounce live in Python instead of escaped JSON.

Exit codes:
  0 — no-op (file not in scope, or debounce active) OR regeneration succeeded
  0 — regeneration failed silently (we never propagate errors to the hook, so a
       broken registry tool never blocks the user's edit; failures surface via
       the pre-commit gate)
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

# Resolve project root relative to this script (.claude/tools/_hook_regen_registry.py).
ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY = ROOT / ".claude" / "class-registry.json"
GENERATOR = ROOT / ".claude" / "tools" / "generate_class_registry.py"
SCOPE_PREFIX = "src/"
DEBOUNCE_SECONDS = 15


def _file_path_from_stdin() -> str | None:
    """Read the Claude Code PostToolUse JSON and pull out the edited file path.

    Tolerant of schema variants — handles file_path, file_paths (list), and
    falls back to None if neither is present. Never raises; a bad JSON payload
    just means "don't regenerate".
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(tool_input, dict):
        fp = tool_input.get("file_path")
        if isinstance(fp, str):
            return fp
        fps = tool_input.get("file_paths")
        if isinstance(fps, list) and fps and isinstance(fps[0], str):
            return fps[0]
    return None


def _in_scope(file_path: str) -> bool:
    """Trigger only on Python files inside src/."""
    if not file_path.endswith(".py"):
        return False
    try:
        abs_path = Path(file_path).resolve()
        rel = abs_path.relative_to(ROOT)
    except (ValueError, OSError):
        return False
    return str(rel).startswith(SCOPE_PREFIX)


def _debounce_active() -> bool:
    """True if the registry was regenerated less than DEBOUNCE_SECONDS ago."""
    if not REGISTRY.exists():
        return False
    age = time.time() - REGISTRY.stat().st_mtime
    return age < DEBOUNCE_SECONDS


def main() -> int:
    file_path = _file_path_from_stdin()
    if not file_path or not _in_scope(file_path):
        return 0
    if _debounce_active():
        return 0
    with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
        subprocess.run(
            ["uv", "run", "python", str(GENERATOR)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
