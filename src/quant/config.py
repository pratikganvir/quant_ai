from pathlib import Path
import os


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = Path("/home/pratik/data/quant_ai")

PROJECT_ROOT = Path(os.getenv("QUANT_PROJECT_ROOT", str(_REPO_ROOT)))


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

TEJHQ_API_KEY = os.getenv("TEJHQ_API_KEY") or os.getenv("TEJ_API_KEY")
TEJHQ_BASE_URL = os.getenv("TEJHQ_BASE_URL", "https://api.tejhq.dev")


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

TEJHQ_CACHE_DIR = Path(
    os.getenv("TEJHQ_CACHE_DIR", str(RAW_DATA_DIR / "tejhq"))
).expanduser()

TEJHQ_MANIFEST_PATH = METADATA_DIR / "tejhq_sync_manifest.csv"

UPSTOX_CACHE_DIR = Path(
    os.getenv("UPSTOX_CACHE_DIR", str(RAW_DATA_DIR / "upstox"))
).expanduser()

UPSTOX_SYMBOL_MANIFEST_PATH = METADATA_DIR / "upstox_symbol_manifest.csv"

# Preferred daily sync: upstox | tejhq (scripts only; both write nse_cm_*.parquet)
DATA_SYNC_SOURCE = os.getenv("QUANT_DATA_SYNC_SOURCE", "upstox").lower()

INDEX_DATA_DIR = DATA_DIR / "indices"

NIFTY50_PATH = INDEX_DATA_DIR / "nifty50" / "NIFTY50.parquet"

RESULTS_DIR = Path(os.getenv("QUANT_RESULTS_DIR", str(DATA_DIR / "results")))

BACKTEST_RESULTS_DIR = RESULTS_DIR / "backtests"

MONTE_CARLO_RESULTS_DIR = RESULTS_DIR / "monte_carlo"

WALK_FORWARD_RESULTS_DIR = RESULTS_DIR / "walk_forward"

REPORTS_DIR = RESULTS_DIR / "reports"
