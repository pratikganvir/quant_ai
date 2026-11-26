"""Daily max-hold backtest + inside-breakout signals."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant.research.daily_max_hold import backtest_daily_max_hold, normalize_daily_ohlcv
from quant.research.reliance_inside_breakout import (
    backtest_reliance_inside_breakout,
    compute_inside_breakout_signals,
)


def _row(d: date, o: float, h: float, l: float, c: float) -> dict:
    return {"Date": d, "Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}


def test_max_hold_caps_sessions():
    days = [_row(date(2024, 1, i), 100, 101, 99, 100.5) for i in range(1, 25)]
    df = normalize_daily_ohlcv(pd.DataFrame(days))
    entry = pd.Series(False, index=df.index)
    exit_ = pd.Series(False, index=df.index)
    entry.iloc[16] = True  # ATR(14) defined
    trades, _, summary = backtest_daily_max_hold(df, entry, exit_, max_hold_sessions=5)
    assert len(trades) == 1
    assert trades.iloc[0]["hold_sessions"] == 5
    assert trades.iloc[0]["exit_reason"] == "max_hold"


def test_hold_sessions_never_exceed_param():
    idx = pd.date_range("2024-01-02", periods=79, freq="B")
    days = [
        _row(d.date(), 100 + n * 0.1, 102 + n * 0.1, 98, 101 + n * 0.1)
        for n, d in enumerate(idx)
    ]
    df = normalize_daily_ohlcv(pd.DataFrame(days))
    frame, entry, _ = compute_inside_breakout_signals(df, {"trend_sma_sessions": 20})
    trades, _, _ = backtest_reliance_inside_breakout(
        frame,
        {"max_hold_sessions": 15, "round_trip_cost_bps": 0, "trend_sma_sessions": 20},
    )
    if trades.empty:
        return
    assert trades["hold_sessions"].max() <= 15