"""Quick search: regime-filtered 12-1 momentum on monthly MCP panel."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

MCP = "https://mcp.marketsenseanalytics.com"
SYMBOLS = [
    "TRENT", "ASIANPAINT", "TCS", "PERSISTENT", "ICICIBANK", "MARUTI", "HDFCBANK",
    "BAJFINANCE", "TATASTEEL", "HINDALCO", "TITAN", "INFY", "ONGC", "JSWSTEEL",
    "BEL", "POLYCAB", "RELIANCE", "COALINDIA", "BSE", "DIXON",
]
BENCH = "NIFTYBEES"


def fetch(sym: str) -> pd.DataFrame:
    bars = json.loads(subprocess.check_output(["curl", "-sS", f"{MCP}/api/monthly/{sym}"], text=True))[
        "bars"
    ]
    df = pd.DataFrame(bars)
    df["Date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df["Symbol"] = sym
    return df[["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]]


def summarize(eq: pd.Series) -> tuple[float, float, float]:
    eq = eq.dropna()
    ic, fc = float(eq.iloc[0]), float(eq.iloc[-1])
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 12)
    cagr = ((fc / ic) ** (1 / years) - 1) * 100
    dd = float((eq / eq.cummax() - 1).min() * 100)
    tot = (fc / ic - 1) * 100
    return cagr, tot, dd


def backtest(px: pd.DataFrame, bench_px: pd.Series, cfg: dict) -> tuple[float, float, float]:
    top_k = int(cfg["top_k"])
    mom_lb = int(cfg["mom_lb"])
    skip = int(cfg.get("skip", 1))
    sma_lb = int(cfg.get("sma_lb", 10))
    abs_mom_min = float(cfg.get("abs_mom_min", 0.0))
    max_gross = float(cfg.get("max_gross", 1.0))
    max_w = float(cfg.get("max_w", 0.15))
    dd_cut = cfg.get("dd_cut")
    dd_soft = cfg.get("dd_soft")
    cash_yield = float(cfg.get("cash_monthly", 0.005))

    cash = 1.0
    shares: dict[str, float] = {}
    peak = 1.0
    eq_dates: list[pd.Timestamp] = []
    eq_vals: list[float] = []

    idx = list(px.index)
    for i, m in enumerate(idx):
        cl = px.loc[m]
        if i < mom_lb + skip:
            eq_dates.append(m)
            eq_vals.append(cash)
            continue

        bpx = float(bench_px.loc[m]) if pd.notna(bench_px.loc[m]) else np.nan
        bhist = bench_px.loc[:m].iloc[-sma_lb:]
        b_mom = float(bench_px.loc[m] / bench_px.shift(mom_lb).loc[m] - 1) if i >= mom_lb else -1.0
        risk_on = pd.notna(bpx) and bpx >= float(bhist.mean()) and b_mom >= abs_mom_min

        port = cash + sum(
            sh * float(cl[sym])
            for sym, sh in shares.items()
            if sym in cl.index and pd.notna(cl[sym])
        )
        peak = max(peak, port)
        dd_now = port / peak - 1
        scale = 1.0
        if dd_soft is not None and dd_now <= dd_soft:
            scale = min(scale, 0.5)
        if dd_cut is not None and dd_now <= dd_cut:
            for sym, sh in list(shares.items()):
                if sym in cl.index and pd.notna(cl[sym]):
                    cash += sh * float(cl[sym])
            shares.clear()
            risk_on = False
            scale = 0.0

        if i > mom_lb + skip and risk_on and scale > 0:
            mom: dict[str, float] = {}
            for sym in cl.dropna().index:
                p0 = px[sym].shift(mom_lb + skip).loc[m]
                p1 = px[sym].shift(skip).loc[m]
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    mom[str(sym)] = float(p1 / p0 - 1)
            if mom:
                ranked = sorted(mom, key=mom.get, reverse=True)[:top_k]
                target_w: dict[str, float] = {}
                for sym in ranked:
                    target_w[sym] = min(1.0 / len(ranked), max_w)
                tw = sum(target_w.values())
                if tw > 0:
                    target_w = {s: v / tw for s, v in target_w.items()}

                for sym, sh in list(shares.items()):
                    if sym in cl.index and pd.notna(cl[sym]):
                        cash += sh * float(cl[sym])
                shares.clear()

                gross = max_gross * scale
                invest = port * gross
                for sym, w in target_w.items():
                    price = float(px[sym].loc[m])
                    if not np.isfinite(price) or price <= 0:
                        continue
                    alloc = invest * w
                    shares[sym] = alloc / price
                    cash -= alloc
        elif not risk_on:
            for sym, sh in list(shares.items()):
                if sym in cl.index and pd.notna(cl[sym]):
                    cash += sh * float(cl[sym])
            shares.clear()
            cash *= 1 + cash_yield

        port = cash + sum(
            sh * float(cl[sym])
            for sym, sh in shares.items()
            if sym in cl.index and pd.notna(cl[sym])
        )
        eq_dates.append(m)
        eq_vals.append(port)

    eq = pd.Series(eq_vals, index=pd.to_datetime(eq_dates))
    return summarize(eq)


def main() -> None:
    panel = pd.concat([fetch(s) for s in SYMBOLS], ignore_index=True)
    bench = fetch(BENCH).sort_values("Date")
    px = panel.pivot(index="Date", columns="Symbol", values="Close").sort_index()
    bench_px = bench.set_index("Date")["Close"].reindex(px.index).ffill()
    rets = px.pct_change()
    ew = (1 + rets.mean(axis=1, skipna=True)).cumprod()
    print("EW B&H", summarize(ew))

    best: list[tuple[float, float, float, dict]] = []
    for top_k in (3, 5, 8):
        for mom_lb in (6, 9, 12):
            for sma_lb in (8, 10, 12):
                for abs_mom in (0.0, 0.03, 0.05):
                    for max_gross in (0.5, 0.7, 1.0):
                        for max_w in (0.12, 0.18, 0.25):
                            for dd_cut in (None, -0.10, -0.12):
                                for dd_soft in (None, -0.07):
                                    cfg = dict(
                                        top_k=top_k,
                                        mom_lb=mom_lb,
                                        sma_lb=sma_lb,
                                        abs_mom_min=abs_mom,
                                        max_gross=max_gross,
                                        max_w=max_w,
                                        dd_cut=dd_cut,
                                        dd_soft=dd_soft,
                                    )
                                    cagr, tot, dd = backtest(px, bench_px, cfg)
                                    if dd >= -10.5 and cagr >= 18:
                                        best.append((cagr, dd, tot, cfg))

    best.sort(key=lambda x: -x[0])
    print("feasible (CAGR>=18, DD>=-10.5):", len(best))
    for row in best[:15]:
        print(f"CAGR {row[0]:5.1f}% DD {row[1]:5.1f}% total {row[2]:7.0f}% {row[3]}")


if __name__ == "__main__":
    main()