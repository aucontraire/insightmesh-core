# Spike: Content-Type Handling & Multimodal Artifacts in Chat Exports

**Status**: Research spike (no implementation). Informs a future Spec (likely 002.x "synthesis input hygiene" and/or a larger multimodal spec).
**Created**: 2026-05-25
**Author trigger**: Real-data testing of Spec 002 surfaced that the CLI message count didn't match the chat app's count, and that non-conversational content (custom-instructions/memory, chain-of-thought, tool I/O) was leaking into synthesized wiki pages as if it were real conversation.

---

## 1. Problem statement

When InsightMesh extracts a conversation from an export and flattens it to `{role, content}` turns, it currently keeps every `user`/`assistant` message with non-empty content. But "messages" in a real export include a large class of **non-conversational content types** — custom instructions, model memory, hidden chain-of-thought, tool calls, code-interpreter I/O, browsing internals, image/audio pointers, canvas/artifact documents. These either:

- **leak into the transcript** as spurious turns (polluting what the Synthesis agent sees), or
- **get silently dropped** (losing content that might actually be valuable).

This was first noticed as a **message-count discrepancy**: `insightmesh list` reports more messages than the user sees as turns in the app, because echomine surfaces these extra content types as messages.

### Prevalence in real data

From the user's actual OpenAI export (280 conversations, 29,022 mapping nodes):

| Content type | Count | Conversational? |
|--------------|-------|-----------------|
| `text` | 23,812 | ✅ yes |
| `multimodal_text` | 1,726 | ⚠️ partial (text + image pointers) |
| `app_pairing_content` | 1,290 | ❌ no |
| `code` | 620 | ⚠️ sometimes (real code blocks) |
| `tether_quote` | 378 | ❌ browsing internal |
| `execution_output` | 353 | ❌ tool output |
| `thoughts` | 182 | ❌ hidden chain-of-thought |
| `reasoning_recap` | 164 | ❌ hidden reasoning |
| `tether_browsing_display` | 94 | ❌ browsing internal |
| `user_editable_context` | 84 | ❌ custom instructions/memory |
| `system_error` | 39 | ❌ error artifact |

Plus: 280 null-message root nodes, 2,640 empty-content messages (already filtered as of `4455fe7`), 3,448 visually-hidden messages, 137 regeneration branches across 45 conversations, and 292 incomplete generations (`in_progress` 143 + `finished_partial_completion` 149).

---

## 2. What echomine actually does (the load-bearing finding)

The two echomine adapters handle non-conversational content **completely differently**. This is the single most important fact for the design.

### OpenAI adapter (`echomine/adapters/openai.py`, `_parse_message`)

Switches on `content.content_type`:

| content_type | echomine output |
|--------------|-----------------|
| `text` | `parts[0]` (⚠️ **only the first part** — multi-part text drops `parts[1:]`) |
| `multimodal_text` | text parts joined; images extracted into `Message.images` |
| `image_asset_pointer` / `image` | literal `"[Image]"` |
| `code` | `parts[0]` or `"[Code]"` |
| **anything else** (`user_editable_context`, `thoughts`, `reasoning_recap`, `tether_*`, `execution_output`, `system_error`, `app_pairing_content`, …) | **`f"[{content_type}]"`** — a placeholder string |

So `user_editable_context` becomes a message whose content is the literal string `"[user_editable_context]"`. That is exactly the leak we observed:

```
[user] '[user_editable_context]'      ← surfaced as a real user turn
```

**Critically: echomine does NOT preserve the original `content_type` anywhere structured.** `Message.metadata` only carries `{"original_role", "update_time"}`. The content type survives only as the placeholder string for unknown types. There is no clean field to filter on.

### Claude adapter (`echomine/adapters/claude.py`, `_extract_content_from_blocks`)

Iterates `content[]` blocks and extracts **only** `type == "text"` blocks (joined with newlines). **Skips `tool_use` and `tool_result`** explicitly (FR-015a). Implicitly skips `thinking` and `voice_note` (only `text` is matched). Result: `Message.content` is clean conversational prose, or empty if a message had no text blocks.

### The asymmetry

| | OpenAI path | Claude path |
|--|-------------|-------------|
| Non-conversational content | **Leaks** as `[content_type]` placeholder strings | **Cleanly dropped** at block level |
| Tool I/O | `[execution_output]`, `[tether_quote]` placeholders | skipped (tool_use/tool_result) |
| Chain-of-thought | `[thoughts]`, `[reasoning_recap]` placeholders | skipped (thinking blocks not extracted) |
| Custom instructions / memory | `[user_editable_context]` placeholder | n/a (Claude doesn't export this in chat_messages) |
| Images | `Message.images` populated + `[Image]`/text | dropped (no image handling shown) |
| Multi-part text | ⚠️ only `parts[0]` kept | all text blocks concatenated |

**Bottom line**: the OpenAI path is leaky; the Claude path is clean (sometimes too clean — it drops `thinking` and `voice_note`). InsightMesh's empty-content filter (`4455fe7`) catches the Claude-side "no text block → empty" case, but does nothing about the OpenAI placeholder leak.

---

## 3. The four questions, answered

### Q: Has echomine standardized output enough for us to distinguish non-message content types? Do we need to change echomine?

**No, not enough — and yes, echomine needs a change for a clean solution.** echomine collapses content type into either real text, a known placeholder (`[Image]`, `[Code]`), or an unknown placeholder (`[content_type]`), and discards the structured `content_type`. To distinguish content types cleanly, InsightMesh would otherwise have to pattern-match placeholder strings (fragile, breaks on schema drift).

**Recommended echomine change**: preserve the original `content_type` (OpenAI) / block `type` (Claude) in `Message.metadata` (e.g., `metadata["content_type"]` and `metadata["is_visually_hidden"]`). This is non-lossy — it lets each consumer decide what to filter, rather than echomine making the decision opaquely. Since Omar maintains echomine, this is a coordinated upstream change, not a fork.

**Secondary echomine bug found**: the OpenAI adapter keeps only `parts[0]` for `text` messages — multi-part text messages silently lose `parts[1:]`. Worth a separate echomine fix.

### Q: Is there a different set for Anthropic vs OpenAI?

**Yes, materially different.**

- **OpenAI content types**: `text`, `multimodal_text`, `code`, `execution_output`, `tether_quote`, `tether_browsing_display`, `tether_browsing_code`, `user_editable_context`, `model_editable_context`, `thoughts`, `reasoning_recap`, `system_error`, `app_pairing_content`, `image_asset_pointer`. OpenAI puts a lot of "off to the side" machinery into the mapping (browsing, code interpreter, canvas, memory, reasoning).
- **Anthropic block types**: `text`, `thinking`, `voice_note`, `tool_use`, `tool_result`. Plus `attachments[]` / `files_v2[]` at the message level. Anthropic's model is simpler and echomine already filters it to text-only.

And the adapters behave differently (see §2), so InsightMesh must normalize to get provider-parity clean transcripts.

### Q: Is any of this content valuable to the sub-agents (or a 4th agent)?

Yes — several types have latent value, which is why "just drop everything non-text" is too blunt a long-term answer:

| Content type | Potential value | Which agent |
|--------------|-----------------|-------------|
| `thoughts` / `thinking` (chain-of-thought) | Reasoning transparency, bias/assumption detection | **Critic** (Story 6) — NOT the wiki prose, but useful context for critique |
| `code` / `execution_output` | Real code + results for technical topics | **Synthesis** should preserve genuine code blocks (echomine extracts `code` parts already) |
| `tether_*` (browsing citations) | Source URLs the model browsed | **Source attribution** (Story 8); overlaps with the URL-preservation rule already added to synthesis.md |
| images (`multimodal_text` / `image_asset_pointer`) | Visual context | A future multimodal-aware agent; currently `Message.images` is populated by echomine but **InsightMesh ignores it entirely** |
| Canvas / Artifacts (see Q4) | Often the actual deliverable | Possibly a dedicated "artifact" handling path |

This suggests a possible **content-classification / ingestion step** (a 4th sub-agent, or a pre-Synthesis normalization pass) that routes content types: prose → Synthesis, citations → attribution, reasoning → Critic, code → preserved blocks, images/artifacts → referenced or described. That is a significant architectural expansion — flagged here, not decided.

### Q: What about uploaded images, generated documents (OpenAI Canvas), code rendered off to the side, etc.? Are we leaving those out? Provider differences?

**We are currently leaving almost all of it out, and the two providers differ in where this content lives:**

- **OpenAI**:
  - **DALL-E / uploaded images** → `multimodal_text` with `image_asset_pointer`. echomine extracts pointers into `Message.images` (an `ImageRef` list) and puts text parts in content. **InsightMesh ignores `Message.images`** — so images are dropped except any accompanying text.
  - **Code Interpreter** → `code` (input) + `execution_output` (results). `code` survives as text; `execution_output` leaks as `[execution_output]`.
  - **Canvas** (the side-panel documents) → believed to live in `model_editable_context` / a canvas-specific content type → currently leaks as a placeholder or is dropped. **This is likely the "appears in a sidebar div" content the user asked about** — and Canvas docs are often the real deliverable, so losing them is a meaningful gap.
  - **Browsing** → `tether_*` placeholders (the URLs are the valuable part — ties to source attribution).
  - **File uploads** → `attachments[]` (not in the mapping content; separate field echomine may not surface).
- **Anthropic**:
  - **Artifacts** (the side-panel documents/code/components) → `tool_use` / `tool_result` blocks → **echomine skips these entirely**, so Anthropic artifacts are silently dropped. Like Canvas, artifacts are often the actual output the user cares about.
  - **Uploaded files/images** → `attachments` / `files_v2` at message level → not surfaced as content.

**Provider difference summary**: OpenAI scatters more machinery into the conversation (canvas, code interpreter, browsing, memory, reasoning) that currently *leaks as placeholders*; Anthropic keeps tool/artifact content in structured blocks that echomine *cleanly drops*. Both lose the genuinely valuable side-panel deliverables (Canvas / Artifacts). Neither surfaces uploaded files into the transcript.

---

## 4. Design decisions (proposed, for the future spec to ratify)

### D1 — Where to filter non-conversational content?

| Option | Description | Verdict |
|--------|-------------|---------|
| D1-a | InsightMesh pattern-matches placeholder strings (`[user_editable_context]`, `[thoughts]`, …) | ❌ fragile; breaks on echomine/schema drift |
| D1-b | echomine preserves `content_type` in `Message.metadata`; InsightMesh filters on it | ✅ **recommended** — non-lossy, clean separation, each consumer decides |
| D1-c | echomine filters non-conversational types at the adapter level (like it already does for Claude tool blocks) | ⚠️ cleanest for naive consumers, but makes echomine opinionated/lossy; harms consumers who want the data |

**Recommendation: D1-b — now LOCKED upstream.** The echomine "Content Fidelity & Asset Recovery" spec (finalized 2026-05-25; see `echomine/CONTENT_FIDELITY_AND_ASSETS.md`) goes further than the original D1-b and adds a **standardized `content_type_category`** to `Message.metadata` (values: `conversational`, `reasoning`, `tool_io`, `system`, `media`, `attachment`, `unknown`), alongside the raw `content_type`. This is better than the per-provider allowlist this spike originally proposed:

- **InsightMesh's filter becomes one provider-agnostic line**: keep `metadata["content_type_category"] == "conversational"`. No more maintaining `{text, multimodal_text, code}` vs `{text, voice_note}` allowlists — the adapter pattern delivers on its promise.
- **~80% of the leak is fixed by echomine alone**: echomine AC-1 sets `content=""` for unmapped/non-conversational types, so InsightMesh's *existing* empty-skip filter (commit `4455fe7`) already drops them the moment we bump the echomine dependency. The category filter is then a refinement (cleanly excluding `reasoning`/`tool_io`/`system` that may still carry content), not the urgent fix.
- **Sequencing**: echomine's changes are additive/non-breaking, so InsightMesh can upgrade the dependency without breaking, then adopt `content_type_category` deliberately. Gate the InsightMesh hygiene spec on the echomine release that ships AC-1.

### D2 — What about the valuable non-prose content (images, code, canvas, artifacts)?

Defer the *capture* of images/canvas/artifacts to a dedicated later spec (multimodal). For the near-term hygiene spec: drop them cleanly (no placeholder pollution) but **do not architecturally foreclose** capturing them later. Keep genuine `code` blocks (they're already extracted and are valuable for technical syntheses).

### D3 — Message count semantics

`insightmesh list`'s "Msgs" column should count **conversational turns** (`content_type_category == "conversational"`), not raw mapping nodes, so it matches what the user sees in the app. This is a small change but directly addresses the discrepancy that surfaced the whole issue. Depends on the echomine category contract (D1). The echomine spec's own success criteria include "message counts match the human-visible turn count" — so once echomine ships, this is a direct downstream consequence.

### D4 — Provider parity

The hygiene spec must produce equivalent clean transcripts regardless of provider. Acceptance test: the same logical conversation exported from both providers yields the same conversational turns (modulo provider-specific phrasing).

### D5 — Incomplete / interrupted generations

292 incomplete messages exist in the sample. Decision deferred: likely include partial assistant content as-is (it's real, just truncated), but consider dropping `finish_details.type == "interrupted"` empties. Low priority vs the leak.

---

## 5. Scope split: echomine vs InsightMesh

Because Omar maintains both, the spike cleanly separates the work:

**echomine changes (upstream):**
1. Preserve `content_type` (OpenAI) / block `type` (Claude) + `is_visually_hidden` in `Message.metadata`. (Enables D1-b.)
2. Fix multi-part `text` truncation (OpenAI adapter keeps only `parts[0]`).
3. (Optional, larger) surface Canvas/Artifacts and attachments in a structured way.

**InsightMesh changes (this repo, future spec):**
1. Content-type allowlist filter in `_to_role_content` (depends on echomine #1).
2. Fix the `list` message count to reflect conversational turns (D3).
3. (Later) decide on multimodal/artifact capture (D2) and a possible content-classification step / 4th agent (Q3).

---

## 6. Open questions / risks

- **echomine API change coordination**: adding `content_type` to metadata is an echomine release; InsightMesh's filter depends on it. Sequence the two specs accordingly (echomine first, or InsightMesh tolerates both old/new echomine).
- **Canvas/Artifacts are often the real output.** Dropping them keeps the wiki clean but may discard the most valuable content of some conversations. Needs a product call: is InsightMesh a "conversation synthesizer" (prose only) or a "knowledge capturer" (including generated artifacts)?
- **Chain-of-thought for the Critic**: capturing `thoughts`/`thinking` for Story 6 is appealing but raises volume + privacy considerations.
- **Schema drift**: both providers change formats frequently; the allowlist must default-drop unknown types (fail safe), and echomine's tolerant parsing must hold.

---

## 7. Recommended next steps

1. **Near-term hygiene spec** (small, high-value): echomine `content_type` metadata + InsightMesh allowlist filter + count fix. Resolves the leak and the count discrepancy. This is the natural Spec 002.x.
2. **Separately**: a larger multimodal/artifact spec (capture images, Canvas, Artifacts) — only if the product decision in §6 favors it. Bigger; apply Rule of Three.
3. Do **not** bolt the filter onto Spec 002 — it's a new edge-case class with its own design surface (this spike is the evidence).

---

## 8. Asset recoverability — confirmed (research + real-bundle inspection, 2026-05-25)

Supersedes the speculative parts of §3 Q4. Verified against the user's real export bundles plus external research.

**OpenAI — binaries ARE in the bundle (for this export):** the OpenAI export directory alongside `conversations.json` held **456 PNG, 130 JPEG, 21 JPG, 19 WebP, 12 WAV** files. Filenames embed the file id (`file_00000000…752c61…-sanitized.png`, `file-8T2U5Cp4…-Screenshot….png`); `conversations.json` references them via `asset_pointer` = `sediment://file_…` (new) / `file-service://file-…` (old). Resolution = strip scheme, match the `file_<id>`/`file-<id>` token as a filename prefix, sniff magic bytes for the true extension.

**Caveat**: presence is **version/account-dependent** — sources conflict on whether DALL-E/media binaries always ship in the zip, and the export download link expires in ~24h. Code must detect presence at runtime, never assume it.

**Anthropic — JSON only, no binaries:** uploads appear as **text extracts** (`attachments[].extracted_content`); **Artifacts** are inline tool blocks (recoverable as text/code, currently skipped by echomine); uploaded binaries are unrecoverable from the export by design.

**Recoverable-from-export verdict:**

| Artifact | OpenAI | Anthropic |
|----------|--------|-----------|
| Generated images (DALL-E) | **Yes** when binaries in bundle (this export had them); else API-only/expiring | n/a |
| Uploaded images | **Yes** when in bundle | binary lost |
| Uploaded docs/PDFs | usually API-only | **text extract only** |
| Audio / voice | **Yes** (12 WAV present) | n/a |
| Code Interpreter outputs | lost (session-only) | lost |
| Canvas / Artifacts | Canvas **lost** (no `textdocs/`) | Artifacts: **text/code recoverable inline**, no binary |

**Implication for InsightMesh**: an OpenAI image/audio → Obsidian-attachment pipeline is genuinely achievable for exports that include binaries. Realistic v1 scope: (a) try local file match by pointer token, (b) copy into vault attachments + embed with `![[file]]`, (c) on miss, write a placeholder note with the pointer/metadata. This work depends on echomine surfacing the `asset_pointer` + a resolver (see `echomine/CONTENT_FIDELITY_AND_ASSETS.md` §C1).

---

## 9. Open threads to flesh out (so the work is informed and plannable)

> **Update (2026-05-26): the echomine side is now spec'd and locked** (`echomine/CONTENT_FIDELITY_AND_ASSETS.md` → echomine "Content Fidelity & Asset Recovery" spec). That resolves the *upstream* half of several threads below (content_type_category contract, `unknown` drift canary, symmetric reasoning handling, attachment `extracted_content` surfacing, OpenAI asset resolver). The threads below are now the *InsightMesh-side* questions that remain once echomine ships. Two consumer-side items the echomine work newly enables: **(a)** `extracted_content` from Claude attachments is ready-to-synthesize source text — decide how it enters a wiki page (inline context vs. its own note vs. a "Sources" section, ties to thread 4); **(b)** `tether_*` browsing citations are deferred upstream (kept as raw `content_type`, not surfaced as a category) — a future InsightMesh source-attribution feature (Story 8) can pull them, so log it as a known future source rather than a loss.

> **Ordering gotcha (from echomine's 005 reconcile, 2026-05-26):** echomine resolved the dead `attachment` category by keeping it for the *attachment-only* case (message with `extracted_content` but no conversational text), symmetric with `media` for image-only. But such a message has **empty `content`** (the doc text lives in `metadata["attachments"]`, not `content`). So InsightMesh's existing empty-content skip (commit `4455fe7`) will drop it **before** we ever read the extracted text — unless the hygiene spec explicitly harvests `metadata["attachments"]` from `category == "attachment"` (and `conversational`-with-attachments) messages *ahead of* the empty-content filter. **Design rule for the hygiene spec: harvest attachment/artifact metadata before empty-skip, not after.** This is the same ordering trap as not letting a hidden-but-content-bearing node get filtered before its metadata is captured.

Subjects this spike should develop before a spec is written:

1. **Product identity decision (the load-bearing one):** is InsightMesh a *prose synthesizer* (clean conversational text only) or a *knowledge capturer* (also preserves generated artifacts — images, code, Canvas/Artifacts, audio)? Every scoping decision below flows from this. Define explicit criteria, not vibes. Candidate framing: "preserve any artifact recoverable from the export with bounded effort; drop the rest cleanly."

2. **Asset → vault pipeline design:** attachment folder layout, filename normalization (the pointer-token → human-readable name + magic-byte extension), `![[embed]]` placement in the synthesized page (where does an image go relative to the prose that referenced it?), and **cross-conversation dedup** (the same image reused across chats).

3. **Privacy / PII:** custom instructions, memory (`user_editable_context`/`model_editable_context`), and uploaded-file extracts can contain sensitive data. What is allowed to land in the wiki? What's scrubbed? This matters more once we stop dropping these as placeholders.

4. **Provenance in frontmatter:** should a wiki page record which content types/sources contributed (content_type provenance, preserved source URLs, embedded asset filenames)? Ties to Story 8 (citations) and the URL-preservation rule already shipped.

5. **Message-count semantics + pairing interaction:** the `list` "Msgs" count should reflect conversational turns post-filter (Decision D3). Spell out how content-type filtering interacts with the user/assistant pairing in `transcript.py` (e.g., dropping a hidden node between two real turns must not merge them incorrectly).

6. **Incomplete/interrupted generation policy:** include partial assistant text as-is, flag it, or drop `finish_details.type == "interrupted"` empties? (292 such messages in the sample.)

7. **Chain-of-thought routing:** capture `thoughts`/`thinking` for a future **Critic** agent (Story 6)? Weigh value vs volume vs privacy. Likely: not in wiki prose, optionally available as agent context.

8. **echomine version sequencing / compatibility:** InsightMesh's content-type filter depends on echomine preserving `content_type` (echomine work item A1). Decide: gate the InsightMesh spec on an echomine release, or make the filter tolerate both old (placeholder-string) and new (metadata) echomine. Affects spec ordering.

9. **Test strategy:** per-content-type, per-provider fixtures; golden transcripts; the headline assertion *"surfaced turn count matches the source app."* For assets, a tiny fixture export dir with real (small) binaries + matching pointers.

10. **The 4th-agent question:** when is a dedicated ingestion/content-classification step (or agent) warranted vs. a simple allowlist filter? Criterion: an allowlist suffices for *dropping* non-prose; a classification step is only warranted if we start *routing* content types to different consumers (prose→Synthesis, citations→attribution, reasoning→Critic, artifacts→asset pipeline). Don't build it until that routing is real (Rule of Three).

11. **Scope boundaries (explicit non-goals to state in the spec):** live-API fetching of expired/absent assets; Canvas recovery (lost from export); Code Interpreter output recovery (session-only).

---

## Sources

Verified during research (2026-05-25):

- pionxzh/chatgpt-exporter `src/api.ts` (full OpenAI schema: AuthorRole, all content_types, finish_details, is_visually_hidden_from_conversation, is_complete, current_node walk): https://github.com/pionxzh/chatgpt-exporter/blob/master/src/api.ts
- pionxzh/chatgpt-exporter `src/exporter/markdown.ts` (content-type handling, unicode citation normalization, continuation merge): https://github.com/pionxzh/chatgpt-exporter/blob/master/src/exporter/markdown.ts
- OpenAI Developer Forum — "Decoding Exported Data by Parsing conversations.json": https://community.openai.com/t/decoding-exported-data-by-parsing-conversations-json-and-or-chat-html/403144
- OpenAI Developer Forum — "Questions about the JSON structures in the exported conversations.json": https://community.openai.com/t/questions-about-the-json-structures-in-the-exported-conversations-json/954762
- osteele/claude-chat-viewer `src/schemas/chat.ts` (authoritative Claude Zod schema: sender, dual text+content[], block types text/thinking/voice_note/tool_use/tool_result, truncated, cut_off, is_error, current_leaf_message_uuid, parent_message_uuid): https://github.com/osteele/claude-chat-viewer/blob/main/src/schemas/chat.ts
- OpenAI Help — export instructions: https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history-and-data
- Anthropic Help — export instructions: https://support.claude.com/en/articles/9450526-how-can-i-export-my-claude-data

Local source verified: `echomine/src/echomine/adapters/openai.py` (`_parse_message`, lines ~860-928), `echomine/src/echomine/adapters/claude.py` (`_extract_content_from_blocks`, lines ~186-219), `echomine/src/echomine/models/message.py` (Message field set; metadata carries only original_role/update_time).
