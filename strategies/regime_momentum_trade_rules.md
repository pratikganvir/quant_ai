# Regime Momentum Portfolio — Trade Rules (human execution)

**Engine version:** `1.3` · **Code:** `src/quant/research/regime_momentum_portfolio.py`  
**Style:** NSE monthly bars · one **portfolio** book · rebalance **once per month**

This document mirrors the backtest. You can run the same logic in the notebook (`strategies/institutional_accumulation_monthly.ipynb` with `strategy_engine: regime_momentum`) or scripts (`scripts/run_regime_momentum_full_universe.py`).

---

## What you are doing (one sentence)

Each month, if India’s broad market regime is “on,” hold **5 liquid momentum leaders** with **inverse-volatility weights** and **volatility-scaled exposure**; otherwise hold **cash**.

---

## Calendar (critical)

| Step | When | Uses prices from |
|------|------|------------------|
| **Measure signals** | After the **last trading day** of month *T* (month-end close) | All closes through month *T* |
| **Trade** | **Open** of the first trading session of month *T+1* | Month *T+1* **open** (backtest uses monthly bar open) |
| **Mark portfolio** | Month *T+1* close | For reporting and next month’s drawdown check |

Do **not** use month *T+1* close to pick names for month *T+1*; picks are fixed from month *T* data.

---

## Instruments & data

| Item | Default |
|------|---------|
| **Regime benchmark** | `NIFTYBEES` (Nifty 50 ETF) |
| **Stock universe** | NSE names with **≥ 36 months** of monthly history in your data feed |
| **Bar type** | **Monthly** OHLCV (same as MCP `/api/monthly/{symbol}`) |

You need: month-end **Close** (signals), month-start **Open** (orders), and **Volume** (liquidity filter).

---

## Default parameters (v1.3)

| Parameter | Value | Meaning |
|-----------|------:|---------|
| `top_k` | 5 | Number of stocks held |
| `mom_lookback_months` | 12 | Momentum window |
| `mom_skip_months` | 1 | Skip last month (12–1 momentum) |
| `bench_sma_months` | 12 | Benchmark trend filter |
| `bench_min_momentum` | 0% | Min 12m return on benchmark |
| `vol_lookback_months` | 12 | Vol for weights & targeting |
| `target_vol_annual` | 18% | Portfolio vol target |
| `max_leverage` | 1.25 | Max gross scaling |
| `min_leverage` | 0.25 | Min gross scaling |
| `gross_cap` | 1.0 | Max 100% of equity deployed (before cap) |
| `max_name_weight` | 22% | Cap weight per name (pre-renormalize) |
| `dd_hard_stop_pct` | 12% | Peak-to-trough equity fuse |
| `cash_yield_annual` | 6% | Monthly accrual on cash while risk-off |
| `universe_min_close` | ₹20 | Min price at signal date |
| `universe_top_n_liquidity` | 200 | Keep top N by median volume |
| `universe_liquidity_lookback_months` | 12 | Volume lookback |
| `universe_min_median_volume` | 0 | Optional extra volume floor |

Benchmark and picks: **never** hold `NIFTYBEES` as a momentum stock (regime only).

---

## Monthly checklist

Do this **once**, after month *T* closes, orders for month *T+1* open.

### A. Portfolio health (drawdown fuse)

1. Compute **portfolio equity** at month *T* close (cash + marked positions).
2. Update **peak equity** (running high watermark).
3. **Drawdown %** = `(equity / peak - 1) × 100`.
4. If drawdown **≤ −12%** → go to **Section D (risk-off)**. Skip B and C for this month.

### B. Regime filter (benchmark)

Using **NIFTYBEES** closes through month *T*:

1. **SMA₁₂** = average of last 12 month-end closes.
2. **Bench 12m return** = `Close(T) / Close(T−12) − 1`.
3. **Risk ON** only if **all** true:
   - Drawdown fuse **not** triggered (Section A),
   - `Close_NIFTYBEES(T) ≥ SMA₁₂`,
   - `Bench 12m return ≥ 0%` (default).

If **risk OFF** → **Section D**.  
If **risk ON** → continue to **Section C**.

### C. Rebalance (risk on)

#### C1. Eligible universe (liquidity & price)

As of month *T* close, among all symbols with data:

1. Exclude `NIFTYBEES` and any names in `universe_exclude_symbols`.
2. Drop names with month *T* close **< ₹20**.
3. For each remaining name, **median volume** = median of monthly volume over months **T−11 … T** (12 months).
4. Keep the **top 200** names by median volume (rest dropped).

#### C2. Momentum rank (12–1)

For each eligible symbol:

- **Momentum** = `Close(T−1) / Close(T−12) − 1`  
  (i.e. 12-month return **excluding** the most recent month — “skip 1”).

Rank descending. **Select top 5** symbols. Ties: prefer higher momentum; arbitrary tie-break is fine if equal.

#### C3. Position weights (inverse volatility)

Over months **T−11 … T**, compute each pick’s **monthly returns** (close-to-close).

1. **σ₁₂** = stdev of monthly returns × √12 (annualized vol). If missing or zero, use **25%**.
2. **Raw score** = `1 / max(σ₁₂, 8%)`.
3. **Weight** = score / sum(scores), then **cap each name at 22%**, then **renormalize** so weights sum to 1.

#### C4. Gross exposure (vol targeting)

1. Over months **T−11 … T**, build **equal-weight** monthly returns across the 5 picks (average of the five stocks’ monthly returns).
2. **Portfolio vol** = stdev of those monthly returns × √12. If invalid, use **20%**.
3. **Leverage** = clip(`18% / portfolio vol`, 0.25, 1.25).
4. **Gross deployment** = `min(leverage, 1.0) × portfolio equity at month *T* close`.

*(Backtest deploys this fraction of equity; it does not use margin above 100% when `gross_cap` is 1.0.)*

#### C5. Orders (month *T+1* open)

1. **Sell all** current holdings at **month *T+1* open** (full rebalance).
2. For each of the 5 picks, **target notional** = `gross deployment × weight`.
3. **Buy** at **month *T+1* open**: `shares = target notional / open price` (integer shares if you trade physically; backtest uses fractional).
4. **Unused cash** stays in cash (if opens are missing or notional exceeds cash, invest what you can — backtest caps each leg by available cash).

**Hold** until the next month’s open rebalance (or until risk-off / fuse).

### D. Risk-off (regime off or drawdown fuse)

At **month *T+1* open**:

1. **Sell all** positions into cash.
2. Accrue **cash yield**: multiply cash by `(1 + 6%/12)` once for the month (model for idle cash; optional in live brokerage).

Stay in cash until a future month passes **Section A** (fuse cleared — note: fuse is **not** auto-reset in code; it uses peak equity, so new highs reset drawdown) **and** **Section B** (regime ON).

---

## Execution notes (live vs backtest)

| Topic | Backtest | Live |
|-------|----------|------|
| **Prices** | Single monthly Open/Close per symbol | Use **opening auction / first print** on rebalance day |
| **Partial fills** | Full fill at open | Use limit/MOO; review illiquid names in top 200 |
| **Corporate actions** | Depends on your data vendor | Adjust shares for splits/bonus before signals |
| **GST / charges** | Not modeled | Reduce effective `gross_cap` or add slippage buffer |
| **Leverage** | Scales **deployed cash**, capped at 100% with default `gross_cap` | Same intent: size book to vol target without exceeding your margin rules |

---

## Quick reference formulas

**12–1 momentum (symbol *i*):**

```
mom_i = Close_i(T - 1 month) / Close_i(T - 12 months) - 1
```

**Inverse-vol weight (symbol *i* among picks):**

```
w_i ∝ 1 / max(σ_i_12m_annual, 8%)
w_i = min(w_i, 22%) then normalize
```

**Regime:**

```
risk_on = (DD > -12%) AND (NIFTYBEES_close ≥ SMA12) AND (NIFTYBEES_12m_ret ≥ 0)
```

---

## Sanity checks each month

- [ ] Signal date is **last close of completed month**, not today’s intraday.
- [ ] Exactly **0 or 5** stocks (never 1–4 unless a symbol has no open price — then cash residual).
- [ ] No `NIFTYBEES` in the stock sleeve.
- [ ] Weights after cap sum to **100%** of the **deployed** sleeve (not necessarily 100% of total equity if `gross_cap` & vol target imply less).
- [ ] Log: regime on/off, fuse, picks, weights, gross %, trades.

---

## Expected behavior (backtest context, not a forecast)

On a broad NSE panel with universe filter (v1.3), historical simulations showed roughly **~19% CAGR** and **~−18% max drawdown** vs weak results without liquidity filtering. Past performance ≠ future results.

---

## Disable or tune universe filter

| Goal | Override |
|------|----------|
| Wider universe | Raise `universe_top_n_liquidity` or set `0` |
| Stricter liquidity | Raise `universe_top_n_liquidity` floor via `universe_min_median_volume` |
| Allow cheaper stocks | Lower `universe_min_close` |

Pass overrides in notebook `REGIME_PARAMS` or script `params` dict; fingerprints include all default keys.

---

## TradingView (Pine Script v5)

Copy-paste: [`regime_momentum_tradingview_v5.pine`](regime_momentum_tradingview_v5.pine) (Pine **v6**, overlays price chart)

- Chart timeframe **1 month**; paste script in Pine Editor.
- **Teal line** = simulated 5-stock portfolio equity; **Strategy Tester** = regime long/flat on **NIFTYBEES** (proxy sizing).
- Set **Universe 01–20** symbol inputs (Pine requires `input.symbol` — no dynamic CSV loops).
- **Per-stock test:** Mode → **Chart symbol**, open e.g. `NSE:RELIANCE` on **1M**, enable overlay on price chart. Strategy Tester is long only when that symbol is in the monthly top-K vs the 20-slot universe (chart symbol auto-joins ranking if not already in the list).

## Automation hook

To reproduce picks without manual math:

```bash
.venv/bin/python scripts/run_regime_momentum_full_universe.py
```

Outputs trades/equity via the same rules; use MCP monthly cache or your `QUANT_DATA_DIR` parquet panel.