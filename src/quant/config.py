from pathlib import Path
import os


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = Path("/home/pratik/data/quant_ai")

PROJECT_ROOT = Path(os.getenv("QUANT_PROJECT_ROOT", str(_REPO_ROOT)))


def _resolve_data_dir() -> Path:
    configured = Path(os.getenv("QUANT_DATA_DIR", str(_DEFAULT_DATA_DIR))).expanduser()
    for root in (configured, configured / "data"):
        if (root / "parquet").is_dir() or (root / "indices").is_dir():
            return root
    return configured


DATA_DIR = _resolve_data_dir()

RAW_DATA_DIR = DATA_DIR / "raw"

PARQUET_DATA_DIR = (
    DATA_DIR / "parquet" if (DATA_DIR / "parquet").is_dir() else DATA_DIR
)

METADATA_DIR = DATA_DIR / "metadata"

INDEX_DATA_DIR = DATA_DIR / "indices"

NIFTY50_PATH = INDEX_DATA_DIR / "nifty50" / "NIFTY50.parquet"

RESULTS_DIR = Path(os.getenv("QUANT_RESULTS_DIR", str(DATA_DIR / "results")))

BACKTEST_RESULTS_DIR = RESULTS_DIR / "backtests"

MONTE_CARLO_RESULTS_DIR = RESULTS_DIR / "monte_carlo"

WALK_FORWARD_RESULTS_DIR = RESULTS_DIR / "walk_forward"

REPORTS_DIR = RESULTS_DIR / "reports"
