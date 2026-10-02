"""The tools the analyst agent can call, built on the same functions the app uses.

Every tool returns JSON text. Numbers come straight from the normalised XBRL
statements, so the agent can quote them and cite the tag they came from.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tenk.demo import example_10k_sections
from tenk.financials.ratios import PERCENT_RATIOS, RATIO_LABELS
from tenk.financials.statements import DERIVED
from tenk.financials.xbrl_map import ALL_ITEMS
from tenk.nlp.search import TenKIndex
from tenk.nlp.sentiment import NewsMood
from tenk.service import DEMO_TICKER, CompanyData, load_company, load_sample, saved_news
from tenk.sources.edgar import EdgarClient
from tenk.sources.filing_text import filing_sections

ITEMS = {item.key: item for item in ALL_ITEMS}
FINANCIAL_KEYS = [*ITEMS, *RATIO_LABELS]
MAX_YEARS = 5
DERIVATIONS = {
    "gross_profit": "revenue minus cost of revenue",
    "total_liabilities": "total assets minus total equity",
    "free_cash_flow": "cash from operations minus capital expenditures",
}


class ToolError(Exception):
    """A problem the agent should see and can recover from, e.g. an unknown ticker."""


@dataclass
class CompanyResources:
    """What the tools can read for one company; the slow parts load on first use."""

    data: CompanyData
    load_sections: Callable[[], dict[str, str]]
    load_news: Callable[[], NewsMood | None]
    _index: TenKIndex | None = field(default=None, init=False)

    def index(self) -> TenKIndex:
        if self._index is None:
            self._index = TenKIndex(self.load_sections())
        return self._index


def sample_resources() -> CompanyResources:
    """The fictional Example Corp, with its sample 10-K text and headlines."""
    return CompanyResources(load_sample(), example_10k_sections, lambda: saved_news(DEMO_TICKER))


def edgar_resources(
    ticker: str,
    edgar: EdgarClient,
    load_news: Callable[[CompanyData], NewsMood | None],
    data: CompanyData | None = None,
) -> CompanyResources:
    """A real company: XBRL numbers now, 10-K text and news when a tool first needs them."""
    if ticker.strip().upper() == DEMO_TICKER:
        return sample_resources()
    data = data or load_company(ticker, edgar)

    def sections() -> dict[str, str]:
        if data.latest_10k is None:
            raise ToolError(f"{data.ticker} has no 10-K on EDGAR.")
        found = filing_sections(edgar.filing_document(data.latest_10k))
        if not found:
            raise ToolError("Couldn't find the Business, Risk Factors or MD&A sections.")
        return found

    return CompanyResources(data, sections, lambda: load_news(data))


class Toolbox:
    """Runs tool calls. ``load`` returns the resources for a ticker or raises."""

    def __init__(self, load: Callable[[str], CompanyResources]) -> None:
        self._load = load
        self._companies: dict[str, CompanyResources] = {}

    def company(self, ticker: str) -> CompanyResources:
        ticker = ticker.strip().upper()
        if ticker not in self._companies:
            self._companies[ticker] = self._load(ticker)
        return self._companies[ticker]

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool: {name}")
        return json.dumps(handler(**arguments), indent=1)

    # --- tools -------------------------------------------------------------

    def _tool_get_company_profile(self, ticker: str) -> dict[str, Any]:
        data = self.company(ticker).data
        p = data.profile
        filing = data.latest_10k
        return {
            "ticker": data.ticker,
            "name": p.name,
            "cik": p.cik,
            "exchanges": p.exchanges,
            "industry": p.sic_description,
            "fiscal_year_end": p.fiscal_year_end,
            "fiscal_years_available": [period[:4] for period in data.statements.periods],
            "latest_10k": None
            if filing is None
            else {"period": filing.report_date, "filed": filing.filing_date, "url": filing.url},
        }

    def _tool_get_financials(
        self, ticker: str, items: list[str], years: int = MAX_YEARS
    ) -> dict[str, Any]:
        unknown = [key for key in items if key not in FINANCIAL_KEYS]
        if unknown:
            raise ToolError(f"Unknown items {unknown}. Choose from {FINANCIAL_KEYS}.")
        data = self.company(ticker).data
        s = data.statements
        periods = s.periods[-max(1, min(years, MAX_YEARS)) :]
        result: dict[str, Any] = {"ticker": data.ticker, "fiscal_year_ends": periods, "items": {}}
        for key in items:
            if key in RATIO_LABELS:
                values = {p: _number(data.ratios.at[key, p]) for p in periods}
                result["items"][key] = {
                    "label": RATIO_LABELS[key],
                    "unit": "fraction (0.25 = 25%)" if key in PERCENT_RATIOS else "multiple",
                    "values": values,
                    "source": "computed by this app from the statement values",
                }
                continue
            item = ITEMS[key]
            values = {p: _number(s.value(key, p)) for p in periods}
            sources = {p: s.sources.get((key, p)) for p in periods if values[p] is not None}
            result["items"][key] = {
                "label": item.label,
                "unit": item.unit,
                "values": values,
                "source": {
                    p: f"derived: {DERIVATIONS[key]}" if tag == DERIVED else f"XBRL us-gaap:{tag}"
                    for p, tag in sources.items()
                },
            }
        return result

    def _tool_search_10k(self, ticker: str, query: str, top_k: int = 4) -> dict[str, Any]:
        resources = self.company(ticker)
        filing = resources.data.latest_10k
        hits = resources.index().search(query, top_k=max(1, min(top_k, 8)))
        return {
            "ticker": resources.data.ticker,
            "filing_period": filing.report_date if filing else "sample 10-K",
            "results": [
                {
                    "section": p.section_label,
                    "passage_id": p.id,
                    "score": round(score, 2),
                    "text": p.text,
                }
                for p, score in hits
            ],
            "note": "" if hits else "No passage matched. Try other keywords.",
        }

    def _tool_get_news_sentiment(self, ticker: str) -> dict[str, Any]:
        resources = self.company(ticker)
        news = resources.load_news()
        if news is None or not news.articles:
            return {"ticker": resources.data.ticker, "headlines": [], "note": "No recent news."}
        return {
            "ticker": resources.data.ticker,
            "overall_tone": news.tone,
            "average_finbert_score": _number(news.average_score),
            "tone_counts": news.sentiment_counts() if news.scored else None,
            "mood_counts": news.mood_counts() if news.tagged else None,
            "summary": news.summary,
            "saved_on": news.saved_on,
            "headlines": [
                {
                    "date": a.article.published[:10],
                    "title": a.article.title,
                    "source": a.article.source,
                    "finbert": None
                    if a.sentiment is None
                    else {"label": a.sentiment.label, "score": round(a.sentiment.score, 2)},
                    "mood": a.mood,
                }
                for a in news.articles[:25]
            ],
        }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else round(value, 6)


TICKER = {"type": "string", "description": "US stock ticker, e.g. AAPL."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_company_profile",
        "description": (
            "Basic facts about a company from SEC EDGAR: name, industry, exchanges, fiscal year "
            "end, which fiscal years have data, and the latest 10-K's period, filing date and "
            "link. Call this first when you need to know which fiscal years exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": TICKER},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_financials",
        "description": (
            "Annual numbers from the company's 10-K XBRL data, for up to the last 5 fiscal "
            "years. Includes income statement, balance sheet and cash flow line items (in USD, "
            "EPS in USD per share, shares as a count) and ratios (margins, growth and ROE as "
            "fractions, current ratio and debt to equity as multiples). Each line item says "
            "which XBRL tag it came from. Call this for every number you state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": TICKER,
                "items": {
                    "type": "array",
                    "items": {"type": "string", "enum": FINANCIAL_KEYS},
                    "description": "Line items and ratios to return.",
                },
                "years": {
                    "type": "integer",
                    "description": "How many recent fiscal years, 1 to 5.",
                },
            },
            "required": ["ticker", "items", "years"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_10k",
        "description": (
            "Keyword search over the latest 10-K's Business (Item 1), Risk Factors (Item 1A) and "
            "MD&A (Item 7) sections. Returns the best-matching passages with their section. Call "
            "this for questions about what the company does, why numbers changed, risks, "
            "segments or outlook. Use specific keywords; search again with other words if the "
            "first results miss."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": TICKER,
                "query": {"type": "string", "description": "Keywords, e.g. 'chip suppliers'."},
                "top_k": {"type": "integer", "description": "Passages to return, 1 to 8."},
            },
            "required": ["ticker", "query", "top_k"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_news_sentiment",
        "description": (
            "Headlines from the last 30 days with FinBERT tone scores (-1 negative to +1 "
            "positive), Claude mood tags when available, and the overall tone. Call this for "
            "questions about recent news, market mood or what people are saying."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": TICKER},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
]
