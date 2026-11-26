import pandas as pd

from quant.research.launch_base_signals import entry_mask_at, entry_score_at


def test_entry_score_prefers_higher_ret3():
    d = pd.Timestamp("2020-01-01")
    panels = {
        "ret_3": pd.DataFrame({"A": [0.1], "B": [0.05]}, index=[d]),
        "ret_6": pd.DataFrame({"A": [0.05], "B": [0.05]}, index=[d]),
        "ret_12": pd.DataFrame({"A": [0.1], "B": [0.1]}, index=[d]),
        "dist_hi12": pd.DataFrame({"A": [-0.2], "B": [-0.2]}, index=[d]),
        "vol_ratio": pd.DataFrame({"A": [1.0], "B": [1.0]}, index=[d]),
        "brk12": pd.DataFrame({"A": [False], "B": [False]}, index=[d]),
        "above_sma12": pd.DataFrame({"A": [True], "B": [True]}, index=[d]),
        "rel_mom_12": pd.DataFrame({"A": [0.1], "B": [0.1]}, index=[d]),
    }
    s = entry_score_at(d, panels, pd.Index(["A", "B"]))
    assert float(s["A"]) > float(s["B"])