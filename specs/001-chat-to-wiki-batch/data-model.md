# Data Model: Chat-to-Wiki Batch Synthesis

**Feature**: `001-chat-to-wiki-batch` | **Date**: 2026-05-16

## Implementation Note

All entities below are implemented as **Pydantic v2 `BaseModel` subclasses** (per constitution v1.1.0 Project Standards), not plain dataclasses. This gives us:
- Native JSON serialization (`model_dump_json()`) for session logs
- Native JSON parsing/validation (`model_validate_json()`) for transcript input
- JSON Schema generation (`model_json_schema()`) for `claude-agent-sdk` structured output
- Strict type enforcement at runtime

## Entities

### ChatTranscript

A parsed representation of the input JSON file.

| Field | Type | Description |
|-------|------|-------------|
| source_path | string | Absolute path to the original transcript file |
| exchanges | list[Exchange] | Ordered list of conversational exchanges (prompt/response pairs) |
| metadata | dict | Optional metadata from the transcript file (title, date, etc.) |

**Validation rules**:
- source_path must exist and be readable
- exchanges must contain at least 1 exchange
- Each exchange must have at least a user_message

### Exchange

A single prompt/response pair — the atomic unit of processing. One Exchange represents one conversational turn (one user message + the assistant's response to it).

| Field | Type | Description |
|-------|------|-------------|
| index | int | Position in the transcript (0-based pair index) |
| user_message | Message | The user's prompt for this turn |
| assistant_message | Message \| None | The assistant's response; None only if the transcript ends with an unanswered user message |

**Validation rules**:
- user_message is required (an Exchange always starts with a user prompt)
- assistant_message may be None only for the final exchange when the transcript ends mid-turn
- index must be a non-negative integer matching the exchange's position in the list

### Message

The atomic unit of communication — one message from one role. Multiple Messages are paired into Exchanges by the parser.

| Field | Type | Description |
|-------|------|-------------|
| role | string | "user" or "assistant" (other roles normalized to "assistant" during parsing) |
| content | string | The message text |
| timestamp | string (optional) | ISO 8601 timestamp if present in source |

**Validation rules**:
- role must be "user" or "assistant"
- content must be a non-empty string
- Unknown roles in source JSON (system, tool, function, etc.) are normalized to "assistant"

### Pairing Rules (Parser Behavior)

The parser converts a flat JSON message array into Exchange pairs using these rules:

1. **Standard alternating**: user → assistant → user → assistant → ... pairs naturally into Exchanges by consecutive pairs
2. **Leading assistant messages**: if a transcript starts with one or more assistant messages (before any user message), they are skipped with a warning (orphan assistant messages have no triggering prompt)
3. **Consecutive user messages**: if two user messages appear without an intervening assistant response, the earlier one is treated as a standalone Exchange with assistant_message=None
4. **Consecutive assistant messages**: concatenated into a single assistant_message (separated by `\n\n`) for the most recent user message — Claude's "continued thinking" splits should not create artificial Exchange boundaries
5. **Trailing user message**: if the transcript ends with a user message that has no response, it becomes the final Exchange with assistant_message=None
6. **Normalized roles**: system/tool/function messages are normalized to "assistant" role before pairing (per Message validation)

### WikiPage

An Obsidian-compatible markdown file with frontmatter.

| Field | Type | Description |
|-------|------|-------------|
| title | string | Page title (also used as filename) |
| file_path | string | Absolute path in the vault |
| content | string | Synthesized markdown content |
| frontmatter | dict | YAML frontmatter (title, date, source, tags) |
| cross_links | list[string] | Titles of related pages linked via `[[wiki links]]` |
| created_at | string | ISO 8601 timestamp of creation |
| updated_at | string | ISO 8601 timestamp of last update |

**Frontmatter structure**:
```yaml
---
title: "Speed of Light"
created: 2026-05-16T17:52:40Z
updated: 2026-05-16T17:52:40Z
source: "path/to/transcript.json"
tags:
  - insightmesh
  - physics
---
```

`created` and `updated` are ISO 8601 UTC datetimes (Z form, seconds precision). On creation they match. On update, `created` is preserved from the prior version and `updated` is bumped to the current batch timestamp.

### SessionLog

A structured JSON file capturing the full processing pipeline for one batch run.

| Field | Type | Description |
|-------|------|-------------|
| session_id | string | Unique identifier (timestamp-based) |
| timestamp | string | ISO 8601 start time |
| source_transcript | string | Path to input transcript |
| exchanges_total | int | Total exchanges in transcript |
| exchanges_processed | int | Number successfully processed |
| agents | dict[str, AgentOutput] | Per-agent outputs keyed by agent name |
| wiki_pages_created | list[string] | Paths of new pages |
| wiki_pages_updated | list[string] | Paths of updated pages |
| cross_links | list[CrossLinkRecord] | Cross-link relationships created (typed; see below) |
| status | string | "completed" or "partial_failure" |
| errors | list[SessionError] | Error details if any (typed; see below) |
| duration_seconds | float | Total processing time |

### CrossLinkRecord

One cross-link relationship recorded in the session log.

| Field | Type | Description |
|-------|------|-------------|
| from_page | string | Title of the page containing the link |
| to_page | string | Title of the page being linked to |
| display_text | string \| None | Alias text if the link uses `[[to_page\|display_text]]` form |

### SessionError

One error captured during a batch run.

| Field | Type | Description |
|-------|------|-------------|
| agent | string | Which sub-agent encountered the error ("synthesis", "historian", "editor") |
| error_type | string | Short categorization (e.g., "rate_limit", "parse_error", "connection_refused") |
| message | string | Full error message for diagnostics |

### AgentOutput

The structured result from a single agent invocation.

| Field | Type | Description |
|-------|------|-------------|
| agent_name | string | One of: "synthesis", "historian", "editor" (lowercase, canonical) |
| input_summary | string | Brief description of what was passed to the agent |
| output | SynthesisOutput \| HistorianOutput \| EditorOutput | Agent-specific structured output (see below) |
| duration_seconds | float | Time taken for this agent |
| status | string | "success" or "error" |
| error_detail | string (optional) | Error message if status is "error" |

### SynthesisOutput

| Field | Type | Description |
|-------|------|-------------|
| drafts | list[WikiPageDraft] | List of draft wiki pages identified from exchanges |

### HistorianOutput

| Field | Type | Description |
|-------|------|-------------|
| augmented_drafts | list[WikiPageDraft] | Drafts augmented with related page titles and cross-link recommendations |

### EditorOutput

| Field | Type | Description |
|-------|------|-------------|
| results | list[WikiPageResult] | List of wiki page write results (created or updated) |
| decisions | list[EditorDecision] | Per-page create-vs-update decisions with reasoning (per FR-014) |

### EditorDecision

The Editor's reasoning for each create-or-update choice. Captured for future heuristic refinement.

| Field | Type | Description |
|-------|------|-------------|
| draft_title | string | Title of the WikiPageDraft being decided on |
| action | string | "created", "updated", or "skipped" (skipped = per-page failure per FR-013) |
| candidate_existing_page | string \| None | Title of the existing page considered for update (None if no candidates) |
| signals | EditorDecisionSignals | Per-signal evaluation (see below) |
| confidence | string | "high", "medium", or "low" |
| rationale | string | Brief LLM-generated explanation of the decision (for "skipped", the skip reason) |
| exchange_indices | list[int] | Indices of the source transcript exchanges this decision covered (forwarded from the input WikiPageDraft). Enables FR-010 "which exchanges succeeded" computation in SessionLog. |

Note: A draft that fails to write (e.g., MCPVault error, transient failure) gets `action: "skipped"` and is recorded in `EditorOutput.decisions` with the error reason in `rationale`. Skipped drafts do NOT appear in `EditorOutput.results` — only successfully-written pages do.

### EditorDecisionSignals

| Field | Type | Description |
|-------|------|-------------|
| normalized_title_match | bool | Whether normalized titles matched |
| tag_overlap_count | int | Number of frontmatter tags shared with the candidate |
| tag_overlap_tags | list[string] | The specific overlapping tags |
| content_keyword_overlap | string | LLM-judged overlap description ("strong", "partial", "weak", "none") |

### WikiPageDraft

A pre-write representation produced by Synthesis and augmented by Historian.

| Field | Type | Description |
|-------|------|-------------|
| tentative_title | string | Proposed page title (may be refined by Editor) |
| exchange_indices | list[int] | Indices of exchanges that contributed to this draft |
| draft_content | string | Draft markdown content |
| suggested_tags | list[string] | Suggested tags for frontmatter |
| related_pages | list[string] (optional) | Added by Historian: related existing page titles |
| crosslink_recommendations | list[string] (optional) | Added by Historian: `[[wiki link]]` references to insert |

### WikiPageResult

The Editor's record of a write operation.

| Field | Type | Description |
|-------|------|-------------|
| file_path | string | Absolute path written |
| action | string | "created" or "updated" |
| final_frontmatter | dict | Frontmatter as written |
| crosslinks_applied | list[string] | List of `[[wiki link]]` references actually inserted |

## Relationships

```
ChatTranscript 1──* Exchange
Exchange 1──1 Message (user_message)
Exchange 1──0..1 Message (assistant_message)
SessionLog 1──* AgentOutput
SessionLog 1──* CrossLinkRecord
SessionLog 1──* SessionError
SessionLog 1──* WikiPage (created/updated)
WikiPage *──* WikiPage (cross-links)

AgentOutput (agent_name="synthesis") ──> SynthesisOutput 1──* WikiPageDraft
AgentOutput (agent_name="historian") ──> HistorianOutput 1──* WikiPageDraft (augmented)
AgentOutput (agent_name="editor")    ──> EditorOutput 1──* WikiPageResult
                                                       1──* EditorDecision
EditorDecision 1──1 EditorDecisionSignals
WikiPageResult 1──1 WikiPage
```

## State Transitions

### Session Processing Pipeline

```
INITIALIZED → PARSING → SYNTHESIZING → SEARCHING_VAULT → EDITING_PAGES → LOGGING → COMPLETED
                                                                                  → PARTIAL_FAILURE
```

- PARSING: Transcript JSON parsed into Message objects, then paired into Exchange objects
- SYNTHESIZING: Synthesis agent processes exchanges, identifies topics
- SEARCHING_VAULT: Historian agent searches vault for related existing pages
- EDITING_PAGES: Editor agent writes/updates wiki pages with cross-links
- LOGGING: Session log written to disk
- COMPLETED: All exchanges processed successfully
- PARTIAL_FAILURE: Some exchanges failed, partial results preserved
