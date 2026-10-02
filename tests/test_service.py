import json
from pathlib import Path

import pytest
import responses

from tenk.cache import JsonCache
from tenk.demo import example_company_facts
from tenk.service import DEMO_TICKER, format_usd, headlines, load_company, load_sample
from tenk.sources.edgar import COMPANY_FACTS_URL, SUBMISSIONS_URL, TICKERS_URL, EdgarClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_load_company_from_edgar():
    client = EdgarClient(user_agent="tests test@example.com", cache=JsonCache(":memory:"))
    with responses.RequestsMock() as mock:
        mock.get(TICKERS_URL, json=load("company_tickers.json"))
        mock.get(SUBMISSIONS_URL.format(cik=320193), json=load("submissions_aapl.json"))
        mock.get(COMPANY_FACTS_URL.format(cik=320193), json=example_company_facts())
        data = load_company(" aapl ", client)
    assert data.ticker == "AAPL"
    assert data.profile.name == "Apple Inc."
    assert data.latest_10k.report_date == "2024-09-28"
    assert data.statements.periods[-1] == "2024-12-31"
    assert data.ratios.at["gross_margin", "2024-12-31"] == pytest.approx(0.4)
    assert not data.is_sample


def test_sample_company():
    data = load_sample()
    assert data.is_sample
    assert data.ticker == DEMO_TICKER
    assert "sample data" in data.profile.name
    assert data.latest_10k is None


def test_headlines():
    revenue, net_income, fcf, margin = headlines(load_sample())
    assert revenue.value == 1300e6
    assert revenue.change == pytest.approx(100 / 1200)
    assert net_income.value == pytest.approx(148e6)
    assert fcf.value == pytest.approx(138e6)
    assert margin.is_percent
    assert margin.value == pytest.approx(148 / 1300)
    assert margin.change == pytest.approx(148 / 1300 - 136 / 1200)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (391_035_000_000, "$391.04B"),
        (1_300_000_000, "$1.30B"),
        (850_000_000, "$850.00M"),
        (-12_500_000, "-$12.50M"),
        (2_500_000_000_000, "$2.50T"),
        (950, "$950"),
        (None, "n/a"),
    ],
)
def test_format_usd(value, text):
    assert format_usd(value) == text
