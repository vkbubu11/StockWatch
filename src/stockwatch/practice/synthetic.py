"""Szintetikus arfolyamsorok (#38): adatlicenc nelkuli gyakorlo feladatok.

A papirnev "SYN0001" alaku; ugyanaz a nev mindig ugyanazt a sort adja (seedelt, GENERATOR_VERSION-nel egyutt).
A sort ugyanazok a motorok ertekelik (meanrev, patterns, short), mint a valodit - a sampler.load_prices adja at.

  * alap: napi log-hozam GARCH(1,1) volatilitassal (volatilitas-klaszterezes), Student-t (vastag farok) zajjal es
    Markov-rezsimvaltassal (emelkedo / eso / oldalazo trend); papironkent mas szint es volatilitas
  * nyitasi res (nehany gyorsjelentes-szeru nagyobb ugrassal), napon beluli sav a volatilitasbol, volumen a
    hozam nagysagaval egyutt mozog
  * beepitett alakzatok: pivot-sablonok ATR-egysegben (dupla / tripla alj es teto, fej-vall es forditottja, zaszlo,
    emelkedo / eso haromszog, csesze-ful), veletlen merettel es zajjal; utana a kimenet vegyes (kitores vagy
    kudarc) - az alakzat nem mindig "mukodik", ahogy a valodi piacon sem
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np
import pandas as pd

GENERATOR_VERSION = 1
PREFIX = "SYN"
SYMBOL_RE = re.compile(r"^SYN\d{4}$")
N_BARS = 2600                                    # ~10 ev kereskedesi nap
END = pd.Timestamp("2025-03-31")                 # a sampler.VISIBLE_END (a holdout elotti nap)
SUCCESS_RATE = 0.55                              # a beepitett alakzat utan ekkora esellyel jon kitores

# (ido, ar) pivotok; az ar ATR-egysegben a kezdoponthoz kepest. Medves parjuk a tukorkepuk.
BULLISH_TEMPLATES = {
    "Dupla alj": [(0, 0), (28, -8.0), (44, -3.4), (62, -8.1), (76, -3.3)],
    "Tripla alj": [(0, 0), (26, -8.0), (38, -4.0), (52, -8.1), (64, -4.0), (78, -8.0), (90, -3.9)],
    "Forditott fej-vall": [(0, 0), (20, -5.5), (30, -2.8), (46, -8.0), (60, -2.8), (74, -5.6), (86, -2.7)],
    "Bull zaszlo": [(0, 0), (12, 7.5), (17, 6.3), (21, 7.0), (26, 5.9), (30, 6.6), (35, 5.6)],
    "Emelkedo haromszog": [(0, 0), (12, 4.5), (20, 1.4), (28, 4.5), (35, 2.5), (42, 4.5), (48, 3.4)],
    "Csesze-ful": [(0, -4.0), (14, 0), (26, -3.5), (38, -6.0), (52, -6.2), (66, -3.5), (80, 0.0), (88, -1.2),
                   (96, -0.2)],
}
BEARISH_NAMES = {"Dupla alj": "Dupla teto", "Tripla alj": "Tripla teto", "Forditott fej-vall": "Fej-vall",
                 "Bull zaszlo": "Bear zaszlo", "Emelkedo haromszog": "Csokkeno haromszog"}
TEMPLATES = BULLISH_TEMPLATES | {bear: [(t, -y) for t, y in BULLISH_TEMPLATES[bull]]
                                 for bull, bear in BEARISH_NAMES.items()}
DIRECTION = {name: "bullish" for name in BULLISH_TEMPLATES} | {name: "bearish" for name in BEARISH_NAMES.values()}


def symbols(n: int) -> list[str]:
    return [f"{PREFIX}{k:04d}" for k in range(1, n + 1)]


def is_synthetic(symbol: str) -> bool:
    return bool(SYMBOL_RE.match(symbol or ""))


def _rng(symbol: str) -> np.random.Generator:
    raw = f"{GENERATOR_VERSION}|{symbol}".encode()
    return np.random.default_rng(int(hashlib.sha1(raw).hexdigest()[:12], 16))


def _base_returns(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(napi log-hozam, napi szigma): GARCH(1,1), Student-t(5) zaj, Markov-rezsim a sodrodasban."""
    vol = rng.uniform(0.011, 0.024)                       # a papir hosszu tavu napi volatilitasa
    alpha, beta = 0.08, 0.90
    omega = vol ** 2 * (1 - alpha - beta)
    drifts = np.array([0.0008, -0.0009, 0.0])             # emelkedo, eso, oldalazo
    stay = 0.992
    z = np.clip(rng.standard_t(5, n) / np.sqrt(5 / 3), -7, 7)   # egysegnyi szorasu vastag farku zaj
    r, sig = np.empty(n), np.empty(n)
    var, regime = vol ** 2, int(rng.integers(3))
    for t in range(n):
        if rng.random() > stay:
            regime = int(rng.integers(3))
        sig[t] = np.sqrt(var)
        r[t] = drifts[regime] + sig[t] * z[t]
        var = min(omega + alpha * (r[t] - drifts[regime]) ** 2 + beta * var, (3 * vol) ** 2)
    return r, sig


def _insert(rng, lc: np.ndarray, sig: np.ndarray, start: int, points: list, outcome: list) -> int:
    """A sablon (+ a kimeneti szakasz) beultetese a log-zaro sorba a `start`-tol; a sor tobbi resze eltolodik,
    hogy folytonos maradjon. Visszaadja az utolso sablon-pivot indexet."""
    u = 1.4 * sig[start] * rng.uniform(0.9, 1.3)          # ~ATR / ar (a napi sav a szigma ~1,4-szerese)
    stretch = rng.uniform(0.85, 1.25)                     # idoben is valtozo meret
    pts = [(int(round(t * stretch)), y) for t, y in points] + outcome
    path = [lc[start]]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        steps = max(t1 - t0, 1)
        line = np.linspace(y0, y1, steps + 1)[1:]
        bridge = np.cumsum(rng.normal(0, 0.18, steps))
        bridge -= np.linspace(0, bridge[-1], steps)       # Brown-hid: a pivotokon pontosan atmegy
        path.extend(lc[start] + (line + bridge) * u)
    path = np.array(path)
    end = min(start + len(path), len(lc))
    shift = path[end - start - 1] - lc[end - 1]
    lc[start:end] = path[: end - start]
    lc[end:] += shift
    return start + pts[len(points) - 1][0]


@lru_cache(maxsize=64)
def generate(symbol: str) -> tuple[pd.DataFrame, tuple]:
    """(OHLCV gyertyak, beepitett alakzatok: (nev, irany, kezdo index, utolso pivot index, siker))."""
    if not is_synthetic(symbol):
        raise ValueError(f"Nem szintetikus papir: {symbol}")
    rng = _rng(symbol)
    n = N_BARS
    r, sig = _base_returns(rng, n)
    lc = np.log(rng.uniform(20, 300)) + np.cumsum(r)
    names = sorted(TEMPLATES)
    embedded = []
    pos = 260 + int(rng.integers(0, 80))
    while pos < n - 160:
        name = names[int(rng.integers(len(names)))]
        sign = 1.0 if DIRECTION[name] == "bullish" else -1.0
        last_t, last_y = TEMPLATES[name][-1]
        success = bool(rng.random() < SUCCESS_RATE)
        leg = rng.uniform(3.0, 6.0)
        outcome = [(int(last_t * 1.25) + 18, last_y + sign * (leg if success else -leg))]
        pivot = _insert(rng, lc, sig, pos, TEMPLATES[name], outcome)
        embedded.append((name, DIRECTION[name], pos, pivot, success))
        pos = pivot + int(rng.integers(120, 260))
    close = np.exp(lc)
    ret = np.diff(lc, prepend=lc[0])
    # nyitasi res: a hozam egy resze ejszaka (nehany gyorsjelentes-szeru ugrassal), a tobbi napkozben
    gap = ret * rng.uniform(0.1, 0.4, n) + rng.normal(0, 0.15, n) * sig
    jumps = rng.random(n) < 0.012
    gap[jumps] += rng.normal(0, 4, jumps.sum()) * sig[jumps]
    prev = np.r_[close[0], close[:-1]]
    open_ = prev * np.exp(gap)
    close = open_ * np.exp(ret - gap)                     # a zaro-zaro hozam valtozatlan marad
    close[0] = open_[0] = np.exp(lc[0])
    spread = np.abs(rng.normal(0, 0.55, (2, n))) * sig
    high = np.maximum(open_, close) * np.exp(spread[0])
    low = np.minimum(open_, close) * np.exp(-spread[1])
    noise = np.convolve(rng.normal(0, 0.35, n), np.ones(5) / 5, mode="same")
    volume = rng.uniform(3e5, 8e6) * np.exp(noise) * (1 + 0.6 * np.abs(ret) / sig)
    index = pd.bdate_range(end=END, periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": np.round(volume)},
                      index=index)
    return df, tuple(embedded)


def frame(symbol: str) -> pd.DataFrame:
    return generate(symbol)[0].copy()
