import math

import pytest

from tenk.financials.ratios import RATIO_LABELS, compute_ratios


@pytest.fixture
def ratios(statements):
    return compute_ratios(statements)


def test_shape(ratios, statements):
    assert list(ratios.index) == list(RATIO_LABELS)
    assert list(ratios.columns) == statements.periods


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        ("gross_margin", 0.40),
        ("operating_margin", 0.15),
        ("net_margin", 148 / 1300),
        ("fcf_margin", 138 / 1300),
        ("revenue_growth", 1300 / 1200 - 1),
        ("roe", 148 / ((940 + 1000) / 2)),
        ("current_ratio", 850 / 450),
        ("debt_to_equity", 550 / 1000),
    ],
)
def test_latest_year(ratios, ratio, expected):
    assert ratios.at[ratio, "2024-12-31"] == pytest.approx(expected)


def test_first_year_has_no_growth(ratios):
    # The prior year is outside the five-year window.
    assert math.isnan(ratios.at["revenue_growth", "2020-12-31"])


def test_first_year_roe_uses_ending_equity(ratios):
    assert ratios.at["roe", "2020-12-31"] == pytest.approx(100 / 760)
