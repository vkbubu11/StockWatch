"""Szintetikus univerzum a kutatougynok tesztjeihez: emelkedo trend, idonkent mely, rovid visszahuzodassal
(RSI(2) melyrepules), hogy a Visszahuzas-motor jelezzen. Az adat a holdouton TUL is tart, igy a vagas
ellenorizheto. Halozat nelkul, a meglevo betoltok (meanrev_research.load_raw, market.load_context) helyett."""
from __future__ import annotations

import numpy as np
import pandas as pd

START, END = "2016-10-03", "2025-12-31"


def frame(seed: int, drift: float = 0.0009, vol: float = 0.011, price: float = 50.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(START, END, name="date")
    ret = rng.normal(drift, vol, len(idx))
    dips = np.flatnonzero(rng.random(len(idx)) < 0.025)    # ~6 melyrepules / ev, 3 napos
    for i in dips:
        ret[i:i + 3] -= 0.03
        ret[i + 3:i + 7] += 0.016                           # visszapattanas
    close = price * np.exp(np.cumsum(ret))
    opens = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.004, len(idx)))
    spread = np.abs(rng.normal(0, 0.012, len(idx))) * close
    low = np.minimum(opens, close) - spread
    after = np.clip(dips + 3, 0, len(idx) - 1)               # a melyrepules utan napkozben melyebb kanocok
    low[after] = np.minimum(opens[after], close[after]) * (1 - rng.uniform(0.04, 0.09, len(after)))
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, close) + spread, "low": low, "close": close,
                         "volume": rng.uniform(2e6, 4e6, len(idx))}, index=idx)


def universe(n: int = 24) -> dict[str, pd.DataFrame]:
    out = {f"S{i:02d}": frame(100 + i) for i in range(n)}
    out["QQQ"] = frame(7, drift=0.0009, vol=0.005, price=150)
    out["^GSPC"] = frame(8, drift=0.0008, vol=0.005, price=2200)
    return out


class NoEarnings:
    def __init__(self, *a, **k):
        pass

    def dates(self, symbol):
        return []

    def times(self, symbol):
        return {}

    def save(self):
        pass


def install(monkeypatch, frames: dict[str, pd.DataFrame], symbols: list[str]) -> None:
    from stockwatch import market
    from stockwatch import meanrev_research as mr
    from stockwatch.research_agent import search

    monkeypatch.setattr(mr, "load_raw", lambda symbol, settings: frames.get(symbol, pd.DataFrame()).copy())
    ctx = market.MarketContext({"QQQ": frames["QQQ"], "^GSPC": frames["^GSPC"]}, frozenset(symbols), frozenset())
    monkeypatch.setattr(market, "load_context", lambda settings, refresh=True, allow_network=None: ctx)
    monkeypatch.setattr(market, "HistoricalEarnings", NoEarnings)
    monkeypatch.setattr(search, "universe_symbols", lambda brief, settings, ws="admin": list(symbols))
