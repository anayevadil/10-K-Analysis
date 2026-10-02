"""Plain-English company overview written by Claude from the 10-K itself.

One request per filing: the Business, Risk Factors and MD&A sections go in,
a structured overview comes out. Structured outputs guarantee the reply
matches ``OVERVIEW_SCHEMA``, so there is no free-text parsing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from tenk.sources.filing_text import SECTION_LABELS

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
# If the request is declined by a safety classifier, the API retries it on
# Anthropic's recommended fallback model instead of returning the refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Bump when the prompt or schema changes, so cached overviews are rewritten.
PROMPT_VERSION = 1
# About 25k tokens per section keeps even a large bank's MD&A affordable.
MAX_SECTION_CHARS = 100_000

SYSTEM_PROMPT = """\
You explain US public companies to people who are new to investing. You are \
given sections of a company's latest annual report (Form 10-K). Write an \
overview that someone without a finance background can read in two minutes.

- Use only what the filing says. Do not add outside facts, prices or forecasts.
- Prefer plain words over jargon; when a finance term is needed, explain it \
in a few words.
- Mention concrete numbers from the filing where they help (revenue growth, \
segment size), with the period they refer to.
- Stay neutral. Do not say whether to buy, sell or hold the stock.

Fields:
- business_summary: two or three sentences on what the company does.
- revenue_sources: the main segments, products or services that bring in \
money, most important first (two to six entries).
- key_risks: the five risks that matter most for this company specifically, \
not boilerplate that applies to every company, each with a one or two \
sentence explanation.
- management_highlights: three to five points from MD&A on how the year went \
and why, in management's own framing.
- missing_sections: names of any requested sections that were not provided."""

_STRING = {"type": "string"}
OVERVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_summary": _STRING,
        "revenue_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": _STRING, "description": _STRING},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "key_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": _STRING, "explanation": _STRING},
                "required": ["title", "explanation"],
                "additionalProperties": False,
            },
        },
        "management_highlights": {"type": "array", "items": _STRING},
        "missing_sections": {"type": "array", "items": _STRING},
    },
    "required": [
        "business_summary",
        "revenue_sources",
        "key_risks",
        "management_highlights",
        "missing_sections",
    ],
    "additionalProperties": False,
}


class OverviewError(RuntimeError):
    """Raised when Claude can't produce an overview for a filing."""


@dataclass(frozen=True)
class RevenueSource:
    name: str
    description: str


@dataclass(frozen=True)
class Risk:
    title: str
    explanation: str


@dataclass(frozen=True)
class Overview:
    business_summary: str
    revenue_sources: list[RevenueSource]
    key_risks: list[Risk]
    management_highlights: list[str]
    missing_sections: list[str] = field(default_factory=list)
    model: str = ""  # the model that wrote it (can be a fallback model)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Overview:
        return cls(
            business_summary=data["business_summary"],
            revenue_sources=[RevenueSource(**r) for r in data["revenue_sources"]],
            key_risks=[Risk(**r) for r in data["key_risks"]],
            management_highlights=list(data["management_highlights"]),
            missing_sections=list(data.get("missing_sections", [])),
            model=data.get("model", ""),
        )


def build_prompt(company: str, fiscal_year: str, sections: dict[str, str]) -> str:
    parts = [f"Company: {company}\nFiscal year ended: {fiscal_year}\n"]
    for key, label in SECTION_LABELS.items():
        text = sections.get(key)
        if not text:
            parts.append(f'<section name="{label}">\n(not found in the filing)\n</section>')
            continue
        if len(text) > MAX_SECTION_CHARS:
            text = text[:MAX_SECTION_CHARS] + "\n[... section truncated ...]"
        parts.append(f'<section name="{label}">\n{text}\n</section>')
    parts.append("Write the overview of this company from the sections above.")
    return "\n\n".join(parts)


def summarize_filing(
    client: Any,
    company: str,
    fiscal_year: str,
    sections: dict[str, str],
    model: str = MODEL,
) -> Overview:
    """Ask Claude for the overview. ``client`` is an ``anthropic.Anthropic``."""
    if not sections:
        raise OverviewError("Couldn't find the Business, Risk Factors or MD&A sections.")
    response = client.beta.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": OVERVIEW_SCHEMA},
        },
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(company, fiscal_year, sections)}],
    )
    if response.stop_reason == "refusal":
        raise OverviewError("Claude declined to summarize this filing.")
    if response.stop_reason == "max_tokens":
        raise OverviewError("The overview was cut off before it finished. Try again.")
    # Text written before a fallback block came from a model that then declined
    # mid-answer, so only the text after the last fallback block counts.
    text = ""
    for block in response.content:
        if block.type == "fallback":
            text = ""
        elif block.type == "text":
            text += block.text
    if not text:
        raise OverviewError("Claude returned no overview.")
    data = json.loads(text)
    data["model"] = response.model  # the model that served the answer
    return Overview.from_dict(data)
