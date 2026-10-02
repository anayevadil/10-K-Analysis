"""Which XBRL tags feed each line item of the three statements.

Companies tag the same concept differently, and many switched tags over time
(for example to RevenueFromContractWithCustomerExcludingAssessedTax after
ASC 606 in 2018). Each line item therefore lists fallback tags in priority
order; for every fiscal year the first tag with a reported value wins.

Line items with ``derive`` are computed from other line items when no tag has
a value for that year.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Statement = Literal["income", "balance", "cash_flow"]
PeriodType = Literal["duration", "instant"]

USD = "USD"
USD_PER_SHARE = "USD/shares"
SHARES = "shares"


@dataclass(frozen=True)
class LineItem:
    key: str
    label: str
    statement: Statement
    tags: tuple[str, ...]
    unit: str = USD
    derive: Callable[[dict[str, float]], float | None] | None = field(default=None, compare=False)

    @property
    def period_type(self) -> PeriodType:
        return "instant" if self.statement == "balance" else "duration"


def _diff(a: str, b: str) -> Callable[[dict[str, float]], float | None]:
    def compute(values: dict[str, float]) -> float | None:
        if a in values and b in values:
            return values[a] - values[b]
        return None

    return compute


INCOME_STATEMENT = (
    LineItem(
        "revenue",
        "Revenue",
        "income",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    ),
    LineItem(
        "cost_of_revenue",
        "Cost of revenue",
        "income",
        ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"),
    ),
    LineItem(
        "gross_profit",
        "Gross profit",
        "income",
        ("GrossProfit",),
        derive=_diff("revenue", "cost_of_revenue"),
    ),
    LineItem("rnd", "Research and development", "income", ("ResearchAndDevelopmentExpense",)),
    LineItem(
        "sga",
        "Selling, general and administrative",
        "income",
        ("SellingGeneralAndAdministrativeExpense",),
    ),
    LineItem("operating_income", "Operating income", "income", ("OperatingIncomeLoss",)),
    LineItem(
        "interest_expense",
        "Interest expense",
        "income",
        ("InterestExpense", "InterestExpenseNonoperating"),
    ),
    LineItem(
        "pretax_income",
        "Income before taxes",
        "income",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    LineItem("income_tax", "Income tax", "income", ("IncomeTaxExpenseBenefit",)),
    LineItem("net_income", "Net income", "income", ("NetIncomeLoss", "ProfitLoss")),
    LineItem(
        "eps_diluted", "Diluted EPS", "income", ("EarningsPerShareDiluted",), unit=USD_PER_SHARE
    ),
    LineItem(
        "diluted_shares",
        "Diluted shares",
        "income",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        unit=SHARES,
    ),
)

BALANCE_SHEET = (
    LineItem(
        "cash",
        "Cash and equivalents",
        "balance",
        ("CashAndCashEquivalentsAtCarryingValue",),
    ),
    LineItem(
        "short_term_investments",
        "Short-term investments",
        "balance",
        ("ShortTermInvestments", "MarketableSecuritiesCurrent"),
    ),
    LineItem("receivables", "Accounts receivable", "balance", ("AccountsReceivableNetCurrent",)),
    LineItem("inventory", "Inventory", "balance", ("InventoryNet",)),
    LineItem("current_assets", "Total current assets", "balance", ("AssetsCurrent",)),
    LineItem("ppe", "Property, plant and equipment", "balance", ("PropertyPlantAndEquipmentNet",)),
    LineItem("goodwill", "Goodwill", "balance", ("Goodwill",)),
    LineItem("total_assets", "Total assets", "balance", ("Assets",)),
    LineItem("accounts_payable", "Accounts payable", "balance", ("AccountsPayableCurrent",)),
    LineItem(
        "short_term_debt",
        "Short-term debt",
        "balance",
        ("LongTermDebtCurrent", "DebtCurrent"),
    ),
    LineItem(
        "current_liabilities", "Total current liabilities", "balance", ("LiabilitiesCurrent",)
    ),
    LineItem("long_term_debt", "Long-term debt", "balance", ("LongTermDebtNoncurrent",)),
    LineItem(
        "total_equity",
        "Total equity",
        "balance",
        (
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
        ),
    ),
    LineItem(
        "total_liabilities",
        "Total liabilities",
        "balance",
        ("Liabilities",),
        derive=_diff("total_assets", "total_equity"),
    ),
    LineItem(
        "shareholders_equity",
        "Shareholders' equity (parent)",
        "balance",
        ("StockholdersEquity",),
    ),
)

CASH_FLOW = (
    LineItem(
        "cfo",
        "Cash from operations",
        "cash_flow",
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    LineItem(
        "d_and_a",
        "Depreciation and amortization",
        "cash_flow",
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
        ),
    ),
    LineItem(
        "sbc",
        "Stock-based compensation",
        "cash_flow",
        ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    ),
    LineItem(
        "capex",
        "Capital expenditures",
        "cash_flow",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    LineItem(
        "cfi",
        "Cash from investing",
        "cash_flow",
        (
            "NetCashProvidedByUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
        ),
    ),
    LineItem(
        "dividends",
        "Dividends paid",
        "cash_flow",
        ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    ),
    LineItem("buybacks", "Share repurchases", "cash_flow", ("PaymentsForRepurchaseOfCommonStock",)),
    LineItem(
        "cff",
        "Cash from financing",
        "cash_flow",
        (
            "NetCashProvidedByUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
        ),
    ),
    LineItem(
        "free_cash_flow",
        "Free cash flow",
        "cash_flow",
        (),
        derive=_diff("cfo", "capex"),
    ),
)

ALL_ITEMS: tuple[LineItem, ...] = INCOME_STATEMENT + BALANCE_SHEET + CASH_FLOW
