"""Napi eletjel: tartalom, figyelmeztetesek, kikuldes (jsonl es csendes idoszak nelkul), indulasi uzenet."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from stockwatch import heartbeat
from stockwatch.alerts import Dispatcher
from stockwatch.alerts.base import AlertChannel
from stockwatch.config import AlertSettings, Settings


class FakeTracker:
    def __init__(self, *a, **k) -> None:
        pass

    def active(self):
        return [object(), object()]

    def closed_stats(self):
        return {"n": 4, "win": 0.5, "ev": 0.3, "total": 1.2}


@pytest.fixture()
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(heartbeat.positions, "PositionTracker", FakeTracker)
    monkeypatch.setattr(heartbeat.broker.AlpacaClient, "from_env", classmethod(lambda cls: None))
    monkeypatch.setattr(heartbeat.housekeeping, "total_usage_mb", lambda s=None: 36.0)
    monkeypatch.setattr(heartbeat, "trading_day", lambda day: True)
    monkeypatch.setattr(heartbeat, "STATE_PATH", tmp_path / "heartbeat.json")
    return Settings(alerts=AlertSettings(console=False, jsonl=False, telegram=False, discord=False, webhook=False))


DAY = pd.Timestamp("2026-10-08", tz="America/New_York")      # mult heti, lezart nap
STARTED = datetime.now(timezone.utc) - timedelta(days=3, hours=5)


def test_healthy_day(settings):
    report = heartbeat.DayReport(day=DAY, watchlist_total=15, watchlist_analysed=15, last_bar=pd.Timestamp("2026-10-08"))
    alert = heartbeat.build(settings, report, Counter({"entry_breakout": 1, "broker_filled": 2}),
                            heartbeat.ErrorCounter(), STARTED)
    assert alert.event == "heartbeat" and alert.title == "Eletjel - rendben"
    body = alert.body
    assert "Watchlist: 15/15 papir elemezve, utolso gyertya: 2026-10-08" in body
    assert "Mai ertesitesek: 3 (paper 2, belepes 1)" in body
    assert "Kovetett trade-ek: 2 nyitott | lezart 4, talalati arany 50%, osszesen +1.2R" in body
    assert "Paper szamla: nincs beallitva" in body
    assert "Monitor fut: 3 napja 5 oraja" in body


def test_problems_turn_into_warning(settings):
    errors = heartbeat.ErrorCounter()
    logging.getLogger("stockwatch.feed").addHandler(errors)
    try:
        logging.getLogger("stockwatch.feed").error("Yahoo nem valaszol")
    finally:
        logging.getLogger("stockwatch.feed").removeHandler(errors)
    report = heartbeat.DayReport(day=DAY, watchlist_total=15, watchlist_analysed=12,
                                 last_bar=pd.Timestamp("2026-10-07"), failures=["paper szinkron"])
    alert = heartbeat.build(settings, report, Counter(), errors, STARTED)
    assert alert.event == "heartbeat_warn"
    for text in ("paper szinkron", "3 papirhoz nincs adat", "nem friss", "1 hiba"):
        assert text in alert.title
    assert "utolso hiba: feed: Yahoo nem valaszol" in alert.body


def test_holiday_is_not_stale(settings, monkeypatch):
    monkeypatch.setattr(heartbeat, "trading_day", lambda day: False)
    report = heartbeat.DayReport(day=DAY, watchlist_total=1, watchlist_analysed=1, last_bar=pd.Timestamp("2026-10-07"))
    alert = heartbeat.build(settings, report, Counter(), heartbeat.ErrorCounter(), STARTED)
    assert alert.event == "heartbeat" and "unnepnap" in alert.body


def test_before_close_today_is_not_stale(settings, monkeypatch):
    monkeypatch.setattr(heartbeat.sessions, "session_closed", lambda day, now=None: False)
    report = heartbeat.DayReport(day=DAY, watchlist_total=1, watchlist_analysed=1, last_bar=pd.Timestamp("2026-10-07"))
    alert = heartbeat.build(settings, report, None, None, None)          # kezi jelentes, nap kozben
    assert alert.event == "heartbeat"
    assert "Mai ertesitesek" not in alert.body and "Monitor fut" not in alert.body


class Recorder(AlertChannel):
    def __init__(self, name):
        self.name = name
        self.got = []

    def send(self, alert):
        self.got.append(alert.event)
        return True


def test_broadcast_skips_jsonl_and_ignores_quiet_hours(settings):
    quiet = settings.model_copy(update={"alerts": AlertSettings(quiet_hours=["00:00-23:59"], console=False,
                                                                jsonl=False, telegram=False, discord=False,
                                                                webhook=False)})
    d = Dispatcher(quiet)
    discord, jsonl = Recorder("discord"), Recorder("jsonl")
    d.channels = [discord, jsonl]
    d.dispatch(heartbeat.start_alert(3, None))          # rendes jelzes csendes idoben: csak jsonl
    assert discord.got == [] and jsonl.got == ["heartbeat_start"]
    d.broadcast(heartbeat.start_alert(3, None))         # eletjel: kimegy, de a jsonl-be nem
    assert discord.got == ["heartbeat_start"] and jsonl.got == ["heartbeat_start"]
    assert d.sent["heartbeat_start"] == 1               # csak a dispatch szamol


def test_start_notice_throttled(settings):
    now = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    assert heartbeat.start_notice_due(now)
    assert not heartbeat.start_notice_due(now + timedelta(minutes=10))     # ujrainditasi hurok
    assert heartbeat.start_notice_due(now + timedelta(minutes=31))


def _ny(text):
    return pd.Timestamp(text, tz="America/New_York")


def test_last_session_waits_for_close_run(settings, monkeypatch):
    monkeypatch.setattr(heartbeat, "trading_day", lambda day: day.weekday() < 5)
    assert heartbeat.last_session(_ny("2026-10-13 16:14")) == pd.Timestamp("2026-10-12")
    assert heartbeat.last_session(_ny("2026-10-13 16:15")) == pd.Timestamp("2026-10-13")
    assert heartbeat.last_session(_ny("2026-10-13 06:07")) == pd.Timestamp("2026-10-12")
    assert heartbeat.last_session(_ny("2026-10-12 08:00")) == pd.Timestamp("2026-10-09")    # hetfo -> pentek


def test_missed_close_is_caught_up(settings, monkeypatch):
    monkeypatch.setattr(heartbeat, "trading_day", lambda day: day.weekday() < 5)
    morning = _ny("2026-10-13 06:07")            # a szerver hajnalban indul, a tegnapi zaraskor nem futott
    assert heartbeat.missed_close(morning) is None                     # friss telepites: nem potolunk
    heartbeat.mark_close_done(pd.Timestamp("2026-10-09"))
    assert heartbeat.missed_close(morning) == pd.Timestamp("2026-10-12")
    heartbeat.mark_close_done(pd.Timestamp("2026-10-12"))
    assert heartbeat.missed_close(morning) is None
    assert heartbeat.missed_close(_ny("2026-10-13 17:00")) == pd.Timestamp("2026-10-13")   # a mai zaras is kimaradt


def test_state_keys_kept_together(settings):
    heartbeat.mark_close_done(pd.Timestamp("2026-10-12"))
    assert heartbeat.start_notice_due(datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc))
    assert heartbeat.missed_close(_ny("2026-10-13 06:07")) is None     # az indulasi uzenet nem torolte


def test_late_run_is_marked_in_heartbeat(settings):
    report = heartbeat.DayReport(day=DAY, watchlist_total=1, watchlist_analysed=1,
                                 last_bar=pd.Timestamp("2026-10-08"), late=True)
    alert = heartbeat.build(settings, report, Counter(), heartbeat.ErrorCounter(), STARTED)
    assert "Potlolagos futas" in alert.body
