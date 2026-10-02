import json
from pathlib import Path

import pytest
import responses
from conftest import FakeClaude, text

from tenk import service
from tenk.cache import JsonCache
from tenk.demo import example_company_facts
from tenk.service import (
    DEMO_TICKER,
    find_overview,
    format_usd,
    headlines,
    load_company,
    load_sample,
    saved_overview,
    write_overview,
)
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


OVERVIEW = {
    "business_summary": "Apple designs phones and computers.",
    "revenue_sources": [{"name": "iPhone", "description": "Phones."}],
    "key_risks": [{"title": "Supply chain", "explanation": "Made in a few places."}],
    "management_highlights": ["Services grew."],
    "missing_sections": [],
}


@pytest.fixture
def apple():
    """Apple's company data plus a mocked 10-K document, with fresh caches."""
    edgar = EdgarClient(user_agent="tests test@example.com", cache=JsonCache(":memory:"))
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.get(TICKERS_URL, json=load("company_tickers.json"))
        mock.get(SUBMISSIONS_URL.format(cik=320193), json=load("submissions_aapl.json"))
        mock.get(COMPANY_FACTS_URL.format(cik=320193), json=example_company_facts())
        data = load_company("AAPL", edgar)
        mock.get(data.latest_10k.url, body=(FIXTURES / "10k_example.htm").read_text())
        yield data, edgar, mock


def test_write_overview_reads_the_10k_and_caches_by_filing(apple):
    data, edgar, mock = apple
    claude = FakeClaude([text(json.dumps(OVERVIEW))])
    cache = JsonCache(":memory:")
    assert find_overview(data, cache) is None

    overview = write_overview(data, edgar, claude, cache)
    assert overview.business_summary == "Apple designs phones and computers."
    prompt = claude.request["messages"][0]["content"]
    assert "Company: Apple Inc." in prompt
    assert "Fiscal year ended: 2024-09-28" in prompt
    assert "chip suppliers" in prompt  # risk factors from the 10-K document

    assert write_overview(data, edgar, claude, cache) == overview
    assert find_overview(data, cache) == overview
    assert len(claude.requests) == 1


def test_saved_demo_overview_is_used_only_for_the_same_filing(apple, tmp_path, monkeypatch):
    data, _, _ = apple
    record = service.demo_record(data, service.Overview.from_dict(OVERVIEW))
    (tmp_path / "AAPL.json").write_text(json.dumps(record))
    monkeypatch.setattr(service, "DEMO_CACHE", tmp_path)

    assert find_overview(data, JsonCache(":memory:")).key_risks[0].title == "Supply chain"
    assert saved_overview("aapl", "0000320193-23-000106") is None  # older 10-K
    assert saved_overview("MSFT", data.latest_10k.accession_number) is None


def test_sample_company_has_a_saved_overview():
    data = load_sample()
    overview = find_overview(data, JsonCache(":memory:"))
    assert overview is data.overview
    assert "fictional" in overview.business_summary
    assert len(overview.key_risks) == 5
