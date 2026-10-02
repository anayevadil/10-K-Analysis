import pytest

from tenk.financials.statements import DERIVED, build_statements, fiscal_year_ends

M = 1_000_000
PERIODS = ["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]


def test_keeps_last_five_fiscal_years(statements):
    assert statements.periods == PERIODS
    assert list(statements.balance.columns) == PERIODS
    assert list(statements.cash_flow.columns) == PERIODS


def test_finds_all_fiscal_years(example_facts):
    assert fiscal_year_ends(example_facts)[0] == "2019-12-31"


def test_company_metadata(statements):
    assert statements.company == "Example Corp"
    assert statements.cik == 1234567


def test_revenue_follows_tag_switch(statements):
    assert statements.value("revenue", "2020-12-31") == 900 * M
    assert statements.sources["revenue", "2020-12-31"] == "SalesRevenueNet"
    assert statements.value("revenue", "2024-12-31") == 1300 * M
    assert (
        statements.sources["revenue", "2024-12-31"]
        == "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


def test_ignores_quarterly_and_10q_values(statements):
    # The fixture also carries a Q4-only value (340) and a 10-Q value (960) for 2024.
    assert statements.value("revenue", "2024-12-31") == 1300 * M


def test_uses_restated_value(statements):
    assert statements.value("net_income", "2023-12-31") == pytest.approx(136 * M)


def test_derives_gross_profit_when_untagged(statements):
    assert statements.value("gross_profit", "2021-12-31") == pytest.approx(400 * M)
    assert statements.sources["gross_profit", "2021-12-31"] == DERIVED
    assert statements.sources["gross_profit", "2022-12-31"] == "GrossProfit"


def test_derives_total_liabilities(statements):
    assert statements.value("total_liabilities", "2024-12-31") == 1000 * M
    assert statements.sources["total_liabilities", "2024-12-31"] == DERIVED


def test_free_cash_flow(statements):
    assert statements.value("free_cash_flow", "2024-12-31") == pytest.approx(138 * M)


def test_non_usd_units(statements):
    assert statements.value("eps_diluted", "2024-12-31") == pytest.approx(1.48)
    assert statements.value("diluted_shares", "2024-12-31") == 100 * M


def test_drops_items_with_no_data(statements):
    assert "inventory" not in statements.balance.index
    assert "goodwill" not in statements.balance.index


def test_balance_sheet_balances(statements):
    for period in statements.periods:
        liabilities = statements.value("total_liabilities", period)
        equity = statements.value("total_equity", period)
        assert liabilities + equity == statements.value("total_assets", period)


def test_no_annual_data_raises():
    with pytest.raises(ValueError, match="No annual"):
        build_statements({"facts": {"us-gaap": {}}})
