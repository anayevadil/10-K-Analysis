import json
from pathlib import Path

import pytest
import responses

from tenk.cache import JsonCache
from tenk.sources.edgar import (
    COMPANY_FACTS_URL,
    SUBMISSIONS_URL,
    TICKERS_URL,
    EdgarClient,
    EdgarError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client():
    return EdgarClient(user_agent="tests test@example.com", cache=JsonCache(":memory:"))


@pytest.fixture
def edgar():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.get(TICKERS_URL, json=load("company_tickers.json"))
        mock.get(SUBMISSIONS_URL.format(cik=320193), json=load("submissions_aapl.json"))
        yield mock


def test_requires_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(EdgarError, match="User-Agent"):
        EdgarClient(cache=JsonCache(":memory:"))


def test_sends_user_agent(client, edgar):
    client.ticker_to_cik("AAPL")
    assert edgar.calls[0].request.headers["User-Agent"] == "tests test@example.com"


@pytest.mark.parametrize("ticker", ["AAPL", "aapl", " AAPL "])
def test_ticker_to_cik(client, edgar, ticker):
    assert client.ticker_to_cik(ticker) == 320193


def test_class_share_ticker_with_dot(client, edgar):
    assert client.ticker_to_cik("BRK.B") == 1067983


def test_unknown_ticker(client, edgar):
    with pytest.raises(EdgarError, match="Unknown ticker"):
        client.ticker_to_cik("ZZZZ")


def test_profile(client, edgar):
    profile = client.profile("AAPL")
    assert profile.name == "Apple Inc."
    assert profile.sic_description == "Electronic Computers"
    assert profile.fiscal_year_end == "0928"


def test_filings_returns_only_10ks_newest_first(client, edgar):
    filings = client.filings("AAPL", form="10-K")
    assert [f.report_date for f in filings] == ["2024-09-28", "2023-09-30"]
    assert filings[0].url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
    )


def test_latest_10k(client, edgar):
    assert client.latest_10k("AAPL").accession_number == "0000320193-24-000123"


def test_responses_are_cached(client, edgar):
    client.profile("AAPL")
    client.profile("AAPL")
    assert len(edgar.calls) == 2  # tickers + submissions, each fetched once


def test_company_facts_not_found(client):
    with responses.RequestsMock() as mock:
        mock.get(COMPANY_FACTS_URL.format(cik=1), status=404)
        with pytest.raises(EdgarError, match="Not found"):
            client.company_facts(1)
