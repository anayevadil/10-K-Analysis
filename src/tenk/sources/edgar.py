"""Client for SEC EDGAR's free JSON APIs.

Endpoints used:
- https://www.sec.gov/files/company_tickers.json      ticker -> CIK map
- https://data.sec.gov/submissions/CIK##########.json company profile + filing list
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json  every XBRL fact

SEC requires a descriptive User-Agent with a contact email and allows at most
10 requests per second: https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

from tenk.cache import JsonCache

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

ONE_DAY = 24 * 60 * 60
MAX_REQUESTS_PER_SECOND = 10


class EdgarError(RuntimeError):
    """Raised when EDGAR returns an error or a ticker cannot be resolved."""


@dataclass(frozen=True)
class CompanyProfile:
    cik: int
    name: str
    tickers: list[str]
    exchanges: list[str]
    sic: str
    sic_description: str
    fiscal_year_end: str  # "MMDD", e.g. "0928" for Apple


@dataclass(frozen=True)
class Filing:
    form: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str
    url: str


class EdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        cache: JsonCache | None = None,
        session: requests.Session | None = None,
    ) -> None:
        user_agent = user_agent or os.environ.get("SEC_USER_AGENT")
        if not user_agent:
            raise EdgarError(
                "SEC requires a User-Agent with a contact email. "
                'Set SEC_USER_AGENT, e.g. "10-K Analyzer you@example.com".'
            )
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        self.cache = cache or JsonCache()
        self._lock = threading.Lock()
        self._last_request = 0.0

    # --- low level -------------------------------------------------------

    def _throttle(self) -> None:
        with self._lock:
            wait = 1 / MAX_REQUESTS_PER_SECOND - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _get_json(self, url: str, ttl_seconds: float = ONE_DAY) -> Any:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        self._throttle()
        response = self.session.get(url, timeout=30)
        if response.status_code == 404:
            raise EdgarError(f"Not found on EDGAR: {url}")
        response.raise_for_status()
        data = response.json()
        self.cache.set(url, data, ttl_seconds)
        return data

    # --- lookups ---------------------------------------------------------

    def ticker_to_cik(self, ticker: str) -> int:
        """Resolve a ticker such as "AAPL" (or "BRK.B") to its numeric CIK."""
        wanted = ticker.strip().upper().replace(".", "-")
        for entry in self._get_json(TICKERS_URL).values():
            if entry["ticker"].upper() == wanted:
                return int(entry["cik_str"])
        raise EdgarError(f"Unknown ticker: {ticker!r}")

    def submissions(self, cik: int) -> dict[str, Any]:
        return self._get_json(SUBMISSIONS_URL.format(cik=cik))

    def company_facts(self, cik: int) -> dict[str, Any]:
        """Every XBRL fact the company has reported, grouped by taxonomy and tag."""
        return self._get_json(COMPANY_FACTS_URL.format(cik=cik))

    # --- convenience -----------------------------------------------------

    def profile(self, ticker: str) -> CompanyProfile:
        cik = self.ticker_to_cik(ticker)
        data = self.submissions(cik)
        return CompanyProfile(
            cik=cik,
            name=data["name"],
            tickers=data.get("tickers", []),
            exchanges=data.get("exchanges", []),
            sic=data.get("sic", ""),
            sic_description=data.get("sicDescription", ""),
            fiscal_year_end=data.get("fiscalYearEnd", ""),
        )

    def filings(self, ticker: str, form: str = "10-K", limit: int = 5) -> list[Filing]:
        """Most recent filings of one form type, newest first.

        Only the "recent" block of the submissions file is read, which covers
        at least the last year or 1,000 filings; that always includes the
        last several 10-Ks.
        """
        cik = self.ticker_to_cik(ticker)
        recent = self.submissions(cik)["filings"]["recent"]
        found: list[Filing] = []
        for i, filing_form in enumerate(recent["form"]):
            if filing_form != form:
                continue
            accession = recent["accessionNumber"][i]
            document = recent["primaryDocument"][i]
            found.append(
                Filing(
                    form=filing_form,
                    accession_number=accession,
                    filing_date=recent["filingDate"][i],
                    report_date=recent["reportDate"][i],
                    primary_document=document,
                    url=ARCHIVES_URL.format(
                        cik=cik, accession=accession.replace("-", ""), document=document
                    ),
                )
            )
            if len(found) == limit:
                break
        return found

    def latest_10k(self, ticker: str) -> Filing:
        filings = self.filings(ticker, form="10-K", limit=1)
        if not filings:
            raise EdgarError(f"No 10-K found for {ticker!r}")
        return filings[0]
