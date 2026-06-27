# Feature Specification: Checkpointed synthesis with wiki-as-carry-over

**Feature Branch**: `004-checkpointed-synthesis`
**Created**: 2026-06-25
**Status**: Draft
**Input**: User description: "Checkpointed synthesis with wiki-as-carry-over. Today InsightMesh flattens the entire ChatTranscript and hands it to Synthesis in one shot, so long real conversations will eventually overflow the model's context window even with 1M-token models, and an interrupted run is wasted work with no resume path. This feature processes the transcript in linear forward chunks: after each chunk, the wiki pages produced by Editor become the carry-over state for the next chunk (read back by Historian), and a per-conversation cursor in a JSON sidecar file under logs/ lets a later invocation pick up where the previous one stopped. The wiki itself is the materialized state, with no separate running-summary artifact. Linear forward order only; non-linear slicing (range start, range end, fractional ranges, multi-range, branching) is explicitly rejected because it would corrupt the carry-over invariant. This is a transcript-projection and orchestrator-flow change; no multimodal work, no persistence-schema work beyond the cursor file."

## Clarifications

### Session 2026-06-25

- Q: How should the checkpoint cursor distinguish user-stop from error states? → A: Three-state status (`complete` | `interrupted` | `failed`) plus a separate `last_error: str | None`. `interrupted` covers clean stops (per-invocation cap reached, manual stop); `failed` covers error-induced bails. All three are resumable from the persisted index.
- Q: What does Synthesis see for second-or-later checkpoints of the same conversation? → A: Hybrid digest. Synthesis receives only the new exchanges plus a compact "topics already covered" digest (titles + one-line gists) produced by Historian, scoped to pages from prior checkpoints of this conversation. Full prior-page bodies are NOT inlined.
- Q: What token-budget target should each checkpoint's Synthesis input use? → A: Approximately 50% of the underlying model's context window (default; configurable). Leaves headroom for the topics-covered digest, agent prompts, Synthesis output, and orchestration overhead.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Synthesize a long chat across multiple checkpoints (Priority: P1)

When a user synthesizes a long Claude.ai conversation that would overflow the context window in a single pass, the system processes the transcript in linear forward chunks, advancing a per-conversation cursor after each chunk. Running the same command again continues from where it left off, automatically. The resulting wiki reflects the full conversation as if it had all fit in one shot.

**Why this priority**: This is the core correctness fix and the MVP. Without it the longest real conversations are unsynthesizable, and any interrupted run is wasted.

**Independent Test**: Run the batch command on a transcript large enough to need more than one checkpoint; confirm the wiki contains pages covering every exchange and the cursor reaches end-of-transcript with status complete. Re-run the same command and confirm it is a no-op (already complete).

**Acceptance Scenarios**:

1. **Given** a long conversation requiring multiple checkpoints to fit the synthesis token budget, **When** the user runs the batch command, **Then** the orchestrator processes the transcript in chunks, persists the cursor after each chunk, and produces wiki pages covering every exchange.
2. **Given** a run that was interrupted (crash or manual stop) partway through, **When** the user runs the batch command again on the same input, **Then** processing resumes from the last-persisted cursor position with no duplicate agent work for already-processed exchanges.
3. **Given** a conversation that has been processed to completion, **When** the user runs the batch command again, **Then** the system reports "already complete" and exits without re-invoking the agents.
4. **Given** a second-or-later checkpoint of a long conversation, **When** Synthesis runs for that checkpoint, **Then** it has access (via Historian) to the wiki pages produced by prior checkpoints and extends rather than duplicates them.

---

### User Story 2 - Cap a single invocation's work (Priority: P2)

The user wants to validate the output of the first chunk before committing to a full run, or pace processing across multiple sessions. They pass a per-invocation exchange cap; the cursor persists normally and the next invocation continues forward from where the previous one stopped.

**Why this priority**: A refinement on top of US1. US1 ensures any chat eventually completes; US2 lets the user pace it (useful for spot-checking, scheduled overnight runs, and not committing to a long run on short notice).

**Independent Test**: Run the batch command with an exchange cap on a longer transcript; confirm the cursor advances by approximately the capped count and processing stops. Run again with the same cap and confirm it picks up from where the prior invocation left off.

**Acceptance Scenarios**:

1. **Given** a long transcript and a per-invocation cap of N, **When** the user runs the batch command, **Then** processing stops after at most N exchanges (soft cap: the in-flight checkpoint completes for internal consistency), the cursor is persisted, and the invocation exits cleanly.
2. **Given** a prior capped run that left the cursor at index K, **When** the user runs the batch command again with the same cap, **Then** processing covers approximately exchanges K through K+N and the cursor advances accordingly.
3. **Given** a remaining count smaller than the cap, **When** the user runs the batch command, **Then** processing covers all remaining exchanges and the cursor reaches end-of-transcript.

---

### Edge Cases

- The transcript has been re-exported and its content hash has changed since the last run (for example, the upstream parser added fields, or the user re-exported with edits). The resume invocation refuses to run; the system reports the mismatch and the user MAY pass `--force-resume` to override.
- The cursor exists but the transcript is now shorter than the cursor position (upstream truncation). Resume refuses via the same hash-mismatch path.
- The cursor's `last_processed_exchange_index` exceeds the current transcript's length (truncation upstream that coincidentally preserved the hash, or schema-version drift). The orchestrator MUST refuse to resume and report the index-out-of-bounds, treating it like a hash mismatch.
- The user passes a per-invocation cap of zero or a negative value. The command errors before any agent runs.
- The user explicitly requests resume on a conversation that has no cursor. The command errors with a friendly message naming the expected cursor file path.
- The user wants to re-run a completed conversation from scratch. They delete the cursor file and re-run.
- A checkpoint write fails (vault error, disk error, etc.). The cursor records the failed status and the last error; subsequent invocations refuse to proceed silently and require an explicit `--retry` flag to continue.
- The `--max-exchanges N` and `--resume` flags compose freely: `--resume` requires an existing cursor (errors otherwise); `--max-exchanges N` caps the work for this invocation. Using both means "require a cursor AND cap this invocation at N additional exchanges."
- The cursor file exists on disk but is malformed (corrupted JSON or schema validation failure). The orchestrator MUST refuse to load it, report the parse failure with the file path, and exit non-zero. The user can delete the cursor file to start fresh.
- The cursor file is deleted (by the user or another process) between the orchestrator's load and its next write. The orchestrator's next checkpoint write recreates the file at the resolved path; the user-deleted intermediate state is lost. The single-writer assumption means this is not a supported workflow; documented for completeness.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST process a transcript in one or more checkpoints, advancing in linear forward exchange order from the cursor position (or from index 0 when no cursor exists) toward end-of-transcript or the per-invocation cap, whichever is reached first.
- **FR-002**: After each successful checkpoint, System MUST persist a per-conversation cursor recording the last exchange index processed and the appropriate status value (see Checkpoint cursor entity for status semantics). The cursor write MUST be atomic against process interruption: a crash or kill signal during the write MUST NOT leave the cursor file in an unparseable or partially-written state. Implementations may use write-temp-then-rename or an equivalent atomicity primitive.
- **FR-003**: When a cursor exists for the target conversation, System MUST resume processing from the exchange immediately following the persisted index, with no re-invocation of agents for already-processed exchanges.
- **FR-004**: System MUST store the cursor in a single JSON sidecar file per conversation, separate from any wiki page frontmatter. The cursor MUST NOT be reconstructable from the wiki pages alone (a conversation produces many wiki pages but holds only one cursor, so the single source of truth lives outside the pages). Exactly one cursor file exists per (export source, conversation identifier) pair at any time; the orchestrator MUST NOT write cursor state to any other location (no wiki frontmatter, no separate database, no shadow file).
- **FR-005**: Cursor lookup MUST be keyed on the (export source, conversation identifier) pair so that multi-conversation exports do not collide and a single export file can hold many independent cursors. The cursor file is stored under the logs directory using one of two paths depending on the source: for a single-conversation source file, `logs/{stem}.checkpoint.json`; for a multi-conversation export with a conversation identifier, `logs/{stem}__{conversation_id}.checkpoint.json` (with filesystem-unsafe characters in the conversation identifier sanitized to hyphens).
- **FR-006**: System MUST detect when the source transcript has changed since the cursor was written, via a SHA-256 content hash of the extracted ChatTranscript's canonical JSON serialization, recorded with the cursor at write time. On hash mismatch, the resume invocation MUST refuse by default and surface the mismatch to the user; the user MAY pass `--force-resume` to override and continue from the cursor's recorded index against the new transcript (at their own risk: indices may now point at different exchanges).
- **FR-007**: When the cursor indicates the conversation is fully processed, System MUST report "already complete" and exit without invoking the synthesis pipeline. The `complete` status is terminal: it cannot transition to any other status while the cursor exists. To re-process the conversation, the user must delete the cursor file (after which the next invocation starts from index 0).
- **FR-008**: When the user provides a per-invocation cap of zero or a negative value, System MUST error out before invoking any agent.
- **FR-009**: When the user provides a positive per-invocation cap of N, System MUST process up to N additional exchanges this invocation, treating the limit as a soft cap evaluated between checkpoints. The cap is checked after each successful checkpoint completes; if the count of exchanges processed has reached or exceeded N at that point, processing stops. As a result, the cursor may advance past N by up to the size of the most recent checkpoint (ensuring no checkpoint is interrupted mid-flight and that the cursor's resting point is always a checkpoint boundary).
- **FR-010**: When the user explicitly requests resume for a conversation with no existing cursor, System MUST error out with a message naming the expected cursor file path.
- **FR-011**: For second-or-later checkpoints of the same conversation, Synthesis MUST receive a compact "topics-covered digest" produced by Historian, consisting of prior wiki page titles plus a brief one-line gist per page, scoped to pages produced by prior checkpoints of this conversation. Synthesis MUST use the digest to extend or cross-reference prior pages rather than producing duplicates. Full prior-page bodies MUST NOT be inlined into Synthesis input; the digest is the carry-over surface, keeping Synthesis input lean.
- **FR-012**: System MUST NOT support non-linear processing of a transcript (slicing by range start and end, fractional ranges, multi-range, branching cursors). The linear forward order is a structural invariant: each checkpoint's output is context for the next, and non-linear processing would corrupt that.
- **FR-013**: For any conversation small enough to fit within a single checkpoint, System MUST produce the same wiki output as if it had been processed in one shot, with no behavior change versus the pre-feature pipeline.
- **FR-014**: When a checkpoint write fails (vault write error, disk error, etc.) or an agent invocation errors, System MUST record the cursor with status `failed` and a `last_error` message and exit; subsequent resume invocations MUST surface the prior `failed` status and error message to the user before continuing. On resume against a cursor with `status == failed`, the orchestrator MUST print the prior `last_error` to stderr and exit with code 1 unless the user explicitly passes `--retry`, in which case the orchestrator proceeds with a fresh checkpoint attempt from the cursor position. This forces explicit acknowledgement of prior failure while remaining scriptable. When the cursor's `status` is anything other than `failed`, `last_error` MUST be null.
- **FR-015**: System MUST size each checkpoint's Synthesis input (the rendered new exchanges plus the topics-covered digest plus agent prompts) to fit within approximately 50% of the underlying model's context window, leaving the remaining capacity as headroom for Synthesis output and orchestration overhead. The 50% target MAY be configurable; this is the default.
- **FR-016**: The cursor file MUST carry a `schema_version` integer field. When a loaded cursor's `schema_version` does not match the version the orchestrator understands, the orchestrator MUST refuse to resume and report the version mismatch to the user. The user must delete the cursor to start fresh.
- **FR-017**: The cursor's `meaning_summary` field MUST remain null throughout this spec's scope. The field exists for forward-compatibility only; populating it is deferred to a future spec.

### Key Entities

- **Checkpoint cursor**: Per-conversation state record. Captures the source export reference, the conversation identifier, the transcript content hash, the last processed exchange index, a monotonic checkpoint sequence number, a status, a `last_error` field, and an optional metadata field reserved for future use. The status takes one of three values: `complete` (the cursor reached end-of-transcript), `interrupted` (the run stopped cleanly before end-of-transcript; for example, the per-invocation cap was reached or the process was manually stopped, with no error), or `failed` (an agent or vault write error caused the bail; the error message is recorded in `last_error`). All three are resumable from the persisted index; on resume the prior `failed` status and `last_error` are surfaced to the user before continuing. Single source of truth for "where did processing leave off on this conversation."
- **Conversation**: A single chat thread, identified by the (export source, conversation identifier) pair. May produce one or many wiki pages over its lifetime. Holds exactly one cursor.
- **Wiki page**: Synthesized output. May be created during one checkpoint and updated during later checkpoints of the same conversation (or other conversations on overlapping topics, via existing Editor behavior).
- **Carry-over state**: The set of wiki pages produced by prior checkpoints of the same conversation. The pages themselves are the running materialized view; there is no separate running-summary artifact. For second-or-later checkpoints, this carry-over reaches Synthesis as a compact topics-covered digest (see below), not as full page bodies.
- **Topics-covered digest**: A compact context object produced by Historian for second-or-later checkpoints of a conversation. Lists prior wiki page titles plus a one-line gist per page, scoped to pages produced by prior checkpoints of this conversation. Passed to Synthesis as input context (alongside the new exchanges) so Synthesis can extend or cross-reference prior pages without receiving their full bodies.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A conversation whose flattened transcript exceeds the per-checkpoint Synthesis input budget (default approximately 50% of the model's context window) now processes to completion across multiple checkpoints. On a real example from the user's exports, a conversation that previously could not be synthesized in one shot now produces a wiki covering every exchange.
- **SC-002**: An interrupted run, when re-invoked, completes the remaining exchanges with zero re-invocations of Synthesis for any already-processed exchange (verifiable via session logs).
- **SC-003**: Running the batch command on an already-complete conversation exits without invoking any agent and reports "already complete."
- **SC-004**: For a conversation small enough to fit in a single checkpoint, the produced wiki is identical to the pre-feature behavior (same number of pages, same content).
- **SC-005**: After multiple checkpoints, every exchange in the original transcript contributes to at least one wiki page; the cumulative effect is equivalent to processing the entire transcript in one pass.
- **SC-006**: When a per-invocation cap of N exchanges is passed, the cursor's `last_processed_exchange_index` advances by at most N plus the number of additional exchanges in the in-flight checkpoint at the cap boundary (the soft cap is measured in exchanges, not in tokens or characters; this confirms the in-flight checkpoint completes for internal consistency).
- **SC-007**: When the transcript hash changes between runs, resume refuses by default; the user is informed of the mismatch and can choose to override.

## Assumptions

- Multi-conversation exports (Claude.ai bundles many conversations into one file) provide a stable conversation identifier that survives re-extraction. The (export source, conversation identifier) pair uniquely names a cursor.
- Historian's existing search-based retrieval (BM25 over the vault) is sufficient for surfacing prior-checkpoint pages of the same conversation when relevant. Tighter retrieval (semantic search, per-conversation filtering, controlled tag vocabulary) is a separate concern outside the scope of this spec.
- Editor's existing create-vs-update logic (the three-signal rule from Spec 001) is idempotent on repeated input. Re-running a checkpoint produces the same output via the update path rather than creating duplicates.
- The user's command surface for chunking is the existing batch command with new flags. No new top-level command is introduced.
- Cursor files live alongside session logs in the existing logs directory. No new top-level directory is introduced.
- A failed checkpoint write does not corrupt prior checkpoint outputs (wiki pages from earlier checkpoints remain valid and the partial work to date is recoverable).
- The "non-linear slicing" rejection is structural, not a deferred feature. A future need to re-synthesize a middle section of a transcript is resolved by deleting the cursor and re-running forward from the desired point, not by adding slice flags.
- The CLI is invoked one process at a time per conversation. Concurrent invocations on the same conversation are not supported by this spec; the cursor file is a single-writer artifact. Multi-process safety (file locks, transactional writes) is a future concern if multi-user or scheduled-job scenarios emerge.

## Out of Scope

- **Non-linear slicing flags** (range start, range end, fractional ranges, multi-range processing, branching cursors). Considered and explicitly rejected: linear forward order is a structural invariant because earlier wiki pages are the carry-over context for later checkpoints, and slicing would corrupt that. A debugging need to re-synthesize a middle section is resolved by deleting the cursor and re-running forward from the desired point.
- **Persisted per-page provenance** (a frontmatter sources list mapping specific exchanges to specific pages). Belongs with the next spec on visible wiki evolution.
- **Visualization of checkpoint history** (per-checkpoint git commits, frontmatter changelog, sidecar timeline UI). The cursor schema lands first; the viewer is its own slice.
- **Semantic or epistemic commit engine** (an extra model pass that decides whether the state has changed enough to warrant a checkpoint). The cursor schema reserves a nullable metadata field as a forward-compatibility hook, but it is never populated in this spec. A future iteration may populate it from already-existing agent output (never via an additional agent call).
- **Historian retrieval scaling** (vector store, pgvector, graph traversal, controlled tag vocabulary). This spec relies on existing BM25 search. A related Phase A tag-vocabulary refinement is tracked separately in project memory and is also out of scope here.
- **Separate summarizer agent** or **tiered memory architecture** (MemGPT-style). Doubling agents to dodge context is the wrong direction; wiki-as-carry-over is the lower-cost first step.
- **Refiner, Critic, or Researcher agent additions**. Independent of state; their own specs.
- **Live interactive inquiry mode**. Independent of batch synthesis; if added later, it becomes "checkpoints of size 1, forever," inheriting the contracts defined here.
- **Contradiction or conflict detection** (Editor flags when new content disagrees with existing wiki content). Its own spec; raised in stakes by checkpointing across conversations on shared topics, but downstream work.
- **Database or Postgres for cursor storage**. Phase A stays with JSON sidecar files in the logs directory. Phase B migration is gated on real scale need and uses the JSON schema as its migration source.
- **Multimodal additions** (image binaries, audio, video). Separate effort, separate spec.
