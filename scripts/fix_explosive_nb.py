#!/usr/bin/env python3
"""Apply notebook cell sources from scripts/explosive_nb_*.py into explosive_moves_research.ipynb."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

NB = Path(__file__).resolve().parents[1] / "strategies" / "explosive_moves_research.ipynb"
SCRIPTS = Path(__file__).resolve().parent

AUDIT_CODE = """
def stock_parquet_glob():
    return sorted(
        p for p in tqdm(
            DATA_DIR.rglob("nse_cm_*.parquet"),
            desc="Phase 1 — discover files",
            unit="path",
        )
        if "results" not in p.parts and "indices" not in p.parts
    )

files = stock_parquet_glob()
print("Daily stock files:", len(files))
if not files:
    raise FileNotFoundError(f"No nse_cm_*.parquet under {DATA_DIR}")

_audit_fp = fingerprint_dict({"n_files": len(files)})

if phase_cache_hit(CFG, "audit", [AUDIT_PATH], AUDIT_META_PATH, _audit_fp):
    audit = json.loads(AUDIT_PATH.read_text())
    FEATURE_FLAGS.update(audit.get("feature_flags", {}))
    audit_df = pd.DataFrame(audit.get("duckdb_summary", []))
    display(audit_df)
    if audit.get("duplicate_sample"):
        print("Duplicate (date,symbol) rows (sample):", len(audit["duplicate_sample"]))
    print("Feature flags:", FEATURE_FLAGS)
else:
    schema_union = set()
    for p in tqdm([files[0], files[len(files) // 2], files[-1]], desc="Phase 1 — schema samples"):
        schema_union |= {c.lower() for c in pq.ParquetFile(p).schema.names}

    con = duckdb.connect()
    paths = [str(p).replace("'", "''") for p in files]
    paths_sql = ", ".join(f"'{p}'" for p in paths)
    with tqdm(total=2, desc="Phase 1 — DuckDB audit") as pbar:
        audit_df = con.execute(
            f'''
            SELECT COUNT(*) AS n_rows,
                   COUNT(DISTINCT symbol) AS n_symbols,
                   MIN(CAST(date AS DATE)) AS dmin,
                   MAX(CAST(date AS DATE)) AS dmax
            FROM read_parquet([{paths_sql}], union_by_name=true)
            '''
        ).df()
        pbar.update(1)
        dup_df = con.execute(
            f'''
            SELECT date, symbol, COUNT(*) AS n
            FROM read_parquet([{paths_sql}], union_by_name=true)
            GROUP BY 1,2 HAVING n>1 LIMIT 10
            '''
        ).df()
        pbar.update(1)

    FEATURE_FLAGS = {
        "HAS_TURNOVER": "turnover" in schema_union,
        "HAS_DELIVERY": ("deliv_per" in schema_union) or ("deliv_qty" in schema_union),
        "HAS_SERIES": "series" in schema_union,
        "HAS_PREV_CLOSE": "prev_close" in schema_union,
    }
    audit = {
        "n_files": len(files),
        "schema_union": sorted(schema_union),
        "feature_flags": FEATURE_FLAGS,
        "duckdb_summary": audit_df.to_dict(orient="records"),
        "duplicate_sample": dup_df.to_dict(orient="records"),
        "nifty_exists": NIFTY50_PATH.exists(),
    }
    AUDIT_PATH.write_text(json.dumps(audit, indent=2, default=str))
    write_phase_meta(AUDIT_META_PATH, "audit", _audit_fp)
    display(audit_df)
    if not dup_df.empty:
        print("Duplicate (date,symbol) rows (sample):", len(dup_df))
    print("Feature flags:", FEATURE_FLAGS)
""".strip() + "\n"


def _find(nb, prefix: str) -> int:
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] == "code" and "".join(c.get("source", [])).startswith(prefix):
            return i
    raise KeyError(prefix)


def _patch_panel(nb) -> None:
    idx = _find(nb, "def first_series")
    src = "".join(nb["cells"][idx]["source"])
    marker = "def rebuild_panel"
    if marker not in src:
        raise ValueError("panel cell missing rebuild_panel")
    head = src.split(marker)[0]
    tail = (SCRIPTS / "explosive_nb_panel.py").read_text()
    nb["cells"][idx]["source"] = [head + tail + "\n"]


def _patch_cell1(nb) -> None:
    idx = _find(nb, "import pickle")
    src = "".join(nb["cells"][idx]["source"])
    if "from tqdm.auto import tqdm" not in src:
        insert = (
            "\nif importlib.util.find_spec(\"tqdm\") is None:\n"
            "    subprocess.check_call([sys.executable, \"-m\", \"pip\", \"install\", \"-q\", \"tqdm\"])\n"
            "from tqdm.auto import tqdm\n"
        )
        marker = "warnings.filterwarnings(\"ignore\")"
        src = src.replace(marker, marker + insert)
    if "tqdm((\"panel\"" not in src:
        src = src.replace(
            "for sub in (\"panel\", \"events\", \"features\", \"models\", \"reports\", \"reports/figures\", \"scanner\"):",
            "for sub in tqdm((\"panel\", \"events\", \"features\", \"models\", \"reports\", \"reports/figures\", \"scanner\"), desc=\"Setup — output dirs\"):",
        )
    artifact_imports = (
        "    LABEL_ENGINE_VERSION,\n"
        "    fingerprint_dict,\n"
        "    force_rerun_phase,\n"
        "    phase_cache_hit,\n"
        "    write_phase_meta,\n"
        "    read_meta_fingerprint,\n"
        "    label_cfg_fingerprint,\n"
        "    file_fingerprint,\n"
        "    label_symbol_panel,\n"
        "    purge_overlaps_calendar,\n"
    )
    if "phase_cache_hit" not in src:
        src = src.replace(
            "    summarize_daily_metrics,\n    purge_overlaps_calendar,\n)",
            "    summarize_daily_metrics,\n" + artifact_imports + ")",
        )
        src = src.replace(
            "    summarize_daily_metrics,\n)",
            "    summarize_daily_metrics,\n" + artifact_imports + ")",
        )
    if "META_DIR" not in src:
        extra = (SCRIPTS / "explosive_nb_cell1_extra.py").read_text()
        anchor = 'print("NIFTY:", NIFTY50_PATH, "exists" if NIFTY50_PATH.exists() else "missing")'
        src = src.replace(anchor, anchor + "\n\n" + extra)
    for name in ("label_symbol_panel",):
        if name not in src:
            src = src.replace(
                "    LABEL_ENGINE_VERSION,\n    purge_overlaps_calendar,\n)",
                f"    LABEL_ENGINE_VERSION,\n    {name},\n    purge_overlaps_calendar,\n)",
            )
    nb["cells"][idx]["source"] = [src]


def _patch_cell2(nb) -> None:
    idx = _find(nb, "required = [")
    src = "".join(nb["cells"][idx]["source"])
    if '"tqdm"' not in src:
        src = src.replace(
            'required = ["pandas", "numpy", "pyarrow", "duckdb", "matplotlib", "scipy"]',
            'required = ["pandas", "numpy", "pyarrow", "duckdb", "matplotlib", "scipy", "tqdm"]',
        )
    if "from tqdm.auto import tqdm" not in src:
        src = src.replace("from scipy import stats", "from scipy import stats\nfrom tqdm.auto import tqdm")
    nb["cells"][idx]["source"] = [src]


def _patch_cell4(nb) -> None:
    cell4 = (SCRIPTS / "explosive_nb_setup_cells.py").read_text()
    start = cell4.index('CELL4_SOURCE = """') + len('CELL4_SOURCE = """')
    end = cell4.index('""".strip()', start)
    try:
        idx = _find(nb, "with tqdm(total=1, desc=\"Phase 0")
    except KeyError:
        idx = _find(nb, "CFG = {")
    nb["cells"][idx]["source"] = [cell4[start:end].strip() + "\n"]


def _patch_markdown(nb) -> None:
    for c in nb["cells"]:
        if c["cell_type"] != "markdown":
            continue
        s = "".join(c.get("source", []))
        if "master research notebook" not in s or "**Resume:**" in s:
            continue
        s += (
            "\n\n**Resume:** each phase skips heavy work when parquet/csv artifacts and "
            "`results/explosive_moves/meta/*_meta.json` fingerprints match. "
            "Force rebuild: `CFG[\"rerun_all\"] = True` or `CFG[\"force_rerun\"] = [\"panel\", \"events\", ...]` "
            '(see cell 4).\n'
        )
        c["source"] = [s]


def main() -> None:
    nb = json.loads(NB.read_text())
    _patch_markdown(nb)
    _patch_cell1(nb)
    _patch_cell2(nb)
    _patch_cell4(nb)
    nb["cells"][_find(nb, "def stock_parquet_glob")]["source"] = [AUDIT_CODE]
    _patch_panel(nb)

    replacements = {
        "import os\nfrom concurrent.futures import ProcessPoolExecutor": "explosive_nb_events.py",
        "def compute_features": "explosive_nb_phase5.py",
        "def compute_panel_features": "explosive_nb_phase5.py",
        "_pre_fp = fingerprint_dict": "explosive_nb_pre_event.py",
        "rng = np.random.default_rng": "explosive_nb_pre_event.py",
        "FAMILIES = {": "explosive_nb_helpers.py",
        "_ml_fp = fingerprint_dict": "explosive_nb_ml.py",
        "from sklearn.isotonic import IsotonicRegression": "explosive_nb_ml.py",
        "def split_masks(df):": "explosive_nb_ml.py",
        "def transaction_cost": "explosive_nb_sim.py",
        "_scan_fp = fingerprint_dict": "explosive_nb_scan.py",
        'with tqdm(total=4, desc="Phase 9 — scanner")': "explosive_nb_scan.py",
        "latest_date = panel_features": "explosive_nb_scan.py",
        "latest_date = features": "explosive_nb_scan.py",
    }
    for prefix, fname in replacements.items():
        path = SCRIPTS / fname
        if not path.exists():
            continue
        try:
            idx = _find(nb, prefix)
        except KeyError:
            continue
        nb["cells"][idx]["source"] = [path.read_text() + "\n"]

    NB.write_text(json.dumps(nb, indent=1))
    print("updated", NB)
    _run_explosive_tests()


def _run_explosive_tests() -> None:
    repo = NB.resolve().parents[1]
    py = repo / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    cmd = [
        str(py),
        "-m",
        "pytest",
        "tests/unit/test_explosive_research.py",
        "tests/unit/test_explosive_notebook_contract.py",
        "-q",
    ]
    print("running", " ".join(cmd))
    subprocess.run(cmd, cwd=repo, check=True)


if __name__ == "__main__":
    main()