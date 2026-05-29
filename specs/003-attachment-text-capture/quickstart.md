# Quickstart: Verify attachment & pasted text capture

How to confirm the feature end to end. All commands via `uv run`.

## Unit + type + lint

```bash
uv run pytest tests/test_exports.py tests/test_transcript.py -q
uv run mypy --strict src/
uv run ruff check src/ tests/
```

Expected: the new attachment tests pass (the attachment-only regression test fails on pre-feature code), no transcript-pairing regression, mypy and ruff clean.

## End to end against the fixture

The Claude fixture (`tests/fixtures/claude_ai_export.json`) gains a conversation with (a) an attachment-only message and (b) a message with both typed text and an attachment.

```bash
uv run python -c "
from pathlib import Path
from src.exports import extract_conversation
t = extract_conversation(Path('tests/fixtures/claude_ai_export.json'), '<fixture-conversation-id>')
text = '\n'.join((e.assistant_message.content if e.assistant_message else '') + e.user_message.content for e in t.exchanges)
assert 'Attached/pasted content' in text   # rendered block present
print('attachment text present:', 'EXTRACTED-MARKER' in text)
"
```

## End to end against real data (optional, manual)

Use a real Claude export known to contain pasted content (verified earlier: conversation "Environmental Sensor Technology Project", id `f152f34e-50c2-4f79-8ea7-198da09c074a`, has ~24KB of pasted text that was previously dropped):

```bash
uv run insightmesh batch ~/Downloads/<claude-export>/conversations.json \
  --conversation f152f34e-50c2-4f79-8ea7-198da09c074a \
  --vault ~/Documents/InsightMesh-test-vault
```

Confirm the synthesized page now reflects the pasted content (it did not before), and that no `--- Attached/pasted content ---` markers leak into the page.

## Acceptance mapping

- SC-001 / SC-002: pasted/attached extracted text appears in the transcript and the page (no silent drop).
- SC-003: a ChatGPT export produces the same result as before (no regression).
- SC-004: non-conversational content stays excluded.
- SC-005: empty-extracted-content attachments add nothing.
