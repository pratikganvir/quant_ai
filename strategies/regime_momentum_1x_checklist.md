# Regime Momentum 1× — Monthly Checklist (human)

**Profile:** Growth / default (`DEFAULT_REGIME_MOMENTUM_PARAMS`)  
**Target (backtest context, not a forecast):** ~**22% CAGR**, ~**−19%** max DD on Nifty 50 panel  
**Book:** One pool of cash · **5 stocks** · **100% max gross** (cash only, no margin)  
**Full rules:** [`regime_momentum_trade_rules.md`](regime_momentum_trade_rules.md)

---

## When

| Do this | Timing |
|---------|--------|
| Measure everything | Last close of month **T** |
| Sell old + buy new | **Open** of first session, month **T+1** |

---

## Numbers to keep (this profile)

| Rule | Value |
|------|------:|
| Stocks held | **5** |
| Momentum | **12–1** (12m return, skip last month) |
| Regime | `NIFTYBEES` Close ≥ **SMA₁₂** and 12m return ≥ **0%** |
| Vol target | **18%** annual (scales gross) |
| Gross cap | **100%** of equity |
| Max per name | **22%** of deployed sleeve |
| Hard stop | Flat if drawdown ≤ **−12%** from peak equity |

Soft drawdown scale: **off** (1× default).

---

## Month-end worksheet (month **T**)

### Step 1 — Drawdown fuse

- [ ] **Equity** = cash + MTM positions at **T** close: ₹________  
- [ ] **Peak equity** (high watermark): ₹________  
- [ ] **DD %** = (equity / peak − 1) × 100: ______%  

If DD **≤ −12%** → **go flat** at **T+1** open (Step 5 only). **Stop.**

---

### Step 2 — Regime (NIFTYBEES)

- [ ] **SMA₁₂** at **T** close: ______  
- [ ] **Close** at **T**: ______  
- [ ] **12m return** = Close(T) / Close(T−12m) − 1: ______%  

**Regime ON** only if close ≥ SMA₁₂ **and** 12m return ≥ **0%**.

If **OFF** → **go flat** at **T+1** open (Step 5). **Stop.**

---

### Step 3 — Universe (liquidity)

On **T** close, among names with monthly data:

- [ ] Exclude `NIFTYBEES` and illiquid screens  
- [ ] Drop if price **< ₹20**  
- [ ] **Median volume** over last **12** months  
- [ ] Keep **top 200** by median volume  

*(Optional: use only Nifty 50 list in `universe/nifty50_eq_symbols.txt`.)*

---

### Step 4 — Rebalance (regime ON, fuse OK)

#### Picks

For each universe symbol:

```
mom = Close(T − 1m) / Close(T − 12m) − 1
```

- [ ] Rank **highest mom** → select **top 5** tickers:

| # | Symbol | 12–1 mom % |
|---|--------|------------|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |

#### Weights (inverse vol)

Last **12** monthly returns per pick → stdev × √12 = **σ**.

- Raw weight ∝ **1 / max(σ, 8%)**  
- Cap each at **22%**, renormalize → weights sum to **100%** of deployed sleeve  

| Symbol | σ (approx) | Weight % |
|--------|------------|----------|
| | | |
| | | |
| | | |
| | | |
| | | |

#### Gross size

- Equal-weight the **5** picks’ monthly returns over last 12 months → **portfolio vol** (× √12).  
- **Scaler** = clip(**18%** / portfolio vol, **0.25**, **1.25**)  
- **Gross deploy** = min(scaler, **1.0**) × **equity at T close** = ₹________  

#### Orders at **T+1 open**

- [ ] **Sell all** existing positions at open  
- [ ] For each pick: **notional** = gross × weight → buy at open (round to NSE lots)  

| Symbol | Weight | Notional ₹ | Open | Shares/lots |
|--------|--------|------------|------|-------------|
| | | | | |
| | | | | |
| | | | | |
| | | | | |
| | | | | |

- [ ] Remaining **cash** = equity − sum bought (before costs)  

---

### Step 5 — Risk-off (regime OFF or fuse)

At **T+1 open**:

- [ ] **Sell all** → **100% cash**  
- [ ] Next month: repeat from Step 1 (fuse clears when equity makes **new peak**)

---

## Every month — sanity

- [ ] Signals used **T** close only (not intraday **T+1**)  
- [ ] **0 or 5** stocks (no ad-hoc 3-stock book)  
- [ ] No `NIFTYBEES` in stock sleeve  
- [ ] Log: DD %, regime, gross %, five names, weights, fills  

---

## Reproduce / export (₹1L backtest)

```bash
python scripts/simulate_regime_momentum_book_100k.py --profile default
```

Trades: `strategies/exports/nifty50_regime_momentum_book_100k_trades.csv`  
Monthly book returns: `strategies/exports/nifty50_regime_momentum_book_100k_monthly_returns.csv`