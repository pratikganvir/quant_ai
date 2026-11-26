# Launch Base — Trade Rules (monthly) v1.3

**Engine:** `launch_base_portfolio.py`  
**Mode:** **Tighter entries only** — no profit-floor scratch wins.

```bash
python scripts/simulate_launch_base_book_100k.py
```

---

## Constraints (read this first)

| Your target | v1.3 entries-only (full MCP panel) |
|-------------|-------------------------------------|
| Hit ratio **>50%** | **~47%** (not met without profit-floor) |
| Max leg loss **~20%** | **Yes** (−20% cap per leg) |
| Book max DD **~20%** | **Yes** (~−13% in last backtest) |

**Honest trade-off:** On monthly bars across ~1,800 names, we did **not** find a parameter set with **>50% hit ratio**, **≤20% leg loss**, and **no** artificial +3% profit-floor exits. Profit-floor (v1.2) hit >50% but CAGR ~3%. **v1.3** drops that crutch and **tightens entries** instead.

Full note: `src/quant/research/launch_base_constraints.py`

---

## Calendar

Signals at month **T** close · trade at **T+1** open.

---

## Regime (new buys only)

**NIFTYBEES:** close ≥ SMA₁₂ and 12m return ≥ **+5%**.  
Regime off → no **new** slots; open names exit on stock rules.

---

## Universe

Top **300** liquid · close ≥ **₹20** · ≥ **36** months history.

---

## Entry (tight multibagger launch)

**Thrust path (all required):**

| Filter | v1.3 |
|--------|-----:|
| vs 12m high | **−42%** to **−14%** |
| 3m / 6m return | ≥ **+8%** / **+5%** |
| 12m return | **0%** to **+45%** |
| Vol ratio 6/24 | ≥ **0.95** |
| Above SMA₁₂ | Yes |
| Rel 12m vs Nifty | ≥ **+8%** |

**Breakout path:** 12m high break + above SMA₁₂ + rel ≥ **+12%**, 12m ret 0–45%.

**Rank:** launch score ≥ **0.12** · hold **3** names · **80%** gross max.

---

## Exit (stock only)

| Rule | Detail |
|------|--------|
| Leg stop | **−20%** vs entry (month low / close; fill capped) |
| Book fuse | **−12%** equity from peak |
| Loser trend | 1 close below SMA₁₂ while underwater |
| Winner | Peak trail / 2 closes below SMA₂₄ |

**No** profit-floor rule in v1.3.

---

## Latest backtest snapshot (₹1L, not a forecast)

~**8%** CAGR · **49** trades · **6** legs **≥100%** · **~47%** hit ratio · worst leg **−20%**.

Trades: `strategies/exports/launch_base_book_100k_trades.csv`