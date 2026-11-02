"""Gyertyankenti visszajatszas es poziciokezeles a felfedeskor (#76).

Kezelesi modban (a kor `manage` jelzoje) a dontes utan a szerver NEM adja ki a jovot: a kliens lepesenkent kerheti
(`step`), es csak a mar kiadott gyertyakat kapja. Nyitott poziciot a legutobb latott gyertya utan lehet kezelni,
a hatas a KOVETKEZO nyitastol el (csalas ellen: a muvelet a mar kiadott gyertyakon alapul, a jovobeli lepesre
hivatkozo keres hiba):
  * stop athuzasa: csak a pozicio javara (longnal feljebb, shortnal lejjebb), es a legutobbi zaro innenso oldalan;
  * reszleges zaras: a pozicio fele a kovetkezo nyitason (egyszer);
  * kiszallas most: a maradek a kovetkezo nyitason.
Ugyanaz a motor (scoring.simulate), naponkenti stoppal es kiszallasi jellel; muvelet nelkul a kotes bitre azonos a
terv szerintivel (result["user"]). A belepes itelete valtozatlan; a kezeles kulon pontot kap: a kezelt R a terv
szerinti R-hez kepest (+1 R = +5 pont, 0-10 pont), mellette a tartas alatt elerheto legjobb R.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import meanrev
from ..config import Settings
from . import scoring

STEP_MAX = 5                                   # egy keresben legfeljebb ennyi uj gyertya
HALF = 0.5
ACTIONS = ("stop", "half", "exit")


def start() -> dict:
    return {"shown": 0, "actions": [], "done": False}


def active(task: dict) -> bool:
    """Folyamatban levo visszajatszas: az eredmeny (es a jovo) meg nem adhato ki."""
    rp = (task.get("result") or {}).get("replay")
    return bool(rp) and not rp.get("done")


def _setup(task: dict, df: pd.DataFrame, i: int, settings: Settings) -> dict:
    scale = 100.0 / float(df["close"].iloc[i])
    feat = meanrev.features(df, settings.strategies.pullback.params)
    return {"scale": scale, "feat": feat, "cost": scoring.cost_bps(settings), "borrow": scoring.borrow_bps(settings),
            "mirrored": scoring.mirror(feat), "side": scoring.side_of(task["decision"])}


def _sim(task: dict, ctx: dict, i: int, actions: list[dict], until_half: bool = False) -> meanrev.Trade:
    """A kezelt kotes (vagy `until_half`: a reszleges zarassal lezart fele) - valodi (nem normalizalt) aron."""
    d, feat, scale = task["decision"], ctx["feat"], ctx["scale"]
    n = len(feat)
    stops = np.full(n, d["stop"] / scale)
    exits = np.zeros(n, dtype=bool)
    for a in actions:
        at = i + int(a["at"])
        if a["type"] == "stop":
            stops[at + 1:] = a["stop"] / scale                    # a kovetkezo naptol el
        elif a["type"] == "exit" or (a["type"] == "half" and until_half):
            exits[at] = True                                       # a zaron -> kovetkezo nyitas
    limit = d["limit"] / scale if d["entry"] == "limit" else math.inf
    target = d["target"] / scale if d.get("target") else None
    return scoring.simulate(feat, i, limit, d["stop"] / scale, target, int(d["days"]), ctx["cost"], ctx["side"],
                            ctx["mirrored"], ctx["borrow"], exits=exits, stops=stops)


def _offset(feat: pd.DataFrame, day, i: int) -> int | None:
    return None if day is None else int(feat.index.get_loc(day)) - i


def _r(task: dict, ctx: dict, trade: meanrev.Trade) -> float | None:
    if trade.status != "closed":
        return None
    d = task["decision"]
    planned = (d["limit"] if d["entry"] == "limit" else 100.0) / ctx["scale"]
    return scoring.r_multiple(trade.entry_price, trade.exit_price, d["stop"] / ctx["scale"], planned, ctx["cost"],
                              ctx["side"], trade.held, ctx["borrow"])


def _current_stop(task: dict, actions: list[dict]) -> float:
    stops = [a["stop"] for a in actions if a["type"] == "stop"]
    return stops[-1] if stops else float(task["decision"]["stop"])


def state(task: dict, df: pd.DataFrame, i: int, settings: Settings) -> dict:
    """A kliensnek: a kiadott gyertyak, es a pozicio allapota CSAK a kiadott gyertyakig (a kesobbi kilepes nem)."""
    res, rp = task["result"], task["result"]["replay"]
    s = int(rp["shown"])
    ctx = _setup(task, df, i, settings)
    trade = _sim(task, ctx, i, rp["actions"])
    entry_k, exit_k = _offset(ctx["feat"], trade.entry_date, i), _offset(ctx["feat"], trade.exit_date, i)
    entered = entry_k is not None and entry_k <= s
    closed = exit_k is not None and exit_k <= s
    exiting = any(a["type"] == "exit" for a in rp["actions"])
    halved = any(a["type"] == "half" for a in rp["actions"])
    pos = {"side": ctx["side"], "stop": _current_stop(task, rp["actions"]), "target": task["decision"].get("target"),
           "entered": entered, "closed": closed, "halved": halved, "exiting": exiting and not closed,
           "unfilled": s >= 1 and trade.status == "unfilled",
           "open": entered and not closed and not exiting}
    if entered:
        pos |= {"entry": round(float(trade.entry_price) * ctx["scale"], 2), "entry_k": entry_k}
    if closed:
        pos |= {"exit": round(float(trade.exit_price) * ctx["scale"], 2), "exit_k": exit_k,
                "reason": "manual" if trade.reason == "signal" else trade.reason}
    return {"shown": s, "total": len(res["future"]), "bars": res["future"][:s], "position": pos,
            "actions": rp["actions"], "decision": task["decision"]}


def step(task: dict, n: int) -> None:
    rp = task["result"]["replay"]
    if not isinstance(n, int) or not 1 <= n <= STEP_MAX:
        raise ValueError(f"Egyszerre 1-{STEP_MAX} gyertya kérhető.")
    rp["shown"] = min(len(task["result"]["future"]), int(rp["shown"]) + n)


def act(task: dict, body: dict, df: pd.DataFrame, i: int, settings: Settings) -> None:
    """Kezelesi muvelet a legutobb kiadott gyertya utan (`at` = a kiadott gyertyak szama)."""
    rp = task["result"]["replay"]
    kind, at = body.get("type"), body.get("at")
    if kind not in ACTIONS:
        raise ValueError("Ismeretlen kezelési művelet.")
    if not isinstance(at, int) or at != int(rp["shown"]):
        raise ValueError("Kezelni csak a már látott gyertyák alapján lehet.")
    st = state(task, df, i, settings)
    pos = st["position"]
    if not pos["open"]:
        raise ValueError("Nincs nyitott pozíció.")
    action = {"at": at, "type": kind}
    if kind == "half":
        if pos["halved"]:
            raise ValueError("A pozíció felét már lezártad.")
    elif kind == "stop":
        v = body.get("stop")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError("Érvénytelen szám.")
        v = round(float(v), 2)
        long = pos["side"] == "long"
        last = float(st["bars"][-1]["c"])
        if (v <= pos["stop"]) if long else (v >= pos["stop"]):
            raise ValueError("A stop csak a pozíció javára mozdulhat (longnál feljebb, shortnál lejjebb).")
        if (v >= last) if long else (v <= last):
            raise ValueError("A stop a legutóbbi záró alatt legyen (shortnál felette).")
        action["stop"] = v
    rp["actions"].append(action)


def finish(task: dict, df: pd.DataFrame, i: int, settings: Settings) -> int:
    """A visszajatszas vege: a kezelt kotes, a kezeles pontja (a teljes jovo ezutan kiadhato). Visszaad: pont."""
    res, rp = task["result"], task["result"]["replay"]
    rp["done"] = True
    rp["shown"] = len(res["future"])
    if not rp["actions"] or not res.get("user"):
        return 0
    ctx = _setup(task, df, i, settings)
    trade = _sim(task, ctx, i, rp["actions"])
    r_full = _r(task, ctx, trade)
    view = scoring.trade_view(trade, ctx["feat"], i, ctx["scale"],
                              (task["decision"]["limit"] if task["decision"]["entry"] == "limit" else 100.0) / ctx["scale"],
                              ctx["cost"], ctx["side"], ctx["borrow"],
                              task["decision"]["target"] / ctx["scale"] if task["decision"].get("target") else None)
    view["stop"] = round(float(task["decision"]["stop"]), 2)
    if view.get("reason") == "signal":
        view["reason"] = "manual"
    r = r_full
    half_r = None
    if any(a["type"] == "half" for a in rp["actions"]):
        half_r = _r(task, ctx, _sim(task, ctx, i, rp["actions"], until_half=True))
        r = None if r_full is None or half_r is None else HALF * half_r + (1 - HALF) * r_full
    plan_r = res["user"].get("r")
    delta = None if r is None or plan_r is None else r - plan_r
    points = 0 if delta is None else int(max(0, min(10, round(5 + 5 * delta))))
    res["manage"] = {"actions": rp["actions"], "r": None if r is None else round(r, 3), "half_r": None if half_r is None else round(half_r, 3),
                     "plan_r": plan_r, "best_r": best_r(task, ctx, i), "delta": None if delta is None else round(delta, 3),
                     "points": points, "user": view}
    return points


def best_r(task: dict, ctx: dict, i: int) -> float | None:
    """A terv szerinti tartas alatt elerheto legjobb R (a legkedvezobb csucs / melypont a belepotol, koltseg nelkul)."""
    u = task["result"].get("user") or {}
    if u.get("entry_k") is None or u.get("exit_k") is None:
        return None
    feat, scale = ctx["feat"], ctx["scale"]
    entry, stop = float(u["entry"]), float(task["decision"]["stop"])
    risk = abs(entry - stop)
    window = feat.iloc[i + u["entry_k"]: i + u["exit_k"] + 1]
    if not risk or window.empty:
        return None
    best = float(window["high"].max()) * scale - entry if ctx["side"] == "long" else entry - float(window["low"].min()) * scale
    return round(best / risk, 3)
