"""Score news headlines: FinBERT for tone, Claude for the mood behind it.

FinBERT (ProsusAI/finbert) is a BERT model fine-tuned on financial news. It
runs locally for free and gives each headline positive / negative / neutral
probabilities. It can't tell confidence from optimism or uncertainty from
fear, so Claude adds one mood tag per headline and a two-sentence summary of
what the news is about. The Claude step is optional; without an API key the
page shows FinBERT scores only.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from tenk.sources.news import Article

FINBERT_MODEL = "ProsusAI/finbert"
MOOD_MODEL = "claude-haiku-4-5"  # a small, fast model is enough for tagging headlines
MOODS = ("confidence", "optimism", "neutral", "uncertainty", "fear")
POSITIVE_ABOVE = 0.15  # average FinBERT score (positive minus negative) for a positive tone
NEGATIVE_BELOW = -0.15


@dataclass(frozen=True)
class Sentiment:
    positive: float
    negative: float
    neutral: float

    @property
    def score(self) -> float:
        """From -1 (certainly negative) to +1 (certainly positive)."""
        return self.positive - self.negative

    @property
    def label(self) -> str:
        probabilities = {
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
        }
        return max(probabilities, key=probabilities.__getitem__)


@dataclass(frozen=True)
class ScoredArticle:
    article: Article
    sentiment: Sentiment | None = None
    mood: str | None = None


@dataclass
class NewsMood:
    articles: list[ScoredArticle]
    summary: str = ""  # Claude's "what the news is about", empty without Claude
    tagged_by: str = ""  # the model that added mood tags
    saved_on: str = ""  # set when replayed from demo_cache

    @property
    def scored(self) -> bool:
        return any(a.sentiment for a in self.articles)

    @property
    def tagged(self) -> bool:
        return any(a.mood for a in self.articles)

    @property
    def average_score(self) -> float | None:
        scores = [a.sentiment.score for a in self.articles if a.sentiment]
        return sum(scores) / len(scores) if scores else None

    @property
    def tone(self) -> str | None:
        average = self.average_score
        if average is None:
            return None
        if average > POSITIVE_ABOVE:
            return "Positive"
        if average < NEGATIVE_BELOW:
            return "Negative"
        return "Neutral"

    def sentiment_counts(self) -> dict[str, int]:
        counts = Counter(a.sentiment.label for a in self.articles if a.sentiment)
        return {label: counts[label] for label in ("positive", "neutral", "negative")}

    def mood_counts(self) -> dict[str, int]:
        counts = Counter(a.mood for a in self.articles if a.mood)
        return {mood: counts[mood] for mood in MOODS}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewsMood:
        articles = [
            ScoredArticle(
                article=Article(**a["article"]),
                sentiment=Sentiment(**a["sentiment"]) if a.get("sentiment") else None,
                mood=a.get("mood"),
            )
            for a in data["articles"]
        ]
        return cls(
            articles=articles,
            summary=data.get("summary", ""),
            tagged_by=data.get("tagged_by", ""),
            saved_on=data.get("saved_on", ""),
        )


def headline_text(article: Article) -> str:
    return f"{article.title}. {article.summary}" if article.summary else article.title


# --- FinBERT ---------------------------------------------------------------


def finbert_installed() -> bool:
    return all(importlib.util.find_spec(name) for name in ("transformers", "torch"))


class FinBertScorer:
    """Loads FinBERT on first use (about 440 MB, downloaded once from Hugging Face)."""

    def __init__(self, model: str = FINBERT_MODEL) -> None:
        self.model = model
        self._pipeline: Any = None

    def _classifier(self) -> Any:
        if self._pipeline is None:
            from transformers import pipeline

            self._pipeline = pipeline("text-classification", model=self.model, top_k=None)
        return self._pipeline

    def score(self, texts: list[str]) -> list[Sentiment]:
        if not texts:
            return []
        results = self._classifier()(texts, truncation=True, batch_size=16)
        return [to_sentiment(labels) for labels in results]


def to_sentiment(labels: list[dict[str, Any]]) -> Sentiment:
    """FinBERT's per-label output, e.g. [{"label": "positive", "score": 0.9}, ...]."""
    probabilities = {item["label"].lower(): float(item["score"]) for item in labels}
    return Sentiment(
        positive=probabilities.get("positive", 0.0),
        negative=probabilities.get("negative", 0.0),
        neutral=probabilities.get("neutral", 0.0),
    )


def score_articles(articles: list[Article], scorer: Any | None) -> NewsMood:
    """FinBERT scores for each article; unscored when ``scorer`` is None."""
    if scorer is None:
        return NewsMood([ScoredArticle(a) for a in articles])
    sentiments = scorer.score([headline_text(a) for a in articles])
    return NewsMood([ScoredArticle(a, s) for a, s in zip(articles, sentiments, strict=True)])


# --- Claude mood tags ------------------------------------------------------

MOOD_SYSTEM_PROMPT = """\
You label the mood of news headlines about one company for readers who are new \
to investing. For each numbered headline pick exactly one mood:

- confidence: the company or market shows strength or certainty (record results, \
strong demand, upgrades)
- optimism: hope about something that hasn't happened yet (new product, expected growth)
- neutral: factual news with no clear emotion
- uncertainty: open questions, mixed signals, pending decisions or investigations
- fear: worry about losses, lawsuits, layoffs, falling demand or sell-offs

Judge the headline's mood about this company, not about the wider market. Then \
write a summary of two short sentences on what the news is mostly about, \
neutral in tone, with no advice to buy or sell."""

MOOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "moods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "mood": {"type": "string", "enum": list(MOODS)},
                },
                "required": ["id", "mood"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["moods", "summary"],
    "additionalProperties": False,
}


class MoodError(RuntimeError):
    """Raised when Claude can't tag the headlines."""


def mood_prompt(company: str, articles: list[Article]) -> str:
    lines = [f"Company: {company}", "", "Headlines:"]
    for i, article in enumerate(articles, start=1):
        lines.append(f"{i}. [{article.published[:10]}, {article.source}] {headline_text(article)}")
    return "\n".join(lines)


def tag_moods(client: Any, company: str, news: NewsMood, model: str = MOOD_MODEL) -> NewsMood:
    """``news`` with a mood per article and a summary. ``client`` is anthropic.Anthropic."""
    if not news.articles:
        return news
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=MOOD_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": mood_prompt(company, [a.article for a in news.articles]),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": MOOD_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise MoodError("Claude declined to tag these headlines.")
    if response.stop_reason == "max_tokens":
        raise MoodError("Claude's answer was cut off. Try again.")
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text:
        raise MoodError("Claude returned no mood tags.")
    data = json.loads(text)
    moods = {item["id"]: item["mood"] for item in data["moods"]}
    articles = [
        ScoredArticle(a.article, a.sentiment, moods.get(i)) for i, a in enumerate(news.articles, 1)
    ]
    return NewsMood(articles, summary=data["summary"], tagged_by=response.model)
