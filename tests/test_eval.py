"""The agent eval set. Calls the live Claude API, so it only runs on request:

    ANTHROPIC_API_KEY=... pytest -m eval

Results, including every answer and tool call, are written to evals/results.json.
"""

import json
import os
from pathlib import Path

import pytest

from tenk.agent.evaluate import grade, load_cases, summarize
from tenk.agent.loop import ask
from tenk.agent.tools import Toolbox, ToolError, sample_resources
from tenk.service import DEMO_TICKER, load_sample

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"),
]
RESULTS = Path(__file__).parents[1] / "evals" / "results.json"
CASES = load_cases()


def only_sample(ticker):
    if ticker.upper() != DEMO_TICKER:
        raise ToolError("The eval only has data for EXMPL.")
    return sample_resources()


@pytest.fixture(scope="module")
def results():
    collected = []
    yield collected
    RESULTS.write_text(
        json.dumps({"summary": summarize(collected), "results": collected}, indent=2) + "\n"
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_agent_answers(case, results):
    import anthropic

    data = load_sample()
    answer = ask(
        anthropic.Anthropic(), Toolbox(only_sample), DEMO_TICKER, "Example Corp", case.question
    )
    failures = grade(case, answer, data)
    results.append({"id": case.id, "failures": failures, **answer.to_dict()})
    assert not failures, f"{case.question}\n{answer.answer}\n{failures}"
