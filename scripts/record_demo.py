"""Save AI overviews, scored news and agent answers for a few tickers into src/tenk/demo_cache/.

The deployed app shows these saved results when it has no Anthropic API key,
so the demo costs nothing after this runs once.

    pip install -e ".[finbert]"
    export SEC_USER_AGENT="10-K Analyzer you@example.com"
    export ANTHROPIC_API_KEY=...
    python scripts/record_demo.py AAPL MSFT NVDA
"""

from __future__ import annotations

import json
import sys
from datetime import date

import anthropic

from tenk.agent.loop import ask
from tenk.agent.prompts import suggested_questions
from tenk.agent.tools import Toolbox, edgar_resources
from tenk.cache import JsonCache
from tenk.nlp.sentiment import FinBertScorer, finbert_installed
from tenk.service import (
    DEMO_CACHE,
    add_moods,
    demo_record,
    load_company,
    load_news,
    write_overview,
)
from tenk.sources.edgar import EdgarClient
from tenk.sources.news import NewsClient


def main(tickers: list[str]) -> None:
    if not tickers:
        sys.exit(__doc__)
    if not finbert_installed():
        sys.exit('FinBERT is not installed. Run: pip install -e ".[finbert]"')
    edgar = EdgarClient()
    claude = anthropic.Anthropic()
    cache = JsonCache()
    news_client = NewsClient(cache=cache)
    scorer = FinBertScorer()
    for ticker in tickers:
        data = load_company(ticker, edgar)
        overview = write_overview(data, edgar, claude, cache)
        news = add_moods(data, load_news(data, news_client, scorer), claude, cache)

        def resources(other, data=data, news=news):
            if other.upper() == data.ticker:
                return edgar_resources(other, edgar, lambda _: news, data=data)
            return edgar_resources(other, edgar, lambda c: load_news(c, news_client, scorer))

        toolbox = Toolbox(resources)
        company = data.statements.company
        questions = [suggested_questions(company)[i] for i in (0, 1, 2, 4)]
        answers = [
            ask(claude, toolbox, data.ticker, company, question).to_dict() for question in questions
        ]
        record = demo_record(
            data, overview, news, saved_on=date.today().isoformat(), answers=answers
        )
        path = DEMO_CACHE / f"{data.ticker}.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(
            f"{data.ticker}: saved {path} ({len(news.articles)} headlines, {len(answers)} answers)"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
