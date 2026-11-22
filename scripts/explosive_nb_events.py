import os
from concurrent.futures import ProcessPoolExecutor, as_completed

from quant.research.explosive_research import explosive_mfe_threshold


def add_labels(df, cfg):
    df = df.copy()
    H = int(cfg["primary_horizon"])
    thr = explosive_mfe_threshold(cfg)
    mfe = df[f"mfe_{H}"]
    ok = mfe.notna() & df[f"horizon_complete_{H}"] & ~df[f"ca_fwd_invalid_{H}"]
    df[f"y_explosive_{H}"] = ((mfe >= thr) & ok).astype("int8")
    return df


TRADING_CAL = trading_calendar_from_panel(panel)
_events_fp = fingerprint_dict(
    {
        "panel": read_meta_fingerprint(PANEL_META_PATH),
        "labels": label_cfg_fingerprint(CFG),
        "symbol_continuity": CFG.get("symbol_continuity"),
    }
)

if phase_cache_hit(CFG, "events", [EVENTS_PATH], EVENTS_META_PATH, _events_fp):
    events = pd.read_parquet(EVENTS_PATH)
    print("Loaded events:", len(events), EVENTS_PATH)
else:
    sym_groups = [(sym, g) for sym, g in panel.groupby("Symbol", observed=True)]
    min_len = CFG["min_history_bars"] + CFG["h_max"] + 1
    sym_groups = [(s, g) for s, g in sym_groups if len(g) >= min_len]

    tb = CFG["triple_barrier"]
    horizons = CFG["horizons"]
    ca_gap = CFG["ca_gap_abs_exclude"]
    workers = int(CFG.get("parallel_workers", 0))
    if workers <= 0:
        workers = min(4, os.cpu_count() or 1)

    def _label_one(pair):
        _, gsym = pair
        return label_symbol_panel(
            gsym, TRADING_CAL, horizons, ca_gap_abs=ca_gap, tb_up=tb["up"], tb_down=tb["down"]
        )

    labeled_parts = []
    if workers > 1 and len(sym_groups) > workers * 2:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_label_one, p) for p in sym_groups]
            for fut in tqdm(as_completed(futs), total=len(futs), desc="Phase 3 — label symbols", unit="sym"):
                labeled_parts.append(fut.result())
    else:
        for pair in tqdm(sym_groups, desc="Phase 3 — label symbols", unit="sym"):
            labeled_parts.append(_label_one(pair))

    with tqdm(total=2, desc="Phase 3 — finalize events") as bar:
        labeled = pd.concat(labeled_parts, ignore_index=True)
        bar.update(1)
        labeled = add_labels(labeled, CFG)
        bar.update(1)

    H = CFG["primary_horizon"]
    labeled["ca_excluded"] = labeled["ca_suspect"].astype("int8")

    print("Explosive label: MFE >=", explosive_mfe_threshold(CFG), f"within {H} forward sessions (window only)")
    print("Incomplete horizons:", int((~labeled[f"horizon_complete_{CFG['h_max']}"]).sum()))
    print("CA-forward invalid:", int(labeled[f"ca_fwd_invalid_{CFG['h_max']}"].sum()))

    events = labeled.dropna(subset=[f"mfe_{CFG['primary_horizon']}"])
    events.to_parquet(EVENTS_PATH, compression="zstd", index=False)
    write_phase_meta(EVENTS_META_PATH, "events", _events_fp, n_rows=len(events))
    print("Wrote", EVENTS_PATH, len(events))