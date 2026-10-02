"""Streamlit front end. Run with: streamlit run app/streamlit_app.py"""

from __future__ import annotations

import os

import anthropic
import pandas as pd
import requests
import streamlit as st

from tenk.cache import JsonCache
from tenk.export.excel import workbook_bytes
from tenk.financials.ratios import PERCENT_RATIOS, RATIO_LABELS
from tenk.financials.xbrl_map import ALL_ITEMS, USD_PER_SHARE
from tenk.nlp.summarize import Overview, OverviewError
from tenk.service import (
    CompanyData,
    find_overview,
    format_usd,
    headlines,
    load_company,
    load_sample,
    write_overview,
)
from tenk.sources.edgar import EdgarClient, EdgarError

LABELS = {item.key: item.label for item in ALL_ITEMS}
UNITS = {item.key: item.unit for item in ALL_ITEMS}
CACHE_SECONDS = 6 * 60 * 60

st.set_page_config(page_title="10-K Analyzer", page_icon="📊", layout="wide")


def secret(name: str) -> str | None:
    """From .streamlit/secrets.toml (or Streamlit Cloud secrets), else the environment."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except FileNotFoundError:
        pass
    return os.environ.get(name)


def sec_user_agent() -> str | None:
    return secret("SEC_USER_AGENT")


@st.cache_resource
def overview_cache() -> JsonCache:
    return JsonCache()


@st.cache_data(ttl=CACHE_SECONDS, show_spinner="Reading SEC filings...")
def cached_company(ticker: str) -> CompanyData:
    return load_company(ticker, EdgarClient(user_agent=sec_user_agent()))


def statement_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Statement in USD millions with readable labels (EPS stays in dollars)."""
    table = frame.copy()
    for key in table.index:
        if UNITS[key] != USD_PER_SHARE:
            table.loc[key] = table.loc[key] / 1_000_000
    table.index = [LABELS[k] for k in table.index]
    table.columns = [f"FY {c[:4]}" for c in table.columns]
    return table


def ratio_table(ratios: pd.DataFrame) -> pd.DataFrame:
    table = pd.DataFrame(index=[RATIO_LABELS[k] for k in ratios.index])
    for period in ratios.columns:
        table[f"FY {period[:4]}"] = [
            "" if pd.isna(v) else (f"{v:.1%}" if k in PERCENT_RATIOS else f"{v:.2f}x")
            for k, v in ratios[period].items()
        ]
    return table


def render_header(data: CompanyData) -> None:
    p = data.profile
    st.header(p.name)
    facts = [data.ticker]
    if p.exchanges:
        facts.append(", ".join(p.exchanges))
    if p.sic_description:
        facts.append(p.sic_description)
    if p.fiscal_year_end:
        facts.append(f"fiscal year ends {p.fiscal_year_end[:2]}/{p.fiscal_year_end[2:]}")
    st.caption(" · ".join(facts))
    if data.latest_10k:
        f = data.latest_10k
        st.markdown(f"Latest 10-K: [{f.report_date} (filed {f.filing_date})]({f.url})")


def show_overview(overview: Overview, data: CompanyData) -> None:
    st.write(overview.business_summary)
    money, risks = st.columns(2)
    with money:
        st.markdown("**How it makes money**")
        st.markdown("\n".join(f"- **{r.name}**: {r.description}" for r in overview.revenue_sources))
    with risks:
        st.markdown("**Key risks**")
        st.markdown("\n".join(f"- **{r.title}**: {r.explanation}" for r in overview.key_risks))
    st.markdown("**What management says about the year**")
    st.markdown("\n".join(f"- {point}" for point in overview.management_highlights))
    if data.is_sample:
        st.caption("Sample overview written by hand for the fictional company.")
    else:
        source = f"Written by Claude from the {data.latest_10k.report_date} 10-K"
        st.caption(f"{source}. AI summaries can contain mistakes, so check the filing.")


def render_overview(data: CompanyData) -> None:
    st.subheader("Company overview")
    overview = find_overview(data, overview_cache())
    if overview:
        show_overview(overview, data)
        return
    if data.latest_10k is None:
        st.info("There's no 10-K on EDGAR to summarize for this company.")
        return
    api_key = secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.info(
            "The AI overview needs an Anthropic API key. Set ANTHROPIC_API_KEY to have Claude "
            "read this company's 10-K, or try a ticker with a saved overview."
        )
        return
    if not st.button("Write the overview from the 10-K"):
        st.caption("Claude reads the Business, Risk Factors and MD&A sections of the latest 10-K.")
        return
    try:
        with st.spinner("Claude is reading the 10-K..."):
            overview = write_overview(
                data,
                EdgarClient(user_agent=sec_user_agent()),
                anthropic.Anthropic(api_key=api_key),
                overview_cache(),
            )
    except (OverviewError, EdgarError, ValueError) as error:
        st.error(str(error))
        return
    except (anthropic.APIError, requests.RequestException) as error:
        st.error(f"Couldn't write the overview right now ({type(error).__name__}). Try again.")
        return
    show_overview(overview, data)


def render_headlines(data: CompanyData) -> None:
    latest = data.statements.periods[-1]
    st.subheader(f"Fiscal year ending {latest}")
    for column, h in zip(st.columns(4), headlines(data), strict=True):
        if h.is_percent:
            value = "n/a" if h.value is None else f"{h.value:.1%}"
            delta = None if h.change is None else f"{h.change * 100:+.1f} pts"
        else:
            value = format_usd(h.value)
            delta = None if h.change is None else f"{h.change:+.1%} YoY"
        column.metric(h.label, value, delta)


def render_financials(data: CompanyData) -> None:
    s = data.statements
    tabs = st.tabs(["Income statement", "Balance sheet", "Cash flow", "Ratios"])
    for tab, frame in zip(tabs[:3], (s.income, s.balance, s.cash_flow), strict=True):
        with tab:
            st.caption("USD millions, except EPS in USD and shares in millions")
            st.dataframe(statement_table(frame).style.format("{:,.2f}", na_rep=""), width="stretch")
    with tabs[3]:
        st.dataframe(ratio_table(data.ratios), width="stretch")

    st.download_button(
        "Download the Excel model",
        data=workbook_bytes(s),
        file_name=f"{data.ticker}-three-statement-model.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


def main() -> None:
    st.title("10-K Analyzer")
    st.caption(
        "Company financials straight from SEC filings, in plain language. Not investment advice."
    )

    with st.sidebar:
        with st.form("search"):
            ticker = st.text_input("Ticker", value="AAPL", max_chars=10)
            use_sample = st.toggle("Use sample data (works offline)", value=False)
            submitted = st.form_submit_button("Analyze", type="primary")
        st.caption("Data: SEC EDGAR XBRL company facts, annual 10-K values.")

    if not submitted and "data" not in st.session_state:
        st.info("Enter a US ticker and press Analyze.")
        return

    if submitted:
        try:
            st.session_state.data = load_sample() if use_sample else cached_company(ticker)
        except EdgarError as error:
            st.error(str(error))
            return
        except requests.RequestException:
            st.error(
                "Couldn't reach SEC EDGAR. Try again shortly, or turn on sample data to see "
                "how the app works."
            )
            return
        except ValueError as error:
            st.error(f"{ticker.upper()} has no annual 10-K financial data to show ({error}).")
            return

    data: CompanyData = st.session_state.data
    render_header(data)
    render_overview(data)
    render_headlines(data)
    render_financials(data)


main()
