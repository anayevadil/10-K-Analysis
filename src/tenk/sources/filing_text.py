"""Turn a 10-K's HTML into plain text and pull out the sections people read.

A 10-K is organised into numbered Items. The ones that explain the company:

- Item 1   Business: what the company does and how it makes money
- Item 1A  Risk Factors: what management says could go wrong
- Item 7   Management's Discussion and Analysis (MD&A): management's own
           explanation of the year's results

Every Item heading appears at least twice: once in the table of contents and
once where the section really starts (sometimes a third time in a cross
reference). For each Item we keep the occurrence followed by the most text
before the next Item heading, which is the real section, not the contents line.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

SECTIONS = {
    "business": "1",
    "risk_factors": "1a",
    "mdna": "7",
}
SECTION_LABELS = {
    "business": "Item 1. Business",
    "risk_factors": "Item 1A. Risk Factors",
    "mdna": "Item 7. Management's Discussion and Analysis",
}

BLOCK_TAGS = (
    "p",
    "div",
    "br",
    "tr",
    "li",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
)

# "Item 1A." / "ITEM 7 -" / "Item 1. Business" at the start of a line.
ITEM_HEADING = re.compile(r"^[ \t]*item[ \t]+(\d{1,2}[a-c]?)(?![0-9a-z])", re.IGNORECASE | re.M)


def html_to_text(html: str) -> str:
    """Readable text with one line per paragraph, table row or heading."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.IGNORECASE)):
        tag.decompose()
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    for tag in soup.find_all(["td", "th"]):
        tag.append(" ")
    text = soup.get_text().replace("\xa0", " ").replace("​", "")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def extract_sections(text: str) -> dict[str, str]:
    """Business, risk factors and MD&A text, keyed as in ``SECTIONS``.

    A section is missing from the result when its heading can't be found
    (for example, smaller companies may omit risk factors).
    """
    headings = [(m.start(), m.group(1).lower()) for m in ITEM_HEADING.finditer(text)]
    found: dict[str, str] = {}
    for key, item in SECTIONS.items():
        best = ""
        for i, (start, number) in enumerate(headings):
            if number != item:
                continue
            end = next(
                (pos for pos, other in headings[i + 1 :] if other != item),
                len(text),
            )
            if end - start > len(best):
                best = text[start:end].strip()
        if best:
            found[key] = best
    return found


def filing_sections(html: str) -> dict[str, str]:
    return extract_sections(html_to_text(html))
