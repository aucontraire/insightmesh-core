# Research: Synthesis input hygiene — attachment and pasted text

Phase 0 decisions. Most of this was settled during a prior cross-provider research pass (real export data + web schemas + echomine 1.4.0 source) and the two clarification answers; consolidated here.

## Decision 1 — Representation: inline into message content

- **Decision**: Fold attachment `extracted_content` into the owning message's `content` as a labeled delimiter block, rather than adding a structured `attachments` field to the transcript models.
- **Rationale**: `transcript.Message.content` is `Field(min_length=1)`, so an attachment-only message (parser forces `content=""`) becomes a valid non-empty user turn with no model change, no change to `model_dump_json()` serialization in the orchestrator, and no change to the synthesis output contract. Minimal-Diff per the constitution.
- **Alternatives considered**: A structured `attachments` field on `Message`/`Exchange`. Rejected for now: it adds a new Pydantic submodel, changes the serialized transcript the orchestrator embeds, and forces the synthesis agent to learn a new input shape, all for the same text-into-synthesis outcome. Its only real advantage (first-class provenance) belongs with the future persistence / wiki-as-state work and is not foreclosed by inline.

## Decision 2 — Scope: text only (Claude side), images deferred

- **Decision**: Capture Claude attachment text (pasted text and uploaded-document text). Do not capture images/binaries; do not attempt ChatGPT uploaded-document text.
- **Rationale**: Cross-provider research established the asymmetry. Claude attachment text is recoverable via `metadata["attachments"][].extracted_content`. ChatGPT pasted text is already inline (captured today). ChatGPT uploaded-document text is not in the export (server-side). Images are the inverse problem (recoverable on ChatGPT via the asset resolver, lost on Claude) and a separate multimodal effort.
- **Alternatives considered**: Bundling ChatGPT image capture now. Rejected (Rule of Three / scope): provider-asymmetric, pulls in the binary-asset pipeline and the "knowledge capturer" product question.

## Decision 3 — Demarcation: labeled delimiter block (clarify Q1)

- **Decision**: Wrap attached text in a labeled block whose header identifies the source (filename when present, otherwise "pasted text"), appended after any typed text. The marker is internal and never appears in the final page.
- **Rationale**: Makes FR-003 (distinguishable), FR-008 (attribute by filename), and FR-010 (no markup leak) objectively testable, and gives the synthesis agent a clear signal that the block is user-provided source material.
- **Alternatives considered**: Unlabeled separator (no attribution possible); raw concatenation (indistinguishable). Both rejected as untestable against the acceptance criteria.

## Decision 4 — Large attachment handling: full text, no cap (clarify Q2)

- **Decision**: Include the full extracted text with no size cap.
- **Rationale**: Capping would re-introduce the silent loss this feature exists to remove. Very large inputs already fall under the documented long-chat token-limit limitation.
- **Alternatives considered**: Truncate beyond a threshold (re-introduces loss); warn on large attachments (adds scope without removing the underlying limitation). Deferred to future context-budget work.

## Decision 5 — Ordering: harvest before the empty-content/category skip

- **Decision**: Render attachment text before any `continue` that would drop a message.
- **Rationale**: Attachment-only messages arrive with `content=""` and category `attachment`; the current empty-content/category filter drops them before their metadata is read. Harvesting first is the only way attachment-only content survives. This is the "harvest before empty-skip" rule from the content-type spike.

## Reference — what the parser surfaces (echomine 1.4.0)

- Claude `Message.metadata["attachments"]` = list of `{file_name, file_type, file_size, extracted_content}`. `file_name` is empty for pasted text, a real name for uploaded documents.
- Non-conversational content is already categorized (`content_type_category`) and excluded; that behavior is preserved.
- No echomine change is required for this feature.
