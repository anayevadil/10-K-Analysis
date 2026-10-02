import pytest

from tenk.agent.evaluate import EvalCase, expected_value, extract_numbers, grade, load_cases
from tenk.agent.loop import AgentAnswer, Step
from tenk.service import load_sample


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Revenue was $1.30 billion [XBRL Revenues, FY2024].", [1.3e9]),
        ("about $1,300 million", [1.3e9]),
        ("gross margin of 40%", [0.4]),
        ("ROE was 15.3 percent", [0.153]),
        ("debt to equity of 0.55x", [0.55]),
        ("FCF of $138M, down -$5m", [138e6, -5e6]),
        ("in 2024 margin held", [2024.0]),
    ],
)
def test_extract_numbers(text, expected):
    assert extract_numbers(text) == pytest.approx(expected)


def answer(text, tools=()):
    return AgentAnswer("q", text, steps=[Step(t, {}, "{}") for t in tools])


@pytest.fixture(scope="module")
def data():
    return load_sample()


def test_number_within_tolerance_passes(data):
    case = EvalCase("roe", "q", item="roe", year=2024)
    assert grade(case, answer("ROE was 15.3% [computed ratio, FY2024]."), data) == []
    assert grade(case, answer("ROE was 14%."), data)


def test_tools_mentions_and_citations(data):
    case = EvalCase(
        "risks",
        "q",
        tools=["search_10k"],
        mentions_any=["chip"],
        must_not_mention=["you should buy"],
        cites=["10-K"],
    )
    good = answer("A chip shortage could delay deliveries [10-K Item 1A].", ["search_10k"])
    assert grade(case, good, data) == []
    bad = answer("You should buy it.", ["get_financials"])
    assert grade(case, bad, data) == [
        "didn't call search_10k",
        "mentions none of ['chip']",
        "mentions 'you should buy'",
        "no [10-K ...] citation",
    ]


def test_eval_file_has_twenty_valid_cases(data):
    cases = load_cases()
    assert len(cases) == 20
    assert len({c.id for c in cases}) == 20
    for case in cases:
        if case.item:
            assert expected_value(case, data) is not None
