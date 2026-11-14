_scan_fp = fingerprint_dict(
    {
        "panel_features": file_fingerprint(PANEL_FEATURES_PATH),
        "ml": read_meta_fingerprint(ML_META_PATH),
        "prob_threshold": CFG["strategy"]["prob_threshold"],
    }
)

if phase_cache_hit(CFG, "scanner", [SCANNER_PATH, SCANNER_CSV], SCANNER_META_PATH, _scan_fp):
    scan = pd.read_parquet(SCANNER_PATH)
    rank_cols = ["Date", "Symbol", "Close", "prob_explosive", "vol_rvol_20", "vola_vr", "px_dist_252h", "rs_20"]
    rank_cols = [c for c in rank_cols if c in scan.columns]
    print("Loaded scanner:", scan["Date"].max() if "Date" in scan.columns else "?", len(scan), SCANNER_PATH)
    display(scan[rank_cols].head(20))
else:
    if not models.get("lgbm"):
        hydrate_models_from_bundle()

    with tqdm(total=4, desc="Phase 9 — scanner") as pbar:
        latest_date = panel_features["Date"].max()
        scan = panel_features.loc[panel_features["Date"] == latest_date].copy()
        pbar.update(1)

        prob_thresh = CFG["strategy"]["prob_threshold"]
        if "lgbm" in models and calibrator is not None:
            X = scan[Xcols].replace([np.inf, -np.inf], np.nan).fillna(0)
            scan["prob_explosive"] = calibrator.predict(models["lgbm"].predict_proba(X)[:, 1])
        elif full_model is not None:
            scan["prob_explosive"] = score_df(scan, full_model, full_cols, TARGET)
        else:
            scan["prob_explosive"] = np.nan
        pbar.update(1)

        if np.isfinite(prob_thresh):
            scan = scan.loc[scan["prob_explosive"] >= prob_thresh]
        pbar.update(1)

        rank_cols = ["Date", "Symbol", "Close", "prob_explosive", "vol_rvol_20", "vola_vr", "px_dist_252h", "rs_20"]
        rank_cols = [c for c in rank_cols if c in scan.columns]
        scan = scan.sort_values("prob_explosive", ascending=False)
        scan[rank_cols].head(50).to_csv(SCANNER_CSV, index=False)
        scan.to_parquet(SCANNER_PATH, compression="zstd", index=False)
        write_phase_meta(
            SCANNER_META_PATH,
            "scanner",
            _scan_fp,
            asof_date=str(latest_date),
        )
        pbar.update(1)

    print("Scanner date", latest_date, "rows:", len(scan), "→", SCANNER_PATH)
    display(scan[rank_cols].head(20))