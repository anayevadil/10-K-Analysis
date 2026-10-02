import json

import pytest
from conftest import ScriptedClaude, reply, text, tool_use

from tenk.agent.loop import (
    AGENT_TOOLS,
    FALLBACK_BETA,
    MAX_MODEL_CALLS,
    AgentAnswer,
    ask,
)
from tenk.agent.tools import TOOLS, Toolbox, ToolError, sample_resources


def load(ticker):
    if ticker != "EXMPL":
        raise ToolError(f"Unknown ticker {ticker}")
    return sample_resources()


@pytest.fixture
def toolbox():
    return Toolbox(load)


def run(toolbox, name, **arguments):
    return json.loads(toolbox.run(name, arguments))


# --- tools ------------------------------------------------------------------


def test_profile(toolbox):
    profile = run(toolbox, "get_company_profile", ticker="exmpl")
    assert profile["name"] == "Example Corp (sample data)"
    assert profile["fiscal_years_available"] == ["2020", "2021", "2022", "2023", "2024"]


def test_financials_cite_the_xbrl_tag_per_year(toolbox):
    result = run(toolbox, "get_financials", ticker="EXMPL", items=["revenue"], years=5)
    revenue = result["items"]["revenue"]
    assert revenue["values"]["2024-12-31"] == 1_300_000_000
    assert revenue["source"]["2020-12-31"] == "XBRL us-gaap:SalesRevenueNet"
    assert revenue["source"]["2024-12-31"].endswith(
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


def test_financials_ratios_and_derived_items(toolbox):
    result = run(
        toolbox, "get_financials", ticker="EXMPL", items=["roe", "total_liabilities"], years=1
    )
    assert result["fiscal_year_ends"] == ["2024-12-31"]
    assert result["items"]["roe"]["values"]["2024-12-31"] == pytest.approx(0.152577, abs=1e-6)
    liabilities = result["items"]["total_liabilities"]
    assert liabilities["source"]["2024-12-31"] == "derived: total assets minus total equity"


def test_first_year_growth_is_null_not_nan(toolbox):
    result = run(toolbox, "get_financials", ticker="EXMPL", items=["revenue_growth"], years=5)
    assert result["items"]["revenue_growth"]["values"]["2020-12-31"] is None


def test_unknown_item_is_a_tool_error(toolbox):
    with pytest.raises(ToolError, match="Unknown items"):
        toolbox.run("get_financials", {"ticker": "EXMPL", "items": ["ebitda"], "years": 1})


def test_search_10k(toolbox):
    result = run(toolbox, "search_10k", ticker="EXMPL", query="chip shortage", top_k=2)
    assert result["results"][0]["section"] == "Item 1A. Risk Factors"
    assert "chip" in result["results"][0]["text"]


def test_news(toolbox):
    news = run(toolbox, "get_news_sentiment", ticker="EXMPL")
    assert news["overall_tone"] == "Neutral"
    assert news["headlines"][0]["mood"] == "confidence"


def test_tool_schemas_are_strict_and_complete():
    assert [t["name"] for t in TOOLS] == [
        "get_company_profile",
        "get_financials",
        "search_10k",
        "get_news_sentiment",
    ]
    for tool in AGENT_TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


# --- loop -------------------------------------------------------------------


def test_loop_runs_tools_until_claude_answers(toolbox):
    claude = ScriptedClaude(
        reply(
            text("Let me look that up."),
            tool_use("get_financials", {"ticker": "EXMPL", "items": ["revenue"], "years": 1}),
        ),
        reply(text("Revenue was $1.30 billion in 2024 [XBRL Revenues, FY2024].")),
    )
    steps = []
    answer = ask(
        claude, toolbox, "EXMPL", "Example Corp", "What was revenue?", on_step=steps.append
    )

    assert answer.answer == "Revenue was $1.30 billion in 2024 [XBRL Revenues, FY2024]."
    assert answer.tools_used == ["get_financials"]
    assert steps == answer.steps
    assert answer.model_calls == 2
    assert answer.input_tokens == 200
    assert not answer.stopped

    first, second = claude.requests
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default"
    assert first["betas"] == [FALLBACK_BETA]
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "EXMPL" in first["system"] and "investment advice" in first["system"]
    assert first["messages"] == [{"role": "user", "content": "What was revenue?"}]

    assistant, results = second["messages"][1:]
    assert assistant["role"] == "assistant"
    assert assistant["content"][1].name == "get_financials"  # the full turn goes back
    result = results["content"][0]
    assert result["tool_use_id"] == "toolu_1"
    assert result["is_error"] is False
    assert json.loads(result["content"])["items"]["revenue"]["values"]["2024-12-31"] == 1.3e9


def test_parallel_tool_calls_go_back_in_one_message(toolbox):
    claude = ScriptedClaude(
        reply(
            tool_use("get_company_profile", {"ticker": "EXMPL"}, id="a"),
            tool_use("get_news_sentiment", {"ticker": "EXMPL"}, id="b"),
        ),
        reply(text("Done.")),
    )
    answer = ask(claude, toolbox, "EXMPL", "Example Corp", "Overview?")
    results = claude.requests[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert answer.tools_used == ["get_company_profile", "get_news_sentiment"]


def test_tool_errors_are_reported_to_claude(toolbox):
    claude = ScriptedClaude(
        reply(tool_use("get_company_profile", {"ticker": "ZZZZ"})),
        reply(text("I couldn't find that ticker.")),
    )
    answer = ask(claude, toolbox, "EXMPL", "Example Corp", "Tell me about ZZZZ")
    result = claude.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True
    assert "Unknown ticker ZZZZ" in result["content"]
    assert answer.steps[0].is_error


def test_refusal_stops_the_loop(toolbox):
    claude = ScriptedClaude(reply(stop_reason="refusal"))
    answer = ask(claude, toolbox, "EXMPL", "Example Corp", "?")
    assert answer.stopped == "refusal"
    assert "declined" in answer.answer


def test_step_limit(toolbox):
    responses = [
        reply(tool_use("get_company_profile", {"ticker": "EXMPL"})) for _ in range(MAX_MODEL_CALLS)
    ]
    answer = ask(ScriptedClaude(*responses), toolbox, "EXMPL", "Example Corp", "Loop forever")
    assert answer.stopped == "max_model_calls"
    assert len(answer.steps) == MAX_MODEL_CALLS


def test_answer_round_trips_through_json(toolbox):
    claude = ScriptedClaude(
        reply(tool_use("get_company_profile", {"ticker": "EXMPL"})), reply(text("Hi."))
    )
    answer = ask(claude, toolbox, "EXMPL", "Example Corp", "Who?")
    assert AgentAnswer.from_dict(json.loads(json.dumps(answer.to_dict()))) == answer
