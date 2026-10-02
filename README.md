# 10-K Analyzer

![CI](https://github.com/anayevadil/10-K-Analysis/actions/workflows/ci.yml/badge.svg)

Type a US ticker and get a plain-English company overview, a downloadable three-statement
Excel model with key margins highlighted, recent news scored for mood, and an AI research
agent that answers questions about the company using the filings themselves.

All data comes from free sources: SEC EDGAR for filings and XBRL financials, RSS feeds for news.

> Work in progress. Not investment advice.

![The app in sample-data mode](docs/app-sample-mode.png)

## Status

- [x] Project setup, CI, SEC EDGAR client with caching
- [x] Three-statement model and key ratios from XBRL company facts
- [x] Excel export with live ratio formulas and highlighting
- [x] Streamlit app: profile, headline numbers, statements, ratios, Excel download
- [x] Company overview written by Claude from the 10-K
- [x] News and sentiment (FinBERT scores + Claude mood tags)
- [ ] "Ask the analyst" agent with step trace and eval set

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # use ".[dev,finbert]" for headline sentiment (installs PyTorch)
export SEC_USER_AGENT="10-K Analyzer you@example.com"   # SEC requires a contact email
pytest
```

```python
from tenk.sources.edgar import EdgarClient

edgar = EdgarClient()  # reads SEC_USER_AGENT from the environment
print(edgar.profile("AAPL"))
print(edgar.latest_10k("AAPL").url)

from tenk.financials.statements import build_statements
from tenk.financials.ratios import compute_ratios

statements = build_statements(edgar.company_facts(edgar.ticker_to_cik("AAPL")), years=5)
print(statements.income)  # rows = line items, columns = fiscal year end dates
print(compute_ratios(statements))  # margins, growth, ROE, current ratio, debt to equity

from tenk.export.excel import write_workbook

write_workbook(statements, "AAPL.xlsx")
```

The workbook has a Summary sheet, the three statements in USD millions, a Ratios sheet
built from live Excel formulas (green when a ratio improves on the prior year, red when it
gets worse), a balance sheet check row, and a Sources sheet with the XBRL tag behind every
number.

Companies tag the same line item differently in XBRL, and many changed tags over time.
`src/tenk/financials/xbrl_map.py` lists fallback tags per line item; for each year the
first tag with a value wins, and `statements.sources` records which tag was used.
Missing subtotals such as gross profit are derived, restated values replace the originals,
and only full-year values from 10-K filings are used.

## Run the app

```bash
export SEC_USER_AGENT="10-K Analyzer you@example.com"
streamlit run app/streamlit_app.py
```

Turn on "Use sample data" in the sidebar to try the app without network access; it loads
a fictional company (`src/tenk/demo.py`) through the same code path as real filings.

### AI company overview

With `ANTHROPIC_API_KEY` set, a "Write the overview from the 10-K" button appears. The app
downloads the latest 10-K, pulls out Item 1 (Business), Item 1A (Risk Factors) and Item 7
(MD&A), and asks Claude for a plain-English overview: what the company does, how it makes
money, the five risks that matter most, and management's view of the year.

- Structured outputs make Claude's reply match a JSON schema, so nothing is parsed from free text.
- Server-side fallback (`fallbacks="default"`) retries a declined request on another
  model inside the same API call.
- Each overview is cached per filing, so a company costs one request per 10-K.
- `python scripts/record_demo.py AAPL MSFT` saves overviews into `src/tenk/demo_cache/`;
  the app shows saved overviews without an API key, so a public demo costs nothing to run.

### News and mood

![The News and mood tab in sample-data mode](docs/app-news.png)

The News tab collects the last 30 days of headlines from Google News and Yahoo Finance RSS
(no keys), merges them and drops syndicated duplicates.

- [FinBERT](https://huggingface.co/ProsusAI/finbert), a BERT model tuned on financial news,
  scores every headline as positive, neutral or negative on your own machine, for free.
  The average gives the overall tone. Install it with `pip install -e ".[finbert]"`.
- FinBERT can't tell confidence from optimism or uncertainty from fear, so with an API key a
  "Tag the mood with Claude" button labels each headline with one of five moods and writes a
  two-sentence summary of what the news is about (`claude-haiku-4-5`, one request).
- Without FinBERT the headlines are still listed, just unscored; without a key, demo tickers
  show the headlines and tags saved by `scripts/record_demo.py`.

### Deploy on Streamlit Community Cloud

1. At [share.streamlit.io](https://share.streamlit.io), choose "Create app" and pick this
   repository, branch `main`, main file `app/streamlit_app.py`.
2. Under Advanced settings, add the secret `SEC_USER_AGENT = "10-K Analyzer you@example.com"`.
   Add `ANTHROPIC_API_KEY` too only if visitors should be able to run Claude themselves.
3. Deploy. `requirements.txt` installs this package with FinBERT, using the CPU-only
   PyTorch build to keep the install small.

## Project layout

```
src/tenk/
  cache.py         SQLite cache for API responses
  sources/edgar.py SEC EDGAR client (ticker lookup, filings, XBRL company facts)
  sources/filing_text.py  10-K HTML to text; Business, Risk Factors, MD&A sections
  sources/news.py  Google News and Yahoo Finance RSS headlines
  nlp/summarize.py Claude overview: prompt, JSON schema, fallback handling
  nlp/sentiment.py FinBERT headline scores and Claude mood tags
  demo_cache/      saved overviews and news shown without an API key
  financials/      XBRL tag map, three statements, ratios
  export/excel.py  three-statement Excel workbook
  service.py       loads everything the app shows for one company
  demo.py          fictional sample company (offline mode and tests)
app/
  streamlit_app.py web interface
scripts/
  record_demo.py   saves AI overviews and scored news for the demo
tests/             offline tests against recorded EDGAR responses
```
