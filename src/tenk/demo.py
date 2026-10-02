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


def example_10k_sections() -> dict[str, str]:
    """Business, Risk Factors and MD&A of Example Corp's fictional 2024 10-K.

    Written to match ``example_company_facts`` so the agent's 10-K search and
    its financial tools agree with each other in sample mode.
    """
    return {key: _unwrap(text) for key, text in _EXAMPLE_10K.items()}


def _unwrap(text: str) -> str:
    """One line per heading or paragraph, the way 10-K HTML converts to text."""
    lines: list[str] = []
    paragraph: list[str] = []
    for line in text.strip().splitlines():
        if line in _HEADINGS or line.startswith("Item "):
            if paragraph:
                lines.append(" ".join(paragraph))
                paragraph = []
            lines.append(line)
            continue
        paragraph.append(line)
        if line.endswith("."):
            lines.append(" ".join(paragraph))
            paragraph = []
    if paragraph:
        lines.append(" ".join(paragraph))
    return "\n".join(lines)


_HEADINGS = {
    "Overview",
    "Products and segments",
    "Customers",
    "Competition",
    "Suppliers",
    "2024 compared with 2023",
    "Liquidity and capital resources",
    "Outlook",
}


_EXAMPLE_10K = {
    "business": """
Item 1. Business
Overview
Example Corp designs building sensors and sells the software that office landlords and
facility managers use to monitor energy use, air quality and room bookings. We were founded
in 2009 and are headquartered in Springfield. At December 31, 2024 we had about 3,100
employees.
Products and segments
We report three revenue streams. Hardware: sensors, gateways and controllers sold to building
owners, usually as part of a new construction or renovation project. Hardware revenue was
$650 million in 2024, down from $660 million in 2023. Software subscriptions: annual or
multi-year subscriptions to the Example Cloud dashboard, priced per square foot monitored.
Subscription revenue was $520 million in 2024, up 24% from $420 million in 2023, and made up
40% of total revenue. Services: installation, maintenance contracts and training, which
brought in $130 million in 2024 compared with $120 million in 2023.
Customers
Our customers are commercial landlords, property managers, universities and hospitals. No
single customer accounted for more than 6% of revenue in 2024. About 70% of revenue comes
from North America and the rest from Europe.
Competition
We compete with large building-management companies that sell sensors together with
heating and ventilation systems, and with smaller software start-ups. We believe customers
choose us because our sensors work with most building systems and our software is simple to
set up.
Suppliers
Our sensors use microcontrollers and radio chips from a small number of suppliers. We
assemble products through two contract manufacturers in Mexico and Malaysia.
""",
    "risk_factors": """
Item 1A. Risk Factors
Demand for our hardware depends on office construction and renovation. When landlords
delay new buildings or upgrades, sales of new sensors fall first. Office construction in
our main markets slowed in 2024 and may slow further.
We rely on a small number of chip suppliers. A shortage of microcontrollers or radio chips
would delay deliveries to customers and raise our costs, as happened in 2021 and 2022.
Our software stores data about buildings and the people in them. A security breach could
expose customer data, lead to fines under privacy laws and cause customers to leave.
Large building-management companies could bundle similar monitoring software with their
heating and ventilation systems at little or no extra cost, which would put pressure on our
subscription prices.
We have $500 million of long-term notes due in 2028 and $50 million due within one year.
Higher interest rates would increase the cost of refinancing this debt.
About 30% of our revenue is earned in Europe, so a stronger US dollar reduces our reported
revenue and profit.
""",
    "mdna": """
Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
2024 compared with 2023
Revenue increased 8.3% to $1,300 million from $1,200 million. Growth came from software
subscriptions, which rose 24% to $520 million as customers added more buildings to Example
Cloud. Hardware revenue declined 1.5% to $650 million because several landlords postponed
renovation projects. Services revenue grew 8% to $130 million.
Gross margin was 40.0%, unchanged from 2023. Higher-margin subscription revenue offset
higher chip prices in hardware. Research and development expense rose to $130 million and
selling, general and administrative expense rose to $195 million, both growing in line with
revenue, so operating margin stayed at 15.0% and operating income rose to $195 million.
Net income was $148 million, up from $136 million. Net income for 2023 has been restated from
the $130 million first reported to $136 million to correct an error in the income tax
provision; the correction did not affect revenue or operating income.
Liquidity and capital resources
Cash from operations was $198 million and capital expenditures were $60 million, leaving
free cash flow of $138 million. We paid $30 million of dividends and repurchased $20 million
of shares. Cash and equivalents were $300 million at year end, up from $280 million. Debt was
unchanged at $500 million of long-term notes and $50 million due within one year.
Outlook
For 2025 we expect revenue growth of 6% to 8%, with subscriptions growing faster than
hardware. We expect gross margin to stay close to 40%.
""",
}
