import json
from types import SimpleNamespace

import pytest
from conftest import FakeClaude, text

from tenk.nlp.summarize import (
    FALLBACK_BETA,
    MAX_SECTION_CHARS,
    MODEL,
    OVERVIEW_SCHEMA,
    Overview,
    OverviewError,
    build_prompt,
    summarize_filing,
)

OVERVIEW = {
    "business_summary": "Example Corp makes building sensors.",
    "revenue_sources": [{"name": "Hardware", "description": "Sensors."}],
    "key_risks": [{"title": "Chip supply", "explanation": "Few suppliers."}],
    "management_highlights": ["Sales rose 8%."],
    "missing_sections": [],
}
SECTIONS = {"business": "Item 1. Business\nSensors.", "mdna": "Item 7. MD&A\nSales rose."}


def test_returns_structured_overview():
    claude = FakeClaude([text(json.dumps(OVERVIEW))])
    overview = summarize_filing(claude, "Example Corp", "2024-12-31", SECTIONS)
    assert overview.business_summary == "Example Corp makes building sensors."
    assert overview.key_risks[0].title == "Chip supply"
    assert overview.revenue_sources[0].name == "Hardware"
    assert overview.model == MODEL


def test_request_uses_schema_effort_and_server_side_fallback():
    claude = FakeClaude([text(json.dumps(OVERVIEW))])
    summarize_filing(claude, "Example Corp", "2024-12-31", SECTIONS)
    request = claude.request
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert request["output_config"]["effort"] == "medium"
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": OVERVIEW_SCHEMA}
    assert "buy, sell or hold" in request["system"]
    assert "Item 1. Business\nSensors." in request["messages"][0]["content"]


def test_schema_requires_every_field():
    assert set(OVERVIEW_SCHEMA["required"]) == set(OVERVIEW_SCHEMA["properties"])
    assert OVERVIEW_SCHEMA["additionalProperties"] is False


def test_text_before_a_fallback_block_is_discarded():
    content = [
        text('{"business_summary": "partial'),
        SimpleNamespace(type="fallback"),
        text(json.dumps(OVERVIEW)),
    ]
    claude = FakeClaude(content, model="claude-opus-4-8")
    overview = summarize_filing(claude, "Example Corp", "2024-12-31", SECTIONS)
    assert overview.business_summary == "Example Corp makes building sensors."
    assert overview.model == "claude-opus-4-8"


def test_refusal_raises():
    claude = FakeClaude([], stop_reason="refusal")
    with pytest.raises(OverviewError, match="declined"):
        summarize_filing(claude, "Example Corp", "2024-12-31", SECTIONS)


def test_cut_off_answer_raises():
    claude = FakeClaude([text('{"business_')], stop_reason="max_tokens")
    with pytest.raises(OverviewError, match="cut off"):
        summarize_filing(claude, "Example Corp", "2024-12-31", SECTIONS)


def test_no_sections_raises_without_calling_claude():
    claude = FakeClaude([])
    with pytest.raises(OverviewError, match="Couldn't find"):
        summarize_filing(claude, "Example Corp", "2024-12-31", {})
    assert claude.request is None


def test_prompt_marks_missing_and_truncates_long_sections():
    prompt = build_prompt(
        "Example Corp", "2024-12-31", {"business": "x" * (MAX_SECTION_CHARS + 50)}
    )
    assert "[... section truncated ...]" in prompt
    assert "x" * (MAX_SECTION_CHARS + 1) not in prompt
    assert prompt.count("(not found in the filing)") == 2


def test_overview_round_trips_through_json():
    overview = Overview.from_dict({**OVERVIEW, "model": MODEL})
    assert Overview.from_dict(json.loads(json.dumps(overview.to_dict()))) == overview
