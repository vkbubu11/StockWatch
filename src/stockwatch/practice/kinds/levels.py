"""Support / rezisztencia es trendvonal berajzolasa (#52).

  * "levels" mod: a felhasznalo 1-MAX_LINES vizszintes vonalat huz a szerinte legfontosabb szintekre. Referencia:
    a levels.find_levels klaszterezett szintjei (erosseg-pontszammal) a dontes napjaig ismert adatbol, a lathato
    arsavban a REF_COUNT legerosebb. Pont: eltalalt erős szint (felidezes) es a vonalak hany szazaleka esik erős
    szintre (pontossag) - a tures ATR-ben (settings.practice.levels_tolerance_atr).
  * "trend" mod: ketpontos vonal a melypontokra (emelkedo trend: zaro > EMA200) vagy a csucsokra (csokkeno). Pont:
    hany megerositett pivotot erint (tures ATR-ben), es hany gyertyatesten vag at a ket pont kozott.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ... import indicators, structure
from ... import levels as level_engine
from ...config import Settings
from .. import sampler, scoring
from .common import line, norm

NAME = "levels"
MAX_LINES = 3
REF_COUNT = 3
WINDOW = 600                           # a szintkereso es a pivotok ablaka a dontes napjaig (gyertya)


def _frame(pick: dict, settings: Settings) -> tuple[pd.DataFrame, int]:
    df = sampler.load_prices(pick["symbol"], settings)
    return df, int(df.index.get_loc(pd.Timestamp(pick["date"])))


def make(pick: dict, rng, settings: Settings) -> dict:
    df, i = _frame(pick, settings)
    close = df["close"].iloc[: i + 1]
    up = float(close.iloc[-1]) > float(indicators.ema(close, 200).iloc[-1])
    mode = "trend" if rng.random() < 0.5 else "levels"
    return pick | {"params": {"prompt": {"mode": mode, "side": "lows" if up else "highs", "max": MAX_LINES}, "secret": {}}}


def _price(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("Érvénytelen szám.") from None
    if not math.isfinite(v) or not 0 < v < 1000:
        raise ValueError("Érvénytelen szám.")
    return round(v, 2)


def validate(body: dict, prompt: dict) -> dict:
    if prompt["mode"] == "levels":
        raw = body.get("lines")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LINES:
            raise ValueError("Húzz legalább egy, legfeljebb három vonalat.")
        return {"lines": sorted({_price(v) for v in raw})}
    pts = body.get("points")
    if not isinstance(pts, list) or len(pts) != 2 or not all(isinstance(p, list) and len(p) == 2 for p in pts):
        raise ValueError("A trendvonalhoz két pont kell.")
    out = []
    for k, v in pts:
        if not isinstance(k, int) or isinstance(k, bool) or not -(sampler.VISIBLE_BARS - 1) <= k <= 0:
            raise ValueError("A pontok a látható charton legyenek.")
        out.append([k, _price(v)])
    if out[0][0] == out[1][0]:
        raise ValueError("A két pont legyen különböző napon.")
    return {"points": sorted(out)}


def reference_levels(df: pd.DataFrame, i: int, settings: Settings, scale: float, lo: float, hi: float) -> list[dict]:
    """A lathato arsavba eso REF_COUNT legerosebb szint (normalizalt aron) - csak a dontes napjaig ismert adatbol."""
    window = indicators.enrich(df.iloc[max(0, i + 1 - WINDOW): i + 1])
    found = level_engine.find_levels(window, settings.levels)
    inside = [lv for lv in found if lo <= lv.price * scale <= hi]
    inside.sort(key=lambda lv: -lv.score)
    return [{"kind": lv.kind, "v": round(lv.price * scale, 2), "score": round(float(lv.score), 1)} for lv in inside[:REF_COUNT]]


def _visible(df: pd.DataFrame, i: int, scale: float) -> pd.DataFrame:
    return df.iloc[max(0, i + 1 - sampler.VISIBLE_BARS): i + 1] * scale


def _levels(df, i, answer, settings, scale, atr) -> dict:
    vis = _visible(df, i, scale)
    refs = reference_levels(df, i, settings, scale, float(vis["low"].min()), float(vis["high"].max()))
    tol_atr = settings.practice.levels_tolerance_atr
    tol = tol_atr * atr
    mine = answer["lines"]
    lines = []
    hit_refs = 0
    for ref in refs:
        lines.append(line("l_support" if ref["kind"] == "support" else "l_resistance", ref["v"], ref["score"]))
        near = [v for v in mine if abs(v - ref["v"]) <= tol]
        if near:
            hit_refs += 1
            lines.append(line("l_hit", min(near, key=lambda v: abs(v - ref["v"])), tol_atr))
        else:
            lines.append(line("l_miss"))
    good_mine = [v for v in mine if any(abs(v - r["v"]) <= tol for r in refs)]
    for v in mine:
        if refs and v not in good_mine:
            lines.append(line("l_extra", v))
    if refs:
        quality = round((hit_refs / len(refs) + len(good_mine) / len(mine)) / 2, 2)
        lines.append(line("l_score", hit_refs, len(refs), len(good_mine), len(mine), tol_atr))
    else:
        quality = 1.0
        lines.append(line("l_none"))
    levels = [{"kind": r["kind"], "v": r["v"]} for r in refs] + [{"kind": "mine", "v": v} for v in mine]
    return {"quality": quality, "lines": lines, "levels": levels, "segments": [], "reference": refs}


def _trend(df, i, answer, side, settings, scale, atr) -> dict:
    start = max(0, i + 1 - sampler.VISIBLE_BARS)
    pivots = structure.confirmed_only(structure.find_zigzag_pivots(df.iloc[max(0, i + 1 - WINDOW): i + 1]))
    offset = max(0, i + 1 - WINDOW)
    kind = "L" if side == "lows" else "H"
    piv = [(offset + p.index - i, p.price * scale) for p in pivots if p.kind == kind and offset + p.index >= start]
    (k1, v1), (k2, v2) = answer["points"]
    slope = (v2 - v1) / (k2 - k1)
    at = lambda k: v1 + (k - k1) * slope                                   # noqa: E731
    tol_atr = settings.practice.levels_tolerance_atr
    touches = sum(1 for k, v in piv if abs(at(k) - v) <= tol_atr * atr)
    vis = _visible(df, i, scale)
    body_lo = np.minimum(vis["open"], vis["close"]).to_numpy()
    body_hi = np.maximum(vis["open"], vis["close"]).to_numpy()
    first = len(vis) - 1                                                   # k = 0 a lathato ablak utolso gyertyaja
    crossings = sum(1 for k in range(k1, k2 + 1) if body_lo[first + k] < at(k) < body_hi[first + k])
    quality = round(0.6 * min(touches, 3) / 3 + 0.4 * max(0.0, 1 - crossings / 5), 2)
    lines = [line("t_lows" if side == "lows" else "t_highs"), line("t_touch", touches, tol_atr), line("t_cross", crossings)]
    segments = [{"kind": "mine", "k1": k1, "v1": v1, "k2": k2, "v2": v2}]
    last = piv[-3:]
    if len(last) >= 2:
        fit = structure.fit_trendline([structure.Pivot(k, None, v, kind, True) for k, v in last])
        segments.append({"kind": "ref", "k1": last[0][0], "v1": round(fit.value_at(last[0][0]), 2),
                         "k2": 0, "v2": round(fit.value_at(0), 2)})
        lines.append(line("t_ref", len(last)))
    else:
        lines.append(line("t_none"))
    return {"quality": quality, "lines": lines, "levels": [], "segments": segments,
            "touches": touches, "crossings": crossings, "pivots": [{"k": k, "v": round(v, 2)} for k, v in piv]}


def evaluate(task: dict, df, i: int, answer: dict, settings: Settings) -> dict:
    scale, atr = norm(df, i)
    prompt = task["params"]["prompt"]
    if prompt["mode"] == "levels":
        out = _levels(df, i, answer, settings, scale, atr)
    else:
        out = _trend(df, i, answer, prompt["side"], settings, scale, atr)
    out["lines"].append(line("q_quality", int(round(out["quality"] * 100))))
    return out | {"future": scoring.future_bars(df, i, scale), "atr": round(atr, 3), "marks": []}
