# Regime + Dual Momentum (single-name monthly)

**Upgrade path:** from **Regime + Close > SMA12** to a tighter **trend + short momentum** filter.  
**Validated:** Nifty 50 median **~26.3% CAGR** vs **~25.1%** baseline, median max DD **~−14%** vs **~−20%** (independent backtests per stock, monthly opens; MCP panel Nov 2025).

Use for **one stock / one future** (e.g. RELIANCE). For a **diversified book**, prefer [`regime_momentum_trade_rules.md`](regime_momentum_trade_rules.md) or **NIFTY50 Balanced** params below.

---

## One sentence

At month-end, stay **long** only while **regime is ON**, price is **above SMA12**, and **3-month price momentum is positive**; otherwise **flat**. Trade at the **next** month’s **open**.

---

## Calendar

Same as [`reliance_futures_monthly_trade_rules.md`](reliance_futures_monthly_trade_rules.md): signal at month **T** close → orders at month **T+1** open.

---

## Signals

### Regime (NIFTYBEES)

**ON** when:

- `Close > SMA12(Close)`
- 12-month return `Close / Close[12m ago] − 1 > 0`

*(Optional stricter filter: 12-month return ≥ **3%** — fewer entries, similar drawdown on Nifty 50 median.)*

### Stock (e.g. RELIANCE)

**Long allowed** at month **T** close when **all**:

1. Regime **ON**
2. `Close(T) > SMA12(Close)` on the stock
3. `Close(T) / Close(T−3m) − 1 > 0` (3-month momentum)

### Position state

- **Enter** at **T+1 open** when the three conditions flip **on** (from flat).
- **Hold** while all three remain true at each subsequent month-end.
- **Exit** at **next open** after the **first** month-end where **any** condition fails.

No pyramiding; one position at a time.

---

## RELIANCE futures (sanity table)

| Rule | Median-style single name | Typical max DD (cash proxy) |
|------|-------------------------:|----------------------------:|
| Regime + SMA12 | ~25% CAGR | ~−18% |
| **Regime + SMA12 + 3m mom > 0** | **~26% CAGR** | **~−14%** |

Futures amplify returns and drawdowns; add roll/friction mentally.

---

## Portfolio alternative (lower DD book)

If you run **5-name rotation** instead of one future, use code preset `REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS` in `regime_momentum_portfolio.py`:

- Vol target **14%**, soft DD fuse at **−8%** (halve gross), hard fuse at **−10%**
- Full-sample on Nifty 50 MCP panel: **~19% CAGR**, **~−14.5% max DD** (vs default engine ~22% / ~−19%)

Reproduce: `python scripts/compare_improved_regime_strategies.py`

---

## When **not** to use

- **Regime OFF** (e.g. late 2025 snapshot): rule is **flat** across Nifty 50 — do not force trades.
- Names with **< 36 months** history (e.g. recent listings): skip or use cash-only regime.

Past backtests are not forecasts.