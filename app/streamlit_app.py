"""Streamlit front end. Run with: streamlit run app/streamlit_app.py"""

from __future__ import annotations

import json
import os
import re

import anthropic
import pandas as pd
import requests
import streamlit as st

from tenk.agent.loop import AgentAnswer, Step, ask, saved_answers
from tenk.agent.prompts import suggested_questions
from tenk.agent.tools import Toolbox, edgar_resources, sample_resources
from tenk.cache import JsonCache
from tenk.export.excel import workbook_bytes
from tenk.financials.ratios import PERCENT_RATIOS, RATIO_LABELS
from tenk.financials.xbrl_map import ALL_ITEMS, USD_PER_SHARE
from tenk.nlp.sentiment import (
    MOODS,
    FinBertScorer,
    MoodError,
    NewsMood,
    ScoredArticle,
    finbert_installed,
)
from tenk.nlp.summarize import Overview, OverviewError
from tenk.service import (
    DEMO_TICKER,
    CompanyData,
    add_moods,
    find_moods,
    find_overview,
    format_usd,
    headlines,
    load_company,
    load_news,
    load_sample,
    saved_news,
    write_overview,
)
from tenk.sources.edgar import EdgarClient, EdgarError
from tenk.sources.news import NewsClient, NewsError

LABELS = {item.key: item.label for item in ALL_ITEMS}
UNITS = {item.key: item.unit for item in ALL_ITEMS}
CACHE_SECONDS = 6 * 60 * 60
NEWS_CACHE_SECONDS = 60 * 60
TONE_ICONS = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}

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


def md(text: str) -> str:
    """Escape Markdown, so "$1.3B to $1.4B" isn't rendered as a math formula."""
    return re.sub(r"([\\`*_\[\]$<>#|~])", r"\\\1", text)


@st.cache_resource
def result_cache() -> JsonCache:
    """Claude results, kept across sessions so each costs one request."""
    return JsonCache()


@st.cache_resource(show_spinner="Loading FinBERT...")
def finbert() -> FinBertScorer | None:
    return FinBertScorer() if finbert_installed() else None


@st.cache_data(ttl=CACHE_SECONDS, show_spinner="Reading SEC filings...")
def cached_company(ticker: str) -> CompanyData:
    return load_company(ticker, EdgarClient(user_agent=sec_user_agent()))


@st.cache_data(ttl=NEWS_CACHE_SECONDS, show_spinner="Reading the news...")
def cached_news(_data: CompanyData, ticker: str) -> NewsMood:
    return load_news(_data, NewsClient(), finbert())


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
    st.markdown(md(overview.business_summary))
    money, risks = st.columns(2)
    with money:
        st.markdown("**How it makes money**")
        st.markdown(
            "\n".join(f"- **{md(r.name)}**: {md(r.description)}" for r in overview.revenue_sources)
        )
    with risks:
        st.markdown("**Key risks**")
        st.markdown(
            "\n".join(f"- **{md(r.title)}**: {md(r.explanation)}" for r in overview.key_risks)
        )
    st.markdown("**What management says about the year**")
    st.markdown("\n".join(f"- {md(point)}" for point in overview.management_highlights))
    if data.is_sample:
        st.caption("Sample overview written by hand for the fictional company.")
    else:
        source = f"Written by Claude from the {data.latest_10k.report_date} 10-K"
        st.caption(f"{source}. AI summaries can contain mistakes, so check the filing.")


def render_overview(data: CompanyData) -> None:
    st.subheader("Company overview")
    overview = find_overview(data, result_cache())
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
                result_cache(),
            )
    except (OverviewError, EdgarError, ValueError) as error:
        st.error(str(error))
        return
    except (anthropic.APIError, requests.RequestException) as error:
        st.error(f"Couldn't write the overview right now ({type(error).__name__}). Try again.")
        return
    show_overview(overview, data)


def article_line(item: ScoredArticle) -> str:
    a = item.article
    parts = [a.published[:10], f"[{md(a.title)}]({a.link})", md(a.source)]
    if item.sentiment:
        s = item.sentiment
        parts.append(f"{TONE_ICONS[s.label]} {s.label} ({s.score:+.2f})")
    if item.mood:
        parts.append(f"*{item.mood}*")
    return "- " + " · ".join(parts)


def show_news(news: NewsMood) -> None:
    if news.saved_on == "sample":
        st.caption("Sample headlines written by hand for the fictional company.")
    elif news.saved_on:
        st.caption(f"Headlines saved on {news.saved_on} for the demo.")
    if not news.articles:
        st.info("No headlines about this company in the last 30 days.")
        return

    tone, average, count = st.columns(3)
    tone.metric("Overall tone", news.tone or "n/a", help="FinBERT, averaged over all headlines")
    average.metric(
        "Average score",
        "n/a" if news.average_score is None else f"{news.average_score:+.2f}",
        help="-1 is fully negative, +1 fully positive",
    )
    count.metric("Headlines", len(news.articles))

    if news.summary:
        st.markdown(f"**What the news is about:** {md(news.summary)}")
    left, right = st.columns(2)
    if news.scored:
        with left:
            st.markdown("**Tone (FinBERT)**")
            st.bar_chart(
                pd.Series(news.sentiment_counts(), name="headlines"), height=220, sort=False
            )
    else:
        left.info('Install FinBERT with `pip install -e ".[finbert]"` to score each headline.')
    if news.tagged:
        with right:
            st.markdown("**Mood (Claude)**")
            counts = pd.Series(news.mood_counts(), index=list(MOODS), name="headlines")
            st.bar_chart(counts, height=220, sort=False)

    st.markdown("\n".join(article_line(item) for item in news.articles))
    if news.tagged and news.tagged_by:
        st.caption(f"Mood tags and summary by Claude ({news.tagged_by}). AI labels can be wrong.")


def render_news(data: CompanyData) -> None:
    st.subheader("News and mood")
    st.caption("Headlines from the last 30 days via Google News and Yahoo Finance.")
    api_key = secret("ANTHROPIC_API_KEY")
    saved = saved_news(data.ticker)
    if data.is_sample or (saved and not api_key):
        show_news(saved or NewsMood([]))
        return
    try:
        news = find_moods(cached_news(data, data.ticker), result_cache())
    except (NewsError, requests.RequestException):
        st.warning("Couldn't read the news feeds right now. Try again shortly.")
        return

    if api_key and news.articles and not news.tagged:
        if st.button("Tag the mood with Claude"):
            try:
                with st.spinner("Claude is reading the headlines..."):
                    news = add_moods(
                        data, news, anthropic.Anthropic(api_key=api_key), result_cache()
                    )
            except MoodError as error:
                st.error(str(error))
            except anthropic.APIError as error:
                st.error(f"Couldn't tag the headlines right now ({type(error).__name__}).")
        else:
            st.caption(
                "Claude labels each headline as confidence, optimism, neutral, "
                "uncertainty or fear, and sums up what the news is about."
            )
    show_news(news)


def describe_step(step: Step) -> str:
    args = ", ".join(f"{k}={json.dumps(v)}" for k, v in step.input.items())
    return f"`{step.tool}({args})`"


def show_answer(answer: AgentAnswer) -> None:
    st.markdown(answer.answer.replace("$", "\\$"))
    calls = len(answer.steps)
    label = f"How the agent got there: {calls} tool call{'s' if calls != 1 else ''}"
    with st.expander(label):
        if answer.model:
            st.caption(
                f"{answer.model} · {answer.model_calls} model calls · "
                f"{answer.input_tokens:,} input and {answer.output_tokens:,} output tokens"
            )
        for number, step in enumerate(answer.steps, start=1):
            status = " · failed" if step.is_error else ""
            if step.seconds and not step.is_error:
                status = f" · {step.seconds:.2f}s"
            st.markdown(f"**Step {number}.** {describe_step(step)}{status}")
            st.code(step.output[:2000], language="json" if not step.is_error else None)


def agent_toolbox(data: CompanyData) -> Toolbox:
    def news(company: CompanyData) -> NewsMood | None:
        return find_moods(cached_news(company, company.ticker), result_cache())

    def load(ticker: str):
        if ticker.upper() == DEMO_TICKER:
            return sample_resources()
        edgar = EdgarClient(user_agent=sec_user_agent())
        known = data if ticker.upper() == data.ticker else None
        return edgar_resources(ticker, edgar, news, data=known)

    return Toolbox(load)


def render_agent(data: CompanyData) -> None:
    st.subheader("Ask the analyst")
    st.caption(
        "An AI agent that answers questions with this app's own tools: the XBRL financials, "
        "a search over the 10-K and the news scores. Every answer shows each step it took. "
        "Not investment advice."
    )
    api_key = secret("ANTHROPIC_API_KEY")
    if not api_key:
        saved = saved_answers(data.ticker)
        if not saved:
            st.info(
                "The agent needs an Anthropic API key (ANTHROPIC_API_KEY). Turn on sample data "
                "to see saved answers."
            )
            return
        st.info("Saved answers. Set ANTHROPIC_API_KEY to ask your own questions.")
        if data.is_sample:
            st.caption("Sample answers are written by hand; the tool steps are real outputs.")
        for answer in saved:
            with st.container(border=True):
                st.markdown(f"**{md(answer.question)}**")
                show_answer(answer)
        return

    company = data.statements.company
    with st.form("ask"):
        question = st.text_input("Your question", placeholder=f"Why did {company}'s margin change?")
        asked = st.form_submit_button("Ask", type="primary")
    columns = st.columns(len(suggested_questions(company)))
    for column, suggestion in zip(columns, suggested_questions(company), strict=True):
        if column.button(suggestion, width="stretch"):
            question, asked = suggestion, True

    answers: list[AgentAnswer] = st.session_state.setdefault("answers", {}).setdefault(
        data.ticker, []
    )
    if asked and question.strip():
        with st.status("Working on it...", expanded=True) as status:
            try:
                answer = ask(
                    anthropic.Anthropic(api_key=api_key),
                    agent_toolbox(data),
                    data.ticker,
                    company,
                    question.strip(),
                    on_step=lambda step: status.markdown(f"Called {describe_step(step)}"),
                )
            except anthropic.APIError as error:
                status.update(label="Something went wrong", state="error")
                st.error(f"Couldn't reach Claude right now ({type(error).__name__}).")
                answer = None
            else:
                calls = len(answer.steps)
                status.update(label=f"Done after {calls} tool calls", state="complete")
        if answer:
            answers.insert(0, answer)

    for answer in answers:
        with st.container(border=True):
            st.markdown(f"**{md(answer.question)}**")
            show_answer(answer)


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
    overview_tab, model_tab, news_tab, agent_tab = st.tabs(
        ["Overview", "Financial model", "News and mood", "Ask the analyst"]
    )
    with overview_tab:
        render_headlines(data)
        render_overview(data)
    with model_tab:
        render_financials(data)
    with news_tab:
        render_news(data)
    with agent_tab:
        render_agent(data)


main()
