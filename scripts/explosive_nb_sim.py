from quant.research.explosive_research import (
    SIM_ENGINE_VERSION,
    report_equity_diagnostics,
    simulate_topk_calendar,
)


eq_paths = [OUT_DIR / "reports" / f"equity_top{k}.parquet" for k in CFG["strategy"]["top_k"]]
_sim_fp = fingerprint_dict(
    {
        "ml": read_meta_fingerprint(ML_META_PATH),
        "top_k": CFG["strategy"]["top_k"],
        "strategy": CFG["strategy"],
        "sim_engine": SIM_ENGINE_VERSION,
    }
)

if phase_cache_hit(CFG, "sim", eq_paths, SIM_META_PATH, _sim_fp):
    if not models.get("lgbm"):
        hydrate_models_from_bundle()
    for k in CFG["strategy"]["top_k"]:
        sp = OUT_DIR / "reports" / f"equity_top{k}_stats.json"
        if sp.exists():
            print(f"Top-{k} (cached):", json.loads(sp.read_text()))
elif not models.get("lgbm") and not hydrate_models_from_bundle():
    print("Calibrated LightGBM unavailable; skip strategy simulation.")
else:
    test = features.loc[test2_m | holdout_m].copy()
    Xte = test[Xcols].replace([np.inf, -np.inf], np.nan).fillna(0)
    test_scores = calibrator.predict(models["lgbm"].predict_proba(Xte)[:, 1])
    for k in tqdm(CFG["strategy"]["top_k"], desc="Phase 8 — backtest"):
        eq, stout, trades = simulate_topk_calendar(test, panel, test_scores, k, CFG, TRADING_CAL)
        if not eq.empty:
            print(f"Top-{k} sim:", {kk: (f"{vv:.2%}" if kk == "total_return" else vv) for kk, vv in stout.items()})
            eq.to_parquet(OUT_DIR / "reports" / f"equity_top{k}.parquet", index=False)
            Path(OUT_DIR / "reports" / f"equity_top{k}_stats.json").write_text(json.dumps(stout, indent=2))
            if not trades.empty:
                trades.to_parquet(OUT_DIR / "reports" / f"trades_top{k}.parquet", index=False)
    write_phase_meta(SIM_META_PATH, "sim", _sim_fp)

print("\n--- Equity diagnostics (cached or fresh) ---")
for k in CFG["strategy"]["top_k"]:
    path = OUT_DIR / "reports" / f"equity_top{k}.parquet"
    if path.exists():
        report_equity_diagnostics(
            pd.read_parquet(path),
            f"TOP {k}",
            CFG["strategy"]["initial_capital"],
        )
    trade_path = OUT_DIR / "reports" / f"trades_top{k}.parquet"
    if trade_path.exists():
        tl = pd.read_parquet(trade_path)
        sells = tl.loc[tl["side"] == "SELL"]
        print(f"\nTOP {k} trade log: {len(tl)} rows ({len(sells)} exits)")
        if not sells.empty and "notional" in sells.columns:
            print("Exit reasons:", sells["reason"].value_counts().to_dict())