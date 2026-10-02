from datetime import UTC, datetime
from pathlib import Path

import pytest
import responses

from tenk.cache import JsonCache
from tenk.sources.news import NewsClient, NewsError, parse_rss, search_name

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def feed(name):
    return (FIXTURES / name).read_text()


@pytest.fixture
def client():
    return NewsClient(cache=JsonCache(":memory:"))


@pytest.fixture
def feeds(client):
    urls = client.feed_urls("AAPL", "Apple Inc.", 30)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.get(urls["Google News"], body=feed("news_google.xml"))
        mock.get(urls["Yahoo Finance"], body=feed("news_yahoo.xml"))
        yield mock


@pytest.mark.parametrize(
    ("edgar_name", "expected"),
    [
        ("Apple Inc.", "Apple"),
        ("Microsoft Corp", "Microsoft"),
        ("NIKE, Inc.", "NIKE"),
        ("COCA COLA CO", "COCA COLA"),
        ("Alphabet Inc.", "Alphabet"),
        ("Berkshire Hathaway Inc", "Berkshire Hathaway"),
    ],
)
def test_search_name(edgar_name, expected):
    assert search_name(edgar_name) == expected


def test_google_query_uses_name_ticker_and_window(client):
    url = client.feed_urls("AAPL", "Apple Inc.", 30)["Google News"]
    assert "q=%22Apple%22+OR+AAPL+stock+when%3A30d" in url


def test_parse_google_strips_source_suffix_and_repeated_description():
    articles = parse_rss(feed("news_google.xml"))
    first = articles[0]
    assert first.title == "Apple shares hit record as iPhone sales beat estimates"
    assert first.source == "Example Times"
    assert first.published == "2026-10-01T14:05:00+00:00"
    assert first.summary == ""
    assert len(articles) == 3  # the item without a date is skipped


def test_parse_yahoo_uses_feed_name_as_source():
    articles = parse_rss(feed("news_yahoo.xml"), default_source="Yahoo Finance")
    assert articles[0].source == "Yahoo Finance"
    assert "$46B" in articles[0].summary


def test_articles_are_merged_deduplicated_recent_and_newest_first(client, feeds):
    articles = client.articles("AAPL", "Apple Inc.", now=NOW)
    titles = [a.title for a in articles]
    assert titles == [
        "Apple shares hit record as iPhone sales beat estimates",
        "Why investors are watching Apple's AI plans",
        "Apple faces EU fine over App Store rules",
    ]
    assert articles[0].source == "Example Times"  # the newer copy of the duplicate wins


def test_limit(client, feeds):
    assert len(client.articles("AAPL", "Apple Inc.", now=NOW, limit=2)) == 2


def test_one_failing_feed_is_skipped(client):
    urls = client.feed_urls("AAPL", "Apple Inc.", 30)
    with responses.RequestsMock() as mock:
        mock.get(urls["Google News"], status=503)
        mock.get(urls["Yahoo Finance"], body=feed("news_yahoo.xml"))
        articles = client.articles("AAPL", "Apple Inc.", now=NOW)
    assert len(articles) == 2


def test_all_feeds_failing_raises(client):
    urls = client.feed_urls("AAPL", "Apple Inc.", 30)
    with responses.RequestsMock() as mock:
        mock.get(urls["Google News"], status=503)
        mock.get(urls["Yahoo Finance"], body="not xml")
        with pytest.raises(NewsError, match="Couldn't read any news feed"):
            client.articles("AAPL", "Apple Inc.", now=NOW)


def test_feeds_are_cached(client, feeds):
    client.articles("AAPL", "Apple Inc.", now=NOW)
    client.articles("AAPL", "Apple Inc.", now=NOW)
    assert len(feeds.calls) == 2
