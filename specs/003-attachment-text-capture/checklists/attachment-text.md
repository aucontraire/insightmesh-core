# Requirements Quality Checklist: Attachment & Pasted Text Capture

**Purpose**: Validate the quality, clarity, completeness, and consistency of the requirements in `spec.md` before task generation and implementation (unit tests for the requirements, not the code).
**Created**: 2026-05-29
**Feature**: [spec.md](../spec.md)
**Depth**: Standard | **Audience**: Author (pre-implementation self-check)

## Requirement Completeness

- [x] CHK001 - Are requirements defined for both unnamed pasted text and named uploaded-document text? [Completeness, Spec FR-001/FR-008]
- [x] CHK002 - Is the behavior for a message carrying multiple attachments specified (including ordering)? [Completeness, Spec US1 AC4]
- [x] CHK003 - Is the placement of attached text relative to typed text specified? [Completeness, Spec FR-003]
- [x] CHK004 - Is the handling of empty or whitespace-only extracted content specified? [Completeness, Spec FR-004]
- [x] CHK005 - Are requirements for the missing-content-type-category (older parser output) case documented? [Completeness, Spec FR-006]
- [x] CHK006 - Is the expectation that Claude messages WITHOUT attachments are unaffected stated, not just the ChatGPT no-regression case? [Gap]

## Requirement Clarity

- [x] CHK007 - Is the labeled-block demarcation defined precisely enough to verify (header content for named vs unnamed sources)? [Clarity, Spec FR-003]
- [x] CHK008 - Is "distinguishable" (typed vs attached text) given an objective, checkable criterion? [Clarity, Spec FR-003]
- [x] CHK009 - Is "treat as user-provided source material" expressed in observable terms rather than intent? [Clarity, Spec FR-009]
- [x] CHK010 - Is "does not dominate or swamp the page relative to the conversation" quantified, or is it knowingly left qualitative? [Ambiguity, Spec FR-009 / US2 AC3]
- [x] CHK011 - Is the internal markup that FR-010 forbids in output identified clearly enough to check for leakage? [Clarity, Spec FR-010]

## Requirement Consistency

- [x] CHK012 - Is FR-003 consistent with the demarcation answer recorded in Clarifications (Session 2026-05-29)? [Consistency, Spec §Clarifications / FR-003]
- [x] CHK013 - Is the "full text, no cap" decision consistent with FR-009 "does not swamp the page" (no contradiction between including everything and not dominating)? [Consistency, Spec FR-009 / §Assumptions]
- [x] CHK014 - Does FR-008 ("MAY attribute") conflict with the User Story 2 independent test, which states the named source is attributed (implying it is required)? [Conflict, Spec FR-008 / US2]
- [x] CHK015 - Is the attribution requirement consistent with the constitution's source-attribution principle? [Consistency, Spec FR-008]

## Acceptance Criteria Quality

- [x] CHK016 - Are SC-001 through SC-005 objectively measurable without implementation knowledge? [Measurability, Spec §Success Criteria]
- [x] CHK017 - Is SC-002's "~24,000 characters" tied to a specific, reproducible conversation so it is verifiable? [Measurability, Spec SC-002]
- [x] CHK018 - Does SC-003 define the comparison that establishes "same result as before" for ChatGPT (what is compared, against what baseline)? [Measurability, Spec SC-003]
- [x] CHK019 - Given FR-008 uses "MAY", is there a measurable acceptance criterion for attribution at all, or is it unverifiable by design? [Acceptance Criteria, Spec FR-008]

## Scenario Coverage

- [x] CHK020 - Is the attachment-only message (no typed text) scenario covered by an acceptance scenario? [Coverage, Spec FR-002 / US1 AC2]
- [x] CHK021 - Is the typed-text-plus-attachment scenario covered? [Coverage, Spec FR-003 / US1 AC3]
- [x] CHK022 - Is the no-regression scenario for inline-paste exports (ChatGPT) covered? [Coverage, Spec FR-007 / SC-003]
- [x] CHK023 - Is the exclusion of non-conversational content that also carries attachment metadata covered? [Coverage, Spec FR-005]

## Edge Case Coverage

- [x] CHK024 - Is whitespace-only (distinct from empty) extracted content explicitly addressed? [Edge Case, Spec FR-004]
- [x] CHK025 - Is the behavior of very large attachment text defined (rather than left undefined) including its relationship to the existing token-limit limitation? [Edge Case, Spec §Assumptions / Clarifications]
- [x] CHK026 - Are mixed messages (typed text + multiple attachments) addressed as a combined case? [Edge Case, Coverage]

## Non-Functional Requirements

- [x] CHK027 - Is the context-budget behavior for very large attachments a stated decision with a documented deferral, rather than an implicit assumption? [Non-Functional, Spec §Assumptions / Clarifications]

## Dependencies & Assumptions

- [x] CHK028 - Is the dependency on the parser surfacing attachment extracted content and filenames documented? [Assumption, Spec §Assumptions]
- [x] CHK029 - Is the scope assumption "attachment means text-bearing only; images excluded" explicit and reconciled with the Out of Scope section? [Assumption, Spec §Assumptions / Out of Scope]
- [x] CHK030 - Is the reuse of existing conversation pairing and non-conversational exclusion stated as an assumption/constraint? [Assumption, Spec §Assumptions]

## Boundary / Out of Scope

- [x] CHK031 - Are the exclusions (images/binaries, ChatGPT document text, frontmatter provenance, contradiction detection, Claude Artifacts) each explicitly bounded? [Coverage, Spec §Out of Scope]

## Notes

- Resolved via /spec-gaps (2026-05-29): CHK006 (added FR-011), CHK010 (FR-009 reworded to a qualitative guideline), CHK013 (Assumptions clarifies input vs output), CHK014/CHK015/CHK019 (FR-008 MAY to SHOULD, US2 aligned), CHK002 (US1 AC4 source order), CHK018 (SC-003 comparison defined). The remaining items were validated as already satisfied by the spec.
- Most items are requirements-writing checks; resolve by editing `spec.md` (not code) where a gap or ambiguity is confirmed.
