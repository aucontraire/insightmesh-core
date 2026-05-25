"""Tests for src.orchestrator JSON extraction (regression coverage).

Focused on `_try_extract_json`, which had a two-condition failure surfaced
during Spec 002 real-data testing:

1. The SDK appended trailing metadata (`agentId:` resumption line + `<usage>`
   block) after the JSON, so the response no longer ended with `}` — defeating
   the `startswith("{") and endswith("}")` happy path.
2. The agent's `draft_content` contained a fenced code block (a markdown
   drum-tab pattern), so the fence-matching fallback grabbed that inner fence
   instead of the JSON.

Either alone was survivable; together they produced an unparseable candidate.
The fix uses `json.JSONDecoder().raw_decode` from the first `{`, which ignores
trailing data and tolerates braces/fences inside string values.
"""

from __future__ import annotations

import json

from src.orchestrator import _parse_agent_output, _try_extract_json

# A synthesis response that reproduces BOTH failure conditions:
# - draft_content contains a fenced code block (the ```...``` drum tab)
# - trailing agentId: + <usage> metadata after the closing brace
# Built via concatenation so physical lines stay under the line-length limit
# while the embedded JSON-escaped content stays realistic.
_DRUM_DRAFT = (
    "## Playing Behind the Beat\\n\\nA drum pattern:\\n\\n"
    "```\\nKick:  o - - -\\nSnare: - - o -\\nCount: 1 2 3 4\\n```\\n\\n"
    "Sit behind the click."
)
_REGRESSION_RESPONSE = (
    "{\n"
    '  "drafts": [\n'
    "    {\n"
    '      "tentative_title": "Reggae Bass - Self-Study Starting Point",\n'
    '      "exchange_indices": [1, 2],\n'
    f'      "draft_content": "{_DRUM_DRAFT}",\n'
    '      "suggested_tags": ["reggae", "bass", "self-study"]\n'
    "    },\n"
    "    {\n"
    '      "tentative_title": "BOSS TU-3 Power Troubleshooting",\n'
    '      "exchange_indices": [3, 4],\n'
    '      "draft_content": "## DC OUT Jack\\n\\nThe TU-3 can daisy-chain power.",\n'
    '      "suggested_tags": ["boss-tu-3", "guitar-pedal"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "agentId: a6d604a41f0623d4e (use SendMessage with to: 'a6d604a41f0623d4e')\n"
    "<usage>total_tokens: 13394\ntool_uses: 0\nduration_ms: 49623</usage>"
)


class TestTryExtractJson:
    def test_regression_trailing_metadata_plus_inner_fence(self) -> None:
        """The exact Spec 002 real-data failure: trailing SDK metadata + a fenced
        code block inside draft_content. Extracted candidate must be valid JSON."""
        candidate = _try_extract_json(_REGRESSION_RESPONSE)
        parsed = json.loads(candidate)  # must not raise
        assert [d["tentative_title"] for d in parsed["drafts"]] == [
            "Reggae Bass - Self-Study Starting Point",
            "BOSS TU-3 Power Troubleshooting",
        ]

    def test_pure_json_happy_path(self) -> None:
        raw = '{"drafts": []}'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_trailing_metadata_only(self) -> None:
        raw = '{"drafts": []}\nagentId: abc123\n<usage>total_tokens: 5</usage>'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_inner_fence_only(self) -> None:
        """draft_content with a fenced code block but no trailing metadata."""
        raw = '{"drafts": [{"draft_content": "see ```\\ncode\\n``` here"}]}'
        parsed = json.loads(_try_extract_json(raw))
        assert "```" in parsed["drafts"][0]["draft_content"]

    def test_json_wrapped_in_json_fence(self) -> None:
        """Agent wraps the whole JSON in a ```json fence (older failure mode)."""
        raw = '```json\n{"drafts": []}\n```'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_prose_around_json(self) -> None:
        raw = 'Here is the output:\n{"drafts": []}\nLet me know if you need more.'
        assert json.loads(_try_extract_json(raw)) == {"drafts": []}

    def test_no_json_returns_stripped(self) -> None:
        raw = "  no json here  "
        assert _try_extract_json(raw) == "no json here"


class TestParseAgentOutputRegression:
    def test_synthesis_parses_through_full_chain(self) -> None:
        """_parse_agent_output must produce a SynthesisOutput from the regression
        response (end-to-end: extraction + Pydantic validation)."""
        result = _parse_agent_output("synthesis", _REGRESSION_RESPONSE)
        assert result is not None
        assert len(result.drafts) == 2
        assert result.drafts[0].tentative_title == "Reggae Bass - Self-Study Starting Point"
        # The fenced drum-tab survived intact inside the draft content.
        assert "Count: 1 2 3 4" in result.drafts[0].draft_content
        assert "```" in result.drafts[0].draft_content
