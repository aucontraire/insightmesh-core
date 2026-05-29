# Contract: Attachment rendering (exports projection -> synthesis agent)

This is the internal contract between `src/exports.py` (which builds the transcript) and the `synthesis` sub-agent (which reads it). There is no user-facing CLI change in this feature.

## Rendered block format

When a message carries one or more attachments with non-empty `extracted_content`, each is rendered as a labeled block and appended after any typed text, separated by a blank line. Multiple attachments concatenate in source order.

Named attachment (uploaded document):

```
--- Attached/pasted content (file: report.pdf) ---
<extracted_content>
--- End attached content ---
```

Unnamed attachment (pasted text, `file_name == ""`):

```
--- Attached/pasted content (pasted text) ---
<extracted_content>
--- End attached content ---
```

A message with typed text plus one attachment yields:

```
<typed user text>

--- Attached/pasted content (pasted text) ---
<extracted_content>
--- End attached content ---
```

## Rules (mapped to FRs)

- The block header identifies the source: `file: <name>` when a filename exists, else `pasted text`. Filenames are never invented (FR-008).
- The markers (`--- Attached/pasted content ... ---` / `--- End attached content ---`) are internal. The synthesis agent MUST NOT reproduce them in the final page (FR-010).
- Attachment text is user-provided **source material**, not the assistant's words. Synthesis incorporates its substance; it does not quote a large block verbatim and does not let it dominate the page (FR-009).
- An attachment with empty/whitespace `extracted_content` produces no block (FR-004).

## Consumer expectation (synthesis agent)

The synthesis prompt is updated so the agent: recognizes the block as user-supplied source material, synthesizes its substance into the relevant topic page, should attribute facts to a named source in prose (for example, "according to the attached `report.pdf`"), and never emits the delimiter markers or invents filenames.
