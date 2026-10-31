"""Szektorok a "Legjobb belepo" szektorvalasztojahoz (#74).

  * GICS-szektor tickerenkent: az S&P 500 tagokra a Wikipedia `constituents` tablazatabol (ugyanaz a fajl,
    amit az esemenytanulmanyok hasznalnak: data/events/sectors.json). Ha meg nincs, egyszer letoltjuk.
  * A csak Nasdaq-100 vagy csak watchlist papiroknal nincs GICS: a Yahoo `sector` mezojet kerjuk le EGYSZER,
    GICS-nevre forditva, es gyorsitotarazzuk (data/sector_cache.json). Ami igy sem talalhato: "Egyeb".
    A negativ talalatot is megjegyezzuk (30 napig), hogy ne kerdezzuk ujra minden kattintasra.
  * Szektor-ETF allapota (XLK, XLF, ...): zaro az SMA200 felett / alatt, 1 honapos (21 nap) hozam - csak
    lezart, tarolt gyertyabol.

Semmi nem dont itt: csak csoportosit es megmutat.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import DATA_DIR, Settings
from .locks import write_atomic

log = logging.getLogger(__name__)

OTHER = "Egyéb / ismeretlen"

# A 11 GICS-szektor es a hozza tartozo SPDR szektor-ETF.
SECTOR_ETFS: dict[str, str] = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}
SECTORS = list(SECTOR_ETFS)

# A Yahoo sajat szektornevei -> GICS
YAHOO_TO_GICS = {
    "Technology": "Information Technology",
    "Healthcare": "Health Care",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}

NEGATIVE_TTL = timedelta(days=30)      # a "nem talalhato" valaszt ennyi ideig nem kerdezzuk ujra
MAX_LOOKUPS = 40                       # egy hivasban legfeljebb ennyi Yahoo-lekeres (a tobbi a kovetkezore var)


def cache_path():
    return DATA_DIR / "sector_cache.json"


def normalize(name: str | None) -> str | None:
    """GICS- vagy Yahoo-nev -> GICS-nev; ismeretlen nev -> None."""
    if not name:
        return None
    name = " ".join(str(name).split())
    if name in SECTOR_ETFS:
        return name
    return YAHOO_TO_GICS.get(name)


def _load_cache() -> dict[str, dict]:
    try:
        return json.loads(cache_path().read_text(encoding="utf-8")) if cache_path().exists() else {}
    except (OSError, ValueError):
        return {}


def _yahoo_sector(symbol: str) -> str | None:
    import yfinance as yf

    info = yf.Ticker(symbol).get_info() or {}
    return normalize(info.get("sector"))


def _gics_base(allow_network: bool) -> dict[str, str]:
    from .events.sources import index_changes

    base = index_changes.load_sectors()
    if base or not allow_network:
        return base
    try:                                    # meg nincs letoltve: egyetlen Wikipedia-keres
        from . import indexes

        fetched = indexes.fetch_sectors()
        if len(fetched) >= 400:
            write_atomic(index_changes.sectors_path(), json.dumps(
                {"fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sectors": fetched},
                ensure_ascii=False))
            return fetched
    except Exception as exc:                # halozat / oldal-valtozas: a tobbi "Egyeb" lesz
        log.warning("GICS-szektorok letoltese sikertelen: %s", exc)
    return {}


def sector_map(symbols: list[str], allow_network: bool = False, lookup=None,
               base: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, str]:
    """Ticker -> GICS-szektor (vagy OTHER) a megadott papirokra.

    `lookup` (teszthez) a Yahoo-lekerest helyettesiti; `base` a GICS-tablat."""
    now = now or datetime.now(timezone.utc)
    base = _gics_base(allow_network) if base is None else base
    cache = _load_cache()
    lookup = lookup or (_yahoo_sector if allow_network else None)
    out: dict[str, str] = {}
    changed, asked = False, 0
    for symbol in symbols:
        gics = normalize(base.get(symbol))
        if gics:
            out[symbol] = gics
            continue
        hit = cache.get(symbol)
        if hit and hit.get("sector"):
            out[symbol] = normalize(hit["sector"]) or OTHER
            continue
        fresh_negative = hit and now - datetime.fromisoformat(hit["checked"]) < NEGATIVE_TTL
        if lookup is not None and not fresh_negative and asked < MAX_LOOKUPS:
            asked += 1
            try:
                found = lookup(symbol)
            except Exception as exc:
                log.info("Szektor nem kerdezheto le (%s): %s", symbol, exc)
                found = None
            cache[symbol] = {"sector": found, "checked": now.isoformat(timespec="seconds")}
            changed = True
            out[symbol] = found or OTHER
            continue
        out[symbol] = OTHER
    if changed:
        try:
            write_atomic(cache_path(), json.dumps(cache, ensure_ascii=False, indent=0))
        except OSError as exc:
            log.warning("A szektor-gyorsitotar nem irhato: %s", exc)
    return out


def members(sector: str, mapping: dict[str, str]) -> list[str]:
    """A szektor tagjai a mapping sorrendjeben (OTHER: a besorolatlanok)."""
    return [s for s, sec in mapping.items() if sec == sector]


def etf_status(frame: pd.DataFrame | None) -> dict | None:
    """Szektor-ETF: zaro vs. SMA200, 21 napos hozam - a tarolt, lezart gyertyakbol."""
    if frame is None or frame.empty or len(frame) < 22:
        return None
    close = frame["close"].astype(float)
    last = float(close.iloc[-1])
    sma = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    return {"close": round(last, 2), "above_sma200": None if sma is None else last > sma,
            "sma200": None if sma is None else round(sma, 2),
            "month_pct": round((last / float(close.iloc[-22]) - 1) * 100, 2),
            "as_of": f"{frame.index[-1]:%Y-%m-%d}"}


def overview(settings: Settings, symbols: list[str], allow_network: bool = False, refresh_etfs: bool = False) -> dict:
    """A szektorvalaszto adatai: szektoronkent a tagok szama (a "Legjobb belepo" listajan) + az ETF allapota."""
    from . import feed, store

    mapping = sector_map(symbols, allow_network=allow_network)
    if refresh_etfs:
        try:
            feed.update(list(SECTOR_ETFS.values()), settings, universe=True)
        except Exception as exc:
            log.warning("Szektor-ETF frissites sikertelen: %s", exc)
    rows = []
    for name, etf in SECTOR_ETFS.items():
        rows.append({"sector": name, "etf": etf, "count": len(members(name, mapping)),
                     "etf_status": etf_status(store.load(etf, settings, universe=True))})
    other = len(members(OTHER, mapping))
    if other:
        rows.append({"sector": OTHER, "etf": None, "count": other, "etf_status": None})
    return {"sectors": rows, "symbols": len(symbols)}
