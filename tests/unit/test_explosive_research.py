"""Unit tests for explosive NSE research primitives (notebook Phases 3–9)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path

from quant.research.explosive_research import (
    ca_invalidates_forward_window,
    calendar_shift,
    daily_precision_lift,
    dedupe_event_episodes,
    fingerprint_dict,
    first_touch_triple_barrier,
    force_rerun_phase,
    forward_excursion_calendar,
    mark_ca_suspect,
    phase_cache_hit,
    purge_overlaps_calendar,
    summarize_daily_metrics,
    trading_calendar_from_panel,
    write_phase_meta,
)

from tests.unit.explosive_fixtures import (
    ca_gap_panel,
    daily_symbol_series,
    single_symbol_linear_highs,
    triple_barrier_panel,
    two_symbol_panel_with_gap,
)


class TestTradingCalendar:
    def test_unique_sorted(self):
        cal = pd.bdate_range("2025-01-01", periods=5)
        panel = daily_symbol_series("X", cal)
        panel = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
        out = trading_calendar_from_panel(panel)
        assert len(out) == 5
        assert out.is_monotonic_increasing


class TestCalendarShift:
    def test_shift_back(self):
        cal = pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=10))
        d0 = cal[5]
        shifted = calendar_shift(cal, pd.Series([d0], index=[0]), -2)
        assert isinstance(shifted, pd.Series)
        assert shifted.iloc[0] == cal[3]

    def test_shift_forward(self):
        cal = pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=10))
        shifted = calendar_shift(cal, pd.Series([cal[2]]), 3)
        assert shifted.iloc[0] == cal[5]

    def test_out_of_range_returns_nat(self):
        cal = pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=3))
        shifted = calendar_shift(cal, pd.Series([cal[0]]), -5)
        assert pd.isna(shifted.iloc[0])

    def test_preserves_index(self):
        cal = pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=8))
        idx = pd.Index(["a", "b"])
        shifted = calendar_shift(cal, pd.Series([cal[4], cal[4]], index=idx), -1)
        assert list(shifted.index) == ["a", "b"]


class TestMarkCaSuspect:
    def test_prevclose_split_gap(self):
        panel, _ = ca_gap_panel()
        ca = mark_ca_suspect(panel, gap_abs=0.35)
        assert ca.iloc[5]

    def test_pct_change_without_prevclose(self):
        cal = pd.bdate_range("2025-01-01", periods=5)
        panel = daily_symbol_series("A", cal)
        panel.loc[panel.index[3], "Close"] *= 2.0
        ca = mark_ca_suspect(panel, gap_abs=0.35)
        assert ca.iloc[3]


class TestForwardExcursion:
    def test_known_mfe_mae_ret(self):
        panel, cal = single_symbol_linear_highs()
        out = forward_excursion_calendar(panel, cal, horizons=[5])
        row = out.iloc[0]
        # T+1..T+5 highs are 105..109 -> mfe vs close 100 = 9%
        assert row["horizon_complete_5"]
        assert abs(row["mfe_5"] - 0.10) < 1e-6
        assert abs(row["mae_5"] - 0.0) < 1e-6
        assert abs(row["ret_5"] - 0.05) < 1e-6

    def test_incomplete_when_missing_sessions(self):
        panel, cal = two_symbol_panel_with_gap()
        bbb = panel.loc[panel["Symbol"] == "BBB"].copy()
        out = forward_excursion_calendar(bbb, cal, horizons=[5])
        incomplete = (~out["horizon_complete_5"]).sum()
        assert incomplete > 0

    def test_row_count_horizon_differs_from_calendar(self):
        """More rows in 20 row-based window than 20 sessions when bars missing."""
        panel, cal = two_symbol_panel_with_gap()
        bbb = panel.loc[panel["Symbol"] == "BBB"].sort_values("Date")
        cal_pos = cal.get_indexer(pd.to_datetime(bbb["Date"]))
        i = 10
        if cal_pos[i] + 20 < len(cal):
            start = cal_pos[i] + 1
            end = cal_pos[i] + 20
            fut = cal[start : end + 1]
            present = sum(1 for d in fut if d in set(bbb["Date"]))
            assert present < 20


class TestCaInvalidatesForwardWindow:
    def test_ca_inside_forward_window_invalidates(self):
        panel, cal = ca_gap_panel()
        ca = mark_ca_suspect(panel, 0.35)
        inv = ca_invalidates_forward_window(panel, cal, horizon=5, ca_flag=ca)
        assert inv.iloc[4]
        assert not inv.iloc[6]


class TestFirstTouchTripleBarrier:
    def test_upper_hits_first(self):
        panel, cal = triple_barrier_panel(up_first=True)
        tb = first_touch_triple_barrier(panel, cal, horizon=3, up=0.20, down=-0.10)
        assert tb.iloc[0] == 1.0

    def test_lower_hits_first(self):
        panel, cal = triple_barrier_panel(up_first=False)
        tb = first_touch_triple_barrier(panel, cal, horizon=3, up=0.20, down=-0.10)
        assert tb.iloc[0] == 0.0


class TestDedupeEventEpisodes:
    def test_collapses_adjacent_runs(self):
        cal = pd.bdate_range("2025-01-01", periods=30)
        events = pd.DataFrame(
            {
                "Date": [cal[2], cal[3], cal[4], cal[20]],
                "Symbol": ["AAA", "AAA", "AAA", "AAA"],
                "mfe_20": [0.25, 0.30, 0.28, 0.22],
            }
        )
        out = dedupe_event_episodes(events, mfe_col="mfe_20", calendar=cal, gap_sessions=10)
        assert len(out) == 2
        assert list(pd.to_datetime(out["Date"])) == [cal[2], cal[20]]

    def test_independent_per_symbol(self):
        cal = pd.bdate_range("2025-01-01", periods=15)
        events = pd.DataFrame(
            {
                "Date": [cal[1], cal[2], cal[1], cal[2]],
                "Symbol": ["A", "A", "B", "B"],
                "mfe_20": [0.3, 0.35, 0.3, 0.35],
            }
        )
        out = dedupe_event_episodes(events, mfe_col="mfe_20", calendar=cal, gap_sessions=10)
        assert len(out) == 2
        assert set(out["Symbol"]) == {"A", "B"}

    def test_regires_iterrows_not_itertuples_for_cal_pos(self):
        """Regression: _cal_pos + itertuples raised AttributeError."""
        cal = pd.bdate_range("2025-01-01", periods=10)
        events = pd.DataFrame({"Date": cal[:3], "Symbol": ["Z"] * 3, "mfe_20": [0.2, 0.21, 0.22]})
        dedupe_event_episodes(events, mfe_col="mfe_20", calendar=cal, gap_sessions=0)


class TestDailyPrecisionLift:
    def test_per_day_top_k(self):
        df = pd.DataFrame(
            {
                "Date": ["2025-01-01"] * 5 + ["2025-01-02"] * 5,
                "score": [0.9, 0.8, 0.1, 0.2, 0.3, 0.5, 0.4, 0.3, 0.2, 0.1],
                "y": [1, 1, 0, 0, 0, 1, 0, 0, 0, 0],
            }
        )
        df["Date"] = pd.to_datetime(df["Date"])
        daily = daily_precision_lift(df, date_col="Date", score_col="score", y_col="y", k=2)
        assert len(daily) == 2
        d1 = daily.loc[daily["Date"] == pd.Timestamp("2025-01-01"), "precision_at_2"].iloc[0]
        assert d1 == 1.0

    def test_summarize_keys(self):
        daily = pd.DataFrame(
            {"precision_at_10": [0.2, 0.4], "lift_at_10": [2.0, 4.0], "any_hit": [1, 1]}
        )
        s = summarize_daily_metrics(daily, k=10)
        assert s["mean_precision_at_10"] == pytest.approx(0.3)
        assert s["n_days"] == 2


class TestPurgeOverlapsCalendar:
    def test_drops_train_rows_after_cutoff(self):
        cal = pd.bdate_range("2025-01-01", periods=30)
        dates = pd.Series(cal, index=range(30))
        mask = pd.Series(True, index=range(30))
        test_start = cal[25]
        out = purge_overlaps_calendar(mask, dates, test_start, horizon=5, calendar=cal)
        cutoff = cal[25 - 5]
        assert out.sum() == (dates <= cutoff).sum()
        assert out.iloc[20] and not out.iloc[21]


class TestArtifactResume:
    def test_force_rerun_all(self, tmp_path: Path):
        cfg = {"rerun_all": True, "force_rerun": []}
        assert force_rerun_phase(cfg, "panel") is True

    def test_phase_cache_hit_skips_when_fresh(self, tmp_path: Path):
        art = tmp_path / "out.parquet"
        art.write_bytes(b"x")
        meta = tmp_path / "meta.json"
        fp = "abc123"
        write_phase_meta(meta, "test", fp)
        cfg = {"rerun_all": False, "force_rerun": []}
        assert phase_cache_hit(cfg, "test", [art], meta, fp) is True

    def test_phase_cache_miss_on_force(self, tmp_path: Path):
        art = tmp_path / "out.parquet"
        art.write_bytes(b"x")
        meta = tmp_path / "meta.json"
        fp = "abc123"
        write_phase_meta(meta, "test", fp)
        cfg = {"force_rerun": ["test"]}
        assert phase_cache_hit(cfg, "test", [art], meta, fp) is False


class TestForwardExcursionUsesCalendarNotRows:
    def test_smoke(self):
        cal = pd.bdate_range("2025-01-01", periods=30)
        panel = daily_symbol_series("AAA", cal)
        out = forward_excursion_calendar(panel, cal, horizons=[5])
        assert np.isfinite(out.iloc[10]["mfe_5"])