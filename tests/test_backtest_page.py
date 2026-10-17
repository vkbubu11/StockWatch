"""Backteszt oldal: adatgyujtes, adatminoseg, a kotesek egyezese a kutatassal, holdout-zar, mutatok, API."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry_research, market, meanrev, performance, webapp
from stockwatch import meanrev_research as mr
from stockwatch.backtest_page import data, runner, runs
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
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "backtest" / "runs")
    monkeypatch.setattr(entry_research, "HOLDOUT_LOG", tmp_path / "holdout_log.jsonl")
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
    s = Settings()
    with pytest.raises(ValueError, match="Ismeretlen parameter"):
        runner.resolve_params(s, "etf", {"nincs_ilyen": 1}, None)
    with pytest.raises(ValueError):
        runner.resolve_params(s, "etf", {"stop_atr": 99}, None)              # a PullbackParams validacioja
    with pytest.raises(ValueError, match="Kitores"):
        runner.resolve_params(s, "breakout", {}, None)
    p = runner.resolve_params(s, "etf", {"rsi2_max": "5", "regime_filter": "false"}, 7)
    assert p.rsi2_max == 5.0 and p.regime_filter is False and p.cost_bps == 7.0


def test_commission_from_settings_reaches_every_engine(tmp_path, monkeypatch):
    s = Settings(broker={"commission_pct": 0.1})
    assert s.entry.commission_bps == 10 and s.entry.total_cost_bps == s.entry.slippage_bps + 10
    for sid in ("pullback", "deep", "etf", "momentum", "sandbox"):
        p = s.strategies.get(sid).params
        assert p.commission_bps == 10 and p.total_cost_bps == p.cost_bps + 10
    assert meanrev.net_return(100, 110, 10)[1] < meanrev.net_return(100, 110, 0)[1]
    with pytest.raises(ValueError):
        Settings(broker={"commission_pct": 5})
    # a strategies.yaml-ba nem irodik (kulonben ketszer szamolodna)
    from stockwatch import config
    monkeypatch.setattr(config, "STRATEGIES_PATH", tmp_path / "strategies.yaml")
    config.save_strategies(s.strategies)
    assert "commission_bps" not in (tmp_path / "strategies.yaml").read_text(encoding="utf-8")


def test_commission_lowers_backtest_result(store):
    plain = runner.run("TEST", "etf", Settings(), start="2012-01-02")
    costly = runner.run("TEST", "etf", Settings(broker={"commission_pct": 0.2}), start="2012-01-02")
    assert costly["params"]["commission_bps"] == 20
    assert costly["periods"]["all"]["ev"] < plain["periods"]["all"]["ev"]


def test_trades_match_research_engine(store):
    """Egyetlen igazsagforras: azonos adatra es parameterre ugyanazok a kotesek, mint a kutatasban."""
    s = Settings()
    result = runner.run("TEST", "etf", s, start="2012-01-02")
    params = s.strategies.etf.params
    df = data.load("TEST")
    df = df[df.index <= entry_research.HOLDOUT_START - pd.Timedelta(days=1)]
    feat = meanrev.features(df, params)
    sd = mr.SymbolData(feat, meanrev.regime_series(feat, df), [])
    orders, _ = mr.portfolio(mr.candidates({"TEST": sd}, params), params)
    expected = sorted((t.to_dict() for t in orders), key=lambda d: d["signal_date"])
    got = [{k: v for k, v in t.items() if k != "r"} for t in result["trades"]]
    assert len(got) >= 20 and got == expected
    assert result["periods"]["all"]["n"] == sum(1 for t in orders if t.status == "closed")


def test_holdout_hidden_by_default_and_logged_when_opened(store):
    s = Settings()
    hidden = runner.run("TEST", "etf", s, start="2020-01-02")
    assert hidden["end"] == "2025-03-31" and not hidden["holdout"] and "holdout" not in hidden["periods"]
    assert all(t["signal_date"] < "2025-04-01" for t in hidden["trades"])
    assert hidden["equity"]["t"][-1] <= "2025-03-31" and hidden["price"]["t"][-1] <= "2025-03-31"
    assert not entry_research.HOLDOUT_LOG.exists()
    opened = runner.run("TEST", "etf", s, start="2020-01-02", include_holdout=True)
    assert opened["holdout"] and "holdout" in opened["periods"]
    assert any(t["signal_date"] >= "2025-04-01" for t in opened["trades"])
    log = [json.loads(line) for line in entry_research.HOLDOUT_LOG.read_text().splitlines()]
    assert log[-1]["study"] == "backtest-page"


def test_overrides_change_result_and_small_sample_warns(store):
    s = Settings()
    base = runner.run("TEST", "etf", s, start="2012-01-02")
    strict = runner.run("TEST", "etf", s, start="2012-01-02", overrides={"rsi2_max": 2})
    assert strict["periods"]["all"]["n"] < base["periods"]["all"]["n"]
    assert strict["overrides"] == {"rsi2_max": 2}
    assert any("kis minta" in w for w in strict["warnings"])


def test_equity_curve_follows_trades_and_buy_hold(store):
    s = Settings()
    r = runner.run("TEST", "etf", s, start="2012-01-02", capital=10_000, alloc_pct=50)
    assert r["equity"]["v"][0] == pytest.approx(10_000, rel=0.02)
    df = data.load("TEST")
    df = df[(df.index >= "2012-01-02") & (df.index <= "2025-03-31")]
    assert r["buy_hold"]["v"][-1] == pytest.approx(10_000 * df["close"].iloc[-1] / df["close"].iloc[0], rel=1e-3)
    assert 0 < r["strategy_metrics"]["exposure"] < 50                    # csak kotesben van piacon
    assert r["years"][0]["year"] == 2012


def test_performance_metrics_known_values():
    idx = pd.bdate_range("2020-01-01", periods=253)
    eq = pd.Series(np.linspace(100, 110, 253), index=idx)
    m = performance.metrics(eq)
    assert m["total"] == pytest.approx(10.0) and m["max_dd"] == 0 and m["calmar"] is None
    dd = pd.Series([100, 120, 90, 130], index=pd.bdate_range("2020-01-01", periods=4))
    assert performance.metrics(dd)["max_dd"] == pytest.approx(-25.0)
    assert len(performance.downsample(pd.Series(range(5000)), 1000)) <= 1001


def test_runs_saved_listed_and_capped(store, monkeypatch):
    monkeypatch.setattr(runs, "MAX_RUNS", 2)
    s = Settings()
    ids = [runs.save(runner.run("TEST", "etf", s, start="2015-01-02")) for _ in range(3)]
    listed = [x["id"] for x in runs.listing()]
    assert len(listed) == 2 and ids[0] not in listed
    assert runs.load(ids[-1])["symbol"] == "TEST"
    assert runs.delete(ids[-1]) and runs.load(ids[-1]) is None
    with pytest.raises(ValueError):
        runs.load("../../etc")


def _wait_job(timeout=30.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = webapp.backtest_jobs().snapshot()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("a backteszt-feladat nem fejezodott be")


def test_web_run_fetches_data_first(store, monkeypatch):
    """A Futtatas maga gyujti az adatot (kulon gomb nelkul), a tozsdere lepes elotti kezdodatumot igazitja."""
    from stockwatch import feed

    monkeypatch.setattr(webapp, "_BT_JOBS", None)
    monkeypatch.setattr(feed, "get_provider", lambda settings: store)
    store.frames["NEW"] = synthetic("2014-03-03", seed=9)
    store.first_trade_date = lambda s: pd.Timestamp("2014-03-03") if s == "NEW" else None
    assert data.load("NEW").empty
    webapp.backtest_action("run", {"symbol": "NEW", "strategy": "etf", "start": "2000-01-01"})
    job = _wait_job()
    assert job["state"] == "done" and job["run_id"], job
    assert data.meta("NEW")["first"] == "2014-03-03" and runs.load(job["run_id"])["start"] >= "2014-03-03"
    webapp.backtest_action("run", {"symbol": "NOPE", "strategy": "etf", "start": "2012-01-02"})
    job = _wait_job()
    assert job["state"] == "error" and "Nincs elerheto adat" in job["message"]


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


def test_web_actions_validate_before_starting(store):
    with pytest.raises(ValueError, match="Ervenytelen ticker"):
        webapp.backtest_action("run", {"symbol": "rossz ticker!", "strategy": "etf"})
    with pytest.raises(ValueError, match="Ismeretlen parameter"):
        webapp.backtest_action("run", {"symbol": "TEST", "strategy": "etf", "params": {"x": 1}})
    with pytest.raises(ValueError):
        webapp.backtest_action("run", {"symbol": "TEST", "strategy": "etf", "start": "1900-01-01"})
    with pytest.raises(ValueError):
        webapp.backtest_action("data", {"symbol": "TEST", "start": "1900-01-01"})
    payload = webapp.backtest_payload()
    assert [x["symbol"] for x in payload["symbols"]] == ["TEST"]
    assert {x["id"]: x["available"] for x in payload["strategies"]}["breakout"] is False


def test_warns_when_split_adjusted_prices_hide_signals(store):
    s = Settings()
    r = runner.run("TEST", "etf", s, start="2012-01-02", overrides={"min_price": 150})
    assert any("split-korrekcio" in w for w in r["warnings"])
    assert not any("split-korrekcio" in w for w in runner.run("TEST", "etf", s, start="2012-01-02")["warnings"])
