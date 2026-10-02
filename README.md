# 10-K Analyzer

![CI](https://github.com/anayevadil/10-K-Analysis/actions/workflows/ci.yml/badge.svg)

Type a US ticker and get a plain-English company overview, a downloadable three-statement
Excel model with key margins highlighted, recent news scored for mood, and an AI research
agent that answers questions about the company using the filings themselves.

All data comes from free sources: SEC EDGAR for filings and XBRL financials, RSS feeds for news.

> Work in progress. Not investment advice.

## Status

- [x] Project setup, CI, SEC EDGAR client with caching
- [ ] Three-statement model from XBRL company facts
- [ ] Excel export with ratios and highlighting
- [ ] Streamlit app
- [ ] Company overview with LLM summary
- [ ] News and sentiment (FinBERT + LLM mood tags)
- [ ] "Ask the analyst" agent with step trace and eval set

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SEC_USER_AGENT="10-K Analyzer you@example.com"   # SEC requires a contact email
pytest
```

```python
from tenk.sources.edgar import EdgarClient

edgar = EdgarClient()  # reads SEC_USER_AGENT from the environment
print(edgar.profile("AAPL"))
print(edgar.latest_10k("AAPL").url)
```

## Project layout

```
src/tenk/
  cache.py         SQLite cache for API responses
  sources/edgar.py SEC EDGAR client (ticker lookup, filings, XBRL company facts)
tests/             offline tests against recorded EDGAR responses
```
