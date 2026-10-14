"""A Visszahuzas (RSI2) motor: indikatorok, teljesules, kilepes, koltseg, portfolio - jovobe latas nelkul."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import indicators, market, meanrev
from stockwatch import meanrev_research as mr
from stockwatch.config import PullbackParams, Settings

P = PullbackParams(cost_bps=0.0)


# ---------------------------------------------------------------------------
# Indikatorok
# ---------------------------------------------------------------------------
def test_streak_and_percent_rank():
    close = pd.Series([10, 11, 12, 12, 11, 10, 9, 10], dtype=float)
    assert indicators.streak(close).tolist() == [0, 1, 2, 0, -1, -2, -3, 1]
    s = pd.Series([1, 2, 3, 4, 2.5], dtype=float)
    rank = indicators.percent_rank(s, 4)
    assert np.isnan(rank.iloc[3]) and rank.iloc[4] == pytest.approx(50.0)     # 2.5 > 1, 2 (a 4 elozobol)


def test_connors_rsi_bounds_and_no_lookahead():
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    full = indicators.connors_rsi(close)
    cut = indicators.connors_rsi(close.iloc[:300])
    assert full.dropna().between(0, 100).all()
    assert full.iloc[299] == pytest.approx(cut.iloc[-1])                       # a jovo nem valtoztat


def _frame(rows, atr=2.0, rsi2=1.0, natr=2.0):
    """Kezzel megadott gyertyak (o, h, l, c) a jelzes napjatol."""
    idx = pd.bdate_range("2026-01-05", periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["atr"], df["rsi2"], df["natr"] = atr, rsi2, natr
    return df


def _run(rows, exits=None, params=P):
    feat = _frame(rows)
    flags = np.zeros(len(feat), dtype=bool) if exits is None else np.array(exits, dtype=bool)
    return meanrev.simulate_trade("T", feat, flags, 0, params)


# jelzes: zaro 100, ATR 2 -> limit 95, stop 95 - 3*2 = 89
SIG = (100, 101, 99, 100)


def test_limit_fill_rules():
    assert _run([SIG, (97, 98, 95.5, 97)]).status == "unfilled"                 # a low nem ment a limit ala
    t = _run([SIG, (97, 98, 94, 96), (96, 97, 95, 96)])
    assert (t.status, t.entry_price) == ("open", 95)                              # min(nyito, limit)
    t = _run([SIG, (93, 94, 92, 93.5), (94, 95, 93, 94)])
    assert t.entry_price == 93                                                    # a nyito mar a limit alatt
    assert _run([SIG]).status == "pending"                                        # a limit holnapra szol


def test_stops_are_pessimistic():
    t = _run([SIG, (88, 90, 87, 89)])
    assert (t.reason, t.exit_price) == ("gap_stop", 88)                          # nyito a stop alatt
    t = _run([SIG, (96, 97, 88.5, 96)])
    assert (t.reason, t.exit_price) == ("stop", 89)                              # a belepes napjan
    # ugyanazon a gyertyan stop es kilepesi jel: a stop szamit
    t = _run([SIG, (96, 97, 94, 96), (96, 99, 88, 98)], exits=[0, 0, 1])
    assert t.reason == "stop"


def test_exit_next_open_after_signal_and_time_stop():
    t = _run([SIG, (96, 97, 94, 96), (96, 99, 95, 98), (99, 100, 98, 99)], exits=[0, 0, 1, 0])
    assert (t.reason, t.exit_price, t.held) == ("signal", 99, 2)                 # a jel UTANI nyitason
    rows = [SIG, (96, 97, 94, 96)] + [(96, 97, 95, 96)] * 12
    t = _run(rows, params=P.model_copy(update={"time_stop": 3}))
    assert (t.reason, t.held, t.exit_date) == ("time", 3, _frame(rows).index[4])


def test_costs_turn_a_tiny_gain_into_a_loss():
    gross, net = meanrev.net_return(100.0, 100.05, 5.0)
    assert gross > 0 > net


# ---------------------------------------------------------------------------
# Jelzes (eles) = kutatas
# ---------------------------------------------------------------------------
def _uptrend_with_drop(n=320, drop_days=3):
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = np.linspace(50, 120, n)
    close[-drop_days:] = close[-drop_days - 1] * np.array([0.97, 0.94, 0.91][:drop_days])
    df = pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995, "close": close,
                       "volume": 2_000_000}, index=idx)
    return df


def test_live_signal_matches_research_rules():
    df = _uptrend_with_drop()
    settings = Settings()
    regime = market.Regime(True, None, None, "teszt", "Piac (QQQ)")
    sig = meanrev.evaluate("ABC", df, settings, P, regime)
    assert sig is not None and sig.kind == "pullback" and sig.strategy == "pullback" and sig.passed
    feat = meanrev.features(df, P)
    limit, stop = meanrev.order_levels(float(feat["close"].iloc[-1]), float(feat["atr"].iloc[-1]), P)
    assert (sig.plan.entry_limit, sig.plan.stop) == (pytest.approx(limit), pytest.approx(stop))
    mask = meanrev.setup_mask(feat, P, pd.Series(True, index=feat.index))
    assert bool(mask.iloc[-1])                                                    # ugyanaz a feltetel
    # a sandbox azonos jelzese mas azonositot kap
    other = meanrev.evaluate("ABC", df, settings, P, regime, strategy="sandbox")
    assert other.signal_id != sig.signal_id
    # gyenge rezsim: jelolt marad (naploba kerul), de nem megy at
    weak = meanrev.evaluate("ABC", df, settings, P, market.Regime(False, None, None, "gyenge", "Piac (QQQ)"))
    assert weak is not None and not weak.passed
    # nincs tuladottsag -> nincs jelolt
    assert meanrev.evaluate("ABC", df.iloc[:-3], settings, P, regime) is None


def test_features_have_no_lookahead():
    df = _uptrend_with_drop()
    full = meanrev.features(df, P)
    part = meanrev.features(df.iloc[:300], P)
    pd.testing.assert_series_equal(full.iloc[299], part.iloc[-1], check_names=False)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
def _trade(sym, day, rsi2, status="closed", exit_day=None):
    t = meanrev.Trade(sym, pd.Timestamp(day), 95, 89, rsi2, 2.0, status=status)
    if status == "closed":
        t.exit_date, t.net_pct = pd.Timestamp(exit_day), 1.0
    return t


def test_portfolio_cap_rank_and_slots():
    p = P.model_copy(update={"max_signals_per_day": 2, "max_open": 2})
    cands = [_trade("A", "2026-01-05", 1.5, exit_day="2026-01-09"), _trade("B", "2026-01-05", 0.5, exit_day="2026-01-07"),
             _trade("C", "2026-01-05", 1.0, exit_day="2026-01-08"),
             _trade("D", "2026-01-06", 0.1, exit_day="2026-01-12"),      # nincs hely (B es C meg bent)
             _trade("E", "2026-01-08", 0.2, exit_day="2026-01-12")]      # C aznap nyitaskor zart -> van hely
    chosen, meta = mr.portfolio(cands, p)
    assert [t.symbol for t in chosen] == ["B", "C", "E"]                  # legalacsonyabb RSI(2) elol
    assert meta["capped_days"] == 2


def test_unfilled_order_frees_slot_next_day():
    p = P.model_copy(update={"max_signals_per_day": 1, "max_open": 1})
    cands = [_trade("A", "2026-01-05", 1.0, status="unfilled"), _trade("B", "2026-01-06", 1.0, exit_day="2026-01-08")]
    assert [t.symbol for t in mr.portfolio(cands, p)[0]] == ["A", "B"]


def test_gate_and_report_render():
    t = _trade("A", "2021-03-01", 1.0, exit_day="2021-03-03")
    res = mr.split([t], P, pd.Timestamp("2017-01-02"), pd.Timestamp("2026-10-01"), holdout=False)
    out = mr.gate(res, None)
    assert out["passed"] is False and res["train"]["n"] == 1
    text = mr.report_markdown({"params": P.model_dump(), "symbols": 1, "main": res, "gate": out,
                               "cost_2x": res["visible"]})
    assert "Elfogadasi feltetel" in text
