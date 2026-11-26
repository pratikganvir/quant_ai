# Conviction Peak — Trade Rules (monthly) v1.0

**Engine:** `conviction_peak_portfolio.py`  
**Idea:** Same multibagger **launch** fingerprint as Launch Base (moderate), but **exits** sized from `analyze_multibagger_drawdowns.py` — multibaggers median **~−45%** off peak before 2×, then givebacks tighten.

```bash
python scripts/simulate_conviction_peak_book_100k.py
```

---

## Why this exists

Launch Base v1.3 used **−20%** leg caps and tight peak trails. The drawdown study shows **~95%** of 2× paths spend **≥20%** below their running peak and **~24%** dip **≥20%** below entry on monthly closes. Tight stops fight the path distribution. Conviction Peak **widens** peak tolerance and catastrophe stop to **stay on** multibagger-shaped moves.

| Calibration | Source (2× episodes) | Rule |
|-------------|---------------------:|------|
| Peak trail while building | ~median −45% giveback | **44%** trail from running peak until **2×** |
| Peak trail after 2× | ~p90 −24% giveback | **24%** trail once peak ≥ **2×** entry |
| Catastrophe | ~p90 −20% below entry | **−30%** from entry (monthly low/close) |
| Book fuse | risk budget | **−28%** book DD → flatten via `book_fuse` |

No profit-floor exits.

---

## Calendar

Signals at month **T** close · trade at **T+1** open.

---

## Regime

**NIFTYBEES:** close ≥ SMA₁₂ and 12m return ≥ **+2%**.  
Regime off → **no new** buys; open names use stock exits only.

---

## Universe

Top **300** liquid · close ≥ **₹20** · ≥ **36** months history · up to **7** names · **95%** gross.

---

## Entry (convicted launch)

| Filter | v1.0 |
|--------|-----:|
| vs 12m high | −45% to −12% |
| 3m / 6m return | ≥ +5% / +2% |
| 12m return | −15% to +60% |
| Vol ratio 6/24 | ≥ 0.88 |
| Thrust or breakout path | yes |
| Launch score floor | ≥ 0.07 |

---

## Exits (per name)

1. **Book fuse** — portfolio DD ≥ 28% from book peak (monthly signal bar).
2. **Catastrophe** — monthly low or close ≤ **−30%** vs entry.
3. **Peak trail (build)** — close **≥44%** below running peak while peak &lt; 2× entry.
4. **Peak trail (after 2×)** — close **≥24%** below peak once peak ≥ 2× entry.
5. **Underwater trend** — if below SMA₂₄ **2** months and still **&gt;8%** below entry → exit.
6. **Profit trend** — below SMA₂₄ **3** months while in profit → exit.

---

## Files

- Trades: `results/reports/conviction_peak_book_100k_trades.csv`
- Export: `strategies/exports/conviction_peak_book_100k_trades.csv`
- Study: `results/reports/multibagger_episode_drawdown_stats.csv`