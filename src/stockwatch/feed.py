"""Inkrementalis adatfrissites: szolgaltato + cache osszekotese.

Csak a hianyzo gyertyakat toltjuk le, a tobbi a lokalis CSV-bol jon.  Egy
tipikus napi futas igy 1-2 uj sort jelent papironkent, nem 2500-at.
"""
from __future__ import annotations

import logging

import pandas as pd

from . import store
from .config import Settings, ensure_dirs, load_settings
from .providers.base import PriceProvider
from .providers.yahoo import YahooProvider

log = logging.getLogger(__name__)

# Ennyi napnyi atfedessel toltunk ujra: a szolgaltato utolag javithat egy
# gyertyat (kesve erkezo volumen, split-korrekcio), es igy ezt is atvesszuk.
OVERLAP_DAYS = 7
CHUNK = 100          # ennyi ticker egy letoltesi keresben

_PROVIDERS: dict[str, type[PriceProvider]] = {"yahoo": YahooProvider}


def get_provider(settings: Settings | None = None) -> PriceProvider:
    settings = settings or load_settings()
    name = settings.data.provider.lower()
    if name not in _PROVIDERS:
        raise ValueError(
            f"Ismeretlen szolgaltato: {name}. Elerheto: {', '.join(_PROVIDERS)}"
        )
    return _PROVIDERS[name]()


def update(
    symbols: list[str],
    settings: Settings | None = None,
    provider: PriceProvider | None = None,
    force_full: bool = False,
    universe: bool = False,
) -> dict[str, pd.DataFrame]:
    """Frissiti a megadott tickerek cache-et, es visszaadja a teljes sorozatot.

    `universe=True` eseten a szeles tanito/backtest univerzum gzip-elt
    cache-ebe (data/universe) ir, nem a figyelt papirok koze.

    A tickereket ket kotegre bontjuk - teljes historia vs. inkrementalis -,
    igy legfeljebb ket halozati keres kell tiz papirhoz.
    """
    settings = settings or load_settings()
    provider = provider or get_provider(settings)
    ensure_dirs()

    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return {}

    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    full_start = today - pd.DateOffset(years=settings.data.history_years)

    needs_full: list[str] = []
    needs_increment: list[str] = []
    increment_start = today

    for symbol in symbols:
        last = None if force_full else store.last_date(symbol, settings, universe)
        if last is None:
            needs_full.append(symbol)
        else:
            needs_increment.append(symbol)
            increment_start = min(increment_start, last - pd.Timedelta(days=OVERLAP_DAYS))

    fetched: dict[str, pd.DataFrame] = {}
    # A vegpont exkluziv, ezert +1 nap, kulonben a mai gyertya kimaradna.
    end = today + pd.Timedelta(days=1)

    if needs_full:
        log.info("Teljes historia letoltese (%d ev): %s", settings.data.history_years, ", ".join(needs_full))
        fetched.update(_fetch_chunked(provider, needs_full, full_start, end, settings.data.interval))

    if needs_increment:
        log.info("Inkrementalis frissites %s-tol: %s", increment_start.date(), ", ".join(needs_increment))
        fetched.update(_fetch_chunked(provider, needs_increment, increment_start, end, settings.data.interval))

    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        existing = store.load(symbol, settings, universe)
        fresh = fetched.get(symbol)

        if fresh is None or fresh.empty:
            if existing.empty:
                log.warning("Nincs elerheto adat: %s", symbol)
                continue
            log.debug("Nincs uj gyertya (%s), a cache-t hasznaljuk.", symbol)
            result[symbol] = existing
            continue

        merged = store.merge(existing, fresh)
        store.save(symbol, merged, settings, universe)
        new_rows = len(merged) - len(existing)
        log.info("%s: %d gyertya a cache-ben (+%d uj)", symbol, len(merged), max(new_rows, 0))
        result[symbol] = store.apply_retention(merged, settings.storage.ohlcv_retention_years)

    return result


def _fetch_chunked(provider: PriceProvider, symbols: list[str], start, end, interval: str):
    """Sok ticker (indexszkenneles) eseten csomagokban kerdezunk - egy 500-as
    keres a Yahoonal konnyen reszleges vagy ures valaszt ad."""
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        if len(symbols) > CHUNK:
            log.info("Letoltes %d-%d / %d...", i + 1, i + len(chunk), len(symbols))
        out.update(provider.fetch(chunk, start, end, interval))
    return out


def load_or_update(
    symbols: list[str],
    settings: Settings | None = None,
    max_staleness_days: int = 1,
) -> dict[str, pd.DataFrame]:
    """Cache-bol olvas, es csak akkor halozik, ha az adat elavult.

    A dashboard ezt hasznalja: egy ujratoltes ne inditson tiz halozati kerest,
    ha delelott mar frissitettunk.
    """
    settings = settings or load_settings()
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)

    stale: list[str] = []
    cached: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        df = store.load(symbol, settings)
        last = None if df.empty else df.index.max()
        # A tozsde hetvegen zarva - ilyenkor a pentek zaro nem elavult adat.
        business_days = len(pd.bdate_range(last, today)) - 1 if last is not None else 999
        if last is None or business_days > max_staleness_days:
            stale.append(symbol)
        else:
            cached[symbol] = df

    if stale:
        cached.update(update(stale, settings))

    return {s: cached[s] for s in symbols if s in cached}
