"""One call that gathers everything the app shows for a company.

Kept free of Streamlit so it can be tested directly and reused by the agent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from tenk.cache import JsonCache
from tenk.demo import example_company_facts
from tenk.financials.ratios import compute_ratios
from tenk.financials.statements import Statements, build_statements
from tenk.nlp.sentiment import NewsMood, score_articles, tag_moods
from tenk.nlp.summarize import PROMPT_VERSION, Overview, summarize_filing
from tenk.sources.edgar import ONE_YEAR, CompanyProfile, EdgarClient, Filing
from tenk.sources.filing_text import filing_sections
from tenk.sources.news import NewsClient

DEMO_TICKER = "EXMPL"
# Overviews saved with scripts/record_demo.py, so the demo works without an API key.
DEMO_CACHE = Path(__file__).parent / "demo_cache"


@dataclass
class CompanyData:
    ticker: str
    profile: CompanyProfile
    statements: Statements
    ratios: pd.DataFrame
    latest_10k: Filing | None
    is_sample: bool = False
    overview: Overview | None = None


@dataclass(frozen=True)
class Headline:
    label: str
    value: float | None
    change: float | None  # year-over-year change as a fraction, e.g. 0.08 for +8%
    is_percent: bool = False


def load_company(ticker: str, client: EdgarClient, years: int = 5) -> CompanyData:
    ticker = ticker.strip().upper()
    profile = client.profile(ticker)
    statements = build_statements(client.company_facts(profile.cik), years=years)
    filings = client.filings(ticker, form="10-K", limit=1)
    return CompanyData(
        ticker=ticker,
        profile=profile,
        statements=statements,
        ratios=compute_ratios(statements),
        latest_10k=filings[0] if filings else None,
    )


def load_sample(years: int = 5) -> CompanyData:
    """The fictional Example Corp, for trying the app without network access."""
    statements = build_statements(example_company_facts(), years=years)
    profile = CompanyProfile(
        cik=statements.cik,
        name=f"{statements.company} (sample data)",
        tickers=[DEMO_TICKER],
        exchanges=["Sample"],
        sic="",
        sic_description="Fictional company used for demos and tests",
        fiscal_year_end="1231",
    )
    return CompanyData(
        ticker=DEMO_TICKER,
        profile=profile,
        statements=statements,
        ratios=compute_ratios(statements),
        latest_10k=None,
        is_sample=True,
        overview=saved_overview(DEMO_TICKER, "sample"),
    )


def _overview_key(filing: Filing) -> str:
    return f"overview:v{PROMPT_VERSION}:{filing.accession_number}"


def _saved(ticker: str) -> dict[str, Any] | None:
    path = DEMO_CACHE / f"{ticker.upper()}.json"
    return json.loads(path.read_text()) if path.exists() else None


def saved_overview(ticker: str, accession_number: str) -> Overview | None:
    """An overview recorded into ``demo_cache/`` for exactly this filing, if any."""
    saved = _saved(ticker)
    if not saved or saved["accession_number"] != accession_number:
        return None  # nothing saved, or saved for an older 10-K
    return Overview.from_dict(saved["overview"])


def saved_news(ticker: str) -> NewsMood | None:
    """Headlines with scores and mood tags recorded into ``demo_cache/``, if any."""
    saved = _saved(ticker)
    if not saved or "news" not in saved:
        return None
    return NewsMood.from_dict(saved["news"])


def find_overview(data: CompanyData, cache: JsonCache) -> Overview | None:
    """An overview that is already written, without calling Claude."""
    if data.overview or not data.latest_10k:
        return data.overview
    cached = cache.get(_overview_key(data.latest_10k))
    if cached is not None:
        return Overview.from_dict(cached)
    return saved_overview(data.ticker, data.latest_10k.accession_number)


def write_overview(
    data: CompanyData, edgar: EdgarClient, claude: Any, cache: JsonCache
) -> Overview:
    """Read the latest 10-K and have Claude write the overview (cached per filing)."""
    found = find_overview(data, cache)
    if found:
        return found
    filing = data.latest_10k
    if filing is None:
        raise ValueError(f"{data.ticker} has no 10-K to summarize.")
    sections = filing_sections(edgar.filing_document(filing))
    overview = summarize_filing(claude, data.profile.name, filing.report_date, sections)
    cache.set(_overview_key(filing), overview.to_dict(), ONE_YEAR)
    return overview


def load_news(data: CompanyData, client: NewsClient, scorer: Any | None) -> NewsMood:
    """Last 30 days of headlines, scored by FinBERT when ``scorer`` is given."""
    articles = client.articles(data.ticker, data.profile.name)
    return score_articles(articles, scorer)


def _moods_key(news: NewsMood) -> str:
    links = "\n".join(a.article.link for a in news.articles)
    return "moods:" + hashlib.sha256(links.encode()).hexdigest()


def find_moods(news: NewsMood, cache: JsonCache) -> NewsMood:
    """``news`` with Claude's mood tags if these exact headlines were tagged before."""
    if news.tagged:
        return news
    cached = cache.get(_moods_key(news))
    return NewsMood.from_dict(cached) if cached else news


def add_moods(data: CompanyData, news: NewsMood, claude: Any, cache: JsonCache) -> NewsMood:
    """Have Claude tag each headline's mood and summarize the news (cached for a day)."""
    found = find_moods(news, cache)
    if found.tagged or not news.articles:
        return found
    tagged = tag_moods(claude, data.profile.name, news)
    cache.set(_moods_key(news), tagged.to_dict(), 24 * 60 * 60)
    return tagged


def demo_record(
    data: CompanyData, overview: Overview, news: NewsMood | None = None, saved_on: str = ""
) -> dict[str, Any]:
    """The JSON saved in ``demo_cache/`` by scripts/record_demo.py."""
    filing = data.latest_10k
    if filing is None:
        raise ValueError(f"{data.ticker} has no 10-K to record.")
    record = {
        "ticker": data.ticker,
        "company": data.profile.name,
        "accession_number": filing.accession_number,
        "report_date": filing.report_date,
        "overview": overview.to_dict(),
    }
    if news is not None:
        record["news"] = {**news.to_dict(), "saved_on": saved_on}
    return record


def _change(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before)


def headlines(data: CompanyData) -> list[Headline]:
    """Latest-year revenue, net income, free cash flow and net margin, with YoY change."""
    s = data.statements
    periods = s.periods
    latest = periods[-1]
    prior = periods[-2] if len(periods) > 1 else None

    def metric(label: str, key: str) -> Headline:
        now = s.value(key, latest)
        before = s.value(key, prior) if prior else None
        return Headline(label, now, _change(now, before))

    margin = data.ratios.at["net_margin", latest]
    prior_margin = data.ratios.at["net_margin", prior] if prior else None
    margin_change = None
    if prior_margin is not None and not pd.isna(prior_margin) and not pd.isna(margin):
        margin_change = float(margin - prior_margin)  # percentage points
    return [
        metric("Revenue", "revenue"),
        metric("Net income", "net_income"),
        metric("Free cash flow", "free_cash_flow"),
        Headline(
            "Net margin",
            None if pd.isna(margin) else float(margin),
            margin_change,
            is_percent=True,
        ),
    ]


def format_usd(value: float | None) -> str:
    """Compact dollars: $1.30B, $391.04B, $850.00M, -$12.50M."""
    if value is None or pd.isna(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    value = abs(value)
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= size:
            return f"{sign}${value / size:,.2f}{suffix}"
    return f"{sign}${value:,.0f}"
