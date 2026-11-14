from sklearn.isotonic import IsotonicRegression

MIN_TRAIN_POS = 20
Xcols = [c for c in feature_cols if c in features.columns]
TARGET = f"y_explosive_{CFG['primary_horizon']}"
MODEL_BUNDLE_PATH = OUT_DIR / "models" / "primary_model_bundle.pkl"
calibrator = None


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


results = []
models = {}

# Purged train for the primary model; calibrate only on chronological validation.
tr_mask = purge_overlaps_calendar(train_m, features.loc[valid_m, "Date"].min(), CFG["h_max"], TRADING_CAL)
Xtr, ytr, _ = prep_xy(features.loc[tr_mask])
Xva, yva, fva = prep_xy(features.loc[valid_m])

if ytr.sum() >= MIN_TRAIN_POS and len(Xva) > 50 and HAS_LGBM:
    scale = (ytr == 0).sum() / max((ytr == 1).sum(), 1)
    primary_lgbm = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=63,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight={0: 1.0, 1: scale},
        random_state=CFG["random_seed"],
    )
    primary_lgbm.fit(Xtr, ytr)
    p_va_raw = primary_lgbm.predict_proba(Xva)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(p_va_raw, yva)
    models["lgbm"] = primary_lgbm

for name, mask in [("valid", valid_m), ("test1", test1_m), ("test2", test2_m), ("holdout", holdout_m)]:
    tr_mask = train_m
    if name != "valid":
        tr_mask = train_m | valid_m
    test_start = features.loc[mask, "Date"].min()
    if pd.notna(test_start):
        tr_mask = purge_overlaps_calendar(tr_mask, test_start, CFG["h_max"], TRADING_CAL)
    Xtr, ytr, _ = prep_xy(features.loc[tr_mask])
    Xte, yte, fte = prep_xy(features.loc[mask])
    if len(Xtr) < 100 or len(Xte) < 50:
        continue
    if ytr.sum() < MIN_TRAIN_POS:
        print(f"skip {name}: only {int(ytr.sum())} positives in training")
        continue

    scaler = StandardScaler()
    Xtr_s = scaler.fit_transform(Xtr)
    Xte_s = scaler.transform(Xte)

    logit = LogisticRegression(max_iter=800, class_weight="balanced")
    logit.fit(Xtr_s, ytr)
    p_logit = logit.predict_proba(Xte_s)[:, 1]
    results.append(eval_probs_daily(fte, pd.Series(p_logit, index=fte.index), f"logit_{name}"))

    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=50,
        class_weight="balanced",
        random_state=CFG["random_seed"],
        n_jobs=-1,
    )
    rf.fit(Xtr, ytr)
    p_rf = rf.predict_proba(Xte)[:, 1]
    results.append(eval_probs_daily(fte, pd.Series(p_rf, index=fte.index), f"rf_{name}"))

    if models.get("lgbm") is not None and calibrator is not None:
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
display(model_eval)