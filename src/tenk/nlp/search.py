"""Keyword search over a 10-K's sections with BM25.

The 10-K text is split into passages of a few sentences, and BM25 ranks them
by how often the query's words appear, giving rare words more weight. It
needs no embeddings, API calls or extra dependencies, which is enough for
questions that name what they're looking for ("chip suppliers", "restated").
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from tenk.sources.filing_text import SECTION_LABELS

PASSAGE_WORDS = 120  # target passage length; lines are never split
K1 = 1.5
B = 0.75

_STOPWORDS = """
a an and are as at be been by did do does for from had has have how in is it its of on or our
that the their this to was were what when which who why will with company companies
"""
STOPWORDS = frozenset(_STOPWORDS.split())


@dataclass(frozen=True)
class Passage:
    id: int
    section: str  # key from SECTION_LABELS, e.g. "risk_factors"
    text: str

    @property
    def section_label(self) -> str:
        return SECTION_LABELS.get(self.section, self.section)


def tokenize(text: str) -> list[str]:
    text = re.sub(r"['’]s\b", "", text.lower())  # "company's" -> "company"
    words = re.findall(r"[a-z0-9]+", text)
    return [_stem(w) for w in words if w not in STOPWORDS]


def _stem(word: str) -> str:
    """Crude plural folding so "suppliers" matches "supplier"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def split_passages(sections: dict[str, str], words: int = PASSAGE_WORDS) -> list[Passage]:
    """Group each section's lines into passages of roughly ``words`` words."""
    passages: list[Passage] = []
    for section, text in sections.items():
        chunk: list[str] = []
        size = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            chunk.append(line)
            size += len(line.split())
            if size >= words:
                passages.append(Passage(len(passages), section, "\n".join(chunk)))
                chunk, size = [], 0
        if chunk:
            passages.append(Passage(len(passages), section, "\n".join(chunk)))
    return passages


class TenKIndex:
    def __init__(self, sections: dict[str, str]) -> None:
        self.passages = split_passages(sections)
        self._terms = [Counter(tokenize(p.text)) for p in self.passages]
        self._lengths = [sum(terms.values()) for terms in self._terms]
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 0
        document_frequency: Counter[str] = Counter()
        for terms in self._terms:
            document_frequency.update(terms.keys())
        n = len(self.passages)
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    def search(self, query: str, top_k: int = 5) -> list[tuple[Passage, float]]:
        """Best-matching passages, highest score first; passages with no query word are left out."""
        query_terms = set(tokenize(query))
        scored = []
        for passage, terms, length in zip(self.passages, self._terms, self._lengths, strict=True):
            score = 0.0
            for term in query_terms:
                tf = terms.get(term, 0)
                if tf:
                    norm = K1 * (1 - B + B * length / self._average_length)
                    score += self._idf[term] * tf * (K1 + 1) / (tf + norm)
            if score > 0:
                scored.append((passage, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
