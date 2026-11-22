from sklearn.isotonic import IsotonicRegression
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

MIN_TRAIN_POS = 20
Xcols = [c for c in feature_cols if c in features.columns]
TARGET = f"y_explosive_{CFG['primary_horizon']}"
ML_CFG = CFG.get("ml", {})


def _subsample_train(X, y, f, max_rows):
    if not max_rows or len(X) <= max_rows:
        return X, y, f
    rng = np.random.default_rng(CFG["random_seed"])
    if y.sum() < 5:
        idx = rng.choice(len(X), size=max_rows, replace=False)
    else:
        pos_idx = np.flatnonzero(y.to_numpy() == 1)
        neg_idx = np.flatnonzero(y.to_numpy() == 0)
        n_pos = min(len(pos_idx), max(5, max_rows // 10))
        n_neg = max_rows - n_pos
        pick = np.concatenate([
            rng.choice(pos_idx, size=n_pos, replace=False),
            rng.choice(neg_idx, size=min(n_neg, len(neg_idx)), replace=False),
        ])
        idx = np.sort(pick)
    return X.iloc[idx], y.iloc[idx], f.iloc[idx]


_ml_fp = fingerprint_dict(
    {
        "features": read_meta_fingerprint(FEATURES_META_PATH),
        "wf_splits": CFG["wf_splits"],
        "h_max": CFG["h_max"],
        "random_seed": CFG["random_seed"],
        "ml": ML_CFG,
    }
)

if phase_cache_hit(CFG, "ml", [MODEL_EVAL_PATH, MODEL_BUNDLE_PATH], ML_META_PATH, _ml_fp):
    hydrate_models_from_bundle()
    model_eval = pd.read_csv(MODEL_EVAL_PATH)
    display(model_eval)
else:

    def prep_xy(frame):
        f = frame.dropna(subset=Xcols + [TARGET])
        X = f[Xcols].replace([np.inf, -np.inf], np.nan).fillna(0)
        y = f[TARGET]
        return X, y, f

    def eval_probs_daily(frame, scores, tag, k=20):
        y = frame[TARGET]
        _, summ = eval_daily_scanner(frame, scores, TARGET, k=k)
        out = {"split": tag, "pr_auc": float(average_precision_score(y, scores)) if y.sum() > 0 else np.nan}
        out.update(summ)
        out["brier"] = float(brier_score_loss(y, scores))
        out["base_rate"] = float(y.mean())
        out["n"] = int(len(y))
        return out

    wf_models = list(ML_CFG.get("walk_forward_models", ["lgbm"]))
    bench_models = list(ML_CFG.get("benchmark_models", ["logit", "rf"]))
    bench_splits = set(ML_CFG.get("benchmark_splits", ["valid"]))
    train_cap = ML_CFG.get("train_sample_max")
    lgbm_trees = int(ML_CFG.get("lgbm_n_estimators", 200))
    rf_trees = int(ML_CFG.get("rf_n_estimators", 80))

    results = []
    calibrator = None

    tr_mask = purge_overlaps_calendar(
        train_m, features["Date"], features.loc[valid_m, "Date"].min(), CFG["h_max"], TRADING_CAL
    )
    Xtr, ytr, _ = prep_xy(features.loc[tr_mask])
    Xva, yva, fva = prep_xy(features.loc[valid_m])

    if ytr.sum() >= MIN_TRAIN_POS and len(Xva) > 50 and HAS_LGBM and "lgbm" in wf_models:
        with tqdm(total=2, desc="Phase 7 — primary LGBM + isotonic") as pbar:
            scale = (ytr == 0).sum() / max((ytr == 1).sum(), 1)
            primary_lgbm = lgb.LGBMClassifier(
                n_estimators=lgbm_trees,
                learning_rate=0.05,
                num_leaves=63,
                subsample=0.8,
                colsample_bytree=0.8,
                class_weight={0: 1.0, 1: scale},
                random_state=CFG["random_seed"],
                n_jobs=-1,
            )
            primary_lgbm.fit(Xtr, ytr)
            pbar.update(1)
            p_va_raw = primary_lgbm.predict_proba(Xva)[:, 1]
            calibrator = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(p_va_raw, yva)
            models["lgbm"] = primary_lgbm
            pbar.update(1)

    split_iter = [("valid", valid_m), ("test1", test1_m), ("test2", test2_m), ("holdout", holdout_m)]
    for name, mask in tqdm(split_iter, desc="Phase 7 — walk-forward eval"):
        Xte, yte, fte = prep_xy(features.loc[mask])
        if len(Xte) < 50:
            continue

        run_bench = name in bench_splits
        models_this = list(wf_models)
        if run_bench:
            for m in bench_models:
                if m not in models_this:
                    models_this.append(m)

        if "logit" in models_this or "rf" in models_this:
            tr_mask = train_m
            if name != "valid":
                tr_mask = train_m | valid_m
            test_start = features.loc[mask, "Date"].min()
            if pd.notna(test_start):
                tr_mask = purge_overlaps_calendar(tr_mask, features["Date"], test_start, CFG["h_max"], TRADING_CAL)
            Xtr, ytr, ftr = prep_xy(features.loc[tr_mask])
            if ytr.sum() < MIN_TRAIN_POS or len(Xtr) < 100:
                print(f"skip benchmarks {name}: insufficient train")
            else:
                Xtr_b, ytr_b, _ = _subsample_train(Xtr, ytr, ftr, train_cap)
                if "logit" in models_this:
                    scaler = StandardScaler()
                    Xtr_s = scaler.fit_transform(Xtr_b)
                    Xte_s = scaler.transform(Xte)
                    logit = LogisticRegression(max_iter=400, class_weight="balanced")
                    logit.fit(Xtr_s, ytr_b)
                    p_logit = logit.predict_proba(Xte_s)[:, 1]
                    results.append(eval_probs_daily(fte, pd.Series(p_logit, index=fte.index), f"logit_{name}"))
                if "rf" in models_this:
                    rf = RandomForestClassifier(
                        n_estimators=rf_trees,
                        max_depth=8,
                        min_samples_leaf=50,
                        class_weight="balanced",
                        random_state=CFG["random_seed"],
                        n_jobs=-1,
                    )
                    rf.fit(Xtr_b, ytr_b)
                    p_rf = rf.predict_proba(Xte)[:, 1]
                    results.append(eval_probs_daily(fte, pd.Series(p_rf, index=fte.index), f"rf_{name}"))

        if "lgbm" in models_this and models.get("lgbm") is not None and calibrator is not None:
            p_raw = models["lgbm"].predict_proba(Xte)[:, 1]
            p_lgb = calibrator.predict(p_raw)
            results.append(eval_probs_daily(fte, pd.Series(p_lgb, index=fte.index), f"lgbm_{name}"))

    if models.get("lgbm") is not None and calibrator is not None:
        bundle = {
            "model": models["lgbm"],
            "calibrator": calibrator,
            "feature_cols": Xcols,
            "target": TARGET,
            "cfg_snippet": {
                "primary_horizon": CFG["primary_horizon"],
                "prob_threshold": CFG["strategy"]["prob_threshold"],
            },
        }
        MODEL_BUNDLE_PATH.write_bytes(pickle.dumps(bundle))
        models["lgbm"].booster_.save_model(str(OUT_DIR / "models" / "lgbm_primary.txt"))
        print("Saved bundle:", MODEL_BUNDLE_PATH)

    model_eval = pd.DataFrame(results)
    model_eval.to_csv(MODEL_EVAL_PATH, index=False)
    write_phase_meta(ML_META_PATH, "ml", _ml_fp)
    display(model_eval)