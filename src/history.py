"""Per-page provenance: structured checkpoint JSON + frontmatter merge + shadow git repo (Spec 005).

After each successful checkpoint (Spec 004 loop), the orchestrator writes a
permanent record of what happened in two complementary forms:

  1. `<vault>/InsightMesh/.history/checkpoints/<conv-id-or-_flat>/cp-<NNN>.json`
     A structured Pydantic-validated JSON file holding the conversation block,
     per-exchange message identifiers, per-page Editor decisions (with rationale,
     confidence, contributing exchange indices, and the full signals dict), the
     results summary, and convenience links to the session log + cursor.

  2. A cumulative `provenance:` block in each touched wiki page's YAML
     frontmatter, summarizing latest checkpoint + conversations + action +
     confidence + total_edits + exchange_count.

US2 layers a shadow git repository on top: page snapshots at
`<vault>/InsightMesh/.history/pages/<slug>.md` + one machine-greppable commit
per checkpoint, providing `git log -p` diff history.

All bookkeeping is orchestrator-side; the Editor agent's contract is unchanged.
Provenance failures NEVER fail the run (FR-019).

This module exposes:
  - Pydantic v2 models for the on-disk shapes (write-side strict, read-side permissive)
  - Custom exceptions for the documented failure paths
  - Pure helpers: compute_checkpoint_payload, write_checkpoint_metadata, merge_page_provenance,
    snapshot_page, init_shadow_repo, commit_checkpoint, is_git_available
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal[1] = 1
"""The current write-side schema version. Bumping requires explicit migration
tooling per FR-002; reserved for incompatible changes. Within v1, schema
evolution is additive: optional fields may be added without bumping the version
and the read-side (`CheckpointRecordRead`) tolerates unknown extras."""


# ---------------------------------------------------------------------------
# Internal scaffolding: per-exchange message-identifier ferry
# ---------------------------------------------------------------------------


class ExchangeMessageIds(BaseModel):
    """Per-exchange identifier pair, stored in `ChatTranscript.metadata` by exports.py.

    The orchestrator looks up entries by exchange index at provenance-write time
    to populate `ExchangeRecord.user_message_id` and `.assistant_message_id`.
    Either field is `None` when the source transcript does not carry per-message
    identifiers (Spec 001 flat-array shape).

    Internal scaffolding; not serialized to disk on its own.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    user_message_id: str | None = None
    assistant_message_id: str | None = None


# ---------------------------------------------------------------------------
# Write-side models — strict, extra="forbid"
# ---------------------------------------------------------------------------


class ConversationRecord(BaseModel):
    """Source-conversation block inside a `CheckpointRecord`.

    `provider` is tagged at `src/exports.py:detect_adapter` time per Research
    Decision R3 and threaded into `ChatTranscript.metadata`. `models_used`
    comes from `Conversation.models_used` (echomine 1.5.0); empty list when
    the upstream parser does not surface model identifiers (Claude exports
    today; Spec 001 flat-array always). `transcript_hash` is sourced from the
    Spec 004 cursor so the two artifacts agree by construction.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str | None
    export_path: str
    provider: Literal["anthropic", "openai"] | None
    models_used: list[str] = Field(default_factory=list)
    transcript_hash: str


class ExchangeRecord(BaseModel):
    """One entry per exchange processed in this checkpoint."""

    model_config = ConfigDict(strict=True, extra="forbid")

    index: int = Field(ge=0)
    user_message_id: str | None
    assistant_message_id: str | None


class EditorDecisionRecord(BaseModel):
    """One entry per page Editor touched in this checkpoint.

    `signals` is intentionally `dict[str, Any]` here even though the Editor's
    upstream `EditorDecisionSignals` is a typed model: this preserves
    forward-compatibility when the Editor's signals dict gains new fields
    without forcing a coordinated bump of this schema. The dict values MUST be
    JSON-serializable per FR-005; non-serializable values are coerced to
    `repr()` at payload-build time (see `compute_checkpoint_payload`).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    file: str
    action: Literal["created", "updated", "skipped"]
    confidence: Literal["high", "medium", "low"]
    rationale: str
    exchange_indices: list[int] = Field(default_factory=list)
    # signals is opaque pass-through; values are coerced for JSON-serializability at payload build.
    signals: dict[str, Any] = Field(default_factory=dict)


class ResultsRecord(BaseModel):
    """Mirrors the Spec 004 `EditorOutput.results` categories.

    Each list contains vault-relative page filenames, sorted ascending per
    FR-001b deterministic ordering.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    pages_created: list[str] = Field(default_factory=list)
    pages_updated: list[str] = Field(default_factory=list)
    pages_skipped: list[str] = Field(default_factory=list)


class LinksRecord(BaseModel):
    """Convenience pointers to sibling artifacts.

    Vault-relative POSIX paths. Reads MUST NOT depend on these targets
    existing per FR-007; they are advisory only.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    session_log: str
    cursor: str


class EditorBlock(BaseModel):
    """Wrapper around the editor decisions array.

    The extra level of nesting under `editor` reserves room for future fields
    (e.g., `editor.errors`, `editor.summary`) without a schema bump.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    decisions: list[EditorDecisionRecord] = Field(default_factory=list)


class CheckpointRecord(BaseModel):
    """Top-level write-side model for `cp-<NNN>.json`.

    Permanent, append-only system of record for provenance per Spec 005.
    Self-sufficient: provenance queries MUST NOT need to traverse the
    `links.*` pointers to be answered.

    The `checkpoint_id` field is derived from `checkpoint_number` per FR-001;
    a `model_validator` enforces the binding so the two fields cannot drift.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    checkpoint_id: str
    checkpoint_number: int = Field(ge=1)
    timestamp: datetime
    conversation: ConversationRecord
    exchanges: list[ExchangeRecord] = Field(min_length=1)
    editor: EditorBlock
    results: ResultsRecord
    links: LinksRecord

    @model_validator(mode="after")
    def _checkpoint_id_matches_number(self) -> CheckpointRecord:
        expected = f"cp-{self.checkpoint_number:03d}"
        if self.checkpoint_id != expected:
            raise ValueError(
                f"checkpoint_id must equal f'cp-{{checkpoint_number:03d}}'; "
                f"got checkpoint_id={self.checkpoint_id!r}, "
                f"checkpoint_number={self.checkpoint_number} (expected {expected!r})"
            )
        return self


class ProvenanceFrontmatter(BaseModel):
    """Cumulative `provenance:` block written into a wiki page's YAML frontmatter.

    Merged across checkpoints per FR-009: `conversations` becomes the union of
    prior and new, `total_edits` increments by 1, `exchange_count` is the size
    of the union of contributing exchange indices, `latest_*` fields are taken
    from this checkpoint.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    latest_checkpoint: str
    conversations: list[str] = Field(default_factory=list)
    latest_action: Literal["created", "updated"]
    latest_confidence: Literal["high", "medium", "low"]
    total_edits: int = Field(ge=1)
    exchange_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Read-side variants — permissive (extra="allow") per FR-002
# ---------------------------------------------------------------------------


class CheckpointRecordRead(CheckpointRecord):
    """Permissive read-side variant of `CheckpointRecord`.

    Spec-mandated deviation from the project-default strict-extras posture
    (constitution v1.1.4 §Project Standards): permanent records must outlive
    readers. Per FR-002, `schema_version=1` evolves additively; future
    versions of this codebase (or external readers like the planned Obsidian
    viewer plugin) may add optional fields. Read-side readers MUST tolerate
    those extras rather than aborting.

    Used by tests today and by any future migration tooling. Production write
    path uses `CheckpointRecord` (strict, extra='forbid').
    """

    model_config = ConfigDict(strict=True, extra="allow")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class HistoryError(Exception):
    """Base for all provenance-related errors. Always caught at the orchestrator
    seam per FR-019; never propagates to fail the run."""


class ShadowRepoUnavailable(HistoryError):
    """`git` not found on PATH, or `git --version` / `git init` returned non-zero.

    Non-fatal at the run level per FR-015. The orchestrator logs a stderr
    `[provenance] git not on PATH; skipping shadow-repo commit` line and
    continues. The JSON write + frontmatter merge have already landed.
    """


class ShadowRepoCommitFailed(HistoryError):
    """`git add` or `git commit` returned non-zero in the shadow repo.

    Non-fatal at the run level per FR-016. The orchestrator logs the captured
    stderr and continues. The next successful commit sweeps up the orphaned
    snapshot files.
    """


class FrontmatterParseFailed(HistoryError):
    """Existing page frontmatter is unparseable YAML.

    Non-fatal at the run level per FR-010. The orchestrator logs a stderr
    warning naming the page, leaves the page's frontmatter unchanged, and
    continues with other pages and the JSON write.
    """


# ---------------------------------------------------------------------------
# JSON coercion helpers
# ---------------------------------------------------------------------------


def _is_json_serializable(value: Any) -> bool:
    """Quick predicate: would `json.dumps(value)` succeed?

    Used by `_coerce_signals_dict` to decide whether to keep a value as-is or
    coerce it via `repr()` per FR-005.
    """
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _coerce_signals_dict(signals: dict[str, Any]) -> dict[str, Any]:
    """Walk `signals` and coerce any non-JSON-serializable value via `repr()`.

    Per FR-005: "If a non-serializable value is encountered, System MUST coerce
    it via repr() and emit a stderr warning naming the offending key."

    Returns a new dict; does not mutate the input.
    """
    coerced: dict[str, Any] = {}
    for key, value in signals.items():
        if _is_json_serializable(value):
            coerced[key] = value
        else:
            print(
                f"[provenance] signal value not JSON-serializable: {key}; coerced via repr()",
                file=sys.stderr,
            )
            coerced[key] = repr(value)
    return coerced


# ---------------------------------------------------------------------------
# Public payload-building + write helpers
# ---------------------------------------------------------------------------


def compute_checkpoint_payload(
    *,
    checkpoint_number: int,
    conversation_id: str | None,
    export_path: str,
    provider: Literal["anthropic", "openai"] | None,
    models_used: list[str],
    transcript_hash: str,
    exchange_records: list[ExchangeRecord],
    editor_decisions: list[EditorDecisionRecord],
    pages_created: list[str],
    pages_updated: list[str],
    pages_skipped: list[str],
    session_log_path: str,
    cursor_path: str,
) -> CheckpointRecord:
    """Build a `CheckpointRecord` from in-memory orchestrator state.

    Pure function: no I/O. Raises `ValidationError` if inputs violate model
    invariants (e.g., empty `exchange_records`, mismatched checkpoint_id
    derivation).

    The orchestrator constructs the inputs from existing state (the
    transcript's metadata, the Spec 004 cursor's transcript_hash, the
    EditorOutput.decisions list, etc.). This function does not consume the raw
    Editor or transcript objects directly to keep the boundary clean and
    testable.

    Determinism: sorts list-of-strings inputs (`pages_*`) ascending,
    sorts `exchange_indices` inside each `EditorDecisionRecord`, and sorts
    `exchange_records` by index. Preserves insertion order of
    `editor_decisions` per FR-001b.
    """
    sorted_decisions: list[EditorDecisionRecord] = []
    for d in editor_decisions:
        sorted_decisions.append(
            EditorDecisionRecord(
                file=d.file,
                action=d.action,
                confidence=d.confidence,
                rationale=d.rationale,
                exchange_indices=sorted(d.exchange_indices),
                signals=_coerce_signals_dict(d.signals),
            )
        )
    sorted_exchanges = sorted(exchange_records, key=lambda r: r.index)
    return CheckpointRecord(
        checkpoint_id=f"cp-{checkpoint_number:03d}",
        checkpoint_number=checkpoint_number,
        timestamp=datetime.now(UTC),
        conversation=ConversationRecord(
            id=conversation_id,
            export_path=export_path,
            provider=provider,
            models_used=models_used,
            transcript_hash=transcript_hash,
        ),
        exchanges=sorted_exchanges,
        editor=EditorBlock(decisions=sorted_decisions),
        results=ResultsRecord(
            pages_created=sorted(pages_created),
            pages_updated=sorted(pages_updated),
            pages_skipped=sorted(pages_skipped),
        ),
        links=LinksRecord(
            session_log=session_log_path,
            cursor=cursor_path,
        ),
    )


def write_checkpoint_metadata(
    *,
    history_dir: Path,
    conversation_subdir: str,
    record: CheckpointRecord,
) -> Path:
    """Atomically write a `CheckpointRecord` to its target path.

    Target: `<history_dir>/checkpoints/<conversation_subdir>/<record.checkpoint_id>.json`.

    Atomicity per FR-001 + Research Decision R6: write to a temp file in the
    target's parent dir, fsync, then `os.replace` to the target path. A reader
    observing the target between writes sees either the prior file or the new
    file, never a partial write.

    Immutability per FR-001a: if the target already exists with the same
    `cp-<NNN>.json` name (e.g., re-running a checkpoint whose cursor advance
    failed), raise `FileExistsError`. The orchestrator catches this and logs
    `[provenance] checkpoint already exists: <path>` per FR-016 without
    failing the run.

    Datetime fields serialize as ISO 8601 UTC with `Z` suffix per FR-001.

    Returns the absolute target path on successful write.
    """
    target_dir = history_dir / "checkpoints" / conversation_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{record.checkpoint_id}.json"

    if target.exists():
        raise FileExistsError(
            f"checkpoint already exists at {target} (cannot overwrite per FR-001a)"
        )

    payload = record.model_dump_json(indent=2)
    payload = payload.replace("+00:00", "Z")

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target_dir),
        prefix=f".{record.checkpoint_id}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            with suppress(OSError):
                os.unlink(tmp_name)
        raise
    return target


# ---------------------------------------------------------------------------
# Frontmatter merge
# ---------------------------------------------------------------------------


_FM_DELIM = "---"


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a markdown file into (frontmatter dict, body text).

    Returns (None, full_text) when the file has no frontmatter (does not start
    with `---\\n`). Raises `FrontmatterParseFailed` when the frontmatter
    delimiters are present but the YAML inside fails to parse.

    Recognizes frontmatter only when the file begins with a line containing
    just `---` followed by YAML content terminated by another `---` line
    (per CommonMark / Obsidian convention). Rejects mid-body `---` rules.
    """
    if not text.startswith(_FM_DELIM):
        return None, text
    lines = text.split("\n")
    if lines[0].strip() != _FM_DELIM:
        return None, text
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIM:
            end_idx = i
            break
    if end_idx is None:
        return None, text
    yaml_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise FrontmatterParseFailed(f"yaml error in frontmatter: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise FrontmatterParseFailed(
            f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )
    return parsed, body


def _assemble_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Reassemble a markdown file from a frontmatter dict + body text.

    Uses PyYAML's safe_dump with `sort_keys=False` so the orchestrator's
    insertion order is preserved. The body is appended verbatim.
    """
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    return f"{_FM_DELIM}\n{fm_text}{_FM_DELIM}\n{body}"


def _merge_provenance_blocks(
    *,
    prior_block: dict[str, Any] | None,
    incoming: ProvenanceFrontmatter,
    incoming_exchange_indices: list[int],
    resolve_prior_indices: Callable[[str], list[int] | None] | None = None,
) -> ProvenanceFrontmatter:
    """Compute the cumulative merge of an existing provenance block with this checkpoint.

    `resolve_prior_indices(prior_latest_checkpoint_path)` is called when a
    prior block is present so we can recover the prior contributing exchange
    indices and union them with the new ones (FR-009). Returns the prior set
    or `None` when the pointer is missing/unparseable/dangling; the latter
    triggers the upper-bound fallback per FR-009 expanded clause.

    `prior_block` may be `None` (no prior block on this page) or a dict from
    the page's existing frontmatter. Unknown extra fields in `prior_block`
    are ignored (forward-compatibility); we extract only the fields we need.
    """
    if prior_block is None:
        return ProvenanceFrontmatter(
            latest_checkpoint=incoming.latest_checkpoint,
            conversations=sorted(set(incoming.conversations)),
            latest_action=incoming.latest_action,
            latest_confidence=incoming.latest_confidence,
            total_edits=1,
            exchange_count=len(set(incoming_exchange_indices)),
        )

    prior_conversations: list[str] = []
    raw_convs = prior_block.get("conversations")
    if isinstance(raw_convs, list):
        prior_conversations = [c for c in raw_convs if isinstance(c, str)]

    prior_total_edits = 0
    raw_total = prior_block.get("total_edits")
    if isinstance(raw_total, int):
        prior_total_edits = raw_total

    prior_exchange_count = 0
    raw_xcount = prior_block.get("exchange_count")
    if isinstance(raw_xcount, int):
        prior_exchange_count = raw_xcount

    new_exchange_count: int
    prior_pointer = prior_block.get("latest_checkpoint")
    if isinstance(prior_pointer, str) and resolve_prior_indices is not None:
        prior_indices = resolve_prior_indices(prior_pointer)
    else:
        prior_indices = None

    if prior_indices is not None:
        new_exchange_count = len(set(prior_indices) | set(incoming_exchange_indices))
    else:
        new_exchange_count = prior_exchange_count + len(incoming_exchange_indices)

    return ProvenanceFrontmatter(
        latest_checkpoint=incoming.latest_checkpoint,
        conversations=sorted(set(prior_conversations) | set(incoming.conversations)),
        latest_action=incoming.latest_action,
        latest_confidence=incoming.latest_confidence,
        total_edits=prior_total_edits + 1,
        exchange_count=new_exchange_count,
    )


def merge_page_provenance(
    *,
    page_path: Path,
    incoming: ProvenanceFrontmatter,
    incoming_exchange_indices: list[int],
    resolve_prior_indices: Callable[[str], list[int] | None] | None = None,
) -> Path:
    """Merge a `provenance:` block into a wiki page's YAML frontmatter atomically.

    Per FR-008 / FR-009 / FR-010 / FR-011:
      - Cumulative merge when an existing `provenance:` block is present
      - Fresh init when no `provenance:` block exists (legacy / user-authored page)
      - Preserves all other frontmatter keys verbatim
      - Atomic write at page-file granularity (tempfile + os.replace)

    Raises:
      FileNotFoundError: page disappeared between Editor's write and this call
        (edge case from spec). Orchestrator catches and logs
        `[provenance] page disappeared before snapshot: <path>` per the
        page-disappeared edge case.
      FrontmatterParseFailed: existing frontmatter is unparseable YAML.
        Orchestrator catches and logs per FR-010.

    Returns the page path on successful merge.
    """
    text = page_path.read_text(encoding="utf-8")
    prior_fm, body = _split_frontmatter(text)
    prior_block = None
    if prior_fm is not None and isinstance(prior_fm.get("provenance"), dict):
        prior_block = prior_fm["provenance"]

    merged = _merge_provenance_blocks(
        prior_block=prior_block,
        incoming=incoming,
        incoming_exchange_indices=incoming_exchange_indices,
        resolve_prior_indices=resolve_prior_indices,
    )

    if prior_fm is None:
        new_fm: dict[str, Any] = {}
    else:
        new_fm = dict(prior_fm)
    new_fm["provenance"] = merged.model_dump()

    new_text = _assemble_frontmatter(new_fm, body if prior_fm is not None else text)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(page_path.parent),
        prefix=f".{page_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, page_path)
    except Exception:
        if os.path.exists(tmp_name):
            with suppress(OSError):
                os.unlink(tmp_name)
        raise
    return page_path


def load_prior_exchange_indices(
    history_dir_root: Path,
    latest_checkpoint_relative: str,
    page_file: str,
) -> list[int] | None:
    """Helper for `merge_page_provenance`: read the prior `cp-<NNN>.json` and
    extract this page's `editor.decisions[*].exchange_indices`.

    `history_dir_root` is the vault root (so `latest_checkpoint_relative` like
    `"InsightMesh/.history/checkpoints/.../cp-002.json"` resolves correctly).
    `page_file` is the vault-relative path matching `EditorDecisionRecord.file`.

    Returns the prior decisions' merged exchange indices for this page, or
    `None` if the file is missing, unreadable, unparseable, or doesn't
    reference this page. Triggers the FR-009 expanded-clause fallback in the
    caller when `None` is returned. Emits a stderr warning on a missing
    pointer (one of the documented stderr-prefix cases per FR-016a).
    """
    full = history_dir_root / latest_checkpoint_relative
    if not full.exists():
        print(
            f"[provenance] prior checkpoint pointer missing for {page_file}: {full}",
            file=sys.stderr,
        )
        return None
    try:
        text = full.read_text(encoding="utf-8")
        parsed = CheckpointRecordRead.model_validate_json(text)
    except (OSError, ValueError) as exc:
        print(
            f"[provenance] prior checkpoint unparseable for {page_file}: {exc}",
            file=sys.stderr,
        )
        return None
    indices: set[int] = set()
    for d in parsed.editor.decisions:
        if d.file == page_file:
            indices.update(d.exchange_indices)
    if not indices:
        return None
    return sorted(indices)


# ---------------------------------------------------------------------------
# Page snapshot
# ---------------------------------------------------------------------------


def snapshot_page(
    *,
    source_page: Path,
    history_dir: Path,
    sanitized_slug: str,
) -> Path:
    """Copy a wiki page to `<history_dir>/pages/<sanitized_slug>.md` preserving mtime.

    Uses `shutil.copy2` per Research Decision R8: preserves mtime + most
    metadata, so the snapshot reflects the page as Editor produced it (not as
    the orchestrator stat'd it later).

    Raises `FileNotFoundError` when the source has been deleted between
    Editor's write and this call (edge case). Orchestrator catches and logs
    the page-disappeared message.

    Returns the destination absolute path.
    """
    dest_dir = history_dir / "pages"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sanitized_slug
    shutil.copy2(source_page, dest)
    return dest


# ---------------------------------------------------------------------------
# Shadow git repository
# ---------------------------------------------------------------------------


_GIT_AVAILABLE_CACHE: bool | None = None


def is_git_available() -> bool:
    """Probe `git --version` once with a short timeout; cache the result per-process.

    Returns `True` when git is on PATH and responds normally, `False` when it
    is missing or fails. Used by the orchestrator to decide whether to attempt
    the shadow-repo commit step per FR-015. Per Research Decision R1, we
    invoke system git directly via `subprocess` rather than depending on
    GitPython / pygit2 (single bounded capability; no new dependency).
    """
    global _GIT_AVAILABLE_CACHE
    if _GIT_AVAILABLE_CACHE is not None:
        return _GIT_AVAILABLE_CACHE
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        _GIT_AVAILABLE_CACHE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _GIT_AVAILABLE_CACHE = False
    return _GIT_AVAILABLE_CACHE


def init_shadow_repo(history_dir: Path) -> None:
    """Idempotently initialize the shadow git repository at `history_dir`.

    Handles all three states defined by FR-012:
      (a) `history_dir` does not exist: mkdir, then `git init`.
      (b) `history_dir` exists AND `<history_dir>/.git/` exists: return
          immediately (treat as already-initialized; do not reset / reconfigure
          per FR-012).
      (c) `history_dir` exists but `<history_dir>/.git/` does not (e.g., user
          manually deleted `.git/`): run `git init` to re-initialize. Existing
          files are preserved (git init is non-destructive).

    Raises `ShadowRepoUnavailable` if git is not on PATH or `git init` returns
    non-zero. Orchestrator catches per FR-015 / FR-016 and logs without
    failing the run.
    """
    if (history_dir / ".git").exists():
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "-C", str(history_dir), "init"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ShadowRepoUnavailable(f"git not on PATH: {exc}") from exc
    if result.returncode != 0:
        raise ShadowRepoUnavailable(f"git init failed in {history_dir}: {result.stderr.strip()}")


def commit_checkpoint(
    *,
    history_dir: Path,
    checkpoint_id: str,
    conversation_id: str | None,
    conversation_subdir: str,
    decisions: list[EditorDecisionRecord],
    pages_created: list[str],
    pages_updated: list[str],
    snapshot_filenames: list[str],
) -> None:
    """Stage and commit the new checkpoint JSON + page snapshots in the shadow repo.

    Builds the commit message per FR-014 (machine-greppable subject + body):

        [InsightMesh checkpoint:cp-002 conversation:<id-or-_flat>] N pages updated, M created

        Metadata: checkpoints/<conv-id>/cp-002.json
        Pages touched:
          - <file> (<action>, confidence:<level>)
          - ...

    Stages explicitly via `git add -- <specific files>` so user-modified state
    elsewhere in `.history/` is not accidentally swept up. Commits with a
    per-call `-c user.email=insightmesh@local -c user.name=InsightMesh` so the
    user's global git config is never read or written.

    Raises `ShadowRepoCommitFailed` with captured stderr on any non-zero exit.
    Orchestrator catches per FR-016 and logs `[provenance] commit failed:
    <git stderr>` without failing the run; the next successful commit sweeps
    up the orphaned snapshot files (`pages/<slug>.md`).
    """
    conv_tag = conversation_id if conversation_id is not None else "_flat"
    subject = (
        f"[InsightMesh checkpoint:{checkpoint_id} conversation:{conv_tag}] "
        f"{len(pages_updated)} pages updated, {len(pages_created)} created"
    )
    body_lines = [
        f"Metadata: checkpoints/{conversation_subdir}/{checkpoint_id}.json",
        "Pages touched:",
    ]
    for d in decisions:
        if d.action == "skipped":
            continue
        body_lines.append(f"  - {d.file} ({d.action}, confidence:{d.confidence})")
    body = "\n".join(body_lines)

    rel_metadata = f"checkpoints/{conversation_subdir}/{checkpoint_id}.json"
    paths_to_add = [rel_metadata]
    for slug in snapshot_filenames:
        paths_to_add.append(f"pages/{slug}")

    add_cmd = ["git", "-C", str(history_dir), "add", "--", *paths_to_add]
    add_result = subprocess.run(add_cmd, capture_output=True, text=True, check=False)
    if add_result.returncode != 0:
        raise ShadowRepoCommitFailed(
            f"git add failed in {history_dir}: {add_result.stderr.strip()}"
        )

    commit_cmd = [
        "git",
        "-C",
        str(history_dir),
        "-c",
        "user.email=insightmesh@local",
        "-c",
        "user.name=InsightMesh",
        "commit",
        "-m",
        subject,
        "-m",
        body,
    ]
    commit_result = subprocess.run(commit_cmd, capture_output=True, text=True, check=False)
    if commit_result.returncode != 0:
        raise ShadowRepoCommitFailed(
            f"git commit failed in {history_dir}: {commit_result.stderr.strip()}"
        )
