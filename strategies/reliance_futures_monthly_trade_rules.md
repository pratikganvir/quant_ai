# RELIANCE futures — monthly strategy (recommended)

**Style:** One **near-month RELIANCE future**, rebalance **once per month**  
**Data proxy:** Cash monthly OHLCV (`/api/monthly/RELIANCE`, `/api/monthly/NIFTYBEES`)  
**Not modeled in backtest:** roll cost, basis, span margin, MTM — add ~0.05–0.15% per roll mentally

---

## Best simple rule (from 2005→2025 monthly backtest)

**Regime + trend + 3-month momentum** is the preferred upgrade over plain **Regime + SMA12** (higher median CAGR, shallower drawdowns on Nifty 50). Full wording: [`regime_dual_mom_trade_rules.md`](regime_dual_mom_trade_rules.md).

| Approach | Approx. CAGR | Approx. max DD |
|----------|-------------:|---------------:|
| Always long (cash bars) | ~16% | (worse in bears) |
| Regime only (long / flat) | ~17% | ~−23% |
| Regime + RELIANCE > SMA12 | ~25% | ~−18% |
| **Regime + SMA12 + 3m mom > 0** | **~26%** | **~−14%** |
| Institutional V4.9 (single name) | ~0.5% | ~−52% |

Past performance is not a forecast. Futures leverage **scales P&amp;L and drawdowns**.

---

## One sentence

At **month-end**, go **long RELIANCE futures** only if **India regime is ON** and **RELIANCE is above its 12-month trend**; otherwise **flat** (or minimum margin). **Roll** before expiry; rebalance flags at the **next** month’s open.

---

## Calendar

| Step | When |
|------|------|
| **Signals** | Last trading day of month **T** — use **monthly closes** |
| **Trade** | **Open** of first session of month **T+1** (add / reduce / roll) |
| **Roll** | Typically **3–5 sessions before** current month expiry (your broker calendar) |

Same lookahead discipline as [`regime_momentum_trade_rules.md`](regime_momentum_trade_rules.md).

---

## Signals (defaults)

### 1. Regime (benchmark)

Use **NIFTYBEES** monthly (or Nifty future / Nifty index monthly).

**ON** when **both**:

- `Close(T) > SMA12(Close)`
- `Close(T) / Close(T−12) − 1 > 0` (12-month return positive)

Matches spirit of `bench_sma_months=12`, `bench_min_momentum=0` in regime momentum.

### 2. RELIANCE trend (+ momentum, recommended)

**ON** when **all**:

- `RELIANCE Close(T) > SMA12(RELIANCE Close)`
- `Close(T) / Close(T−3m) − 1 > 0` (3-month momentum positive)

SMA12-only is still valid but tends to **deeper drawdowns** on Nifty 50 medians.

### 3. Position

| Regime | Stock trend ON | Action |
|:------:|:--------------:|--------|
| OFF | — | **Flat** (no net long) |
| ON | OFF | **Flat** |
| ON | ON | **Long** near-month future (target book notional) |
| ON | ON (was OFF last month) | **Enter** long at T+1 open |
| Was ON, now OFF | — | **Exit** at T+1 open |

No pyramiding in the base rule: **one** net direction.

---

## Futures execution (live)

1. **Contract:** Most liquid near-month **RELIANCE** F&amp;O symbol; roll to next month before expiry.
2. **Size:** Map equity to lots (e.g. 1 lot ≈ `lot_size × price` notional). Keep **≤ 2×** notional vs cash equity unless you run a dedicated risk sheet.
3. **Costs:** brokerage + **stamp** + **roll slippage**; budget **~1–2 ticks** per roll.
4. **Stops (optional):** The monthly rule is **flat** on signal; you can add a **catastrophic** stop (e.g. −8% from entry on **monthly close**) if you cannot monitor daily.
5. **Avoid** running the heavy **institutional accumulation** entry stack on **one** name with futures — backtest showed poor payoff vs trend filter on RELIANCE.

---

## MCP checks (month-end)

```bash
curl -s "https://mcp.marketsenseanalytics.com/api/monthly/NIFTYBEES?limit=24"
curl -s "https://mcp.marketsenseanalytics.com/api/monthly/RELIANCE?limit=24"
```

Compute SMA12 on the last 12 **month-end** closes.

## Local sync (test universe, avoid repeat MCP / API)

Symbol list: [`universe/regime_sma12_monthly_symbols.txt`](../universe/regime_sma12_monthly_symbols.txt)

```bash
cd ~/quant_ai
export QUANT_DATA_DIR=/home/pratik/data/quant_ai
chmod +x scripts/sync_regime_sma12_universe.sh

# See missing months only (no HTTP)
./scripts/sync_regime_sma12_universe.sh --plan-only

# One-time monthly pull into raw/upstox/symbols/nse_month/
./scripts/sync_regime_sma12_universe.sh

# Extend through latest month later
./scripts/sync_regime_sma12_universe.sh --incremental
```

Edit the `.txt` file to add/remove names. Data path: `$QUANT_DATA_DIR/raw/upstox/symbols/nse_month/{SYMBOL}.parquet`.

---

## If you want ~2%/month **on the book**

Single-name RELIANCE futures **months** will not print **2% every month**. For that ambition use a **multi-stock monthly book** (regime momentum or month-hold 3×) and treat RELIANCE futures as a **satellite** — see [`target_2pct_monthly_playbook.md`](target_2pct_monthly_playbook.md).

---

## Code alignment

- Regime logic: `src/quant/research/regime_momentum_portfolio.py`
- Monthly execution pattern: `strategies/regime_momentum_trade_rules.md`
- Backtest RELIANCE panel in notebook with `Symbol == "RELIANCE"` only (institutional notebook Phase 4, custom filter).