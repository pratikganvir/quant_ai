#!/usr/bin/env python3
"""Apply second-review fixes to explosive_moves_research.ipynb."""
from __future__ import annotations

import json
from pathlib import Path

NB = Path(__file__).resolve().parents[1] / "strategies" / "explosive_moves_research.ipynb"
SCRIPTS = Path(__file__).resolve().parent

AUDIT_CODE = """
def stock_parquet_glob():
    return sorted(
        p for p in DATA_DIR.rglob("nse_cm_*.parquet")
        if "results" not in p.parts and "indices" not in p.parts
    )

files = stock_parquet_glob()
print("Daily stock files:", len(files))
if not files:
    raise FileNotFoundError(f"No nse_cm_*.parquet under {DATA_DIR}")

schema_union = set()
for p in [files[0], files[len(files) // 2], files[-1]]:
    schema_union |= {c.lower() for c in pq.ParquetFile(p).schema.names}

con = duckdb.connect()
paths = [str(p).replace("'", "''") for p in files]
paths_sql = ", ".join(f"'{p}'" for p in paths)
audit_df = con.execute(
    f'''
    SELECT COUNT(*) AS n_rows,
           COUNT(DISTINCT symbol) AS n_symbols,
           MIN(CAST(date AS DATE)) AS dmin,
           MAX(CAST(date AS DATE)) AS dmax
    FROM read_parquet([{paths_sql}], union_by_name=true)
    '''
).df()
dup_df = con.execute(
    f'''
    SELECT date, symbol, COUNT(*) AS n
    FROM read_parquet([{paths_sql}], union_by_name=true)
    GROUP BY 1,2 HAVING n>1 LIMIT 10
    '''
).df()

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


def main() -> None:
    nb = json.loads(NB.read_text())
    nb["cells"][_find(nb, "def stock_parquet_glob")]["source"] = [AUDIT_CODE]
    _patch_panel(nb)

    replacements = {
        "def compute_features": "explosive_nb_phase5.py",
        "def compute_panel_features": "explosive_nb_phase5.py",
        "rng = np.random.default_rng": "explosive_nb_pre_event.py",
        "FAMILIES = {": "explosive_nb_helpers.py",
        "def split_masks(df):": "explosive_nb_ml.py",
        "def transaction_cost": "explosive_nb_sim.py",
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


if __name__ == "__main__":
    main()