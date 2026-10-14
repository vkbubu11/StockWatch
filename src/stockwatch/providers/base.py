"""Absztrakt arfolyam-szolgaltato.

Az elemzo reteg soha nem hivja kozvetlenul a yfinance-t: mindig ezen az
interfeszen keresztul kap adatot.  Igy egy kesobbi valtas (Alpaca, Polygon)
egyetlen uj osztaly, a tobbi kod valtozatlan marad.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

# A rendszerben mindenhol ez a kanonikus alak:
#   index : tz-naiv, ejfelre normalizalt DatetimeIndex, neve "date"
#   oszlopok: open, high, low, close, volume  (float64)
CANONICAL_COLUMNS = ["open", "high", "low", "close", "volume"]


class PriceProvider(ABC):
    """Arfolyamadat-forras."""

    name: str = "base"

    @abstractmethod
    def fetch(
        self,
        symbols: list[str],
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """Gyertyak letoltese tickerenkent.

        A hianyzo vagy hibas tickerek egyszeruen kimaradnak a visszaadott
        szotarbol - egy rossz szimbolum nem akaszthatja meg a tobbi papir
        feldolgozasat.
        """

    @staticmethod
    def normalise(df: pd.DataFrame) -> pd.DataFrame:
        """Tetszoleges szolgaltatoi tablat a kanonikus alakra hoz."""
        if df is None or df.empty:
            return _empty_canonical()

        out = df.copy()
        out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]

        if "adj_close" in out.columns and "close" not in out.columns:
            out = out.rename(columns={"adj_close": "close"})

        missing = [c for c in CANONICAL_COLUMNS if c not in out.columns]
        if missing:
            raise ValueError(f"Hianyzo oszlopok a szolgaltatoi valaszban: {missing}")

        index = pd.DatetimeIndex(out.index)
        if index.tz is not None:
            # A tozsdei datum a lenyeges, az idozona csak zavarna az osszefuzest.
            index = index.tz_convert("America/New_York").tz_localize(None)
        out.index = index.normalize()
        out.index.name = "date"

        out = out[CANONICAL_COLUMNS].astype("float64")
        out = out[~out.index.duplicated(keep="last")].sort_index()
        # Gyertya ervenytelen zaro nelkul; a nulla volumen viszont legitim
        # (nagyon ritka papirnal elofordul), ezert azt meghagyjuk.
        return out.dropna(subset=["open", "high", "low", "close"])


def _empty_canonical() -> pd.DataFrame:
    empty = pd.DataFrame(columns=CANONICAL_COLUMNS, dtype="float64")
    empty.index = pd.DatetimeIndex([], name="date")
    return empty
