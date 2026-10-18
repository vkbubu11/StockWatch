"""SEC EDGAR: a papir bejelentesei (8-K tetelkodokkal, kibocsatasi prospektusok), ingyenes, evtizedekre vissza.

Point-in-time: az `acceptanceDateTime` mezot a SEC "Z" (UTC) jelolessel adja, de a gyakorlatban
keleti parti ido is lehet. Konzervativan a KESOBBI ertelmezest hasznaljuk (UTC vagy New York-i
falido), igy egy bejelentes soha nem latszik korabban, mint ahogy valoban megjelent.

A SEC kerese: azonosito User-Agent (nev + e-mail) es legfeljebb 10 keres / masodperc. A korlat FOLYAMATOK
KOZOTT is ervenyes (a monitor, a labor es a felulet egyszerre is kerhet): egy kozos, zarolt idobelyeg-fajl
tartja a kereseket legalabb MIN_INTERVAL tavolsagra (osszesen <= 8 / mp).
Beallitas: SEC_USER_AGENT a .env-ben (pl. "StockWatch kutatas te@pelda.hu").
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from ...config import DATA_DIR, env_value
from ..model import Article, utc_iso

log = logging.getLogger(__name__)

NAME = "edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
CACHE = DATA_DIR / "news" / "_sec_tickers.json"
FORMS = ("8-K", "424B4", "424B5")
ROUTINE_424B = 12                 # evente ennyinel tobb 424B5 -> rutinszeru (pl. bankok kotvenyei), kimarad
TIMEOUT = 20
MIN_INTERVAL = 0.125                  # mp: osszesen legfeljebb 8 keres / mp, minden folyamatbol egyutt
RATE_FILE = DATA_DIR / "news" / "_sec_rate"


def user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT") or env_value("SEC_USER_AGENT") or "StockWatch research (set SEC_USER_AGENT)"


def throttle() -> None:
    """Varakozas a kovetkezo SEC-keres elott - a zar alatt alszik, igy a folyamatok sorban kernek."""
    from ...locks import file_lock

    RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(RATE_FILE.with_name(RATE_FILE.name + ".lock"), timeout=60):
        try:
            last = float(RATE_FILE.read_text(encoding="utf-8") or 0)
        except (OSError, ValueError):
            last = 0.0
        wait = last + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        RATE_FILE.write_text(repr(time.time()), encoding="utf-8")


def get_bytes(url: str, session=None, timeout: float = TIMEOUT) -> bytes:
    """Nyers letoltes (XML, ZIP) a SEC-tol, a kozos korlattal es azonosito User-Agenttel."""
    throttle()
    r = (session or requests).get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip"}, timeout=timeout)
    r.raise_for_status()
    return r.content


def _get(url: str, session=None) -> dict:
    return json.loads(get_bytes(url, session))


def ticker_map(session=None, max_age_days: int = 30) -> dict[str, dict]:
    """TICKER -> {cik, name} a SEC listajabol (30 naponta frissul, ~0,8 MB)."""
    if CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if data.get("fetched", "") >= (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat():
                return data["tickers"]
        except (OSError, ValueError, KeyError):
            pass
    try:
        raw = _get(TICKERS_URL, session)
    except Exception as exc:
        log.warning("A SEC tickerlista nem toltheto le: %s", exc)
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))["tickers"] if CACHE.exists() else {}
        except (OSError, ValueError, KeyError):
            return {}
    tickers = {row["ticker"].upper().replace("-", "."): {"cik": int(row["cik_str"]), "name": row["title"]}
               for row in raw.values()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fetched": datetime.now(timezone.utc).isoformat(), "tickers": tickers}), encoding="utf-8")
    return tickers


def accepted_utc(value: str, filing_date: str) -> pd.Timestamp:
    """A ket ertelmezes (UTC vagy New York-i falido) kozul a kesobbi - soha ne lassuk korabban."""
    if not value:
        return pd.Timestamp(filing_date).tz_localize("America/New_York").replace(hour=23, minute=59).tz_convert("UTC")
    naive = pd.Timestamp(value.replace("Z", "")).tz_localize(None)
    as_utc = naive.tz_localize("UTC")
    as_ny = naive.tz_localize("America/New_York", ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
    return max(as_utc, as_ny)


def _rows(block: dict):
    keys = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items", "primaryDocument", "primaryDocDescription")
    cols = [block.get(k) or [""] * len(block.get("form", [])) for k in keys]
    for values in zip(*cols):
        yield dict(zip(keys, values))


def fetch(symbol: str, since: pd.Timestamp, settings=None, session=None) -> tuple[list[Article], tuple | None]:
    info = ticker_map(session).get(symbol.upper())
    if not info:
        return [], None                                            # ETF-nek / kulfoldi papirnak nincs 8-K-ja
    cik = f"{info['cik']:010d}"
    first = _get(SUBMISSIONS_URL.format(name=f"CIK{cik}.json"), session)
    blocks = [first["filings"]["recent"]]
    for extra in first["filings"].get("files", []):
        if pd.Timestamp(extra["filingTo"]) >= since.tz_localize(None):
            blocks.append(_get(SUBMISSIONS_URL.format(name=extra["name"]), session))
    now = utc_iso(datetime.now(timezone.utc))
    rows = [r for b in blocks for r in _rows(b)
            if r["form"] in FORMS and pd.Timestamp(r["filingDate"]) >= since.tz_localize(None)]
    per_year = {}
    for r in rows:
        if r["form"] == "424B5":
            per_year[r["filingDate"][:4]] = per_year.get(r["filingDate"][:4], 0) + 1
    out = []
    for r in rows:
        if r["form"] == "424B5" and per_year.get(r["filingDate"][:4], 0) > ROUTINE_424B:
            continue
        items = [i.strip() for i in (r["items"] or "").split(",") if i.strip()]
        acc = r["accessionNumber"]
        url = (f"https://www.sec.gov/Archives/edgar/data/{info['cik']}/{acc.replace('-', '')}/{r['primaryDocument']}"
               if r.get("primaryDocument") else "")
        title = f"{r['form']} bejelentes" + (f" - tetelek: {', '.join(items)}" if items else "")
        out.append(Article(id=f"edgar:{acc}", source=NAME, symbol=symbol.upper(), kind="filing", form=r["form"],
                           items=items, title=title, url=url, publisher="SEC EDGAR", fetched_at=now,
                           published_at=utc_iso(accepted_utc(r["acceptanceDateTime"], r["filingDate"]))))
    oldest = min((pd.Timestamp(b["filingDate"][-1]) for b in blocks if b.get("filingDate")), default=None)
    cover_from = max(since.tz_localize(None), oldest) if oldest is not None else since.tz_localize(None)
    return out, (utc_iso(cover_from), now)


def company_name(symbol: str) -> str | None:
    """A cegnev a SEC listajabol, jogi formak nelkul ("NVIDIA CORP" -> "NVIDIA"). Csak a gyorsitotarbol."""
    import re

    if not CACHE.exists():
        return None
    info = ticker_map().get(symbol.upper())
    if not info:
        return None
    name = re.sub(r"[,.]", " ", info["name"])
    name = re.sub(r"\b(inc|corp|corporation|co|ltd|plc|holdings?|group|company|the|class [a-z]|n ?v|s ?a|ag|se|lp)\b",
                  " ", name, flags=re.I)
    name = " ".join(name.split())
    return name if len(name) >= 3 else None
