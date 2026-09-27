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

## Research workflow

1. Develop strategy/code locally in VS Code.
2. Run unit tests locally.
3. Push code to GitHub.
4. Pull and restart on the VM with `scripts/update_and_restart.sh`.
5. Run notebooks in Jupyter at port 8888 against `/home/pratik/data/quant_ai`.