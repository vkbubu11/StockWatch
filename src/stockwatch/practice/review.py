"""Itelet es levezetes egy gyakorlo dontes utan (#25, #26) - LLM nelkul, minden szam a szamitasbol.

ITELET - ket kulon szempont, mert a jo dontes es a jo kimenet nem ugyanaz:
  * kimenet szerint (utolag): kihagyas (0 R), long, short - azonos feltetelekkel (kovetkezo nyitas, a felhasznalo
    stop-tavolsaga ATR-ben, celja es idokorlatja; kihagyasnal a referencia-beallitas), ugyanazzal a szimulacioval,
    mint a felhasznalo kotese. Belepesnel a kimenet jo, ha a teljesult kotes nyereseges; kihagyasnal (es nem
    teljesult limitnel), ha egyik alternativa sem lett legalabb `practice.good_r` R.
  * szabaly szerint (a dontes napjaig ismert adatbol): long, ha a Visszahuzas / Mely visszahuzas jelzett (es nem
    volt gyorsjelentes-tilalom), vagy a Kitores kotese atment a szurokon; short, ha a kiserleti short motor
    megerositett letorese minden szuron atment; mindketto: barmelyik irany; egyik sem: kihagyas.
  * kategoria: jo (szabaly + kimenet), szerencses (csak kimenet), balszerencses (csak szabaly), rossz (egyik sem);
    melle vegrehajtasi jelzesek (tul szoros stop, alacsony cel/kockazat, nem teljesult limit).

LEVEZETES - lepesek sablonos mondatokkal (templates.py) es a charthoz tartozo szintekkel:
  1. mit lehetett latni a donteskor (csak a dontes napjaig: df.iloc[: i + 1])
  2. a szabalyok szuronkent (ertek, feltetel, teljesult)
  3. a felhasznalo kotese: belepes, kockazat, kilepes napja es oka, brutto - koltseg - dij = netto, R = netto / kockazat
  4. az alternativak ugyanigy
  5. mi lett volna a jo dontes, mi kulonboztette meg, es (belepesnel) egy elore rogzitett kis racsbol egy utolagos
     stop-osszevetes, utolagoskent jelolve
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators, market, meanrev
from ..config import Settings
from . import scoring
from .templates import STRATEGY_LABELS, Line

STOP_GRID = (1.0, 1.5, 2.0, 2.5, 3.0)
GRID_LABEL = "1–3 ATR"
SIDE_LABELS = {"long": "Long", "short": "Short"}
CHOICE_LABELS = {"long": "long", "short": "short", "either": "long vagy short", "skip": "kihagyás"}
REASON_LABELS = {"stop": "stop", "gap_stop": "stop (nyitási rés)", "target": "cél", "time": "időkorlát",
                 "signal": "kilépési jel"}
STATUS_LABELS = {"unfilled": "a szabály szerint nem teljesült", "open": "nyitva maradt", "filtered": "a szűrők kizárták",
                 "unknown": "a labor még nem értékelte"}
TREND_LABELS = {"up": "emelkedő (záró > SMA200, EMA50 > EMA200)", "down": "csökkenő (záró < SMA200, EMA50 < EMA200)",
                "mixed": "vegyes (a záró és az átlagok nem egy irányba mutatnak)"}
REGIME_LABELS = {True: "emelkedő (záró > SMA200)", False: "csökkenő (záró < SMA200)", None: "nem ismert"}
INDEX_LABELS = {market.NASDAQ_INDEX: "QQQ", market.SP500_INDEX: "S&P 500"}


def _num(v, digits: int = 2) -> str:
    return "–" if v is None or not np.isfinite(v) else f"{v:.{digits}f}"


def _line(key: str, *args, numeric: tuple[int, ...] = ()) -> dict:
    return Line(key, [str(a) for a in args], list(numeric)).to_dict()


# ---------------------------------------------------------------------------
# 1. Mit lehetett latni (csak a dontes napjaig)
# ---------------------------------------------------------------------------
def context(symbol: str, df: pd.DataFrame, i: int, scale: float, mctx: market.MarketContext | None,
            earnings_dates: list | None) -> dict:
    hist = df.iloc[: i + 1]
    close = hist["close"].astype(float)
    last = float(close.iloc[-1])

    def at(series: pd.Series) -> float | None:
        v = float(series.iloc[-1])
        return v if np.isfinite(v) else None

    sma200 = at(close.rolling(200, min_periods=200).mean())
    ema50, ema200 = at(indicators.ema(close, 50)), at(indicators.ema(close, 200))
    atr = at(scoring.atr_series(hist))
    trend = "mixed"
    if sma200 is not None and ema50 is not None and ema200 is not None:
        if last > sma200 and ema50 > ema200:
            trend = "up"
        elif last < sma200 and ema50 < ema200:
            trend = "down"
    regime_ok, regime_name = None, "saját trend"
    if mctx is not None:
        index = mctx.index_of(symbol)
        regime_name = INDEX_LABELS.get(index, index) if index else "saját trend"
        frame = mctx.regime_frame(symbol, df)
        if frame is not None and not frame.empty:
            regime_ok = market.market_regime(frame.loc[: df.index[i]], df.index[i]).ok
    earn = market.next_earnings_days(earnings_dates, df.index[i].date()) if earnings_dates else None
    norm = lambda v: None if v is None else round(v * scale, 2)                      # noqa: E731
    return {"close": round(last * scale, 2), "sma200": norm(sma200), "ema50": norm(ema50), "ema200": norm(ema200),
            "rsi2": at(indicators.rsi(close, 2)), "rsi14": at(indicators.rsi(close, 14)),
            "atr": norm(atr), "atr_pct": round(atr / last * 100, 2) if atr else None,
            "high20": round(float(hist["high"].iloc[-20:].max()) * scale, 2),
            "low20": round(float(hist["low"].iloc[-20:].min()) * scale, 2),
            "trend": trend, "regime": regime_ok, "regime_name": regime_name, "earnings_days": earn}


# ---------------------------------------------------------------------------
# 2. A szabalyok szuronkent
# ---------------------------------------------------------------------------
def _meanrev_checks(name: str, settings: Settings, symbol: str, df: pd.DataFrame, i: int, scale: float,
                    mctx: market.MarketContext | None, earnings_dates: list | None, compared: dict) -> dict:
    params = getattr(settings.strategies, name).params
    feat = meanrev.features(df.iloc[: i + 1], params)
    row = feat.iloc[-1]
    checks = [
        {"name": "Trend: záró az átlag felett", "value": _num(row["close"] * scale),
         "limit": f"> SMA{params.trend_sma} = {_num(row['sma_trend'] * scale)}",
         "passed": bool(row["close"] > row["sma_trend"]) if np.isfinite(row["sma_trend"]) else None},
        {"name": "RSI(2) a küszöb alatt", "value": _num(row["rsi2"]), "limit": f"< {_num(params.rsi2_max, 1)}",
         "passed": bool(row["rsi2"] < params.rsi2_max) if np.isfinite(row["rsi2"]) else None},
        {"name": "Minimális ár", "value": f"{_num(row['close'])} $", "limit": f"≥ {_num(params.min_price)} $",
         "passed": bool(row["close"] >= params.min_price)},
        {"name": "Dollárforgalom (20 nap)", "value": f"{_num(row['dollar_vol'] / 1e6, 1)} M$",
         "limit": f"≥ {_num(params.min_dollar_volume / 1e6, 1)} M$",
         "passed": bool(row["dollar_vol"] >= params.min_dollar_volume) if np.isfinite(row["dollar_vol"]) else None},
    ]
    if params.regime_filter:
        regime = None
        if mctx is not None:
            frame = mctx.regime_frame(symbol, df)
            regime = (bool(meanrev.regime_series(feat, frame).iloc[-1])
                      if frame is not None and not frame.empty else None)
        checks.append({"name": "Piaci rezsim", "value": REGIME_LABELS[regime], "limit": "záró > SMA200",
                       "passed": regime})
    blocked = meanrev.earnings_blocked(df.index[i].date(), earnings_dates, params)
    checks.append({"name": "Gyorsjelentés-tilalom", "value": REGIME_LABELS[None] if blocked is None else
                   ("igen" if blocked else "nem"), "limit": f"{params.earnings_blackout_days} nap",
                   "passed": None if blocked is None else not blocked})
    return {"strategy": name, "signaled": bool(compared.get("signaled")), "blocked": compared.get("blocked"),
            "checks": checks}


def rules(symbol: str, df: pd.DataFrame, i: int, scale: float, settings: Settings, mctx, earnings_dates,
          strategies: list[dict]) -> list[dict]:
    by = {s["strategy"]: s for s in strategies}
    out = [_meanrev_checks(name, settings, symbol, df, i, scale, mctx, earnings_dates, by.get(name, {}))
           for name in ("pullback", "deep")]
    brk = by.get("breakout", {})
    out.append({"strategy": "breakout", "signaled": bool(brk.get("signaled")), "status": brk.get("status"), "checks": []})
    sh = by.get("short", {})
    out.append({"strategy": "short", "signaled": bool(sh.get("signaled")), "status": sh.get("status"),
                "pattern": sh.get("pattern"), "experimental": True,
                "checks": [{"name": c["name"], "value": c["detail"], "limit": "", "passed": c["passed"]}
                           for c in sh.get("checks", [])]})
    return out


def supports(s: dict) -> str | None:
    """Melyik iranyt tamasztja ala egy strategia a dontes pillanataban (szurokon atjutott jelzes), vagy None."""
    if not s.get("signaled"):
        return None
    name, status = s["strategy"], s.get("status")
    if name in ("pullback", "deep"):
        return None if s.get("blocked") else "long"
    if name == "breakout":
        return None if status in ("filtered", "unknown") else "long"
    if name == "short":
        return None if status == "filtered" else "short"
    return None


def rule_sides(strategies: list[dict]) -> list[str]:
    """Mely iranyt tamasztottak ala a szabalyok a dontes pillanataban."""
    return sorted({side for side in map(supports, strategies) if side})


# ---------------------------------------------------------------------------
# Alternativak es itelet
# ---------------------------------------------------------------------------
def alternatives(feat: pd.DataFrame, atr: np.ndarray, i: int, scale: float, params: dict, cost: float,
                 mirrored, borrow: float) -> dict:
    """Kihagyas, long, short azonos feltetelekkel (kovetkezo nyitas, a dontes stop-/celtavolsaga ATR-ben, idokorlatja)."""
    stop_atr = max(float(params["stop_atr"]), 0.05)
    target_atr = params.get("target_atr")
    out = {"params": {"stop_atr": round(stop_atr, 3), "target_atr": round(target_atr, 3) if target_atr else None,
                      "days": int(params["days"]), "source": "user" if params.get("side") else "reference"},
           "skip": {"r": 0.0}}
    close = float(feat["close"].iloc[i])
    for side in scoring.SIDES:
        trade = scoring.distance_sim(feat, atr, i, stop_atr, target_atr, int(params["days"]), cost, side, mirrored, borrow)
        if trade is None:
            out[side] = None
            continue
        _, target = scoring.distance_levels(close, float(atr[i]), stop_atr, target_atr, side)
        out[side] = scoring.trade_view(trade, feat, i, scale, close, cost, side, borrow, target)
    return out


def verdict(decision: dict, result: dict, alts: dict, settings: Settings) -> dict:
    cfg = settings.practice
    side = scoring.side_of(decision)
    user = result.get("user") or {}
    filled = side is not None and user.get("r") is not None
    user_r = float(user["r"]) if filled else 0.0
    outcomes = {"skip": 0.0} | {s: (alts.get(s) or {}).get("r") for s in scoring.SIDES}
    known = {k: float(v) for k, v in outcomes.items() if v is not None}
    best = max(known, key=lambda k: (known[k], k == "skip"))
    best_alt = max((v for k, v in known.items() if k != "skip"), default=0.0)
    outcome_ok = user_r > 0 if filled else best_alt < cfg.good_r
    sides = rule_sides(result.get("strategies", []))
    choice = "either" if len(sides) == 2 else (sides[0] if sides else "skip")
    mine = side or "skip"
    rule_ok = mine == choice or (choice == "either" and side is not None)
    category = ("good" if outcome_ok else "unlucky") if rule_ok else ("lucky" if outcome_ok else "bad")
    flags = []
    if side is not None:
        if user.get("stop_atr") is not None and user["stop_atr"] < cfg.tight_stop_atr:
            flags.append({"key": "tight", "value": user["stop_atr"], "limit": cfg.tight_stop_atr})
        if user.get("rr") is not None and user["rr"] < cfg.min_rr:
            flags.append({"key": "rr", "value": user["rr"], "limit": cfg.min_rr})
        if user.get("status") == "unfilled":
            flags.append({"key": "unfilled"})
    return {"category": category, "outcome_ok": outcome_ok, "rule_ok": rule_ok, "rule_sides": sides,
            "rule_choice": choice, "user_choice": mine, "user_r": round(user_r, 3), "filled": filled,
            "best": best, "best_r": round(known[best], 3), "outcomes": {k: (round(v, 3) if v is not None else None)
                                                                        for k, v in outcomes.items()},
            "flags": flags}


def better_stop(feat: pd.DataFrame, i: int, decision: dict, scale: float, atr_norm: float | None, cost: float,
                mirrored, borrow: float, user_r: float, min_gain: float) -> dict | None:
    """Utolagos osszevetes egy ELORE ROGZITETT kis racsbol (STOP_GRID): a felhasznalo belepojevel, celjaval es
    idokorlatjaval, csak a stop-tavolsag valtozik. Csak a legalabb `min_gain` R javulas jelenik meg."""
    side = scoring.side_of(decision)
    if side is None or not atr_norm:
        return None
    sign = 1.0 if side == "long" else -1.0
    planned = decision["limit"] if decision["entry"] == "limit" else 100.0
    limit = decision["limit"] / scale if decision["entry"] == "limit" else math.inf
    target = decision["target"] / scale if decision.get("target") else None
    best = None
    for mult in STOP_GRID:
        stop_norm = planned - sign * mult * atr_norm
        if stop_norm <= 0 or (target is not None and sign * (decision["target"] - planned) <= 0):
            continue
        trade = scoring.simulate(feat, i, limit, stop_norm / scale, target, int(decision["days"]), cost, side,
                                 mirrored, borrow)
        if trade.status != "closed":
            continue
        r = scoring.r_multiple(trade.entry_price, trade.exit_price, trade.stop, planned / scale, cost, side,
                               trade.held, borrow)
        if r is not None and (best is None or r > best["r"]):
            best = {"stop_atr": mult, "r": round(r, 3)}
    if best is None or best["r"] - user_r < min_gain:
        return None
    return best | {"user_r": round(user_r, 3), "grid": GRID_LABEL, "hindsight": True}


# ---------------------------------------------------------------------------
# Levezetes (sablonos lepesek)
# ---------------------------------------------------------------------------
def _trade_lines(view: dict, decision: dict | None, future: list[dict], atr_norm: float | None) -> list[dict]:
    side = view["side"]
    lines = []
    calc = view.get("calc")
    if decision is not None and decision.get("entry") == "limit":
        if view["status"] == "unfilled":
            return [_line("x_unfilled", _num(decision["limit"]), _num(0.0), numeric=(0, 1))]
        lines.append(_line("x_entry_limit", SIDE_LABELS[side], _num(decision["limit"]), view["entry_k"],
                           _num(view["entry"]), numeric=(1, 2, 3)))
    elif view.get("entry") is not None:
        lines.append(_line("x_entry_open", SIDE_LABELS[side], view["entry_k"], _num(view["entry"]), numeric=(1, 2)))
    if view["status"] == "unfilled" or view.get("entry") is None:
        return lines + [_line("x_open")]
    if view["entry"] is not None:
        entry, stop = (calc["entry"], calc["stop"]) if calc else (view["entry"], view["stop"])
        risk = abs(entry - stop)
        if calc and abs(calc["risk"] - risk) > 1e-6:                    # a nyito mar a stopon tul volt
            planned = stop + (calc["risk"] if side == "long" else -calc["risk"])
            lines.append(_line("x_risk_gap", _num(planned, 3), _num(stop, 3), _num(calc["risk"], 3), numeric=(0, 1, 2)))
        else:
            lines.append(_line("x_risk", _num(entry, 3), _num(stop, 3), _num(risk, 3),
                               _num(risk / atr_norm if atr_norm else None), numeric=(0, 1, 2, 3)))
    if view["status"] != "closed":
        return lines + [_line("x_open")]
    k = view["exit_k"]
    bar = future[k - 1] if k and 0 < k <= len(future) else None
    reason = view["reason"]
    if reason == "stop":
        key, extreme = ("x_exit_stop_long", bar and bar["l"]) if side == "long" else ("x_exit_stop_short", bar and bar["h"])
        lines.append(_line(key, k, _num(extreme), _num(view["stop"]), numeric=(0, 1, 2)))
        if view.get("same_bar"):
            lines.append(_line("x_same_bar"))
    elif reason == "gap_stop":
        lines.append(_line("x_exit_gap", k, _num(view["exit"]), _num(view["stop"]), numeric=(0, 1, 2)))
    elif reason == "target":
        lines.append(_line("x_exit_target", k, _num(view.get("target")), _num(view["exit"]), numeric=(0, 1, 2)))
    elif reason == "signal":
        lines.append(_line("x_exit_signal", k - 1, _num(view["exit"]), numeric=(0, 1)))
    else:
        days = decision["days"] if decision else view["held"]
        lines.append(_line("x_exit_time", days, k, _num(view["exit"]), numeric=(0, 1, 2)))
    if calc:
        gross_key = "x_gross_long" if side == "long" else "x_gross_short"
        a, b = (calc["exit"], calc["entry"]) if side == "long" else (calc["entry"], calc["exit"])
        lines.append(_line(gross_key, _num(a, 3), _num(b, 3), _num(calc["gross"], 3), numeric=(0, 1, 2)))
        lines.append(_line("x_cost", _num(calc["cost_bps"], 1), _num(calc["cost"], 3), numeric=(0, 1)))
        if side == "short" and calc["borrow_bps"]:
            lines.append(_line("x_borrow", calc["held"], _num(calc["borrow_bps"], 1), _num(calc["borrow"], 3),
                               numeric=(0, 1, 2)))
        lines.append(_line("x_net", _num(calc["gross"], 3), _num(calc["cost"], 3), _num(calc["borrow"], 3),
                           _num(calc["net"], 3), numeric=(0, 1, 2, 3)))
        lines.append(_line("x_r", _num(calc["net"], 3), _num(calc["risk"], 3), _num(view["r"]), numeric=(0, 1, 2)))
    return lines


def _levels(view: dict | None) -> list[dict]:
    if not view:
        return []
    out = [{"v": view["stop"], "kind": "stop"}]
    if view.get("target") is not None:
        out.append({"v": view["target"], "kind": "target"})
    return out


def _marks(view: dict | None) -> list[dict]:
    if not view:
        return []
    out = []
    if view.get("entry_k") is not None and view.get("entry") is not None:
        out.append({"k": view["entry_k"], "v": view["entry"], "kind": "entry"})
    if view.get("exit_k") is not None and view.get("exit") is not None:
        out.append({"k": view["exit_k"], "v": view["exit"], "kind": "exit", "r": view.get("r")})
    return out


def steps(decision: dict, result: dict, ctx: dict, rule_rows: list[dict], alts: dict, ver: dict,
          better: dict | None) -> list[dict]:
    side = scoring.side_of(decision)
    atr_norm = result.get("atr")
    out = []

    # 1. kontextus
    lines = [_line("x_value", "Záró", _num(ctx["close"]), numeric=(1,))]
    for label, key in (("SMA200", "sma200"), ("EMA50", "ema50"), ("EMA200", "ema200"), ("RSI(2)", "rsi2"),
                       ("RSI(14)", "rsi14"), ("ATR(14)", "atr"), ("20 napos csúcs", "high20"), ("20 napos mélypont", "low20")):
        if ctx.get(key) is not None:
            lines.append(_line("x_value", label, _num(ctx[key], 1 if key.startswith("rsi") else 2), numeric=(1,)))
    lines.append(_line("x_trend", TREND_LABELS[ctx["trend"]]))
    lines.append(_line("x_regime", ctx["regime_name"], REGIME_LABELS[ctx["regime"]]))
    lines.append(_line("x_earn", ctx["earnings_days"], numeric=(0,)) if ctx["earnings_days"] is not None
                 else _line("x_earn_none"))
    focus = [{"v": ctx[k], "kind": k} for k in ("sma200", "high20", "low20") if ctx.get(k) is not None]
    out.append({"id": "context", "title": _line("x_t_context"), "lines": lines, "levels": focus, "marks": []})

    # 2. szabalyok
    signaled = [STRATEGY_LABELS[st["strategy"]] for st in result.get("strategies", []) if supports(st)]
    lines = [_line("x_rules_some", ", ".join(signaled)) if signaled else _line("x_rules_none"),
             _line("x_rules_choice", CHOICE_LABELS[ver["rule_choice"]]),
             _line("x_breakout_note"), _line("x_short_note")]
    out.append({"id": "rules", "title": _line("x_t_rules"), "lines": lines, "table": rule_rows, "levels": [], "marks": []})

    # 3. a felhasznalo kotese
    user = result.get("user")
    if side is None:
        lines = [_line("x_skip", _num(0.0), numeric=(0,))]
    else:
        lines = _trade_lines(user, decision, result["future"], atr_norm)
    out.append({"id": "trade", "title": _line("x_t_trade"), "lines": lines, "levels": _levels(user) + (
        [{"v": decision["limit"], "kind": "limit"}] if side and decision.get("entry") == "limit" else []),
        "marks": _marks(user)})

    # 4. alternativak
    p = alts["params"]
    target = "cél nélkül" if not p["target_atr"] else f"{_num(p['target_atr'])} ATR cél"
    source = "a te stop-távolságod és időkorlátod" if p["source"] == "user" else "referencia-beállítás"
    lines = [_line("x_alt_params", _num(p["stop_atr"]), target, p["days"], source, numeric=(0, 2)),
             _line("x_alt_skip", _num(0.0), numeric=(0,))]
    rows = [{"key": "skip", "r": 0.0}]
    for s in scoring.SIDES:
        v = alts.get(s)
        if v and v.get("r") is not None:
            lines.append(_line("x_alt_row", SIDE_LABELS[s], _num(v["r"]), REASON_LABELS.get(v["reason"], v["reason"] or ""),
                               v["held"], numeric=(1, 3)))
        else:
            lines.append(_line("x_alt_none", SIDE_LABELS[s]))
        rows.append({"key": s, "r": v.get("r") if v else None, "levels": _levels(v), "marks": _marks(v)})
    for st in result.get("strategies", []):
        if not st.get("signaled"):
            continue
        name = STRATEGY_LABELS[st["strategy"]]
        if st.get("r") is not None:
            lines.append(_line("x_strat_row", name, _num(st["r"]), numeric=(1,)))
        else:
            lines.append(_line("x_strat_status", name, STATUS_LABELS.get(st.get("status"), "nem ismert")))
        rows.append({"key": st["strategy"], "r": st.get("r"), "levels": _levels(st if st.get("stop") is not None else None),
                     "marks": _marks(st if st.get("stop") is not None else None)})
    out.append({"id": "alts", "title": _line("x_t_alts"), "lines": lines, "rows": rows, "levels": [], "marks": []})

    # 5. mi lett volna a jo dontes
    best_label = CHOICE_LABELS[ver["best"]]
    lines = [_line("x_best", best_label, _num(ver["best_r"]), numeric=(1,)),
             _line({"up": "x_why_trend_up", "down": "x_why_trend_down"}.get(ctx["trend"], "x_why_trend_mixed")),
             _line("x_why_rules", CHOICE_LABELS[ver["rule_choice"]], CHOICE_LABELS[ver["user_choice"]]),
             _line(f"x_v_{ver['category']}")]
    for flag in ver["flags"]:
        if flag["key"] == "tight":
            lines.append(_line("x_flag_tight", _num(flag["value"]), _num(flag["limit"]), numeric=(0, 1)))
        elif flag["key"] == "rr":
            lines.append(_line("x_flag_rr", _num(flag["value"]), _num(flag["limit"]), numeric=(0, 1)))
        elif flag["key"] == "unfilled":
            lines.append(_line("x_flag_unfilled"))
    if better:
        lines.append(_line("x_better", better["grid"], _num(better["stop_atr"], 1), _num(better["r"]),
                           _num(better["user_r"]), numeric=(1, 2, 3)))
    lines.append(_line("disclaimer"))
    best_view = alts.get(ver["best"]) if ver["best"] in scoring.SIDES else None
    out.append({"id": "best", "title": _line("x_t_best"), "lines": lines, "levels": _levels(best_view),
                "marks": _marks(best_view)})
    return out


def build(symbol: str, df: pd.DataFrame, feat: pd.DataFrame, mirrored, atr: np.ndarray, i: int, decision: dict,
          result: dict, params: dict, settings: Settings, mctx, earnings_dates, scale: float, cost: float,
          borrow: float) -> dict:
    alts = alternatives(feat, atr, i, scale, params, cost, mirrored, borrow)
    ctx = context(symbol, df, i, scale, mctx, earnings_dates)
    rule_rows = rules(symbol, df, i, scale, settings, mctx, earnings_dates, result["strategies"])
    ver = verdict(decision, result, alts, settings)
    better = (better_stop(feat, i, decision, scale, result.get("atr"), cost, mirrored, borrow, ver["user_r"],
                          settings.practice.better_r) if ver["filled"] else None)
    return {"verdict": ver, "verdict_line": _line(f"x_v_{ver['category']}"), "context": ctx, "alternatives": alts,
            "better": better, "steps": steps(decision, result, ctx, rule_rows, alts, ver, better)}
