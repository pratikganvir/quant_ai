# Target ~2% per month — what is realistic

**2% every calendar month** is not the same as **24% per year**. Compounded,  
**(1.02)^12 − 1 ≈ 26.8% CAGR**. That is an institutional-grade equity return, delivered **with drawdowns** and **many** losing months.

This playbook is for your **book**, not a “always win” promise.

---

## RELIANCE daily (15-day holds) — does **not** hit the target

On MCP daily history (2011→2025):

| Approach | Mean month | Months ≥ 2% | Notes |
|----------|------------|---------------|--------|
| Buy & hold RELIANCE | **~1.17%** | **~41%** | Median month **~0.37%** |
| Inside-day breakout (IDB-15) | ~0.7% trade edge spread | low | ~8% CAGR full sample |
| Vol-targeted 2.5× long RELIANCE (SMA200 filter) | **~1.1%** | **~29%** | Max DD **~−54%** |

**Conclusion:** One large-cap name on daily bars **cannot** sustainably average **2%/month** without leverage—and even **~3×** on RELIANCE still fails to put **most** months above 2%.

Use RELIANCE daily for **tactical** trades, not a **2%/month** wealth engine.

---

## What **can** approach ~2%/month **on average** (with risk)

Use a **monthly portfolio** on a **liquid NSE universe**, not a single stock:

| Engine | Typical full-sample CAGR | ≈ Geometric mean / month | Code |
|--------|-------------------------|---------------------------|------|
| Regime momentum (default) | ~22% | ~**1.66%** | `regime_momentum_portfolio.py` |
| Regime momentum (walk-forward **test** window) | ~27% | ~**2.0%** | `VALIDATED_FULL_SAMPLE["test_cagr_pct"]` |
| Regime momentum **target 2%** profile | *validate on your cache* | target band | `REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS` |
| Month-hold **3×** margin book | *run on VM* | higher, **much** more tail risk | `MONTH_HOLD_3X_PARAMS` |

**2% every month** will still **not** happen: expect **12–18** months per decade below target even on strong systems.

---

## Recommended setup (practical)

### 1. Book-level engine (preferred)

1. Sync **monthly** + **daily** universe on the VM (`sync_upstox_nse_daily` / institutional notebook Phase 0).
2. Backtest **regime momentum** with aggressive params:

```python
from quant.research.regime_momentum_portfolio import (
    REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS,
    backtest_regime_momentum_portfolio,
)
```

Or run:

```bash
PYTHONPATH=src python scripts/validate_regime_momentum.py
PYTHONPATH=src python scripts/run_regime_momentum_full_universe.py
```

3. In the notebook, set `strategy_engine: regime_momentum` and pass `REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS` as overrides after you confirm walk-forward on **your** panel.

### 2. If you insist on **more** return (margin)

- Profile: **`month_hold` / `3x`** (`MONTH_HOLD_3X_PARAMS`).
- Requires margin account, borrow cost, and tolerance for **−12%** (or tighter conservative 3×) book fuses.
- Rules: [`month_hold_trade_rules.md`](month_hold_trade_rules.md).

### 3. How to **measure** “2% this month”

- Mark equity at **month-end** (same as backtest).
- Monthly return = `(EOM equity / prior EOM equity) − 1`.
- Track a **12-month rolling average**, not one bad month in isolation.

---

## Honest expectations

| Expectation | Realistic? |
|-------------|------------|
| **2% every month** on RELIANCE daily | **No** (history + simple leverage) |
| **~2%/month average** over years on a **diversified momentum book** | **Sometimes** in strong bulls; **not guaranteed** |
| **Lower DD** than ~2%/month target | Use **defensive** month-hold / lower `target_vol_annual` — you trade return for stability |

For discretionary RELIANCE only: keep **IDB-15** or similar as a **sidebook** (fraction of capital), and run the **monthly portfolio** for the **2%/month ambition**.