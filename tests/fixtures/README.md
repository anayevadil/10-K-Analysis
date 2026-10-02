Small hand-made examples in the same shape as real SEC EDGAR responses.
Tests run against these (and the sample company in `src/tenk/demo.py`) so CI never calls sec.gov.

- `company_tickers.json`, `submissions_aapl.json`: trimmed EDGAR JSON responses
- `10k_example.htm`: a tiny inline XBRL 10-K with a table of contents, a hidden
  XBRL header and headings split across tags, like real filings
