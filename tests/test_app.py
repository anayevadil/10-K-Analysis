import json
from pathlib import Path

import pytest
import responses
import streamlit as st
from streamlit.testing.v1 import AppTest

from tenk import service
from tenk.demo import example_company_facts
from tenk.nlp.sentiment import NewsMood, ScoredArticle
from tenk.nlp.summarize import Overview
from tenk.sources.edgar import COMPANY_FACTS_URL, SUBMISSIONS_URL, TICKERS_URL
from tenk.sources.news import NewsClient

APP = str(Path(__file__).parents[1] / "app" / "streamlit_app.py")
FIXTURES = Path(__file__).parent / "fixtures"
TEN_K_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No disk cache, no API key, and no results cached by earlier tests."""
    monkeypatch.setenv("TENK_CACHE_PATH", ":memory:")
    monkeypatch.setenv("SEC_USER_AGENT", "tests test@example.com")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def edgar():
    def load(name):
        return json.loads((FIXTURES / name).read_text())

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.get(TICKERS_URL, json=load("company_tickers.json"))
        mock.get(SUBMISSIONS_URL.format(cik=320193), json=load("submissions_aapl.json"))
        mock.get(COMPANY_FACTS_URL.format(cik=320193), json=example_company_facts())
        mock.get(TEN_K_URL, body=(FIXTURES / "10k_example.htm").read_text())
        urls = NewsClient(cache=None).feed_urls("AAPL", "Apple Inc.", 30)
        mock.get(urls["Google News"], body=(FIXTURES / "news_google.xml").read_text())
        mock.get(urls["Yahoo Finance"], body=(FIXTURES / "news_yahoo.xml").read_text())
        yield mock


def run_app():
    return AppTest.from_file(APP, default_timeout=30).run()


def analyze(at, sample=False):
    if sample:
        at.toggle[0].set_value(True)
    return at.button[0].click().run()


def test_start_page_asks_for_a_ticker():
    at = run_app()
    assert not at.exception
    assert "Enter a US ticker" in at.info[0].value


def test_sample_mode_renders_company():
    at = analyze(run_app(), sample=True)
    assert not at.exception
    assert at.header[0].value == "Example Corp (sample data)"
    labels = [m.label for m in at.metric]
    assert labels[:4] == ["Revenue", "Net income", "Free cash flow", "Net margin"]
    assert at.metric[0].value == "$1.30B"
    assert len(at.dataframe) == 4


def test_sample_mode_shows_the_saved_overview():
    at = analyze(run_app(), sample=True)
    assert "Company overview" in [s.value for s in at.subheader]
    text = " ".join(m.value for m in at.markdown)
    assert "Fewer new office buildings" in text
    assert "Sensors and hardware" in text


def test_missing_user_agent_shows_error(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    at = analyze(run_app())
    assert not at.exception
    assert "User-Agent" in at.error[0].value


def test_without_api_key_the_overview_explains_what_it_needs(edgar):
    at = analyze(run_app())
    assert not at.exception
    assert at.header[0].value == "Apple Inc."
    assert "needs an Anthropic API key" in at.info[0].value


def test_sample_mode_shows_saved_news():
    at = analyze(run_app(), sample=True)
    assert "News and mood" in [s.value for s in at.subheader]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Overall tone"] == "Neutral"
    assert metrics["Headlines"] == "10"
    text = " ".join(m.value for m in at.markdown)
    assert "What the news is about" in text
    assert "Chip shortage could delay Example Corp deliveries" in text
    assert "*fear*" in text


def test_live_news_without_finbert_or_key_lists_headlines(edgar):
    at = analyze(run_app())
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Apple faces EU fine over App Store rules" in text
    assert any("Install FinBERT" in i.value for i in at.info)
    assert "Tag the mood with Claude" not in [b.label for b in at.button]


def test_with_api_key_claude_tags_the_news_on_request(edgar, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_tag(client, company, news):
        articles = [ScoredArticle(a.article, a.sentiment, "uncertainty") for a in news.articles]
        return NewsMood(articles, summary="Regulators and AI plans dominate.", tagged_by="test")

    monkeypatch.setattr(service, "tag_moods", fake_tag)
    at = analyze(run_app())
    tag = next(b for b in at.button if b.label == "Tag the mood with Claude")
    at = tag.click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Regulators and AI plans dominate." in text
    assert "*uncertainty*" in text


def test_with_api_key_claude_writes_the_overview_on_request(edgar, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []

    def fake_summarize(client, company, fiscal_year, sections):
        calls.append(sections)
        return Overview.from_dict(
            {
                "business_summary": "Apple designs phones and computers.",
                "revenue_sources": [{"name": "iPhone", "description": "Phones."}],
                "key_risks": [{"title": "Supply chain", "explanation": "Few factories."}],
                "management_highlights": ["Services grew."],
            }
        )

    monkeypatch.setattr(service, "summarize_filing", fake_summarize)
    at = analyze(run_app())
    assert not at.exception
    assert not calls  # nothing is spent until the button is pressed
    write = next(b for b in at.button if b.label == "Write the overview from the 10-K")
    at = write.click().run()
    assert not at.exception
    assert len(calls) == 1
    assert "chip suppliers" in calls[0]["risk_factors"]
    assert any("Apple designs phones" in m.value for m in at.markdown)
