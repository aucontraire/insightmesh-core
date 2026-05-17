# Agent Pipeline Requirements Quality Checklist

**Purpose**: Validate that agent responsibilities, contracts, and coordination requirements are complete, clear, and implementable  
**Created**: 2026-05-16  
**Feature**: [spec.md](../spec.md)  
**Audience**: Self-review before implementation  
**Focus**: Agent pipeline — Synthesis, Historian, Editor contracts and handoffs

## Requirement Completeness

- [x] CHK001 - Are the specific responsibilities of each agent (Synthesis, Historian, Editor) defined individually in the spec? [Completeness, Gap]
- [x] CHK002 - Is the expected input format for the Synthesis agent specified (raw exchanges? grouped? full transcript at once?)? [Completeness, Gap]
- [x] CHK003 - Is the expected output format of the Synthesis agent defined (structured JSON schema? prose? list of topic-page pairs?)? [Completeness, Gap]
- [x] CHK004 - Is the expected input for the Historian agent specified (what does it receive — Synthesis output, vault path, both?)? [Completeness, Gap]
- [x] CHK005 - Is the expected output of the Historian agent defined (list of related pages? cross-link map? relevance scores?)? [Completeness, Gap]
- [x] CHK006 - Is the expected input for the Editor agent specified (Synthesis output + Historian output? just Synthesis?)? [Completeness, Gap]
- [x] CHK007 - Is the expected output of the Editor agent defined (file paths written? content returned? confirmation?)? [Completeness, Gap]
- [x] CHK008 - Are the criteria for "topic boundary" that the Synthesis agent uses to split pages documented beyond "LLM-determined"? [Completeness, Spec §FR-003] — Intentionally left flexible per anti-slop principle; criteria emerge from agent prompt and session log analysis (documented in Assumptions).

## Requirement Clarity

- [x] CHK009 - Is the sequential ordering of agents (Synthesis → Historian → Editor) explicitly stated in the spec, or only implied? [Clarity, Gap]
- [x] CHK010 - Is "update existing wiki pages" (FR-007) clear about what triggers an update vs. creating a new page? [Clarity, Spec §FR-007]
- [x] CHK011 - Is the granularity of processing defined — does Synthesis process one exchange at a time, batches, or the full transcript? [Clarity, Gap]
- [x] CHK012 - Is "search existing wiki pages" (FR-005) defined with specific search criteria (title match? content keywords? tags?)? [Clarity, Spec §FR-005]
- [x] CHK013 - Is it clear whether Historian searches only pages created in THIS session or also pre-existing vault pages? [Clarity, Spec §FR-005]

## Scenario Coverage

- [x] CHK014 - Are requirements defined for what happens when the Historian finds no related prior pages? [Coverage, Edge Case] — Implied by Historian contract; pages still written without cross-links.
- [x] CHK015 - Are requirements defined for what happens when the Synthesis agent cannot determine a coherent topic from exchanges? [Coverage, Edge Case]
- [x] CHK016 - Are requirements defined for the Editor agent's behavior when a wiki page with the same title already exists in the vault? [Coverage, Edge Case]
- [x] CHK017 - Is the behavior specified when two exchanges in the same transcript produce wiki pages that should be the same topic? [Coverage, Spec §FR-003] — Editor merges per FR-007 update rule.
- [x] CHK018 - Are requirements defined for agent retry behavior if an individual agent call fails (retry? skip? abort batch?)? [Coverage, Gap]

## Agent Failure & Recovery

- [x] CHK019 - Is it specified which agent failures are recoverable vs. which abort the full batch? [Coverage, Spec §FR-010]
- [x] CHK020 - If the Historian fails, is the fallback behavior defined (skip cross-linking? proceed without prior context?)? [Edge Case, Gap]
- [x] CHK021 - If the Editor fails to write a page, are requirements defined for what gets logged and whether processing continues? [Edge Case, Gap]
- [x] CHK022 - Is the partial failure state defined — at what point are "successfully processed exchanges" considered complete (after Synthesis? after Editor writes?)? [Clarity, Spec §SC-005]

## Consistency

- [x] CHK023 - Does the data model's AgentOutput.output field (typed as generic `dict`) align with the need for per-agent structured evaluation? [Consistency, data-model.md §AgentOutput]
- [x] CHK024 - Are the agent names consistent across spec, data model, and plan ("synthesis"/"historian"/"editor" vs "Synthesis"/"Historian"/"Editor")? [Consistency]
- [x] CHK025 - Does the plan's "file-based state passing" between agents conflict with any spec requirements about agent coordination? [Consistency, plan.md] — No conflict; spec now explicitly states intermediate outputs are persisted.

## Dependencies & Assumptions

- [x] CHK026 - Is the assumption that agents are stateless explicitly validated against the need for Historian to know what Synthesis produced? [Assumption]
- [x] CHK027 - Is the dependency on Claude Code's Agent tool capabilities documented as an assumption (can agents read/write files, search vault)? [Dependency, Gap]
- [x] CHK028 - Are token/context limits addressed — what happens if a transcript is too large for a single agent prompt? [Dependency, Edge Case]

## Notes

- Focus: Agent pipeline contracts, handoffs, and failure modes
- Depth: Standard (self-review)
- All 28 items resolved via spec-gaps run on 2026-05-16
- Key additions: Agent Contracts section, Pipeline Coordination section, FR-013 (recoverable/non-recoverable failures), expanded Edge Cases, expanded Assumptions
- Data model: AgentOutput.output now typed as SynthesisOutput | HistorianOutput | EditorOutput; new entities WikiPageDraft and WikiPageResult added
- One intentional non-resolution: CHK008 (topic boundary criteria) kept flexible per anti-slop — to be refined from session log data
