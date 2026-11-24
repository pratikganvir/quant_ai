import pandas as pd

from quant.research.explosive_research import trading_calendar_from_panel

from scripts.short_term_5pct_10d_research import attach_forward_labels


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