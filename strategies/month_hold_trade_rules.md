# Month-Hold Portfolio — Trade Rules (human execution)

**Engine version:** `1.1` · **Code:** `src/quant/research/month_hold_portfolio.py`  
**Style:** NSE **monthly** bars · **shared book** (e.g. ₹1,00,000) · **up to 5 names** · each leg held **≤ 1 calendar month**

Every position opened at **month T+1 open** is closed at the **next** month’s open (full rebalance). You never carry a stock into a third calendar month in the same leg.

---

## What you are doing (one sentence)

If the benchmark regime is **on**, hold **5 liquid momentum leaders** for **one month** with inverse-vol weights and vol-scaled exposure; otherwise hold **cash**. Repeat every month.

---

## Calendar (critical)

| Step | When | Uses prices from |
|------|------|------------------|
| **Measure signals** | After the **last trading day** of month *T* (month-end close) | All closes through month *T* |
| **Trade** | **Open** of the first trading session of month *T+1* | Month *T+1* **open** |
| **Maximum hold** | **Exit** at the open of month *T+2* (or sooner on risk-off / fuse) | One month per round trip |

Do **not** use month *T+1* close to pick names you buy at *T+1* open; signals are fixed from month *T*.

---

## Instruments & data

| Item | Default |
|------|---------|
| **Regime benchmark** | `NIFTYBEES` (Nifty 50 ETF) |
| **Stock universe** | NSE names with **≥ 36 months** monthly history |
| **Bar type** | **Monthly** OHLCV (e.g. MCP `https://mcp.marketsenseanalytics.com/api/monthly/{symbol}`) |

You need month-end **Close** (signals), month-start **Open** (orders), and **Volume** (liquidity screen).

---

## Profiles (pick one)

### Growth (default)

Walk-forward stable on a broad NSE panel; backtests ~**19.4% CAGR**, ~**−18.4%** max drawdown (not a forecast).

| Parameter | Value | Meaning |
|-----------|------:|---------|
| `top_k` | 5 | Names held at once |
| `mom_lookback_months` / `mom_skip_months` | 12 / 1 | Classic **12–1** momentum |
| `bench_sma_months` | 12 | Benchmark trend filter |
| `bench_min_momentum` | **3%** | NIFTYBEES 12m return must be ≥ 3% |
| `vol_lookback_months` | 12 | Vol for weights & targeting |
| `target_vol_annual` | 18% | Book vol target |
| `max_leverage` | 1.25 | Max gross scaler |
| `min_leverage` | 0.25 | Min gross scaler |
| `gross_cap` | 1.0 | Cap gross at 100% of equity |
| `max_name_weight` | 22% | Per-name weight cap (before renormalize) |
| `dd_hard_stop_pct` | 12% | Peak-to-trough fuse → all cash |
| `universe_min_close` | ₹20 | Min signal-day price |
| `universe_top_n_liquidity` | 200 | Top names by median volume |
| `universe_liquidity_lookback_months` | 12 | Volume lookback |
| `cash_yield_annual` | 6% | Idle cash accrual while risk-off (model) |

### Defensive (lower drawdown)

~**17.6% CAGR**, ~**−15.3%** max DD in backtests (same data; not a forecast).

| Override vs growth | Value |
|--------------------|------:|
| `mom_lookback_months` | **9** |
| `target_vol_annual` | **16%** |
| `gross_cap` | **0.95** |
| `max_leverage` | **1.15** |

All other growth defaults unchanged.

---

## Monthly checklist

Run **once** after month *T* closes; execute at month *T+1* open.

### A. Portfolio health (drawdown fuse)

1. **Equity** at month *T* close = cash + marked positions.
2. Update **peak equity** (high watermark).
3. **Drawdown %** = `(equity / peak − 1) × 100`.
4. If drawdown **≤ −12%** → **Section D** (risk-off). Skip B–C this month.

### B. Regime filter (benchmark)

On **NIFTYBEES** through month *T* close:

1. **SMA₁₂** = mean of last 12 month-end closes.
2. **Bench 12m return** = `Close(T) / Close(T−12) − 1`.
3. **Risk ON** only if **all**:
   - Fuse **not** tripped (Section A),
   - `Close_NIFTYBEES(T) ≥ SMA₁₂`,
   - `Bench 12m return ≥ 3%` (growth; defensive uses same 3% floor).

Otherwise → **Section D**.

### C. Rebalance (risk on)

#### C1. Eligible universe

As of month *T* close:

1. Exclude `NIFTYBEES` and any `universe_exclude_symbols`.
2. Drop names with close **< ₹20**.
3. **Median volume** over months *T−11…T* (12 months).
4. Keep **top 200** by median volume.

#### C2. Momentum rank (12–1 growth / 9–1 defensive)

For each eligible symbol:

- **Growth:** `mom = Close(T−1) / Close(T−12) − 1`  
- **Defensive:** `mom = Close(T−1) / Close(T−9) − 1`  

Rank **descending**. Take **top 5** (`top_k`).

#### C3. Weights (inverse volatility)

On months *T−11…T*, monthly close-to-close returns per pick:

1. **σ₁₂_annual** = stdev(monthly returns) × √12; if bad, use 25%.
2. Raw = `1 / max(σ, 8%)`.
3. Weight = cap each at **22%**, renormalize to sum **1** across the five names.

#### C4. Gross exposure (vol targeting)

1. **Portfolio vol** = stdev of **equal-weight** monthly returns of the 5 picks × √12 (12m window). Fallback 20%.
2. **Leverage** = clip(`target_vol / portfolio_vol`, `min_leverage`, `max_leverage`)  
   - Growth: target **18%**, clip to **[0.25, 1.25]**  
   - Defensive: target **16%**, clip to **[0.25, 1.15]**
3. **Gross fraction** = `min(leverage, gross_cap)`.
4. **Deploy budget** = `gross fraction × equity at month T close`.

#### C5. Orders (month *T+1* open)

1. **Sell** every holding at *T+1* **open** (always full exit of last month’s sleeve).
2. **Buy** each pick: `notional_i = deploy_budget × weight_i` at *T+1* **open**  
   `shares_i = notional_i / open_price_i` (whole shares in live if required).
3. Allocate across names **proportionally** so the five legs use the full deploy budget (not “first names use all cash”).
4. **Hold** until next month’s open rebalance (~**1 month**).

### D. Risk-off (regime off or fuse)

At **month *T+1* open**:

1. **Sell all** into cash.
2. Optional model: multiply cash by `(1 + 6%/12)` for the month while flat.

Remain in cash until **both** fuse is no longer binding (new equity highs reduce DD) **and** regime (Section B) is ON again.

---

## Example (growth, ₹1,00,000 start)

- Month-end *T*: regime ON, picks A–E, weights 22% / 22% / 20% / 18% / 18%, gross 85% → deploy **₹85,000**.
- *T+1* open: sell prior names; buy A–E for **₹18,700 / ₹18,700 / …** totaling **₹85,000**.
- *T+2* open: sell A–E regardless of P&amp;L; repeat signal process for the new month.

Concurrent positions: **exactly 5** when risk-on and all opens exist (otherwise residual cash).

---

## Execution notes (live vs backtest)

| Topic | Backtest | Live |
|-------|----------|------|
| **Rebalance price** | Monthly bar **open** | MOO / opening auction on first session of month |
| **Costs / STT / GST** | Not modeled | Expect ~0.1–0.2% round-trip drag; size slightly under `gross_cap` |
| **Illiquids** | Assumes fill at open | Check spread; skip names you cannot buy at open |
| **Corporate actions** | Data-dependent | Adjust quantities before signals |
| **Margin / leverage** | Scales **deploy budget** only; `gross_cap` ≤ 1.0 | Do not exceed your broker margin policy |

---

## Quick reference formulas

**12–1 momentum (growth):**

```
mom_i = Close_i(T - 1) / Close_i(T - 12) - 1
```

**Regime (growth & defensive):**

```
risk_on = (DD > -12%) AND (NIFTYBEES ≥ SMA12) AND (NIFTYBEES_12m_ret ≥ 3%)
```

**Inverse-vol weight:**

```
w_i ∝ 1 / max(σ_i, 8%); cap w_i at 22%; normalize
```

---

## Sanity checks each month

- [ ] Signals use **completed** month *T* only.
- [ ] **0 or 5** stocks when risk-on (not a partial sleeve unless missing quotes).
- [ ] No `NIFTYBEES` in the stock book.
- [ ] Every open leg **exits** at the **next** month open (≤ 1 month hold).
- [ ] Log: regime, fuse, picks, weights, gross %, notionals, fills.

---

## Reproduce picks & export trades (automation)

```bash
# Growth profile (default) — updates data/cache/month_hold_trades.csv
PYTHONPATH=src .venv/bin/python scripts/run_month_hold_full_universe.py --capital 100000

# Defensive profile
PYTHONPATH=src .venv/bin/python scripts/run_month_hold_full_universe.py --profile defensive --capital 100000
```

CSV columns: `Symbol`, `EntryDate`, `EntryPrice`, `ExitDate`, `ExitPrice`, `Shares`, notionals, `ProfitLossINR`, `ProfitLossPercent`, `InitialCapitalINR`.

Parameter search: `scripts/optimize_month_hold.py`.

---

## Related docs

- Longer-horizon sibling: [`regime_momentum_trade_rules.md`](regime_momentum_trade_rules.md) (same engine family; v1.4 adds optional soft de-risk knobs).
- TradingView overlay (regime + monthly chart): [`regime_momentum_tradingview_v5.pine`](regime_momentum_tradingview_v5.pine) — use **1 month** chart; logic aligns with regime sleeve, not per-leg CSV export.

---

## Disclaimer

Backtest statistics (e.g. ~19.4% / −18.4% growth, ~17.6% / −15.3% defensive) describe **historical simulation** on cached MCP monthly data. They are **not** guarantees. Walk-forward stability was checked in-repo; live slippage, costs, and regime shifts can differ materially.