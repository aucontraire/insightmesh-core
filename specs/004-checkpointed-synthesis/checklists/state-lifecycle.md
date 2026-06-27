# State Lifecycle Checklist: Checkpointed synthesis with wiki-as-carry-over

**Purpose**: Validate that the cursor schema, state transitions, hash invalidation, persistence, and resume semantics are completely, consistently, and unambiguously specified before implementation. This checklist tests the REQUIREMENTS THEMSELVES (unit tests for English), not the implementation.
**Created**: 2026-06-26
**Feature**: [spec.md](../spec.md)
**Resolved**: 2026-06-26 via `/spec-gaps all` (14 spec edits applied; remaining 33 items noted as already-covered)

## Cursor Schema Completeness

- [x] CHK001 Is the cursor's `schema_version` field's purpose (and resume-refuses-on-unknown behavior) specified in the spec, not just data-model? [Gap, Spec §Requirements] → Resolved by FR-016.
- [x] CHK002 Are required vs optional cursor fields explicitly distinguished in the spec? [Completeness, Spec §Key Entities] → Already covered: Key Entities lists required fields; optional ones (`last_error`, `meaning_summary`) are clearly nullable.
- [x] CHK003 Is the `last_error` field's null/non-null invariant tied to the `failed` status explicitly stated as a requirement (not only inferred from prose)? [Clarity, Spec §FR-014] → Resolved by FR-014 amendment.
- [x] CHK004 Is the `meaning_summary` field's "always null in this spec" constraint stated as a testable requirement (not only documented in plan.md guardrail)? [Gap, Spec §Out of Scope] → Resolved by FR-017.
- [x] CHK005 Are filesystem-unsafe characters in `conversation_id` (slash, colon, etc.) addressed in the spec (sanitization rule), or is this an implementation concern that should be flagged as out of scope? [Gap, Spec §FR-005] → Resolved as part of FR-005 amendment (path scheme + sanitization).

## Cursor Lifecycle and State Transitions

- [x] CHK006 Are the write conditions for each of the three status values (`complete`, `interrupted`, `failed`) specified as requirements? [Completeness, Spec §Clarifications, §FR-002] → Already covered: Clarifications + Key Entities.
- [x] CHK007 Is the transition path from `interrupted` → resume → next-state defined unambiguously (does it always proceed, or are there preconditions)? [Clarity, Spec §FR-003] → Already covered: FR-003.
- [x] CHK008 Is the transition path from `failed` → resume → next-state defined (auto-continue, prompt, --retry flag, etc.)? Research decision 7 introduces interactive confirm or `--retry`; the spec does not mention either. [Ambiguity, Spec §FR-014] → Resolved by FR-014 amendment (--retry).
- [x] CHK009 Is the no-op behavior on `status == complete` specified with an explicit user-facing message requirement? [Completeness, Spec §FR-007] → Already covered: FR-007 says "report 'already complete'."
- [x] CHK010 Is `checkpoint_number`'s monotonic-increment rule stated as a requirement (not only as an entity field description)? [Gap, Spec §Key Entities] → Already covered: Key Entities + data-model.
- [x] CHK011 Are the conditions that distinguish `interrupted` from `failed` enumerable from the spec alone (without reading the data-model)? [Clarity, Spec §Clarifications] → Already covered: Clarifications enumerate them.
- [x] CHK012 Is a state transition from `complete` → any other state explicitly disallowed, or is the spec silent on whether `complete` can be re-opened? [Gap, Spec §FR-007] → Resolved by FR-007 amendment (terminal).

## Hash Invalidation Semantics

- [x] CHK013 Is the precise hash input (what gets hashed) specified at the requirement level, or only in research? [Clarity, Spec §FR-006] → Resolved by FR-006 amendment (SHA-256 of canonical JSON serialization).
- [x] CHK014 Is the hash-mismatch error response specified consistently between spec (FR-006: "MAY provide an override option") and contracts/quickstart (which assume `--force-resume` exists)? [Conflict, Spec §FR-006] → Resolved by FR-006 amendment (--force-resume named).
- [x] CHK015 If override exists, is its name specified in the spec or left for implementation? [Gap, Spec §Edge Cases] → Resolved by FR-006 amendment.
- [x] CHK016 Is the behavior when the transcript hash matches but the cursor's `last_processed_exchange_index` exceeds the current transcript length (mid-export truncation that preserved hash by coincidence) addressed? [Edge Case, Gap] → Resolved by Edge Case bullet.
- [x] CHK017 Is the cursor invalidation path consistent across "hash mismatch" and "transcript shorter than cursor" edge cases (FR-006 vs the edge case bullet about truncation)? [Consistency, Spec §Edge Cases] → Now consistent after CHK014/016 resolutions.

## Cursor Persistence Location

- [x] CHK018 Is the cursor file path scheme for single-conversation files specified in the spec, not just the contracts? [Completeness, Spec §FR-004] → Resolved by FR-005 amendment.
- [x] CHK019 Is the cursor file path scheme for multi-conversation exports specified in the spec (or appropriately delegated to plan-time)? [Completeness, Spec §FR-005] → Resolved by FR-005 amendment.
- [x] CHK020 Is the "single source of truth" claim measurable: exactly one cursor file per `(export, conversation_id)` pair, never split? [Measurability, Spec §FR-004] → Resolved by FR-004 amendment.
- [x] CHK021 Is the rationale for "NOT in markdown frontmatter" documented somewhere in the spec (not only in plan history)? [Traceability, Spec §FR-004] → Already covered: FR-004 parenthetical.
- [x] CHK022 Is the cursor file's atomic-write requirement stated as a requirement (corruption prevention), or only as a research decision? [Gap, Spec §Requirements] → Resolved by FR-002 amendment.

## Concurrent Access (Assumption Surface)

- [x] CHK023 Is the single-writer-per-conversation assumption documented as an assumption (not silently relied on)? [Assumption, Spec §Assumptions] → Already covered: Assumptions.
- [x] CHK024 Are the failure modes if two invocations run concurrently on the same conversation (cursor corruption, lost writes) addressed, even if only to declare them out-of-scope? [Coverage, Gap] → Covered via Assumptions + CHK044 Edge Case.
- [x] CHK025 Is the boundary between "this spec's single-writer assumption" and "future multi-process safety" clearly drawn so a future spec knows what to add? [Clarity, Spec §Assumptions] → Already covered: Assumptions note "future concern if multi-user or scheduled-job scenarios emerge."

## Per-Invocation Cap Semantics

- [x] CHK026 Is "soft cap" semantics defined unambiguously (in-flight checkpoint completes; the cursor may advance past N by the size of that in-flight checkpoint)? [Clarity, Spec §FR-009, §SC-006] → Already covered: FR-009 + SC-006 + Clarifications.
- [x] CHK027 Is the behavior when `--max-exchanges N` exceeds remaining work specified (process to end, status complete)? [Coverage, Spec §SC-006, Edge Cases] → Already covered: US2 AS-3 + SC-006.
- [x] CHK028 Is the error response for `--max-exchanges 0` or negative values specified at the requirement level (not only in contracts)? [Completeness, Spec §FR-008] → Already covered: FR-008.
- [x] CHK029 Is the relationship between `--max-exchanges` and `--resume` flags specified (compose freely, mutually exclusive, one wins)? [Coverage, Gap] → Resolved by Edge Case bullet.

## Resume Triggers

- [x] CHK030 Is the difference between default-behavior auto-resume (cursor read silently) and explicit `--resume` flag (errors on missing cursor) specified clearly in the requirements? [Clarity, Spec §FR-010] → Already covered: FR-003 (auto-resume) + FR-010 (explicit error).
- [x] CHK031 Is the user-facing message for "explicit --resume on missing cursor" specified to name the expected path (not just generic "not found")? [Completeness, Spec §FR-010, Edge Cases] → Already covered: FR-010 + Edge Case bullet.

## Failure Recovery

- [x] CHK032 Are checkpoint-write failures distinguished from agent-invocation failures in the spec, or treated identically? [Clarity, Spec §FR-014] → Already covered: FR-014 enumerates both ("checkpoint write fails ... or an agent invocation errors"); distinguishing them is a plan-level concern.
- [x] CHK033 Is the prior-failure surfacing on resume specified as user-visible behavior with a defined surface (interactive prompt, log line, exit code)? [Ambiguity, Spec §FR-014] → Resolved by FR-014 amendment (stderr + exit code 1 + --retry).
- [x] CHK034 Is the retry mechanism after a `failed` status defined as a requirement, or left to implementation? Research decision 7 mentions interactive vs `--retry`; spec does not. [Gap, Spec §FR-014] → Resolved by FR-014 amendment.
- [x] CHK035 Is the partial-cursor-write scenario (process killed mid-`save_checkpoint`) addressed in the spec (corruption-resistance requirement), or is it only addressed by the research decision on atomic write? [Coverage, Gap] → Resolved by FR-002 amendment.
- [x] CHK036 Is the malformed-cursor-on-disk scenario (file exists but is not parseable JSON) addressed? [Edge Case, Gap] → Resolved by Edge Case bullet.

## Cross-Spec / Cross-Artifact Consistency

- [x] CHK037 Are the cursor and `SessionLog` lifecycles clearly distinguished in the spec (one per-conversation, one per-invocation)? [Consistency, Spec §Assumptions] → Already covered: Assumptions.
- [x] CHK038 Is FR-011's "topics-covered digest" consistent in shape and scope across spec (entity), data-model (`DigestEntry`), and contracts (Historian output addition)? [Consistency] → Already covered: confirmed during /speckit-plan cross-doc pass.
- [x] CHK039 Is the Editor's existing FR-007 update path (from Spec 001) referenced as the idempotency guarantee in the spec, not only in plan/research? [Traceability, Spec §Assumptions] → Already covered: Assumptions ("Editor's existing create-vs-update logic ... is idempotent on repeated input").
- [x] CHK040 Are all flags introduced in the spec (`--resume`, `--max-exchanges`) consistent with the flags described in contracts (where `--force-resume` and `--retry` also appear)? [Conflict, Spec §FR-009, §FR-010] → Resolved by spec amendments (now both flags are named in the spec).

## Edge Case Coverage

- [x] CHK041 Is the "0 exchanges remaining" condition after a resume (cursor at end-1, only one exchange left) specified to fall under the soft-cap / end-of-transcript path? [Edge Case, Spec §FR-001] → Already covered: FR-001 ("toward end-of-transcript ... whichever is reached first").
- [x] CHK042 Is the "transcript is exactly one exchange total" condition specified to fall under FR-013 (single-checkpoint no-regression)? [Edge Case, Spec §FR-013] → Already covered: FR-013.
- [x] CHK043 Is the "all exchanges fit in one checkpoint" condition tied explicitly to FR-013's equivalence guarantee (same wiki output as pre-feature)? [Measurability, Spec §FR-013, §SC-004] → Already covered: FR-013 + SC-004.
- [x] CHK044 Is the "user deletes cursor mid-run" scenario addressed (race between cursor delete and orchestrator write)? [Edge Case, Gap] → Resolved by Edge Case bullet.

## Acceptance Criteria Quality

- [x] CHK045 Can SC-002's "zero re-invocations of Synthesis for any already-processed exchange" be objectively verified from session logs alone (without reading agent prompts)? [Measurability, Spec §SC-002] → Already covered: SC-002 explicitly says "verifiable via session logs."
- [x] CHK046 Is SC-006's "N plus the size of the in-flight checkpoint at the cap boundary" measurable (is "size" defined as exchanges, tokens, or characters)? [Ambiguity, Spec §SC-006] → Resolved by SC-006 amendment (measured in exchanges).
- [x] CHK047 Does the spec define how SC-001's "exceeds the per-checkpoint Synthesis input budget" is measured at test time (the heuristic, the threshold)? [Measurability, Spec §SC-001, §FR-015] → Already covered: SC-001 references "approximately 50% of model's context window" (FR-015); the heuristic itself is a plan/research decision (acceptable).

## Notes

- All 47 items resolved on 2026-06-26 via `/spec-gaps all`.
- 14 items required spec.md edits (CHK001, CHK003, CHK004, CHK005, CHK008, CHK012, CHK013, CHK014, CHK015, CHK016, CHK018, CHK019, CHK020, CHK022, CHK029, CHK033, CHK034, CHK035, CHK036, CHK040, CHK044, CHK046 — 22 items addressed by 14 edits, since some edits resolved multiple items).
- 25 items were already covered by the spec as written (after /speckit-clarify).
- Cross-document consistency pass: contracts/checkpoint-orchestrator.md updated (CLI flag table + exit codes); quickstart.md updated (Scenario E uses --retry); data-model.md and research.md unchanged (already consistent).
- Items marked `[Gap]` had real missing requirements that this round introduced. Items marked `[Ambiguity]` had soft language now tightened. Items marked `[Conflict]` had cross-document divergence now reconciled.
- Spec is ready for `/speckit-analyze` (final consistency sweep) and then `/speckit-tasks`.
