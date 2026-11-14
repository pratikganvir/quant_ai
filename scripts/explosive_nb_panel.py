PANEL_META_PATH = OUT_DIR / "panel" / "panel_build_meta.json"


def panel_source_fingerprint(file_paths):
    if not file_paths:
        return ""
    mtimes = sorted((p.stat().st_mtime, str(p)) for p in file_paths)
    return json.dumps({"n_files": len(file_paths), "first": mtimes[0][1], "last": mtimes[-1][1], "sum_mtime": sum(m for m, _ in mtimes)})


def rebuild_panel(force=False):
    files = stock_parquet_glob()
    fp = panel_source_fingerprint(files)
    meta = {}
    if PANEL_META_PATH.exists():
        meta = json.loads(PANEL_META_PATH.read_text())
    if PANEL_PATH.exists() and not force and meta.get("fingerprint") == fp:
        print("Using existing panel (fingerprint match):", PANEL_PATH)
        return
    writer = None
    n_ok = n_err = 0
    for i, f in enumerate(tqdm(files, desc="Phase 2 — stitch panel", unit="file"), 1):
        try:
            x = normalize_ohlc(pd.read_parquet(f), f.name)
            keep = [c for c in BASE_COLS + PANEL_EXTRA if c in x.columns]
            table = pa.Table.from_pandas(x[keep], preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(PANEL_PATH, table.schema, compression="zstd")
            writer.write_table(table)
            n_ok += 1
            if i % 400 == 0:
                gc.collect()
        except Exception as exc:
            n_err += 1
            if n_err <= 5:
                print("skip", f.name, exc)
    if writer:
        writer.close()
    PANEL_META_PATH.write_text(json.dumps({"fingerprint": fp, "n_files": len(files), "n_ok": n_ok, "n_err": n_err}, indent=2))
    print("Panel OK:", n_ok, "errors:", n_err)


with tqdm(total=2, desc="Phase 2 — panel load") as pbar:
    rebuild_panel(force=False)
    pbar.update(1)
    panel = pd.read_parquet(PANEL_PATH)
    panel["Date"] = pd.to_datetime(panel["Date"])
    panel["Symbol"] = panel["Symbol"].astype("string")
    panel = panel.sort_values(["Symbol", "Date"])
    n_dup = int(panel.duplicated(["Date", "Symbol"]).sum())
    if n_dup:
        print("Dropping duplicate Date-Symbol rows:", n_dup)
        panel = panel.drop_duplicates(["Date", "Symbol"], keep="last")
    panel = panel.reset_index(drop=True)

    if "Turnover" not in panel.columns:
        panel["Turnover"] = (panel["Close"] * panel["Volume"]).astype("float32")

    if CFG["eq_series_only"] and "Series" in panel.columns:
        bad = panel["Series"].notna() & ~panel["Series"].str.upper().eq("EQ")
        panel = panel.loc[~bad]

    nifty = None
    if NIFTY50_PATH.exists():
        nifty = normalize_ohlc(pd.read_parquet(NIFTY50_PATH), "NIFTY50")
        nifty = nifty.sort_values("Date")
        nifty["nifty_ret_1"] = nifty["Close"].pct_change()

    print(panel.shape, panel["Date"].min(), "→", panel["Date"].max(), "symbols:", panel["Symbol"].nunique())
    pbar.update(1)