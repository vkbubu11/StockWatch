"""A paper vegrehajtas es a backtest egyezese: nyitaskori belepes (kitores), nyitoaukcios kilepes
(visszahuzas), beragadt kilepo megbizas, helyfoglalas es az eles kapu - halozat nelkul."""
from __future__ import annotations

import pandas as pd
import pytest

from stockwatch import broker, meanrev
from tests.test_broker import FakeAlpaca, env, submit  # noqa: F401  (fixture)
from tests.test_meanrev_live import P, REGIME, FakeOto, extend, uptrend_with_drop
from tests.test_ledger import journal_dir  # noqa: F401  (fixture)
from tests.test_meanrev_live import env as mr_env  # noqa: F401  (fixture)


def open_clock(fake: FakeAlpaca, at: str) -> None:
    """A piac nyitva, az ora New York-i idoben `at` (pl. "2023-03-02 09:36")."""
    fake.is_open = True
    stamp = pd.Timestamp(at, tz=broker.MARKET_TZ)
    fake.clock = lambda: {"is_open": True, "timestamp": stamp.isoformat(),
                          "next_close": (stamp.normalize() + pd.Timedelta(hours=16)).isoformat()}


def submitted_evening_before(trade) -> None:
    trade.submitted_at = pd.Timestamp("2023-03-01 16:20", tz=broker.MARKET_TZ).tz_convert("UTC").isoformat()


# ---------------------------------------------------------------------------
# Kitores: a belepo csak a nyitaskor teljesulhet
# ---------------------------------------------------------------------------
def test_breakout_entry_cancelled_when_open_was_above_limit(env):  # noqa: F811
    settings, df, fake, book = env
    trade = submit(env)
    submitted_evening_before(trade)
    open_clock(fake, "2023-03-02 09:33")
    assert broker.sync(settings, fake, book) == []                       # meg az ablakon belul
    open_clock(fake, "2023-03-02 09:36")
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_expired"] and trade.status == "expired"
    assert all(fake.orders_by_id[leg.order_id]["status"] == "canceled" for leg in trade.leg_objs())


def test_breakout_entry_filled_at_open_is_kept(env):  # noqa: F811
    settings, df, fake, book = env
    trade = submit(env)
    submitted_evening_before(trade)
    for leg in trade.leg_objs():
        fake.fill(leg.order_id, trade.entry_limit - 0.2, day="2023-03-02")
    open_clock(fake, "2023-03-02 09:45")
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_filled"] and trade.status == "open"


def test_entry_filled_during_cancel_is_not_expired(env):  # noqa: F811
    settings, df, fake, book = env
    trade = submit(env)
    submitted_evening_before(trade)
    open_clock(fake, "2023-03-02 09:36")
    original = fake.cancel_order

    def fill_instead(order_id):                       # a megbizas a visszavonas elott teljesult
        fake.fill(order_id, trade.entry_limit, day="2023-03-02")
        original(order_id)

    fake.cancel_order = fill_instead
    assert broker.sync(settings, fake, book) == [] and trade.status == "submitted"
    fake.cancel_order = original
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_filled"] and trade.status == "open"


def test_manual_intraday_submit_and_disabled_window_not_cancelled(env):  # noqa: F811
    settings, df, fake, book = env
    trade = submit(env)
    trade.submitted_at = pd.Timestamp("2023-03-02 11:00", tz=broker.MARKET_TZ).tz_convert("UTC").isoformat()
    open_clock(fake, "2023-03-02 11:30")
    assert broker.sync(settings, fake, book) == [] and trade.status == "submitted"
    submitted_evening_before(trade)
    settings.broker.entry_window_min = 0
    assert broker.sync(settings, fake, book) == [] and trade.status == "submitted"


# ---------------------------------------------------------------------------
# Visszahuzas: kilepes a nyitoaukcion, beragadt kilepo megbizas
# ---------------------------------------------------------------------------
def _pullback_with_exit_signal(settings, monkeypatch):
    df = uptrend_with_drop()
    sig = meanrev.evaluate("ABC", df, settings, P, REGIME)
    fake, book = FakeOto(), broker.BrokerBook(settings)
    trade = broker.submit_pullback(sig, settings, fake, book, P)
    leg = trade.leg_objs()[0]
    limit = sig.plan.entry_limit
    fake.fill(leg.order_id, limit, day=f"{df.index[-1] + pd.offsets.BDay(1):%Y-%m-%d}")
    bars = extend(df, [(limit, limit * 1.01, limit * 0.99, limit)])
    for k in range(1, 4):
        c = limit * (1 + 0.04 * k)
        bars = extend(bars, [(c, c * 1.01, c * 0.99, c)])
    monkeypatch.setattr(broker.store, "load", lambda symbol, s=None, universe=False: bars)
    monkeypatch.setattr(broker, "_today", lambda: bars.index[-1])
    broker.sync(settings, fake, book, final_bar=True)
    assert trade.exit_pending == "signal"
    return fake, book, trade, limit


def test_pullback_exit_goes_to_opening_auction(mr_env, monkeypatch):  # noqa: F811
    fake, book, trade, limit = _pullback_with_exit_signal(mr_env, monkeypatch)
    events = [e for _, e in broker.preopen(mr_env, fake, book)]
    assert events == ["broker_exit"]
    sell = fake.submitted[-1]
    assert sell["side"] == "sell" and sell["type"] == "market" and sell["time_in_force"] == "opg"
    leg = trade.leg_objs()[0]
    assert fake.orders_by_id[leg.sl_id]["status"] == "canceled"
    # nyitas utan nem ad el meg egyszer; a teljesules utan lezarul
    fake.is_open = True
    assert [e for _, e in broker.sync(mr_env, fake, book)] == []
    assert sum(1 for b in fake.submitted if b["side"] == "sell") == 1
    fake.fill(leg.exit_order_id, limit * 1.1)
    assert "broker_closed" in [e for _, e in broker.sync(mr_env, fake, book)]
    assert trade.status == "closed" and trade.realised_pnl > 0


def test_preopen_does_nothing_while_market_open(mr_env, monkeypatch):  # noqa: F811
    fake, book, trade, _ = _pullback_with_exit_signal(mr_env, monkeypatch)
    fake.is_open = True
    assert broker.preopen(mr_env, fake, book) == []


def test_rejected_opg_falls_back_to_market_at_open(mr_env, monkeypatch):  # noqa: F811
    fake, book, trade, _ = _pullback_with_exit_signal(mr_env, monkeypatch)
    original = fake.submit_order

    def reject_opg(body):
        if body.get("time_in_force") == "opg":
            raise broker.BrokerError("Alpaca 422: opg orders not accepted now")
        return original(body)

    fake.submit_order = reject_opg
    assert broker.preopen(mr_env, fake, book) == []
    fake.is_open = True
    assert [e for _, e in broker.sync(mr_env, fake, book)] == ["broker_exit"]
    assert fake.submitted[-1]["time_in_force"] == "day"


def test_dead_exit_order_is_resubmitted(mr_env, monkeypatch):  # noqa: F811
    fake, book, trade, limit = _pullback_with_exit_signal(mr_env, monkeypatch)
    broker.preopen(mr_env, fake, book)
    leg = trade.leg_objs()[0]
    fake.orders_by_id[leg.exit_order_id]["status"] = "canceled"      # a nyitoaukcion nem teljesult
    fake.is_open = True
    events = [e for _, e in broker.sync(mr_env, fake, book)]
    assert events == ["broker_exit_retry"]
    new = trade.leg_objs()[0]
    assert new.exit_order_id != leg.exit_order_id and fake.submitted[-1]["time_in_force"] == "day"
    fake.fill(new.exit_order_id, limit * 1.05)
    assert "broker_closed" in [e for _, e in broker.sync(mr_env, fake, book)]
    assert trade.leg_objs()[0].exit_reason == "signal"


# ---------------------------------------------------------------------------
# Helyfoglalas es eles kapu
# ---------------------------------------------------------------------------
def test_pending_pullback_orders_occupy_slots(mr_env):  # noqa: F811
    """A fuggo limit is helyet foglal: a korlatnal tobb megbizas nem megy be, igy esesnapon sem
    nyilhat a max.-nal tobb pozicio, ha minden limit egyszerre teljesul."""
    mr_env.broker.max_open_trades = 2
    fake, book = FakeOto(), broker.BrokerBook(mr_env)
    sigs = [meanrev.evaluate(symbol, uptrend_with_drop(symbol_seed=k), mr_env, P, REGIME)
            for k, symbol in enumerate(("AAA", "BBB", "CCC"))]
    trades = [broker.submit_pullback(sig, mr_env, fake, book, P) for sig in sigs[:2]]
    assert all(t.status == "submitted" for t in trades) and broker.occupied_slots(book) == trades
    with pytest.raises(broker.NoRoom):
        broker.submit_pullback(sigs[2], mr_env, fake, book, P)
    assert len(book.active()) == 2


def _closed(book, n_win, n_loss, strategy, win_pnl=100.0, loss_pnl=-80.0):
    for k in range(n_win + n_loss):
        pnl = win_pnl if k < n_win else loss_pnl
        book.trades[f"{strategy}-{k}"] = broker.BrokerTrade(
            id=f"{strategy}-{k}", symbol=f"S{k}", pattern="x", kind="pullback", signal_date="2026-01-02",
            entry_limit=100, stop=90, target=110, partial_price=None, shares=10, horizon=10,
            exit_mode="indicator", status="closed", entry_price=100.0, realised_pnl=pnl, strategy=strategy)


def test_live_gate_per_strategy(mr_env):  # noqa: F811
    book = broker.BrokerBook(mr_env)
    _closed(book, 40, 12, "pullback")              # 77%, PF 4000/960
    _closed(book, 30, 25, "breakout")              # 55%
    res = broker.readiness(book, mr_env)
    assert res["pullback"]["ready"] and res["pullback"]["n"] == 52
    assert res["pullback"]["win_lo"] < res["pullback"]["win"] < res["pullback"]["win_hi"]
    assert not res["breakout"]["ready"]
    assert any("ATMENT" in line for line in broker.readiness_lines(book, mr_env))


def test_live_gate_needs_enough_trades_and_profit(mr_env):  # noqa: F811
    book = broker.BrokerBook(mr_env)
    _closed(book, 20, 4, "pullback")               # 83%, de keves
    assert not broker.readiness(book, mr_env)["pullback"]["ready"]
    book.trades.clear()
    _closed(book, 45, 10, "pullback", win_pnl=10.0, loss_pnl=-100.0)   # 82% nyero, de veszteseges
    r = broker.readiness(book, mr_env)["pullback"]
    assert not r["ready"] and r["pf"] < 1


def test_entry_window_setting_validated():
    from stockwatch.config import BrokerSettings

    with pytest.raises(ValueError):
        BrokerSettings(entry_window_min=-1)
    with pytest.raises(ValueError):
        BrokerSettings(live_gate_win=1.5)


def test_fill_times_are_recorded_and_shown(mr_env, monkeypatch):  # noqa: F811
    df = uptrend_with_drop()
    sig = meanrev.evaluate("ABC", df, mr_env, P, REGIME)
    fake, book = FakeOto(), broker.BrokerBook(mr_env)
    trade = broker.submit_pullback(sig, mr_env, fake, book, P)
    monkeypatch.setattr(broker.store, "load", lambda symbol, s=None, universe=False: df)
    fake.fill(trade.leg_objs()[0].order_id, sig.plan.entry_limit, day="2024-03-04")
    broker.sync(mr_env, fake, book)
    assert trade.status == "open" and trade.filled_at == "2024-03-04"
    assert trade.filled_ts == "2024-03-04T15:00:00+00:00"
    _, body = broker.format_event(trade, "broker_filled")
    assert "2024-03-04 16:00 (New York 10:00)" in body and "Bekuldve:" in body
