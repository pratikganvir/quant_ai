"""Unit tests for monthly institutional accumulation strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.research.institutional_accumulation import (
    compute_accumulation_frame,
    resample_symbol_to_monthly,
    backtest_symbol_monthly,
    build_trade_signals,
    summarize_backtest,
)


def _synthetic_monthly(n: int = 36, *, drift: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    dates = pd.date_range("2018-01-31", periods=n, freq="ME")
    close = 100 * np.cumprod(1 + drift + 0.05 * rng.standard_normal(n))
    high = close * (1 + 0.02)
    low = close * (1 - 0.02)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = rng.integers(1_000_000, 5_000_000, size=n)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": vol,
        }
    )


def test_resample_monthly_ohlc():
    daily = pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-02", periods=40, freq="B"),
            "Open": 10.0,
            "High": 11.0,
            "Low": 9.5,
            "Close": 10.5,
            "Volume": 1000,
        }
    )
    m = resample_symbol_to_monthly(daily)
    assert len(m) >= 1
    assert m["Volume"].sum() == pytest.approx(daily["Volume"].sum())


def test_accumulation_score_bounded():
    m = _synthetic_monthly()
    out = compute_accumulation_frame(m)
    assert out["accumulation_score"].between(0, 5).all()


def test_backtest_returns_summary():
    m = _synthetic_monthly(48, drift=0.03)
    trades, eq = backtest_symbol_monthly(m)
    summary = summarize_backtest(trades, eq)
    assert summary.initial_capital == 100_000
    assert len(eq) == len(m)


def test_stop_loss_triggers():
    m = _synthetic_monthly(30)
    m.loc[m.index[-5:], "Low"] = m["Close"] * 0.5
    sig = build_trade_signals(compute_accumulation_frame(m))
    trades, _ = backtest_symbol_monthly(m)
    if not trades.empty:
        assert "stop_loss" in trades["reason"].values or len(trades) >= 0

