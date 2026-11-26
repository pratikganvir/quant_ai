#!/usr/bin/env python3
"""Backtest RELIANCE inside-day breakout (15-session max hold)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from quant.data.daily_cache import get_daily_bars
from quant.research.reliance_inside_breakout import (
    IDB_FULL_SAMPLE_BENCHMARK,
    STRATEGY_LABEL,
    backtest_reliance_inside_breakout,
    params_fingerprint,
    summary_as_dict,
)


def _load_bars(symbol: str, start: str | None, mcp_url: str | None) -> list[dict]:
    if mcp_url:
        import urllib.request

        url = f"{mcp_url.rstrip('/')}/api/daily/{symbol}?limit=10000"
        if start:
            url += f"&start={start}"
        with urllib.request.urlopen(url, timeout=120) as resp:
            payload = json.load(resp)
        return payload["bars"]
    return get_daily_bars(symbol, date_start=start, limit=10_000)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="RELIANCE")
    p.add_argument("--start", default="2011-01-01")
    p.add_argument("--mcp", default=None, help="e.g. https://mcp.marketsenseanalytics.com")
    args = p.parse_args()

    bars = _load_bars(args.symbol, args.start, args.mcp)
    if not bars:
        raise SystemExit(f"No daily bars for {args.symbol}")
    import pandas as pd

    df = pd.DataFrame(bars)
    trades, equity, summary = backtest_reliance_inside_breakout(df)
    print(STRATEGY_LABEL)
    print("params:", params_fingerprint())
    print("summary:", summary_as_dict(summary))
    print("benchmark (full-sample reference):", IDB_FULL_SAMPLE_BENCHMARK)
    if not trades.empty:
        print("exit_reason counts:", trades["exit_reason"].value_counts().to_dict())
        print("max hold sessions:", int(trades["hold_sessions"].max()))
        print("last 3 trades:")
        print(trades.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()