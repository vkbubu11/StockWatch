"""A kilepesi modok szimulaciojanak tesztjei (entry_research.simulate_exit)."""
from __future__ import annotations

import pandas as pd
import pytest

from stockwatch.entry_research import SPLIT_DATE, Result, simulate_exit, split_stats, train_whitelist


def bars(rows):
    index = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


# belepo 100, stop 95 (1R = 5), cel 115 (3R)
ENTRY, STOP, TARGET, ATR = 100.0, 95.0, 115.0, 2.0


def run(df, mode, horizon=20, **kw):
    return simulate_exit(df, 0, ENTRY, STOP, TARGET, horizon, ATR, mode, **kw)


def test_target_mode_hits_target():
    df = bars([(100, 104, 99, 103), (103, 116, 102, 115)])
    r, reason, held = run(df, "target")
    assert reason == "target" and r == pytest.approx(3.0) and held == 2


def test_stop_first_when_both_on_same_bar():
    df = bars([(100, 101, 99, 100), (100, 120, 90, 110)])
    r, reason, _ = run(df, "target")
    assert reason == "stop" and r == pytest.approx(-1.0)


def test_gap_below_stop_fills_at_open():
    df = bars([(100, 101, 99, 100), (90, 92, 88, 91)])
    r, reason, _ = run(df, "target")
    assert reason == "stop" and r == pytest.approx(-2.0)


def test_partial_then_breakeven_is_a_win():
    # +1R (105) elerve -> fele eladva, stop a belepon; utana visszaesik 100 ala
    df = bars([(100, 101, 99, 100), (101, 106, 101, 105), (104, 104, 99, 99)])
    r, reason, _ = run(df, "partial")
    assert reason == "breakeven"
    assert r == pytest.approx(0.5)            # 0.5 * 1R + 0.5 * 0R


def test_partial_then_target():
    df = bars([(100, 101, 99, 100), (101, 106, 101, 105), (106, 116, 105, 115)])
    r, reason, _ = run(df, "partial")
    assert reason == "target" and r == pytest.approx(0.5 * 1 + 0.5 * 3)


def test_partial_same_bar_below_entry_is_pessimistic():
    df = bars([(100, 101, 99, 100), (101, 106, 98, 100)])
    r, reason, _ = run(df, "partial")
    assert reason == "breakeven" and r == pytest.approx(0.5)


def test_full_stop_before_partial():
    df = bars([(100, 101, 99, 100), (99, 100, 94, 95)])
    r, reason, _ = run(df, "partial")
    assert reason == "stop" and r == pytest.approx(-1.0)


def test_trailing_stop_follows_price():
    # a legmagasabb ar 120 -> kovetett stop 120 - 3*2 = 114, ott zar
    df = bars([(100, 101, 99, 100), (101, 120, 101, 119), (118, 118, 113, 114)])
    r, reason, _ = run(df, "trail", trail_atr=3.0)
    assert reason == "trail" and r == pytest.approx((114 - 100) / 5)


def test_trail_ignores_fixed_target_and_times_out():
    df = bars([(100, 101, 99, 100)] + [(100 + i, 101 + i, 99.5 + i, 100.5 + i) for i in range(1, 6)])
    r, reason, held = run(df, "trail", horizon=3, trail_atr=3.0)
    assert reason == "timeout" and held == 6       # a kovetett modban 2x horizont
    assert r == pytest.approx((105.5 - 100) / 5)


def test_split_and_whitelist():
    early = [Result("A", "Bull zaszlo", SPLIT_DATE - pd.Timedelta(days=10), "breakout", 1.0, "target", 5)] * 8
    bad = [Result("A", "Dupla alj", SPLIT_DATE - pd.Timedelta(days=10), "breakout", -1.0, "stop", 5)] * 8
    late = [Result("A", "Dupla alj", SPLIT_DATE + pd.Timedelta(days=10), "breakout", 2.0, "target", 5)] * 8
    stats = split_stats(early + bad + late)
    assert stats["train"]["n"] == 16 and stats["test"]["n"] == 8
    # a teszt-idoszak sikere NEM szivarog vissza a mintavalasztasba
    assert train_whitelist(early + bad + late) == {"Bull zaszlo"}


# ---------------------------------------------------------------------------
# Modszertan: konfidencia-intervallum, zarolt holdout, evenkenti bontas
# ---------------------------------------------------------------------------
from stockwatch import entry_research as er  # noqa: E402


def res(date: str, r: float, pattern: str = "Bull zaszlo") -> Result:
    return Result(symbol="T", pattern=pattern, date=pd.Timestamp(date), kind="breakout", r=r,
                  reason="x", held=5)


def test_wilson_interval_matches_known_values():
    lo, hi = er.wilson(33, 49)
    assert lo == pytest.approx(0.534, abs=0.001) and hi == pytest.approx(0.788, abs=0.001)
    assert er.wilson(0, 0) == (None, None)
    assert er.wilson(10, 10)[1] == pytest.approx(1.0)


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    values = [1.0, -1.0, 2.0, 0.5, -1.0, 1.5, -0.5, 3.0]
    lo, hi = er.bootstrap_ci(values)
    assert lo < sum(values) / len(values) < hi
    assert er.bootstrap_ci(values) == (lo, hi)
    assert er.bootstrap_ci([1.0]) == (None, None)


def test_holdout_is_hidden_unless_explicitly_opened():
    results = [res("2020-05-01", 1.0), res("2023-05-01", -1.0), res("2025-06-01", 5.0)]
    hidden = er.split_stats(results)
    assert hidden["all"]["n"] == 2 and "holdout" not in hidden            # a 2025-os trade nem latszik
    assert hidden["train"]["n"] == 1 and hidden["test"]["n"] == 1
    opened = er.split_stats(results, include_holdout=True)
    assert opened["holdout"]["n"] == 1 and opened["holdout"]["ev"] == 5.0


def test_holdout_opening_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "HOLDOUT_LOG", tmp_path / "holdout_log.jsonl")
    assert er.holdout_openings() == 0
    er.log_holdout_opening(["a", "b"], 10)
    er.log_holdout_opening(["a"], 10)
    assert er.holdout_openings() == 2


def test_yearly_breakdown():
    results = [res("2019-03-01", 1.0), res("2019-09-01", 1.0), res("2020-03-01", -2.0), res("2021-03-01", 0.5)]
    y = er.yearly(results)
    assert y["years"] == 3 and y["positive"] == 2 and y["worst_year"] == 2020 and y["worst"] == -2.0


def test_robust_winner_needs_confidence_lower_bound():
    def row(name, rs):
        results = [res(f"{2016 + i % 9}-0{1 + i % 9}-15", r) for i, r in enumerate(rs)]   # 2016-2024
        return {"variant": type("V", (), {"name": name})(), "stats": er.split_stats(results)}

    base = row("base", [0.5, -0.4] * 20)                         # EV +0.05, szuk intervallum
    noisy = row("noisy", [6.0, -1.0, -1.0, -1.0, -1.0] * 8)        # EV magasabb, de szeles, also hatar < base
    steady = row("steady", [1.0, -0.5] * 20)                        # EV 0.25, also hatar > base
    winners = [w["variant"].name for w in er.robust_winners([noisy, steady], base["stats"], min_period_n=5)]
    assert "steady" in winners and "noisy" not in winners


# ---------------------------------------------------------------------------
# Earnings-tilalom visszatesztelese historikus jelentesi datumokkal
# ---------------------------------------------------------------------------
from datetime import date  # noqa: E402

from stockwatch import market  # noqa: E402
from stockwatch.config import EntrySettings, yaml_tickers  # noqa: E402


def test_next_earnings_days_from_history():
    dates = [date(2024, 1, 25), date(2024, 4, 30), date(2024, 7, 30)]
    assert market.next_earnings_days(dates, date(2024, 4, 25)) == 5
    assert market.next_earnings_days(dates, date(2024, 4, 30)) == 0
    assert market.next_earnings_days(dates, date(2024, 8, 1)) is None


def test_variant_earnings_flag_controls_blackout():
    cfg = EntrySettings(earnings_blackout_days=7)
    with_e = er.Variant.from_settings(cfg)
    assert with_e.earnings is True
    assert with_e.entry_settings(cfg).earnings_blackout_days == 7
    without = er.Variant("x", earnings=False)
    assert without.entry_settings(cfg).earnings_blackout_days == 0


def test_yaml_tickers_skips_boolean_ticker():
    assert yaml_tickers(["aapl", True, " msft ", "AAPL", None, ""]) == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# Idoponthelyes kalibracio: a jelzes UTAN lezarulo kimenetel nem szamit
# ---------------------------------------------------------------------------
from stockwatch.patterns.base import PatternMatch  # noqa: E402


def test_point_in_time_calibration_has_no_look_ahead():
    idx = pd.bdate_range("2024-01-01", periods=40)
    close = [100.0] * 10 + [100 + i for i in range(1, 31)]          # a 10. gyertya utan emelkedik
    df = pd.DataFrame({"open": close, "high": [c + 0.5 for c in close], "low": [c - 0.5 for c in close],
                       "close": close, "volume": 1e6}, index=idx)
    match = PatternMatch(symbol="T", name="Bull zaszlo", direction="bullish", status="triggered", quality=0.8,
                         trigger_level=100.0, target=105.0, invalidation=95.0,
                         start_date=idx[0], end_date=idx[9], trigger_date=idx[9])
    event = er.Event(symbol="T", index=9, horizon=20, match=match)
    pit = er.PointInTimeCalibration({"T": [event]}, {"T": df})
    key = "Bull zaszlo|bullish"
    # a cel (105) a 15. gyertyanal teljesul -> elotte nincs minta, utana 1 talalat
    before = pit.calibration_for(idx[12], key).data["patterns"][key]
    after = pit.calibration_for(idx[30], key).data["patterns"][key]
    assert before["samples"] == 0
    assert after["samples"] == 1 and after["hit_rate"] == 1.0 and after["expectancy_r"] == pytest.approx(1.0)
    assert pit.calibration_for(idx[30], "Nincs|bullish").data == {}
