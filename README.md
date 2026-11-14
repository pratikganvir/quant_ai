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

### TejHQ (optional source)

Put `TEJHQ_API_KEY` in the repo `.env` (gitignored). The sync client **stores everything locally** so reruns do not hit the API unless you ask it to:

| Layer | Path | Purpose |
|-------|------|---------|
| Daily Parquet | `parquet/YYYY/nse_cm_YYYYMMDD.parquet` | Same layout as bhavcopy sync; skipped when valid |
| Raw API cache | `raw/tejhq/snapshots/nse/YYYY/snapshot_YYYYMMDD.json.gz` | Full snapshot JSON; rebuild Parquet without API |
| Per-symbol cache | `raw/tejhq/{ohlcv,delivery,adjusted}/nse/*.json.gz` | Symbol time-series calls |
| Manifest | `metadata/tejhq_sync_manifest.csv` | Last sync status per date |

Fill **all missing** weekdays (holes before your newest file, through API latest):

```bash
.venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps
# preview count only:
.venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps --dry-run
```

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