"""Az ertesitesi irany (long / short / mindketto) es a paper szamla eredmenyenek tesztjei."""
from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from stockwatch import broker, monitor, webapp
from stockwatch.config import AlertSettings


def cfg(direction: str, legacy: bool = False) -> AlertSettings:
    return AlertSettings(direction=direction, pattern_events=legacy)


def test_direction_validated():
    assert AlertSettings().direction == "long"                    # alap: a mostani viselkedes
    with pytest.raises(ValidationError):
        AlertSettings(direction="mindegy")


@pytest.mark.parametrize("direction, bear_ok", [("long", False), ("short", True), ("both", True)])
def test_short_breakdown_only_when_short_enabled(direction, bear_ok):
    allowed = monitor.pattern_alert_allowed
    assert allowed(cfg(direction), "bearish", "triggered", fresh=True) is bear_ok
    assert allowed(cfg(direction), "bearish", "target_hit") is bear_ok
    # a nem friss (regi) torest nem jelezzuk, az uj (formalodo) alakzatot sem
    assert allowed(cfg(direction), "bearish", "triggered", fresh=False) is False
    assert allowed(cfg(direction), "bearish", "new") is False
    # bikas alakzat-ertesites csak a regi alakzat-ertesitesekkel megy (a long jelzes a belepesi motor)
    assert allowed(cfg(direction), "bullish", "triggered") is False


def test_legacy_pattern_events_follow_direction():
    allowed = monitor.pattern_alert_allowed
    assert allowed(cfg("long", legacy=True), "bullish", "new") is True
    assert allowed(cfg("long", legacy=True), "bearish", "new") is False
    assert allowed(cfg("short", legacy=True), "bullish", "triggered") is False
    assert allowed(cfg("both", legacy=True), "bearish", "failed") is True


def test_fresh_breakout_window():
    index = pd.bdate_range("2026-10-01", periods=5)
    df = pd.DataFrame({"close": range(5)}, index=index)

    class M:
        trigger_date = index[-2]

    assert monitor._fresh_breakout(M, df)
    M.trigger_date = index[-4]
    assert not monitor._fresh_breakout(M, df)
    M.trigger_date = None
    assert not monitor._fresh_breakout(M, df)


class FakeHistoryClient:
    def __init__(self, raw=None, fail=False):
        self.raw, self.fail = raw, fail

    def portfolio_history(self, period="all", timeframe="1D"):
        if self.fail:
            raise broker.BrokerError("nincs")
        return self.raw


def trade(symbol, status, pnl=None):
    t = broker.BrokerTrade(id=symbol, symbol=symbol, pattern="Bull zaszlo", kind="breakout",
                           signal_date="2026-10-09", entry_limit=100, stop=90, target=130,
                           partial_price=110, shares=10, horizon=40, exit_mode="partial", status=status)
    t.realised_pnl = pnl
    return t


def test_broker_pnl_account_and_system():
    raw = {"timestamp": [1760054400, 1760140800, 1760227200], "equity": [10000, 10150, 10320],
           "base_value": 10000}
    account = {"equity": "10320", "last_equity": "10150"}
    positions = [{"symbol": "AAA", "unrealized_pl": "45.5"}, {"symbol": "KEZI", "unrealized_pl": "500"}]
    trades = [trade("AAA", "open"), trade("BBB", "closed", 120.0), trade("CCC", "closed", -60.0),
              trade("DDD", "expired")]
    history, pnl = webapp._broker_pnl(FakeHistoryClient(raw), account, positions, trades)
    # a napi tortenet vegere a mai, elo equity kerul (a 2025-os pontok utan)
    assert history["equity"] == [10000, 10150, 10320, 10320] and history["base"] == 10000 and history["live"]
    assert pnl["account_total"] == pytest.approx(320) and pnl["account_total_pct"] == pytest.approx(3.2)
    assert pnl["today"] == pytest.approx(170)
    assert pnl["system_realised"] == pytest.approx(60)            # 120 - 60
    assert pnl["system_unrealised"] == pytest.approx(45.5)        # a kezi KEZI pozicio nem a rendszere
    assert pnl["system_total"] == pytest.approx(105.5)
    assert pnl["closed_trades"] == 2 and pnl["open_trades"] == 1


def test_broker_pnl_without_history():
    history, pnl = webapp._broker_pnl(FakeHistoryClient(fail=True), {"equity": "9800"}, [], [])
    assert history is None and pnl["account_total"] is None and pnl["equity"] == 9800


def test_live_point_not_duplicated_when_today_is_in_history(monkeypatch):
    # az Alpaca napi pontja 00:00 UTC = elozo nap 20:00 NY: a 10-10 (pentek) es 10-13 (hetfo) zaro
    raw = {"timestamp": [1760140800, 1760400000], "equity": [10000, 10050], "base_value": 10000}
    monkeypatch.setattr(webapp.sessions, "now_ny", lambda: pd.Timestamp("2025-10-13 15:00", tz="America/New_York"))
    history, _ = webapp._broker_pnl(FakeHistoryClient(raw), {"equity": "10060"}, [], [])
    assert history["t"] == ["2025-10-10", "2025-10-13"] and not history["live"]      # nem szombat / kedd
    monkeypatch.setattr(webapp.sessions, "now_ny", lambda: pd.Timestamp("2025-10-14 11:00", tz="America/New_York"))
    history, _ = webapp._broker_pnl(FakeHistoryClient(raw), {"equity": "10060"}, [], [])
    assert history["t"][-1] == "2025-10-14" and history["equity"][-1] == 10060 and history["live"]
