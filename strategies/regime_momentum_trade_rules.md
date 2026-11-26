# Regime Momentum Portfolio — Trade Rules (human execution)

**Engine version:** `1.5` · **Code:** `src/quant/research/regime_momentum_portfolio.py`  
**Style:** NSE **monthly** bars · one **shared portfolio** · rebalance **once per month** at the **open**

Single-name / futures rules (not this book): see [`regime_dual_mom_trade_rules.md`](regime_dual_mom_trade_rules.md) and [`reliance_futures_monthly_trade_rules.md`](reliance_futures_monthly_trade_rules.md).

---

## What you are doing (one sentence)

After each month closes, if the **Nifty regime is on** and your **book drawdown fuse** allows risk, hold **5 liquid 12–1 momentum leaders** with **inverse-vol weights** and **vol-scaled gross exposure**; otherwise hold **cash** (or accrue idle cash yield in the model).

---

## Calendar (non‑negotiable)

| Step | When | Uses prices from |
|------|------|------------------|
| **Signals** | After the **last trading day** of month *T* | All **month-end closes** through *T* |
| **Orders** | **Open** of the first session of month *T+1* | *T+1* **open** |
| **Risk / DD mark** | Month *T* close equity (cash + MTM positions) | For fuse and gross sizing |
| **Hold** | Until *T+2* open (full rebalance) or risk-off this month |

Do **not** use month *T+1* close to choose names you buy at *T+1* open.

---

## Instruments & data

| Item | Default |
|------|---------|
| **Regime benchmark** | `NIFTYBEES` (Nifty 50 ETF) |
| **Universe** | Liquid NSE equities; default backtest uses **top 200 by median monthly volume** (see below) |
| **Nifty 50 sleeve** | Optional: restrict to [`universe/nifty50_eq_symbols.txt`](../universe/nifty50_eq_symbols.txt) (50 names) |
| **History** | ≥ **36 months** of monthly OHLCV per name |
| **Data source** | MCP `/api/monthly/{symbol}` or local `QUANT_DATA_DIR` panel |

You need **Close** (signals), **Open** (orders), **Volume** (liquidity screen). Never hold `NIFTYBEES` as a momentum pick — regime only.

---

## Pick a profile (live book)

Choose **one** profile and stick to it for at least a year before tuning.

### A — Growth (default)

Code: `DEFAULT_REGIME_MOMENTUM_PARAMS` · Nifty 50 book backtest **~22% CAGR**, **~−19%** max DD (not a forecast).

| Setting | Value |
|---------|------:|
| Top names | 5 |
| 12–1 momentum | 12 / skip 1 |
| Bench filter | Close ≥ SMA₁₂, 12m return ≥ **0%** |
| Vol target (annual) | **18%** |
| Gross cap | **100%** of equity (cash only, no margin) |
| Leverage clip | 0.25 – 1.25 on vol scaler |
| Max weight / name | **22%** |
| Hard DD fuse | **−12%** from peak equity |
| Soft DD fuse | **off** (0) |

### B — Balanced (recommended if DD matters)

Code: `REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS` · Nifty 50 **~19.5% CAGR**, **~−14.5%** max DD.

| Setting | Value |
|---------|------:|
| Vol target | **14%** |
| Gross cap | **95%** |
| Max weight / name | **20%** |
| Hard DD fuse | **−10%** |
| Soft DD fuse | **−8%** → multiply this month’s gross by **0.5** |

### C — Fixed conservative (manual fixed sizing)

No vol scaler: fixed **equal weights** and fixed **gross %** of equity. Approximates backtest **~12.5% CAGR**, **~−10.5%** max DD with tight fuse.

| Setting | Value |
|---------|------:|
| Top names | 5 |
| Weights | **Equal** (20% of **deployed** sleeve each) |
| Gross deployment | **50%** of month-*T* equity (do **not** vol-scale) |
| Hard DD fuse | **−8%** |
| Soft DD fuse | **−5%** → deploy **20%** of equity only (¼ of normal) |

### D — Avoid for most books: 3× margin

`REGIME_MOMENTUM_3X_PARAMS` — historical Nifty 50 run had **lower CAGR** and **~−33%** DD vs 1×. Use only if you run a dedicated margin risk sheet.

---

## Monthly checklist (human)

Run **once** after month *T* closes; place orders for month *T+1* **open**.

### 1 — Book equity & drawdown

1. **Equity** = cash + MTM of all positions at **month *T* close**.
2. **Peak** = max of all prior month-end equities (high watermark).
3. **DD %** = `(equity / peak − 1) × 100`.

**Fuses (use your profile’s numbers):**

- If **DD ≤ hard fuse** (e.g. −10% or −12%) → **Section 5 (risk-off)** only; skip picks.
- Else if **DD ≤ soft fuse** (balanced: −8%) → still do regime + picks, but **halve gross deployment** (× 0.5).
- Else → full sizing per profile.

### 2 — Regime (NIFTYBEES)

On month *T* close:

1. **SMA₁₂** = mean of last 12 month-end closes.
2. **R₁₂** = `Close(T) / Close(T−12) − 1`.
3. **Regime ON** if: fuse allows **and** `Close ≥ SMA₁₂` **and** `R₁₂ ≥` bench floor (0% default; 3% in aggressive playbooks).

If **OFF** → **Section 5**.

### 3 — Universe (liquidity)

Among symbols with data, as of *T*:

1. Exclude benchmark and any `universe_exclude_symbols`.
2. Drop if close **< ₹20**.
3. **Median volume** = median of monthly volume over months *T−11 … T* (12m).
4. Keep **top N** by median volume (default **N = 200**; Nifty-only: use 50-file list and skip step 4 or set N = 50).

### 4 — Picks & weights (risk ON)

#### 4a — 12–1 momentum rank

For each universe name:

```
mom = Close(T − 1 month) / Close(T − 12 months) − 1
```

Rank **descending**. Take **top 5**. (Optional: drop names with `mom < 0` if you use dual-momentum on names.)

#### 4b — Weights

**Growth / balanced (inverse vol):**

1. For each pick, stdev of **monthly** returns over *T−11…T* × √12 → **σ**.
2. Raw weight ∝ `1 / max(σ, 8%)`.
3. Cap each at **20% or 22%** (profile), renormalize to sum to 1.

**Fixed conservative:**

- Weights = **20%** each of the **deployed** sleeve (equal).

#### 4c — Gross deployment

**Growth / balanced:**

1. Equal-weight the 5 picks’ monthly returns over *T−11…T* → portfolio vol (annualized).
2. **Scaler** = clip(`target_vol / portfolio_vol`, min_lev, max_lev).
3. **Gross** = min(scaler, **gross_cap**) × equity at *T* close.
4. Apply **soft fuse** scale (× 0.5) if applicable.

**Fixed conservative:**

- **Gross** = **50%** × equity at *T* close (× 0.25 if soft fuse on fixed profile).

#### 4d — Orders at month *T+1* open

1. **Sell all** positions at open (full rebalance).
2. For each pick: **notional** = gross × weight.
3. **Buy** at open: shares = notional / open (round to lot size on NSE).
4. **Cash** = equity − sum(notionals) (minus costs in live).

Hold until next month’s open rebalance (unless you exited via fuse).

### 5 — Risk-off

At *T+1* **open**:

1. Flat all stocks.
2. Stay in cash until **next** month: hard fuse no longer binding (new peak if equity recovers) **and** regime ON.

Model uses ~**6% p.a.** on idle cash (optional in live).

---

## Worked example (balanced, ₹1,00,000 book)

*Illustrative math only — use your own month-end prints.*

| Step | Example |
|------|---------|
| Equity *T* close | ₹1,00,000 |
| Peak | ₹1,08,000 |
| DD | −7.4% → soft fuse **not** hit (threshold −8%) |
| Regime | NIFTYBEES above SMA₁₂, 12m return +5% → **ON** |
| Top 5 | Names A–E by 12–1 mom |
| Weights after inv-vol cap | 18%, 22%, 20%, 22%, 18% |
| Portfolio vol | 22% annual → scaler = 14/22 ≈ 0.64 |
| Gross | min(0.64, 0.95) × 1,00,000 ≈ **₹64,000** |
| Leg A | 0.18 × 64,000 = **₹11,520** at *T+1* open |

Log: date, DD, regime, gross %, tickers, weights, orders, fills.

---

## Sanity checks

- [ ] Signals use **completed** month *T* only.
- [ ] **0 or 5** stocks in the risk-on sleeve (unless a symbol has no valid open — then cash residual).
- [ ] No `NIFTYBEES` in stock sleeve.
- [ ] Sum of weights on **deployed** sleeve = 100% (gross may be &lt; 100% of total equity).
- [ ] Fuse state logged every month.

---

## Live vs backtest

| Topic | Backtest | You |
|-------|----------|-----|
| Open | Monthly bar open | Opening price / MOO |
| Costs | Not modeled | Deduct brokerage + STT; shave **0.1–0.2%**/leg buffer |
| Splits | Vendor-adjusted | Fix share count before signals |
| Partial fills | Full | Review illiquid names even in top 200 |

---

## Quick formulas

**12–1 momentum:**

```
mom_i = Close_i(T − 1m) / Close_i(T − 12m) − 1
```

**Regime (default):**

```
risk_on = (DD > hard_fuse) AND (NIFTYBEES ≥ SMA12) AND (NIFTYBEES_12m ≥ 0)
```

**Inverse-vol weight:**

```
w_i ∝ 1 / max(σ_i, 8%);  w_i = min(w_i, w_max);  normalize
```

---

## Reproduce picks & ₹1L exports

```bash
# Signals / full universe backtest
PYTHONPATH=src python scripts/run_regime_momentum_full_universe.py

# ₹1L book trades + monthly returns (profiles: default | balanced | 3x)
python scripts/simulate_regime_momentum_book_100k.py --profile balanced
python scripts/simulate_regime_momentum_book_100k.py --all-profiles
```

Exports:

| File | Content |
|------|---------|
| `strategies/exports/nifty50_regime_momentum_book_100k_trades.csv` | Growth profile legs |
| `strategies/exports/nifty50_regime_momentum_balanced_100k_trades.csv` | Balanced legs |
| `strategies/exports/nifty50_regime_momentum_book_100k_monthly_returns.csv` | `Jan 2006` style book returns |

Month-end scan (regime + SMA12, not this engine):

```bash
python scripts/scan_regime_sma12_monthly.py --mcp https://mcp.marketsenseanalytics.com
```

---

## TradingView

Monthly chart + overlay: [`regime_momentum_tradingview_v5.pine`](regime_momentum_tradingview_v5.pine)  
Set universe symbol inputs to your Nifty 50 list; regime sleeve is NIFTYBEES long/flat proxy.

---

## Related docs

| Doc | Use |
|-----|-----|
| [`month_hold_trade_rules.md`](month_hold_trade_rules.md) | Force **≤1 month** hold per leg |
| [`target_2pct_monthly_playbook.md`](target_2pct_monthly_playbook.md) | Aggressive params — validate first |
| [`regime_dual_mom_trade_rules.md`](regime_dual_mom_trade_rules.md) | Single-stock / futures |
| [`regime_turn_trade_rules.md`](regime_turn_trade_rules.md) | **Early** tide entry (before full regime) |

---

## Backtest context (not a forecast)

| Profile | Nifty 50 panel (approx.) |
|---------|--------------------------|
| Growth | ~22% CAGR, ~−19% max DD |
| Balanced | ~19.5% CAGR, ~−14.5% max DD |
| Fixed 50% + tight fuse | ~12.5% CAGR, ~−10.5% max DD |
| 3× margin | ~15% CAGR, ~−33% max DD |

Past performance ≠ future results. Prefer **drawdown control** and **regime filter** over leverage for this rule set.