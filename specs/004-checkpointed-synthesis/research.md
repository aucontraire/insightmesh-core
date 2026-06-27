# Research: Checkpointed synthesis with wiki-as-carry-over

Phase 0 output. Resolves the technical-context decisions whose exact mechanism the spec left to implementation. No NEEDS CLARIFICATION items from the spec remain.

## Decision 1: Transcript content hash

**Decision**: SHA-256 hex digest of `ChatTranscript.model_dump_json()` (Pydantic v2's default, which is deterministic for a model containing only ordered primitives + lists). 64-char hex string stored in `Checkpoint.transcript_hash`.

**Rationale**: The cursor must invalidate when the upstream transcript changes (FR-006). The transcript is a Pydantic model with no sets or unordered structures, so its JSON serialization is deterministic. SHA-256 is cheap, ubiquitous, and collision-safe for this use case. Using `model_dump_json()` rather than rolling our own canonicalization keeps the implementation tiny and reuses Pydantic's guarantees.

**Alternatives considered**:
- *MD5*: faster but not collision-safe enough; SHA-256 is the modern default.
- *Custom canonical JSON* (e.g., `json.dumps(sort_keys=True)`): adds a step that Pydantic already does correctly; risk of drift between hash computation and serialization.
- *Hash the source file*: would invalidate when irrelevant export-file metadata changes (e.g., timestamps in OpenAI bundle wrappers); the post-extraction transcript is the right level.

## Decision 2: Token-budget enforcement for checkpoint boundaries

**Decision**: Character-count heuristic (`approx_tokens = len(text) // 3.5`) applied to the rendered Synthesis input (the `_to_role_content` JSON plus the digest plus agent prompt overhead). Default budget = 50% of the model's context window (Sonnet → ~100K tokens; configurable via a single constant or env var, but not exposed as a CLI flag in this spec). Boundary preference: when adding the next exchange would exceed the budget, cut the checkpoint here.

**Rationale**: Exact token counting requires the Anthropic token-counter API (network round-trip) or `tiktoken` (OpenAI's tokenizer, wrong tokenizer for Claude). The char-based approximation is off by maybe 30%, but the spec target is "approximately 50%" and we have ~100K of headroom for output and overhead on a 200K context window — far more than 30% error consumes. Pragmatic, no new dependency, no network call.

**Alternatives considered**:
- *Anthropic token-counter API*: precise but requires a network round-trip per boundary check; over-engineered for the precision needed.
- *tiktoken*: wrong tokenizer for Claude; would silently overcount or undercount.
- *Topic-boundary-aware splitting* (an extra Synthesis pre-pass that identifies natural breaks): explicitly deferred per the Out of Scope "semantic / epistemic commit engine" item. The current approach is mechanical by design; topic-aware splitting layers on later if/when the simple thing fails.

**Note**: 3.5 chars/token is a well-known rule of thumb for English text; we can tune the constant after observing real runs. The exact value is not part of the spec.

## Decision 3: Atomic cursor JSON write

**Decision**: Write-to-temp-then-rename pattern. The save helper writes `logs/{stem}.checkpoint.json.tmp`, then `os.replace` to `logs/{stem}.checkpoint.json`. On POSIX, `os.replace` is atomic for files on the same filesystem (which `logs/` always is).

**Rationale**: A partial write during a crash could leave the cursor in an unparseable state, defeating resume. Atomic rename is the standard idiom. Cheap, no new dependency.

**Alternatives considered**:
- *Direct write*: vulnerable to torn writes on crash.
- *File lock + journal*: overkill for a single-writer assumption (spec assumes single process per conversation).
- *SQLite*: brings a Phase B-style dependency (a database) for state we explicitly chose to keep in flat files for this spec.

## Decision 4: Topics-covered digest production

**Decision**: Historian emits a `topics_covered_increment: list[DigestEntry]` field in its existing per-checkpoint output. One entry per `augmented_draft` Historian processed in this checkpoint: `{page_title, gist}`, where `gist` is a one-line summary Historian generates from the draft's title and first paragraph. The orchestrator accumulates these into `Checkpoint.topics_covered_digest` after each successful checkpoint. On entry to the next checkpoint, the orchestrator passes the accumulated digest to Synthesis as input context (alongside the new exchanges). Checkpoint #1 has no prior digest, so Synthesis input is identical to the pre-feature behavior.

**Rationale**: This satisfies FR-011 (Synthesis sees a hybrid digest, not full prior-page bodies) without an additional agent invocation. Historian was already going to look at the augmented drafts in its existing pass; producing the digest is essentially free (no new vault reads, no extra MCPVault calls — the digest source is the drafts in hand). The cursor accumulates digest entries across checkpoints so Synthesis on checkpoint N sees the digest of checkpoints 1..N-1 combined.

This design also keeps the pipeline shape unchanged (still Synthesis → Historian → Editor per checkpoint); only the data flowing into and out of the pipeline grows.

**Alternatives considered**:
- *Pre-pass agent invocation* (Historian or a new agent runs before Synthesis just to produce the digest): doubles agent invocations per checkpoint; unnecessary because the existing Historian invocation has what it needs.
- *Orchestrator reads vault pages directly to produce digest*: violates the project's "vault I/O happens from agents" pattern; requires duplicate page-reading logic in Python.
- *Per-page frontmatter caches a `gist` field that Editor wrote at creation*: requires Editor schema changes that touch the wiki output format (out of scope per spec; pairs with the deferred provenance work).

## Decision 5: Cursor file path derivation

**Decision**:
- Multi-conversation source (a `--conversation` flag was used): `logs/{export_stem}__{conversation_id}.checkpoint.json`
- Single-conversation source file (no `--conversation`): `logs/{stem}.checkpoint.json`

Where `{export_stem}` is the input filename without extension, and `{conversation_id}` is the full conversation ID as provided to the CLI (typically a UUID for Claude.ai, alphanumeric for ChatGPT). Filesystem-unsafe characters in the conversation ID are sanitized (slash and colon become hyphen).

**Rationale**: The path encodes the `(export_path, conversation_id)` key in a human-readable way so a user can find the cursor by listing `logs/`. Using the full conversation ID (not a hash or truncation) preserves at-a-glance identification.

**Alternatives considered**:
- *Hash both into a short id*: harder to inspect; no real space savings on a local filesystem.
- *Separate `state/` directory*: introduces a new top-level directory for one new artifact type; the spec keeps everything under `logs/` for symmetry with `SessionLog`.

## Decision 6: Per-conversation key uniqueness

**Decision**: The cursor key is `(export_path, conversation_id)`. `export_path` is the absolute path of the input file as the CLI saw it. `conversation_id` is `None` for single-conversation source files and the user-supplied identifier for multi-conversation exports.

**Rationale**: This matches how the CLI already identifies a conversation (Spec 002 added the `--conversation` flag). Same identifier, same cursor.

**Edge note**: If the user moves the export file, the cursor path no longer matches the new export_path. That is the user's responsibility; we do not chase the file. A future enhancement could index cursors by transcript hash instead, but it is out of scope here.

## Decision 7: Failure-status semantics on resume

**Decision**: Per FR-014 (clarified Q1, amended via /spec-gaps 2026-06-26), three-state cursor status:
- `complete` — cursor reached end-of-transcript; resume is a no-op (FR-007; status is terminal).
- `interrupted` — clean stop (cap reached, manual stop, soft cap); resume continues forward silently.
- `failed` — agent error or vault write error; resume surfaces the prior `last_error` and requires explicit acknowledgement.

On `failed`, the resume invocation prints the prior `last_error` to stderr and exits 1 by default. The user MUST pass `--retry` to proceed (no interactive prompt; the CLI stays scriptable in non-interactive contexts like cron). With `--retry`, the orchestrator runs as a fresh checkpoint attempt from the cursor position.

**Rationale**: Failures often have a transient cause (vault locked, network blip) but sometimes don't (malformed input, bug). Forcing the user to see the prior error AND pass an explicit flag makes failure-mode triage explicit instead of silent, without breaking scripted workflows. The interactive-prompt option was considered and rejected in favor of `--retry` to keep the CLI scriptable everywhere.

**Alternatives considered**:
- *Auto-retry on resume*: hides bugs.
- *Refuse to resume after failure (no override)*: too strict; transient errors are common.
- *Interactive prompt only*: breaks scriptable contexts (cron, scheduled jobs); `--retry` flag is equivalent and works everywhere.

## Open questions deferred to implementation

- *Cursor schema versioning*: how to handle future field additions. Likely answer: a `schema_version: int = 1` field; resume refuses on unknown versions. Decided at implementation time.
- *Configurability of the 50% budget target*: env var vs `pyproject.toml` setting vs hard-coded constant. Trivial choice; pick the one that keeps the diff smallest.
- *Exact one-line-gist length for `DigestEntry.gist`*: cap at 200 chars; tune after observing real digests.
