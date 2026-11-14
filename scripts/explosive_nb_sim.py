def transaction_cost(notional, side, cfg):
    c = cfg["costs"]
    brokerage = notional * c["brokerage_bps"] / 10000
    stt = notional * c["stt_bps"] / 10000 if side == "SELL" else 0
    exchange = notional * c["exchange_bps"] / 10000
    stamp = notional * c["stamp_bps"] / 10000 if side == "BUY" else 0
    sebi = notional * c["sebi_bps"] / 10000
    gst = (brokerage + exchange + sebi) * c["gst_rate"]
    return brokerage + stt + exchange + stamp + sebi + gst


def execution_price(px, side, cfg):
    slip = cfg["costs"]["slippage_bps"] / 10000
    return px * (1 + slip) if side == "BUY" else px * (1 - slip)


def sessions_held(entry_date, current_date, calendar):
    i0 = calendar.get_indexer([pd.Timestamp(entry_date)])[0]
    i1 = calendar.get_indexer([pd.Timestamp(current_date)])[0]
    if i0 < 0 or i1 < 0:
        return 0
    return max(0, i1 - i0)


def simulate_topk_calendar(features_df, panel_df, scores, top_k, cfg, calendar):
    st = cfg["strategy"]
    f = features_df.copy()
    f["score"] = scores
    f["Date"] = pd.to_datetime(f["Date"])
    dmin, dmax = f["Date"].min(), f["Date"].max()
    dates = [d for d in calendar if dmin <= d <= dmax]
    if len(dates) < 2:
        return pd.DataFrame(), {}

    cash = st["initial_capital"]
    positions = {}
    equity = []
    trades = 0

    for i in tqdm(range(len(dates) - 1), desc=f"Sim top-{top_k}", leave=False):
        d, nxt = dates[i], dates[i + 1]
        panel_nxt = panel_df.loc[panel_df["Date"] == nxt].set_index("Symbol")

        for sym in list(positions):
            pos = positions[sym]
            if sym not in panel_nxt.index:
                continue
            row = panel_nxt.loc[sym]
            opn, lo = float(row["Open"]), float(row["Low"])
            held = sessions_held(pos["entry_date"], nxt, calendar)
            stop = pos["entry_px"] * (1 - st["stop_loss"])
            sell = False
            if np.isfinite(lo) and lo <= stop:
                sell = True
                raw = min(opn, stop)
            elif held >= st["max_hold"]:
                sell = True
                raw = opn
            if sell and np.isfinite(raw) and raw > 0:
                px = execution_price(raw, "SELL", cfg)
                cash += pos["shares"] * px - transaction_cost(pos["shares"] * px, "SELL", cfg)
                del positions[sym]
                trades += 1

        day = f.loc[f["Date"] == d].nlargest(top_k, "score")
        slots = top_k - len(positions)
        for _, r in day.iterrows():
            if slots <= 0:
                break
            sym = r["Symbol"]
            if sym in positions:
                continue
            if sym not in panel_nxt.index:
                continue
            nxt_row = panel_nxt.loc[sym]
            opn = float(nxt_row["Open"])
            prev_c = float(r["Close"])
            if not np.isfinite(opn) or opn <= 0:
                continue
            if prev_c > 0 and opn >= prev_c * 1.195:
                continue
            vol = float(nxt_row["Volume"]) if np.isfinite(nxt_row["Volume"]) else 0
            max_notional = vol * opn * st["max_participation_pct"]
            alloc = cash / max(slots, 1)
            notional = min(alloc, max_notional) if max_notional > 0 else alloc
            px = execution_price(opn, "BUY", cfg)
            shares = notional / px
            cost = transaction_cost(notional, "BUY", cfg)
            if cash < notional + cost:
                continue
            cash -= notional + cost
            positions[sym] = {"entry_px": px, "shares": shares, "entry_date": nxt, "prev_close": prev_c}
            slots -= 1
            trades += 1

        mtm = cash
        for sym, pos in positions.items():
            if sym in panel_nxt.index:
                mtm += pos["shares"] * float(panel_nxt.loc[sym, "Close"])
        equity.append({"Date": nxt, "equity": mtm, "n_pos": len(positions)})

    eq = pd.DataFrame(equity)
    if eq.empty:
        return eq, {}
    peak = eq["equity"].cummax()
    dd = (eq["equity"] / peak - 1).min()
    ret = eq["equity"].iloc[-1] / st["initial_capital"] - 1
    rets = eq["equity"].pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 1 and rets.std() > 0 else np.nan
    stats_out = {
        "total_return": float(ret),
        "max_drawdown": float(dd),
        "sharpe_daily_ann": sharpe,
        "n_trades": trades,
        "n_sessions": len(eq),
    }
    return eq, stats_out


if "lgbm" in models and calibrator is not None:
    test = features.loc[test2_m | holdout_m].copy()
    Xte = test[Xcols].replace([np.inf, -np.inf], np.nan).fillna(0)
    test_scores = calibrator.predict(models["lgbm"].predict_proba(Xte)[:, 1])
    for k in tqdm(CFG["strategy"]["top_k"], desc="Phase 8 — backtest"):
        eq, stout = simulate_topk_calendar(test, panel, test_scores, k, CFG, TRADING_CAL)
        if not eq.empty:
            print(f"Top-{k} sim:", {kk: (f"{vv:.2%}" if kk == "total_return" else vv) for kk, vv in stout.items()})
            eq.to_parquet(OUT_DIR / "reports" / f"equity_top{k}.parquet", index=False)
            Path(OUT_DIR / "reports" / f"equity_top{k}_stats.json").write_text(json.dumps(stout, indent=2))
else:
    print("Calibrated LightGBM unavailable; skip strategy simulation.")