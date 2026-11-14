# Cell 1 tail — import tqdm after optional install (prepended to cell 1 by patcher)
SETUP_TQDM_CELL1 = """
if importlib.util.find_spec("tqdm") is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "tqdm"])
from tqdm.auto import tqdm
""".strip()

# Cell 2 — add tqdm to deps block replacement lines
CELL2_TQDM_LINES = """
if importlib.util.find_spec("tqdm") is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "tqdm"])
from tqdm.auto import tqdm
""".strip()

# Cell 4 wrap
CELL4_SOURCE = """
with tqdm(total=1, desc="Phase 0 — config") as pbar:
    CFG = {
        "horizons": [5, 10, 20],
        "h_max": 20,
        "event_thresholds": {"E1": (0.10, 5), "E2": (0.20, 10), "E3": (0.20, 20)},
        "extra_thresholds": [0.40, 0.50, 1.00],
        "sustained_retention": {5: 0.05, 10: 0.10, 20: 0.10},
        "triple_barrier": {"up": 0.20, "down": -0.10},
        "min_price": 20.0,
        "min_history_bars": 60,
        "min_median_turnover_20d": 1_000_000.0,
        "eq_series_only": True,
        "top_n_events": 500,
        "pre_event_lags": [5, 10, 20],
        "event_episode_gap": 10,
        "match_k": 3,
        "random_seed": 42,
        "primary_horizon": 20,
        "primary_mfe_thresh": 0.20,
        "ca_gap_abs_exclude": 0.35,
        "wf_splits": {
            "train_end": 2018,
            "valid_end": 2020,
            "test1_end": 2022,
            "test2_end": 2024,
        },
        "costs": {
            "slippage_bps": 20.0,
            "brokerage_bps": 0.0,
            "stt_bps": 10.0,
            "exchange_bps": 3.25,
            "stamp_bps": 1.5,
            "sebi_bps": 0.0001,
            "gst_rate": 0.18,
        },
        "strategy": {
            "top_k": (10, 20),
            "prob_threshold": 0.25,
            "max_hold": 20,
            "stop_loss": 0.10,
            "max_participation_pct": 0.05,
            "initial_capital": 1_000_000.0,
        },
    }

    FEATURE_FLAGS = {
        "HAS_DELIVERY": False,
        "HAS_TURNOVER": False,
        "HAS_SERIES": False,
        "HAS_PREV_CLOSE": False,
    }

    print(json.dumps(CFG, indent=2))
    pbar.update(1)
""".strip() + "\n"