"""Gyors kiertekeles (quickcheck.py): jelzesek, formalodo alakzatok, Visszahuzas-szabalyok egy papirra."""
from __future__ import annotations

import pandas as pd
import pytest

from stockwatch import entry, market, quickcheck, shadow
from stockwatch.config import Settings
from stockwatch.forecast import Calibration, Forecast

from .test_entry import enrich, lenient, make_match, uptrend


def _settings(**entry_overrides) -> Settings:
    return Settings(entry=lenient().model_copy(update=entry_overrides))


def _forecast(df: pd.DataFrame, matches) -> Forecast:
    return Forecast(symbol="TEST", as_of=df.index[-1], price=float(df["close"].iloc[-1]), score=0.3,
                    label="Emelkedo", matches=matches)


def _view(df, matches, settings, monkeypatch, universes=None):
    monkeypatch.setattr(quickcheck, "analyse", lambda *a, **k: _forecast(df, matches))
    monkeypatch.setattr(shadow, "score_signals", lambda *a, **k: None)
    ctx = market.MarketContext(benches={})
    return quickcheck._view("TEST", df, settings, Calibration(), ctx, None, universes or {}, True)


def test_what_if_covers_only_patterns_without_a_signal():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    cfg = lenient().model_copy(update={"confirm_breakout": True})
    kind_of = quickcheck._what_if(df, cfg)
    assert kind_of(make_match(df, trigger=close + 3 * atr, status="forming")) == "setup"      # tavol: formalodo
    assert kind_of(make_match(df, trigger=close + 0.5 * atr, status="forming")) is None       # mar elokeszulet-jelzes
    assert kind_of(make_match(df, trigger=close * 0.99)) == "breakout"                        # ma tort ki, megerosites holnap
    assert kind_of(make_match(df, trigger=close * 0.99, trigger_pos=-2)) is None              # megerositett: valodi jelzes
    assert kind_of(make_match(df, trigger=close + 3 * atr, status="forming", direction="bearish")) is None


def test_view_separates_signals_from_forming(monkeypatch):
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    near = make_match(df, trigger=close + 0.5 * atr, status="forming")
    far = make_match(df, trigger=close + 3 * atr, status="forming", target_mult=0.4)
    far.name = "Dupla alj"
    view = _view(df, [near, far], _settings(min_score=0), monkeypatch)

    assert [s["kind"] for s in view["signals"]] == ["setup"]
    assert view["signals"][0]["hypothetical"] is False
    forming = view["forming"]
    assert [(s["pattern"], s["kind"]) for s in forming] == [("Dupla alj", "setup")]
    assert forming[0]["hypothetical"] is True and forming[0]["qualifies"] is False   # soha nem jelzes
    assert forming[0]["would_pass"] is True and forming[0]["score"] > 0
    assert forming[0]["distance_atr"] == pytest.approx(3.0, abs=0.01)
    assert forming[0]["status"] == "forming"
    assert {p["name"] for p in view["patterns"]} == {"Bull zaszlo", "Dupla alj"}


def test_forming_uses_same_checks_and_score_as_entry(monkeypatch):
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    far = make_match(df, trigger=close + 3 * atr, status="forming")
    settings = _settings(min_risk_reward=50)
    view = _view(df, [far], settings, monkeypatch)
    ref = entry.evaluate("TEST", df, [far], settings, kind_of=lambda m: "setup",
                         regime=market.Regime(True, None, None, "kikapcsolva"))[0]
    got = view["forming"][0]
    assert got["score"] == ref.score
    assert "Hozam/kockazat" in got["failed"] and got["would_pass"] is False


def test_pullback_reports_rsi2_gap_and_universe(monkeypatch):
    df = enrich(uptrend())
    view = _view(df, [], _settings(), monkeypatch, universes={"pullback": {"TEST"}})
    rows = {r["strategy"]: r for r in view["pullback"]}
    assert set(rows) == {"pullback", "deep", "etf"}
    pb = rows["pullback"]
    assert pb["in_universe"] is True and rows["etf"]["in_universe"] is False
    assert pb["rsi2"] > pb["rsi2_max"]                 # emelkedo trendben nincs tuladottsag
    assert "RSI(2)" in pb["failed"] and pb["passed"] is False
    assert pb["met"] < pb["total"] and pb["checks"][0]["name"] == "RSI(2)"
    assert pb["limit"] < view["price"] and pb["stop"] < pb["limit"]


def test_short_history_and_missing_data(monkeypatch):
    df = enrich(uptrend(bars=100))
    view = _view(df, [], _settings(), monkeypatch)
    assert all("tul rovid" in r["error"] for r in view["pullback"])

    monkeypatch.setattr(quickcheck.best, "_frame", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ValueError, match="Nincs arfolyamadat"):
        quickcheck.evaluate_symbol("NOPE", Settings(), refresh=False)
