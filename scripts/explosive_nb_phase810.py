"""Phase 8.1.0 — trading calendar validation and missing-bar exit forensics."""

from quant.research.explosive_research import (
    MISSING_BAR_CLASS_LABELS,
    SIM_ENGINE_VERSION,
    build_exchange_session_registry,
    calendar_shift,
    classify_calendar_gap,
    classify_stock_absence,
    refined_trading_calendar,
    simulate_topk_calendar,
    trading_calendar_from_panel,
)


def _calendar_days_between(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return int((pd.Timestamp(b).normalize() - pd.Timestamp(a).normalize()).days)


def _sessions_between(a: pd.Timestamp, b: pd.Timestamp, cal: pd.DatetimeIndex) -> int:
    i0, i1 = cal.get_indexer([pd.Timestamp(a), pd.Timestamp(b)])
    if i0 < 0 or i1 < 0:
        return 0
    return max(0, i1 - i0)


def _replay_missing_streak(
    sym: str,
    entry: pd.Timestamp,
    exit_dt: pd.Timestamp,
    panel_cal: pd.DatetimeIndex,
    panel_symbols_by_date: dict,
) -> list[pd.Timestamp]:
    """Panel-calendar sessions in (entry, exit] where the symbol has no bar."""
    lo = panel_cal.get_indexer([pd.Timestamp(entry)])[0]
    hi = panel_cal.get_indexer([pd.Timestamp(exit_dt)])[0]
    if lo < 0 or hi < 0:
        return []
    miss: list[pd.Timestamp] = []
    for d in panel_cal[lo + 1 : hi + 1]:
        if sym not in panel_symbols_by_date.get(pd.Timestamp(d), ()):
            miss.append(pd.Timestamp(d))
    return miss


def _pair_missing_bar_exits(trades: pd.DataFrame, top_k: int) -> pd.DataFrame:
    rows: list[dict] = []
    if trades.empty:
        return pd.DataFrame(rows)
    t = trades.sort_values(["Symbol", "Date", "side"]).copy()
    t["Date"] = pd.to_datetime(t["Date"])
    open_lots: dict[str, list[dict]] = {}
    for _, r in t.iterrows():
        sym = str(r["Symbol"])
        if r["side"] == "BUY":
            open_lots.setdefault(sym, []).append(r.to_dict())
            continue
        if r["side"] != "SELL" or r.get("reason") != "missing_bar":
            continue
        if not open_lots.get(sym):
            continue
        buy = open_lots[sym].pop(0)
        rows.append(
            {
                "top_k": top_k,
                "Symbol": sym,
                "entry_date": pd.Timestamp(buy["Date"]),
                "exit_date": pd.Timestamp(r["Date"]),
                "entry_px": float(buy["px"]),
                "exit_px": float(r["px"]),
                "shares": float(r["shares"]),
                "exit_reason": r["reason"],
                "realized_pnl": float(r["notional"]) - float(buy["notional"]),
            }
        )
    return pd.DataFrame(rows)


print("\n" + "=" * 72)
print("Phase 8.1.0 — Trading Calendar & Missing-Bar Forensics")
print("=" * 72)

# --- 1. Inspect Phase 8 engine (read-only) ---
print(
    "\n[1] Current Phase 8 calendar / simulator logic\n"
    "  TRADING_CAL          := trading_calendar_from_panel(panel) — union of all panel dates\n"
    "  calendar_shift         := indexer shift on that calendar (labels + pre-event)\n"
    "  simulate_topk_calendar := iterate consecutive calendar sessions; count missing_bars when\n"
    "                            symbol ∉ panel on session nxt; exit after max_missing_sessions\n"
    f"  max_missing_sessions   := {CFG['strategy'].get('max_missing_sessions', 10)}\n"
    f"  sessions_held          := calendar index distance(entry_date, nxt) on TRADING_CAL\n"
    f"  SIM_ENGINE_VERSION     := {SIM_ENGINE_VERSION}"
)

if "TRADING_CAL" not in globals():
    TRADING_CAL = trading_calendar_from_panel(panel)
PANEL_CAL = TRADING_CAL
SESSION_REGISTRY = build_exchange_session_registry(panel, nifty)
REFINED_CAL = refined_trading_calendar(SESSION_REGISTRY)

# --- 2. Calendar reconstruction & validation ---
cal_report = {
    "panel_union_sessions": int(len(PANEL_CAL)),
    "refined_sessions": int(len(REFINED_CAL)),
    "nifty_available": bool(nifty is not None and not nifty.empty),
    "panel_date_min": str(pd.Timestamp(panel["Date"].min()).date()),
    "panel_date_max": str(pd.Timestamp(panel["Date"].max()).date()),
}
ghost = SESSION_REGISTRY.loc[~SESSION_REGISTRY["is_valid_session"]]
weekends = ghost.loc[ghost["weekday"] >= 5]
holidays = ghost.loc[(ghost["weekday"] < 5) & ~ghost["nifty_session"] & ~ghost["is_broad_panel"]]
thin = ghost.loc[(ghost["weekday"] < 5) & ghost["n_panel_symbols"].lt(50)]
muhurat = SESSION_REGISTRY.loc[SESSION_REGISTRY["session_type"] == "muhurat_or_partial"]
nifty_only = set(pd.to_datetime(nifty["Date"].unique())) if nifty is not None else set()
panel_only = set(PANEL_CAL) - nifty_only
nifty_missing_in_panel = sorted(nifty_only - set(PANEL_CAL))

cal_report.update(
    {
        "ghost_sessions_in_panel_union": int(len(ghost)),
        "ghost_weekends": int(len(weekends)),
        "ghost_weekday_holidays_or_closures": int(len(holidays)),
        "ghost_thin_ingestion": int(len(thin)),
        "muhurat_or_partial_sessions": int(len(muhurat)),
        "panel_dates_not_in_nifty": int(len(panel_only)),
        "nifty_dates_missing_in_panel": int(len(nifty_missing_in_panel)),
    }
)
print("\n[2] Calendar validation report")
for k, v in cal_report.items():
    print(f"  {k}: {v}")

cal_validation = SESSION_REGISTRY.copy()
cal_validation.to_parquet(OUT_DIR / "reports" / "phase810_calendar_validation.parquet", index=False)

if len(ghost) > 0:
    display(
        ghost.groupby("session_type", observed=True)
        .agg(n_dates=("Date", "count"), median_symbols=("n_panel_symbols", "median"))
        .reset_index()
    )

# --- 3–4. Missing-bar exit forensics (cached Phase 8 trade logs) ---
panel["Date"] = pd.to_datetime(panel["Date"])
sym_by_date = panel.groupby("Date", observed=True)["Symbol"].apply(lambda s: frozenset(s.astype(str)))
panel_symbols_by_date = {pd.Timestamp(k): v for k, v in sym_by_date.items()}

all_affected: list[pd.DataFrame] = []
all_miss_rows: list[dict] = []

for k in CFG["strategy"]["top_k"]:
    tp = OUT_DIR / "reports" / f"trades_top{k}.parquet"
    if not tp.exists():
        print(f"\nTop-{k}: no trade log at {tp} — run Phase 8 first.")
        continue
    trades = pd.read_parquet(tp)
    sells = trades.loc[(trades["side"] == "SELL") & (trades["reason"] == "missing_bar")]
    print(f"\nTop-{k}: {len(sells)} missing_bar exits in cached Phase 8 log")
    paired = _pair_missing_bar_exits(trades, k)
    if paired.empty:
        continue
    paired = paired.copy()
    paired["expected_exit"] = calendar_shift(
        PANEL_CAL, paired["entry_date"], CFG["strategy"]["max_hold"]
    )
    for i, row in paired.iterrows():
        miss_dates = _replay_missing_streak(
            row["Symbol"],
            row["entry_date"],
            row["exit_date"],
            PANEL_CAL,
            panel_symbols_by_date,
        )
        classifications: list[str] = []
        for md in miss_dates:
            reg = SESSION_REGISTRY.loc[SESSION_REGISTRY["Date"] == md]
            if not reg.empty and not bool(reg.iloc[0]["is_valid_session"]):
                code = classify_calendar_gap(SESSION_REGISTRY, md)
            else:
                code = classify_stock_absence(row["Symbol"], md, panel, SESSION_REGISTRY)
            classifications.append(code)
            all_miss_rows.append(
                {
                    "top_k": k,
                    "Symbol": row["Symbol"],
                    "missing_date": md,
                    "class_code": code,
                    "class_label": MISSING_BAR_CLASS_LABELS.get(code, "unknown"),
                    "is_valid_session": bool(reg.iloc[0]["is_valid_session"]) if not reg.empty else False,
                    "nifty_session": bool(reg.iloc[0]["nifty_session"]) if not reg.empty else False,
                }
            )
        trigger = miss_dates[-1] if miss_dates else pd.NaT
        trigger_code = classifications[-1] if classifications else "H"
        last_bar = (
            panel.loc[
                (panel["Symbol"].astype(str) == str(row["Symbol"]))
                & (panel["Date"] <= row["exit_date"])
            ]
            .sort_values("Date")
        )
        last_close = float(last_bar["Close"].iloc[-1]) if not last_bar.empty else np.nan
        last_bar_date = pd.Timestamp(last_bar["Date"].iloc[-1]) if not last_bar.empty else pd.NaT
        streak_misalign = any(
            (not SESSION_REGISTRY.loc[SESSION_REGISTRY["Date"] == md, "is_valid_session"].iloc[0])
            if (SESSION_REGISTRY["Date"] == md).any()
            else True
            for md in miss_dates
        )
        paired.loc[i, "missing_dates"] = json.dumps([str(x.date()) for x in miss_dates])
        paired.loc[i, "n_missing_sessions"] = len(miss_dates)
        paired.loc[i, "trigger_missing_date"] = trigger
        paired.loc[i, "trigger_class"] = trigger_code
        paired.loc[i, "calendar_misalignment"] = streak_misalign
        paired.loc[i, "last_bar_date"] = last_bar_date
        paired.loc[i, "last_available_close"] = last_close
        paired.loc[i, "exchange_sessions_held"] = _sessions_between(
            row["entry_date"], row["exit_date"], PANEL_CAL
        )
        paired.loc[i, "calendar_days_held"] = _calendar_days_between(
            row["entry_date"], row["exit_date"]
        )
    all_affected.append(paired)

missing_class = pd.DataFrame(all_miss_rows)
affected_trades = pd.concat(all_affected, ignore_index=True) if all_affected else pd.DataFrame()

if not missing_class.empty:
    cls_summary = (
        missing_class.groupby(["class_code", "class_label"], observed=True)
        .size()
        .reset_index(name="n_missing_observations")
        .sort_values("n_missing_observations", ascending=False)
    )
    print("\n[3] Missing-bar classification table (per missing session)")
    display(cls_summary)
    missing_class.to_parquet(OUT_DIR / "reports" / "phase810_missing_class.parquet", index=False)
else:
    cls_summary = pd.DataFrame()
    print("\n[3] No missing-bar observations to classify (no trade logs or zero exits).")

if not affected_trades.empty:
    print("\n[4] Affected missing-bar trades (sample)")
    show_cols = [
        "top_k",
        "Symbol",
        "entry_date",
        "expected_exit",
        "exit_date",
        "last_bar_date",
        "trigger_missing_date",
        "trigger_class",
        "n_missing_sessions",
        "calendar_misalignment",
        "entry_px",
        "last_available_close",
        "exit_px",
        "realized_pnl",
        "exit_reason",
    ]
    display(affected_trades[show_cols].head(25))
    affected_trades.to_parquet(OUT_DIR / "reports" / "phase810_affected_trades.parquet", index=False)
    n_total = len(affected_trades)
    n_misalign = int(affected_trades["calendar_misalignment"].sum())
    print(
        f"\nCalendar misalignment diagnosis: {n_misalign}/{n_total} missing-bar exits "
        f"({100 * n_misalign / max(n_total, 1):.1f}%) include ≥1 non-valid union-calendar session "
        "in the missing streak (weekend / holiday / thin ghost date)."
    )
else:
    n_misalign = 0
    n_total = 0

# --- 6. Comparative simulation (baseline panel union vs calendar-corrected) ---
if not models.get("lgbm") and not hydrate_models_from_bundle():
    print("\n[6] Skipping comparative sim — LightGBM bundle unavailable.")
else:
    test = features.loc[test2_m | holdout_m].copy()
    Xte = test[Xcols].replace([np.inf, -np.inf], np.nan).fillna(0)
    prob = models["lgbm"].predict_proba(Xte)[:, 1]
    test_scores = calibrator.predict(prob) if calibrator is not None else prob

    cmp_rows = []
    for k in tqdm(CFG["strategy"]["top_k"], desc="Phase 8.1 — comparative sim"):
        stats_b_path = OUT_DIR / "reports" / f"equity_top{k}_stats.json"
        trades_b_path = OUT_DIR / "reports" / f"trades_top{k}.parquet"
        if stats_b_path.exists() and trades_b_path.exists():
            st_b = json.loads(stats_b_path.read_text())
            tr_b = pd.read_parquet(trades_b_path)
        else:
            eq_b, st_b, tr_b = simulate_topk_calendar(
                test, panel, test_scores, k, CFG, PANEL_CAL
            )
            tr_b.to_parquet(trades_b_path, index=False)
            Path(stats_b_path).write_text(json.dumps(st_b, indent=2))
        base_mb = int((tr_b["reason"] == "missing_bar").sum()) if not tr_b.empty else 0
        eq_c, st_c, tr_c = simulate_topk_calendar(
            test,
            panel,
            test_scores,
            k,
            CFG,
            REFINED_CAL,
            missing_bar_sessions=REFINED_CAL,
        )
        mb_c = int((tr_c["reason"] == "missing_bar").sum()) if not tr_c.empty else 0
        unresolved = int(eq_c["n_pos"].iloc[-1]) if not eq_c.empty and "n_pos" in eq_c.columns else 0
        cmp_rows.append(
            {
                "top_k": k,
                "baseline_missing_bar_exits": base_mb,
                "corrected_missing_bar_exits": mb_c,
                "delta_missing_bar_exits": mb_c - base_mb,
                "baseline_n_trades": st_b.get("n_trades"),
                "corrected_n_trades": st_c.get("n_trades"),
                "baseline_total_return": st_b.get("total_return"),
                "baseline_max_drawdown": st_b.get("max_drawdown"),
                "baseline_sharpe": st_b.get("sharpe_daily_ann"),
                "corrected_total_return": st_c.get("total_return"),
                "corrected_max_drawdown": st_c.get("max_drawdown"),
                "corrected_sharpe": st_c.get("sharpe_daily_ann"),
                "corrected_ending_equity": float(eq_c["equity"].iloc[-1]) if not eq_c.empty else np.nan,
                "unresolved_positions_corrected": unresolved,
            }
        )
        eq_c.to_parquet(OUT_DIR / "reports" / f"equity_top{k}_calendar_corrected.parquet", index=False)
        tr_c.to_parquet(OUT_DIR / "reports" / f"trades_top{k}_calendar_corrected.parquet", index=False)
        Path(OUT_DIR / "reports" / f"equity_top{k}_calendar_corrected_stats.json").write_text(
            json.dumps(st_c, indent=2)
        )

    cmp = pd.DataFrame(cmp_rows)
    print("\n[6] Original vs calendar-corrected simulation")
    display(cmp)
    cmp.to_parquet(OUT_DIR / "reports" / "phase810_sim_comparison.parquet", index=False)

# --- 7. Final diagnosis ---
print("\n[7] Final diagnosis")
print(
    "  • Phase 8 uses the panel-union calendar; any date present for any symbol advances the simulator.\n"
    "  • Ghost union dates (weekends, exchange holidays, thin partial ingests) can increment missing_bars\n"
    "    without a true stock-specific data gap.\n"
    "  • Calendar-corrected mode uses NIFTY-backed / breadth-filtered sessions for iteration, holding\n"
    "    periods, and missing-bar counting.\n"
    f"  • Flagged misalignment among missing-bar exits: {n_misalign}/{max(n_total, 1)} trades."
)
print("\nPhase 8.1.0 artifacts →", OUT_DIR / "reports" / "phase810_*.parquet")