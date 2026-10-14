"""Papironkenti rezsim: Nasdaq-100 tag -> QQQ, csak S&P 500 tag -> ^GSPC, egyik sem -> sajat trend."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry_research as er
from stockwatch import market, replay


def _series(trend: float, n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = pd.Series(100 * np.exp(np.linspace(0, trend, n)), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e6})


UP, DOWN = _series(0.3), _series(-0.3)


def ctx(qqq=UP, spx=UP):
    return market.MarketContext({"QQQ": qqq, "^GSPC": spx}, frozenset({"AAPL", "NDXONLY"}),
                                frozenset({"AAPL", "XOM"}))


def test_index_of_membership():
    c = ctx()
    assert c.index_of("AAPL") == "QQQ"              # mindket indexben: a Nasdaq-100 elsobbseget kap
    assert c.index_of("NDXONLY") == "QQQ"
    assert c.index_of("XOM") == "^GSPC"             # csak S&P 500
    assert c.index_of("MOD") is None and c.regime_source("MOD") == "self"   # csak watchlist


def test_regime_follows_own_index():
    c = ctx(qqq=DOWN, spx=UP)
    day = UP.index[-1]
    nasdaq, sp = c.regime("AAPL", UP, day), c.regime("XOM", UP, day)
    assert (nasdaq.ok, nasdaq.name) == (False, "Piac (QQQ)")
    assert (sp.ok, sp.name) == (True, "Piac (S&P 500)")
    own_up, own_down = c.regime("MOD", UP, day), c.regime("MOD", DOWN, day)
    assert own_up.ok is True and own_down.ok is False and own_up.name == "Sajat trend (SMA200)"
    # a relativ ero indexe: indexen kivul az S&P 500
    assert c.bench_for("MOD") is c.benches["^GSPC"] and c.bench_for("AAPL") is c.benches["QQQ"]
    assert c.regime_frame("MOD", DOWN) is DOWN


def test_regime_has_no_lookahead():
    c = ctx(qqq=pd.concat([UP, DOWN.set_axis(pd.bdate_range(UP.index[-1] + pd.offsets.BDay(1), periods=260))]))
    assert c.regime("AAPL", UP, UP.index[-1]).ok is True        # a kesobbi eses nem latszik


def test_research_uses_per_symbol_regime(monkeypatch):
    seen = {}

    def fake_trade(event, df, i, kind, variant, cfg, settings, bench, dates, context, regime_bench=None):
        seen[event.symbol] = (bench, regime_bench)
        return None

    monkeypatch.setattr(er, "_trade", fake_trade)
    event = lambda sym: er.Event(sym, None, 5, 10)               # noqa: E731
    c = ctx()
    er.run_variant(er.Variant("x"), {"AAPL": [event("AAPL")], "MOD": [event("MOD")]},
                   {"AAPL": UP, "MOD": DOWN}, __import__("stockwatch.config", fromlist=["x"]).Settings(), c)
    assert seen["AAPL"][1] is c.benches["QQQ"]
    assert seen["MOD"][1] is DOWN and seen["MOD"][0] is c.benches["^GSPC"]


@pytest.mark.parametrize("source,member", [("QQQ", "nasdaq"), ("^GSPC", "sp500"), ("self", None), (None, None)])
def test_replay_context_matches_logged_source(source, member):
    c = replay._replay_context("ABC", source, UP)
    if source is None:
        assert c is None
        return
    assert c.regime_source("ABC") == source
    assert c.regime("ABC", DOWN, DOWN.index[-1]).ok is (source != "self")


def test_index_refreshed_after_close(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(market.store, "load", lambda *a, **k: UP)
    monkeypatch.setattr("stockwatch.feed.update", lambda symbols, *a, **k: calls.append(symbols) or {})
    last = UP.index[-1]                              # utolso gyertya: last
    after_close = pd.Timestamp(last + pd.Timedelta(hours=16, minutes=20)).tz_localize("America/New_York")
    before_close = pd.Timestamp(last + pd.offsets.BDay(1) + pd.Timedelta(hours=11)).tz_localize("America/New_York")
    next_after = pd.Timestamp(last + pd.offsets.BDay(1) + pd.Timedelta(hours=16, minutes=10)).tz_localize(
        "America/New_York")
    for now, expected in ((after_close, 0), (before_close, 0), (next_after, 1)):
        calls.clear()
        monkeypatch.setattr(market.sessions, "now_ny", lambda now=now: now)
        market.load_index("QQQ", __import__("stockwatch.config", fromlist=["x"]).Settings())
        assert len(calls) == expected, now
