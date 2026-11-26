"""Conviction Peak exit rules."""

from quant.research.conviction_peak_exits import conviction_peak_exit


def test_peak_trail_build_uses_wide_band():
    p = {}
    reason, _ = conviction_peak_exit(
        close=45.0,
        low=44.0,
        entry_px=50.0,
        peak_close=90.0,
        months_below_sma24=0,
        in_profit=False,
        p=p,
    )
    assert reason == "peak_trail_build"


def test_peak_trail_after_2x_tightens():
    p = {}
    reason, _ = conviction_peak_exit(
        close=160.0,
        low=159.0,
        entry_px=50.0,
        peak_close=220.0,
        months_below_sma24=0,
        in_profit=True,
        p=p,
    )
    assert reason == "peak_trail_after_2x"


def test_catastrophe_stop():
    reason, _ = conviction_peak_exit(
        close=36.0,
        low=34.0,
        entry_px=50.0,
        peak_close=55.0,
        months_below_sma24=0,
        in_profit=False,
        p={},
    )
    assert reason == "catastrophe_stop"