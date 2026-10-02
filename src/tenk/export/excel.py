"""Export the three statements and key ratios to an Excel workbook.

Sheets:
- Summary: company, period range, notes
- Income Statement, Balance Sheet, Cash Flow: values in USD millions
- Ratios: live Excel formulas that point at the statement sheets, so the
  workbook stays a working model when someone edits a number
- Sources: the XBRL tag behind every number (or "derived")

Highlighting: subtotals and margins are bold, ratios turn green when they
improve on the prior year and red when they get worse, and a check row flags
a balance sheet that does not balance.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell
from xlsxwriter.workbook import Workbook
from xlsxwriter.worksheet import Worksheet

from tenk.financials.ratios import PERCENT_RATIOS, RATIO_LABELS, compute_ratios
from tenk.financials.statements import Statements
from tenk.financials.xbrl_map import ALL_ITEMS, SHARES, USD

ITEMS = {item.key: item for item in ALL_ITEMS}
MILLION = 1_000_000

SHEET_NAMES = {
    "income": "Income Statement",
    "balance": "Balance Sheet",
    "cash_flow": "Cash Flow",
}

# Rows shown in bold as subtotals.
SUBTOTALS = {
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "current_assets",
    "total_assets",
    "current_liabilities",
    "total_liabilities",
    "total_equity",
    "cfo",
    "cfi",
    "cff",
    "free_cash_flow",
}

# Ratios where a lower value is the better one.
LOWER_IS_BETTER = {"debt_to_equity"}

FIRST_DATA_COL = 1  # column A holds labels
HEADER_ROW = 0


@dataclass
class _Layout:
    """Where each line item landed, so ratio formulas can reference it."""

    rows: dict[str, tuple[str, int]]  # item key -> (sheet name, zero-based row)

    def cell(self, key: str, col: int) -> str | None:
        if key not in self.rows:
            return None
        sheet, row = self.rows[key]
        return f"'{sheet}'!{xl_rowcol_to_cell(row, col)}"


class _Formats:
    def __init__(self, wb: Workbook) -> None:
        self.title = wb.add_format({"bold": True, "font_size": 14})
        self.header = wb.add_format(
            {"bold": True, "bottom": 1, "align": "center", "bg_color": "#DDEBF7"}
        )
        self.label = wb.add_format({})
        self.label_bold = wb.add_format({"bold": True})
        self.money = wb.add_format({"num_format": "#,##0;(#,##0)"})
        self.money_bold = wb.add_format({"num_format": "#,##0;(#,##0)", "bold": True, "top": 1})
        self.eps = wb.add_format({"num_format": "0.00;(0.00)"})
        self.percent = wb.add_format({"num_format": "0.0%"})
        self.percent_bold = wb.add_format({"num_format": "0.0%", "bold": True})
        self.multiple = wb.add_format({"num_format": "0.00x"})
        self.check = wb.add_format({"num_format": "#,##0;(#,##0)", "italic": True})
        self.note = wb.add_format({"italic": True, "font_color": "#595959"})
        self.better = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})
        self.worse = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})
        self.bad_check = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})


def _header(ws: Worksheet, fmt: _Formats, first: str, periods: list[str]) -> None:
    ws.write(HEADER_ROW, 0, first, fmt.header)
    for i, period in enumerate(periods):
        ws.write(HEADER_ROW, FIRST_DATA_COL + i, f"FY ending {period}", fmt.header)
    ws.set_column(0, 0, 36)
    ws.set_column(FIRST_DATA_COL, FIRST_DATA_COL + len(periods) - 1, 18)
    ws.freeze_panes(HEADER_ROW + 1, FIRST_DATA_COL)


def _write_statement(
    wb: Workbook,
    fmt: _Formats,
    name: str,
    frame: pd.DataFrame,
    periods: list[str],
    layout: _Layout,
) -> Worksheet:
    ws = wb.add_worksheet(name)
    _header(ws, fmt, "USD millions (EPS in USD, shares in millions)", periods)
    for row, key in enumerate(frame.index, start=HEADER_ROW + 1):
        item = ITEMS[key]
        bold = key in SUBTOTALS
        ws.write(row, 0, item.label, fmt.label_bold if bold else fmt.label)
        if item.unit == USD:
            scale, cell_fmt = MILLION, fmt.money_bold if bold else fmt.money
        elif item.unit == SHARES:
            scale, cell_fmt = MILLION, fmt.money
        else:
            scale, cell_fmt = 1, fmt.eps
        for col, period in enumerate(periods, start=FIRST_DATA_COL):
            value = frame.at[key, period] if period in frame.columns else None
            if value is not None and not pd.isna(value):
                ws.write_number(row, col, float(value) / scale, cell_fmt)
        layout.rows[key] = (name, row)
    return ws


def _write_balance_check(
    ws: Worksheet, fmt: _Formats, periods: list[str], layout: _Layout, row: int
) -> None:
    """Assets minus liabilities minus equity; should be zero in every year."""
    needed = ("total_assets", "total_liabilities", "total_equity")
    if not all(k in layout.rows for k in needed):
        return
    ws.write(row, 0, "Check: assets - liabilities - equity", fmt.note)
    for col in range(FIRST_DATA_COL, FIRST_DATA_COL + len(periods)):
        assets, liabilities, equity = (layout.cell(k, col) for k in needed)
        ws.write_formula(row, col, f"={assets}-{liabilities}-{equity}", fmt.check)
    ws.conditional_format(
        row,
        FIRST_DATA_COL,
        row,
        FIRST_DATA_COL + len(periods) - 1,
        {
            "type": "cell",
            "criteria": "not between",
            "minimum": -1,
            "maximum": 1,
            "format": fmt.bad_check,
        },
    )


def _ratio(numerator: str, denominator: str) -> Callable[[_Layout, int], str | None]:
    def build(layout: _Layout, col: int) -> str | None:
        num, den = layout.cell(numerator, col), layout.cell(denominator, col)
        return f"{num}/{den}" if num and den else None

    return build


def _revenue_growth(layout: _Layout, col: int) -> str | None:
    if col == FIRST_DATA_COL:
        return None
    now, before = layout.cell("revenue", col), layout.cell("revenue", col - 1)
    return f"{now}/{before}-1" if now and before else None


def _roe(layout: _Layout, col: int) -> str | None:
    income, equity = layout.cell("net_income", col), layout.cell("shareholders_equity", col)
    if not income or not equity:
        return None
    if col == FIRST_DATA_COL:
        return f"{income}/{equity}"
    prior = layout.cell("shareholders_equity", col - 1)
    # Average equity, falling back to ending equity when the prior year is blank.
    return f"{income}/IF(ISNUMBER({prior}),AVERAGE({prior},{equity}),{equity})"


def _debt_to_equity(layout: _Layout, col: int) -> str | None:
    debt = [c for k in ("short_term_debt", "long_term_debt") if (c := layout.cell(k, col))]
    equity = layout.cell("shareholders_equity", col)
    if not debt or not equity:
        return None
    cells = ",".join(debt)
    return f'IF(COUNT({cells})=0,"",SUM({cells})/{equity})'


RATIO_FORMULAS: dict[str, Callable[[_Layout, int], str | None]] = {
    "gross_margin": _ratio("gross_profit", "revenue"),
    "operating_margin": _ratio("operating_income", "revenue"),
    "net_margin": _ratio("net_income", "revenue"),
    "fcf_margin": _ratio("free_cash_flow", "revenue"),
    "revenue_growth": _revenue_growth,
    "roe": _roe,
    "current_ratio": _ratio("current_assets", "current_liabilities"),
    "debt_to_equity": _debt_to_equity,
}


def _write_ratios(
    wb: Workbook, fmt: _Formats, s: Statements, periods: list[str], layout: _Layout
) -> None:
    ws = wb.add_worksheet("Ratios")
    _header(ws, fmt, "Ratio", periods)
    values = compute_ratios(s)
    last_col = FIRST_DATA_COL + len(periods) - 1
    for row, key in enumerate(RATIO_LABELS, start=HEADER_ROW + 1):
        is_percent = key in PERCENT_RATIOS
        is_margin = key.endswith("_margin")
        ws.write(row, 0, RATIO_LABELS[key], fmt.label_bold if is_margin else fmt.label)
        cell_fmt = fmt.multiple
        if is_percent:
            cell_fmt = fmt.percent_bold if is_margin else fmt.percent
        for col, period in enumerate(periods, start=FIRST_DATA_COL):
            formula = RATIO_FORMULAS[key](layout, col)
            value = values.at[key, period]
            cached = None if pd.isna(value) else float(value)
            if formula:
                ws.write_formula(row, col, f'=IFERROR({formula},"")', cell_fmt, cached)
            elif cached is not None:
                ws.write_number(row, col, cached, cell_fmt)

        # Compare each year with the one before it.
        if last_col > FIRST_DATA_COL:
            cur = xl_rowcol_to_cell(row, FIRST_DATA_COL + 1, row_abs=False, col_abs=False)
            prev = xl_rowcol_to_cell(row, FIRST_DATA_COL, row_abs=False, col_abs=False)
            both = f"ISNUMBER({cur}),ISNUMBER({prev})"
            up, down = (
                (f"{cur}<{prev}", f"{cur}>{prev}")
                if key in LOWER_IS_BETTER
                else (f"{cur}>{prev}", f"{cur}<{prev}")
            )
            for criteria, cond_fmt in ((up, fmt.better), (down, fmt.worse)):
                ws.conditional_format(
                    row,
                    FIRST_DATA_COL + 1,
                    row,
                    last_col,
                    {"type": "formula", "criteria": f"=AND({both},{criteria})", "format": cond_fmt},
                )

    note_row = HEADER_ROW + len(RATIO_LABELS) + 2
    ws.write(
        note_row,
        0,
        "Green: better than the prior year. Red: worse. "
        "Formulas update if you edit the statements.",
        fmt.note,
    )


def _write_sources(wb: Workbook, fmt: _Formats, s: Statements, periods: list[str]) -> None:
    ws = wb.add_worksheet("Sources")
    _header(ws, fmt, "Line item (XBRL tag used, or derived)", periods)
    row = HEADER_ROW + 1
    for frame in (s.income, s.balance, s.cash_flow):
        for key in frame.index:
            ws.write(row, 0, ITEMS[key].label)
            for col, period in enumerate(periods, start=FIRST_DATA_COL):
                tag = s.sources.get((key, period))
                if tag:
                    ws.write(row, col, tag)
            row += 1
    ws.set_column(FIRST_DATA_COL, FIRST_DATA_COL + len(periods) - 1, 48)


def _write_summary(wb: Workbook, fmt: _Formats, s: Statements, periods: list[str]) -> None:
    ws = wb.add_worksheet("Summary")
    ws.set_column(0, 0, 26)
    ws.set_column(1, 1, 70)
    ws.write(0, 0, s.company or "Company", fmt.title)
    rows = [
        ("SEC CIK", str(s.cik)),
        ("Fiscal years", f"{periods[0]} to {periods[-1]}"),
        ("Source", "SEC EDGAR XBRL company facts, annual 10-K values"),
        ("Units", "USD millions unless noted"),
        ("Generated", date.today().isoformat()),
        ("Note", "Generated automatically from XBRL data. Not investment advice."),
    ]
    for i, (label, value) in enumerate(rows, start=2):
        ws.write(i, 0, label, fmt.label_bold)
        ws.write(i, 1, value)


def write_workbook(s: Statements, target: str | Path | BinaryIO) -> None:
    """Write the workbook to a file path or a binary file-like object."""
    periods = s.periods
    wb = xlsxwriter.Workbook(target, {"in_memory": True})
    fmt = _Formats(wb)
    layout = _Layout(rows={})

    _write_summary(wb, fmt, s, periods)
    _write_statement(wb, fmt, SHEET_NAMES["income"], s.income, periods, layout)
    balance = _write_statement(wb, fmt, SHEET_NAMES["balance"], s.balance, periods, layout)
    _write_balance_check(balance, fmt, periods, layout, row=HEADER_ROW + len(s.balance) + 2)
    _write_statement(wb, fmt, SHEET_NAMES["cash_flow"], s.cash_flow, periods, layout)
    _write_ratios(wb, fmt, s, periods, layout)
    _write_sources(wb, fmt, s, periods)
    wb.close()


def workbook_bytes(s: Statements) -> bytes:
    """The workbook as bytes, ready for a download button."""
    buffer = io.BytesIO()
    write_workbook(s, buffer)
    return buffer.getvalue()
