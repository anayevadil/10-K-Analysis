import json

import pytest
from conftest import FakeClaude, text

from tenk.nlp.sentiment import (
    MOOD_MODEL,
    MOOD_SCHEMA,
    MOODS,
    FinBertScorer,
    MoodError,
    NewsMood,
    Sentiment,
    score_articles,
    tag_moods,
    to_sentiment,
)
from tenk.sources.news import Article

ARTICLES = [
    Article(
        "Example Corp beats forecasts", "https://example.com/1", "Wire", "2026-10-01T10:00:00+00:00"
    ),
    Article(
        "Chip shortage hits Example Corp",
        "https://example.com/2",
        "Wire",
        "2026-09-30T10:00:00+00:00",
        "Deliveries may slip.",
    ),
    Article(
        "Example Corp to present at conference",
        "https://example.com/3",
        "Wire",
        "2026-09-29T10:00:00+00:00",
    ),
]


class FakePipeline:
    """Mimics transformers' text-classification pipeline called with top_k=None."""

    OUTPUT = {
        "beats": [("positive", 0.9), ("neutral", 0.08), ("negative", 0.02)],
        "shortage": [("negative", 0.8), ("neutral", 0.15), ("positive", 0.05)],
        "present": [("neutral", 0.9), ("positive", 0.06), ("negative", 0.04)],
    }

    def __init__(self):
        self.calls = []

    def __call__(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [
            [{"label": label, "score": score} for label, score in self._labels(t)] for t in texts
        ]

    def _labels(self, text):
        return next(v for k, v in self.OUTPUT.items() if k in text)


@pytest.fixture
def scorer():
    scorer = FinBertScorer()
    scorer._pipeline = FakePipeline()
    return scorer


def test_to_sentiment_reads_all_labels():
    s = to_sentiment([{"label": "Positive", "score": 0.7}, {"label": "negative", "score": 0.1}])
    assert s == Sentiment(positive=0.7, negative=0.1, neutral=0.0)
    assert s.score == pytest.approx(0.6)
    assert s.label == "positive"


def test_finbert_scores_title_and_summary(scorer):
    news = score_articles(ARTICLES, scorer)
    labels = [a.sentiment.label for a in news.articles]
    assert labels == ["positive", "negative", "neutral"]
    texts, kwargs = scorer._pipeline.calls[0]
    assert texts[1] == "Chip shortage hits Example Corp. Deliveries may slip."
    assert kwargs["truncation"] is True


def test_overall_tone_and_counts(scorer):
    news = score_articles(ARTICLES, scorer)
    assert news.average_score == pytest.approx((0.88 - 0.75 + 0.02) / 3)
    assert news.tone == "Neutral"
    assert news.sentiment_counts() == {"positive": 1, "neutral": 1, "negative": 1}
    assert not news.tagged


def test_without_scorer_articles_are_unscored():
    news = score_articles(ARTICLES, None)
    assert not news.scored
    assert news.tone is None
    assert news.average_score is None


def test_tag_moods(scorer):
    reply = {
        "moods": [
            {"id": 1, "mood": "confidence"},
            {"id": 2, "mood": "fear"},
            {"id": 3, "mood": "neutral"},
        ],
        "summary": "Results were strong. A chip shortage is the main worry.",
    }
    claude = FakeClaude([text(json.dumps(reply))], model=MOOD_MODEL)
    news = tag_moods(claude, "Example Corp", score_articles(ARTICLES, scorer))
    assert [a.mood for a in news.articles] == ["confidence", "fear", "neutral"]
    assert news.articles[0].sentiment.label == "positive"  # FinBERT scores are kept
    assert news.summary.startswith("Results were strong")
    assert news.tagged_by == MOOD_MODEL
    assert news.mood_counts() == {
        "confidence": 1,
        "optimism": 0,
        "neutral": 1,
        "uncertainty": 0,
        "fear": 1,
    }

    request = claude.request
    assert request["model"] == "claude-haiku-4-5"
    assert request["output_config"] == {"format": {"type": "json_schema", "schema": MOOD_SCHEMA}}
    prompt = request["messages"][0]["content"]
    assert "2. [2026-09-30, Wire] Chip shortage hits Example Corp. Deliveries may slip." in prompt
    assert "buy or sell" in request["system"]


def test_mood_schema_only_allows_known_moods():
    item = MOOD_SCHEMA["properties"]["moods"]["items"]
    assert item["properties"]["mood"]["enum"] == list(MOODS)


def test_missing_mood_for_an_article_is_left_empty():
    reply = {"moods": [{"id": 2, "mood": "fear"}], "summary": "Worries."}
    news = tag_moods(
        FakeClaude([text(json.dumps(reply))]), "Example Corp", score_articles(ARTICLES, None)
    )
    assert [a.mood for a in news.articles] == [None, "fear", None]


def test_refusal_raises():
    with pytest.raises(MoodError, match="declined"):
        tag_moods(
            FakeClaude([], stop_reason="refusal"), "Example Corp", score_articles(ARTICLES, None)
        )


def test_no_articles_skips_claude():
    claude = FakeClaude([])
    assert tag_moods(claude, "Example Corp", NewsMood([])).articles == []
    assert claude.request is None


def test_round_trips_through_json(scorer):
    news = score_articles(ARTICLES, scorer)
    assert NewsMood.from_dict(json.loads(json.dumps(news.to_dict()))) == news
