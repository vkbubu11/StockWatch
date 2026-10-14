"""A sajat (kezzel felvett) poziciok tesztjei: tarolas, validalas, kiertekeles,
ertesitesi ismetlesvedelem."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stockwatch import holdings, housekeeping
from stockwatch.config import Settings
from stockwatch.forecast import Forecast
from stockwatch.patterns.base import PatternMatch
from tests.test_entry import enrich, uptrend


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = Settings()
    monkeypatch.setattr(type(s), "holdings_path", property(lambda self: tmp_path / "holdings.json"))
    return s


def downtrend(bars: int = 320) -> pd.DataFrame:
    df = uptrend(bars, drift=0.0)
    closes = 100 * np.cumprod(np.full(bars, 1 - 0.002))
    df["close"] = closes
    df["open"] = np.r_[closes[0], closes[:-1]]
    df["high"] = np.maximum(df["open"], df["close"]) * 1.004
    df["low"] = np.minimum(df["open"], df["close"]) * 0.996
    return df


def forecast_for(df: pd.DataFrame, matches=None, score: float = 0.3) -> Forecast:
    return Forecast(symbol="TEST", as_of=df.index[-1], price=float(df["close"].iloc[-1]),
                    score=score, label="Emelkedo", matches=matches or [])


def holding(df: pd.DataFrame, **kw) -> holdings.Holding:
    base = dict(id="TEST-1", symbol="TEST", entry_price=float(df["close"].iloc[-60]),
                entry_date=f"{df.index[-60]:%Y-%m-%d}", shares=10)
    base.update(kw)
    return holdings.Holding(**base)


# ---------------------------------------------------------------------------
# Tarolas es validalas
# ---------------------------------------------------------------------------
def test_store_roundtrip_and_lifecycle(settings):
    own = holdings.HoldingStore(settings)
    h = own.add("aapl", 180, 10, "2026-03-02", stop=170, target=210, note="teszt")
    own.save()

    again = holdings.HoldingStore(settings)
    loaded = again.get(h.id)
    assert loaded.symbol == "AAPL" and loaded.stop == 170 and loaded.note == "teszt"
    assert again.symbols() == ["AAPL"]

    again.close(h.id, 195)
    again.save()
    closed = holdings.HoldingStore(settings).get(h.id)
    assert closed.status == "closed" and closed.realised_pnl == pytest.approx(150)
    assert holdings.HoldingStore(settings).open() == []


@pytest.mark.parametrize("kwargs, message", [
    (dict(entry_price=-1), "belepo"),
    (dict(shares=0), "darab"),
    (dict(entry_date=(date.today() + timedelta(days=3)).isoformat()), "jovoben"),
    (dict(entry_date="2026/03/02"), "formatumu"),
    (dict(stop=200, target=190), "cel"),
])
def test_validation(settings, kwargs, message):
    own = holdings.HoldingStore(settings)
    args = dict(symbol="AAPL", entry_price=100, shares=5, entry_date="2026-03-02")
    args.update(kwargs)
    with pytest.raises(ValueError, match=message):
        own.add(**args)


def test_invalid_ticker_rejected(settings):
    with pytest.raises(ValueError, match="ticker"):
        holdings.HoldingStore(settings).add("AAPL; rm", 100, 1)


def test_closed_holdings_are_capped(settings, monkeypatch):
    monkeypatch.setattr(holdings, "MAX_CLOSED", 3)
    own = holdings.HoldingStore(settings)
    for i in range(6):
        h = own.add("MSFT", 100 + i, 1, "2026-01-02")
        own.close(h.id, 110, on=f"2026-02-{i + 1:02d}")
    own.add("MSFT", 100, 1, "2026-01-02")       # a nyitott sosem esik ki
    own.save()
    again = holdings.HoldingStore(settings)
    assert len(again.open()) == 1
    closed = [h for h in again.items.values() if not h.is_open]
    assert sorted(h.closed_at for h in closed) == ["2026-02-04", "2026-02-05", "2026-02-06"]


def test_update_resets_stop_alert(settings):
    own = holdings.HoldingStore(settings)
    h = own.add("AAPL", 100, 1, "2026-03-02", stop=90)
    h.alerted["stop"] = "2026-04-01"
    own.update(h.id, stop=95)
    assert h.stop == 95 and "stop" not in h.alerted


def test_data_usage_lists_holdings(settings):
    labels = [u.label for u in housekeeping.disk_usage(settings)]
    assert any("holdings.json" in label for label in labels)


# ---------------------------------------------------------------------------
# Kiertekeles
# ---------------------------------------------------------------------------
def test_healthy_uptrend_with_stop_is_ok():
    df = enrich(uptrend())
    close = float(df["close"].iloc[-1])
    ev = holdings.evaluate(holding(df, stop=close * 0.85), df, forecast_for(df), Settings())
    assert ev.verdict in ("ok", "warn")
    assert not any(c.level == "bad" for c in ev.checks)
    assert ev.pnl > 0 and ev.r_multiple is not None and ev.r_multiple > 0


def test_missing_stop_warns_and_suggests_one_below_noise():
    df = enrich(uptrend())
    ev = holdings.evaluate(holding(df), df, forecast_for(df), Settings())
    stop_check = next(c for c in ev.checks if c.name == "Stop")
    assert stop_check.level == "warn" and "javaslat" in stop_check.detail
    assert ev.suggested_stop <= ev.price - ev.atr + 1e-9
    assert ev.r_multiple is None                      # stop nelkul nincs 1R


def test_stop_hit_is_bad():
    df = enrich(uptrend())
    low = float(df["low"].iloc[-1])
    ev = holdings.evaluate(holding(df, stop=low + 0.01), df, forecast_for(df), Settings())
    assert ev.stop_hit and ev.verdict == "bad"


def test_broken_trend_is_bad():
    df = enrich(downtrend())
    ev = holdings.evaluate(holding(df), df, forecast_for(df, score=-0.4), Settings())
    trend = next(c for c in ev.checks if c.name == "Hosszu trend")
    assert trend.level == "bad" and ev.verdict == "bad"
    assert ev.pnl < 0


def test_triggered_bearish_pattern_is_bad():
    df = enrich(uptrend())
    close = float(df["close"].iloc[-1])
    top = PatternMatch(symbol="TEST", name="Dupla teto", direction="bearish", status="triggered",
                       quality=0.75, trigger_level=close * 1.01, target=close * 0.9,
                       invalidation=close * 1.08, start_date=df.index[-50], end_date=df.index[-3],
                       trigger_date=df.index[-1])
    ev = holdings.evaluate(holding(df, stop=close * 0.85), df, forecast_for(df, [top]), Settings())
    check = next(c for c in ev.checks if c.name == "Medves minta")
    assert check.level == "bad" and "Dupla teto" in check.detail


def test_deep_pullback_from_high_warns():
    df = uptrend()
    df.iloc[-15:, df.columns.get_loc("close")] *= 0.8      # eles visszaeses a csucsrol
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = np.maximum(df["open"], df["close"]) * 1.004
    df["low"] = np.minimum(df["open"], df["close"]) * 0.996
    df = enrich(df)
    ev = holdings.evaluate(holding(df), df, forecast_for(df), Settings())
    check = next(c for c in ev.checks if c.name == "Visszaeses")
    assert check.level == "warn" and "csucs" in check.detail
    calm = enrich(uptrend())
    assert not any(c.name == "Visszaeses"
                   for c in holdings.evaluate(holding(calm), calm, forecast_for(calm), Settings()).checks)


def test_stop_above_entry_has_no_r_multiple():
    df = enrich(uptrend())
    h = holding(df)
    h.stop = h.entry_price * 1.02                     # nyereseg mar bebiztositva
    ev = holdings.evaluate(h, df, forecast_for(df), Settings())
    assert ev.r_multiple is None and ev.pnl_at_stop > 0


def test_user_target_preferred_over_suggestion():
    df = enrich(uptrend())
    close = float(df["close"].iloc[-1])
    ev = holdings.evaluate(holding(df, target=close * 1.2), df, forecast_for(df), Settings())
    assert ev.target == pytest.approx(close * 1.2) and ev.target_source == "sajat"


def test_earnings_inside_blackout_warns():
    df = enrich(uptrend())
    soon = df.index[-1].date() + timedelta(days=3)
    ev = holdings.evaluate(holding(df), df, forecast_for(df), Settings(), earnings_date=soon)
    check = next(c for c in ev.checks if c.name == "Earnings")
    assert check.level == "warn"


def test_evaluation_ignores_future_bars():
    df = enrich(uptrend())
    h = holding(df, stop=float(df["close"].iloc[-1]) * 0.85)
    before = holdings.evaluate(h, df.iloc[:-5], forecast_for(df.iloc[:-5]), Settings())
    assert before.as_of == df.index[-6]
    assert before.price == pytest.approx(float(df["close"].iloc[-6]))


# ---------------------------------------------------------------------------
# Ertesitesek
# ---------------------------------------------------------------------------
def test_alert_events_fire_once_and_on_worsening():
    df = enrich(uptrend())
    close = float(df["close"].iloc[-1])
    h = holding(df, stop=close * 0.85)
    s = Settings()

    first = holdings.evaluate(h, df, forecast_for(df), s)
    assert "hold_risk" not in holdings.alert_events(first)     # elso kiertekeles: nincs riasztas
    assert h.last_verdict == first.verdict

    h.last_verdict, h.stop = "ok", None
    bad = holdings.evaluate(h, enrich(downtrend()), forecast_for(df, score=-0.5), s)
    assert "hold_risk" in holdings.alert_events(bad)
    assert "hold_risk" not in holdings.alert_events(bad)       # ugyanaz az allapot: nem ismetel

    h.stop = float(df["low"].iloc[-1]) + 0.01
    hit = holdings.evaluate(h, df, forecast_for(df), s)
    assert holdings.alert_events(hit).count("hold_stop") == 1
    assert "hold_stop" not in holdings.alert_events(hit)


def test_earnings_alert_once_per_date():
    df = enrich(uptrend())
    h = holding(df, stop=float(df["close"].iloc[-1]) * 0.85)
    soon = df.index[-1].date() + timedelta(days=3)
    ev = holdings.evaluate(h, df, forecast_for(df), Settings(), earnings_date=soon)
    assert "hold_earnings" in holdings.alert_events(ev)
    assert "hold_earnings" not in holdings.alert_events(ev)


def test_event_text_has_disclaimer_and_reasons():
    df = enrich(downtrend())
    h = holding(df)
    ev = holdings.evaluate(h, df, forecast_for(df, score=-0.5), Settings())
    title, body = holdings.format_event(ev, "hold_risk")
    assert "TEST" in title and "Kockazatos".upper() in body
    assert "Hosszu trend" in body and "Nem befektetesi tanacs" in body
