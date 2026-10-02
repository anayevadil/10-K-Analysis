"""Grade the agent's answers against the eval set in ``evals/questions.toml``.

Questions are about the fictional sample company, so the expected numbers are
read from its statements and ratios rather than typed into the eval file.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tenk.agent.loop import AgentAnswer
from tenk.service import CompanyData

EVAL_FILE = Path(__file__).parents[3] / "evals" / "questions.toml"
DEFAULT_TOLERANCE = 0.01

_SCALES = {
    "trillion": 1e12,
    "tn": 1e12,
    "t": 1e12,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "mm": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
}
_NUMBER = re.compile(
    r"(?<![\w.])(-?)\$?(-?\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|trillion\b|billion\b|million\b|"
    r"thousand\b|tn\b|bn\b|mn\b|mm\b|[tbmk]\b|x\b)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    item: str | None = None
    year: int | None = None
    tolerance: float = DEFAULT_TOLERANCE
    tools: list[str] = field(default_factory=list)
    mentions_all: list[str] = field(default_factory=list)
    mentions_any: list[str] = field(default_factory=list)
    must_not_mention: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)


def load_cases(path: Path = EVAL_FILE) -> list[EvalCase]:
    with path.open("rb") as file:
        return [EvalCase(**case) for case in tomllib.load(file)["question"]]


def extract_numbers(text: str) -> list[float]:
    """Every number in ``text``, scaled: "$1.3 billion" -> 1.3e9, "15.3%" -> 0.153."""
    numbers = []
    for match in _NUMBER.finditer(text):
        value = float(match.group(2).replace(",", ""))
        if match.group(1):  # "-$5m"
            value = -value
        unit = (match.group(3) or "").lower()
        if unit in ("%", "percent"):
            value /= 100
        elif unit in _SCALES:
            value *= _SCALES[unit]
        numbers.append(value)
    return numbers


def expected_value(case: EvalCase, data: CompanyData) -> float | None:
    if case.item is None or case.year is None:
        return None
    period = next(p for p in data.statements.periods if p.startswith(str(case.year)))
    if case.item in data.ratios.index:
        value = data.ratios.at[case.item, period]
    else:
        value = data.statements.value(case.item, period)
    if value is None or math.isnan(value):
        raise ValueError(f"{case.id}: no value for {case.item} in {case.year}")
    return float(value)


def grade(case: EvalCase, answer: AgentAnswer, data: CompanyData) -> list[str]:
    """Reasons the answer fails; an empty list means it passes."""
    failures = []
    text = answer.answer
    lower = text.lower()
    if answer.stopped:
        failures.append(f"loop stopped early: {answer.stopped}")

    expected = expected_value(case, data)
    if expected is not None:
        found = extract_numbers(text)
        if not any(_close(value, expected, case.tolerance) for value in found):
            failures.append(f"expected {expected:,.4g} in the answer, found {found}")

    used = set(answer.tools_used)
    failures += [f"didn't call {tool}" for tool in case.tools if tool not in used]
    failures += [f"doesn't mention {w!r}" for w in case.mentions_all if w.lower() not in lower]
    if case.mentions_any and not any(w.lower() in lower for w in case.mentions_any):
        failures.append(f"mentions none of {case.mentions_any}")
    failures += [f"mentions {w!r}" for w in case.must_not_mention if w.lower() in lower]
    failures += [f"no [{c} ...] citation" for c in case.cites if f"[{c}" not in text]
    return failures


def _close(value: float, expected: float, tolerance: float) -> bool:
    if expected == 0:
        return abs(value) <= tolerance
    return abs(value - expected) <= tolerance * abs(expected)


def summarize(results: list[dict[str, Any]]) -> str:
    passed = sum(1 for r in results if not r["failures"])
    return f"{passed}/{len(results)} eval questions passed"
