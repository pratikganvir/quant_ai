"""Launch Base — documented targets vs what backtests can hit (entries-only mode)."""

from __future__ import annotations

LAUNCH_BASE_CONSTRAINTS = """
Launch Base design targets (your spec):
  • Hit ratio (winning legs) > 50%
  • Max loss per leg ≈ 20% (hard stop + backtest fill cap)
  • Book max drawdown ≈ 20% (fuse + sizing)

Constraint honesty (full MCP monthly panel, 2005–2025):
  • >50% hit ratio + ≤20% leg loss + multibagger entries ONLY (no profit-floor scratch wins)
    → NOT jointly achievable on this panel in our grid search; best was ~45% win rate.
  • With profit-floor exits (v1.2) hit ratio >50% was achievable but CAGR collapsed (~3%).

Tighter-entries-only mode (v1.3):
  • Disables profit_floor; only stricter thrust/breakout + score floor + fewer slots.
  • Optimizes for: win rate, leg loss cap, DD — report actuals after each backtest.
"""

# Tighter entries-only defaults (no profit floor).
LAUNCH_BASE_TIGHT_ENTRY: dict[str, float | int | bool] = {
    "dist_hi12_min": -0.42,
    "dist_hi12_max": -0.14,
    "ret_3_min": 0.08,
    "ret_6_min": 0.05,
    "ret_12_min": 0.0,
    "ret_12_max": 0.45,
    "vol_ratio_min": 0.95,
    "breakout_rel_mom_min": 0.12,
    "min_rel_mom_thrust": 0.08,
    "bench_min_momentum": 0.05,
    "min_entry_score": 0.12,
    "top_k": 3,
    "use_profit_floor": False,
    "profit_arm_pct": 0.0,
    "entry_stop_pct": 20.0,
    "dd_hard_stop_pct": 12.0,
    "gross_cap": 0.80,
}