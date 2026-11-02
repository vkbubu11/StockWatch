"""Statisztikam oldal (#78): a felhasznalo SAJAT ertekelt dontesei - mutatok, R-eloszlas a strategiakkal es a veletlen
alapvonallal osszevetve, toke-gorbe R-ben, Monte Carlo (a sorrend szerepe), naptar-hoterkep, bontas.

Csak R (nincs penzbeli ertek), leiro szoveg. Egy dontes R-je a kezelt kotese (#76), ha volt kezeles, kulonben a
terv szerinti. A meg be nem adott napi kihivas / vizsga (HIDDEN_SQL) kimarad. A `stats_excluded` jelzo (#017) csak az
admin-osszegzest erinti: a sajat oldal mindenkinek mukodik.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import db
from .practice import scoring
from .practice.session import HIDDEN_SQL

PERIODS = {"30": 30, "90": 90, "all": None}
SOURCES = {"all": "TRUE", "practice": "s.daily IS NULL AND NOT s.exam", "daily": "s.daily IS NOT NULL", "exam": "s.exam"}
MIN_N = 20                                       # ennel kevesebb lezart kotesnel a hisztogram es a Monte Carlo helyett uzenet
EDGES = list(range(-3, 6))                       # R-sav hatarai: < -3, -3..-2, ..., 4..5, >= 5
MC_RUNS = 1000
MC_SEED = 78
CAL_DAYS = 84                                    # naptar: az utolso 12 het
LIMIT = 2000                                     # a legutobbi ennyi dontes
MISS = ("bad", "lucky")


def _load(user_id: int, period: str, source: str) -> list[dict]:
    days = PERIODS[period]
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT t.decided_at, t.kind, t.verdict, t.r, t.stop_atr, t.category, t.source, s.lesson, "
            "t.decision->>'action', t.quality, t.result FROM practice_tasks t JOIN practice_sessions s ON s.id = t.session_id "
            f"WHERE s.user_id = %s AND t.decision IS NOT NULL AND NOT {HIDDEN_SQL} AND ({SOURCES[source]}) "
            + ("AND t.decided_at >= now() - make_interval(days => %s) " if days else "")
            + "ORDER BY t.decided_at DESC, t.k DESC LIMIT %s",
            (user_id, *((days,) if days else ()), LIMIT)).fetchall()
    out = []
    for decided, kind, verdict, r, stop_atr, category, src, lesson, action, quality, blob in reversed(rows):
        res = json.loads(gzip.decompress(blob)) if blob is not None and (kind or "decision") == "decision" else {}
        manage = res.get("manage") or {}
        out.append({"at": decided, "kind": kind or "decision", "verdict": verdict, "category": category, "source": src,
                    "lesson": lesson, "side": None if action in (None, "skip") else "short" if action == "short" else "long",
                    "r": manage["r"] if manage.get("r") is not None else r, "stop_atr": stop_atr, "quality": quality,
                    "strategies": [x["r"] for x in res.get("strategies", []) if x.get("signaled") and x.get("r") is not None],
                    "baseline": (res.get("baseline") or {}).get("mean_r")})
    return out


# ---------------------------------------------------------------------------
# Mutatok (tiszta fuggvenyek: egysegtesztelhetok)
# ---------------------------------------------------------------------------
def max_drawdown(rs) -> float:
    """A toke-gorbe (R osszeg) legnagyobb visszaesese a csucstol (0-tol indul)."""
    cum = np.concatenate([[0.0], np.cumsum(rs)])
    return float(np.max(np.maximum.accumulate(cum) - cum))


def losing_streak(rs) -> int:
    best = run = 0
    for r in rs:
        run = run + 1 if r < 0 else 0
        best = max(best, run)
    return best


def metrics(rs: list[float], decided: int, entered: int) -> dict:
    a = np.array(rs, dtype=float)
    wins, losses = a[a > 0], a[a < 0]
    return {"decisions": decided, "entered": entered, "closed": len(a),
            "entry_rate": round(entered / decided, 3) if decided else None,
            "win_rate": round(len(wins) / len(a), 3) if len(a) else None,
            "avg_win": round(float(wins.mean()), 3) if len(wins) else None,
            "avg_loss": round(float(losses.mean()), 3) if len(losses) else None,
            "expectancy": round(float(a.mean()), 3) if len(a) else None,
            "total_r": round(float(a.sum()), 3) if len(a) else None,
            "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and len(wins) else None,
            "losing_streak": losing_streak(a), "max_drawdown": round(max_drawdown(a), 3) if len(a) else None}


def histogram(values: list[float]) -> list[float]:
    """Arany savonkent (a szelso savok a kilogo ertekeket is tartalmazzak)."""
    if not values:
        return [0.0] * (len(EDGES) + 1)
    idx = np.searchsorted(EDGES, np.array(values, dtype=float), side="right")
    counts = np.bincount(idx, minlength=len(EDGES) + 1)
    return [round(float(c) / len(values), 4) for c in counts]


def monte_carlo(rs: list[float], runs: int = MC_RUNS, seed: int = MC_SEED) -> dict:
    """Ugyanazok a dontesek `runs`-szor keverve: a legnagyobb visszaeses es a leghosszabb vesztes sorozat 5 / 50 / 95%-os
    sava. Seedelt: ugyanarra a bemenetre ugyanaz."""
    a = np.array(rs, dtype=float)
    perms = np.random.default_rng(seed).permuted(np.tile(a, (runs, 1)), axis=1)
    cum = np.concatenate([np.zeros((runs, 1)), np.cumsum(perms, axis=1)], axis=1)
    dd = np.max(np.maximum.accumulate(cum, axis=1) - cum, axis=1)
    neg = perms < 0
    count = np.cumsum(neg, axis=1)
    streak = np.max(count - np.maximum.accumulate(np.where(neg, 0, count), axis=1), axis=1)
    band = lambda x: {str(p): round(float(np.percentile(x, p)), 2) for p in (5, 50, 95)}   # noqa: E731
    return {"runs": runs, "drawdown": band(dd), "streak": band(streak),
            "actual": {"drawdown": round(max_drawdown(a), 2), "streak": losing_streak(a)}}


def _group(rows: list[dict]) -> dict:
    rs = [x["r"] for x in rows if x["r"] is not None]
    misses = sum(1 for x in rows if x["verdict"] in MISS)
    return {"n": len(rows), "entered": sum(1 for x in rows if x["side"]), "closed": len(rs),
            "win_rate": round(float(np.mean(np.array(rs) > 0)), 3) if rs else None,
            "mean_r": round(float(np.mean(rs)), 3) if rs else None, "misses": misses,
            "miss_rate": round(misses / len(rows), 3) if rows else None,
            "tight": sum(1 for x in rows if x["stop_atr"] is not None and x["stop_atr"] < scoring.TIGHT_ATR)}


def breakdown(rows: list[dict]) -> dict:
    keys = {"category": lambda x: x["category"], "pattern": lambda x: x["source"] if x["category"] == "pattern" else None,
            "level": lambda x: x["lesson"], "side": lambda x: x["side"] or "skip"}
    out = {}
    for name, key in keys.items():
        groups: dict = {}
        for x in rows:
            k = key(x)
            if k is not None:
                groups.setdefault(k, []).append(x)
        out[name] = sorted(({"key": k} | _group(v) for k, v in groups.items()),
                           key=lambda g: (-(g["miss_rate"] or 0), -g["n"]))
    return out


def calendar(rows: list[dict], today: date) -> list[dict]:
    first = today - timedelta(days=CAL_DAYS - 1)
    days: dict[date, list] = {}
    for x in rows:
        d = x["at"].astimezone(timezone.utc).date()
        if d >= first:
            days.setdefault(d, []).append(x)
    out = []
    for n in range(CAL_DAYS):
        d = first + timedelta(days=n)
        rs = [x["r"] for x in days.get(d, []) if x["r"] is not None]
        out.append({"date": d.isoformat(), "n": len(days.get(d, [])), "mean_r": round(float(np.mean(rs)), 3) if rs else None})
    return out


def mine(user_id: int, period: str = "all", source: str = "all", today: date | None = None) -> dict:
    if period not in PERIODS or source not in SOURCES:
        raise ValueError("Ismeretlen szűrő.")
    rows = _load(user_id, period, source)
    dec = [x for x in rows if x["kind"] == "decision"]
    closed = [x for x in dec if x["r"] is not None]
    rs = [x["r"] for x in closed]
    few = len(rs) < MIN_N
    kinds: dict[str, list] = {}
    for x in rows:
        if x["kind"] != "decision":
            kinds.setdefault(x["kind"], []).append(x["quality"] or 0.0)
    return {"period": period, "source": source, "few": few, "min_n": MIN_N,
            "metrics": metrics(rs, len(dec), sum(1 for x in dec if x["side"])),
            "edges": EDGES,
            "histogram": None if few else {"mine": histogram(rs), "strategies": histogram([v for x in closed for v in x["strategies"]]),
                                           "baseline": histogram([x["baseline"] for x in closed if x["baseline"] is not None])},
            "equity": [{"r": round(x["r"], 3), "verdict": x["verdict"]} for x in closed],
            "monte_carlo": None if few else monte_carlo(rs),
            "calendar": calendar(dec, today or datetime.now(timezone.utc).date()),
            "breakdown": breakdown(dec),
            "kinds": {k: {"n": len(v), "mean_quality": round(float(np.mean(v)), 3)} for k, v in kinds.items()}}
