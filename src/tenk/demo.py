"""Sample data: a small, fictional company in the exact shape of EDGAR's companyfacts JSON.

Used by the tests and by the app's offline sample mode, so the app can be
tried without network access to sec.gov. Numbers are round on purpose so
expected ratios can be checked by hand.
Edge cases built in:
- revenue switches tag in 2021 (SalesRevenueNet -> RevenueFromContract...)
- GrossProfit is only tagged from 2022; earlier years must be derived
- 2023 net income was restated in the 2024 10-K (130 -> 136)
- a Q4 duration inside a 10-K and a 10-Q value, both of which must be ignored
- no Liabilities tag, so total liabilities must be derived
"""

from __future__ import annotations

M = 1_000_000
YEARS = [2019, 2020, 2021, 2022, 2023, 2024]


def _filed(year: int) -> str:
    return f"{year + 1}-02-15"


def _duration(year: int, value: float, *, filed_year: int | None = None, form: str = "10-K"):
    filed_year = filed_year or year
    return {
        "start": f"{year}-01-01",
        "end": f"{year}-12-31",
        "val": value,
        "fy": filed_year,
        "fp": "FY",
        "form": form,
        "filed": _filed(filed_year),
    }


def _instant(year: int, value: float):
    return {
        "end": f"{year}-12-31",
        "val": value,
        "fy": year,
        "fp": "FY",
        "form": "10-K",
        "filed": _filed(year),
    }


def _tag(entries, unit="USD"):
    return {"label": "", "description": "", "units": {unit: entries}}


def _series(kind, values_by_year, unit="USD"):
    make = _duration if kind == "duration" else _instant
    return _tag([make(y, v) for y, v in values_by_year.items()], unit)


def example_company_facts() -> dict:
    revenue = {y: (800 + 100 * i) * M for i, y in enumerate(YEARS)}  # 800 .. 1300
    cogs = {y: 0.6 * r for y, r in revenue.items()}
    operating = {y: 0.15 * r for y, r in revenue.items()}
    net_income = {y: 0.8 * (operating[y] - 10 * M) for y in YEARS}  # 88 .. 148
    assets = {y: (1500 + 100 * i) * M for i, y in enumerate(YEARS)}
    equity = {y: (700 + 60 * i) * M for i, y in enumerate(YEARS)}
    cfo = {y: net_income[y] + 50 * M for y in YEARS}

    us_gaap = {
        "SalesRevenueNet": _series("duration", {y: revenue[y] for y in (2019, 2020)}),
        "RevenueFromContractWithCustomerExcludingAssessedTax": _tag(
            [_duration(y, revenue[y]) for y in YEARS if y >= 2021]
            # Q4-only duration reported inside the 10-K: not an annual value
            + [
                {
                    "start": "2024-10-01",
                    "end": "2024-12-31",
                    "val": 340 * M,
                    "fy": 2024,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": _filed(2024),
                }
            ]
            # nine-month value from a 10-Q
            + [_duration(2024, 960 * M, form="10-Q")]
        ),
        "CostOfRevenue": _series("duration", cogs),
        "GrossProfit": _series("duration", {y: revenue[y] - cogs[y] for y in YEARS if y >= 2022}),
        "ResearchAndDevelopmentExpense": _series(
            "duration", {y: 0.1 * r for y, r in revenue.items()}
        ),
        "SellingGeneralAndAdministrativeExpense": _series(
            "duration", {y: 0.15 * r for y, r in revenue.items()}
        ),
        "OperatingIncomeLoss": _series("duration", operating),
        "InterestExpense": _series("duration", {y: 10 * M for y in YEARS}),
        "IncomeTaxExpenseBenefit": _series(
            "duration", {y: 0.2 * (operating[y] - 10 * M) for y in YEARS}
        ),
        "NetIncomeLoss": _tag(
            [_duration(y, net_income[y]) for y in YEARS if y != 2023]
            + [
                _duration(2023, 130 * M, filed_year=2023),  # as first reported
                _duration(2023, net_income[2023], filed_year=2024),  # restated
            ]
        ),
        "EarningsPerShareDiluted": _series(
            "duration", {y: net_income[y] / (100 * M) for y in YEARS}, unit="USD/shares"
        ),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _series(
            "duration", {y: 100 * M for y in YEARS}, unit="shares"
        ),
        "CashAndCashEquivalentsAtCarryingValue": _series(
            "instant", {y: (200 + 20 * i) * M for i, y in enumerate(YEARS)}
        ),
        "AssetsCurrent": _series("instant", {y: (600 + 50 * i) * M for i, y in enumerate(YEARS)}),
        "Assets": _series("instant", assets),
        "LiabilitiesCurrent": _series(
            "instant", {y: (400 + 10 * i) * M for i, y in enumerate(YEARS)}
        ),
        "LongTermDebtCurrent": _series("instant", {y: 50 * M for y in YEARS}),
        "LongTermDebtNoncurrent": _series("instant", {y: 500 * M for y in YEARS}),
        "StockholdersEquity": _series("instant", equity),
        "LiabilitiesAndStockholdersEquity": _series("instant", assets),
        "NetCashProvidedByUsedInOperatingActivities": _series("duration", cfo),
        "DepreciationDepletionAndAmortization": _series("duration", {y: 50 * M for y in YEARS}),
        "PaymentsToAcquirePropertyPlantAndEquipment": _series(
            "duration", {y: 60 * M for y in YEARS}
        ),
        "PaymentsOfDividends": _series("duration", {y: 30 * M for y in YEARS}),
        "PaymentsForRepurchaseOfCommonStock": _series("duration", {y: 20 * M for y in YEARS}),
    }
    return {"cik": 1234567, "entityName": "Example Corp", "facts": {"us-gaap": us_gaap}}
