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

Large historical market datasets are stored separately on Google Drive.

Expected Google Drive location:

`/content/drive/MyDrive/quant/data/`

## Research workflow

1. Develop strategy/code locally in VS Code.
2. Run unit tests locally.
3. Push code to GitHub.
4. Pull code into Google Colab.
5. Mount Google Drive.
6. Run large-scale backtests.
7. Save results to Google Drive.