from quant.research.multibagger_exits import position_exit_reason


def test_trend_break_requires_two_months_below_sma():
    p = {"trend_confirm_months": 2, "stock_peak_dd_pct": 50}
    _, mb = position_exit_reason(
        close=90,
        entry_px=100,
        peak_close=110,
        months_below_sma=0,
        sma_trend=95,
        regime_on=True,
        months_held=3,
        p=p,
    )
    assert mb == 1
    reason, mb2 = position_exit_reason(
        close=89,
        entry_px=100,
        peak_close=110,
        months_below_sma=mb,
        sma_trend=95,
        regime_on=True,
        months_held=4,
        p=p,
    )
    assert reason == "trend_break"
    assert mb2 == 2


def test_entry_stop_before_trend():
    reason, _ = position_exit_reason(
        close=70,
        entry_px=100,
        peak_close=105,
        months_below_sma=0,
        sma_trend=80,
        regime_on=True,
        months_held=2,
        p={"entry_stop_pct": 25},
    )
    assert reason == "entry_stop"