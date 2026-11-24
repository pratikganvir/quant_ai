# Quant

Quantitative research and backtesting framework for Indian equities.

## Architecture

- `src/quant/` - reusable research and backtesting code
- `strategies/` - trading strategies
- `notebooks/` - exploratory research
- `experiments/` - experiment configurations
- `tests/` - automated tests
- `results/` - generated research results

## Data

Large historical market datasets live on the VM, not in git.

Default location:

`/home/pratik/data/quant_ai`

Expected layout (same as the old Drive `quant/data` tree):

- `parquet/` — stock OHLCV Parquet files
- `indices/nifty50/NIFTY50.parquet`
- `results/` — research outputs

Override with `QUANT_DATA_DIR` if needed. `scripts/setup_ubuntu.sh` exports that into the Jupyter systemd service and symlinks `data/` in the repo to this folder.

### Upstox (recommended daily source)

Put your Upstox **access token** in `.env` as `UPSTOX_ACCESS_TOKEN` or `UPSTOX_API_KEY` (JWT from the developer console). Sync is **cache-first**:

| Layer | Path | Purpose |
|-------|------|---------|
| Daily Parquet | `parquet/YYYY/nse_cm_YYYYMMDD.parquet` | Same layout as before; notebooks unchanged |
| NSE instruments | `raw/upstox/instruments/nse_eq_map.json.gz` | `trading_symbol` → `instrument_key` |
| Per-symbol history | `raw/upstox/symbols/nse/{SYMBOL}.parquet` | Full daily OHLCV for one symbol |
| API response cache | `raw/upstox/api/day/*.json.gz` | Historical candle JSON (365d chunks) |
| Manifest | `metadata/upstox_symbol_manifest.csv` | Last sync per symbol |

```bash
# All NSE EQ symbols (slow first run — then cached)
PYTHONPATH=src python scripts/sync_upstox_nse_daily.py --start 2010-01-01

# Extend through today (reuses symbol + API gzip cache)
PYTHONPATH=src python scripts/sync_upstox_nse_daily.py --incremental

# Single names first (e.g. TV parity)
PYTHONPATH=src python scripts/sync_upstox_nse_daily.py --symbols DALBHARAT,RELIANCE --start 2015-01-01

# Rebuild nse_cm_* from disk only (no HTTP)
PYTHONPATH=src python scripts/sync_upstox_nse_daily.py --materialize-only --start 2015-01-01
```

Upstox daily candles return at most **~1 year per API call**; the client walks year-sized chunks and merges locally.

**Rate limits** (standard APIs): 50/s, 500/min, **2000/30min**. The client enforces conservative defaults (~8/s, 90/min, 1800/30min) plus **429 retries** with `Retry-After`. Tune in `.env` if needed:

```bash
UPSTOX_RATE_PER_SECOND=8
UPSTOX_RATE_PER_MINUTE=90
UPSTOX_RATE_PER_30MIN=1800
UPSTOX_MIN_INTERVAL_S=1.0
```

Large first-time universe syncs can take multiple sessions; cached gzip chunks are not re-fetched.

Set `QUANT_DATA_SYNC_SOURCE=tejhq` if you still want TejHQ scripts by default on the VM.

### TejHQ (optional source)

Put `TEJHQ_API_KEY` in the repo `.env` (gitignored). The sync client **stores everything locally** so reruns do not hit the API unless you ask it to:

| Layer | Path | Purpose |
|-------|------|---------|
| Daily Parquet | `parquet/YYYY/nse_cm_YYYYMMDD.parquet` | Same layout as bhavcopy sync; skipped when valid |
| Raw API cache | `raw/tejhq/snapshots/nse/YYYY/snapshot_YYYYMMDD.json.gz` | Full snapshot JSON; rebuild Parquet without API |
| Per-symbol cache | `raw/tejhq/{ohlcv,delivery,adjusted}/nse/*.json.gz` | Symbol time-series calls |
| Manifest | `metadata/tejhq_sync_manifest.csv` | Last sync status per date |

Fill **all missing** weekdays (skips known holidays; uses on-disk snapshot cache before HTTP):

```bash
.venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps
# preview days + estimated snapshot API calls (one per day without cache):
.venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps --dry-run
```

TejHQ has no multi-date snapshot endpoint; `from`/`to` applies to per-symbol `ohlcv`, `delivery`, and `adjusted` only.

Optional bounds:

```bash
.venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps --start 2015-01-01 --end 2026-11-13
```

Incremental (only after the **newest** `nse_cm_*.parquet` filename — can skip interior gaps):

```bash
.venv/bin/python scripts/sync_tejhq_nse_daily.py --incremental
```

Backfill a range (uses cache + existing Parquet when present):

```bash
PYTHONPATH=src python scripts/sync_tejhq_nse_daily.py --start 2020-01-01 --end 2020-12-31
```

Force refresh from API: add `--refresh-cache` (and `--overwrite` to replace Parquet).

## Research workflow

1. Develop strategy/code locally in VS Code.
2. Run unit tests locally.
3. Push code to GitHub.
4. Pull and restart on the VM with `scripts/update_and_restart.sh`.
5. Run notebooks in Jupyter at port 8888 against `/home/pratik/data/quant_ai`.