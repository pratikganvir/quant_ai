from pathlib import Path
import os


PROJECT_ROOT = Path(
    os.getenv(
        "QUANT_PROJECT_ROOT",
        "/content/drive/MyDrive/quant"
    )
)

DATA_DIR = PROJECT_ROOT / "data"

RAW_DATA_DIR = DATA_DIR / "raw"

PARQUET_DATA_DIR = DATA_DIR / "parquet"

METADATA_DIR = DATA_DIR / "metadata"

RESULTS_DIR = PROJECT_ROOT / "results"

BACKTEST_RESULTS_DIR = RESULTS_DIR / "backtests"

MONTE_CARLO_RESULTS_DIR = RESULTS_DIR / "monte_carlo"

WALK_FORWARD_RESULTS_DIR = RESULTS_DIR / "walk_forward"

REPORTS_DIR = RESULTS_DIR / "reports"