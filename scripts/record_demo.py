"""Write AI overviews for a few tickers into src/tenk/demo_cache/.

The deployed app shows these saved overviews when it has no Anthropic API
key, so the demo costs nothing after this runs once.

    export SEC_USER_AGENT="10-K Analyzer you@example.com"
    export ANTHROPIC_API_KEY=...
    python scripts/record_demo.py AAPL MSFT NVDA
"""

from __future__ import annotations

import json
import sys

import anthropic

from tenk.cache import JsonCache
from tenk.service import DEMO_CACHE, demo_record, load_company, write_overview
from tenk.sources.edgar import EdgarClient


def main(tickers: list[str]) -> None:
    if not tickers:
        sys.exit(__doc__)
    edgar = EdgarClient()
    claude = anthropic.Anthropic()
    cache = JsonCache()
    for ticker in tickers:
        data = load_company(ticker, edgar)
        overview = write_overview(data, edgar, claude, cache)
        path = DEMO_CACHE / f"{data.ticker}.json"
        path.write_text(json.dumps(demo_record(data, overview), indent=2) + "\n")
        print(f"{data.ticker}: saved {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
