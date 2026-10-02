"""Recent headlines about a company from free RSS feeds.

- Google News search RSS: broad coverage, searched by company name and ticker
- Yahoo Finance headline RSS: finance-focused, looked up by ticker

Both are keyless. Articles are merged, limited to the last ``days`` days and
deduplicated by title, since the same story is often syndicated many times.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from tenk.cache import JsonCache

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
YAHOO_NEWS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
ONE_HOUR = 60 * 60
USER_AGENT = "Mozilla/5.0 (compatible; 10-K Analyzer)"

# Legal suffixes dropped from EDGAR names to build a search query: "Apple Inc." -> "Apple".
_SUFFIXES = re.compile(
    r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|"
    r"group|sa|nv|ag|lp|llc)\.?$",
    re.IGNORECASE,
)


class NewsError(RuntimeError):
    """Raised when no news feed could be read."""


@dataclass(frozen=True)
class Article:
    title: str
    link: str
    source: str
    published: str  # ISO 8601, UTC
    summary: str = ""


def search_name(company: str) -> str:
    """A company name as people write it in headlines."""
    name = company.strip()
    while True:
        shorter = _SUFFIXES.sub("", name).strip(" ,.")
        if shorter == name or not shorter:
            break
        name = shorter
    return name


def _text(html: str) -> str:
    if "<" not in html:
        return html.strip()
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def parse_rss(xml: str, default_source: str = "") -> list[Article]:
    """Articles from an RSS 2.0 document; items without a title, link or date are skipped."""
    root = ET.fromstring(xml)
    articles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        date = item.findtext("pubDate")
        if not (title and link and date):
            continue
        try:
            published = parsedate_to_datetime(date)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        source = (item.findtext("source") or default_source).strip()
        # Google News appends " - Source" to every title.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        summary = _text(item.findtext("description") or "")
        if _title_key(summary).startswith(_title_key(title)):
            summary = ""  # Google's description only repeats the title
        articles.append(
            Article(
                title=title,
                link=link,
                source=source,
                published=published.astimezone(UTC).isoformat(timespec="seconds"),
                summary=summary,
            )
        )
    return articles


class NewsClient:
    def __init__(
        self, cache: JsonCache | None = None, session: requests.Session | None = None
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.cache = cache or JsonCache()

    def _get(self, url: str) -> str:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        response = self.session.get(url, timeout=20)
        response.raise_for_status()
        self.cache.set(url, response.text, ONE_HOUR)
        return response.text

    def feed_urls(self, ticker: str, company: str, days: int) -> dict[str, str]:
        query = f'"{search_name(company)}" OR {ticker} stock when:{days}d'
        return {
            "Google News": GOOGLE_NEWS_URL.format(query=quote_plus(query)),
            "Yahoo Finance": YAHOO_NEWS_URL.format(ticker=quote_plus(ticker)),
        }

    def articles(
        self,
        ticker: str,
        company: str,
        days: int = 30,
        limit: int = 40,
        now: datetime | None = None,
    ) -> list[Article]:
        """Newest first, at most ``limit``. One failing feed is skipped; all failing raises."""
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        found: list[Article] = []
        errors = []
        for name, url in self.feed_urls(ticker, company, days).items():
            try:
                found += parse_rss(self._get(url), default_source=name)
            except (requests.RequestException, ET.ParseError) as error:
                errors.append(f"{name}: {error}")
        if errors and not found:
            raise NewsError("Couldn't read any news feed. " + "; ".join(errors))

        found.sort(key=lambda a: a.published, reverse=True)
        unique: dict[str, Article] = {}
        for article in found:
            if datetime.fromisoformat(article.published) < cutoff:
                continue
            unique.setdefault(_title_key(article.title), article)
        return list(unique.values())[:limit]
