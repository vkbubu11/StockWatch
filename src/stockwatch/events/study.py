"""Esemenytanulmany - a modszertan.

  * hozam: korrigalt zaroarbol szamolt napi hozam (osztalek- es split-korrigalt)
  * piaci modell: OLS a becslesi ablakon (alap: -260 ... -21. nap), legalabb MIN_ESTIMATION megfigyelessel;
    kevesebbnel piac-korrigalt hozam (R - R_piac), es az esemeny jelolve van
  * AR = R - (alfa + beta * R_piac) az esemenyablakban (alap: -10 ... +60. nap), CAR = az AR osszege
  * a becslesi ablak az esemenyablak ELOTT ER VEGET - a 0. nap utani ar nem hat az alfara / betara
  * atfedes: ugyanannak a papirnak az ablakon belul eso kesobbi esemenye kimarad (szamolva)
  * osszesites: atlagos es median palya; DATUM SZERINT CSOPORTOSITOTT bootstrap-sav (az egy napra eso
    esemenyek - pl. egy gyorsjelentes-het - egyutt mozognak); talalati arany (CAR > 0) Wilson-intervallummal;
    bontas iranyra, szektorra, piaci rezsimre es evre
  * ket CAR: a teljes ablak, es a "+1. naptol" (a 0. nap zaroja utan) - a 0. napi reakcio, es a 0. napi
    adatbol kepzett besorolas (pl. gap-irany) igy nem keveredik bele
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..entry_research import wilson

MIN_ESTIMATION = 120
N_BOOT = 2000
SEED = 7
REGIME_SMA = 200


@dataclass
class Outcome:
    symbol: str
    day: pd.Timestamp
    ar: np.ndarray                       # az esemenyablak napi abnormalis hozamai (tort, nem %)
    model: str                           # market | adjusted
    est_n: int
    alpha: float
    beta: float
    attrs: dict = field(default_factory=dict)
    exact: bool = True
    groups: dict = field(default_factory=dict)


def returns(close: pd.Series) -> pd.Series:
    return close.astype(float).pct_change()


def abnormal(stock: pd.Series, bench: pd.Series, day: pd.Timestamp, window: tuple[int, int],
             estimation: tuple[int, int]) -> tuple[np.ndarray, str, int, float, float] | None:
    """Egy esemeny abnormalis hozamai. `stock`, `bench`: zaroar a papir naptaran (a benchmark ahhoz igazitva).
    None, ha az ablak nem fer bele az adatba (a vegen: meg nem telt le, vagy a vizsgalt idoszak vegen tul)."""
    a, b = window
    e0, e1 = estimation
    if e1 >= a:
        raise ValueError("A becslesi ablak az esemenyablak elott erjen veget.")
    idx = stock.index
    if day not in idx:
        return None
    i = idx.get_loc(day)
    if i + a < 1 or i + b >= len(idx):
        return None
    r, rm = returns(stock).to_numpy(), returns(bench).to_numpy()
    lo, hi = max(i + e0, 1), i + e1
    est_r, est_m = r[lo:hi + 1], rm[lo:hi + 1]
    ok = ~(np.isnan(est_r) | np.isnan(est_m))
    est_r, est_m = est_r[ok], est_m[ok]
    win_r, win_m = r[i + a:i + b + 1], rm[i + a:i + b + 1]
    if np.isnan(win_r).any() or np.isnan(win_m).any():
        return None
    if len(est_r) >= MIN_ESTIMATION and np.var(est_m) > 0:
        beta = float(np.cov(est_r, est_m, ddof=1)[0, 1] / np.var(est_m, ddof=1))
        alpha = float(est_r.mean() - beta * est_m.mean())
        model = "market"
    else:
        alpha, beta, model = 0.0, 1.0, "adjusted"
    return win_r - (alpha + beta * win_m), model, int(len(est_r)), alpha, beta


def drop_overlaps(outcomes: list[Outcome], index_of: dict[str, pd.DatetimeIndex], window: tuple[int, int]) -> tuple[list[Outcome], int]:
    """Ugyanannak a papirnak az elozo (megtartott) esemeny ablakaba eso esemenye kimarad."""
    a, b = window
    kept, dropped = [], 0
    last: dict[str, int] = {}
    for o in sorted(outcomes, key=lambda o: (o.symbol, o.day)):
        pos = index_of[o.symbol].get_loc(o.day)
        if o.symbol in last and pos + a <= last[o.symbol] + b:
            dropped += 1
            continue
        kept.append(o)
        last[o.symbol] = pos
    return kept, dropped


def regime_on(bench: pd.Series, day: pd.Timestamp) -> str:
    """A piaci rezsim a 0. nap ELOTTI zaron (benchmark > SMA200)."""
    before = bench.loc[:day].iloc[:-1]
    if len(before) < REGIME_SMA:
        return "unknown"
    return "bull" if before.iloc[-1] > before.iloc[-REGIME_SMA:].mean() else "bear"


def _cluster_bootstrap(paths: np.ndarray, clusters: np.ndarray, n_boot: int, seed: int) -> np.ndarray:
    """A datum szerint csoportositott bootstrap atlagpalyai: (n_boot x T)."""
    keys, inverse = np.unique(clusters, return_inverse=True)
    sums = np.zeros((len(keys), paths.shape[1]))
    np.add.at(sums, inverse, paths)
    counts = np.bincount(inverse, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    out = np.empty((n_boot, paths.shape[1]))
    for k in range(n_boot):
        pick = rng.integers(0, len(keys), size=len(keys))
        out[k] = sums[pick].sum(axis=0) / counts[pick].sum()
    return out


def _summary(car: np.ndarray, boot_last: np.ndarray | None) -> dict:
    if car.size == 0:
        return {"n": 0, "mean": None, "median": None, "hit": None, "hit_lo": None, "hit_hi": None,
                "ci_lo": None, "ci_hi": None}
    hits = int((car > 0).sum())
    lo, hi = wilson(hits, int(car.size))
    return {"n": int(car.size), "mean": float(car.mean() * 100), "median": float(np.median(car) * 100),
            "hit": hits / car.size, "hit_lo": lo, "hit_hi": hi,
            "ci_lo": float(np.percentile(boot_last, 2.5) * 100) if boot_last is not None else None,
            "ci_hi": float(np.percentile(boot_last, 97.5) * 100) if boot_last is not None else None}


def aggregate(outcomes: list[Outcome], window: tuple[int, int], n_boot: int = N_BOOT, seed: int = SEED,
              group_keys: tuple[str, ...] = ("direction", "sector", "regime", "year")) -> dict:
    a, b = window
    days = list(range(a, b + 1))
    if not outcomes:
        return {"days": days, "n": 0, "path": None, "groups": {}, "summary": _summary(np.array([]), None),
                "post": _summary(np.array([]), None), "histogram": None}
    ar = np.vstack([o.ar for o in outcomes])
    paths = np.cumsum(ar, axis=1)
    post_from = max(0, 1 - a)                       # a +1. nap oszlopa
    car, post = paths[:, -1], ar[:, post_from:].sum(axis=1) if post_from < ar.shape[1] else np.zeros(len(ar))
    clusters = np.array([f"{o.day:%Y-%m-%d}" for o in outcomes])
    boot = _cluster_bootstrap(paths, clusters, n_boot, seed) if len(outcomes) >= 2 else None
    boot_post = _cluster_bootstrap(ar[:, post_from:].cumsum(axis=1), clusters, n_boot, seed)[:, -1] \
        if len(outcomes) >= 2 and post_from < ar.shape[1] else None
    lo_edge, hi_edge = np.percentile(car * 100, [1, 99])
    edges = np.linspace(lo_edge, hi_edge, 21) if hi_edge > lo_edge else np.linspace(lo_edge - 1, hi_edge + 1, 21)
    counts, _ = np.histogram(np.clip(car * 100, edges[0], edges[-1]), bins=edges)
    groups = {}
    for key in group_keys:
        values = [o.groups.get(key) for o in outcomes]
        if all(v is None for v in values):
            continue
        out = {}
        for value in sorted({v for v in values if v is not None}, key=str):
            sel = np.array([v == value for v in values])
            out[str(value)] = {"car": _summary(car[sel], None), "post": _summary(post[sel], None)}
        groups[key] = out
    return {
        "days": days, "n": len(outcomes),
        "path": {"mean": (paths.mean(axis=0) * 100).round(4).tolist(),
                 "median": (np.median(paths, axis=0) * 100).round(4).tolist(),
                 "lo": (np.percentile(boot, 2.5, axis=0) * 100).round(4).tolist() if boot is not None else None,
                 "hi": (np.percentile(boot, 97.5, axis=0) * 100).round(4).tolist() if boot is not None else None},
        "summary": _summary(car, boot[:, -1] if boot is not None else None),
        "post": _summary(post, boot_post),
        "histogram": {"edges": edges.round(3).tolist(), "counts": counts.tolist()},
        "groups": groups,
    }
