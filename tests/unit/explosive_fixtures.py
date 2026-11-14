"""Synthetic panels for explosive-move research tests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_symbol_series(
    symbol: str,
    dates: pd.DatetimeIndex,
    *,
    base: float = 100.0,
    step: float = 0.0,
    gap_session_at: int | None = None,
) -> pd.DataFrame:
    """One symbol; optional missing bar at gap_session_at (skipped date)."""
    rows = []
    for i, d in enumerate(dates):
        if gap_session_at is not None and i == gap_session_at:
            continue
        c = base + i * step
        rows.append(
            {
                "Date": d,
                "Symbol": symbol,
                "Open": c,
                "High": c + 1,
                "Low": c - 1,
                "Close": c,
                "Volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def two_symbol_panel_with_gap() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """AAA has full calendar; BBB missing one session (tests calendar vs row count)."""
    cal = pd.bdate_range("2025-03-01", periods=40)
    aaa = daily_symbol_series("AAA", cal, step=0.1)
    bbb = daily_symbol_series("BBB", cal, step=0.05, gap_session_at=15)
    panel = pd.concat([aaa, bbb], ignore_index=True)
    return panel, pd.DatetimeIndex(cal)


def single_symbol_linear_highs() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    cal = pd.bdate_range("2025-01-01", periods=25)
    rows = []
    for i, d in enumerate(cal):
        c = 100.0 + i
        rows.append(
            {
                "Date": d,
                "Symbol": "LIN",
                "Open": c,
                "High": c + (5 if i >= 3 else 0),
                "Low": c - 1,
                "Close": c,
                "Volume": 500_000,
            }
        )
    return pd.DataFrame(rows), pd.DatetimeIndex(cal)


def triple_barrier_panel(up_first: bool = True) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    cal = pd.bdate_range("2025-06-01", periods=10)
    rows = []
    for i, d in enumerate(cal):
        if i == 0:
            h, l, c = 100.0, 99.0, 100.0
        elif i == 1:
            if up_first:
                h, l, c = 126.0, 99.0, 125.0
            else:
                h, l, c = 101.0, 88.0, 90.0
        else:
            h, l, c = 100.0, 99.0, 100.0
        rows.append({"Date": d, "Symbol": "TB", "Open": c, "High": h, "Low": l, "Close": c, "Volume": 1e6})
    return pd.DataFrame(rows), pd.DatetimeIndex(cal)


def ca_gap_panel() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    cal = pd.bdate_range("2025-02-01", periods=15)
    rows = []
    for i, d in enumerate(cal):
        c = 200.0 if i < 5 else 100.0
        prev = np.nan if i == 0 else (200.0 if i == 5 else c - 0.5)
        rows.append(
            {
                "Date": d,
                "Symbol": "SPL",
                "Open": c,
                "High": c + 1,
                "Low": c - 1,
                "Close": c,
                "Volume": 1e6,
                "PrevClose": prev,
            }
        )
    return pd.DataFrame(rows), pd.DatetimeIndex(cal)