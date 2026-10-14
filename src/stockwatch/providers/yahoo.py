"""yfinance alapu arfolyam-szolgaltato.

A yfinance koteges letoltest tamogat, ezert egy keressel hozzuk le mind a 10
papirt - ez lenyegesen kimeletesebb a Yahoo vegponttal szemben, mint tiz
kulon hivas.

Az API alakja (yfinance 1.x):
  yf.download([...], group_by="ticker")  ->  MultiIndex oszlopok,
  a 0. szinten a ticker, az 1. szinten az OHLCV mezo.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

from .base import PriceProvider

log = logging.getLogger(__name__)


class YahooProvider(PriceProvider):
    name = "yahoo"

    def __init__(self, max_retries: int = 3, backoff_seconds: float = 2.0) -> None:
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def fetch(
        self,
        symbols: list[str],
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        if not symbols:
            return {}

        raw = self._download_with_retry(symbols, start, end, interval)
        if raw is None or raw.empty:
            log.warning("A letoltes ures eredmenyt adott: %s", ", ".join(symbols))
            return {}

        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = self._extract(raw, symbol, len(symbols))
            if frame is None:
                log.warning("Nincs adat erre a tickerre: %s", symbol)
                continue
            try:
                normalised = self.normalise(frame)
            except ValueError as exc:
                log.warning("Feldolgozhatatlan valasz (%s): %s", symbol, exc)
                continue
            if not normalised.empty:
                result[symbol] = normalised

        return result

    # -- belso -------------------------------------------------------------
    def _download_with_retry(
        self,
        symbols: list[str],
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
        interval: str,
    ) -> pd.DataFrame | None:
        kwargs = dict(
            tickers=symbols,
            interval=interval,
            auto_adjust=True,   # split/osztalek szerint korrigalt arak
            actions=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if start is not None:
            kwargs["start"] = start.strftime("%Y-%m-%d")
        if end is not None:
            kwargs["end"] = end.strftime("%Y-%m-%d")
        if start is None and end is None:
            kwargs["period"] = "max"

        for attempt in range(1, self.max_retries + 1):
            try:
                return yf.download(**kwargs)
            except Exception as exc:  # a yfinance sokfele halozati hibat dobhat
                if attempt == self.max_retries:
                    log.error("A letoltes %d probalkozas utan is hibas: %s", attempt, exc)
                    return None
                wait = self.backoff_seconds * (2 ** (attempt - 1))
                log.warning("Letoltesi hiba (%s), ujraprobalas %.0f mp mulva...", exc, wait)
                time.sleep(wait)
        return None

    @staticmethod
    def _extract(raw: pd.DataFrame, symbol: str, symbol_count: int) -> pd.DataFrame | None:
        """Egy ticker kiemelese a koteges valaszbol.

        Egyetlen ticker eseten a yfinance verziotol fuggoen lapos vagy
        MultiIndex oszlopokat ad vissza, ezert mindket alakot kezeljuk.
        """
        if raw.columns.nlevels == 1:
            return raw if symbol_count == 1 else None

        level0 = raw.columns.get_level_values(0)
        if symbol in level0:
            frame = raw[symbol]
        elif symbol_count == 1:
            # group_by="ticker" nelkuli alak: a mezonev van a 0. szinten.
            frame = raw.droplevel(1, axis=1)
        else:
            return None

        return None if frame.dropna(how="all").empty else frame
