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


def r_multiple(entry: float, exit_: float, stop: float, planned_entry: float, cost: float) -> float | None:
    """R a koltseggel: (kilepo * (1 - c) - belepo * (1 + c)) / kockazat. A kockazat a belepo - stop; ha a nyito
    mar a stop alatt volt (azonnali zaras), a tervezett belepo - stop."""
    risk = entry - stop if entry - stop > 0 else planned_entry - stop
    if risk <= 0:
        return None
    c = cost / 10_000
    return float((exit_ * (1 - c) - entry * (1 + c)) / risk)


def practice_params(days: int, cost: float) -> PullbackParams:
    return PullbackParams(time_stop=int(days), cost_bps=cost, commission_bps=0.0)


def simulate(feat: pd.DataFrame, i: int, limit: float, stop: float, target: float | None, days: int,
             cost: float) -> meanrev.Trade:
    """A gyakorlo kotes: `limit` = math.inf -> belepes a kovetkezo nyitason."""
    exits = np.zeros(len(feat), dtype=bool)
    return meanrev.simulate_trade("practice", feat, exits, i, practice_params(days, cost), levels=(limit, stop),
                                  target=target, target_intraday=True)


def _offset(feat: pd.DataFrame, day, i: int) -> int | None:
    return None if day is None else int(feat.index.get_loc(day)) - i


def trade_view(trade: meanrev.Trade, feat: pd.DataFrame, i: int, scale: float, planned_entry: float,
               cost: float) -> dict:
    """A kotes a felfedeshez, normalizalt aron (az utolso lathato zaro = 100)."""
    out = {"status": trade.status, "stop": round(float(trade.stop) * scale, 2), "reason": trade.reason or None,
           "held": trade.held, "entry_k": _offset(feat, trade.entry_date, i), "exit_k": _offset(feat, trade.exit_date, i),
           "entry": round(float(trade.entry_price) * scale, 2) if trade.entry_price is not None else None,
           "exit": round(float(trade.exit_price) * scale, 2) if trade.exit_price is not None else None,
           "net_pct": round(float(trade.net_pct), 3) if trade.net_pct is not None else None, "r": None}
    if trade.status == "closed":
        r = r_multiple(trade.entry_price, trade.exit_price, trade.stop, planned_entry, cost)
        out["r"] = round(r, 3) if r is not None else None
    return out


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


def breakout_compare(symbol: str, df: pd.DataFrame, i: int) -> dict:
    """A Kitores: ha a labor kitores-cache-eben t-n kitores van, a labor ELO kiserletenek mentett kotese
    (megerositett belepesnel a kovetkezo napi jelzes). Nincs mentett kotes: a szurok kizartak, vagy a labor
    meg nem ertekelt."""
    from .. import lab

    day = df.index[i]
    out = {"strategy": "breakout", "signaled": False}
    cache = lab.load_cache(symbol) or {}
    if not any(pd.Timestamp(e[0]) == day for e in cache.get("events", [])):
        return out
    live = next((e for e in lab.load_store().get("experiments", []) if e.get("live")), None)
    results = lab.load_results(live["id"]) if live else []
    if not results:
        return out | {"signaled": True, "status": "unknown"}
    nxt = df.index[i + 1] if i + 1 < len(df) else None
    hit = next((x for x in results if x.symbol == symbol
                and (x.date == day or (x.kind == "confirm" and nxt is not None and x.date == nxt))), None)
    if hit is None:
        return out | {"signaled": True, "status": "filtered"}
    return out | {"signaled": True, "status": "closed", "r": round(float(hit.r), 3), "reason": hit.reason,
                  "held": int(hit.held), "kind": hit.kind}


# ---------------------------------------------------------------------------
# Referencia-kotes es veletlen alapvonal
# ---------------------------------------------------------------------------
def atr_series(df: pd.DataFrame) -> pd.Series:
    return indicators.atr(df, ATR_PERIOD)


def distance_trade(feat: pd.DataFrame, atr: np.ndarray, d: int, stop_atr: float, target_atr: float | None,
                   days: int, cost: float) -> float | None:
    """Kovetkezo nyitasos kotes a d napon: stop = zaro - stop_atr x ATR, cel = zaro + target_atr x ATR."""
    close = float(feat["close"].iloc[d])
    if not atr[d] > 0:
        return None
    stop = close - stop_atr * atr[d]
    target = close + target_atr * atr[d] if target_atr else None
    trade = simulate(feat, d, math.inf, stop, target, days, cost)
    if trade.status != "closed":
        return None
    return r_multiple(trade.entry_price, trade.exit_price, stop, close, cost)


def baseline(feat: pd.DataFrame, atr: np.ndarray, ok: np.ndarray, i: int, stop_atr: float, target_atr: float | None,
             days: int, cost: float, seed: int) -> dict:
    """`ok`: a lehetseges dontesi pontok (sampler.eligible); a vizsgalt pont kimarad."""
    ok = ok.copy()
    ok[i] = False
    days_pool = np.flatnonzero(ok)
    if not len(days_pool):
        return {"n": 0, "mean_r": None}
    rng = np.random.default_rng(seed)
    picks = rng.choice(days_pool, size=min(BASELINE_K, len(days_pool)), replace=False)
    rs = [r for r in (distance_trade(feat, atr, int(d), stop_atr, target_atr, days, cost) for d in sorted(picks))
          if r is not None]
    return {"n": len(rs), "mean_r": round(float(np.mean(rs)), 3) if rs else None,
            "win": round(float(np.mean(np.array(rs) > 0)), 3) if rs else None,
            "stop_atr": round(stop_atr, 2), "target_atr": round(target_atr, 2) if target_atr else None, "days": days}


# ---------------------------------------------------------------------------
# Egy dontes kiertekelese
# ---------------------------------------------------------------------------
def evaluate(symbol: str, df: pd.DataFrame, i: int, decision: dict, settings: Settings, seed: int,
             mctx: market.MarketContext | None = None, earnings_dates: list | None = None) -> dict:
    """A dontes (normalizalt arakkal) kimenetele + az osszevetes + a jovobeli gyertyak a porgeteshez."""
    close = float(df["close"].iloc[i])
    scale = 100.0 / close
    cost = cost_bps(settings)
    feat = meanrev.features(df, settings.strategies.pullback.params)
    atr = atr_series(df).to_numpy()
    atr_norm = float(atr[i] * scale) if atr[i] > 0 else None

    user, stop_atr, target_atr, days = None, REFERENCE["stop_atr"], None, REFERENCE["days"]
    if decision["action"] == "enter":
        planned = decision["limit"] if decision["entry"] == "limit" else 100.0
        limit = decision["limit"] / scale if decision["entry"] == "limit" else math.inf
        stop = decision["stop"] / scale
        target = decision["target"] / scale if decision.get("target") else None
        days = int(decision["days"])
        trade = simulate(feat, i, limit, stop, target, days, cost)
        user = trade_view(trade, feat, i, scale, planned / scale, cost)
        if atr_norm:
            stop_atr = (planned - decision["stop"]) / atr_norm
            target_atr = (decision["target"] - planned) / atr_norm if decision.get("target") else None
            user["stop_atr"] = round(stop_atr, 3)

    reference = distance_trade(feat, atr, i, REFERENCE["stop_atr"], None, REFERENCE["days"], cost)
    strategies = [
        meanrev_compare("pullback", settings.strategies.pullback.params, symbol, df, i, scale, mctx, earnings_dates),
        meanrev_compare("deep", settings.strategies.deep.params, symbol, df, i, scale, mctx, earnings_dates),
        breakout_compare(symbol, df, i),
    ]
    end = min(i + FUTURE_BARS, len(df) - 1)
    future = [{"i": k - i, "o": round(float(df["open"].iloc[k]) * scale, 2), "h": round(float(df["high"].iloc[k]) * scale, 2),
               "l": round(float(df["low"].iloc[k]) * scale, 2), "c": round(float(df["close"].iloc[k]) * scale, 2)}
              for k in range(i + 1, end + 1)]
    return {"user": user, "strategies": strategies, "cost_bps": cost,
            "reference": {"r": round(reference, 3) if reference is not None else None} | REFERENCE,
            "baseline": baseline(feat, atr, eligible(df), i, max(stop_atr, 0.05), target_atr, days, cost, seed),
            "future": future, "symbol": symbol, "date": f"{df.index[i]:%Y-%m-%d}", "atr": round(atr_norm, 3) if atr_norm else None}


# ---------------------------------------------------------------------------
# Kor-statisztika
# ---------------------------------------------------------------------------
def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(float(np.mean(values)), 3) if values else None


def summary(tasks: list[dict]) -> dict:
    decided = [t for t in tasks if t.get("decision")]
    entered = [t for t in decided if t["decision"]["action"] == "enter"]
    skipped = [t for t in decided if t["decision"]["action"] == "skip"]
    closed = [t for t in entered if (t["result"].get("user") or {}).get("r") is not None]
    unfilled = [t for t in entered if (t["result"].get("user") or {}).get("status") == "unfilled"]
    rs = [t["result"]["user"]["r"] for t in closed]
    stops = [t["result"]["user"]["stop_atr"] for t in entered if (t["result"].get("user") or {}).get("stop_atr") is not None]
    strategies: dict[str, dict] = {}
    for name in ("pullback", "deep", "breakout"):
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
            "categories": {c: sum(1 for t in decided if t["category"] == c) for c in ("strategy", "pattern", "nothing")}}
