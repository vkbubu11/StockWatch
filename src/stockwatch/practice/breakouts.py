"""A Kitores-osszevetes a feladatkeszletben (#29): a keszlet EPITESEKOR egyszer szamolodik, es a keszletbe kerul,
igy a gyakorlo kor a labor (szolgaltatas es modul) nelkul is mukodik.

Forras: a labor kimeneti fajljai a data/lab alatt (kitores-cache papironkent es az ELO kiserlet kotesei), ha a
keszletet olyan adatkonyvtarral epitjuk, ahol a labor futott. Ha nincsenek, a kitoresek nem latszanak (a Kitores
"nem jelzett") - a tobbi strategia es az itelet ettol fuggetlen.
"""
from __future__ import annotations

import gzip
import json
import logging
import pickle

import pandas as pd

from ..config import DATA_DIR

log = logging.getLogger(__name__)

LAB_DIR = DATA_DIR / "lab"


def days(symbol: str) -> set[pd.Timestamp]:
    """A labor kitores-cache-enek kitoresi napjai (a Kitores-strategia jelzesei)."""
    path = LAB_DIR / "events" / f"{symbol.upper()}.pkl"
    if not path.exists():
        return set()
    try:
        cache = pickle.loads(path.read_bytes())          # sajat, helyben irt fajl
    except Exception:
        log.warning("Serult kitores-cache: %s", path)
        return set()
    return {pd.Timestamp(e[0]) for e in cache.get("events", [])}


def live_results() -> dict[str, list[dict]]:
    """Papir -> a labor ELO kiserletenek kotesei. Ures: nincs ertekelt ELO kiserlet."""
    try:
        store = json.loads((LAB_DIR / "experiments.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    live = next((e for e in store.get("experiments", []) if e.get("live")), None)
    path = LAB_DIR / "results" / f"{live['id']}.json.gz" if live else None
    if path is None or not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for d, symbol, _pattern, kind, r, reason, held in json.loads(gzip.decompress(path.read_bytes())):
        out.setdefault(symbol, []).append({"date": pd.Timestamp(d), "kind": kind, "r": r, "reason": reason, "held": held})
    return out


def compare(symbol: str, df: pd.DataFrame, i: int, breakout_days: set, results: dict[str, list[dict]]) -> dict:
    """A Kitores a t=i napon: ha kitores van, az ELO kiserlet mentett kotese (megerositett belepesnel a kovetkezo
    napi jelzes). Nincs mentett kotes: a szurok kizartak ("filtered"), vagy a labor meg nem ertekelt ("unknown")."""
    day = df.index[i]
    out = {"strategy": "breakout", "signaled": False}
    if day not in breakout_days:
        return out
    if not results:
        return out | {"signaled": True, "status": "unknown"}
    nxt = df.index[i + 1] if i + 1 < len(df) else None
    hit = next((x for x in results.get(symbol, [])
                if x["date"] == day or (x["kind"] == "confirm" and nxt is not None and x["date"] == nxt)), None)
    if hit is None:
        return out | {"signaled": True, "status": "filtered"}
    return out | {"signaled": True, "status": "closed", "r": round(float(hit["r"]), 3), "reason": hit["reason"],
                  "held": int(hit["held"]), "kind": hit["kind"]}
