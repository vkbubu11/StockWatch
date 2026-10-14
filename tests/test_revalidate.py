"""Lezart gyertya + nyitas elotti ujraellenorzes tesztjei."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stockwatch import broker, ledger, positions, replay, revalidate, sessions, signal_review
from stockwatch.forecast import Calibration
from tests.test_broker import FakeAlpaca
from tests.test_ledger import scenario


def ny(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="America/New_York")


def frame(last_day: str) -> pd.DataFrame:
    idx = pd.bdate_range(end=last_day, periods=3)
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


# ---------------------------------------------------------------------------
# Lezart gyertya
# ---------------------------------------------------------------------------
def test_partial_today_bar_is_dropped_during_session():
    df = frame("2026-10-12")
    assert len(sessions.completed_bars(df, ny("2026-10-12 10:30"))) == 2       # nyitvatartas alatt
    assert len(sessions.completed_bars(df, ny("2026-10-12 09:00"))) == 2       # nyitas elott
    assert len(sessions.completed_bars(df, ny("2026-10-12 16:10"))) == 3       # zaras utan
    assert len(sessions.completed_bars(frame("2026-10-09"), ny("2026-10-12 10:30"))) == 3   # pentek, hetfon
    assert sessions.completed_bars(pd.DataFrame(), ny("2026-10-12 10:30")).empty


# ---------------------------------------------------------------------------
# Ujraellenorzes
# ---------------------------------------------------------------------------
@pytest.fixture
def alerted(tmp_path, monkeypatch):
    settings, raw, signal = scenario()
    for name, path in (("positions_path", "pos.json"), ("broker_path", "broker.json")):
        monkeypatch.setattr(type(settings), name, property(lambda self, p=path: tmp_path / p))
    rec = ledger.Ledger(settings, Calibration()).record(signal, "alerted", "ok", raw=raw, snapshot=True)
    monkeypatch.setattr(replay.store, "load", lambda *a, **k: raw)
    monkeypatch.setattr(replay.market, "load_benchmark", lambda *a, **k: pd.DataFrame())
    next_day = (pd.Timestamp(rec["as_of"]) + timedelta(days=1)).date()
    return settings, raw, signal, rec, next_day


class Collect:
    def __init__(self):
        self.alerts = []

    def dispatch(self, alert):
        self.alerts.append(alert)


def test_pending_only_unchecked_past_alerts(alerted):
    settings, raw, signal, rec, next_day = alerted
    assert [r["signal_id"] for r in revalidate.pending(next_day)] == [signal.signal_id]
    assert revalidate.pending(pd.Timestamp(rec["as_of"]).date()) == []        # a jelzes napjan meg nem
    ledger.append_followup(rec, "confirmed", "ok")
    assert revalidate.pending(next_day) == []                                  # mar ellenorizve


def test_unchanged_final_data_confirms(alerted):
    settings, raw, signal, rec, next_day = alerted
    out = revalidate.revalidate(settings, Collect(), refresh=False, today=next_day)
    assert out[0]["decision"] == "confirmed" and not out[0]["actions"]
    assert ledger.find(signal.signal_id)[0]["decision"] == "confirmed"


def test_revoked_signal_cancels_paper_order_and_virtual_position(alerted, monkeypatch):
    settings, raw, signal, rec, next_day = alerted
    # a jelzesbol paper megbizas es virtualis pozicio lett
    sid = rec["signal_id"]
    signal.kind = "breakout"                       # az elokeszulet nem kerul kovetesre
    tracker = positions.PositionTracker(settings)
    tracker.register(signal).signal_id = sid       # a naplozott jelzeshez tartozik
    tracker.save()
    fake = FakeAlpaca()
    book = broker.BrokerBook(settings)
    trade = broker.BrokerTrade(id="T", symbol="TEST", pattern="Bull zaszlo", kind="breakout", signal_date=rec["as_of"],
                               entry_limit=100, stop=90, target=130, partial_price=None, shares=1, horizon=10,
                               exit_mode="target", signal_id=sid)
    order = fake.submit_order({"symbol": "TEST", "qty": "1", "side": "buy", "type": "limit", "time_in_force": "gtc",
                               "limit_price": "100", "order_class": "bracket", "take_profit": {"limit_price": "130"},
                               "stop_loss": {"stop_price": "90"}})
    trade.set_legs([broker.Leg("target", 1, 130, order_id=order["id"])])
    book.trades[trade.id] = trade
    book.save()
    # a veglegesitett adaton mar nem all meg
    monkeypatch.setattr(revalidate.replay, "replay", lambda *a, **k: {
        "found": True, "replayed": {"qualifies": False, "kind": "breakout"},
        "diffs": [{"field": "szuro.Trend", "then": True, "now": False}], "verdict": "ELTER"})
    sink = Collect()
    out = revalidate.revalidate(settings, sink, refresh=False, today=next_day, client=fake)
    assert out[0]["decision"] == "revoked" and "szuro.Trend" in out[0]["reason"]
    assert "paper megbizas visszavonva" in out[0]["actions"] and "virtualis pozicio kimarad" in out[0]["actions"]
    assert broker.BrokerBook(settings).get("T").status == "cancelled"
    assert fake.orders_by_id[order["id"]]["status"] == "canceled"
    pos = next(iter(positions.PositionTracker(settings).positions.values()))
    assert pos.status == "skipped" and "visszavonva" in pos.reason
    assert sink.alerts[0].event == "entry_revoked" and sink.alerts[0].extra["signal_id"] == sid
    assert signal_review.category({"event": "entry_revoked"}) == ("other", "")


# ---------------------------------------------------------------------------
# Utemezes: New York-i idoben, a gep helyi idejetol fuggetlenul
# ---------------------------------------------------------------------------
def test_schedule_runs_in_new_york_time():
    from stockwatch import monitor
    from stockwatch.config import Settings

    trig = monitor.schedule_triggers(Settings())
    budapest = pd.Timestamp("2026-10-12 16:00", tz="Europe/Budapest").to_pydatetime()   # 10:00 NY
    close = trig["close"].get_next_fire_time(None, budapest)
    assert pd.Timestamp(close).tz_convert("America/New_York").strftime("%H:%M") == "16:15"
    assert pd.Timestamp(close).tz_convert("Europe/Budapest").strftime("%H:%M") == "22:15"
    intraday = trig["intraday"].get_next_fire_time(None, budapest)
    assert pd.Timestamp(intraday).tz_convert("America/New_York").strftime("%H:%M") == "10:00"   # nyitvatartas alatt
    paper = trig["broker"].get_next_fire_time(None, budapest + pd.Timedelta(seconds=30))
    assert pd.Timestamp(paper).tz_convert("America/New_York").strftime("%H:%M") == "10:01"     # percenkent
    preopen = trig["preopen"].get_next_fire_time(None, budapest)
    assert pd.Timestamp(preopen).tz_convert("America/New_York").strftime("%a %H:%M") == "Tue 09:05"
