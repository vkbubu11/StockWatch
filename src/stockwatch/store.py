"""Fajlalapu OHLCV tarolas - adatbazis nelkul.

Tickerenkent egy CSV a `data/ohlcv/` alatt.  Napi gyertyaknal ez 10 evre
~2500 sor / papir (~250 KB), tehat a sima CSV boven eleg, cserebe barmikor
megnyithato Excelben vagy egy szovegszerkesztoben.

Minden iras utan ervenyesitjuk a retention ablakot, igy a fajl nem no
korlatlanul, barmeddig is fut a monitor.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from .config import Settings, load_settings

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def _path_for(symbol: str, settings: Settings, universe: bool = False) -> Path:
    if universe:
        # A szeles backtest univerzum gzip-elve megy: sok ticker, ritkan olvassuk.
        return settings.universe_dir / f"{symbol.upper()}.csv.gz"
    return settings.ohlcv_dir / f"{symbol.upper()}.csv"


def load(symbol: str, settings: Settings | None = None, universe: bool = False) -> pd.DataFrame:
    """Beolvassa a cache-elt gyertyakat.  Ures DataFrame, ha meg nincs adat."""
    settings = settings or load_settings()
    path = _path_for(symbol, settings, universe)
    if not path.exists() and not universe:
        # Nem figyelt papir (pl. az indexszkennelesbol): a tomoritett cache-ben lehet.
        path = _path_for(symbol, settings, universe=True)
    if not path.exists():
        return _empty()

    try:
        df = pd.read_csv(path, parse_dates=["date"], index_col="date")
    except (pd.errors.EmptyDataError, ValueError) as exc:
        log.warning("Serult cache fajl, ujraepites: %s (%s)", path, exc)
        return _empty()

    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        log.warning("Hianyzo oszlopok (%s) a %s fajlban, ujraepites.", missing, path)
        return _empty()
    return df[OHLCV_COLUMNS].sort_index()


def save(
    symbol: str,
    df: pd.DataFrame,
    settings: Settings | None = None,
    universe: bool = False,
) -> Path:
    """Kiirja a gyertyakat, a retention ablakra vagva."""
    settings = settings or load_settings()
    path = _path_for(symbol, settings, universe)
    path.parent.mkdir(parents=True, exist_ok=True)

    trimmed = apply_retention(df, settings.storage.ohlcv_retention_years)
    trimmed = trimmed.round(6)
    trimmed.index.name = "date"
    # atomi csere: a parhuzamosan olvaso folyamat (felulet, strategia-labor) soha nem lat felig irt fajlt
    tmp = path.with_name(f".{path.name}.tmp")
    trimmed.to_csv(tmp, date_format="%Y-%m-%d", compression="gzip" if path.suffix == ".gz" else None)
    os.replace(tmp, path)
    return path


def merge(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Inkrementalis osszefuzes.

    Utkozes eseten a frissebb letoltes nyer: a szolgaltato utolag javithat
    egy gyertyat (split, osztalek, keses), es ilyenkor az uj ertek a helyes.
    """
    if existing.empty:
        return fresh.sort_index()
    if fresh.empty:
        return existing.sort_index()

    combined = pd.concat([existing, fresh])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def apply_retention(df: pd.DataFrame, years: int) -> pd.DataFrame:
    """Levagja a retention ablaknal regebbi sorokat."""
    if df.empty or years <= 0:
        return df
    cutoff = df.index.max() - pd.DateOffset(years=years)
    return df[df.index >= cutoff]


def last_date(symbol: str, settings: Settings | None = None, universe: bool = False):
    """A cache-ben levo legfrissebb gyertya datuma, vagy None."""
    df = load(symbol, settings, universe)
    return None if df.empty else df.index.max()


def cached_symbols(settings: Settings | None = None) -> list[str]:
    settings = settings or load_settings()
    if not settings.ohlcv_dir.exists():
        return []
    return sorted(p.stem.upper() for p in settings.ohlcv_dir.glob("*.csv"))


def _empty() -> pd.DataFrame:
    empty = pd.DataFrame(columns=OHLCV_COLUMNS, dtype="float64")
    empty.index = pd.DatetimeIndex([], name="date")
    return empty
