# Regime Turn Portfolio — Trade Rules (human execution)

**Engine version:** `1.0` · **Code:** `src/quant/research/regime_turn_portfolio.py`  
**Sibling (confirmed tide):** [`regime_momentum_trade_rules.md`](regime_momentum_trade_rules.md)

---

## Philosophy

**Regime momentum** waits for proof the tide is in (index above SMA₁₂, 12m return ≥ 0).  
**Regime turn** enters **earlier**—when the benchmark *starts* to shift—then **latches** risk-on for several months to capture the first leg of a recovery.

Trade-off: more time in the market around **inflection points** → higher upside when turns are real, more **whipsaw** when they are not. Use a **wider DD fuse** (default **−15%** hard) only if you accept that risk.

---

## One sentence

When **NIFTYBEES** shows a **fresh upturn** (SMA₁₂ cross, pre-regime thrust, or full regime), hold **5 fast momentum leaders** (6–1 month rank) with vol-sized gross exposure; exit when the turn fails, latch ends, or fuse fires.

---

## Calendar

Same as regime momentum: signals at month **T** close → trade month **T+1** open.

---

## Turn ON (benchmark, month *T* close)

Risk **on** if drawdown fuse allows **and** any of:

### 1. SMA₁₂ cross up

- `Close(T) > SMA₁₂(T)`
- `Close(T−1) ≤ SMA₁₂(T−1)`

### 2. Pre-regime thrust (tide building)

All true:

- `Close(T) > SMA₆(T)` and SMA₆ **rising** vs prior month  
- 3-month index return ≥ **+2%**  
- Still **early**: `Close < SMA₁₂` **or** 12-month return **< 0**

### 3. Full regime (same as momentum)

- `Close ≥ SMA₁₂` and 12-month return ≥ **0**

### 4. Latch

After a fresh trigger (1–3), stay **on** up to **12** more months unless **exit** below.

---

## Turn OFF

At month *T* close, go **flat** next open if **any**:

| Condition | Meaning |
|-----------|---------|
| Hard DD fuse | Equity drawdown ≤ **−15%** from peak (default) |
| SMA₆ break (while latched) | `Close < SMA₆` |
| No trigger and latch exhausted | Counter hit **0** |

Soft fuse (default **−9%** DD): scale gross × **0.55** this rebalance only.

---

## Stock sleeve (when turn ON)

1. **Universe:** Same liquidity filter as regime momentum (top **200** median volume, price ≥ ₹20).  
2. **Rank:** **6–1 momentum** — `Close(T−1) / Close(T−6) − 1`, highest first.  
3. **Filter:** Drop names with 6–1 return **< 0%**.  
4. **Hold 5** names; **inverse-vol weights**; cap **24%** each; vol target **22%**; gross cap **100%**.

---

## Default parameters

| Setting | Value |
|---------|------:|
| `top_k` | 5 |
| Momentum | **6–1** |
| Vol target | 26% |
| Gross cap | 100% |
| Hard / soft DD | −15% / −9% |
| Latch | 12 months |
| 3m bench min (pre-regime) | +2% |

---

## Monthly checklist (short)

1. Mark equity → peak → **DD %** → fuse?  
2. **Turn signal** on NIFTYBEES (cross / thrust / full / latch)?  
3. If **on:** rank 6–1 mom → top 5 → weights → gross → **T+1 open** rebalance  
4. If **off:** flat at **T+1 open**

---

## Backtest & exports (₹1L, Nifty 50 panel)

```bash
python scripts/simulate_regime_turn_book_100k.py --compare
```

| Artifact | Path |
|----------|------|
| Trades | `strategies/exports/nifty50_regime_turn_book_100k_trades.csv` |
| Monthly returns | `strategies/exports/nifty50_regime_turn_book_100k_monthly_returns.csv` |

Compare head-to-head with `simulate_regime_momentum_book_100k.py --profile default`.

---

## Backtest vs confirmed regime (Nifty 50, ₹1L)

| Book | Approx. CAGR | Approx. max DD |
|------|-------------:|---------------:|
| **Regime momentum 1×** | **~22%** | ~−19% |
| **Regime turn** (this) | ~**17%** | ~−21% |

Early entry **does not beat** waiting for full regime on this sample—but you are **in sooner** after inflections. Use turn only if that early exposure matches your mandate.

## Not a forecast

Turn timing is **hard**. Always log which trigger fired (`cross`, `pre_regime`, `full_regime`, `latched`). Past backtest CAGR/DD is illustrative only.