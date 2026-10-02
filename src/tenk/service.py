"""One call that gathers everything the app shows for a company.

Kept free of Streamlit so it can be tested directly and reused by the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tenk.demo import example_company_facts
from tenk.financials.ratios import compute_ratios
from tenk.financials.statements import Statements, build_statements
from tenk.sources.edgar import CompanyProfile, EdgarClient, Filing

DEMO_TICKER = "EXMPL"


@dataclass
class CompanyData:
    ticker: str
    profile: CompanyProfile
    statements: Statements
    ratios: pd.DataFrame
    latest_10k: Filing | None
    is_sample: bool = False


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
    )


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
