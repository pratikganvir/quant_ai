from quant.research.explosive_research import load_feature_dataset_artifacts

if not (globals().get("feature_cols") and globals().get("features") is not None):
    _feat_ctx = load_feature_dataset_artifacts(FEATURES_PATH, PANEL_FEATURES_PATH)
    globals().update(_feat_ctx)
    print(
        "Hydrated Phase 5 from disk:",
        len(_feat_ctx["feature_cols"]),
        "features,",
        len(_feat_ctx["features"]),
        "rows",
    )

FAMILIES = {
    "E1_volume": [c for c in feature_cols if c.startswith("vol_")],
    "E2_volatility": [c for c in feature_cols if c.startswith("vola_")],
    "E3_delivery": [c for c in feature_cols if c.startswith("deliv_")],
    "E4_price_structure": [c for c in feature_cols if c.startswith("px_")],
    "E5_relative_strength": [c for c in feature_cols if c.startswith("rs_")],
    "E6_liquidity": [c for c in feature_cols if c.startswith("liq_")],
    "E7_market": [c for c in feature_cols if c.startswith("mkt_")],
}

TARGET = f"y_explosive_{CFG['primary_horizon']}"


def split_masks(df):
    y = df["Date"].dt.year
    s = CFG["wf_splits"]
    train = y <= s["train_end"]
    valid = (y > s["train_end"]) & (y <= s["valid_end"])
    test1 = (y > s["valid_end"]) & (y <= s["test1_end"])
    test2 = (y > s["test1_end"]) & (y <= s["test2_end"])
    holdout = y > s["test2_end"]
    return train, valid, test1, test2, holdout


def precision_at_k(y_true, y_score, k=20):
    if len(y_true) == 0:
        return np.nan
    k = min(k, len(y_true))
    idx = np.argsort(-y_score)[:k]
    return float(y_true.iloc[idx].mean())


def lift_at_k(y_true, y_score, k=20):
    base = y_true.mean()
    if base <= 0:
        return np.nan
    return precision_at_k(y_true, y_score, k) / base


def fit_logit_scorer(train_df, cols, target):
    cols = [c for c in cols if c in train_df.columns]
    if not cols:
        return None, cols
    tr = train_df.dropna(subset=cols + [target])
    if tr[target].sum() < 5:
        return None, cols
    X = tr[cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    y = tr[target]
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    w = len(y) / max(y.sum(), 1)
    clf = LogisticRegression(max_iter=500, class_weight={0: 1, 1: w})
    clf.fit(Xs, y)
    return (scaler, clf), cols


def score_df(df, model, cols, target):
    if model is None:
        return pd.Series(np.zeros(len(df)), index=df.index)
    scaler, clf = model
    X = df[cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return pd.Series(clf.predict_proba(scaler.transform(X))[:, 1], index=df.index)


def eval_daily_scanner(frame, scores, target, k=20):
    tmp = frame[["Date", target]].copy()
    tmp["score"] = scores
    daily = daily_precision_lift(tmp, date_col="Date", score_col="score", y_col=target, k=k)
    return daily, summarize_daily_metrics(daily, k)


train_m, valid_m, test1_m, test2_m, holdout_m = split_masks(features)

_ab_fp = fingerprint_dict(
    {"features": read_meta_fingerprint(FEATURES_META_PATH), "wf_splits": CFG["wf_splits"], "target": TARGET}
)

if phase_cache_hit(CFG, "ablation", [ABLATION_PATH], ABLATION_META_PATH, _ab_fp):
    ablation = pd.read_csv(ABLATION_PATH)
    if ABLATION_MODEL_PATH.exists():
        full_model, full_cols = pickle.loads(ABLATION_MODEL_PATH.read_bytes())
    else:
        full_model, full_cols = None, feature_cols
    display(ablation)
else:
    train_ab = features.loc[train_m]
    valid_ab = features.loc[valid_m]
    all_cols = feature_cols

    full_model, full_cols = fit_logit_scorer(train_ab, all_cols, TARGET)
    scores = score_df(valid_ab, full_model, full_cols, TARGET)
    _, full_daily = eval_daily_scanner(valid_ab, scores, TARGET, k=20)

    rows = []
    for fam, cols in tqdm(FAMILIES.items(), desc="Phase 6 — ablation"):
        ablate_cols = [c for c in all_cols if c not in cols]
        m, used = fit_logit_scorer(train_ab, ablate_cols, TARGET)
        sc = score_df(valid_ab, m, used, TARGET)
        _, daily = eval_daily_scanner(valid_ab, sc, TARGET, k=20)
        rows.append({
            "experiment": fam,
            "mean_precision_at_20": daily["mean_precision_at_20"],
            "mean_lift_at_20": daily["mean_lift_at_20"],
            "pct_days_with_hit": daily["pct_days_with_hit"],
            "delta_lift_vs_full": daily["mean_lift_at_20"] - full_daily["mean_lift_at_20"],
            "n_features_removed": len(cols),
        })
    rows.append({
        "experiment": "full",
        "mean_precision_at_20": full_daily["mean_precision_at_20"],
        "mean_lift_at_20": full_daily["mean_lift_at_20"],
        "pct_days_with_hit": full_daily["pct_days_with_hit"],
        "delta_lift_vs_full": 0.0,
        "n_features_removed": 0,
    })
    ablation = pd.DataFrame(rows)
    ablation.to_csv(ABLATION_PATH, index=False)
    if full_model is not None:
        ABLATION_MODEL_PATH.write_bytes(pickle.dumps((full_model, full_cols)))
    write_phase_meta(ABLATION_META_PATH, "ablation", _ab_fp)
    display(ablation)