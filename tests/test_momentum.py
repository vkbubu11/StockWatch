"""Momentum (12-1) rotacio: pontszam, rebalansz-dontes, naptar, kotes-lejatszas, kovetes, paper, kutatas."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import broker, momentum, momentum_research, positions
from stockwatch.config import MomentumParams, Settings
from tests.test_broker import FakeAlpaca


def bars(closes, start="2022-01-03") -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes), name="date")
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.005,
                         "low": np.minimum(opens, closes) * 0.995, "close": closes, "volume": 1e6}, index=idx)


def trend(drift: float, n: int = 400, start: float = 50.0) -> np.ndarray:
    return start * np.cumprod(np.full(n, 1 + drift))


# ---------------------------------------------------------------------------
# Pontszam es dontes
# ---------------------------------------------------------------------------
def test_score_skips_last_month_and_has_no_lookahead():
    params = MomentumParams()
    closes = trend(0.001, 300)
    df = bars(closes)
    expected = closes[-1 - params.skip] / closes[-1 - params.lookback] - 1
    assert momentum.score_one(df, params) == pytest.approx(expected)
    # az utolso honap zuhanasa NEM szamit bele (rovid tavu visszafordulas)
    crash = closes.copy()
    crash[-params.skip:] *= 0.5
    assert momentum.score_one(bars(crash), params) == pytest.approx(expected)
    # rovid historia / olcso / illikvid: nem jogosult
    assert momentum.score_one(bars(closes[:200]), params) is None
    assert momentum.score_one(bars(trend(0.001, 300, start=2.0)), params) is None
    thin = df.assign(volume=10.0)
    assert momentum.score_one(thin, params) is None


def test_decide_keeps_within_buffer_and_fills_slots():
    params = MomentumParams(top_n=3, buffer=5)
    ranked = ["A", "B", "C", "D", "E", "F", "G"]
    d = momentum.decide(ranked, held=["E", "G", "B"], params=params)
    assert d.keep == ["E", "B"]                 # E az 5. (pufferen belul), B a 2.
    assert d.sell == ["G"]                      # G a 7. - kiesett
    assert d.buy == ["A"]                       # egy hely maradt: a legjobb, meg nem tartott
    assert momentum.decide(ranked, [], params).buy == ["A", "B", "C"]
    assert momentum.ranking({"X": 0.1, "Y": 0.3, "Z": float("nan"), "W": 0.3}) == ["W", "Y", "X"]


def test_params_validation():
    with pytest.raises(ValueError):
        MomentumParams(top_n=10, buffer=5)
    with pytest.raises(ValueError):
        MomentumParams(lookback=100, skip=90)


def test_rebalance_day_is_last_session_of_month():
    assert momentum.is_rebalance_day(pd.Timestamp("2025-10-31"))       # pentek, honap vege
    assert not momentum.is_rebalance_day(pd.Timestamp("2025-10-30"))
    assert momentum.is_rebalance_day(pd.Timestamp("2025-05-30"))       # pentek; 31-e szombat
    assert not momentum.is_rebalance_day(pd.Timestamp("2025-05-31"))   # hetvege
    idx = pd.bdate_range("2024-01-01", "2024-03-31")
    assert list(momentum.rebalance_days(idx).strftime("%m-%d")) == ["01-31", "02-29", "03-29"]


# ---------------------------------------------------------------------------
# Kotes lejatszasa es kovetese
# ---------------------------------------------------------------------------
def test_simulate_trade_enters_next_open_exits_after_rebalance():
    df = bars(trend(0.002, 60))
    params = MomentumParams()
    i = 10
    t = momentum.simulate_trade("X", df, i, params)
    assert t.status == "open" and t.entry_date == df.index[i + 1]
    assert t.entry_price == pytest.approx(df["open"].iloc[i + 1])
    exit_day = df.index[30]
    t = momentum.simulate_trade("X", df, i, params, exit_signal=exit_day)
    assert t.status == "closed" and t.reason == "rotation" and t.exit_date == df.index[31]
    assert t.exit_price == pytest.approx(df["open"].iloc[31])
    assert t.net_pct < t.gross_pct                               # koltseg


def test_stop_only_when_enabled():
    closes = np.r_[trend(0.0, 20, 100.0), np.full(10, 70.0)]
    df = bars(closes)
    assert momentum.simulate_trade("X", df, 5, MomentumParams()).status == "open"
    t = momentum.simulate_trade("X", df, 5, MomentumParams(stop_pct=20))
    assert t.status == "closed" and t.reason in ("stop", "gap_stop") and t.exit_price <= 80.0 + 1e-9


def test_tracker_follows_momentum_position(tmp_path, monkeypatch):
    settings = Settings()
    monkeypatch.setattr(type(settings), "positions_path", property(lambda self: tmp_path / "positions.json"))
    params = MomentumParams()
    df = bars(trend(0.002, 300))
    signal_df = df.iloc[:280]
    sig = momentum.make_signal("MOMO", signal_df, settings, params, rank=2, score=0.4, universe=500)
    assert sig.kind == "momentum" and sig.strategy == "momentum" and sig.passed
    assert sig.plan.shares == int(settings.entry.account_size / params.top_n // signal_df["close"].iloc[-1])

    tracker = positions.PositionTracker(settings)
    tracker.register(sig, params)
    events = tracker.update("MOMO", df.iloc[:285], strategy="momentum")
    assert [e for _, e in events] == ["pos_entered"]
    day = df.index[290]
    assert [e for _, e in tracker.mark_momentum_exit("MOMO", day)] == ["pos_exit_signal"]
    assert tracker.mark_momentum_exit("MOMO", day) == []                 # idempotens
    events = tracker.update("MOMO", df.iloc[:295], strategy="momentum")
    pos = events[-1][0]
    assert events[-1][1] == "pos_exit" and pos.status == "closed" and pos.reason == "rotation"
    assert pos.closed_at == f"{df.index[291]:%Y-%m-%d}" and pos.result_pct > 0
    title, body = positions.format_event(pos, "pos_exit")
    assert "ConnorsRSI" not in body


# ---------------------------------------------------------------------------
# Paper
# ---------------------------------------------------------------------------
def test_submit_momentum_market_order_equal_weight(tmp_path, monkeypatch):
    settings = Settings()
    monkeypatch.setattr(type(settings), "broker_path", property(lambda self: tmp_path / "broker.json"))
    params = MomentumParams()
    client = FakeAlpaca(equity=100_000)
    book = broker.BrokerBook(settings)
    df = bars(trend(0.002, 300))
    sig = momentum.make_signal("MOMO", df, settings, params, rank=1, score=0.5, universe=500)
    trade = broker.submit_momentum(sig, settings, client, book, params)
    order = client.submitted[-1]
    assert order["type"] == "market" and order["time_in_force"] == "day" and "stop_loss" not in order
    assert int(order["qty"]) == int(10_000 // df["close"].iloc[-1])
    assert trade.strategy == "momentum" and trade.exit_mode == "indicator"
    with pytest.raises(broker.BrokerError):
        broker.submit_momentum(sig, settings, client, book, params)    # duplikatum

    trade.status = "open"
    marked = broker.mark_momentum_exits(book, ["MOMO"], "2023-01-31")
    assert marked == [trade] and trade.exit_pending == "rotation"
    assert broker.pullback_exit_due(trade, df) == ""                   # a CRSI-kilepes nem vonatkozik ra


def test_submit_momentum_respects_portfolio_size(tmp_path, monkeypatch):
    settings = Settings()
    monkeypatch.setattr(type(settings), "broker_path", property(lambda self: tmp_path / "broker.json"))
    params = MomentumParams(top_n=2, buffer=4)
    client, book = FakeAlpaca(), broker.BrokerBook(settings)
    df = bars(trend(0.002, 300))
    for sym in ("AAA", "BBB"):
        broker.submit_momentum(momentum.make_signal(sym, df, settings, params, 1, 0.3, 10), settings, client, book, params)
    with pytest.raises(broker.NoRoom):
        broker.submit_momentum(momentum.make_signal("CCC", df, settings, params, 2, 0.2, 10), settings, client, book, params)


# ---------------------------------------------------------------------------
# Kutatas
# ---------------------------------------------------------------------------
def test_research_picks_strongest_and_trades_next_open():
    rng = np.random.default_rng(3)
    n = 400
    drifts = {"UP1": 0.003, "UP2": 0.0025, "FLAT": 0.0, "DOWN": -0.002}
    frames = {s: bars(trend(d, n) * np.exp(rng.normal(0, 0.002, n).cumsum())) for s, d in drifts.items()}
    panel = {c: pd.DataFrame({s: f[c] for s, f in frames.items()}) for c in ("open", "low", "close", "volume")}
    params = MomentumParams(top_n=2, buffer=2, min_dollar_volume=0)
    res = momentum_research.simulate(panel, params)
    bought = {t["symbol"] for t in res["trades"]} | {t["symbol"] for t in res["open"]}
    assert bought <= {"UP1", "UP2"}
    first = min(res["open"] + res["trades"], key=lambda t: t["entry_date"])
    reb = momentum.rebalance_days(panel["close"].index)
    assert first["entry_date"] > reb[reb >= panel["close"].index[params.lookback]][0]   # a rebalansz UTANI nyitas
    assert res["returns"].sum() > res["baseline"].sum()
