"""Build annual income statement, balance sheet and cash flow from XBRL company facts.

Input is the JSON returned by EDGAR's companyfacts endpoint
(``EdgarClient.company_facts``). Only annual values from 10-K filings are
used. When several filings report the same period (each 10-K repeats the
prior years), the most recently filed value wins, so restatements are
picked up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from tenk.financials.xbrl_map import ALL_ITEMS, LineItem, Statement

ANNUAL_FORMS = {"10-K", "10-K/A"}
# A fiscal year is 52 or 53 weeks, or 12 calendar months.
MIN_YEAR_DAYS, MAX_YEAR_DAYS = 340, 380
DERIVED = "derived"

# Tags used to discover which fiscal years a company has reported.
_PERIOD_ANCHORS = ("revenue", "net_income", "cfo")


@dataclass
class Statements:
    company: str
    cik: int
    income: pd.DataFrame
    balance: pd.DataFrame
    cash_flow: pd.DataFrame
    # (item key, period end) -> XBRL tag the value came from, or "derived"
    sources: dict[tuple[str, str], str]

    @property
    def periods(self) -> list[str]:
        return list(self.income.columns)

    def value(self, key: str, period: str) -> float | None:
        for frame in (self.income, self.balance, self.cash_flow):
            if key in frame.index and period in frame.columns:
                found = frame.at[key, period]
                return None if pd.isna(found) else float(found)
        return None


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def annual_values(facts: dict[str, Any], tag: str, item: LineItem) -> dict[str, float]:
    """Annual values for one tag, keyed by period end date (YYYY-MM-DD)."""
    entries = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {})
    best: dict[str, tuple[str, float]] = {}  # end -> (filed, value)
    for entry in entries.get(item.unit, []):
        if entry.get("form") not in ANNUAL_FORMS:
            continue
        start = entry.get("start")
        if item.period_type == "duration":
            if start is None or not MIN_YEAR_DAYS <= _days(start, entry["end"]) <= MAX_YEAR_DAYS:
                continue
        elif start is not None:
            continue
        end, filed = entry["end"], entry.get("filed", "")
        if end not in best or filed >= best[end][0]:
            best[end] = (filed, float(entry["val"]))
    return {end: value for end, (_, value) in best.items()}


def fiscal_year_ends(facts: dict[str, Any]) -> list[str]:
    """All fiscal year end dates the company has reported, oldest first."""
    ends: set[str] = set()
    for item in ALL_ITEMS:
        if item.key in _PERIOD_ANCHORS:
            for tag in item.tags:
                ends.update(annual_values(facts, tag, item))
    return sorted(ends)


def build_statements(facts: dict[str, Any], years: int = 5) -> Statements:
    periods = fiscal_year_ends(facts)[-years:]
    if not periods:
        raise ValueError("No annual 10-K data found in company facts")

    values: dict[str, dict[str, float]] = {p: {} for p in periods}
    sources: dict[tuple[str, str], str] = {}
    tag_cache: dict[tuple[str, str], dict[str, float]] = {}

    for item in ALL_ITEMS:
        for period in periods:
            for tag in item.tags:
                if (tag, item.unit) not in tag_cache:
                    tag_cache[tag, item.unit] = annual_values(facts, tag, item)
                if period in tag_cache[tag, item.unit]:
                    values[period][item.key] = tag_cache[tag, item.unit][period]
                    sources[item.key, period] = tag
                    break
            else:
                if item.derive is not None:
                    derived = item.derive(values[period])
                    if derived is not None:
                        values[period][item.key] = derived
                        sources[item.key, period] = DERIVED

    def frame(statement: Statement) -> pd.DataFrame:
        keys = [i.key for i in ALL_ITEMS if i.statement == statement]
        data = pd.DataFrame(
            {p: [values[p].get(k) for k in keys] for p in periods}, index=keys, dtype="float64"
        )
        return data.dropna(how="all")

    return Statements(
        company=facts.get("entityName", ""),
        cik=int(facts.get("cik", 0)),
        income=frame("income"),
        balance=frame("balance"),
        cash_flow=frame("cash_flow"),
        sources=sources,
    )
