# Feature Specification: Chat-to-Wiki Batch Synthesis

**Feature Branch**: `001-chat-to-wiki-batch`  
**Created**: 2026-05-16  
**Status**: Draft  
**Input**: User description: "A user feeds an existing chat transcript into the system. The system processes exchanges sequentially, synthesizing knowledge into organized Obsidian wiki pages with cross-links. Includes structured session logging for agent evaluation and future database schema design."

## Clarifications

### Session 2026-05-16

- Q: What transcript input format should be supported initially? → A: JSON only (ChatGPT/Claude export format: array of message objects with role + content). Additional formats added when needed.
- Q: How does the system determine topic boundaries within a transcript? → A: LLM-determined. The Synthesis agent identifies topic shifts as it processes exchanges sequentially. No separate classification pass or user-marked delimiters.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Chat-to-Wiki Batch Synthesis (Priority: P1)

A user feeds an existing chat transcript (e.g., from EchoMine, ChatGPT export, or any conversational exchange) into the system. The system processes the exchanges sequentially, synthesizing the knowledge covered into organized Obsidian wiki pages. As it processes later exchanges, it checks for connections to pages it already created and cross-links them via `[[wiki links]]`. No interactive refinement, no web research — just organizing scattered conversational knowledge into a coherent, persistent wiki.

**Why this priority**: This is the simplest possible test of the core value proposition — turning scattered, sequential knowledge into organized, cross-linked wiki pages. It strips away all interactive complexity (no refinement dialogue, no web research, no live prompting) while exercising the three foundational agents: Synthesis, Historian, and Editor. If this works, every other story is the same pipeline with a different input source.

**Independent Test**: Feed a 20-exchange chat transcript about a topic (e.g., a conversation about the speed of light), verify that wiki pages are created in the Obsidian vault with coherent synthesis, frontmatter metadata, and `[[wiki links]]` between related pages.

**Acceptance Scenarios**:

1. **Given** a chat transcript with 10+ exchanges about a single topic, **When** the system processes it, **Then** it creates one or more wiki pages in the configured Obsidian vault with synthesized content, frontmatter (title, date, source), and a coherent narrative rather than a raw conversation dump
2. **Given** a chat transcript where later exchanges revisit or deepen an earlier topic, **When** the system processes those later exchanges, **Then** it cross-links the new page to the earlier page via `[[wiki links]]` and incorporates prior context into the new synthesis
3. **Given** a chat transcript covering multiple distinct topics, **When** the system processes it, **Then** it creates separate wiki pages per topic rather than one monolithic page, with cross-links where topics relate
4. **Given** an empty or malformed transcript file, **When** the system attempts to process it, **Then** it reports a clear error and does not create empty or broken wiki pages

---

### User Story 2 - Inquiry Session Logging (Priority: P1)

Every inquiry session is logged as a structured JSON file capturing the full pipeline: each agent's output, the final synthesis, and which wiki pages were created or updated. These logs serve as evaluation data for improving agent quality and as the schema blueprint for the production database.

**Why this priority**: Without logging, you can't evaluate whether agents are improving. You can't debug bad synthesis. And you can't design the production database schema from real data — you'd be guessing. This is the instrumentation that makes everything else improvable.

**Relationship to User Story 1**: Story 2 layers session logging onto Story 1's pipeline — it cannot be implemented before Story 1's orchestrator exists. Both stories ship together as the MVP. Story 1 alone produces wiki pages; Story 1 + Story 2 produces wiki pages with per-session evaluation data.

**Independent Test**: Run a batch synthesis, verify a JSON log file is written with timestamps, all agent inputs/outputs, and references to wiki pages affected.

**Acceptance Scenarios**:

1. **Given** the user completes a batch synthesis, **When** the session ends, **Then** a JSON log file is written to a configured logs directory with: timestamp, source transcript path, each agent's structured output, final synthesis text, and list of wiki pages created or updated
2. **Given** a batch synthesis fails partway through (e.g., an agent errors on a specific exchange), **When** the session is logged, **Then** the log captures which exchanges succeeded, which failed, and the error details
3. **Given** multiple sessions, **When** the user inspects the logs directory, **Then** each session has its own timestamped file and logs are valid, parseable JSON

---

### Edge Cases

- What happens when the Obsidian vault path is misconfigured or the disk is full?
- How does the system handle extremely long chat transcripts (thousands of exchanges)?
- What happens when the LLM rate limit is hit mid-batch?
- How does the system handle non-English content or mixed-language transcripts?
- How does the system handle a single exchange that spans multiple unrelated topics?
- What happens if the Synthesis agent fails entirely? (Abort batch, log error, no wiki pages written)
- What happens if the Historian agent fails? (Fallback: proceed to Editor without cross-link recommendations; wiki pages still written but without cross-links)
- What happens if the Editor agent fails on a specific page? (Skip that page, log error, continue with remaining pages)
- When is an exchange considered "successfully processed"? (When its content has been written to a wiki page by the Editor; intermediate completion in Synthesis or Historian does not count)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept a chat transcript file in JSON format (array of message objects with role and content fields, matching ChatGPT/Claude export format) and process it into wiki pages
- **FR-002**: System MUST synthesize conversational exchanges into coherent narrative content, not raw conversation dumps
- **FR-003**: System MUST create separate wiki pages when a transcript covers distinct topics, with topic boundaries determined by the Synthesis agent during sequential processing (no separate classification pass)
- **FR-004**: System MUST generate Obsidian-compatible markdown with proper frontmatter (title, date, source transcript, tags)
- **FR-005**: System MUST search existing wiki pages in the configured vault directory for related prior content before creating new pages. The search MUST include pages created in prior sessions AND pages created earlier in the current batch. Search criteria are determined by the Historian agent (Phase A: text-based matching on title and content keywords; Phase B: semantic search).
- **FR-006**: System MUST cross-link related wiki pages using `[[wiki links]]` syntax
- **FR-007**: System MUST update an existing wiki page (rather than create a duplicate) when the Editor agent determines that a new draft refers to the same topic as an existing page. The Editor considers three signals for similarity:
  - (a) **Normalized title match** (lowercase, articles stripped)
  - (b) **Frontmatter tag overlap** (set intersection of `tags` field)
  - (c) **Content keyword overlap** (LLM-judged)

  When confidence is low or signals conflict, the Editor MUST default to creating a new page rather than updating (reversible action preferred over destructive merge). When updating, the Editor MUST preserve any user-added content in the existing page and append new content in dedicated sections.
- **FR-008**: System MUST log every session as a structured JSON file with timestamps, agent inputs/outputs, and wiki pages affected
- **FR-009**: System MUST log each agent's output independently so agent quality can be evaluated per-agent
- **FR-010**: System MUST handle partial failures gracefully, logging which exchanges succeeded and which failed with error details
- **FR-011**: System MUST validate the configured vault path exists and is writable before processing
- **FR-012**: System MUST report clear errors for empty or malformed transcript files without creating broken wiki pages
- **FR-013**: System MUST distinguish recoverable agent failures (Historian failure → proceed without cross-links; Editor failure on a single page → skip that page) from non-recoverable failures (Synthesis failure → abort batch). All failure modes MUST be logged with sufficient detail to diagnose the cause.
- **FR-014**: For every page decision (create new vs. update existing), the Editor agent MUST log its reasoning: which similarity signals were used, what overlap was found per signal, the confidence level, and the final decision. This decision log is part of the session JSON log (per FR-008) and is the raw data used to refine the matching heuristic in future specs.

### Key Entities

- **Chat Transcript**: A JSON file containing an array of message objects (each with role and content fields), matching ChatGPT/Claude export format; represents sequential conversational exchanges to be processed
- **Wiki Page**: An Obsidian-compatible markdown file with frontmatter metadata, synthesized content, and cross-links to related pages; stored in the configured vault directory
- **Session Log**: A structured JSON file capturing the full processing pipeline for one batch run; includes timestamps, source path, per-agent outputs, synthesis results, and wiki page references
- **Exchange**: A single prompt/response pair within a transcript; the atomic unit of processing
- **Agent Output**: The structured result from a single agent (Synthesis, Historian, or Editor) for a given set of exchanges; logged independently for per-agent evaluation

### Agent Contracts

The system uses three sub-agents in a sequential pipeline. Each agent has a single responsibility and a defined input/output contract.

**Naming convention**: Use lowercase `synthesis`/`historian`/`editor` for code, JSON keys, and file names. Use capitalized `Synthesis`/`Historian`/`Editor` for prose references.

**Synthesis Agent**
- **Responsibility**: Read exchanges, identify topic boundaries, draft wiki page content for each topic.
- **Input**: Full transcript (array of Exchange objects).
- **Output**: List of WikiPageDraft objects, each containing: tentative title, list of exchange indices that contributed, draft markdown content, suggested tags.

**Historian Agent**
- **Responsibility**: For each WikiPageDraft, search the vault for related existing pages and produce cross-link recommendations.
- **Input**: Vault path + list of WikiPageDraft objects from Synthesis.
- **Output**: List of WikiPageDraft objects augmented with: list of related page titles, list of `[[wiki link]]` recommendations to insert.

**Editor Agent**
- **Responsibility**: Write final wiki pages to the vault, applying cross-links, generating frontmatter, and updating existing pages where appropriate.
- **Input**: List of augmented WikiPageDraft objects from Historian + vault path.
- **Output**: List of WikiPageResult objects with: file_path written, action ("created" or "updated"), final frontmatter, list of cross-links applied.

### Pipeline Coordination

- **Order**: Synthesis → Historian → Editor. Each agent runs once per batch and receives the prior agent's output.
- **Granularity**: Synthesis processes the full transcript at once (not exchange-by-exchange) to enable holistic topic boundary detection. Historian processes all drafts in one pass. Editor writes all pages in one pass.
- **State passing**: Intermediate outputs from each agent are persisted (as part of the session log) so each agent's contribution is independently inspectable and evaluable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A 20-exchange chat transcript about a single topic produces a wiki page within 2 minutes that a user can read and understand without referring back to the original conversation
- **SC-002**: A transcript covering 3+ distinct topics produces separate wiki pages for each topic, with cross-links between related topics
- **SC-003**: When processing a transcript that revisits an earlier topic, the system successfully links back to the prior page and incorporates its context in 100% of cases
- **SC-004**: Every completed session produces a valid JSON log file that captures all agent outputs and can be parsed without errors
- **SC-005**: If a batch fails mid-processing, the system recovers gracefully — successfully processed exchanges have their wiki pages preserved and the log captures the failure point

## Non-Goals

The following are explicitly out of scope:

- **Live interactive inquiry**: Real-time question-asking and question refinement are Spec 002. This spec covers batch processing of existing transcripts only.
- **Bias and assumption checking**: The Critic agent is Spec 003. No perspective analysis in this spec.
- **Web research**: The Researcher agent is Spec 003. No web search or external source fetching.
- **Source citations**: Formal citation tracking is Spec 003. Wiki pages attribute content to the source transcript but do not implement footnote-level citation.
- **Multi-user or team features**: No shared vaults, no collaboration, no user accounts. This is a personal knowledge tool.
- **Cloud hosting or SaaS deployment**: All data stays local. No server, no cloud storage, no remote processing.
- **Authentication and authorization**: Single-user local tool.
- **PDF or document export**: Obsidian handles export natively.
- **Usage metrics and analytics dashboards**: Build the product first, measure later.
- **Obsidian plugin development**: InsightMesh is a CLI tool that writes to the vault directory, not an Obsidian plugin.
- **Database or vector store**: Text search (grep/glob) is sufficient for this spec. PostgreSQL and pgvector are Phase B concerns, informed by the inquiry session logs this spec produces.
- **Speculative agent architecture**: No agents beyond the three needed for this spec (Synthesis, Historian, Editor). Additional agents are introduced in future specs only when needed.

## Assumptions

- The user has an existing Obsidian vault directory on their local machine
- Chat transcripts are in JSON format (array of message objects with role and content fields, matching ChatGPT/Claude export format); additional formats may be added in future specs
- The user has access to the Claude API (or will be using Claude Code sub-agents during Phase A prototyping)
- Internet connectivity is available for LLM API calls during processing
- The vault directory has sufficient disk space for generated wiki pages and log files
- Non-English transcripts are processed as-is; the system does not translate content but synthesizes in the language of the source material
- Transcript files fit in memory; streaming/chunking for very large files (10,000+ exchanges) is deferred to a future enhancement
- Claude Code sub-agents have access to file system tools (Read, Write, Glob, Grep) and can be invoked sequentially with file-based state passing. This is the foundation of the Phase A architecture.
- Agents are stateless within a session — each invocation receives explicit input and produces explicit output. The Historian receives Synthesis output as explicit input rather than relying on shared memory.
- Single-agent context limits are accommodated by transcript size assumptions (≤500 exchanges, ~50K tokens typical). Larger transcripts that exceed an agent's context window are out of scope for Spec 001 and may require chunked processing in a future spec.
- Topic boundary criteria emerge from the Synthesis agent's prompt and will be refined based on session log analysis. No formal classification algorithm is specified for Phase A.
- If the Synthesis agent cannot identify a coherent topic in a group of exchanges, it creates a wiki page with a placeholder title and a note flagging the ambiguity for user review.
