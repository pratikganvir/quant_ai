import numpy as np
import pandas as pd

from quant.research.explosive_research import trading_calendar_from_panel

from scripts.short_term_5pct_10d_research import (
    attach_forward_labels,
    prepare_rf_sample,
    sample_rows_for_export,
    trades_export_frame,
)


def test_forward_label_10_sessions():
    cal = pd.bdate_range("2024-01-01", periods=30)
    rows = []
    for d in cal:
        rows.append({"Date": d, "Symbol": "AAA", "Close": 100.0, "High": 101, "Low": 99, "Volume": 1e6})
    panel = pd.DataFrame(rows)
    panel.iloc[15, panel.columns.get_loc("Close")] = 100.0
    panel.iloc[25, panel.columns.get_loc("Close")] = 106.0  # +6% over 10 sessions from row 15
    out = attach_forward_labels(
        panel,
        forward_sessions=10,
        min_forward_return=0.05,
        calendar=trading_calendar_from_panel(panel),
    )
    row = out.loc[out["Date"] == cal[15]].iloc[0]
    assert row["horizon_ok_10"]
    assert row["fwd_ret_10"] >= 0.05
    assert row["y_hit_10d"] == 1


def test_prepare_rf_sample_stratified():
    n = 200
    df = pd.DataFrame(
        {
            "ret_1": np.random.randn(n),
            "vol_rvol_20": np.random.uniform(0.5, 2.0, n),
            "y_hit_10d": [1] * 20 + [0] * 180,
        }
    )
    X, y, diag = prepare_rf_sample(df, label_col="y_hit_10d", feature_cols=["ret_1", "vol_rvol_20"], max_rows=100)
    assert y.nunique() == 2
    assert len(X) == 100
    assert diag["hits_train"] >= 1


def test_trades_export_frame():
    df = pd.DataFrame(
        {
            "Symbol": ["AAA", "BBB"],
            "Date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "Close": [100.0, 50.0],
            "exit_date": pd.to_datetime(["2024-01-16", "2024-01-17"]),
            "exit_close": [110.0, 48.0],
            "fwd_ret_10": [0.10, -0.04],
            "horizon_ok_10": [True, True],
            "y_hit_10d": [1, 0],
            "ret_1": [0.01, -0.02],
        }
    )
    out = trades_export_frame(df, forward_sessions=10, label_col="y_hit_10d")
    assert list(out.columns[:6]) == [
        "Symbol",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "fwd_ret",
    ]
    assert len(out) == 2
    assert out.loc[0, "outcome"] == "hit"


def test_sample_rows_for_export_cap():
    df = pd.DataFrame(
        {
            "y_hit_10d": [1] * 50 + [0] * 950,
            "x": np.arange(1000),
        }
    )
    out = sample_rows_for_export(df, label_col="y_hit_10d", max_rows=100, random_seed=0)
    assert len(out) == 100
    assert out["y_hit_10d"].nunique() == 2