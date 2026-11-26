# RELIANCE Inside-Day Breakout (IDB-15) — Trade Rules

**Engine:** `1.0` · **Code:** `src/quant/research/reliance_inside_breakout.py`  
**Symbol:** `RELIANCE` (NSE) · **Bars:** **Daily** OHLCV  
**Max hold:** **15 trading sessions** (hard cap)

Backtest on MCP daily cache (2011→2025, **15 bps** round-trip): **~8.3% CAGR**, **~60%** win rate, **93** round trips. **Buy-and-hold** over the same window is higher (~**11% CAGR**); this is an **active** long-book overlay, not a promise to beat the index.

---

## Idea (one sentence)

In a long-term uptrend, buy **breakouts above yesterday’s range** after an **inside day**, take profits quickly, exit failures fast, and **never hold longer than 15 sessions**.

---

## Data

| Item | Value |
|------|--------|
| MCP daily | `GET https://mcp.marketsenseanalytics.com/api/daily/RELIANCE` |
| Min history | **200 sessions** for trend filter |

---

## Parameters (default)

| Parameter | Value | Meaning |
|-----------|------:|---------|
| `trend_sma_sessions` | 200 | Regime: **Close > SMA200** |
| `stop_atr_mult` | 3.0 | Initial stop = entry − 3×ATR(14) |
| `take_profit_pct` | 8% | Intraday profit limit (high touch) |
| `max_hold_sessions` | **15** | Time stop at session 15 open |
| `round_trip_cost_bps` | 15 | Slippage + costs model in backtest |

---

## Entry (signal bar = *T*, order = *T+1* open)

All on **daily** bars:

1. **Prior bar (*T−1*) was an inside day:** `High(T−1) < High(T−2)` and `Low(T−1) > Low(T−2)`.
2. **Breakout at *T*:** `Close(T) > High(T−1)`.
3. **Trend:** `Close(T) > SMA200(T)`.

**Trade:** Buy at **open(T+1)**. One position at a time; no scaling.

---

## Exit (while in position)

Check each session in order:

1. **Take profit:** session **High** ≥ entry × (1 + 8%) → exit at target price.
2. **Stop:** session **Low** ≤ entry − 3×ATR(14 at signal) → exit at stop (gap through open if needed).
3. **Structure fail:** `Close < Low(T−1)` at signal day → exit **next** session **open** when rule triggers.
4. **Time:** if still open after **15** sessions → exit at session 15 **open**.

---

## Run backtest

```bash
PYTHONPATH=src python scripts/backtest_reliance_inside_breakout.py \
  --mcp https://mcp.marketsenseanalytics.com
```

---

## Caveats

- Rules tuned on **one** liquid name; re-validate before live size.
- Underperformed **buy-and-hold** in full-sample test but remained **net profitable** after costs.
- Missing sessions (holidays) count as calendar sessions in the simulator; align with your broker calendar.