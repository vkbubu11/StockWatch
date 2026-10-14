"""Short motor (kiserleti) es a "Legjobb belepo most" kereses."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import best, indicators, market
from stockwatch import short as short_mod
from stockwatch.config import EntrySettings, ScanSettings, Settings
from stockwatch.patterns.base import PatternMatch


def downtrend(bars: int = 320, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 200 * np.cumprod(1 - 0.0025 + rng.normal(0, 0.006, bars))
    opens = np.r_[closes[0], closes[:-1]]
    idx = pd.bdate_range("2022-01-03", periods=bars, name="date")
    df = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.004,
                       "low": np.minimum(opens, closes) * 0.996, "close": closes, "volume": 1e6}, index=idx)
    return indicators.enrich(df, 14)


def bear_match(df: pd.DataFrame, status: str = "triggered", above: float = 0.01) -> PatternMatch:
    close = float(df["close"].iloc[-1])
    trigger = close * (1 + above)
    return PatternMatch("TEST", "Dupla teto", "bearish", status, 0.8, trigger_level=trigger,
                        target=trigger * 0.80, invalidation=trigger * 1.04, start_date=df.index[-60],
                        end_date=df.index[-3], trigger_date=df.index[-2] if status == "triggered" else None)


def bear_regime() -> market.Regime:
    return market.Regime(False, 90.0, 100.0, "benchmark -10% az SMA200 alatt")


def test_short_classify_plan_checks():
    df = downtrend()
    cfg = EntrySettings(rsi_min=0, rsi_max=100)                 # az RSI-savot itt nem teszteljuk
    m = bear_match(df)
    assert short_mod.classify(m, df, cfg) == "breakdown"
    assert short_mod.classify(bear_match(df, status="forming", above=-0.002), df, cfg) == "setup"   # az ar meg a trigger felett
    bull = PatternMatch(**{**m.__dict__, "direction": "bullish"})
    assert short_mod.classify(bull, df, cfg) is None

    plan = short_mod.build_plan(m, "breakdown", df, cfg)
    assert plan.stop > plan.entry > plan.target                # short: stop felul, cel alul
    assert plan.risk_reward == pytest.approx((plan.entry - plan.target) / (plan.stop - plan.entry))
    assert plan.stop - plan.entry >= cfg.min_stop_atr * plan.atr - 1e-9
    assert plan.shares > 0

    checks = {c.name: c.passed for c in short_mod.run_checks(m, "breakdown", df, plan, cfg, bear_regime(), None)}
    assert checks["Trend (lefele)"] is True and checks[f"Piac ({cfg.benchmark})"] is True
    bull_market = market.Regime(True, 110.0, 100.0, "felett")
    checks = {c.name: c.passed for c in short_mod.run_checks(m, "breakdown", df, plan, cfg, bull_market, None)}
    assert checks[f"Piac ({cfg.benchmark})"] is False          # bikapiacon nincs short


def test_short_evaluate_and_dict():
    df = downtrend()
    settings = Settings(entry=EntrySettings(rsi_min=0, rsi_max=100, earnings_blackout_days=0))
    signals = short_mod.evaluate("TEST", df, [bear_match(df)], settings)
    assert len(signals) == 1
    d = signals[0].to_dict(settings.entry)
    assert d["side"] == "short" and d["experimental"] and 0 <= d["score"] <= 100
    assert d["plan"]["stop"] > d["plan"]["entry"] > d["plan"]["target"]


def test_best_list_setting_validation():
    assert ScanSettings(best_list="indexes").best_list == "indexes"
    with pytest.raises(ValueError):
        ScanSettings(best_list="minden")


def _item(symbol, score, qualifies=True, kind="breakout", failed=()):
    return {"symbol": symbol, "score": score, "qualifies": qualifies, "kind": kind, "failed": list(failed)}


def test_side_picks_best_qualifying_entry():
    side = best._side([_item("A", 60), _item("B", 80), _item("C", 90, kind="setup"),
                       _item("D", 95, qualifies=False, failed=["Trend"]), _item("E", 50, qualifies=False,
                                                                                failed=["Trend", "RSI"])])
    assert side["best"]["symbol"] == "B"                       # a magasabb pontszamu belepo, nem az elokeszulet
    assert [d["symbol"] for d in side["others"]] == ["A"]
    assert [d["symbol"] for d in side["setups"]] == ["C"]
    assert [d["symbol"] for d in side["near"]] == ["D", "E"]   # a kevesebb elbukott szuro elol
    assert best._side([])["best"] is None


def test_large_list_uses_nightly_shortlist(tmp_path, monkeypatch):
    import json

    settings = Settings(scan=ScanSettings(best_list="sp500"))
    monkeypatch.setattr(type(settings), "index_scan_path", property(lambda self: tmp_path / "index_scan.json"))
    members = [f"S{k}" for k in range(200)]
    (tmp_path / "index_scan.json").write_text(json.dumps({
        "signals": [{"symbol": "S1", "score": 70, "qualifies": True, "failed": []},
                    {"symbol": "ZZZ", "score": 99, "qualifies": True, "failed": []}],     # nem listatag
        "shorts": [{"symbol": "S2", "score": 55, "qualifies": False, "failed": ["Piac (QQQ)"]}]}))
    assert best.shortlist(settings, members) == ["S1", "S2"]
