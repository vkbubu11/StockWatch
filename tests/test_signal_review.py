"""Az ertesitesek utolagos ertekelesenek tesztjei - kezzel epitett gyertyakon."""
from __future__ import annotations

import pandas as pd
import pytest

from stockwatch import signal_review
from stockwatch.config import Settings


def bars(rows: list[tuple[float, float, float, float]], start: str = "2026-03-02") -> pd.DataFrame:
    """(open, high, low, close) sorok munkanapokra."""
    index = pd.bdate_range(start, periods=len(rows), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    df["volume"] = 1e6
    return df


def pattern_record(event: str, direction: str, price: float, target: float, invalidation: float,
                   as_of: str = "2026-03-02") -> dict:
    return {"event": event, "symbol": "TEST", "price": price, "as_of": f"{as_of} 00:00:00",
            "direction": direction,
            "pattern": {"direction": direction, "target": target, "invalidation": invalidation}}


def entry_record(stop: float, target: float, limit: float, shares: int = 10,
                 as_of: str = "2026-03-02") -> dict:
    return {"event": "entry_breakout", "symbol": "TEST", "price": 100.0, "as_of": f"{as_of} 00:00:00",
            "entry": {"as_of": as_of, "price": 100.0, "plan": {
                "entry": 100.0, "entry_limit": limit, "stop": stop, "target": target, "shares": shares,
                "exit_mode": "partial", "horizon": 40}}}


def test_categories():
    assert signal_review.category({"event": "entry_breakout"}) == ("signal", "long")
    assert signal_review.category(pattern_record("triggered", "bearish", 1, 1, 1)) == ("signal", "short")
    assert signal_review.category(pattern_record("failed", "bullish", 1, 1, 1))[0] == "outcome"
    assert signal_review.category({"event": "pos_stop"}) == ("other", "")
    assert signal_review.category({"event": "broker_filled"}) == ("other", "")


def test_long_pattern_hits_target():
    df = bars([(100, 101, 99, 100), (100, 104, 99.5, 103), (103, 111, 102, 110)])
    r = signal_review.review(pattern_record("triggered", "bullish", 100, 110, 95), df, Settings())
    assert r["grade"] == "good" and "cel elerve" in r["outcome"]
    assert r["change_pct"] == pytest.approx(10.0)
    assert r["r"] == pytest.approx(2.0)                   # 10 / (100 - 95)


def test_short_pattern_profits_when_price_falls():
    df = bars([(100, 101, 99, 100), (99, 99.5, 93, 94), (94, 95, 89, 90)])
    r = signal_review.review(pattern_record("triggered", "bearish", 100, 90, 105), df, Settings())
    assert r["side"] == "short" and r["grade"] == "good"
    assert r["change_pct"] == pytest.approx(10.0)        # 100 -> 90 shortnal +10%
    assert r["mfe_pct"] == pytest.approx(11.0)            # a legmelyebb pont 89
    assert r["mae_pct"] == pytest.approx(0.5)             # 100 fole sosem ment: a "legrosszabb" is kedvezo


def test_same_bar_target_and_invalidation_is_bad():
    df = bars([(100, 101, 99, 100), (100, 111, 94, 105)])
    r = signal_review.review(pattern_record("new", "bullish", 100, 110, 95), df, Settings())
    assert r["grade"] == "bad"


def test_open_when_neither_level_reached():
    df = bars([(100, 101, 99, 100), (100, 103, 99, 102)])
    r = signal_review.review(pattern_record("new", "bullish", 100, 110, 95), df, Settings())
    assert r["grade"] == "open" and r["change_pct"] == pytest.approx(2.0)


def test_already_invalid_at_signal_time():
    df = bars([(100, 101, 99, 100), (100, 103, 99, 102)])
    r = signal_review.review(pattern_record("new", "bullish", 100, 120, 105), df, Settings())
    assert r["grade"] == "bad" and "mar a jelzeskor" in r["outcome"]


def test_entry_signal_partial_then_target():
    # belepes 101-en (nyito), stop 95 -> 1R = 6; +1R = 107; cel 115
    df = bars([(100, 101, 99, 100), (101, 102, 100, 101.5), (102, 108, 101.5, 107), (107, 116, 106, 115)])
    r = signal_review.review(entry_record(stop=95, target=115, limit=102), df, Settings())
    assert r["grade"] == "good"
    assert r["r"] == pytest.approx(0.5 * 1 + 0.5 * (115 - 101) / 6)
    assert r["pnl_usd"] == pytest.approx(r["r"] * 6 * 10)


def test_entry_skipped_when_open_above_limit():
    df = bars([(100, 101, 99, 100), (104, 105, 103, 104)])
    r = signal_review.review(entry_record(stop=95, target=115, limit=102), df, Settings())
    assert r["grade"] == "na" and "nem lett belepes" in r["outcome"]


def test_entry_stopped_out_is_bad():
    df = bars([(100, 101, 99, 100), (101, 101.5, 94, 95)])
    r = signal_review.review(entry_record(stop=95, target=115, limit=102), df, Settings())
    assert r["grade"] == "bad" and r["r"] == pytest.approx(-1.0)


def test_no_future_bars_used_before_signal():
    df = bars([(90, 120, 80, 100), (100, 101, 99, 100), (100, 103, 99, 102)], start="2026-02-27")
    r = signal_review.review(pattern_record("new", "bullish", 100, 110, 95, as_of="2026-03-02"), df, Settings())
    assert r["grade"] == "open"                           # a jelzes elotti nagy gyertya nem szamit


def test_strength_for_entry_uses_entry_score():
    rec = entry_record(stop=95, target=115, limit=102)
    for score, label in ((45, "weak"), (60, "medium"), (82, "strong")):
        rec["entry"]["score"] = score
        s = signal_review.strength(rec, Settings())
        assert s["strength"] == score and s["strength_label"] == label
        assert s["strength_kind"] == "entry_score"


def test_strength_for_pattern_uses_quality_buckets():
    rec = pattern_record("triggered", "bullish", 100, 110, 95)
    for quality, label in ((0.65, "weak"), (0.75, "medium"), (0.9, "strong")):
        rec["pattern"]["quality"] = quality
        s = signal_review.strength(rec, Settings())
        assert s["strength"] == pytest.approx(quality * 100) and s["strength_label"] == label
    assert signal_review.strength({"event": "pos_stop"}, Settings())["strength"] is None


def test_summary_by_strength():
    rows = [{"category": "signal", "grade": g, "change_pct": 0, "strength_label": lbl}
            for g, lbl in (("good", "strong"), ("good", "strong"), ("bad", "strong"), ("bad", "weak"))]
    by = signal_review.summary(rows)["by_strength"]
    assert by["strong"]["hit_rate"] == pytest.approx(2 / 3) and by["weak"]["hit_rate"] == 0
    assert by["medium"]["decided"] == 0 and by["medium"]["hit_rate"] is None


def test_summary_hit_rate():
    rows = [{"category": "signal", "grade": g, "change_pct": c}
            for g, c in (("good", 5), ("bad", -2), ("good", 3), ("open", 1))]
    rows.append({"category": "other", "grade": "na", "change_pct": None})
    s = signal_review.summary(rows)
    assert s["signals"] == 4 and s["decided"] == 3 and s["hit_rate"] == pytest.approx(2 / 3)
    assert s["avg_change_pct"] == pytest.approx(7 / 4)
