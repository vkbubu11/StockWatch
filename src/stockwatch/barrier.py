"""Volatilitas-elorejelzes (HAR-RV) es analitikus celar-valoszinuseg (elso athaladas) a celar-valoszinuseghez.

  * napi variancia napi gyertyabol (intraday adat nincs): ejszakai res^2 + Garman-Klass napkozbeni becsles
    (Yang-Zhang-fele egyszerusites) - a nyitasi reseket is tartalmazza
  * HAR-RV (Corsi 2009): log(atlagos variancia a kovetkezo H napon) = b0 + b1 log(RV_nap) + b2 log(RV_het) +
    b3 log(RV_honap), papirokon atfogo (pooled) OLS, H = 5 / 20 / 60 napra; koztes H-ra log(H) szerint interpolalva
  * elso athaladas: sodrodas nelkuli Brown-mozgas log-aron, napi sigma = sqrt(HAR-elorejelzes):
      - erinti: P(max_{0..T} >= b) = 2 (1 - Phi(b / (sigma sqrt T)))  (tukrozesi elv, zart alak)
      - stoppal (elobb b, mint -c): normalizalt tablazat (z_b, z_c) Monte Carlo-bol, Brown-hid korrekcioval
        (Broadie-Glasserman eltolas), bilinearis interpolacioval
A gepi tanulasi reteg (target_prob) ezekre epul: a tavolsagot a varhato szorashoz merve adja meg, igy a modell
nem fugg attol, milyen volatilitas-rezsimben tanult.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.stats import norm

HAR_H = (5, 20, 60)
EPS = 1e-10


def daily_variance(df: pd.DataFrame) -> pd.Series:
    """Napi log-hozam variancia becslese OHLC-bol: ejszakai res^2 + Garman-Klass (napon beluli)."""
    o, h, l, c = (np.log(df[k]) for k in ("open", "high", "low", "close"))
    gap = (o - c.shift(1)) ** 2
    gk = 0.5 * (h - l) ** 2 - (2 * math.log(2) - 1) * (c - o) ** 2
    return (gap.fillna(0) + gk.clip(lower=0)).clip(lower=EPS)


def har_regressors(v: pd.Series) -> pd.DataFrame:
    """log RV napi / heti (5) / havi (22) - csak a `t` napig."""
    return pd.DataFrame({"har_d": np.log(v), "har_w": np.log(v.rolling(5).mean()), "har_m": np.log(v.rolling(22).mean())})


def har_targets(v: pd.Series) -> pd.DataFrame:
    """JOVOBELI: log(a kovetkezo H nap atlagos varianciaja) - csak a HAR illesztesehez (a t+H-nak a tanito
    idoszakban kell lennie)."""
    return pd.DataFrame({f"_frv{h}": np.log(v.rolling(h).mean().shift(-h)) for h in HAR_H})


def fit_har(x: pd.DataFrame) -> dict[int, np.ndarray]:
    """Pooled OLS horizontonkent: [1, har_d, har_w, har_m] -> _frv{H}; + a reziduum varianciaja (log -> szint)."""
    out = {}
    for h in HAR_H:
        sub = x[["har_d", "har_w", "har_m", f"_frv{h}"]].dropna()
        a = np.column_stack([np.ones(len(sub)), sub[["har_d", "har_w", "har_m"]].to_numpy()])
        coef, *_ = np.linalg.lstsq(a, sub[f"_frv{h}"].to_numpy(), rcond=None)
        resid = sub[f"_frv{h}"].to_numpy() - a @ coef
        out[h] = np.r_[coef, np.var(resid)]
    return out


def har_sigma(x: pd.DataFrame, n: np.ndarray, coef: dict[int, np.ndarray]) -> np.ndarray:
    """A napi sigma elorejelzese az N napos idotavra (log H szerinti interpolacio a 5 / 20 / 60 kozott)."""
    a = np.column_stack([np.ones(len(x)), x[["har_d", "har_w", "har_m"]].to_numpy()])
    preds = {h: a @ coef[h][:4] + 0.5 * coef[h][4] for h in HAR_H}       # log-normal szint-korrekcio
    logn = np.log(np.clip(n, HAR_H[0], HAR_H[-1]))
    lh = np.log(HAR_H)
    out = np.empty(len(x))
    for k in range(len(HAR_H) - 1):
        m = (logn >= lh[k]) & (logn <= lh[k + 1])
        w = (logn[m] - lh[k]) / (lh[k + 1] - lh[k])
        out[m] = (1 - w) * preds[HAR_H[k]][m] + w * preds[HAR_H[k + 1]][m]
    return np.sqrt(np.exp(out))


def touch_prob(z: np.ndarray) -> np.ndarray:
    """P(max >= b) sodrodas nelkul, z = b / (sigma sqrt T) > 0."""
    return np.clip(2 * (1 - norm.cdf(z)), 0, 1)


ZB_GRID = np.r_[np.linspace(0.02, 1.0, 25), np.linspace(1.1, 4.0, 30)]
ZC_GRID = ZB_GRID.copy()


@lru_cache(maxsize=1)
def _table(paths: int = 20000, steps: int = 400, seed: int = 11) -> np.ndarray:
    """P(elobb +zb, mint -zc, T = 1-ig) normalizalt Brown-mozgasra; Monte Carlo, a gatak Broadie-Glasserman
    eltolasaval (a diszkret figyeles alulbecsli a folytonos athaladast)."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / steps
    shift = 0.5826 * math.sqrt(dt)
    w = np.cumsum(rng.normal(0, math.sqrt(dt), (paths, steps)), axis=1)
    run_max, run_min = np.maximum.accumulate(w, axis=1), np.minimum.accumulate(w, axis=1)
    hit_b = np.empty((paths, len(ZB_GRID)), dtype=np.int32)
    hit_c = np.empty((paths, len(ZC_GRID)), dtype=np.int32)
    for p in range(paths):                       # elso athaladas: a futo max / min monoton -> searchsorted
        hit_b[p] = np.searchsorted(run_max[p], ZB_GRID - shift, side="left")
        hit_c[p] = np.searchsorted(-run_min[p], ZC_GRID - shift, side="left")
    table = np.empty((len(ZB_GRID), len(ZC_GRID)))
    for i in range(len(ZB_GRID)):
        tb = hit_b[:, i][:, None]
        table[i] = ((tb < steps) & (tb < hit_c)).mean(axis=0)
    return table


def target_before_stop(zb: np.ndarray, zc: np.ndarray) -> np.ndarray:
    """P(elobb a cel, mint a stop, T-ig) - bilinearis interpolacio a tablazatban (a racson kivul: a szele)."""
    t = _table()
    zb, zc = np.clip(zb, ZB_GRID[0], ZB_GRID[-1]), np.clip(zc, ZC_GRID[0], ZC_GRID[-1])
    i = np.clip(np.searchsorted(ZB_GRID, zb) - 1, 0, len(ZB_GRID) - 2)
    j = np.clip(np.searchsorted(ZC_GRID, zc) - 1, 0, len(ZC_GRID) - 2)
    u = (zb - ZB_GRID[i]) / (ZB_GRID[i + 1] - ZB_GRID[i])
    v = (zc - ZC_GRID[j]) / (ZC_GRID[j + 1] - ZC_GRID[j])
    return ((1 - u) * (1 - v) * t[i, j] + u * (1 - v) * t[i + 1, j] + (1 - u) * v * t[i, j + 1] + u * v * t[i + 1, j + 1])
