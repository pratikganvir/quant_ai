_pre_fp = fingerprint_dict(
    {
        "features": read_meta_fingerprint(FEATURES_META_PATH),
        "events": read_meta_fingerprint(EVENTS_META_PATH),
        "lags": CFG["pre_event_lags"],
        "top_n_events": CFG["top_n_events"],
        "event_episode_gap": CFG["event_episode_gap"],
        "match_k": CFG["match_k"],
        "random_seed": CFG["random_seed"],
        "primary_mfe_thresh": CFG["primary_mfe_thresh"],
    }
)
_cohort_path = OUT_DIR / "reports" / "pre_event_cohort.parquet"

if phase_cache_hit(CFG, "pre_event", [PRE_EVENT_PATH, _cohort_path], PRE_EVENT_META_PATH, _pre_fp):
    pre_tests = pd.read_parquet(PRE_EVENT_PATH)
    print("Loaded pre-event:", len(pre_tests), PRE_EVENT_PATH)
    display(pre_tests.head(20))
else:
    rng = np.random.default_rng(CFG["random_seed"])
    H = CFG["primary_horizon"]
    mfe_col, y_col = f"mfe_{H}", f"y_explosive_{H}"

    event_pool = events.loc[
        events[mfe_col].notna()
        & events[f"horizon_complete_{H}"]
        & ~events[f"ca_fwd_invalid_{H}"]
        & (events[mfe_col] >= CFG["primary_mfe_thresh"])
    ].copy()

    candidates = dedupe_event_episodes(
        event_pool,
        mfe_col=mfe_col,
        calendar=TRADING_CAL,
        gap_sessions=CFG["event_episode_gap"],
    ).sort_values(mfe_col, ascending=False).head(CFG["top_n_events"])

    if candidates.empty:
        raise ValueError("No explosive events after dedup; check panel / thresholds.")

    def liquidity_bucket(turnover):
        if not np.isfinite(turnover) or turnover <= 0:
            return "unk"
        return ["micro", "small", "mid", "large"][min(3, int(np.log10(turnover + 1) // 2))]

    candidates["liq_bucket"] = candidates["Turnover"].map(liquidity_bucket)
    pool = events.loc[events["Close"] >= CFG["min_price"]]
    used_controls: set[tuple] = set()
    LAGS = CFG["pre_event_lags"]
    FEATURES_PRE = [c for c in feature_cols if c in panel_features.columns]
    feat_index = panel_features.set_index(["Date", "Symbol"])

    def match_controls(evt_row, k=3):
        same_day = pool.loc[pool["Date"] == evt_row["Date"]]
        same_day = same_day.loc[same_day[y_col] == 0]
        if same_day.empty:
            return pd.DataFrame()
        lo, hi = evt_row["Close"] * 0.5, evt_row["Close"] * 2.0
        same_day = same_day.loc[(same_day["Close"] >= lo) & (same_day["Close"] <= hi)]
        b = evt_row["liq_bucket"]
        same_day = same_day.loc[same_day["Turnover"].map(liquidity_bucket) == b]
        if same_day.empty:
            same_day = pool.loc[(pool["Date"] == evt_row["Date"]) & (pool[y_col] == 0)]
        same_day = same_day.loc[~same_day.apply(lambda r: (r["Date"], r["Symbol"]) in used_controls, axis=1)]
        if len(same_day) <= k:
            pick = same_day
        else:
            pick = same_day.sample(n=k, random_state=rng.integers(0, 2**31 - 1))
        for _, r in pick.iterrows():
            used_controls.add((r["Date"], r["Symbol"]))
        return pick

    cohort_rows = []
    for _, ev in tqdm(candidates.iterrows(), total=len(candidates), desc="Phase 4 — match controls"):
        ctl = match_controls(ev, CFG["match_k"])
        if ctl.empty:
            continue
        for lag in LAGS:
            ev_asof = calendar_shift(TRADING_CAL, pd.Series([ev["Date"]]), -lag).iloc[0]
            if pd.isna(ev_asof):
                continue
            key = (pd.Timestamp(ev_asof), ev["Symbol"])
            if key not in feat_index.index:
                continue
            ev_rec = {
                "cohort": "event",
                "lag": lag,
                "event_date": ev["Date"],
                "asof_date": ev_asof,
                "Symbol": ev["Symbol"],
            }
            for c in FEATURES_PRE:
                ev_rec[c] = feat_index.loc[key, c]
            cohort_rows.append(ev_rec)
            for _, cr in ctl.iterrows():
                ctl_asof = calendar_shift(TRADING_CAL, pd.Series([cr["Date"]]), -lag).iloc[0]
                if pd.isna(ctl_asof):
                    continue
                ckey = (pd.Timestamp(ctl_asof), cr["Symbol"])
                if ckey not in feat_index.index:
                    continue
                ctl_rec = {
                    "cohort": "control",
                    "lag": lag,
                    "event_date": ev["Date"],
                    "asof_date": ctl_asof,
                    "Symbol": cr["Symbol"],
                }
                for c in FEATURES_PRE:
                    ctl_rec[c] = feat_index.loc[ckey, c]
                cohort_rows.append(ctl_rec)

    cohort = pd.DataFrame(cohort_rows)
    if cohort.empty:
        raise ValueError("Pre-event cohort empty; check panel_features.")

    n_tests = len(FEATURES_PRE) * len(LAGS)
    tests = []
    for lag, fc in tqdm(
        [(lag, fc) for lag in LAGS for fc in FEATURES_PRE],
        desc="Phase 4 — Mann-Whitney",
        leave=False,
    ):
        ev_s = pd.to_numeric(cohort.loc[(cohort["cohort"] == "event") & (cohort["lag"] == lag), fc], errors="coerce")
        ctl_s = pd.to_numeric(cohort.loc[(cohort["cohort"] == "control") & (cohort["lag"] == lag), fc], errors="coerce")
        a = ev_s.to_numpy(dtype=np.float64)
        b = ctl_s.to_numpy(dtype=np.float64)
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        if len(a) < 30 or len(b) < 30:
            continue
        stat, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        tests.append({
            "lag": lag,
            "feature": fc,
            "n_events": len(a),
            "n_controls": len(b),
            "event_median": float(np.median(a)),
            "control_median": float(np.median(b)),
            "u_stat": float(stat),
            "p_value": float(p),
            "p_bonferroni": min(1.0, float(p) * n_tests),
        })

    pre_tests = pd.DataFrame(tests).sort_values("p_bonferroni")
    pre_tests.to_parquet(PRE_EVENT_PATH, compression="zstd", index=False)
    cohort.to_parquet(_cohort_path, compression="zstd", index=False)
    write_phase_meta(PRE_EVENT_META_PATH, "pre_event", _pre_fp, n_tests=len(pre_tests))
    print("Deduped events:", len(candidates), "| cohort rows:", len(cohort), "| tests:", len(pre_tests))
    display(pre_tests.head(20))