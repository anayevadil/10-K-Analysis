"""Key ratios computed from the three statements."""

from __future__ import annotations

import pandas as pd

from tenk.financials.statements import Statements

RATIO_LABELS = {
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "fcf_margin": "Free cash flow margin",
    "revenue_growth": "Revenue growth",
    "roe": "Return on equity",
    "current_ratio": "Current ratio",
    "debt_to_equity": "Debt to equity",
}

# Ratios shown as percentages; the rest are plain multiples.
PERCENT_RATIOS = {
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "revenue_growth",
    "roe",
}


_INPUTS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "free_cash_flow",
    "shareholders_equity",
    "current_assets",
    "current_liabilities",
    "short_term_debt",
    "long_term_debt",
)


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_ratios(s: Statements) -> pd.DataFrame:
    """One row per ratio, one column per fiscal year (same columns as the statements)."""
    rows: dict[str, list[float | None]] = {key: [] for key in RATIO_LABELS}
    previous: str | None = None
    for period in s.periods:
        values = {key: s.value(key, period) for key in _INPUTS}
        v = values.get
        revenue = v("revenue")
        rows["gross_margin"].append(_div(v("gross_profit"), revenue))
        rows["operating_margin"].append(_div(v("operating_income"), revenue))
        rows["net_margin"].append(_div(v("net_income"), revenue))
        rows["fcf_margin"].append(_div(v("free_cash_flow"), revenue))

        prior_revenue = s.value("revenue", previous) if previous else None
        growth = _div(revenue, prior_revenue)
        rows["revenue_growth"].append(None if growth is None else growth - 1)

        # Return on average equity when the prior year is available, else ending equity.
        equity = v("shareholders_equity")
        prior_equity = s.value("shareholders_equity", previous) if previous else None
        if equity is not None and prior_equity is not None:
            equity = (equity + prior_equity) / 2
        rows["roe"].append(_div(v("net_income"), equity))

        rows["current_ratio"].append(_div(v("current_assets"), v("current_liabilities")))
        debt_parts = [d for d in (v("short_term_debt"), v("long_term_debt")) if d is not None]
        debt = sum(debt_parts) if debt_parts else None
        rows["debt_to_equity"].append(_div(debt, v("shareholders_equity")))
        previous = period

    return pd.DataFrame(rows, index=s.periods, dtype="float64").T
