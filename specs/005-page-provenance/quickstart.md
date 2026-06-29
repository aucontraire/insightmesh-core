# Quickstart: Per-page provenance (Spec 005)

What you get after Spec 005 ships, and how to inspect it.

## Prerequisites

- InsightMesh installed and at least one successful `insightmesh batch` run completed against your vault.
- `git` on `PATH` (for the diff-history layer; the structured JSON and frontmatter land even without git, per FR-015).
- A Claude.ai or ChatGPT export, or a Spec 001 flat-array transcript.

## What lands on disk after each successful checkpoint

```
<vault>/InsightMesh/
├── <your wiki pages>.md                # Editor's output (unchanged by Spec 005, except for the added provenance: frontmatter block)
├── .logs/
│   ├── <timestamp>-<stem>.json         # session log (unchanged; operational/diagnostic)
│   └── <stem>__<conv-id>.checkpoint.json   # Spec 004 cursor (unchanged; resume state)
└── .history/                           # Spec 005 — NEW
    ├── .git/                           # shadow git repo (do not modify by hand)
    ├── checkpoints/
    │   └── <conv-id-or-_flat>/
    │       ├── cp-001.json             # structured provenance record per checkpoint
    │       └── cp-002.json
    └── pages/
        └── <slug>.md                   # snapshots of every page Editor touched (git-tracked)
```

## Inspecting provenance

Two surfaces:

- **Power users / scripting** — use `jq` on the JSON files, shell `git -C .history log` on the shadow repo, and YAML readers (or Obsidian Dataview / Bases) on the page frontmatter. Examples in the sections below.
- **In Obsidian (planned)** — a dedicated **InsightMesh Obsidian viewer plugin** is the planned native-experience surface: a side-pane view that joins the open wiki page's `provenance:` block with its linked checkpoint JSON and the snapshot diff history, with click-through navigation across decisions, conversations, and the session log. Lives in a separate repo at [aucontraire/insightmesh-obsidian](https://github.com/aucontraire/insightmesh-obsidian); tracked as the next spec after Spec 005 ships and produces real provenance data to build against. Until that plugin lands, the shell-tool approach below is the option, or [obsidian-git as a tactical viewer with caveats](#viewer-caveats).

### Power-user / scripting access

```bash
# What did checkpoint cp-002 do?
jq . <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-002.json

# Which pages did this checkpoint touch?
jq '.results' <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-002.json

# What was Editor's confidence for each decision?
jq '.editor.decisions[] | {file, confidence, rationale}' \
  <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-002.json

# Across all checkpoints in this conversation, which pages did Editor touch most often?
jq -s '[.[] | .editor.decisions[] | .file] | group_by(.) | map({file: .[0], count: length}) | sort_by(-.count)' \
  <vault>/InsightMesh/.history/checkpoints/<conv-id>/cp-*.json
```

## Read a page's at-a-glance provenance

Open any wiki page that the pipeline has touched. Its YAML frontmatter now includes a `provenance:` block:

```yaml
---
title: Capitalism's Origins
created: 2026-06-25T14:02:13Z
updated: 2026-06-27T22:15:33Z
source: <conv-id>
tags: [history, economics, political-theory, capitalism]
provenance:
  latest_checkpoint: InsightMesh/.history/checkpoints/d126dc13-…/cp-002.json
  conversations:
    - d126dc13-ab72-4657-939d-b1d1ecc0fd33
  latest_action: updated
  latest_confidence: high
  total_edits: 3
  exchange_count: 7
---
```

`latest_checkpoint` is a vault-relative path; click it in Obsidian to open the structured record. `conversations` accumulates every conversation that has ever contributed to this page (cross-thread compounding visible at a glance).

## Browse the page's diff history

```bash
# All commits in the shadow repo
git -C <vault>/InsightMesh/.history log --oneline

# Commits that touched a specific page
git -C <vault>/InsightMesh/.history log --oneline pages/Capitalism\'s\ Origins.md

# See what changed in the page between checkpoint cp-001 and cp-002
git -C <vault>/InsightMesh/.history log -p pages/Capitalism\'s\ Origins.md

# Find the commit for a specific checkpoint id
git -C <vault>/InsightMesh/.history log --oneline --grep 'checkpoint:cp-002'

# Find every commit that touched conversation d126dc13
git -C <vault>/InsightMesh/.history log --oneline --grep 'conversation:d126dc13'
```

Commit message format (FR-014):

```
[InsightMesh checkpoint:cp-002 conversation:d126dc13-…] 2 pages updated, 1 created

Metadata: checkpoints/d126dc13-…/cp-002.json
Pages touched:
  - Capitalism's Origins.md (updated, confidence:high)
  - Empire Decline.md (updated, confidence:medium)
  - American Revolution.md (created, confidence:high)
```

## Viewer caveats

If you reach for [obsidian-git](https://github.com/Vinzent03/obsidian-git) with `basePath = InsightMesh/.history` to browse provenance commits in Obsidian before the dedicated viewer plugin ships, know these limits up front:

- **`basePath` is single-target.** You give up obsidian-git for any other repo (e.g., your vault root) while it's pointed here. The workaround (clone the plugin under a second ID per [#703](https://github.com/Vinzent03/obsidian-git/issues/703)) works but is ugly.
- **Source Control View is read/write.** The "Discard all changes" button in the panel will wipe uncommitted snapshots InsightMesh was about to commit. Treat the view as read-only by convention.
- **Disable auto-commit-and-sync.** If left on, obsidian-git will autonomously commit `.history/` on its schedule with its templated message (`{{numFiles}} {{date}}`), polluting the FR-014-formatted commit history with non-checkpoint commits.
- **Diffs are between snapshots, not live pages.** Clicking `pages/<slug>.md` in the panel opens the snapshot copy, NOT the live wiki page. Don't edit snapshots; you'll pollute history and (if auto-commit is on) commit the rogue edits as if they were checkpoint snapshots.
- **No semantic filter.** Obsidian's History View shows all commits ordered by time with no way to filter by `checkpoint_id` or conversation. Shell `git log --grep` is still your friend for that.
- **Mobile is unstable** per obsidian-git's own README warnings; submodule paths are desktop-only.

The dedicated InsightMesh viewer plugin (planned, separate repo) is built to avoid every one of these: read-only by design, knows live page vs snapshot, semantically filters by checkpoint and conversation, joins the JSON sidecar to the page history. Until it ships, obsidian-git is a tactical option, not a polished one.

## Fallback behavior

### `git` is not installed

The JSON file under `.history/checkpoints/<conv-id>/cp-<NNN>.json` and the `provenance:` frontmatter block on each touched page still land. The shadow-repo commit is skipped with a single stderr warning:

```
[provenance] git not on PATH; skipping shadow-repo commit
```

Install `git`, rerun, and the next checkpoint's commit will include both this checkpoint's snapshots and the prior orphaned ones (git handles untracked diffs cleanly).

### A `git commit` fails (disk full, hooks, permissions)

Same posture. JSON + frontmatter are already on disk; the commit failure logs to stderr and the run still exits 0. Fix the underlying issue, rerun, next commit catches up.

### Existing page frontmatter is malformed YAML

That page's frontmatter is left untouched, a stderr warning names the page, and the rest of the checkpoint's work proceeds normally:

```
[provenance] frontmatter parse failed for InsightMesh/<some-page>.md: <yaml error>
```

The checkpoint JSON still records the decision; only the in-page summary is missing for that one page.

## Coexistence with a user-managed git on the vault root

If you run `git` at the vault root yourself, the shadow repo at `<vault>/InsightMesh/.history/.git/` appears as a nested repo (git treats it as untracked content with an embedded repo). To prevent that nested repo from appearing in `git status` at the vault level, add this line to your vault's `.gitignore`:

```
InsightMesh/.history/
```

InsightMesh will not write to your vault-level `.gitignore` itself (constitution rule: don't modify user-owned files outside the InsightMesh namespace).

## Verifying after a real run

Smoke test (after merge):

```bash
# Run a small Claude conversation
insightmesh batch ~/Downloads/conversations.json \
  --conversation <id> \
  --vault ~/Obsidian/MyVault

# Then verify the provenance landed:
ls ~/Obsidian/MyVault/InsightMesh/.history/checkpoints/<id>/
# expected: cp-001.json (and cp-002.json etc. for multi-checkpoint runs)

jq '.schema_version, .checkpoint_id, .conversation.provider, .editor.decisions | length' \
  ~/Obsidian/MyVault/InsightMesh/.history/checkpoints/<id>/cp-001.json
# expected: 1, "cp-001", "anthropic" (or "openai"), N (matching pages touched)

# Check one touched page's provenance:
head -20 ~/Obsidian/MyVault/InsightMesh/<some-page>.md
# expected: title/created/updated/source/tags + a provenance: block

# Check git history:
git -C ~/Obsidian/MyVault/InsightMesh/.history log --oneline
# expected: at least one commit, subject matching FR-014 pattern
```

## What this does NOT do

(See spec.md `## Out of Scope` for the complete list and rationale. Highlights:)

- No in-Obsidian viewer plugin in Spec 005 itself; the dedicated [insightmesh-obsidian](https://github.com/aucontraire/insightmesh-obsidian) viewer plugin is the planned follow-up (separate repo, separate spec). Until it ships: shell tools (above) or [obsidian-git with caveats](#viewer-caveats).
- No CLI `insightmesh history <page>` subcommand or Python `mesh.history` read library; deferred to a separate future spec.
- No Obsidian Dataview / Bases template ships with the spec (you can build your own from the frontmatter fields).
- No intra-message attribution (which sentence landed where) — that needs a Critic-style agent.
- The checkpoint JSON does NOT carry the git commit SHA; navigate from JSON to commit via the `checkpoint_id` string with `git log --grep`.
- The session log under `.logs/` is NOT removed; it remains the operational/diagnostic artifact, distinct from the checkpoint JSON's role as the permanent provenance record.
