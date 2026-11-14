def compute_panel_features(panel_df, nifty_df=None):
    """Rolling features on the full tradable panel (labels merged later)."""
    x = panel_df.sort_values(["Symbol", "Date"]).copy()
    x["Date"] = pd.to_datetime(x["Date"], errors="coerce")
    g = x.groupby("Symbol", observed=True)

    x["vol_rvol_20"] = x["Volume"] / g["Volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    x["vol_va"] = g["Volume"].transform(lambda s: s.rolling(5, min_periods=3).mean()) / g["Volume"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    x["vol_turn_accel"] = g["Turnover"].transform(lambda s: s.rolling(5, min_periods=3).mean()) / g["Turnover"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    prev_c = g["Close"].shift(1)
    tr = np.maximum(x["High"] - x["Low"], np.maximum((x["High"] - prev_c).abs(), (x["Low"] - prev_c).abs()))
    x["vola_atr14_pct"] = tr.groupby(x["Symbol"]).transform(lambda s: s.rolling(14, min_periods=5).mean()) / x["Close"] * 100
    x["vola_atr5"] = tr.groupby(x["Symbol"]).transform(lambda s: s.rolling(5, min_periods=3).mean())
    x["vola_atr50"] = tr.groupby(x["Symbol"]).transform(lambda s: s.rolling(50, min_periods=20).mean())
    x["vola_vr"] = x["vola_atr5"] / x["vola_atr50"]
    roll_std = g["Close"].transform(lambda s: s.rolling(20, min_periods=10).std())
    x["vola_bbw"] = (4 * roll_std) / g["Close"].transform(lambda s: s.rolling(20, min_periods=10).mean())

    x["px_ret_5"] = g["Close"].pct_change(5)
    x["px_ret_10"] = g["Close"].pct_change(10)
    x["px_ret_20"] = g["Close"].pct_change(20)
    x["px_dist_20h"] = x["Close"] / g["High"].transform(lambda s: s.rolling(20, min_periods=10).max()) - 1
    x["px_dist_50h"] = x["Close"] / g["High"].transform(lambda s: s.rolling(50, min_periods=20).max()) - 1
    x["px_dist_252h"] = x["Close"] / g["High"].transform(lambda s: s.rolling(252, min_periods=60).max()) - 1
    x["px_clv"] = (x["Close"] - x["Low"]) / (x["High"] - x["Low"]).replace(0, np.nan)
    up = (x["Close"] > g["Close"].shift(1)).astype(int)
    x["px_up_streak"] = up.groupby(x["Symbol"]).transform(lambda s: s.groupby((s != s.shift()).cumsum()).cumsum())

    if "DelivPer" in x.columns:
        x["deliv_ma5"] = g["DelivPer"].transform(lambda s: s.rolling(5, min_periods=3).mean())
        x["deliv_ma20"] = g["DelivPer"].transform(lambda s: s.rolling(20, min_periods=10).mean())
        x["deliv_accel"] = x["deliv_ma5"] / x["deliv_ma20"]
    if "DelivQty" in x.columns:
        x["deliv_qty_accel"] = g["DelivQty"].transform(lambda s: s.rolling(5, min_periods=3).mean()) / g["DelivQty"].transform(
            lambda s: s.rolling(20, min_periods=10).mean()
        )

    x["liq_turn_med20"] = g["Turnover"].transform(lambda s: s.rolling(20, min_periods=10).median())
    # stale prints / zero-return days (not a pure illiquidity proxy)
    x["liq_zero_ret_20"] = g["Close"].pct_change().transform(
        lambda s: s.rolling(20, min_periods=10).apply(lambda a: np.mean(np.abs(a) < 1e-8), raw=True)
    )
    if "PrevClose" in x.columns:
        x["liq_gap"] = x["Close"] / x["PrevClose"] - 1

    if nifty_df is not None:
        nd = nifty_df[["Date", "Close"]].rename(columns={"Close": "NiftyClose"})
        nd["Date"] = pd.to_datetime(nd["Date"], errors="coerce")
        x = x.merge(nd, on="Date", how="left")
        nidx = nd.set_index("Date")["NiftyClose"]
        x["mkt_nifty_ret_5"] = nidx.pct_change(5).reindex(x["Date"]).to_numpy()
        x["mkt_nifty_ret_20"] = nidx.pct_change(20).reindex(x["Date"]).to_numpy()
        x["rs_20"] = x["px_ret_20"] - x["mkt_nifty_ret_20"]
        sma20 = g["Close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
        x_day = x.assign(_above=(x["Close"] > sma20).astype(int))
        breadth = x_day.groupby("Date")["_above"].mean().rename("mkt_breadth_20sma")
        x = x.merge(breadth, on="Date", how="left")
    return x


def attach_event_labels(feat_df, events_df):
    label_cols = [c for c in events_df.columns if c.startswith(("mfe_", "mae_", "ret_", "y_", "horizon_", "ca_"))]
    out = feat_df.merge(events_df[["Date", "Symbol"] + label_cols], on=["Date", "Symbol"], how="left")
    out = out.loc[out["Close"] >= CFG["min_price"]]
    out = out.loc[out["liq_turn_med20"] >= CFG["min_median_turnover_20d"]]
    return out


panel_features = compute_panel_features(panel, nifty)
feature_cols = [
    c for c in panel_features.columns if c.startswith(("vol_", "vola_", "px_", "deliv_", "liq_", "rs_", "mkt_"))
]
features = attach_event_labels(panel_features, events)
label_cols = [c for c in features.columns if c.startswith("y_")]
print("panel_features:", panel_features.shape, "| labeled rows:", len(features), "features:", len(feature_cols))
features.to_parquet(FEATURES_PATH, compression="zstd", index=False)
PANEL_FEATURES_PATH = OUT_DIR / "features" / "panel_features.parquet"
panel_features.to_parquet(PANEL_FEATURES_PATH, compression="zstd", index=False)
print("Wrote", FEATURES_PATH, "and", PANEL_FEATURES_PATH)