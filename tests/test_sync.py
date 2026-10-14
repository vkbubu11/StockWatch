"""Az Ertesitesek, a Jelzett trade-ek es a Jelzesnaplo ugyanazt mutatja (kozos jelazonosito)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from stockwatch import ledger, positions, replay, webapp
from stockwatch.broker import BrokerBook, BrokerTrade, Leg
from stockwatch.forecast import Calibration
from tests.test_ledger import journal_dir, scenario  # noqa: F401  (fixture)


@pytest.fixture()
def env(tmp_path, monkeypatch, journal_dir):  # noqa: F811
    settings, raw, signal = scenario()
    signal.kind = "breakout"                         # az elokeszulet nem trade
    cls = type(settings)
    monkeypatch.setattr(cls, "positions_path", property(lambda self: tmp_path / "pos.json"))
    monkeypatch.setattr(cls, "broker_path", property(lambda self: tmp_path / "broker.json"))
    monkeypatch.setattr(cls, "signals_path", property(lambda self: tmp_path / "signals.jsonl"))
    monkeypatch.setattr(webapp, "load_settings", lambda: settings)
    monkeypatch.setattr(webapp.store, "load", lambda *a, **k: raw)
    return settings, raw, signal


def _trade(signal, status="open", signal_id=None) -> BrokerTrade:
    trade = BrokerTrade(id=f"{signal.symbol}|{signal.as_of:%Y-%m-%d}|{signal.pattern.name}", symbol=signal.symbol,
                        pattern=signal.pattern.name, kind="breakout", signal_date=f"{signal.as_of:%Y-%m-%d}",
                        entry_limit=101.0, stop=95.0, target=115.0, partial_price=107.0, shares=10, horizon=20,
                        exit_mode="partial", status=status, entry_price=100.5, filled_at=f"{signal.as_of:%Y-%m-%d}",
                        signal_id=signal.signal_id if signal_id is None else signal_id)
    trade.set_legs([Leg("partial", 5, 107.0, filled_qty=5, entry_fill=100.5),
                    Leg("target", 5, 115.0, filled_qty=5, entry_fill=100.5)])
    return trade


def _save_book(settings, *trades) -> None:
    book = BrokerBook(settings)
    for t in trades:
        book.trades[t.id] = t
    book.save()


def _write_alerts(settings, *records) -> None:
    settings.signals_path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _legacy_alerts(signal) -> list[dict]:
    """A jelzesazonosito bevezetese elotti ertesitesek (entry_breakout + broker_submitted)."""
    day, name = f"{signal.as_of:%Y-%m-%d}", signal.pattern.name
    trade_id = f"{signal.symbol}|{day}|{name}"
    return [
        {"timestamp": "2026-10-12T13:07:58+00:00", "event": "entry_breakout", "symbol": signal.symbol,
         "title": "belepes", "as_of": f"{day} 00:00:00", "pattern": {"name": name},
         "entry": {"signal_id": None, "kind": "breakout", "as_of": day, "pattern": name}},
        {"timestamp": "2026-10-12T13:08:06+00:00", "event": "broker_submitted", "symbol": signal.symbol,
         "title": "bekuldve", "entry": {"broker_trade": trade_id}},
    ]


# ---------------------------------------------------------------------------
# Jelzett trade-ek = minden paper trade
# ---------------------------------------------------------------------------
def test_paper_trade_without_tracked_signal_is_listed(env):
    """A bejelentett hiba: a kezzel bekuldott (DDOG) paper trade nem latszott a Jelzett trade-ek kozott."""
    settings, _, signal = env
    trade = _trade(signal)
    _save_book(settings, trade)
    tracker = positions.PositionTracker(settings)
    p = tracker.positions[trade.id]
    assert (p.source, p.status, p.signal_id, p.entry_price) == ("alpaca", "open", signal.signal_id, 100.5)
    tracker.save()                                   # a monitor menti -> tartos
    assert trade.id in positions.PositionTracker(settings, reconcile=False).positions


def test_mirror_fills_missing_signal_id(env):
    settings, _, signal = env
    tracker = positions.PositionTracker(settings)
    tracker.register(signal).signal_id = ""          # regi, azonosito nelkuli kovetes
    tracker.save()
    _save_book(settings, _trade(signal))
    assert positions.PositionTracker(settings, read_only=True).active()[0].signal_id == signal.signal_id


# ---------------------------------------------------------------------------
# Regi, azonosito nelkuli bejegyzesek osszekotese
# ---------------------------------------------------------------------------
def test_alert_key_and_lookup(env):
    settings, raw, signal = env
    ledger.Ledger(settings, Calibration()).record(signal, "skipped", "x", raw=raw)
    key = (signal.symbol, f"{signal.as_of:%Y-%m-%d}", signal.pattern.name)
    entry_alert, broker_alert = _legacy_alerts(signal)
    assert ledger.alert_key(entry_alert) == key == ledger.alert_key(broker_alert)
    assert ledger.alert_key({**entry_alert, "event": "entry_setup"}) is None
    assert ledger.lookup_ids({key}) == {key: signal.signal_id}
    assert ledger.lookup_ids(set()) == {}


def test_pages_link_legacy_records_to_the_same_signal(env):
    """Ertesites, kovetett trade es naplosor - azonosito nelkul irva is ugyanarra a jelzesre mutat."""
    settings, raw, signal = env
    ledger.Ledger(settings, Calibration()).record(signal, "skipped", "mar van nyitott trade", raw=raw)
    _save_book(settings, _trade(signal, status="cancelled", signal_id=""))
    _write_alerts(settings, *_legacy_alerts(signal))

    alerts = webapp.signals_payload()["records"]
    assert [a["signal_id"] for a in alerts] == [signal.signal_id] * 2

    tracked = webapp.positions_payload()["positions"]
    assert [p["signal_id"] for p in tracked] == [signal.signal_id]

    row = webapp.ledger_payload()["rows"][0]
    assert row["signal_id"] == signal.signal_id
    assert row["trade"]["source"] == "alpaca" and row["trade"]["broker_status"] == "cancelled"

    life = replay.trace(signal.signal_id, settings)
    assert [a["event"] for a in life["alerts"]] == ["entry_breakout", "broker_submitted"]
    assert life["trade"]["status"] == "cancelled" and life["position"]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Kezi bekuldes: naplo + ertesites, mint a monitornal
# ---------------------------------------------------------------------------
def test_manual_submit_is_journalled_and_notified(env, monkeypatch):
    settings, raw, signal = env
    sent = []
    monkeypatch.setattr(webapp, "Dispatcher", lambda s: SimpleNamespace(dispatch=sent.append))
    monkeypatch.setattr(webapp.market, "load_benchmark", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(webapp, "load_watchlist", lambda: SimpleNamespace(symbols=[signal.symbol]))
    trade = _trade(signal, status="submitted")
    webapp.record_manual_submit(settings, signal, None, trade)

    rec = ledger.find(signal.signal_id)[0]
    assert (rec["decision"], rec["source"], rec["mode"], rec["snapshot"]) == ("manual", "web", "watchlist", True)
    assert [(a.event, a.extra["signal_id"]) for a in sent] == [("broker_submitted", signal.signal_id)]

    _save_book(settings, trade)
    row = webapp.ledger_payload()["rows"][0]
    assert row["decision"] == "manual" and row["trade"]["broker_status"] == "submitted"
    assert webapp.ledger_payload()["counts"]["manual"] == 1
