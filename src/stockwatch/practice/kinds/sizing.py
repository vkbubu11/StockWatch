"""Pozicioméret es kockazat (#53): adott szamlameret, kotesenkenti kockazat (%), belepo, stop (es esetleg cel) -
a felhasznalo megadja a darabszamot, a kockazatot dollarban, es ha van cel, a cel / kockazat aranyt.

  * a helyes valasz a meglevo pozicioméret-fuggvenybol (entry.position_size / short.position_size), a tokehatar
    (max_position_pct) nelkul; a stop-tavolsag legalabb MIN_RISK, igy a tokehatar meg 2%-os kockazatnal sem kot
  * valtozatok: short, es „a stop tul tag, a pozicio 0 darab lenne”
  * a szamok normalizaltak (belepo = 100 $, a lathato chart utolso zaroja) - nincs valodi papir es ar
  * tures: darabszam ±1 (kerekites), kockazat max(1 $, 1%), cel / kockazat ±RR_TOL

shortcut: kotesi dij es reszleges zaras utani maradek kockazat nincs - ha a 9. szint igenyli, uj valtozat.
"""
from __future__ import annotations

import math

import numpy as np

from ... import entry as entry_engine
from ... import short as short_engine
from ...config import EntrySettings, Settings
from .common import line, norm
from .levels import _frame

NAME = "sizing"
ACCOUNTS = (5000, 10000, 25000, 50000, 100000)
RISKS = (0.5, 1.0, 2.0)
MIN_RISK = 2.5                         # $/darab (a belepo 100 $): a tokehatar (100%) igy soha nem kot
RR_TOL = 0.05
ENTRY = 100.0


def make(pick: dict, rng, settings: Settings) -> dict:
    df, i = _frame(pick, settings)
    _, atr = norm(df, i)
    side = "short" if pick["category"] == "short" or rng.random() < 0.2 else "long"
    sign = 1.0 if side == "long" else -1.0
    account = int(ACCOUNTS[int(rng.integers(len(ACCOUNTS)))])
    risk_pct = float(RISKS[int(rng.integers(len(RISKS)))])
    dist = max(round(float(rng.uniform(1.5, 3.5)) * atr * 2) / 2, MIN_RISK)
    budget = account * risk_pct / 100
    if rng.random() < 0.15 and budget + 5 < 90:                            # „a stop tul tag”: 0 darab
        dist = float(math.floor(budget) + int(rng.integers(3, 12)))
    stop = round(ENTRY - sign * dist, 2)
    target = round(ENTRY + sign * dist * float(rng.choice([1.5, 2.0, 2.5, 3.0])), 2) if rng.random() < 0.6 else None
    return pick | {"params": {"prompt": {"side": side, "account": account, "risk_pct": risk_pct, "entry": ENTRY,
                                         "stop": stop, "target": target}, "secret": {}}}


def expected(prompt: dict) -> dict:
    cfg = EntrySettings(account_size=prompt["account"], risk_per_trade_pct=prompt["risk_pct"], max_position_pct=100.0)
    engine = entry_engine if prompt["side"] == "long" else short_engine
    shares = engine.position_size(prompt["entry"], prompt["stop"], cfg)
    rps = round(abs(prompt["entry"] - prompt["stop"]), 2)
    rr = round(abs(prompt["target"] - prompt["entry"]) / rps, 2) if prompt.get("target") else None
    return {"shares": shares, "rps": rps, "budget": round(prompt["account"] * prompt["risk_pct"] / 100, 2),
            "risk": round(shares * rps, 2), "value": round(shares * prompt["entry"], 2), "rr": rr}


def _num(value, name: str, integer: bool = False):
    try:
        v = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{name}: érvénytelen szám.") from None
    if not math.isfinite(v) or v < 0 or v > 1e9 or (integer and v != int(v)):
        raise ValueError(f"{name}: érvénytelen szám.")
    return int(v) if integer else round(v, 2)


def validate(body: dict, prompt: dict) -> dict:
    out = {"shares": _num(body.get("shares"), "Darabszám", integer=True), "risk": _num(body.get("risk"), "Kockázat")}
    if prompt.get("target"):
        out["rr"] = _num(body.get("rr"), "Cél / kockázat")
    return out


def evaluate(task: dict, df, i: int, answer: dict, settings: Settings) -> dict:
    p = task["params"]["prompt"]
    e = expected(p)
    raw = e["budget"] / e["rps"]
    lines = [line("z_budget", p["account"], p["risk_pct"], e["budget"]),
             line("z_rps", p["entry"], p["stop"], e["rps"])]
    lines.append(line("z_shares", e["budget"], e["rps"], raw, e["shares"]) if e["shares"] else line("z_zero", e["rps"], e["budget"]))
    lines += [line("z_risk", e["shares"], e["rps"], e["risk"]), line("z_value", e["shares"], p["entry"], e["value"])]
    if e["rr"] is not None:
        lines.append(line("z_rr", p["target"], p["entry"], e["rps"], e["rr"]))
    from ..templates import TEMPLATES

    checks = [("z_shares_label", answer["shares"], e["shares"], abs(answer["shares"] - e["shares"]) <= 1),
              ("z_risk_label", answer["risk"], e["risk"], abs(answer["risk"] - e["risk"]) <= max(1.0, 0.01 * e["risk"]))]
    if e["rr"] is not None:
        checks.append(("z_rr_label", answer["rr"], e["rr"], abs(answer["rr"] - e["rr"]) <= RR_TOL))
    for key, mine, right, ok in checks:
        lines.append(line("z_ok", TEMPLATES[key], mine) if ok else line("z_bad", TEMPLATES[key], mine, right))
    quality = round(float(np.mean([ok for *_, ok in checks])), 2)
    lines.append(line("q_quality", int(round(quality * 100))))
    levels = [{"kind": "entry_level", "v": p["entry"]}, {"kind": "stop", "v": p["stop"]}]
    if p.get("target"):
        levels.append({"kind": "target", "v": p["target"]})
    return {"quality": quality, "lines": lines, "expected": e, "levels": levels, "marks": [], "future": []}
