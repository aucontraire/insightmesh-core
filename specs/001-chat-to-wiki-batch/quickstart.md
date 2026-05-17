# Quickstart: Chat-to-Wiki Batch Synthesis

**Feature**: `001-chat-to-wiki-batch` | **Date**: 2026-05-16

## Prerequisites

- Python 3.12+
- Claude Code CLI installed and authenticated
- An existing Obsidian vault directory

## Setup

```bash
cd insightmesh-core

# Install runtime + dev dependencies (defined in pyproject.toml + uv.lock)
uv sync --all-extras

# Verify claude CLI is installed (the SDK shells to it under the hood)
claude --version

# Verify MCPVault config in .mcp.json points to a valid path or templating
```

**Dependencies (per constitution v1.1.0 Project Standards):**
- Runtime: `claude-agent-sdk`, `pydantic>=2.0`
- Dev: `pytest`, `mypy`, `ruff`, `black`

**External integrations:**
- Obsidian vault knowledge via kepano/obsidian-skills (preloaded per agent)
- Vault I/O via MCPVault MCP server (configured in `.mcp.json`)

## Usage

### Basic batch synthesis

```bash
uv run python -m src.cli batch transcript.json --vault ~/Obsidian/MyVault
```

This will:
1. Parse `transcript.json` (JSON array of `{role, content}` message objects)
2. Process exchanges through Synthesis → Historian → Editor agents
3. Create wiki pages in `~/Obsidian/MyVault/InsightMesh/`
4. Write a session log to `~/Obsidian/MyVault/InsightMesh/.logs/`

### With custom logs directory

```bash
uv run python -m src.cli batch transcript.json --vault ~/Obsidian/MyVault --logs ./logs
```

### Sample transcript format

```json
[
  {"role": "user", "content": "What is the speed of light?"},
  {"role": "assistant", "content": "The speed of light in a vacuum is approximately 299,792,458 meters per second..."},
  {"role": "user", "content": "How was it first measured?"},
  {"role": "assistant", "content": "The first successful measurement was made by Ole Rømer in 1676..."}
]
```

## Expected output

### Wiki pages created in vault

```
~/Obsidian/MyVault/InsightMesh/
├── Speed of Light.md
└── .logs/
    └── 2026-05-16T103000-batch.json
```

### Wiki page format

```markdown
---
title: "Speed of Light"
created: 2026-05-16T10:30:00Z
updated: 2026-05-16T10:30:00Z
source: "transcript.json"
tags:
  - insightmesh
  - physics
---

# Speed of Light

The speed of light in a vacuum is approximately 299,792,458 meters per second...

## Historical Measurement

The first successful measurement was made by Ole Rømer in 1676...

## Related Topics

- [[Electromagnetic Spectrum]]
```

### Session log format

```json
{
  "session_id": "2026-05-16T103000",
  "timestamp": "2026-05-16T10:30:00Z",
  "source_transcript": "transcript.json",
  "exchanges_total": 4,
  "exchanges_processed": 4,
  "agents": {
    "synthesis": { "status": "success", "output": {...} },
    "historian": { "status": "success", "output": {...} },
    "editor": { "status": "success", "output": {...} }
  },
  "wiki_pages_created": ["Speed of Light.md"],
  "status": "completed",
  "duration_seconds": 45.2
}
```

## Verification

After running a batch:

1. **Check wiki pages**: Open Obsidian, navigate to InsightMesh folder, verify pages exist with coherent content
2. **Check cross-links**: If transcript covered multiple topics, verify `[[wiki links]]` connect related pages
3. **Check session log**: Inspect the JSON log file for complete agent outputs and no errors
4. **Check frontmatter**: Each wiki page should have `title`, `created`/`updated` (ISO 8601 UTC datetime), `source`, and `tags` in the YAML header

## Troubleshooting

- **"Vault path does not exist"**: Verify the `--vault` path points to your Obsidian vault root directory
- **"Invalid transcript format"**: Ensure the file is a JSON array of objects with `role` and `content` fields
- **"Empty transcript"**: The transcript file must contain at least one exchange
