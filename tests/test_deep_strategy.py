"""A Mely visszahuzas strategia: beallitas (tag stop), monitor-kor, ertesites-nemitas es paper-kotes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stockwatch import config, meanrev, meanrev_research, monitor, positions
from stockwatch.alerts import Dispatcher
from stockwatch.config import StrategiesSettings
from tests.test_ledger import journal_dir  # noqa: F401  (fixture)
from tests.test_meanrev_live import _cycle_env, env, uptrend_with_drop  # noqa: F401  (fixture)


def test_deep_strategy_defaults_and_paper_link():
    cfg = StrategiesSettings()
    deep, pullback = cfg.deep.params, cfg.pullback.params
    assert "deep" in config.STRATEGY_IDS and "deep" in config.PAPER_STRATEGIES
    assert cfg.deep.enabled and not cfg.deep.alerts            # virtualisan kovet, de nem ertesit
    assert deep.stop_atr == 5.0 and pullback.stop_atr == 3.0   # csak a stop tagabb
    assert deep.model_dump() | {"stop_atr": 3.0} == pullback.model_dump()
    assert StrategiesSettings(paper="deep").paper == "deep"
    assert "deep" in monitor.PULLBACK_STRATEGIES


def test_deep_stop_is_wider_than_pullback(env):  # noqa: F811
    df = uptrend_with_drop()
    cfg = StrategiesSettings()
    a = meanrev.evaluate("A", df, env, cfg.pullback.params, None)
    b = meanrev.evaluate("A", df, env, cfg.deep.params, None)
    assert a.plan.entry_limit == pytest.approx(b.plan.entry_limit)
    assert b.plan.stop < a.plan.stop
    assert (b.plan.entry_limit - b.plan.stop) == pytest.approx((a.plan.entry_limit - a.plan.stop) * 5 / 3)


def test_deep_neighbourhood_includes_its_own_stop():
    names = [n for n, _ in meanrev_research.neighbourhood(StrategiesSettings().deep.params)]
    assert any("stop 5ATR" in n for n in names) and any("stop 3ATR" in n for n in names)
    pullback_names = [n for n, _ in meanrev_research.neighbourhood(StrategiesSettings().pullback.params)]
    assert not any("stop 5ATR" in n for n in pullback_names)    # a Visszahuzas vizsgalata valtozatlan


def test_deep_alerts_muted_by_default(env):  # noqa: F811
    live = env.model_copy(update={"strategies": StrategiesSettings()})
    d = Dispatcher(live)
    assert not d._strategy_alerts(SimpleNamespace(strategy="deep"))
    assert d._strategy_alerts(SimpleNamespace(strategy="pullback"))


def test_deep_cycle_tracks_and_submits_when_linked(env, monkeypatch):  # noqa: F811
    frames = {s: uptrend_with_drop(symbol_seed=i) for i, s in enumerate("ABC")}
    live, submitted = _cycle_env(env, monkeypatch, frames, paper="deep")
    sent = []
    dispatcher = SimpleNamespace(dispatch=sent.append, dry_run=False, settings=live)
    tracker, state = positions.PositionTracker(live), monitor.entry.EntryState(live)
    out = monitor.pullback_cycle(live, "deep", live.strategies.deep.params, dispatcher, tracker, state, refresh=False)
    assert len(out["alerted"]) == 3
    assert all(a.strategy == "deep" for a in sent if a.event == "entry_pullback")
    assert len([p for p in tracker.active() if p.strategy == "deep"]) == 3
    assert len(submitted) == 3                                   # a paper fiok a Mely visszahuzashoz kotott


def test_candidates_carry_exact_evaluation_time(env):  # noqa: F811
    """Minden jelolt (atment vagy elbukott) pontos kiertekelesi idopontot visz; a jelazonosito nem fugg tole."""
    from datetime import datetime, timedelta, timezone

    df = uptrend_with_drop()
    params = StrategiesSettings().pullback.params
    failing = params.model_copy(update={"min_price": 1e9})          # az "Ar" szuron elbukik
    before = datetime.now(timezone.utc).replace(microsecond=0)
    ok, bad = (meanrev.evaluate("A", df, env, p, None) for p in (params, failing))
    assert ok.passed and not bad.passed
    for sig in (ok, bad):
        d = sig.to_dict()
        stamp = datetime.fromisoformat(d["evaluated_at"])
        assert stamp.tzinfo is not None and before <= stamp <= before + timedelta(minutes=1)
        assert d["as_of"] == f"{df.index[-1]:%Y-%m-%d}"
    later = bad.__class__(**{**bad.__dict__, "evaluated_at": before + timedelta(days=1)})
    assert later.signal_id == bad.signal_id
