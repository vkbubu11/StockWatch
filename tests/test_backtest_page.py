"""A feladatkeszlet adatforrasa (a korabbi Backteszt oldal tara): adatgyujtes, adatminoseg, bemenet-ellenorzes."""
from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from stockwatch import market
from stockwatch.backtest_page import data
from stockwatch.config import Settings


def synthetic(start="2012-01-02", end="2026-09-30", seed=3) -> pd.DataFrame:
    """Emelkedo trend zajjal es idonkenti 3 napos esessel - legyenek visszahuzas-jelzesek."""
    idx = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    ret = 0.0013 + rng.normal(0, 0.01, len(idx))
    ret[np.arange(len(idx)) % 37 < 3] -= 0.012
    close = 100 * np.exp(np.cumsum(ret))
    openp = close * (1 + rng.normal(0, 0.002, len(idx)))
    high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
    low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": 5e6}, index=idx)


class FakeProvider:
    name = "fake"

    def __init__(self, frames):
        self.frames, self.calls = frames, []

    def fetch(self, symbols, start=None, end=None, interval="1d"):
        self.calls.append((tuple(symbols), start))
        out = {}
        for s in symbols:
            if s in self.frames:
                f = self.frames[s]
                out[s] = f[f.index >= start] if start is not None else f
        return out


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "BT_DIR", tmp_path / "backtest")
    monkeypatch.setattr(market, "load_context", lambda *a, **k: market.MarketContext({}, frozenset(), frozenset()))
    frames = {"TEST": synthetic(), "^GSPC": synthetic(seed=5), "QQQ": synthetic(seed=6), "SPY": synthetic(seed=7)}
    provider = FakeProvider(frames)
    data.collect("TEST", "2012-01-02", Settings(), provider=provider)
    return provider


def test_collect_stores_symbol_and_benchmarks(store):
    m = data.meta("TEST")
    assert m["first"] == "2012-01-02" and m["bars"] == len(synthetic()) and m["source"] == "fake"
    assert m["quality"]["ok"], m["quality"]
    assert {x["symbol"] for x in data.stored_symbols()} == {"TEST"}      # a benchmarkok nem latszanak
    assert not data.load("^GSPC").empty and not data.load("SPY").empty
    data.collect("SPY", "2012-01-02", Settings(), provider=store)        # benchmark, de a felhasznalo kerte
    assert {x["symbol"] for x in data.stored_symbols()} == {"TEST", "SPY"}


def test_incremental_update_and_refetch_on_adjustment(store):
    frames = store.frames
    data.update("TEST", pd.Timestamp("2012-01-02"), Settings(), provider=store)
    assert store.calls[-1][1] > pd.Timestamp("2026-01-01")                # csak a vege jott le
    adjusted = frames["TEST"].copy()
    adjusted[["open", "high", "low", "close"]] *= 0.98                      # osztalek-korrekcio
    store.frames["TEST"] = adjusted
    data.update("TEST", pd.Timestamp("2012-01-02"), Settings(), provider=store)
    assert store.calls[-1][1] == pd.Timestamp("2012-01-02")                # teljes ujratoltes
    assert data.load("TEST")["close"].iloc[0] == pytest.approx(adjusted["close"].iloc[0])


def test_quality_flags_missing_days_jumps_and_late_start():
    df = synthetic("2015-01-02", "2016-12-30")
    df = df.drop(df.index[100:130])
    df.iloc[200:, df.columns.get_loc("close")] *= 1.5
    q = data.quality(df, pd.Timestamp("2010-01-01"))
    assert not q["ok"] and q["missing_days"] >= 25 and q["jumps"]
    assert any("csak 2015-01-02-tol" in i for i in q["issues"])


def test_input_validation():
    with pytest.raises(ValueError):
        data.clean_symbol("rossz ticker!")
    with pytest.raises(ValueError):
        data.parse_start("1900-01-01")


def test_listing_date_cached_and_bounds_start(store):
    calls = []
    store.first_trade_date = lambda s: calls.append(s) or (pd.Timestamp("2012-05-18") if s == "META" else None)
    assert data.earliest_start("META", Settings(), store) == (pd.Timestamp("2012-05-18"), pd.Timestamp("2012-05-18"))
    assert data.listing_date("META", Settings(), store) == pd.Timestamp("2012-05-18")
    assert calls == ["META"]                                                 # masodszor a gyorsitotarbol
    store.first_trade_date = lambda s: pd.Timestamp("1962-01-02")
    assert data.earliest_start("IBM", Settings(), store)[0] == data.MIN_START   # 1980 elott nincs adat
    store.first_trade_date = lambda s: None
    assert data.earliest_start("XYZ", Settings(), store) == (data.MIN_START, None)


