"""Gyakorlo munkamenet: letrehozas, rejtett feladat, dontes, felfedes, osszegzes.

A felfedes elott az API valasza CSAK ezt tartalmazza: a munkamenet es a feladat veletlen (betus) azonositoja,
a sorszam, es a normalizalt chart (index-tengely -119 ... 0, OHLC ugy, hogy az utolso lathato zaro = 100,
volumen a 20 napos atlagahoz viszonyitva, EMA-k a normalizalt arbol, ATR). Papir, datum, nyers ar, nyers
volumen es a helyzet kategoriaja nem. A dontes utan nem modosithato.
"""
from __future__ import annotations

import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .. import analytics, calibration, indicators, market, progress
from ..config import Settings, load_settings
from . import kinds, sampler, scoring, store, synthetic
from .templates import STRATEGY_LABELS, Line

FEW = 10
DAILY_ROUNDS = 5                                 # a napi kihivas (#35) helyzetei
DAILY_MINUTES = 20                               # az inditastol ennyi perc a beadasra
EMAS = (20, 50, 200)


class Decision(BaseModel):
    """Normalizalt aron (az utolso lathato zaro = 100). Irany: long (a regi "enter" is long) vagy short.
    Long: a stop a belepo alatt, a cel felette; short: a stop a belepo felett, a cel alatta. A limit
    long-nal veteli (a belepo alatt teljesul), short-nal eladasi (a belepo felett teljesul)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["skip", "enter", "long", "short"]
    entry: Literal["open", "limit"] = "open"
    limit: float | None = None
    stop: float | None = None
    target: float | None = None
    days: int = 10
    confidence: int | None = None                # magabiztossag (#54), 50-95%; az iteletet nem befolyasolja

    @model_validator(mode="after")
    def _check(self) -> "Decision":
        if self.confidence is not None and not (calibration.LO <= self.confidence <= calibration.HI):
            raise ValueError(f"A magabiztosság {calibration.LO} és {calibration.HI}% között legyen.")
        if self.action == "skip":
            return self
        if self.action == "enter":
            self.action = "long"
        if not 1 <= self.days <= sampler.MAX_DAYS:
            raise ValueError(f"Az időkorlát 1 és {sampler.MAX_DAYS} nap között legyen.")
        if self.entry == "limit":
            if self.limit is None or not 0 < self.limit <= 150:
                raise ValueError("A limitár 0 és 150 között legyen (normalizált ár).")
        else:
            self.limit = None
        for name in ("limit", "stop", "target"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError("Érvénytelen szám.")
        ref = self.limit if self.entry == "limit" else 100.0
        if self.stop is None:
            raise ValueError("A stop kötelező.")
        if self.action == "long":
            if not 0 < self.stop < ref:
                raise ValueError("Longnál a stop a belépő alatt legyen.")
            if self.target is not None and not ref < self.target <= 1000:
                raise ValueError("Longnál a cél a belépő felett legyen.")
        else:
            if not ref < self.stop <= 1000:
                raise ValueError("Shortnál a stop a belépő felett legyen.")
            if self.target is not None and not 0 < self.target < ref:
                raise ValueError("Shortnál a cél a belépő alatt legyen.")
        return self


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
    # parbaj (#55): a kod nem latszik - vele a helyzetsor elore kiprobalhato lenne
    return {"id": session["id"], "created": session["created"], "rounds": len(session["tasks"]),
            "code": None if session.get("duel") else session["code"], "duel": session.get("duel"),
            "decided": decided, "done": decided == len(session["tasks"]), "warning": session.get("warning"),
            "next": next((k for k, t in enumerate(session["tasks"]) if not t.get("decision")), None),
            "mean_r": session.get("summary", {}).get("mean_r") if decided == len(session["tasks"]) else None,
            "lesson": session.get("lesson"), "daily": session.get("daily"),
            "deadline": deadline(session).isoformat(timespec="seconds") if deadline(session) else None}


def create(user_id: int, settings: Settings | None = None, seed: int | None = None,
           rounds: int = 20, mix: dict | None = None, code: str | None = None,
           only: dict | None = None, lesson: int | None = None, daily: str | None = None,
           kinds_mix: dict | None = None, duel: str | None = None) -> dict:
    """Uj kor. `only` es `lesson`: a tanulasi ut (#33) celzott feladatsora (learning.py); `daily`: a napi
    kihivas napja (daily.py); `kinds_mix`: feladattipus -> suly (#49; None: csak dontes). shortcut: a megosztasi
    kod a tipusokat nem tartalmazza - vegyes kort csak a dontesi feladatokra reprodukal."""
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
    picks = sampler.sample(pool, int(seed), int(rounds), mix, only)
    weights = kinds.normalize_kinds(kinds_mix)
    rng = np.random.default_rng([int(seed), 49])                  # a tipusok sorsolasa a seedbol, kulon folyamban
    names, probs = list(weights), list(weights.values())
    for n, pick in enumerate(picks):
        kind = names[int(rng.choice(len(names), p=probs))]
        picks[n] = pick | {"kind": kind} | (kinds.get(kind).make(pick, rng, settings) if kind != "decision" else {})
    session = {"id": store.new_id(), "created": _now(), "seed": int(seed), "rounds": int(rounds),
               "mix": sampler.normalize_mix(mix), "pool_fingerprint": pool["fingerprint"],
               "code": sampler.share_code(int(seed), pool["fingerprint"], int(rounds)), "warning": warning, "lesson": lesson,
               "daily": daily, "duel": duel,
               "tasks": [p | {"task_id": store.new_id(10), "decision": None, "result": None} for p in picks]}
    store.create_session(session, user_id)
    return public(session)


def deadline(session: dict) -> datetime | None:
    if not session.get("daily"):
        return None
    return datetime.fromisoformat(session["created"]) + timedelta(minutes=DAILY_MINUTES)


def hidden(session: dict) -> bool:
    """A napi kihivas felfedese csak a beadas utan (minden dontes megvan, vagy lejart az ido)."""
    limit = deadline(session)
    return limit is not None and not all(t.get("decision") for t in session["tasks"])         and datetime.now(timezone.utc) < limit


def _load(session_id: str, user_id: int) -> dict:
    session = store.load_session(session_id, user_id)
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


def shown(symbol: str, day: str) -> dict:
    """A felfedett papir es nap - szintetikus helyzetnel nincs valodi papir es datum."""
    return {"symbol": "Szintetikus helyzet", "date": None} if synthetic.is_synthetic(symbol) else {"symbol": symbol, "date": day}


def reveal(task: dict) -> dict:
    return task["result"] | {"category": task["category"], "source": task["source"], "decision": task["decision"],
                             "kind": task.get("kind") or "decision"} | shown(task["symbol"], task["date"])


def _hints(session: dict) -> str:
    from .. import learning                      # az itelet-szempontok (#59) a lecke beallitasa szerint

    return learning.hints(session)


def decision_tasks(tasks: list[dict]) -> list[dict]:
    """A kor-statisztika (scoring.summary) csak a dontesi feladatokbol szamol; a tobbi tipus kulon (kinds_summary)."""
    return [t for t in tasks if (t.get("kind") or "decision") == "decision"]


def kinds_summary(tasks: list[dict]) -> dict:
    out: dict[str, list[float]] = {}
    for t in tasks:
        kind = t.get("kind") or "decision"
        if kind != "decision" and t.get("decision"):
            out.setdefault(kind, []).append(float((t.get("result") or {}).get("quality", t.get("quality")) or 0.0))
    return {k: {"n": len(v), "mean_quality": round(float(np.mean(v)), 3)} for k, v in out.items()}


def task_payload(user_id: int, session_id: str, k: int, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    session = _load(session_id, user_id)
    task = _task(session, k)
    df, i = _frame(task, settings)
    out = {"session": session["id"], "k": k, "rounds": len(session["tasks"]), "task_id": task["task_id"],
           "chart": hidden_chart(df, i), "max_days": sampler.MAX_DAYS, "decided": task.get("decision") is not None,
           "kind": task.get("kind") or "decision", "prompt": (task.get("params") or {}).get("prompt"),
           "hints": _hints(session),
           # magabiztossag (#54): a 6. szinttol alapbol; maskor a felhasznalo kapcsolhatja be (Profil)
           "confidence": bool(session.get("lesson") and int(session["lesson"]) >= 6)}
    if out["decided"] and not hidden(session):
        out["reveal"] = reveal(task)
    return out


def _check_open(session: dict, task: dict) -> None:
    if task.get("decision") is not None:
        raise ValueError("A dontes mar rogzitve - nem modosithato.")
    if deadline(session) and datetime.now(timezone.utc) > deadline(session):
        raise ValueError("A napi kihivas ideje lejart.")


def _evaluate(session: dict, task: dict, k: int, body: dict, settings: Settings) -> tuple[dict, dict, dict]:
    """(valasz, ertekeles, pont) - a feladat tipusa szerint (#49)."""
    kind = task.get("kind") or "decision"
    if kind != "decision":
        module = kinds.get(kind)
        answer = module.validate(body or {}, task["params"]["prompt"])
        _check_open(session, task)
        df, i = _frame(task, settings)
        result = module.evaluate(task, df, i, answer, settings)
        result["review"] = {"verdict": kinds.verdict(result["quality"])}
        return answer, result, kinds.points(result["quality"], settings)
    try:
        decision = Decision(**(body or {}))
    except ValidationError as exc:                   # a felhasznalonak csak az elso, emberi szovegu hiba
        raise ValueError(str(exc.errors()[0]["msg"]).removeprefix("Value error, ")) from None
    _check_open(session, task)
    df, i = _frame(task, settings)
    mctx = market.load_context(settings, refresh=False, allow_network=False)
    dates = market.HistoricalEarnings(settings, allow_network=False).dates(task["symbol"])
    answer = decision.model_dump()
    confidence = answer.pop("confidence")             # az ertekeles nem latja: az itelet fuggetlen tole
    result = scoring.evaluate(task["symbol"], df, i, answer, settings, seed=session["seed"] * 100 + k,
                              mctx=mctx, earnings_dates=dates, breakout=task.get("breakout"))
    verdict = (result.get("review") or {}).get("verdict")
    if confidence is not None:
        answer["confidence"] = confidence
        result["calibration"] = calibration.points(confidence, (verdict or {}).get("category"))
    return answer, result, progress.score(verdict, settings)


def decide(user_id: int, session_id: str, k: int, body: dict, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    session = _load(session_id, user_id)
    task = _task(session, k)
    # a (masodpercekig tarto) ertekeles zar nelkul fut; zar alatt csak az ujraellenorzes es a mentes
    answer, result, points = _evaluate(session, task, k, body, settings)
    with store.editing(session_id, user_id) as (session, save, conn):
        task = _task(session, k)
        if task.get("decision") is not None:
            raise ValueError("A dontes mar rogzitve - nem modosithato.")
        task["result"] = result | {"points": points}
        task["points"] = points["points"]
        task["quality"] = result.get("quality")
        task["decision"] = answer
        task["decided_at"] = _now()
        if all(t.get("decision") for t in session["tasks"]):
            session["summary"] = scoring.summary(decision_tasks(session["tasks"]))
        save(k)
        analytics.track("decision", user_id, conn, kind=task.get("kind") or "decision", daily=bool(session.get("daily")),
                        lesson=session.get("lesson"), hints=_hints(session))
        prog = progress.record(conn, user_id, points["points"], progress.today())
        if (task.get("kind") or "decision") == "decision" and not session.get("daily"):   # hibak ismetlese (#36)
            from .. import repetition

            ver = (result.get("review") or {}).get("verdict") or {}
            repetition.update(conn, user_id, task["category"], task["source"], ver.get("category"), ver.get("flags"),
                              progress.today())
    if session.get("lesson") and session.get("summary"):
        from .. import learning                      # a tanulasi ut (#33) szintje teljesult-e

        learning.record(user_id, session)
    if hidden(session):                          # napi kihivas: az eredmeny (es a pont) csak a beadas utan
        return {"session": public(session), "k": k, "hidden": True}
    return {"session": public(session), "k": k, "reveal": reveal(task), "progress": prog}


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
    sides = s.get("sides") or {}
    if (sides.get("short") or {}).get("entered"):
        lo, sh = sides["long"], sides["short"]
        out.append(Line("sides", [str(lo["filled"]), _num(lo["mean_r"]), str(sh["filled"]), _num(sh["mean_r"])],
                        [0, 1, 2, 3]))
    if s.get("reviewed"):
        v = s["verdicts"]
        out.append(Line("verdicts", [str(v["good"]), str(v["lucky"]), str(v["unlucky"]), str(v["bad"])], [0, 1, 2, 3]))
    if s["missed_good"]:
        out.append(Line("missed", [str(s["missed_good"]), _num(scoring.GOOD_R, 0)], [0, 1]))
    if s["stop_atr_median"] is not None:
        out.append(Line("stop_median", [_num(s["stop_atr_median"]), _num((s["tight_share"] or 0) * 100, 1),
                                        _num(scoring.TIGHT_ATR, 0)], [0, 1, 2]))
    out.append(Line("disclaimer"))
    return [ln.to_dict() for ln in out]


def summary_payload(user_id: int, session_id: str) -> dict:
    session = _load(session_id, user_id)
    if hidden(session):                          # napi kihivas kozben: csak az allapot, eredmeny nelkul
        return {"session": public(session), "hidden": True}
    s = scoring.summary(decision_tasks(session["tasks"]))
    tasks = [{"k": k, "decided": t.get("decision") is not None, "kind": t.get("kind") or "decision"} | (
        shown(t["symbol"], t["date"]) | {"quality": t.get("quality"), "points": t.get("points")}
        if t.get("decision") and (t.get("kind") or "decision") != "decision" else {}) | (
        shown(t["symbol"], t["date"]) | {"category": t["category"], "source": t["source"],
         "action": scoring.side_of(t["decision"]) or "skip", "r": (t["result"].get("user") or {}).get("r"),
         "verdict": ((t["result"].get("review") or {}).get("verdict") or {}).get("category"),
         "status": (t["result"].get("user") or {}).get("status"),
         "strategies": [{"strategy": x["strategy"], "r": x.get("r"), "status": x.get("status")}
                        for x in t["result"]["strategies"] if x.get("signaled")],
         "reference_r": t["result"]["reference"].get("r"), "baseline_r": t["result"]["baseline"].get("mean_r"),
         "points": t.get("points")}
        if t.get("decision") and (t.get("kind") or "decision") == "decision" else {})
        for k, t in enumerate(session["tasks"])]
    from .. import learning

    return {"session": public(session), "summary": s, "lines": lines(s) if s["decided"] else [], "tasks": tasks,
            "kinds": kinds_summary(session["tasks"]),
            "lesson": learning.record(user_id, session) if session.get("lesson") else None}


def listing(user_id: int) -> list[dict]:
    out = []
    for session in store.sessions(user_id):
        try:
            out.append(public(session))
        except (KeyError, TypeError):
            continue
    return out


def delete(user_id: int, session_id: str) -> bool:
    return store.delete_session(session_id, user_id)
