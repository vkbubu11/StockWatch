"""Teljesitmeny-mutatok napi tokegorbebol: CAGR, volatilitas, Sharpe, Sortino, max. visszaeses, Calmar.

Kozos modul (a Backteszt oldal hasznalja eloszor; ticket #7 szerint a kutatas, a virtualis
kovetes es a paper szamla is erre all at). Kockazatmentes hozam: 0 (egyszeru, osszevetheto).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(equity: pd.Series) -> pd.Series:
    """Visszaeses a mindenkori csucstol, negativ szazalekban (0 = uj csucs)."""
    peak = equity.cummax()
    return (equity / peak - 1) * 100


def metrics(equity: pd.Series, exposure: pd.Series | None = None) -> dict:
    """Mutatok egy napi tokegorbere (barmilyen kezdoertekkel). `exposure`: napi piacon-lét (0/1)."""
    equity = equity.dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {"cagr": None, "total": None}
    ret = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
    vol = float(ret.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(ret) > 1 else None
    downside = ret[ret < 0]
    down_vol = float(math.sqrt((downside ** 2).sum() / len(ret)) * math.sqrt(TRADING_DAYS)) if len(ret) else None
    mean = float(ret.mean() * TRADING_DAYS)
    dd = drawdown(equity)
    max_dd = float(dd.min())
    # a leghosszabb ido csucs alatt (naptari nap)
    under, longest, start = dd < 0, 0, None
    for day, flag in under.items():
        if flag and start is None:
            start = day
        elif not flag and start is not None:
            longest, start = max(longest, (day - start).days), None
    if start is not None:
        longest = max(longest, (equity.index[-1] - start).days)
    return {
        "total": float(total * 100), "cagr": float(cagr * 100),
        "vol": vol * 100 if vol is not None else None,
        "sharpe": mean / vol if vol else None,
        "sortino": mean / down_vol if down_vol else None,
        "max_dd": max_dd, "max_dd_days": int(longest),
        "calmar": float(cagr * 100 / -max_dd) if max_dd < 0 else None,
        "exposure": float(exposure.reindex(equity.index).fillna(0).mean() * 100) if exposure is not None else None,
        "years": float(years),
    }


def yearly_returns(equity: pd.Series) -> dict[int, float]:
    """Naptari evenkenti hozam %-ban (az elso ev a kezdonaptol)."""
    equity = equity.dropna()
    if equity.empty:
        return {}
    out, prev = {}, float(equity.iloc[0])
    for year, chunk in equity.groupby(equity.index.year):
        last = float(chunk.iloc[-1])
        out[int(year)] = (last / prev - 1) * 100
        prev = last
    return out


def downsample(series: pd.Series, max_points: int = 1500) -> pd.Series:
    """A chartra: legfeljebb `max_points` pont (az utolso mindig benne marad)."""
    if len(series) <= max_points:
        return series
    step = int(np.ceil(len(series) / max_points))
    picked = series.iloc[::step]
    return picked if picked.index[-1] == series.index[-1] else pd.concat([picked, series.iloc[-1:]])
