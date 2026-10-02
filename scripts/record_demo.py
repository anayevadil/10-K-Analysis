"""Save AI overviews and scored news for a few tickers into src/tenk/demo_cache/.

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
        record = demo_record(data, overview, news, saved_on=date.today().isoformat())
        path = DEMO_CACHE / f"{data.ticker}.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(f"{data.ticker}: saved {path} ({len(news.articles)} headlines)")


if __name__ == "__main__":
    main(sys.argv[1:])
