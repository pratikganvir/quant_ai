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
        # Forward window for MFE measurement (not a "must move within N days" speed rule).
        "primary_horizon": 20,
        "h_max": 20,
        "horizons": [20],
        "primary_mfe_thresh": 2.0,
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
            # None = no calendar exit; hold until stop_loss or missing_bar only.
            "max_hold": None,
            "min_hold_sessions": 1,
            "reentry_cooldown_sessions": 0,
            "stop_loss": 0.10,
            "max_participation_pct": 0.05,
            "initial_capital": 1_000_000.0,
            "max_missing_sessions": 10,
        },
        # Drop symbols with patchy vendor history (see nse_data_inspection coverage).
        "symbol_continuity": {
            "enabled": True,
            "min_coverage_ratio": 0.95,
            "max_internal_gap_sessions": 20,
        },
        # Resume: skip phases when artifacts + meta fingerprint match. Override:
        #   CFG["rerun_all"] = True  OR  CFG["force_rerun"] = ["panel", "events", ...]
        "rerun_all": False,
        "force_rerun": [],
        "parallel_workers": 0,  # 0 = auto (min(4, cpu_count)); 1 = serial symbols
        "ml": {
            # Walk-forward: train only these on each split (rf is slow on full panel)
            "walk_forward_models": ["lgbm"],
            "benchmark_models": ["logit", "rf"],
            "benchmark_splits": ["valid"],
            "lgbm_n_estimators": 200,
            "rf_n_estimators": 80,
            "train_sample_max": 400_000,
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