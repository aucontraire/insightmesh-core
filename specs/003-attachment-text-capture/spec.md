# Feature Specification: Synthesis input hygiene — attachment and pasted text

**Feature Branch**: `003-attachment-text-capture`
**Created**: 2026-05-28
**Status**: Draft
**Input**: User description: "Include attachment and pasted text from chat exports in synthesis. InsightMesh currently drops text the user pasted or attached. In Claude.ai exports this content lives in a message's attachments (with extracted text), separate from the typed body, and is never read, so the synthesized wiki is missing source material that is often the substance of the conversation. Verified on real exports: one conversation lost ~24,000 characters of pasted content; ~544,000 characters were dropped across four exports. ChatGPT keeps pasted text inline, so it is already captured; this feature targets the Claude-side gap. Text only; images and binaries are out of scope."

## Clarifications

### Session 2026-05-29

- Q: How should attached/pasted text be demarcated from typed text in the content synthesis receives? → A: Wrap it in a labeled block whose header identifies the source (the filename when present, otherwise "pasted text"), appended after any typed text; the internal markers never appear in the final page.
- Q: How should very large attachment text be handled relative to the synthesis context budget? → A: Include the full extracted text with no cap; very large attachments fall under the existing documented token-limit behavior, and size management is deferred to future context-budget work.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture pasted and attached text (Priority: P1)

When a user synthesizes a Claude.ai conversation in which they pasted a block of text or attached a document, the text they provided is included in the synthesis, so the resulting wiki page reflects the full substance of the conversation instead of silently dropping the source material.

**Why this priority**: This is the core data-completeness fix. Pasted and attached text is frequently the substance of a research or technical conversation. Without it, every synthesized page built from such a conversation is incomplete, and the loss is silent.

**Independent Test**: Synthesize a Claude conversation that contains a message with an attachment carrying extracted text, and confirm that text reaches the synthesized output. This alone delivers value (no more silent loss) and is the MVP.

**Acceptance Scenarios**:

1. **Given** a Claude conversation where a message has an attachment with non-empty extracted text, **When** the conversation is synthesized, **Then** the attached text is represented in the content that synthesis processes and in the resulting page.
2. **Given** an attachment-only message (the user pasted or attached content with no typed prose), **When** the conversation is synthesized, **Then** the message is not discarded and its attached text is included.
3. **Given** a message that has both typed text and an attachment, **When** the conversation is synthesized, **Then** both the typed text and the attached text contribute and remain distinguishable.
4. **Given** a message with multiple attachments, **When** the conversation is synthesized, **Then** all of their non-empty extracted text is included, in their original source order.

---

### User Story 2 - Treat attached content as attributable source material (Priority: P2)

The user wants pasted and attached content handled as user-provided source material: synthesized into coherent prose rather than reproduced verbatim, and attributed to its filename when one exists, so the page reads well and the user can tell what they supplied versus what the assistant said.

**Why this priority**: A refinement on top of P1. P1 ensures the text is present; P2 ensures it is incorporated well (readable, attributable, not swamping the page). The feature delivers value with P1 alone, but P2 is what makes the output trustworthy and legible.

**Independent Test**: Synthesize a conversation with one named document attachment and one separate unnamed paste, and confirm the page synthesizes both, attributes or clearly incorporates the named one, and does not reproduce either verbatim.

**Acceptance Scenarios**:

1. **Given** an attachment with a filename, **When** the conversation is synthesized, **Then** the page attributes or clearly incorporates the named source and treats the content as user-provided source material rather than the assistant's words.
2. **Given** an unnamed paste, **When** the conversation is synthesized, **Then** it is treated as pasted source material and no filename is invented.
3. **Given** a large pasted block, **When** the conversation is synthesized, **Then** it is synthesized into prose (not quoted verbatim), with the page not dominated by the attachment (a qualitative synthesis expectation, not a measured threshold).

---

### Edge Cases

- An attachment whose extracted text is empty or whitespace produces no empty or placeholder turn or page.
- ChatGPT exports, where pasted text is already inline with typed text, show no change in behavior (no regression).
- Non-conversational content (reasoning, tool output, system, media) remains excluded, even when such a message also carries attachment metadata.
- Exports parsed without a content-type category (older parser output) still include attachment text via a safe default (no regression).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST include the non-empty extracted text of a conversation message's attachments in the content presented to synthesis.
- **FR-002**: System MUST retain attachment-only messages (attached or pasted content with no typed body) rather than discarding them, contributing their attached text to synthesis.
- **FR-003**: When a message has both typed text and one or more attachments, the System MUST include both, with the attached text demarcated by a labeled block (header identifying the source filename, or "pasted text" when unnamed) appended after the typed text, so the two are distinguishable.
- **FR-004**: System MUST NOT produce an empty or placeholder turn or page for an attachment whose extracted text is empty or whitespace.
- **FR-005**: System MUST continue to exclude non-conversational content (reasoning, tool output, system, media) even when such a message carries attachment metadata.
- **FR-006**: System MUST include attachment text even when the source export lacks a content-type category, via a safe default that does not regress prior behavior.
- **FR-007**: System MUST preserve existing behavior for exports where pasted text is already inline (such as ChatGPT), introducing no change for those.
- **FR-008**: When an attachment has a filename, the synthesized page SHOULD attribute its content to that filename (in prose, for example "according to the attached `report.pdf`"); the System MUST NOT invent filenames.
- **FR-009**: System MUST treat attached and pasted text as user-provided source material, synthesizing its substance rather than reproducing it verbatim, and SHOULD weight it by editorial judgment so the synthesized page is not dominated by a large attachment. The non-domination expectation is a qualitative synthesis guideline, not a measured threshold.
- **FR-010**: System MUST NOT surface any internal markup used to carry attachment text into the final synthesized prose.
- **FR-011**: System MUST NOT change the synthesized result for messages that carry no attachments; the feature affects only messages with attachment extracted text.

### Key Entities *(include if feature involves data)*

- **Attachment**: User-contributed content attached to or pasted into a conversation message. Has optional filename and extracted text. Pasted text appears as an attachment with no filename; an uploaded text document appears as an attachment with a filename plus extracted text.
- **Conversation message**: A single turn that may carry typed text, one or more attachments, or both.
- **Wiki page**: The synthesized output, which must reflect both conversational text and attached source material.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a Claude conversation that contains attachment extracted text, 100% of that non-empty extracted text is represented in the content synthesis receives (zero silent drops).
- **SC-002**: A conversation that previously produced a page missing pasted content now produces a page that reflects that content. On the verified real example, the ~24,000 previously dropped characters are included.
- **SC-003**: Synthesizing a ChatGPT conversation that contains no attachments yields the same flattened transcript content as before this feature (no change for the no-attachment case).
- **SC-004**: A conversation whose only non-typed content is non-conversational (reasoning, tool, system, media) yields none of that content in the synthesized output, even when those messages carry attachment metadata.
- **SC-005**: Attachments with empty or whitespace extracted text add zero turns and zero pages.

## Assumptions

- The export-parsing layer surfaces each attachment's extracted text and, when present, its filename. (This is available today.)
- "Attachment" in scope means text-bearing content: Claude pasted text (attachment with no filename) and Claude uploaded-document text (attachment with filename and extracted text).
- Prose-quality handling for User Story 2 (synthesize rather than quote, attribute, do not swamp) is enforced by the synthesis step; this spec sets the expectation and the acceptance scenarios verify the observable outcome.
- Existing conversation pairing and the existing exclusion of non-conversational content are reused; this feature changes only what attached text is carried in, not how turns are paired.
- Attachment extracted text is included in full with no size cap; managing very large inputs against the context budget is deferred (very large attachments fall under the existing long-chat token-limit limitation). This does not conflict with FR-009: the full text is provided to synthesis as input, while FR-009 constrains the synthesized page (output); the two operate at different layers.

## Out of Scope

- Capturing images or other binaries. Claude exports do not include image binaries (unrecoverable from the export); ChatGPT image binaries are recoverable but belong to a separate multimodal effort.
- ChatGPT uploaded-document **content**. Document text lives on OpenAI's servers and is not recoverable from the export (research confirms no official API path; the Files API and ChatGPT are separate systems, and the export's internal `file-service://` / `sediment://` IDs do not resolve through any supported channel). ChatGPT attachment **metadata** (filename, mime type, size, file ID) **is** present in the export but is not currently surfaced by the upstream parser; surfacing it — so a page can note "the user uploaded `resume.pdf`" even without the content — is a small follow-up gated on an echomine update, not on this spec.
- Recording per-source provenance (contributing filenames) in wiki page frontmatter. This depends on a structured representation and belongs with the future persistence / wiki-as-state work.
- Contradiction or conflict detection when attached content disagrees with an existing page. This is a separate, already-planned capability; the existing conservative, additive, create-when-uncertain update behavior remains the interim default.
- Capturing Claude Artifacts (side-panel tool output blocks).
