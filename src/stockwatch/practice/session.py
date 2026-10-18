"""Gyakorlo munkamenet: letrehozas, rejtett feladat, dontes, felfedes, osszegzes.

A felfedes elott az API valasza CSAK ezt tartalmazza: a munkamenet es a feladat veletlen (betus) azonositoja,
a sorszam, es a normalizalt chart (index-tengely -119 ... 0, OHLC ugy, hogy az utolso lathato zaro = 100,
volumen a 20 napos atlagahoz viszonyitva, EMA-k a normalizalt arbol, ATR). Papir, datum, nyers ar, nyers
volumen es a helyzet kategoriaja nem. A dontes utan nem modosithato.
"""
from __future__ import annotations

import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, model_validator

from .. import indicators, market, usage, workspace
from ..config import Settings, load_settings
from ..locks import file_lock
from . import sampler, scoring, store
from .templates import STRATEGY_LABELS, TEMPLATES

FEW = 10
EMAS = (20, 50, 200)


class Decision(BaseModel):
    """Normalizalt aron (az utolso lathato zaro = 100)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["skip", "enter"]
    entry: Literal["open", "limit"] = "open"
    limit: float | None = None
    stop: float | None = None
    target: float | None = None
    days: int = 10

    @model_validator(mode="after")
    def _check(self) -> "Decision":
        if self.action == "skip":
            return self
        if not 1 <= self.days <= sampler.MAX_DAYS:
            raise ValueError(f"days: 1 es {sampler.MAX_DAYS} kozott")
        if self.entry == "limit":
            if self.limit is None or not 0 < self.limit <= 150:
                raise ValueError("limit: 0 es 150 kozott (normalizalt ar)")
        else:
            self.limit = None
        ref = self.limit if self.entry == "limit" else 100.0
        if self.stop is None:
            raise ValueError("stop: kotelezo")
        if not 0 < self.stop < ref:
            raise ValueError("stop: a belepo alatt legyen")
        if self.target is not None and not ref < self.target <= 1000:
            raise ValueError("target: a belepo felett legyen")
        for name in ("limit", "stop", "target"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name}: ervenytelen szam")
        return self


@dataclass
class Line:
    key: str
    args: list[str] = field(default_factory=list)
    numeric: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return re.sub(r"\{(\d+)(?::n)?\}", lambda m: self.args[int(m.group(1))], TEMPLATES[self.key])

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.args, "numeric": self.numeric, "text": self.text}


# ---------------------------------------------------------------------------
# Rejtett chart
# ---------------------------------------------------------------------------
def _r2(v) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), 2)


def hidden_chart(df: pd.DataFrame, i: int) -> dict:
    """A dontesi pontig (t=i) lathato VISIBLE_BARS gyertya, normalizalva. Minden mutato csak az addigi adatbol."""
    hist = df.iloc[: i + 1]
    scale = 100.0 / float(hist["close"].iloc[-1])
    emas = {f"ema{n}": (indicators.ema(hist["close"], n) * scale).to_numpy() for n in EMAS}
    vol = hist["volume"]
    rel = (vol / vol.rolling(20, min_periods=20).mean()).to_numpy()
    atr = float(scoring.atr_series(hist).iloc[-1]) * scale
    start = max(0, len(hist) - sampler.VISIBLE_BARS)
    o, h, l, c = (hist[k].to_numpy() * scale for k in ("open", "high", "low", "close"))
    bars = []
    for k in range(start, len(hist)):
        bar = {"i": k - (len(hist) - 1), "o": _r2(o[k]), "h": _r2(h[k]), "l": _r2(l[k]), "c": _r2(c[k]), "v": _r2(rel[k])}
        bar.update({name: _r2(series[k]) for name, series in emas.items()})
        bars.append(bar)
    return {"bars": bars, "atr": _r2(atr), "last": 100.0}


# ---------------------------------------------------------------------------
# Munkamenet
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public(session: dict) -> dict:
    """A munkamenet a listahoz / a fejlechez - papir es datum nelkul (felfedett feladatoknal sem)."""
    decided = sum(1 for t in session["tasks"] if t.get("decision"))
    return {"id": session["id"], "created": session["created"], "rounds": len(session["tasks"]), "code": session["code"],
            "decided": decided, "done": decided == len(session["tasks"]), "warning": session.get("warning"),
            "next": next((k for k, t in enumerate(session["tasks"]) if not t.get("decision")), None),
            "mean_r": session.get("summary", {}).get("mean_r") if decided == len(session["tasks"]) else None}


def create(settings: Settings | None = None, ws: str = workspace.DEFAULT, seed: int | None = None,
           rounds: int = 20, mix: dict | None = None, code: str | None = None) -> dict:
    settings = settings or load_settings()
    pool = store.load_pool()
    if not pool:
        raise ValueError("Meg nincs feladatkeszlet - epitsd fel (Gyakorlas ful, vagy: python cli.py practice-pool).")
    warning = None
    if code:
        seed, fp, rounds = sampler.parse_code(code)
        if fp != int(pool["fingerprint"][:8], 16):
            warning = "other_pool"
    if seed is None:
        seed = secrets.randbelow(sampler.SEED_MAX)
    if not 0 <= int(seed) < sampler.SEED_MAX:
        raise ValueError("seed: 0 es 2^31 kozott")
    with usage.meter(ws, "practice"):
        picks = sampler.sample(pool, int(seed), int(rounds), mix)
        session = {"id": store.new_id(), "created": _now(), "seed": int(seed), "rounds": int(rounds),
                   "mix": sampler.normalize_mix(mix), "pool_fingerprint": pool["fingerprint"],
                   "code": sampler.share_code(int(seed), pool["fingerprint"], int(rounds)), "warning": warning,
                   "tasks": [p | {"task_id": store.new_id(10), "decision": None, "result": None} for p in picks]}
        store.save_session(session, ws)
    return public(session)


def _load(session_id: str, ws: str) -> dict:
    session = store.load_session(session_id, ws)
    if session is None:
        raise LookupError("Nincs ilyen munkamenet.")
    return session


def _task(session: dict, k: int) -> dict:
    if not 0 <= k < len(session["tasks"]):
        raise LookupError("Nincs ilyen feladat.")
    return session["tasks"][k]


def _frame(task: dict, settings: Settings) -> tuple[pd.DataFrame, int]:
    df = sampler.load_prices(task["symbol"], settings)
    day = pd.Timestamp(task["date"])
    if df is None or day not in df.index:
        raise ValueError("A feladat arfolyamadata mar nem erheto el (a tar megvaltozott).")
    i = int(df.index.get_loc(day))
    if i + sampler.FUTURE_BARS >= len(df):
        raise ValueError("A feladat jovobeli ablaka nem teljes.")
    return df, i


def reveal(task: dict) -> dict:
    return task["result"] | {"category": task["category"], "source": task["source"], "decision": task["decision"]}


def task_payload(session_id: str, k: int, settings: Settings | None = None, ws: str = workspace.DEFAULT) -> dict:
    settings = settings or load_settings()
    session = _load(session_id, ws)
    task = _task(session, k)
    df, i = _frame(task, settings)
    out = {"session": session["id"], "k": k, "rounds": len(session["tasks"]), "task_id": task["task_id"],
           "chart": hidden_chart(df, i), "max_days": sampler.MAX_DAYS, "decided": task.get("decision") is not None}
    if out["decided"]:
        out["reveal"] = reveal(task)
    return out


def decide(session_id: str, k: int, body: dict, settings: Settings | None = None,
           ws: str = workspace.DEFAULT) -> dict:
    settings = settings or load_settings()
    decision = Decision(**(body or {}))
    with file_lock(store.session_path(session_id, ws)), usage.meter(ws, "practice"):
        session = _load(session_id, ws)
        task = _task(session, k)
        if task.get("decision") is not None:
            raise ValueError("A dontes mar rogzitve - nem modosithato.")
        df, i = _frame(task, settings)
        mctx = market.load_context(settings, refresh=False, allow_network=False)
        dates = market.HistoricalEarnings(settings, allow_network=False).dates(task["symbol"])
        task["result"] = scoring.evaluate(task["symbol"], df, i, decision.model_dump(), settings,
                                          seed=session["seed"] * 100 + k, mctx=mctx, earnings_dates=dates)
        task["decision"] = decision.model_dump()
        task["decided_at"] = _now()
        if all(t.get("decision") for t in session["tasks"]):
            session["summary"] = scoring.summary(session["tasks"])
        store.save_session(session, ws)
    return {"session": public(session), "k": k, "reveal": reveal(task)}


# ---------------------------------------------------------------------------
# Osszegzes
# ---------------------------------------------------------------------------
def _num(v, digits: int = 2) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def lines(s: dict) -> list[dict]:
    out = [Line("rounds", [str(s["decided"]), str(s["entered"]), str(s["skipped"])], [0, 1, 2])]
    if s["unfilled"]:
        out.append(Line("unfilled", [str(s["unfilled"])], [0]))
    if s["filled"]:
        out.append(Line("result", [str(s["filled"]), _num((s["win"] or 0) * 100, 1), _num(s["mean_r"]),
                                   _num(s["total_r"])], [0, 1, 2, 3]))
        if s["baseline_r"] is not None:
            out.append(Line("baseline", [_num(s["baseline_r"])], [0]))
        if s["filled"] < FEW:
            out.append(Line("few", [str(s["filled"])], [0]))
    else:
        out.append(Line("no_entries"))
    if s["reference_r"] is not None:
        out.append(Line("reference", [_num(scoring.REFERENCE["stop_atr"], 0), str(scoring.REFERENCE["days"]),
                                      _num(s["reference_r"])], [0, 1, 2]))
    for name, v in s["strategies"].items():
        if v["signaled"] and v["n"]:
            out.append(Line("strategy", [STRATEGY_LABELS[name], str(v["signaled"]), str(v["n"]), _num(v["mean_r"])],
                            [1, 2, 3]))
        elif v["signaled"]:
            out.append(Line("strategy_none", [STRATEGY_LABELS[name], str(v["signaled"])], [1]))
    if s["missed_good"]:
        out.append(Line("missed", [str(s["missed_good"]), _num(scoring.GOOD_R, 0)], [0, 1]))
    if s["stop_atr_median"] is not None:
        out.append(Line("stop_median", [_num(s["stop_atr_median"]), _num((s["tight_share"] or 0) * 100, 1),
                                        _num(scoring.TIGHT_ATR, 0)], [0, 1, 2]))
    out.append(Line("disclaimer"))
    return [ln.to_dict() for ln in out]


def summary_payload(session_id: str, ws: str = workspace.DEFAULT) -> dict:
    session = _load(session_id, ws)
    s = scoring.summary(session["tasks"])
    tasks = [{"k": k, "decided": t.get("decision") is not None} | (
        {"symbol": t["symbol"], "date": t["date"], "category": t["category"], "source": t["source"],
         "action": t["decision"]["action"], "r": (t["result"].get("user") or {}).get("r"),
         "status": (t["result"].get("user") or {}).get("status"),
         "strategies": [{"strategy": x["strategy"], "r": x.get("r"), "status": x.get("status")}
                        for x in t["result"]["strategies"] if x.get("signaled")],
         "reference_r": t["result"]["reference"].get("r"), "baseline_r": t["result"]["baseline"].get("mean_r")}
        if t.get("decision") else {}) for k, t in enumerate(session["tasks"])]
    return {"session": public(session), "summary": s, "lines": lines(s), "tasks": tasks}


def listing(ws: str = workspace.DEFAULT) -> list[dict]:
    out = []
    for session in store.sessions(ws):
        try:
            out.append(public(session))
        except (KeyError, TypeError):
            continue
    return out


def delete(session_id: str, ws: str = workspace.DEFAULT) -> bool:
    return store.delete_session(session_id, ws)
