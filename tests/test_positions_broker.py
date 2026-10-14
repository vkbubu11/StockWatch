"""A Jelzett trade-ek = Alpaca: ha a jelzesbol paper trade lett, a kovetes annak allapotat mutatja."""
from __future__ import annotations

import threading
import time

import pytest

from stockwatch import broker, positions
from stockwatch.broker import BrokerBook, BrokerTrade, Leg
from tests.test_entry import _future, _signal, enrich, lenient, settings_with, uptrend


@pytest.fixture()
def env(tmp_path, monkeypatch):
    settings = settings_with(lenient())
    monkeypatch.setattr(type(settings), "positions_path", property(lambda self: tmp_path / "pos.json"))
    monkeypatch.setattr(type(settings), "broker_path", property(lambda self: tmp_path / "broker.json"))
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    tracker = positions.PositionTracker(settings)
    pos = tracker.register(signal)
    tracker.save()
    return settings, df, signal, pos


def _trade(pos, status, legs, **fields) -> BrokerTrade:
    trade = BrokerTrade(id=pos.id, symbol=pos.symbol, pattern=pos.pattern, kind="breakout",
                        signal_date=pos.signal_date, entry_limit=101.0, stop=95.0, target=115.0,
                        partial_price=107.0, shares=10, horizon=20, exit_mode="partial", status=status, **fields)
    trade.set_legs(legs)
    return trade


def _save(settings, *trades) -> None:
    book = BrokerBook(settings)
    for t in trades:
        book.trades[t.id] = t
    book.save()


def _reload(settings, pid):
    return positions.PositionTracker(settings, read_only=True).positions[pid]


def test_cancelled_on_alpaca_is_not_waiting(env):
    """A bejelentett hiba: Alpacan visszavonva, a feluleten megis 'belepesre var' volt."""
    settings, _, _, pos = env
    assert _reload(settings, pos.id).status == "pending"
    legs = [Leg("partial", 5, 107.0, order_id="a", exit_reason="not_filled"),
            Leg("target", 5, 115.0, order_id="b", exit_reason="not_filled")]
    _save(settings, _trade(pos, "cancelled", legs, closed_at="2026-10-12"))
    p = _reload(settings, pos.id)
    assert (p.status, p.source, p.broker_status) == ("skipped", "alpaca", "cancelled")
    assert "visszavonva" in p.reason and p.closed_at == "2026-10-12"


def test_submitted_expired_and_open(env):
    settings, _, _, pos = env
    _save(settings, _trade(pos, "submitted", [Leg("target", 10, 115.0, order_id="a")]))
    assert _reload(settings, pos.id).status == "pending"

    _save(settings, _trade(pos, "expired", [Leg("target", 10, 115.0, order_id="a", exit_reason="not_filled")]))
    p = _reload(settings, pos.id)
    assert p.status == "skipped" and "nem teljesult" in p.reason

    legs = [Leg("partial", 5, 107.0, filled_qty=5, entry_fill=100.5, exit_reason="take_profit", exit_price=107.0),
            Leg("target", 5, 115.0, filled_qty=5, entry_fill=100.5)]
    _save(settings, _trade(pos, "open", legs, entry_price=100.5, filled_at="2026-10-12", breakeven_done=True))
    p = _reload(settings, pos.id)
    state = p.position_state()
    assert p.status == "open" and p.entry_price == 100.5 and p.shares == 10
    assert state.partial_done and state.stop == 100.5              # nullszaldo: stop a belepon
    assert p.broker_partial == 107.0


def test_closed_result_in_r(env):
    settings, _, _, pos = env
    legs = [Leg("partial", 5, 107.0, filled_qty=5, entry_fill=101.0, exit_reason="take_profit", exit_price=107.0),
            Leg("target", 5, 115.0, filled_qty=5, entry_fill=101.0, exit_reason="stop", exit_price=101.0)]
    _save(settings, _trade(pos, "closed", legs, entry_price=101.0, filled_at="2026-10-12", closed_at="2026-10-15",
                           breakeven_done=True, realised_pnl=30.0))
    p = _reload(settings, pos.id)
    # kockazat 6 $/db * 10 db = 60 $, eredmeny 30 $ -> +0.5R (a fele +1R, a masik fele nullszaldo)
    assert (p.status, p.reason, p.result_r) == ("closed", "breakeven", 0.5)
    tracker = positions.PositionTracker(settings, read_only=True)
    assert tracker.closed_stats()["n"] == 1


def test_virtual_stepping_skips_alpaca_positions(env):
    settings, df, signal, pos = env
    _save(settings, _trade(pos, "cancelled", [Leg("target", 10, 115.0, exit_reason="not_filled")]))
    tracker = positions.PositionTracker(settings)
    e = signal.price * 1.002
    later = _future(df, [(e, e * 1.003, e * 0.998, e * 1.001)])   # virtualisan belepes lenne
    assert tracker.update("TEST", later) == []
    assert tracker.positions[pos.id].status == "skipped"


def test_virtual_tracking_without_paper_trade(env):
    settings, df, signal, pos = env
    tracker = positions.PositionTracker(settings)
    e = signal.price * 1.002
    later = _future(df, [(e, e * 1.003, e * 0.998, e * 1.001)])
    assert [ev for _, ev in tracker.update("TEST", later)] == ["pos_entered"]
    assert tracker.positions[pos.id].source == "virtual"


def test_book_lock_serialises_writers(env):
    settings = env[0]
    order = []

    def writer(name, hold):
        with broker.book_lock(settings, timeout=10):
            order.append(f"{name}+")
            time.sleep(hold)
            order.append(f"{name}-")

    first = threading.Thread(target=writer, args=("a", 0.3))
    first.start()
    time.sleep(0.05)
    writer("b", 0)
    first.join()
    assert order == ["a+", "a-", "b+", "b-"]                   # nincs atfedes


def test_book_save_is_atomic(env):
    settings = env[0]
    _save(settings, _trade(env[3], "submitted", [Leg("target", 10, 115.0)]))
    assert not settings.broker_path.with_name("broker.json.tmp").exists()
    assert BrokerBook(settings).trades
