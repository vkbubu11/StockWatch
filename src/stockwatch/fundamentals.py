"""Point-in-time fundamentalis adatok a SEC XBRL-bol (#72): negyedeves higitott EPS es arbevetel.

Forras: a SEC companyfacts API (data.sec.gov/api/xbrl/companyfacts/CIK##########.json), ingyenes. Minden ertek a
BEADAS (filed) datumaval tarolodik: egy szam csak a beadas UTANI naptol ismert (a beadas napjan meg nem - a
bejelentes zaras utan is jöhet). Egy idoszak kesobbi (javitott) ertekenel a kiertekeles napja elott beadott
legutolso ertek szamit.

  * EPS: EarningsPerShareDiluted (ha nincs: ...BasicAndDiluted, ...Basic), USD/reszveny
  * arbevetel: Revenues, RevenueFromContractWithCustomer..., SalesRevenueNet - idoszakonkent az elso elerheto
    fogalom (a cegek idonkent fogalmat valtanak)
  * negyedev: ~3 honapos idoszak (80-100 nap) a 10-Q-bol; a 4. negyedevet a 10-K eves erteke mínusz a ket
    datum koze eso harom negyedev adja (a 10-K beadasatol ismert). Az EPS-nel ez kozelites (a reszvenyszam
    negyedevenkent valtozik) - jelolve: "derived".

Szarmaztatott mutatok (as_of): ev/ev EPS- es arbevetel-novekedes, gyorsulas (az utolso harom ev/ev EPS-novekedes
no), az egymas utani pozitiv EPS-novekedesu negyedevek szama.

A SEC-keres a kozos korlattal (sentiment.sources.edgar.throttle: osszesen <= 8 / mp) es azonosito User-Agenttel.
Tarolas: data/fundamentals/<TICKER>.json.gz (csak a kivonat, papironkent nehany KB), osszesen legfeljebb LIMIT_MB;
a legregebben frissitett torlodik eloszor.
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .config import DATA_DIR

log = logging.getLogger(__name__)

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DIR = DATA_DIR / "fundamentals"
LIMIT_MB = 30
MAX_AGE_DAYS = 7
EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic")
REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax")
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A")
QUARTER = (80, 100)                            # napok: negyedeves idoszak
YEAR = (350, 380)
STALE_DAYS = 200                               # ennel regebbi utolso negyedev: elavult (pl. megszunt jelentes)


# ---------------------------------------------------------------------------
# Kivonat a companyfacts JSON-bol
# ---------------------------------------------------------------------------
def _rows(facts: dict, concepts: tuple[str, ...], unit: str) -> list[list]:
    """[start, end, ertek, filed, "q" | "a"] - idoszakonkent (tipus, start, end) az elso fogalombol."""
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    out, seen = [], set()
    for concept in concepts:
        found = set()
        for f in ((gaap.get(concept) or {}).get("units") or {}).get(unit, []):
            if f.get("form") not in FORMS or not f.get("start") or not f.get("filed") or f.get("val") is None:
                continue
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
            kind = "q" if QUARTER[0] <= days <= QUARTER[1] else "a" if YEAR[0] <= days <= YEAR[1] else None
            if kind is None or (kind, f["start"], f["end"]) in seen:
                continue
            found.add((kind, f["start"], f["end"]))
            out.append([f["start"], f["end"], float(f["val"]), f["filed"], kind])
        seen |= found
    return sorted(out, key=lambda r: (r[1], r[3]))


def extract(facts: dict) -> dict:
    return {"eps": _rows(facts, EPS, "USD/shares"), "revenue": _rows(facts, REVENUE, "USD")}


# ---------------------------------------------------------------------------
# Letoltes es tarolas
# ---------------------------------------------------------------------------
def _path(symbol: str):
    return DIR / f"{symbol.upper().replace('/', '_')}.json.gz"


def load(symbol: str) -> dict | None:
    path = _path(symbol)
    try:
        return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None
    except (OSError, ValueError):
        return None


def _prune() -> None:
    files = sorted(DIR.glob("*.json.gz"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > LIMIT_MB * 1024 * 1024:
        old = files.pop(0)
        total -= old.stat().st_size
        old.unlink(missing_ok=True)


def fetch(symbol: str, max_age_days: int = MAX_AGE_DAYS, session=None) -> dict | None:
    """A papir kivonata (a tarolt, ha MAX_AGE_DAYS-nal frissebb). None: nincs SEC-azonosito vagy XBRL-adat."""
    from .sentiment.sources import edgar

    symbol = symbol.upper()
    cached = load(symbol)
    if cached and cached.get("fetched", "") >= (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat():
        return cached
    info = edgar.ticker_map(session).get(symbol)
    if info is None:
        return cached
    try:
        facts = json.loads(edgar.get_bytes(URL.format(cik=int(info["cik"])), session))
    except Exception as exc:                    # nincs XBRL (pl. kulfoldi kibocsato) vagy halozati hiba
        log.warning("Fundamentalis adat: %s nem toltheto le: %s", symbol, exc)
        return cached
    data = {"symbol": symbol, "cik": int(info["cik"]), "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data |= extract(facts)
    DIR.mkdir(parents=True, exist_ok=True)
    _path(symbol).write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8")))
    _prune()
    return data


# ---------------------------------------------------------------------------
# Point-in-time negyedeves sor es mutatok
# ---------------------------------------------------------------------------
def _known(rows: list[list], day: date) -> dict[tuple[str, str, str], float]:
    """(tipus, start, end) -> a `day` ELOTT beadott legutolso ertek."""
    out: dict = {}
    for start, end, val, filed, kind in rows:          # filed szerint novekvo az idoszakon belul
        if date.fromisoformat(filed) < day:
            out[(kind, start, end)] = val
    return out


def quarters(rows: list[list], day: date) -> dict[str, tuple[float, bool]]:
    """negyedev vege -> (ertek, szarmaztatott-e), ahogy a `day` napon ismert volt."""
    known = _known(rows, day)
    qs = {end: (v, False) for (kind, _s, end), v in known.items() if kind == "q"}
    for (kind, start, end), annual in known.items():
        if kind != "a" or end in qs:
            continue
        inside = [v for qe, (v, derived) in qs.items() if not derived and start < qe < end]
        if len(inside) == 3:
            qs[end] = (annual - sum(inside), True)
    return dict(sorted(qs.items()))


def _yoy(series: dict[str, tuple[float, bool]]) -> dict[str, float | None]:
    """negyedev vege -> ev/ev novekedes %; negativ vagy nulla bazisnal nem ertelmezett (None)."""
    ends = list(series)
    out = {}
    for end in ends:
        d = date.fromisoformat(end)
        prev = next((e for e in ends if 345 <= (d - date.fromisoformat(e)).days <= 385), None)
        base = series[prev][0] if prev else None
        out[end] = round((series[end][0] - base) / base * 100, 1) if base and base > 0 else None
    return out


def as_of(symbol: str, day: date | pd.Timestamp | str, data: dict | None = None) -> dict | None:
    """A `day` napon ismert fundamentalis kep (csak a tarolt kivonatbol; halozat nelkul). None: nincs adat."""
    day = pd.Timestamp(day).date()
    data = data if data is not None else load(symbol)
    if not data:
        return None
    eps, rev = quarters(data.get("eps", []), day), quarters(data.get("revenue", []), day)
    if not eps and not rev:
        return None
    last = max(list(eps) + list(rev))
    if (day - date.fromisoformat(last)).days > STALE_DAYS:
        return None
    eps_yoy, rev_yoy = _yoy(eps), _yoy(rev)
    growth = [eps_yoy[e] for e in eps]
    recent = [g for g in growth[-3:] if g is not None]
    streak = 0
    for g in reversed(growth):
        if g is None or g <= 0:
            break
        streak += 1
    ends = sorted(set(eps) | set(rev))[-8:]
    return {"last_quarter": last,
            "eps_yoy": growth[-1] if growth else None,
            "revenue_yoy": rev_yoy[list(rev)[-1]] if rev else None,
            "eps_accelerating": len(recent) == 3 and recent[0] < recent[1] < recent[2],
            "eps_positive_streak": streak,
            "quarters": [{"end": e, "eps": eps.get(e, (None,))[0], "revenue": rev.get(e, (None,))[0],
                          "eps_yoy": eps_yoy.get(e), "revenue_yoy": rev_yoy.get(e),
                          "derived": bool(eps.get(e, (0, False))[1] or rev.get(e, (0, False))[1])} for e in ends]}
