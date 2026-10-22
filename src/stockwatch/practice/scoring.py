"""Szimulacio, osszevetes es kor-statisztika a gyakorlo modhoz.

  * a felhasznalo kotese: meanrev.simulate_trade - ugyanaz a pesszimista motor, mint a strategiake; a kovetkezo
    nyitasos belepes = vegtelen limit, a cel napkozbeni (target_intraday=True, csak itt), kilepesi jel nincs,
    idokorlat a felhasznaloe
  * eredmeny R-ben: (kilepo - belepo) / (belepo - stop), oldalankent a strategia koltsegevel csokkentve
  * osszevetes ugyanazon a ponton: Visszahuzas / Mely visszahuzas (ha t-n jelzett, a sajat parametereivel),
    Kitores (a labor ELO kiserletenek mentett eredmenye, ha a kitores-cache-ben t-n kitores van), egy
    referencia-kotes (kovetkezo nyitas, 2 ATR stop, 10 nap), es a veletlen alapvonal: ugyanazon a papiron
    K seedelt veletlen napon a felhasznalo stop- (es cel-) tavolsagaval ATR-ben es idokorlatjaval
A szimulacio adata a holdout elotti napon vagodik.

SHORT (#24): ugyanaz a motor tukrozott arakon (p' = K - p, a csucs es a melypont helyet cserel), igy a
pesszimista szabalyok (azonos gyertyan stop es cel -> stop; nyitasi res a stopon tul -> nyitoar; a belepes napjan
cel csak nyitoari belepesnel) pontosan tukrozve ervenyesulnek. Az eredmeny visszaalakitva: R = (belepo - kilepo -
koltseg - kolcsonzesi dij) / (stop - belepo).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators, market, meanrev
from ..config import PullbackParams, Settings
from .sampler import FUTURE_BARS, eligible

ATR_PERIOD = 14
BASELINE_K = 20
REFERENCE = {"stop_atr": 2.0, "days": 10}
GOOD_R = 1.0
TIGHT_ATR = 1.0
STOP_BUCKETS = ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, math.inf))


def cost_bps(settings: Settings) -> float:
    return settings.strategies.pullback.params.total_cost_bps


SIDES = ("long", "short")


def side_of(decision: dict | None) -> str | None:
    """A dontes iranya: None (kihagyas), "long" (a regi "enter" is), "short"."""
    action = (decision or {}).get("action")
    if action in (None, "skip"):
        return None
    return "short" if action == "short" else "long"


def borrow_bps(settings: Settings) -> float:
    return float(settings.practice.borrow_bps_day)


def derive(entry: float, exit_: float, stop: float, planned_entry: float, cost: float, side: str = "long",
           held: int = 0, borrow: float = 0.0) -> dict | None:
    """Az R levezetese (a magyarazat es az R ugyanebbol jon). Kockazat: |belepo - stop|; ha a nyito mar a stopon tul
    volt (azonnali zaras), a tervezett belepobol. Koltseg: oldalankent c, osszesen c x (belepo + kilepo).
    Short: kolcsonzesi dij = belepo x `borrow` bps x tartott nap."""
    sign = 1.0 if side == "long" else -1.0
    risk = sign * (entry - stop) if sign * (entry - stop) > 0 else sign * (planned_entry - stop)
    if risk <= 0:
        return None
    c = cost / 10_000
    gross = sign * (exit_ - entry)
    fees = c * (entry + exit_)
    lend = entry * borrow / 10_000 * held if side == "short" else 0.0
    net = gross - fees - lend
    return {"side": side, "entry": float(entry), "exit": float(exit_), "stop": float(stop), "risk": float(risk),
            "gross": float(gross), "cost": float(fees), "borrow": float(lend), "net": float(net), "r": float(net / risk),
            "cost_bps": cost, "borrow_bps": borrow if side == "short" else 0.0, "held": int(held)}


def r_multiple(entry: float, exit_: float, stop: float, planned_entry: float, cost: float, side: str = "long",
               held: int = 0, borrow: float = 0.0) -> float | None:
    """R a koltseggel: long (kilepo * (1 - c) - belepo * (1 + c)) / kockazat, short tukrozve (+ kolcsonzesi dij).
    A kockazat a belepo - stop; ha a nyito mar a stopon tul volt (azonnali zaras), a tervezett belepo - stop."""
    d = derive(entry, exit_, stop, planned_entry, cost, side, held, borrow)
    return None if d is None else d["r"]


def practice_params(days: int, cost: float) -> PullbackParams:
    return PullbackParams(time_stop=int(days), cost_bps=cost, commission_bps=0.0)


def mirror(feat: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """A tukrozott arsor a short szimulaciohoz: p' = K - p (a csucs es a melypont helyet cserel). K pozitiv marad."""
    k = 2.0 * float(feat["high"].max()) + 1.0
    out = feat.copy()
    out["open"], out["close"] = k - feat["open"], k - feat["close"]
    out["high"], out["low"] = k - feat["low"], k - feat["high"]
    return k, out


def simulate(feat: pd.DataFrame, i: int, limit: float, stop: float, target: float | None, days: int,
             cost: float, side: str = "long", mirrored: tuple[float, pd.DataFrame] | None = None,
             borrow: float = 0.0) -> meanrev.Trade:
    """A gyakorlo kotes: `limit` = math.inf -> belepes a kovetkezo nyitason. Short: eladasi limit (a belepo
    FELETT teljesul), stop a belepo felett, cel alatta - ugyanaz a motor tukrozott arakon."""
    exits = np.zeros(len(feat), dtype=bool)
    if side != "short":
        return meanrev.simulate_trade("practice", feat, exits, i, practice_params(days, cost), levels=(limit, stop),
                                      target=target, target_intraday=True)
    k, frame = mirrored or mirror(feat)
    trade = meanrev.simulate_trade("practice", frame, exits, i, practice_params(days, cost),
                                   levels=(math.inf if math.isinf(limit) else k - limit, k - stop),
                                   target=None if target is None else k - target, target_intraday=True)
    trade.limit, trade.stop = limit, stop
    if trade.entry_price is not None:
        trade.entry_price = k - trade.entry_price
    if trade.exit_price is not None:
        trade.exit_price = k - trade.exit_price
    if trade.status == "closed":
        c = cost / 10_000
        trade.gross_pct = (trade.entry_price - trade.exit_price) / trade.entry_price * 100
        trade.net_pct = ((trade.entry_price * (1 - c) - trade.exit_price * (1 + c)) / trade.entry_price * 100
                         - borrow / 100 * trade.held)
    return trade


def _offset(feat: pd.DataFrame, day, i: int) -> int | None:
    return None if day is None else int(feat.index.get_loc(day)) - i


def trade_view(trade: meanrev.Trade, feat: pd.DataFrame, i: int, scale: float, planned_entry: float,
               cost: float, side: str = "long", borrow: float = 0.0, target: float | None = None) -> dict:
    """A kotes a felfedeshez, normalizalt aron (az utolso lathato zaro = 100). Lezart kotesnel a levezetes
    (`calc`) is: belepo, kilepo, stop, kockazat, brutto, koltseg, kolcsonzesi dij, netto, R - ugyanabbol az R."""
    out = {"status": trade.status, "side": side, "stop": round(float(trade.stop) * scale, 2), "reason": trade.reason or None,
           "held": trade.held, "entry_k": _offset(feat, trade.entry_date, i), "exit_k": _offset(feat, trade.exit_date, i),
           "entry": round(float(trade.entry_price) * scale, 2) if trade.entry_price is not None else None,
           "exit": round(float(trade.exit_price) * scale, 2) if trade.exit_price is not None else None,
           "target": round(float(target) * scale, 2) if target is not None else None,
           "net_pct": round(float(trade.net_pct), 3) if trade.net_pct is not None else None, "r": None}
    if trade.status == "closed":
        d = derive(trade.entry_price, trade.exit_price, trade.stop, planned_entry, cost, side, trade.held, borrow)
        out["r"] = round(d["r"], 3) if d is not None else None
        if d is not None:
            out["calc"] = {key: (round(v * scale, 4) if key in ("entry", "exit", "stop", "risk", "gross", "cost", "borrow", "net")
                                 else v) for key, v in d.items() if key != "r"} | {"r": out["r"]}
            out["same_bar"] = same_bar(feat, trade, side, target)
    return out


def same_bar(feat: pd.DataFrame, trade: meanrev.Trade, side: str, target: float | None) -> bool:
    """A kilepes napjan a stop es a cel is elerheto volt-e (a pesszimista szabaly dontott)."""
    if trade.reason != "stop" or target is None or trade.exit_date is None:
        return False
    row = feat.loc[trade.exit_date]
    if side == "short":
        return bool(row["high"] >= trade.stop and row["low"] <= target)
    return bool(row["low"] <= trade.stop and row["high"] >= target)


# ---------------------------------------------------------------------------
# Strategiak ugyanazon a ponton
# ---------------------------------------------------------------------------
def meanrev_compare(name: str, params: PullbackParams, symbol: str, df: pd.DataFrame, i: int, scale: float,
                    mctx: market.MarketContext | None, earnings_dates: list | None) -> dict:
    out = {"strategy": name, "signaled": False}
    if i < meanrev.WARMUP:
        return out
    feat = meanrev.features(df, params)
    regime = meanrev.regime_series(feat, mctx.regime_frame(symbol, df)) if mctx is not None else None
    if not bool(meanrev.setup_mask(feat, params, regime).iloc[i]):
        return out
    if meanrev.earnings_blocked(feat.index[i].date(), earnings_dates, params):
        return out | {"blocked": "earnings"}
    trade = meanrev.simulate_trade(symbol, feat, meanrev.exit_flags(feat, params).to_numpy(), i, params)
    return out | {"signaled": True} | trade_view(trade, feat, i, scale, trade.limit, params.total_cost_bps)


# ---------------------------------------------------------------------------
# Referencia-kotes es veletlen alapvonal
# ---------------------------------------------------------------------------
def atr_series(df: pd.DataFrame) -> pd.Series:
    return indicators.atr(df, ATR_PERIOD)


def distance_levels(close: float, atr: float, stop_atr: float, target_atr: float | None,
                    side: str = "long") -> tuple[float, float | None]:
    sign = 1.0 if side == "long" else -1.0
    return close - sign * stop_atr * atr, (close + sign * target_atr * atr if target_atr else None)


def distance_sim(feat: pd.DataFrame, atr: np.ndarray, d: int, stop_atr: float, target_atr: float | None,
                 days: int, cost: float, side: str = "long", mirrored=None, borrow: float = 0.0) -> meanrev.Trade | None:
    if not atr[d] > 0:
        return None
    stop, target = distance_levels(float(feat["close"].iloc[d]), float(atr[d]), stop_atr, target_atr, side)
    return simulate(feat, d, math.inf, stop, target, days, cost, side, mirrored, borrow)


def distance_trade(feat: pd.DataFrame, atr: np.ndarray, d: int, stop_atr: float, target_atr: float | None,
                   days: int, cost: float, side: str = "long", mirrored=None, borrow: float = 0.0) -> float | None:
    """Kovetkezo nyitasos kotes a d napon: long stop = zaro - stop_atr x ATR, cel = zaro + target_atr x ATR;
    short tukrozve."""
    trade = distance_sim(feat, atr, d, stop_atr, target_atr, days, cost, side, mirrored, borrow)
    if trade is None or trade.status != "closed":
        return None
    return r_multiple(trade.entry_price, trade.exit_price, trade.stop, float(feat["close"].iloc[d]), cost, side,
                      trade.held, borrow)


def baseline(feat: pd.DataFrame, atr: np.ndarray, ok: np.ndarray, i: int, stop_atr: float, target_atr: float | None,
             days: int, cost: float, seed: int, side: str = "long", mirrored=None, borrow: float = 0.0) -> dict:
    """`ok`: a lehetseges dontesi pontok (sampler.eligible); a vizsgalt pont kimarad. Short dontesnel short
    kotesek a veletlen napokon."""
    ok = ok.copy()
    ok[i] = False
    days_pool = np.flatnonzero(ok)
    if not len(days_pool):
        return {"n": 0, "mean_r": None}
    rng = np.random.default_rng(seed)
    picks = rng.choice(days_pool, size=min(BASELINE_K, len(days_pool)), replace=False)
    rs = [r for r in (distance_trade(feat, atr, int(d), stop_atr, target_atr, days, cost, side, mirrored, borrow)
                      for d in sorted(picks)) if r is not None]
    return {"n": len(rs), "mean_r": round(float(np.mean(rs)), 3) if rs else None,
            "win": round(float(np.mean(np.array(rs) > 0)), 3) if rs else None,
            "stop_atr": round(stop_atr, 2), "target_atr": round(target_atr, 2) if target_atr else None, "days": days,
            "side": side}


# ---------------------------------------------------------------------------
# Kiserleti short motor ugyanazon a ponton
# ---------------------------------------------------------------------------
SHORT_DEFAULT_DAYS = 20


def short_compare(symbol: str, df: pd.DataFrame, i: int, scale: float, settings: Settings,
                  mctx: market.MarketContext | None, earnings_dates: list | None, cost: float,
                  mirrored=None, borrow: float = 0.0) -> dict:
    """A kiserleti short motor (short.py) a t=i napon, CSAK az addigi adatbol (megerositett pivotok). Ha egy
    megerositett letores minden szuron atment: kotes a kovetkezo nyitason (ha a nyito nem a gap-vedelem alatt
    van), a terv stopjaval, celjaval es idotavjaval. Kiserleti: a short oldal nincs backtesttel igazolva."""
    from .. import patterns, short
    from .sampler import DETECTION_WINDOW, MAX_DAYS

    out = {"strategy": "short", "signaled": False, "experimental": True}
    if i < 210:
        return out
    window = df.iloc[max(0, i - DETECTION_WINDOW): i + 1]
    enriched = indicators.enrich(window, settings.structure.atr_period)
    matches = patterns.detect(symbol, enriched, settings.patterns, settings.structure, confirmed_only=True)
    if not any(m.direction == "bearish" for m in matches):
        return out
    bench = mctx.regime_frame(symbol, df) if mctx is not None else None
    bench = bench.loc[: df.index[i]] if bench is not None else None
    day = df.index[i].date()
    upcoming = sorted(d for d in (earnings_dates or []) if d >= day)
    signals = short.evaluate(symbol, enriched, matches, settings, benchmark=bench,
                             earnings_date=upcoming[0] if upcoming else None)
    breakdowns = [x for x in signals if x.kind == "breakdown"]
    if not breakdowns:
        return out
    best = breakdowns[0]
    checks = [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in best.checks]
    out |= {"pattern": best.pattern.name, "checks": checks}
    if not best.qualifies(settings.entry):
        return out | {"signaled": True, "status": "filtered"}
    feat = meanrev.features(df, settings.strategies.pullback.params)
    plan = best.plan
    days = min(plan.horizon or SHORT_DEFAULT_DAYS, MAX_DAYS)
    if float(df["open"].iloc[i + 1]) < plan.entry_limit:
        return out | {"signaled": True, "status": "unfilled"}
    trade = simulate(feat, i, math.inf, plan.stop, plan.target, days, cost, "short", mirrored, borrow)
    return out | {"signaled": True} | trade_view(trade, feat, i, scale, plan.entry, cost, "short", borrow, plan.target)


# ---------------------------------------------------------------------------
# Egy dontes kiertekelese
# ---------------------------------------------------------------------------
def user_trade(feat: pd.DataFrame, i: int, decision: dict, scale: float, cost: float, mirrored, borrow: float,
               atr_norm: float | None) -> tuple[dict, dict]:
    """A felhasznalo kotese (normalizalt aron) + a kotes parameterei ATR-ben (az alternativakhoz)."""
    side = side_of(decision)
    sign = 1.0 if side == "long" else -1.0
    planned = decision["limit"] if decision["entry"] == "limit" else 100.0
    limit = decision["limit"] / scale if decision["entry"] == "limit" else math.inf
    stop = decision["stop"] / scale
    target = decision["target"] / scale if decision.get("target") else None
    days = int(decision["days"])
    trade = simulate(feat, i, limit, stop, target, days, cost, side, mirrored, borrow)
    user = trade_view(trade, feat, i, scale, planned / scale, cost, side, borrow, target)
    params = {"side": side, "stop_atr": REFERENCE["stop_atr"], "target_atr": None, "days": days}
    if atr_norm:
        params["stop_atr"] = sign * (planned - decision["stop"]) / atr_norm
        params["target_atr"] = sign * (decision["target"] - planned) / atr_norm if decision.get("target") else None
        user["stop_atr"] = round(params["stop_atr"], 3)
        if decision.get("target"):
            user["rr"] = round(params["target_atr"] / params["stop_atr"], 3) if params["stop_atr"] > 0 else None
    return user, params


def evaluate(symbol: str, df: pd.DataFrame, i: int, decision: dict, settings: Settings, seed: int,
             mctx: market.MarketContext | None = None, earnings_dates: list | None = None,
             breakout: dict | None = None) -> dict:
    """A dontes (normalizalt arakkal) kimenetele + az osszevetes + az itelet es a levezetes + a jovobeli gyertyak."""
    from . import review

    close = float(df["close"].iloc[i])
    scale = 100.0 / close
    cost = cost_bps(settings)
    borrow = borrow_bps(settings)
    feat = meanrev.features(df, settings.strategies.pullback.params)
    mirrored = mirror(feat)
    atr = atr_series(df).to_numpy()
    atr_norm = float(atr[i] * scale) if atr[i] > 0 else None

    side = side_of(decision)
    user, params = None, {"side": None, "stop_atr": REFERENCE["stop_atr"], "target_atr": None, "days": REFERENCE["days"]}
    if side is not None:
        user, params = user_trade(feat, i, decision, scale, cost, mirrored, borrow, atr_norm)

    reference = distance_trade(feat, atr, i, REFERENCE["stop_atr"], None, REFERENCE["days"], cost)
    strategies = [
        meanrev_compare("pullback", settings.strategies.pullback.params, symbol, df, i, scale, mctx, earnings_dates),
        meanrev_compare("deep", settings.strategies.deep.params, symbol, df, i, scale, mctx, earnings_dates),
        breakout or {"strategy": "breakout", "signaled": False},          # a keszletbol (breakouts.py)
        short_compare(symbol, df, i, scale, settings, mctx, earnings_dates, cost, mirrored, borrow),
    ]
    end = min(i + FUTURE_BARS, len(df) - 1)
    future = [{"i": k - i, "o": round(float(df["open"].iloc[k]) * scale, 2), "h": round(float(df["high"].iloc[k]) * scale, 2),
               "l": round(float(df["low"].iloc[k]) * scale, 2), "c": round(float(df["close"].iloc[k]) * scale, 2)}
              for k in range(i + 1, end + 1)]
    result = {"user": user, "strategies": strategies, "cost_bps": cost, "borrow_bps": borrow,
              "reference": {"r": round(reference, 3) if reference is not None else None} | REFERENCE,
              "baseline": baseline(feat, atr, eligible(df), i, max(params["stop_atr"], 0.05), params["target_atr"],
                                   params["days"], cost, seed, side or "long", mirrored, borrow),
              "future": future, "symbol": symbol, "date": f"{df.index[i]:%Y-%m-%d}", "atr": round(atr_norm, 3) if atr_norm else None}
    result["review"] = review.build(symbol, df, feat, mirrored, atr, i, decision, result, params, settings, mctx,
                                    earnings_dates, scale, cost, borrow)
    return result


# ---------------------------------------------------------------------------
# Kor-statisztika
# ---------------------------------------------------------------------------
def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(float(np.mean(values)), 3) if values else None


VERDICTS = ("good", "lucky", "unlucky", "bad")


def _side_stats(tasks: list[dict]) -> dict:
    rs = [t["result"]["user"]["r"] for t in tasks if (t["result"].get("user") or {}).get("r") is not None]
    return {"entered": len(tasks), "filled": len(rs), "mean_r": _mean(rs),
            "win": round(float(np.mean(np.array(rs) > 0)), 3) if rs else None}


def summary(tasks: list[dict]) -> dict:
    decided = [t for t in tasks if t.get("decision")]
    entered = [t for t in decided if side_of(t["decision"]) is not None]
    skipped = [t for t in decided if side_of(t["decision"]) is None]
    closed = [t for t in entered if (t["result"].get("user") or {}).get("r") is not None]
    unfilled = [t for t in entered if (t["result"].get("user") or {}).get("status") == "unfilled"]
    rs = [t["result"]["user"]["r"] for t in closed]
    stops = [t["result"]["user"]["stop_atr"] for t in entered if (t["result"].get("user") or {}).get("stop_atr") is not None]
    strategies: dict[str, dict] = {}
    for name in ("pullback", "deep", "breakout", "short"):
        sig = [s for t in decided for s in t["result"]["strategies"] if s["strategy"] == name and s.get("signaled")]
        vals = [s.get("r") for s in sig if s.get("r") is not None]
        strategies[name] = {"signaled": len(sig), "n": len(vals), "mean_r": _mean(vals),
                            "win": round(float(np.mean(np.array(vals) > 0)), 3) if vals else None}

    def good(t: dict) -> bool:
        res = t["result"]
        return ((res["reference"].get("r") or -9) >= GOOD_R
                or any((s.get("r") or -9) >= GOOD_R for s in res["strategies"] if s.get("signaled")))

    return {"tasks": len(tasks), "decided": len(decided), "entered": len(entered), "skipped": len(skipped),
            "filled": len(closed), "unfilled": len(unfilled),
            "win": round(float(np.mean(np.array(rs) > 0)), 3) if rs else None, "mean_r": _mean(rs),
            "total_r": round(float(np.sum(rs)), 3) if rs else None,
            "baseline_r": _mean([t["result"]["baseline"].get("mean_r") for t in closed]),
            "reference_r": _mean([t["result"]["reference"].get("r") for t in decided]),
            "strategies": strategies,
            "missed_good": sum(1 for t in skipped if good(t)), "good_total": sum(1 for t in decided if good(t)),
            "stop_atr_median": round(float(np.median(stops)), 3) if stops else None,
            "tight_share": round(float(np.mean(np.array(stops) < TIGHT_ATR)), 3) if stops else None,
            "stop_buckets": [{"lo": lo, "hi": None if math.isinf(hi) else hi,
                              "n": int(sum(1 for s in stops if lo <= s < hi))} for lo, hi in STOP_BUCKETS],
            "categories": {c: sum(1 for t in decided if t["category"] == c) for c in ("strategy", "pattern", "short", "nothing")},
            "sides": {side: _side_stats([t for t in entered if side_of(t["decision"]) == side]) for side in SIDES},
            "verdicts": {v: sum(1 for t in decided if ((t["result"].get("review") or {}).get("verdict") or {}).get("category") == v)
                         for v in VERDICTS},
            "reviewed": sum(1 for t in decided if (t["result"].get("review") or {}).get("verdict"))}
