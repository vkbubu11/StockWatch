"""A Visszahuzas (RSI2) elesben: monitor-kor, virtualis kovetes, ujraellenorzes, paper (OTO) - halozat nelkul."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stockwatch import broker, config, ledger, market, meanrev, monitor, positions, replay
from stockwatch.config import PullbackParams, Settings, StrategiesSettings
from tests.test_broker import FakeAlpaca
from tests.test_ledger import journal_dir  # noqa: F401  (fixture)

P = PullbackParams(earnings_blackout_days=0, cost_bps=5.0)


def uptrend_with_drop(n=320, symbol_seed=0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = np.linspace(50, 120, n) * (1 + 0.001 * symbol_seed)
    close[-3:] = close[-4] * np.array([0.97, 0.94, 0.91])
    return pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995, "close": close,
                         "volume": 2_000_000.0}, index=idx)


def extend(df, rows):
    """Uj gyertyak (o, h, l, c) a sor vegere."""
    idx = pd.bdate_range(df.index[-1] + pd.offsets.BDay(1), periods=len(rows))
    new = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    new["volume"] = 2_000_000.0
    return pd.concat([df, new])


REGIME = market.Regime(True, None, None, "teszt", "Piac (QQQ)")


@pytest.fixture()
def env(tmp_path, monkeypatch, journal_dir):  # noqa: F811
    settings = Settings()
    cls = type(settings)
    for prop, name in (("positions_path", "pos.json"), ("broker_path", "broker.json"),
                       ("signals_path", "signals.jsonl"), ("entry_state_path", "entry_state.json"),
                       ("holdings_path", "holdings.json")):
        monkeypatch.setattr(cls, prop, property(lambda self, n=name: tmp_path / n))
    return settings


# ---------------------------------------------------------------------------
# Virtualis kovetes
# ---------------------------------------------------------------------------
def test_tracked_pullback_lifecycle(env):
    df = uptrend_with_drop()
    sig = meanrev.evaluate("ABC", df, env, P, REGIME)
    tracker = positions.PositionTracker(env)
    pos = tracker.register(sig, P)
    assert pos.exit_mode == "indicator" and pos.params["exit_crsi"] == P.exit_crsi

    limit = sig.plan.entry_limit
    day1 = extend(df, [(limit * 1.01, limit * 1.02, limit * 0.99, limit * 1.01)])        # limit teljesul
    assert [e for _, e in tracker.update("ABC", day1)] == ["pos_entered"]
    assert pos.status == "open" and pos.entry_price == pytest.approx(limit)
    assert tracker.update("ABC", day1) == []                                              # idempotens

    up = day1
    events = []
    for k in range(1, 6):                                                                 # eros visszapattanas
        c = limit * (1 + 0.03 * k)
        up = extend(up, [(c * 0.99, c * 1.01, c * 0.98, c)])
        events += [e for _, e in tracker.update("ABC", up)]
        if pos.status == "closed":
            break
    assert events[0] == "pos_exit_signal" and events[-1] == "pos_exit"
    assert pos.reason == "signal" and pos.result_pct > 0 and pos.result_r > 0
    title, body = positions.format_event(pos, "pos_exit")
    assert "netto" in body and "Visszahuzas" in title


def test_unfilled_limit_is_skipped(env):
    df = uptrend_with_drop()
    sig = meanrev.evaluate("ABC", df, env, P, REGIME)
    tracker = positions.PositionTracker(env)
    pos = tracker.register(sig, P)
    limit = sig.plan.entry_limit
    day1 = extend(df, [(limit * 1.03, limit * 1.04, limit * 1.01, limit * 1.03)])
    assert [e for _, e in tracker.update("ABC", day1)] == ["pos_skipped"]
    assert pos.status == "skipped" and "nem teljesult" in pos.reason


# ---------------------------------------------------------------------------
# Monitor-kor: rangsor, napi limit, naplo, nincs broker
# ---------------------------------------------------------------------------
def _cycle_env(env, monkeypatch, frames, paper="breakout"):
    monkeypatch.setattr(monitor, "pullback_symbols", lambda s, allow_network=True: list(frames))
    monkeypatch.setattr("stockwatch.store.load", lambda symbol, s=None, universe=False: frames[symbol])
    ctx = market.MarketContext({"QQQ": frames["A"], "^GSPC": frames["A"]}, frozenset(frames), frozenset())
    monkeypatch.setattr(monitor.market, "load_context", lambda *a, **k: ctx)
    monkeypatch.setattr(monitor, "_pullback_chart", lambda *a, **k: None)
    submitted = []
    monkeypatch.setattr(broker, "submit_pullback", lambda *a, **k: submitted.append(a[0].symbol) or (_ for _ in ()).throw(broker.BrokerError("teszt")))
    monkeypatch.setattr(broker, "submit", lambda *a, **k: submitted.append("bracket"))
    live = env.model_copy(update={"broker": env.broker.model_copy(update={"auto_trade": True}),
                                  "strategies": StrategiesSettings(paper=paper)})
    monkeypatch.setattr(monitor, "_fresh_settings", lambda: live)
    monkeypatch.setattr(broker.AlpacaClient, "from_env", classmethod(lambda cls: FakeAlpaca()))
    return live, submitted


def test_cycle_ranks_caps_and_journals(env, monkeypatch):
    frames = {s: uptrend_with_drop(symbol_seed=i) for i, s in enumerate("ABCD")}
    frames["D"] = uptrend_with_drop().iloc[:-3]                       # nincs tuladottsag
    live, submitted = _cycle_env(env, monkeypatch, frames)
    sent = []
    dispatcher = SimpleNamespace(dispatch=sent.append, dry_run=False, settings=live)
    tracker, state = positions.PositionTracker(live), monitor.entry.EntryState(live)
    params = P.model_copy(update={"max_signals_per_day": 2})
    out = monitor.pullback_cycle(live, "pullback", params, dispatcher, tracker, state, refresh=False)
    assert out["candidates"] == 3 and len(out["alerted"]) == 2
    assert [a.event for a in sent] == ["entry_pullback"] * 2 and all(a.strategy == "pullback" for a in sent)
    assert submitted == []                                            # a paper fiok a kitoreshez kotott
    decisions = sorted(r["decision"] for r in ledger.iter_records())
    assert decisions == ["alerted", "alerted", "skipped"]
    assert all(r["strategy"] == "pullback" and r["regime"] == "QQQ" for r in ledger.iter_records())
    assert len([p for p in tracker.active() if p.strategy == "pullback"]) == 2
    # masodszor ugyanaz a nap: a papirok mar foglaltak -> nincs uj ertesites
    sent.clear()
    monitor.pullback_cycle(live, "pullback", params, dispatcher, tracker, state, refresh=False)
    assert [a.event for a in sent] == []


def test_cycle_submits_only_when_paper_linked(env, monkeypatch):
    frames = {"A": uptrend_with_drop()}
    live, submitted = _cycle_env(env, monkeypatch, frames, paper="pullback")
    dispatcher = SimpleNamespace(dispatch=lambda a: None, dry_run=False, settings=live)
    monitor.pullback_cycle(live, "sandbox", P, dispatcher, None, None, refresh=False)
    assert submitted == []                                            # a homokozo soha
    monitor.pullback_cycle(live, "pullback", P, dispatcher, None, None, refresh=False)
    assert submitted == ["A"]


# ---------------------------------------------------------------------------
# Ujraellenorzes / visszajatszas
# ---------------------------------------------------------------------------
def test_replay_confirms_pullback_signal(env, monkeypatch):
    df = uptrend_with_drop()
    monkeypatch.setattr(config, "load_settings", lambda: env)
    sig = meanrev.evaluate("ABC", df, env, P.model_copy(update={"earnings_blackout_days": 5}), REGIME)
    ledger.Ledger(env, None).record(sig, "alerted", "ok", raw=df, benchmark=df, snapshot=True, regime_source="QQQ")
    res = replay.replay(sig.signal_id, env)
    assert res["found"] and res["replayed"]["qualifies"] and res["replayed"]["kind"] == "pullback"
    assert res["verdict"].startswith(("PONTOSAN", "EGYEZIK")), res


# ---------------------------------------------------------------------------
# Paper: OTO bekuldes, kilepes nyitaskor
# ---------------------------------------------------------------------------
class FakeOto(FakeAlpaca):
    def submit_order(self, body):
        parent = super().submit_order(body)
        if body.get("order_class") == "oto":
            sl = self._new(symbol=body["symbol"], side="sell", type="stop", qty=body["qty"],
                           stop_price=body["stop_loss"]["stop_price"], parent=parent["id"], status="held")
            self.orders_by_id[parent["id"]]["legs"] = [sl]
            parent["legs"] = [sl]
        return parent


def test_paper_pullback_oto_and_exit_at_open(env, monkeypatch):
    df = uptrend_with_drop()
    sig = meanrev.evaluate("ABC", df, env, P, REGIME)
    fake, book = FakeOto(), broker.BrokerBook(env)
    trade = broker.submit_pullback(sig, env, fake, book, P)
    body = fake.submitted[-1]
    assert body["order_class"] == "oto" and "take_profit" not in body and trade.exit_mode == "indicator"
    leg = trade.leg_objs()[0]
    assert leg.sl_id and not leg.tp_id

    # teljesul, majd a zaron kilepesi jel, aztan nyitaskor piaci eladas
    filled_day = f"{df.index[-1] + pd.offsets.BDay(1):%Y-%m-%d}"
    fake.fill(leg.order_id, sig.plan.entry_limit, day=filled_day)
    limit = sig.plan.entry_limit
    bars = extend(df, [(limit, limit * 1.01, limit * 0.99, limit)])
    for k in range(1, 4):
        c = limit * (1 + 0.04 * k)
        bars = extend(bars, [(c, c * 1.01, c * 0.99, c)])
    monkeypatch.setattr(broker.store, "load", lambda symbol, s=None, universe=False: bars)
    monkeypatch.setattr(broker, "_today", lambda: bars.index[-1])
    events = [e for _, e in broker.sync(env, fake, book, final_bar=True)]
    assert "broker_filled" in events and "broker_exit_signal" in events and trade.exit_pending == "signal"
    fake.is_open = True
    events = [e for _, e in broker.sync(env, fake, book)]
    assert events[0] == "broker_exit"
    sell = fake.submitted[-1]
    assert sell["side"] == "sell" and sell["type"] == "market"
    fake.fill(trade.leg_objs()[0].exit_order_id, limit * 1.10)
    events = [e for _, e in broker.sync(env, fake, book)]
    assert "broker_closed" in events and trade.status == "closed" and trade.realised_pnl > 0
    book.save()
    # a kovetett trade a Jelzett trade-ek oldalon: a paper allapota, netto %-kal
    tracker = positions.PositionTracker(env)
    pos = tracker.positions[trade.id]
    assert pos.source == "alpaca" and pos.status == "closed" and pos.reason == "signal" and pos.result_pct > 0
