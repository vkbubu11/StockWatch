"""Tobb strategia: aktiv (paper fiokhoz kotott), kutatasi, homokozo - megkulonboztethetoen."""
from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from stockwatch import broker, config, ledger, monitor, positions, webapp
from stockwatch.alerts import Alert, Dispatcher
from stockwatch.broker import BrokerBook
from stockwatch.config import Settings, StrategiesSettings
from tests.test_ledger import journal_dir, scenario  # noqa: F401  (fixture)


@pytest.fixture()
def env(tmp_path, monkeypatch, journal_dir):  # noqa: F811
    settings, raw, signal = scenario()
    signal.kind = "breakout"
    cls = type(settings)
    monkeypatch.setattr(cls, "positions_path", property(lambda self: tmp_path / "pos.json"))
    monkeypatch.setattr(cls, "broker_path", property(lambda self: tmp_path / "broker.json"))
    monkeypatch.setattr(cls, "signals_path", property(lambda self: tmp_path / "signals.jsonl"))
    monkeypatch.setattr(config, "STRATEGIES_PATH", tmp_path / "strategies.yaml")
    return settings, raw, signal


# ---------------------------------------------------------------------------
# Azonositok, regi adatok
# ---------------------------------------------------------------------------
def test_breakout_ids_unchanged_other_strategies_distinct(env):
    _, _, signal = env
    breakout = (signal.signal_id, signal.key, signal.trade_key)
    assert signal.trade_key == f"{signal.symbol}|{signal.as_of:%Y-%m-%d}|{signal.pattern.name}"
    signal.strategy = "sandbox"
    sandbox = (signal.signal_id, signal.key, signal.trade_key)
    signal.strategy = "pullback"
    pullback = (signal.signal_id, signal.key, signal.trade_key)
    assert len({breakout, sandbox, pullback}) == 3                  # azonos papiron / napon sem utkoznek
    assert all(a != b for a, b in zip(breakout, sandbox))
    assert signal.to_dict()["strategy"] == "pullback"


def test_old_records_load_as_breakout(env):
    settings, _, signal = env
    tracker = positions.PositionTracker(settings)
    pos = tracker.register(signal)
    tracker.save()
    raw = json.loads(settings.positions_path.read_text())
    for value in raw["positions"].values():
        value.pop("strategy")                                       # a strategia-mezo elotti fajl
    settings.positions_path.write_text(json.dumps(raw))
    assert positions.PositionTracker(settings, reconcile=False).positions[pos.id].strategy == "breakout"

    trade = broker.BrokerTrade(id="X|2026-01-02|P", symbol="X", pattern="P", kind="breakout",
                               signal_date="2026-01-02", entry_limit=10, stop=9, target=12, partial_price=None,
                               shares=1, horizon=10, exit_mode="target")
    data = asdict(trade)
    data.pop("strategy")
    settings.broker_path.write_text(json.dumps({"trades": {trade.id: data}}))
    assert BrokerBook(settings).trades[trade.id].strategy == "breakout"


def test_tracked_trade_keeps_its_strategy(env):
    settings, _, signal = env
    signal.strategy = "pullback"
    pos = positions.PositionTracker(settings).register(signal)
    assert pos.strategy == "pullback" and pos.id.endswith("|pullback")


# ---------------------------------------------------------------------------
# Beallitasok
# ---------------------------------------------------------------------------
def test_sandbox_can_never_be_paper():
    with pytest.raises(ValueError):
        StrategiesSettings(paper="sandbox")
    with pytest.raises(ValueError):
        StrategiesSettings(sandbox={"entry": {"nincs_ilyen": 1}})
    assert StrategiesSettings().paper == "breakout" and StrategiesSettings().sandbox.alerts is False


def test_strategies_file_overrides_defaults(env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "settings.yaml").write_text("broker:\n  auto_trade: true\n", encoding="utf-8")
    config.save_strategies(StrategiesSettings(paper="pullback"))
    try:
        assert config.load_settings().strategies.paper == "pullback"
    finally:
        config.load_settings.cache_clear()


# ---------------------------------------------------------------------------
# Paper bekuldes: csak a hozzakotott strategia
# ---------------------------------------------------------------------------
def test_only_paper_strategy_is_submitted(env, monkeypatch):
    settings, _, signal = env
    live = settings.model_copy(update={"broker": settings.broker.model_copy(update={"auto_trade": True})})
    monkeypatch.setattr(monitor, "_fresh_settings", lambda: live)
    monkeypatch.setattr(broker.AlpacaClient, "from_env", classmethod(lambda cls: object()))
    sent = []

    def fake_submit(sig, *_):
        sent.append(sig.strategy)
        raise broker.BrokerError("teszt")
    monkeypatch.setattr(broker, "submit", fake_submit)
    sandbox = __import__("copy").copy(signal)
    sandbox.strategy = "sandbox"
    monitor.submit_to_broker([(sandbox, None), (signal, None)], SimpleNamespace(dispatch=lambda a: None))
    assert sent == ["breakout"]


# ---------------------------------------------------------------------------
# Ertesitesek
# ---------------------------------------------------------------------------
def test_alerts_carry_strategy_and_sandbox_is_journal_only(env):
    settings, _, _ = env
    d = Dispatcher(settings)
    names = [c.name for c in d.channels]
    assert "jsonl" in names
    a = Alert(symbol="AAPL", title="AAPL: x", body="", forecast=None, event="entry_breakout", strategy="sandbox")
    labelled = d._labelled(a)
    assert labelled.title.startswith("[Homokozo] ") and d._labelled(labelled).title == labelled.title
    assert d._strategy_alerts(a) is False                           # alapbol nem megy Discordra
    assert d._strategy_alerts(Alert("A", "t", "", None, "entry_breakout", strategy="breakout")) is True
    assert d._strategy_alerts(Alert("A", "t", "", None, "triggered")) is True

    d.dispatch(a)
    rec = json.loads(settings.signals_path.read_text().splitlines()[-1])
    assert rec["strategy"] == "sandbox" and rec["title"].startswith("[Homokozo]")


def test_alert_strategy_of_old_records():
    assert webapp.alert_strategy({"event": "broker_filled"}) == "breakout"
    assert webapp.alert_strategy({"event": "triggered"}) is None
    assert webapp.alert_strategy({"event": "hold_stop"}) is None
    assert webapp.alert_strategy({"event": "entry_pullback", "strategy": "pullback"}) == "pullback"


# ---------------------------------------------------------------------------
# Valtas a feluletrol
# ---------------------------------------------------------------------------
def test_switch_paper_strategy_logged_and_notified(env, monkeypatch):
    settings, _, _ = env
    monkeypatch.setattr(webapp, "load_settings", lambda: config.load_settings())
    monkeypatch.setattr(config, "_read_yaml", lambda path: {} if path.name == "settings.yaml"
                        else __import__("yaml").safe_load(path.read_text(encoding="utf-8")))
    sent = []
    monkeypatch.setattr(webapp, "Dispatcher", lambda s: SimpleNamespace(dispatch=sent.append))
    config.load_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="homokozo"):
            webapp.strategies_action({"action": "paper", "paper": "sandbox"})
        with pytest.raises(ValueError, match="nem kapcsolhato ki"):
            webapp.strategies_action({"action": "update", "strategy": "breakout", "values": {"enabled": False}})

        out = webapp.strategies_action({"action": "paper", "paper": "pullback"})
        assert out["paper"] == "pullback" and config.STRATEGIES_PATH.exists()
        assert [a.event for a in sent] == ["strategy_switch"]
        change = ledger.config_changes(1)[0]
        assert "strategies.paper" in change["changes"]

        out = webapp.strategies_action({"action": "update", "strategy": "sandbox",
                                        "values": {"alerts": True, "params": {"rsi2_max": 3}}})
        sandbox = next(x for x in out["strategies"] if x["id"] == "sandbox")
        assert sandbox["alerts"] is True and sandbox["params"]["rsi2_max"] == 3
        assert sandbox["params"]["limit_pct"] == 5.0                # a tobbi parameter megmarad
    finally:
        config.load_settings.cache_clear()


# ---------------------------------------------------------------------------
# Homokozo
# ---------------------------------------------------------------------------
def test_sandbox_breakout_settings():
    base = Settings()
    assert base.sandbox_breakout() is None                         # alapbol a visszahuzas-motorral fut
    s = base.model_copy(update={"strategies": StrategiesSettings(
        sandbox={"engine": "breakout", "entry": {"min_score": 70, "confirm_breakout": False}})})
    sb = s.sandbox_breakout()
    assert sb.entry.min_score == 70 and sb.entry.confirm_breakout is False
    assert sb.entry.exit_mode == base.entry.exit_mode               # a tobbi mezo az elo beallitas


def test_sandbox_actions(env, monkeypatch, tmp_path):
    from stockwatch import meanrev_research as mr

    monkeypatch.setattr(webapp, "load_settings", lambda: config.load_settings())
    monkeypatch.setattr(config, "_read_yaml", lambda path: {} if path.name == "settings.yaml"
                        else __import__("yaml").safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(mr, "SANDBOX_DIR", tmp_path / "sb")
    monkeypatch.setattr(mr, "SANDBOX_REQUEST", tmp_path / "sb" / "request.json")
    monkeypatch.setattr(mr, "SANDBOX_STATUS", tmp_path / "sb" / "status.json")
    monkeypatch.setattr(mr, "OUT_DIR", tmp_path / "mr")
    config.load_settings.cache_clear()
    try:
        with pytest.raises(ValueError):
            webapp.strategies_action({"action": "update", "strategy": "sandbox",
                                      "values": {"entry": {"min_score": 500}}})
        webapp.strategies_action({"action": "update", "strategy": "sandbox", "values": {"params": {"limit_pct": 4}}})
        out = webapp.sandbox_action({"action": "backtest"})
        assert mr.SANDBOX_REQUEST.exists() and out["backtest"]["status"]["state"] == "queued"
        assert json.loads(mr.SANDBOX_REQUEST.read_text())["params"]["limit_pct"] == 4
        webapp.sandbox_action({"action": "adopt"})
        assert config.load_settings().strategies.pullback.params.limit_pct == 4
        assert config.load_settings().strategies.paper == "breakout"   # az atvetel nem kot a paper fiokhoz
    finally:
        config.load_settings.cache_clear()
