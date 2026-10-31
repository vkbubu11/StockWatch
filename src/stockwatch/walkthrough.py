"""A lecke levezetese: hogyan kell szamolni es mit kell figyelni - a lecke sajat pelda-chartjan (learning.figure),
lepesenkent, sablonos mondatokkal (practice/templates.py, i18n kulcsok), minden szam az abra adataibol.

A lecke `walkthrough` kulcsa valaszt a levezetesek kozul (BUILDERS); a chartra rajzolt szintek (levels) es jelolesek
(marks) a szoveg szamaihoz tartoznak. Leiro: mit mutat az abra, nem ajanl.
"""
from __future__ import annotations

from .practice.templates import Line

UP, DOWN, INFO, MUTED = "var(--up)", "var(--down)", "var(--info)", "var(--muted)"
STOP_ATR = 1.5                  # a peldaszamitasok stop-tavolsaga ATR-ben
TARGET_R = 2
ACCOUNT, RISK_PCT = 10_000, 1   # a pozicioméret peldaszamlaja


def _n(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}"


def _r(v: float) -> float:
    """A reszeredmeny a kiirt pontossagra kerekitve szamol tovabb: a lepesek kezzel ellenorizhetok."""
    return round(v + 0.0, 2)


def _line(key: str, *args) -> dict:
    return Line(key, [a if isinstance(a, str) else _n(a) if isinstance(a, float) else str(a) for a in args],
                list(range(len(args)))).to_dict()


def candles(fig: dict) -> dict:
    bars = fig["bars"]
    k = bars[-1]
    prev, recent = bars[-20:-10], bars[-10:]
    hp, hr = max(b["h"] for b in prev), max(b["h"] for b in recent)
    lp, lr = min(b["l"] for b in prev), min(b["l"] for b in recent)
    trend = "l_trend_up" if hr > hp and lr > lp else "l_trend_down" if hr < hp and lr < lp else "l_trend_side"
    at = lambda rows, key, v: next(b["i"] for b in rows if b[key] == v)          # noqa: E731
    return {"lines": [
        _line("l_candle", k["o"], k["h"], k["l"], k["c"]),
        _line("l_body_up" if k["c"] >= k["o"] else "l_body_down", k["c"], k["o"], abs(k["c"] - k["o"])),
        _line("l_range", k["h"], k["l"], k["h"] - k["l"]),
        _line("l_highs", 10, hp, hr), _line("l_lows", 10, lp, lr), _line(trend)],
        "levels": [], "marks": [
            {"k": at(prev, "h", hp), "v": hp, "color": MUTED, "label": "Korábbi csúcs"},
            {"k": at(recent, "h", hr), "v": hr, "color": INFO, "label": "Utolsó csúcs"},
            {"k": at(prev, "l", lp), "v": lp, "color": MUTED, "label": "Korábbi mélypont"},
            {"k": at(recent, "l", lr), "v": lr, "color": INFO, "label": "Utolsó mélypont"}]}


def _ema_lines(fig: dict) -> list[dict]:
    k = fig["bars"][-1]
    e20, e50, e200, c = k["ema20"], k["ema50"], k["ema200"], k["c"]
    order = "l_order_up" if c > e20 > e50 > e200 else "l_order_down" if c < e20 < e50 < e200 else "l_order_mixed"
    return [_line("l_ema_values", e20, e50, e200, c), _line(order), _line("l_ema_dist", c, e20, (c - e20) / e20 * 100)]


def ema(fig: dict) -> dict:
    prev, k = fig["bars"][-2], fig["bars"][-1]
    alpha = 2 / (20 + 1)
    return {"lines": [_line("l_ema_formula", 20, _n(alpha, 4)),
                      _line("l_ema_step", _n(alpha, 4), k["c"], _n(1 - alpha, 4), prev["ema20"],
                            alpha * k["c"] + (1 - alpha) * prev["ema20"])] + _ema_lines(fig),
            "levels": [], "marks": []}


def _sr(fig: dict, days: int = 60) -> tuple[list[dict], list[dict]]:
    bars, atr = fig["bars"], fig["atr"]
    c = bars[-1]["c"]
    window = bars[-days - 1:-1]
    res, sup = max(b["h"] for b in window), min(b["l"] for b in window)
    touches = sum(1 for b in window if res - b["h"] <= 0.5 * atr)
    lines = [_line("l_sr", days, res, sup), _line("l_atr", atr, 14),
             _line("l_res_above", c, res, atr, (c - res) / atr) if c > res else _line("l_to_res", res, c, atr, (res - c) / atr),
             _line("l_to_sup", c, sup, atr, (c - sup) / atr),
             _line("l_touches", touches, 0.5)]
    levels = [{"v": res, "label": "Rezisztencia", "color": DOWN}, {"v": sup, "label": "Support", "color": UP}]
    return lines, levels


def levels(fig: dict) -> dict:
    lines, lv = _sr(fig)
    return {"lines": lines, "levels": lv, "marks": []}


def double_bottom(fig: dict, days: int = 60) -> dict:
    bars = fig["bars"][-days:]
    a = min(range(len(bars)), key=lambda j: bars[j]["l"])
    b = min((j for j in range(len(bars)) if abs(j - a) >= 5), key=lambda j: bars[j]["l"])
    lo, hi = sorted((a, b))
    neck = max(x["h"] for x in bars[lo:hi + 1])
    bottom = min(bars[a]["l"], bars[b]["l"])
    depth = _r(neck - bottom)
    c = bars[-1]["c"]
    la, lb = bars[lo], bars[hi]
    return {"lines": [
        _line("l_db_lows", la["l"], -la["i"], lb["l"], -lb["i"], abs(la["l"] - lb["l"]) / bottom * 100),
        _line("l_db_neck", neck), _line("l_db_depth", neck, bottom, depth),
        _line("l_db_target", neck, depth, _r(neck + depth)),
        _line("l_db_above" if c > neck else "l_db_below", c)],
        "levels": [{"v": neck, "label": "Nyakvonal", "color": INFO},
                   {"v": _r(neck + depth), "label": "Mért cél", "color": UP, "dash": True}],
        "marks": [{"k": la["i"], "v": la["l"], "color": UP, "label": "1. mélypont"},
                  {"k": lb["i"], "v": lb["l"], "color": UP, "label": "2. mélypont"}]}


def _risk(fig: dict, side: str) -> tuple[list[dict], list[dict]]:
    c, atr = fig["bars"][-1]["c"], fig["atr"]
    sign = 1 if side == "long" else -1
    stop = _r(c - sign * STOP_ATR * atr)
    r = _r(abs(c - stop))
    target = _r(c + sign * TARGET_R * r)
    lines = [_line("l_stop_long" if side == "long" else "l_stop_short", atr, STOP_ATR, c, stop),
             _line("l_r", c, stop, r),
             _line("l_target_long" if side == "long" else "l_target_short", TARGET_R, c, r, target),
             _line("l_outcome", TARGET_R, 1)]
    levels = [{"v": c, "label": "Belépő", "color": INFO}, {"v": stop, "label": "Stop", "color": DOWN},
              {"v": target, "label": "Cél", "color": UP, "dash": True}]
    return lines, levels


def stop(fig: dict, days: int = 10) -> dict:
    lines, lv = _risk(fig, "long")
    swing = min(b["l"] for b in fig["bars"][-days:])
    lines.append(_line("l_swing_below" if lv[1]["v"] < swing else "l_swing_above", days, swing, lv[1]["v"]))
    return {"lines": lines, "levels": lv, "marks": []}


def short(fig: dict) -> dict:
    lines, lv = _risk(fig, "short")
    c, r = fig["bars"][-1]["c"], abs(lv[0]["v"] - lv[1]["v"])
    drop = _r(c - r)                                   # egy 1 R-es esés
    lines.append(_line("l_short_pnl", drop, c, r, (c - drop) / r))
    return {"lines": lines, "levels": lv, "marks": []}


def rsi(fig: dict, threshold: int = 10) -> dict:
    closes = [b["c"] for b in fig["bars"][-3:]]
    ch = [closes[1] - closes[0], closes[2] - closes[1]]
    ch = [_r(x) for x in ch]
    gain, loss = _r(sum(max(x, 0) for x in ch) / 2), _r(sum(max(-x, 0) for x in ch) / 2)
    value = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    k = fig["bars"][-1]
    lines = [_line("l_rsi_changes", ch[0], ch[1]), _line("l_rsi_avg", gain, loss),
             _line("l_rsi_max", value) if loss == 0 else _line("l_rsi", gain, loss, value),
             _line("l_rsi_low" if value < threshold else "l_rsi_mid", threshold, value),
             _line("l_rsi_trend_up" if k["c"] > k["ema200"] else "l_rsi_trend_down", k["c"], k["ema200"])]
    return {"lines": lines, "levels": [], "marks": []}


def breakout(fig: dict, days: int = 20, strong: float = 1.5) -> dict:
    bars, atr = fig["bars"], fig["atr"]
    k = bars[-1]
    high = max(b["h"] for b in bars[-days - 1:-1])
    lines = [_line("l_bo_high", days, high),
             _line("l_bo_yes", k["c"]) if k["c"] > high else _line("l_bo_no", k["c"], (high - k["c"]) / atr)]
    if k["v"] is not None:
        lines.append(_line("l_bo_vol_hi" if k["v"] >= strong else "l_bo_vol_lo", k["v"], strong, 20))
    lines.append(_line("l_bo_stop", high, STOP_ATR, atr, _r(high - STOP_ATR * atr)))
    return {"lines": lines, "levels": [{"v": high, "label": "20 napos csúcs", "color": MUTED}], "marks": []}


def sizing(fig: dict) -> dict:
    lines, lv = _risk(fig, "long")
    entry, stp = lv[0]["v"], lv[1]["v"]
    budget = ACCOUNT * RISK_PCT / 100
    rps = _r(abs(entry - stp))
    shares = int(budget // rps)
    return {"lines": [_line("l_budget", ACCOUNT, RISK_PCT, budget), lines[0],
                      _line("z_rps", entry, stp, rps), _line("z_shares", budget, rps, budget / rps, shares),
                      _line("z_risk", shares, rps, _r(shares * rps)), _line("z_value", shares, entry, _r(shares * entry)),
                      _line("l_share", shares * entry / ACCOUNT * 100)],
            "levels": lv[:2], "marks": []}


def mixed(fig: dict) -> dict:
    sr_lines, sr_levels = _sr(fig)
    risk_lines, risk_levels = _risk(fig, "long")
    return {"lines": _ema_lines(fig) + sr_lines + risk_lines, "levels": sr_levels + risk_levels[1:], "marks": []}


BUILDERS = {"candles": candles, "ema": ema, "levels": levels, "double_bottom": double_bottom, "stop": stop,
            "rsi": rsi, "breakout": breakout, "short": short, "sizing": sizing, "mixed": mixed}
