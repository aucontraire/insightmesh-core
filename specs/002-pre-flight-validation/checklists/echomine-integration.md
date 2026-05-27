# EchoMine Integration Checklist: Pre-flight Validation

**Purpose**: Validate that the spec's EchoMine integration requirements are complete, clear, consistent, and rigorous enough for an implementer with no clarification context to build from. Emphasizes the **error translation contract** between EchoMine's exception taxonomy and InsightMesh's user-facing error messages (Q3 focus from the clarify session).

**Created**: 2026-05-24
**Feature**: [spec.md](../spec.md)
**Audience**: Implementer (someone other than the spec author building this)
**Focus**: External dependency integration quality, with emphasis on error translation
**Source clarify answers**: Q1=C (EchoMine integration), Q2=C (implementer), Q3=C (error translation emphasis)

> These items test the **requirements**, not the implementation. Each asks whether the spec is precise, complete, and unambiguous enough to implement without re-running the clarify cycle.

---

## Requirement Completeness

- [x] CHK001 Is the full set of EchoMine exception types InsightMesh must handle (`EchomineError`, `ParseError`, `ValidationError`, `SchemaVersionError`) enumerated in the spec body, or only in `data-model.md`? [Gap, Completeness]
- [x] CHK002 Are the specific EchoMine public-API symbols InsightMesh depends on (`ClaudeAdapter`, `OpenAIAdapter`, `Conversation`, `Message`, exception types) enumerated in the spec body? [Gap, Spec §FR-023]
- [x] CHK003 Is behavior specified when EchoMine is not installed at all (e.g., user forgot `uv sync` after pulling)? [Gap]
- [x] CHK004 Is behavior specified when EchoMine is installed at a version that does not satisfy `>=1.3.0`? [Gap, Spec §FR-023]
- [x] CHK005 Is the policy for handling EchoMine bugs discovered during integration (file upstream issue vs work around in InsightMesh) documented? [Gap]
- [x] CHK006 Are requirements specified for what happens when EchoMine yields zero conversations during a `batch --conversation` invocation? (FR-006 covers `list`; the `batch` path is implicit.) [Coverage, Gap]

## Requirement Clarity

- [x] CHK007 Is "delegate to EchoMine" (FR-023) precisely scoped — which EchoMine methods are consumed, and which exceptions are caught at the boundary vs propagated? [Clarity, Spec §FR-023]
- [x] CHK008 Is the mapping from EchoMine `Message` fields (`role`, `content`) to InsightMesh's internal `{role, content}` shape explicitly specified, for both Claude and OpenAI adapter outputs? [Clarity, Gap]
- [x] CHK009 Is the rule for walking the canonical thread (root → `current_node` for ChatGPT) specified in the spec body, or does it live only in `research.md`/`data-model.md`? [Ambiguity, Spec body vs research.md R3]
- [x] CHK010 Is the EchoMine adapter selection rule (try Claude first, fall back to OpenAI, raise `UnrecognizedExportFormat`) explicit in the spec body, or only in `research.md` R7? [Clarity, Spec body vs research.md R7]
- [x] CHK011 Is the term "schema drift" (used in FR-021 and the Assumptions section) defined precisely enough that an implementer knows whether EchoMine's behavior satisfies it? [Clarity, Spec §FR-021]

## Requirement Consistency

- [x] CHK012 Is the FR-007 "not a recognized export format" error message consistent with the `UnrecognizedExportFormat` exception's translation defined in `data-model.md`? [Consistency, Spec §FR-007 vs data-model.md]
- [x] CHK013 Is FR-019's "stderr only, no `.logs/` write" rule consistent with how `contracts/cli-commands.md` treats EchoMine-derived errors during `list`/`batch` (which are not pre-flight errors)? [Consistency, Spec §FR-019]
- [x] CHK014 Is the FR-021 silent-ignore rule consistent with EchoMine's *actual* behavior on extra fields — has the spec verified that EchoMine ignores extras rather than raising, or is this an unvalidated assumption? [Consistency, Spec §FR-021 vs Assumption]
- [x] CHK015 Are the EchoMine type imports listed in `data-model.md` "External types from echomine" consistent with the integration sketch in `research.md` R7? (No symbols referenced in one but missing from the other.) [Consistency, data-model.md vs research.md R7]

## Error Translation Contract (Q3 emphasis)

- [x] CHK016 Is the full mapping of EchoMine exception types → user-facing InsightMesh error messages specified in the spec body, not only hinted at in `research.md` R2/R3? [Gap, Q3 emphasis]
- [x] CHK017 Does the spec require that EchoMine exceptions be chained at the boundary (e.g., `raise UnrecognizedExportFormat(...) from echomine_exc`) so the original cause survives for debugging? [Gap, Q3 emphasis]
- [x] CHK018 Is the spec explicit about which EchoMine exception details propagate verbatim to the user (e.g., JSON parse error line numbers) versus which are summarized into a more user-friendly message? [Gap, Q3 emphasis]
- [x] CHK019 Are requirements specified for EchoMine's `on_skip` callback during streaming — does InsightMesh aggregate skipped conversations, surface them in stderr, or silently drop them? [Gap, Q3 emphasis]
- [x] CHK020 Is the error-message format for EchoMine-derived errors visually distinguishable from both pre-flight errors (FR-019 `error: pre-flight checks failed:`) and Spec 001 in-pipeline errors (`error: pipeline failed:`)? [Gap, Q3 emphasis, Spec §FR-019]
- [x] CHK021 Is behavior specified when EchoMine raises `ParseError` mid-stream after yielding some valid conversations during `insightmesh list`? (Is the partial table flushed and an error reported, or is the whole listing aborted?) [Coverage, Q3 emphasis, Gap]
- [x] CHK022 Are requirements specified for what users see when EchoMine raises an exception type not enumerated in InsightMesh's translation table (forward-compat with future EchoMine exception classes)? [Coverage, Q3 emphasis, Gap]

## Acceptance Criteria Quality / Measurability

- [x] CHK023 Can FR-023's "MUST NOT implement, fork, or duplicate adapter logic" be verified by inspection — does the spec describe a concrete test or audit procedure that would catch a future regression reintroducing hand-rolled parsing? [Measurability, Spec §FR-023, Gap]
- [x] CHK024 Are the Independent Test descriptions in User Story 1 and User Story 2 specific enough that an implementer without clarification context can reproduce them on a real Claude.ai or ChatGPT export? [Measurability, Spec §User Story 1, Spec §User Story 2]
- [x] CHK025 Is the SC-002 5-second performance budget validated against EchoMine's streaming overhead (ijson-based) on a realistic 5,000-conversation fixture, or does the spec assume EchoMine's streaming will trivially satisfy it? [Measurability, Spec §SC-002, Assumption]

## Scenario Coverage

- [x] CHK026 Are requirements specified for: an export file where EchoMine adapter detection succeeds for *both* `ClaudeAdapter` and `OpenAIAdapter` (file has signatures of both)? [Coverage, Gap]
- [x] CHK027 Are requirements specified for: EchoMine yields a `Conversation` whose canonical thread walk produces zero user/assistant messages (all nodes are system/tool roles)? [Coverage, Gap]
- [x] CHK028 Are requirements specified for: a ChatGPT export with a malformed message tree (no `current_node`, or unreachable node ids from `current_node`)? [Coverage, Gap]

## Edge Case Coverage

- [x] CHK029 Is behavior specified when the user has both a PyPI `echomine` install AND a local editable install (e.g., from `~/PycharmProjects/echomine`) resolving to different versions? [Edge Case, Gap]
- [x] CHK030 Is behavior specified when EchoMine accepts the file but a later breaking schema change has occurred upstream (i.e., a transient compatibility window between EchoMine versions)? [Edge Case, Gap]

## Non-Functional Requirements

- [x] CHK031 Are memory bounds specified when EchoMine streams a multi-gigabyte export? The spec's "up to 10,000 conversations fits in memory" assumption appears to conflict with EchoMine's streaming-first design — which constraint actually governs? [Consistency, Spec Assumptions vs research.md R7]
- [x] CHK032 Are requirements specified for graceful interrupt (Ctrl+C / SIGINT) mid-stream, such that EchoMine's generator is closed cleanly and any partial output is either flushed or discarded predictably? [Coverage, Gap]

## Dependencies & Assumptions

- [x] CHK033 Is the EchoMine version pin policy specified beyond `>=1.3.0` — is there an upper bound (`<2.0.0`?), an upgrade cadence, or a compatibility-break response plan? [Gap, Spec §FR-023]
- [x] CHK034 Are EchoMine's transitive dependencies (`ijson`, `structlog`, `python-slugify`, `python-dateutil`) audited for compatibility with InsightMesh's existing dependency tree? [Assumption, Gap]
- [x] CHK035 Is the assumption that EchoMine is available on PyPI documented in the spec body, not only in `research.md` R7? (Future readers of just the spec shouldn't have to chase down the PyPI claim.) [Assumption, Gap]

---

## Notes

- This checklist has **35 items**, all with traceability references (100% above the 80% minimum).
- **Item distribution**: Requirement Completeness (6), Clarity (5), Consistency (4), Error Translation Contract — Q3 emphasis (7), Acceptance Criteria Quality (3), Scenario Coverage (3), Edge Case Coverage (2), Non-Functional (2), Dependencies & Assumptions (3).
- **Anticipated outcome**: many `[Gap]`-tagged items will fail on first pass. That's the point — the spec was drafted, then EchoMine integration was added in a single revision pass yesterday; this checklist surfaces the resulting under-specification before `/speckit-tasks` locks it in.
- **Resolution path**: items that fail should be addressed by either (a) tightening the spec via `/spec-gaps` or a direct edit, or (b) explicitly documenting a deferred decision in `## Notes` here with a rationale.
- Items checked off as `[x]`; failing items can be annotated inline with the gap they reveal.
- After resolution, re-run `/speckit-analyze` to verify cross-document consistency before `/speckit-tasks`.
