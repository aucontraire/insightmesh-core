# Durability & Resilience Checklist: Per-page provenance

**Purpose**: Validate that the spec pins down (a) the rules that bind permanent on-disk artifacts and cumulative-state math (data integrity & schema durability), and (b) the behavior, run-level consequence, and observability of every documented failure mode (resilience & failure semantics). These rules are hard to fix later: the JSON files are permanent, the frontmatter writes mutate user files, and silent provenance failure is the dominant risk profile.
**Created**: 2026-06-28
**Feature**: [spec.md](../spec.md)

---

## Schema Durability

- [x] CHK001 Are the rules for "additive forward-compatibility within `schema_version=1`" specified with a concrete reader contract (tolerate unknown extras, default missing optionals) rather than left as a posture? [Clarity, Spec §FR-002]
- [x] CHK002 Is the major-version-bump path specified — what triggers it, what migration tooling MUST exist, and what happens to readers that encounter an unknown major version? [Gap, Completeness, Spec §FR-002]
- [x] CHK003 Are the immutability semantics of an already-written `cp-<NNN>.json` explicit — can the orchestrator ever overwrite, patch, or delete one? [Gap, Clarity, Spec §FR-001]
- [x] CHK004 Is the canonical timestamp serialization format specified (timezone, precision, ISO 8601 with `Z` suffix) in the requirements text, not only in derivative design documents? [Gap, Clarity, Spec §FR-001]
- [x] CHK005 Are the rules for serializing `signals: dict[str, Any]` to JSON specified — JSON-serializable types only? What is the contract when Editor's signals dict contains a non-serializable value? [Gap, Edge Case, Spec §FR-005]
- [x] CHK006 Is the binding between `checkpoint_id` (string) and `checkpoint_number` (int) defined as a derivation rule with a single source of truth, rather than two independent fields that could drift? [Gap, Clarity, Spec §FR-001/§FR-014]
- [x] CHK007 Are the rules for `latest_checkpoint` path resolution specified — vault-relative? Absolute? POSIX separators on all platforms? [Gap, Clarity, Spec §FR-008]
- [x] CHK008 Is the canonical character-sanitization rule for the `<conv-id>` subdirectory specified identically to the Spec 004 cursor rule, with an explicit cross-reference rather than restatement? [Consistency, Spec §FR-001 vs Spec 004 cursor path rule]
- [x] CHK009 Are the rules for deterministic ordering inside cumulative lists (`conversations`, `exchange_indices`) specified — sorted? Insertion order? Is the ordering observable in the on-disk artifact? [Gap, Clarity, Spec §FR-009]
- [x] CHK010 Is the `_flat` sentinel collision case addressed — what is the contract if a real conversation identifier is literally `_flat`? [Gap, Edge Case, Clarifications 2026-06-28]

## Cumulative Merge Correctness

- [x] CHK011 Is the prior-`exchange_indices` lookup mechanism specified for the `exchange_count` cumulative computation (read from the prior `cp-<NNN>.json`? cached upstream? fallback when the prior pointer is broken or unreadable)? [Gap, Clarity, Spec §FR-009]
- [x] CHK012 Is `total_edits` defined unambiguously — does it count checkpoints in which Editor touched the page, OR Editor decisions, AND does a `"skipped"` action ever increment it? [Ambiguity, Spec §FR-009]
- [x] CHK013 Are the cross-conversation merge rules specified — when page X is touched by conversation A's `cp-002` then by conversation B's `cp-001`, what becomes `latest_checkpoint`, `latest_action`, `latest_confidence`? Is there a tie-breaker if both happen in the same wall-clock second? [Gap, Coverage, Spec §FR-009]
- [x] CHK014 Is the invariant "Editor emits at most one decision per page per checkpoint" stated as a requirement the orchestrator can rely on, OR is the orchestrator expected to defend against duplicates? [Ambiguity, Out of Scope item vs FR-005]
- [x] CHK015 Is the merge math measurable and verifiable from the spec alone (cumulative `exchange_count` after two checkpoints with overlapping indices: deterministic and stated)? [Measurability, Spec §FR-009]

## Atomicity & Persistence

- [x] CHK016 Is the atomic-write guarantee for the checkpoint JSON expressed as an observable contract ("no observable half-written file") rather than as an implementation hint? [Clarity, Spec §FR-001]
- [x] CHK017 Is the atomicity requirement for the page frontmatter merge specified — can a half-merged frontmatter ever be observed by a concurrent reader or by crash recovery? Or is partial frontmatter acceptable? [Gap, Spec §FR-008/§FR-011]
- [x] CHK018 Is the ordering between (a) JSON write, (b) frontmatter merge, (c) page snapshot, (d) git commit specified, with the partial-completion contract at each boundary explicit? [Gap, Clarity, Spec §FR-017]
- [x] CHK019 Is the requirement that the Spec 004 cursor advance MUST remain the terminal step (so a non-completing cursor implies provenance status is uncertain) specified? [Clarity, Spec §FR-017/§FR-019]

## Failure Mode Completeness

- [x] CHK020 Are requirements specified for every named failure mode with both observable behavior AND user-visible signal: `git` missing, commit fails, malformed YAML, payload validation fails, OS I/O error on JSON write? [Completeness, Spec §FR-010/§FR-015/§FR-016]
- [x] CHK021 Are the requirements for disk-full and permission-denied errors on JSON write explicit, or do they fall under FR-016's general clause? If the latter, is the umbrella explicit enough that it is unambiguous? [Ambiguity, Spec §FR-016]
- [x] CHK022 Are the requirements for a process kill (SIGTERM/SIGINT) mid-provenance-write specified — what is the recovery contract on the next invocation? Does the next checkpoint detect orphaned partial state? [Gap, Recovery Flow]
- [x] CHK023 Are the requirements for handling a `.history/` directory that exists but is NOT a git repository (e.g., user deleted `.git/` manually) specified? Re-init? Refuse? Skip? [Gap, Edge Case, Spec §FR-012]
- [x] CHK024 Are the requirements for handling a checkpoint subdirectory `<conv-id>/` that already contains a `cp-<NNN>.json` from a prior partial write specified — overwrite? refuse with error? skip? [Gap, Edge Case, Spec §FR-001]
- [x] CHK025 Are the requirements for handling a stale orphaned page snapshot in `.history/pages/<slug>.md` (left behind by a prior failed commit) specified — does the next commit sweep it up, ignore it, or error? [Gap, Recovery Flow, Spec §FR-016]
- [x] CHK026 Is the failure mode "user has manually committed inside `.history/` between InsightMesh runs" specified — supported, unsupported but non-destructive, or refused? [Coverage, Edge Case, Spec edge-case bullet]

## Run-Level Consequence Clarity

- [x] CHK027 Is the decoupling rule explicit — provenance failure MUST NOT block cursor advance AND MUST NOT change the run's exit code? [Clarity, Spec §FR-016/§FR-019]
- [x] CHK028 Are the exit-code semantics specified for every provenance failure path with explicit confirmation that exit 0 is correct even when provenance partially failed? [Gap, Clarity, Spec §FR-015/§FR-016]
- [x] CHK029 Is the contract that no provenance failure can ever propagate to fail the run testable from the spec alone (e.g., "any exception in the provenance step is caught at the orchestrator seam")? [Measurability, Spec §FR-016/§FR-019]

## Observability of Failures

- [x] CHK030 Are the stderr message formats for each failure path specified (prefix, naming convention, machine-greppability), or only described as "log a warning"? [Gap, Clarity, Spec §FR-010/§FR-015/§FR-016]
- [x] CHK031 Is the term "warning" used in stderr messages (FR-010, FR-015) defined consistently — is there a log-level requirement, a prefix requirement, or just "writes to stderr"? [Ambiguity, Spec §FR-010/§FR-015]
- [x] CHK032 Is there a requirement that lets a user inspecting only the on-disk artifacts (without the prior run's stderr) determine whether the corresponding git commit succeeded? Or is the orphaned-snapshot state silently indistinguishable from a successful one? [Gap, Observability]
- [x] CHK033 Is the requirement for surfacing prior provenance failures on subsequent invocations specified — does the next run detect orphaned state and surface it, or is each run isolated? [Gap, Recovery Flow]

## Edge Case Coverage

- [x] CHK034 Are concurrent-invocation failure modes specified beyond "not supported" — what actually happens when two processes race on the same conversation? Crash? Last-writer-wins? Detectable corruption? [Clarity, Assumptions section]
- [x] CHK035 Are the requirements for a page that exists in the vault but was authored by the user (no prior `provenance:` block) specified — initialize a fresh block? Skip? Refuse? [Coverage, Edge Case, Spec §FR-010 edge case bullet]
- [x] CHK036 Are the requirements for a page that has been deleted between Editor's write and the orchestrator's snapshot specified (e.g., user or another tool removed it in the brief window)? [Gap, Edge Case]

## Dependencies & Assumptions

- [x] CHK037 Is the assumption about echomine version pinning (1.5.0+) for `Conversation.models_used` and per-`Message.id` surfaces documented in the SPEC (not only in the plan)? [Gap, Assumptions section]
- [x] CHK038 Is the assumption documented that Editor's existing `EditorDecision` shape (action, confidence, rationale, exchange_indices, signals dict) is stable and that any change to it constitutes a breaking change for Spec 005? [Gap, Assumption, Spec Assumptions section]

## Notes

- "Unit tests for English" — each item asks whether the REQUIREMENT IS WELL-WRITTEN, not whether the code works. Check items off as you confirm the spec covers the question (or update the spec to do so).
- Items flagged `[Gap]` indicate the requirement may be present in design documents (data-model.md, contracts/, research.md) but absent from the user-facing requirements in `spec.md`. Where the gap matters for downstream readers and migration tools (anyone reading `cp-*.json` years from now), promote the rule into spec.md.
- This list intentionally goes deeper than `requirements.md`. Items that fail here are not blockers for `/speckit-implement` per se — they're "fix-before-merge-publishes-the-schema" items.
- Suggested workflow: if a checkbox fails, drop it into `/spec-gaps` to draft remediation text. Items 1–18 (durability) are highest leverage; items 20–33 (resilience) are second-highest.
