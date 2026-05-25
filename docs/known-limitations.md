# Known Limitations

This is the honest list. Phase A (Spec 001) shipped a working pipeline, but several things don't work, are slow, or aren't built yet. They're documented here so users can plan around them and so future work knows what to prioritize.

---

## Performance

### SC-001 timing — 2x over budget

**What's wrong**: The spec target was *under 2 minutes* for a 20-exchange transcript. Actual runtime is consistently **260–294 seconds (~4-5 minutes)**.

**Why**: ~40% of runtime is the main Claude orchestrator's reasoning between agent invocations — not the sub-agents themselves. The per-agent breakdown:

| Agent | Typical duration | Share |
|-------|------------------|-------|
| Synthesis | 27-40s | ~15% |
| Historian | 55-106s | ~25-35% (scales with vault size) |
| Editor | 55-61s | ~22% |
| **Orchestrator overhead** | **75-200s** | **~30-70%** |

**How to mitigate today**: nothing in-product. Plan your runs accordingly.

**Planned fixes** (Spec 002+):

1. Pass transcript by file path instead of inline JSON in the orchestrator prompt
2. Migrate orchestration from main-Claude-as-orchestrator to deterministic Python via LangGraph (Phase B)
3. Use Haiku for Synthesis (~2-3x faster than Sonnet, may be adequate for batch JSON output)
4. Stream Editor writes to start before Historian fully finishes

### Cost scales with conversation length

Every run calls Claude API multiple times. There's no caching across runs. A 200-exchange chat costs ~$1-3 in API spend. A 500-exchange chat may hit per-agent token limits before completing.

**How to mitigate today**: start with short conversations. Check the session log's `duration_seconds` field to calibrate expectations before running larger inputs.

---

## UX gaps

### Missing-agent silent degradation

**What's wrong**: If one of the three sub-agents (`synthesis.md`, `historian.md`, `editor.md`) is missing from `.claude/agents/`, the main Claude orchestrator gracefully skips it without raising an error. The pipeline reports `status: "completed"` with `errors: []` even though a step was silently omitted.

**Why it's technically correct**: An agent that doesn't exist isn't "errored" — it just wasn't invoked. The session log accurately reflects what ran.

**Why it's a problem**: Users can't tell from the CLI output that they got a degraded result. The only signal is fewer cross-links than expected.

**Planned fix** (Spec 002 or 001.1): pre-flight agent presence check in the CLI; warn loudly to stderr if any expected agent is missing.

### Historian doesn't always recommend reverse cross-links

When the pipeline updates an existing page (e.g., re-running on the same topic), the Historian agent sometimes doesn't recommend cross-links back to related pages in the vault — even when they exist. The forward cross-linking (new pages link to existing ones) works well; the reverse case is less reliable.

**Planned fix**: strengthen the Historian agent's prompt for the update case, or add a separate "backlink pass" in Spec 003.

### CLI errors lose the failing JSON preview

When an agent returns malformed JSON, the orchestrator saves the full raw response to `.specify/scratch/agent_responses/<timestamp>.txt` — but the CLI only shows a 1500-char preview. For very long failures, you have to dig into the scratch file manually.

---

## Scope not yet implemented

These are deliberate Phase A omissions, scheduled for future specs.

### ~~No multi-conversation export selection~~ — RESOLVED in Spec 002

Resolved by Spec 002 (the `insightmesh list` subcommand + `--conversation` flag on `batch`, powered by the [`echomine`](https://pypi.org/project/echomine/) library). The original gap is left below for historical context.

#### Original problem (pre-Spec-002)

**What's wrong**: The `insightmesh batch` CLI accepts a single transcript file: a flat JSON array of `{"role": ..., "content": ...}` messages, representing **one conversation**. Real exports from Claude.ai or ChatGPT are arrays of *conversation objects* — each with its own metadata and a nested messages array.

If you download your Claude.ai or ChatGPT data and point `insightmesh batch` directly at the export file, it will fail (or, worse, silently misinterpret the structure).

**What works around it today**: extract one conversation from the export manually — typically with `jq` or a small script — and reshape it to the flat `[{"role": ..., "content": ...}, ...]` shape before running `insightmesh batch`. There's no CLI helper for this yet.

**Why it's not built yet**: Spec 001's mandate was "single transcript → wiki." Browsing a multi-conversation export and picking one is its own user story.

**Planned fix** (Spec 002 or a small 001.x patch):

1. `insightmesh list <export.json>` — browse conversations in an export (id, title, date, message count)
2. `--conversation <id|index>` flag on `batch`
3. Built-in adapter for the Claude.ai and ChatGPT export schemas (so users don't write `jq` pipelines by hand)

### No live inquiry mode (Spec 002)

Currently the pipeline is **batch-only** — you feed it an existing chat transcript and it produces wiki pages. There's no way to:

- Ask a question and get a wiki page back interactively
- Have the system propose clarifying questions before synthesizing (the Refiner agent from CogniVault)
- Run a tighter loop than "export → batch → review"

This is Spec 002's whole purpose.

### No bias or assumption checking (Spec 003)

The Synthesis agent doesn't critique the framing of the source conversation. It synthesizes whatever was discussed without flagging unexamined assumptions, missing perspectives, or potential bias. The Critic agent that handles this is planned for Spec 003.

### No web research (Spec 003)

InsightMesh today synthesizes from what's already in the transcript. It cannot fetch additional sources to fill in gaps or verify claims. The Researcher agent is planned for Spec 003.

### No formal citation tracking (Spec 003)

The frontmatter `source` field points at the transcript file as a whole. Individual claims within a synthesized page aren't traced back to specific exchanges or web sources.

---

## Architecture decisions you might wonder about

### Why is there no database?

Phase A persistence is "JSON session logs on disk + Obsidian markdown files." That's intentional:

- The session logs are the schema blueprint for the Phase B PostgreSQL persistence layer — designing from real data, not assumptions
- Markdown-in-Obsidian is what users actually want for their knowledge base
- A database now would be premature

### Why use the LLM orchestrator instead of Python?

For Phase A prototyping, the main-Claude-as-orchestrator pattern lets us iterate on the agent contracts and prompts without committing to a particular Python orchestration framework. Phase B migrates to LangGraph for production determinism.

### Why MCPVault instead of writing files directly from Python?

MCPVault gives the agents safe, atomic file operations and AST-aware frontmatter editing. Writing files directly from Python would force the orchestrator (not the agents) to know Obsidian syntax — and would lose us the per-agent tool isolation that the SDK provides.

### Why isn't the Historian using semantic (vector) search?

Phase A uses BM25 keyword search via MCPVault — works well for distinct topics, less well for fuzzy semantic similarity. pgvector + embedding-based search is planned for Phase B, informed by the session logs that show where BM25 misses.

---

## Reporting new limitations

If you find a behavior that surprised you and isn't in this list, please open an issue. We'd rather document a known limitation than have it bite users silently.

[github.com/aucontraire/insightmesh-core/issues](https://github.com/aucontraire/insightmesh-core/issues)
