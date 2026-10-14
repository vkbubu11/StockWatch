"""Az ETF visszahuzas strategia: beallitas, SMA-kilepes, sajat lista, monitor-kor es paper-kotes."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stockwatch import config, meanrev, monitor, positions
from stockwatch.config import PullbackParams, StrategiesSettings
from tests.test_ledger import journal_dir  # noqa: F401  (fixture)
from tests.test_meanrev_live import _cycle_env, env, uptrend_with_drop  # noqa: F401  (fixture)


def test_etf_strategy_defaults_and_paper_link():
    cfg = StrategiesSettings()
    etf = cfg.etf
    assert "etf" in config.STRATEGY_IDS and "etf" in config.PAPER_STRATEGIES
    assert etf.enabled and "SPY" in etf.symbols and "QQQ" in etf.symbols
    p = etf.params
    assert (p.rsi2_max, p.limit_pct, p.exit_sma, p.earnings_blackout_days) == (10.0, 1.0, 5, 0)
    assert p.max_signals_per_day == 2 and p.max_open == 4
    assert StrategiesSettings(paper="etf").paper == "etf"
    # a Visszahuzas alapbeallitasa valtozatlan (nincs SMA-kilepes)
    assert cfg.pullback.params.exit_sma == 0 and cfg.pullback.params.limit_pct == 5.0


def test_etf_symbols_normalised_and_validated():
    assert config.EtfStrategy(symbols=[" spy", "qqq", "SPY"]).symbols == ["SPY", "QQQ"]
    with pytest.raises(ValueError):
        config.EtfStrategy(symbols=[])
    with pytest.raises(ValueError):
        PullbackParams(exit_sma=99)


def test_sma_exit_fires_on_first_bounce():
    idx = pd.bdate_range("2024-01-01", periods=260)
    close = np.r_[np.linspace(100, 130, 250), [126, 123, 121, 124, 128, 129, 130, 131, 132, 133]]
    df = pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998, "close": close,
                       "volume": 1e6}, index=idx)
    base = PullbackParams(exit_crsi=99.0)                       # a CRSI-kilepes gyakorlatilag ki
    with_sma = base.model_copy(update={"exit_sma": 5})
    plain = meanrev.exit_flags(meanrev.features(df, base), base)
    sma = meanrev.exit_flags(meanrev.features(df, with_sma), with_sma)
    assert not plain.iloc[-6] and sma.iloc[-6]                  # 128 > az utolso 5 zaro atlaga
    assert sma.sum() > plain.sum()


def test_etf_uses_its_own_symbol_list(env):  # noqa: F811
    live = env.model_copy(update={"strategies": StrategiesSettings(etf=config.EtfStrategy(symbols=["SPY", "XLK"]))})
    assert monitor.pullback_symbols(live, allow_network=False, strategy="etf") == ["SPY", "XLK"]


def test_etf_cycle_alerts_tracks_and_submits_when_linked(env, monkeypatch):  # noqa: F811
    frames = {s: uptrend_with_drop(symbol_seed=i) for i, s in enumerate(["A", "SPY", "XLK", "XLF"])}
    live, submitted = _cycle_env(env, monkeypatch, frames, paper="etf")
    sent = []
    dispatcher = SimpleNamespace(dispatch=sent.append, dry_run=False, settings=live)
    tracker, state = positions.PositionTracker(live), monitor.entry.EntryState(live)
    params = live.strategies.etf.params
    out = monitor.pullback_cycle(live, "etf", params, dispatcher, tracker, state, refresh=False)
    assert len(out["alerted"]) == 2                              # napi max. 2 (az ETF-ek egyutt mozognak)
    assert all(a.strategy == "etf" for a in sent if a.event == "entry_pullback")
    assert len([p for p in tracker.active() if p.strategy == "etf"]) == 2
    assert len(submitted) == 2                                   # a paper fiok az ETF-hez kotott
    sig = meanrev.evaluate("SPY", frames["SPY"], live, params, None)
    assert sig.plan.entry_limit == pytest.approx(sig.price * 0.99)
