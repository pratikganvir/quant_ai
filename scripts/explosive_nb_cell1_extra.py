META_DIR = OUT_DIR / "meta"
META_DIR.mkdir(parents=True, exist_ok=True)
PANEL_META_PATH = OUT_DIR / "panel" / "panel_build_meta.json"
PANEL_FEATURES_PATH = OUT_DIR / "features" / "panel_features.parquet"
MODEL_BUNDLE_PATH = OUT_DIR / "models" / "primary_model_bundle.pkl"
ABLATION_MODEL_PATH = OUT_DIR / "models" / "ablation_logit.pkl"
AUDIT_META_PATH = META_DIR / "audit_meta.json"
EVENTS_META_PATH = META_DIR / "events_meta.json"
FEATURES_META_PATH = META_DIR / "features_meta.json"
PRE_EVENT_META_PATH = META_DIR / "pre_event_meta.json"
ABLATION_META_PATH = META_DIR / "ablation_meta.json"
ML_META_PATH = META_DIR / "ml_meta.json"
SIM_META_PATH = META_DIR / "sim_meta.json"
SCANNER_META_PATH = META_DIR / "scanner_meta.json"

models = {}
calibrator = None
full_model = None
full_cols = []


def load_model_bundle(path=MODEL_BUNDLE_PATH):
    if not path.exists():
        return None
    return pickle.loads(path.read_bytes())


def hydrate_models_from_bundle():
    """Load LGBM + isotonic from disk (resume after ML skip)."""
    global models, calibrator, Xcols
    bundle = load_model_bundle()
    if bundle is None:
        return False
    models = {"lgbm": bundle["model"]}
    calibrator = bundle.get("calibrator")
    if bundle.get("feature_cols"):
        Xcols = bundle["feature_cols"]
    return True