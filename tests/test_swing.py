"""Swing fül: szabalyok (setup, kezeles), meretezes, kotesnaplo, a webes vegpontok - halozat nelkul."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from stockwatch import webapp
from stockwatch.swing import core as sc
from stockwatch.swing import service as swing


# ---------------------------------------------------------------------------
# Szintetikus gyertyak
def _days(n, start=date(2024, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes, spread=0.5, vols=None):
    ds = _days(len(closes))
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append({"d": ds[i], "o": round(o, 4), "h": round(max(o, c) + spread, 4), "l": round(min(o, c) - spread, 4),
                    "c": round(c, 4), "v": int(vols[i] if vols else 2_000_000)})
    return out


def pullback_series():
    closes = [50 + 0.2 * t + 0.6 * math.sin(t / 3) for t in range(250)]
    closes += [closes[-1] - 0.55 * k for k in range(1, 7)]
    return _bars(closes, vols=[2_000_000] * 250 + [1_400_000] * 6)


def breakout_series():
    closes = [40 + 0.2 * t for t in range(230)]
    top = closes[-1]
    closes += [top - (3.0 if k < 10 else 0.8) + (3.0 if k < 10 else 0.8) * math.sin(k) for k in range(20)]
    return _bars(closes, spread=0.6, vols=[2_000_000] * 230 + [2_200_000] * 10 + [1_000_000] * 10)


CFG = dict(sc.DEFAULT_CONFIG)


@pytest.fixture(autouse=True)
def _isolated_swing(tmp_path, monkeypatch):
    monkeypatch.setattr(swing, "SWING_DIR", tmp_path / "swing")
    yield
    sc.BASE_DIR = None


# ---------------------------------------------------------------------------
# Szabalyok
def test_indicators_basic():
    assert sc.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert sc.rsi([float(x) for x in range(30)], 14)[-1] == 100.0
    flat = [{"d": str(i), "o": 10, "h": 11, "l": 9, "c": 10, "v": 1} for i in range(30)]
    assert sc.atr(flat, 14)[-1] == pytest.approx(2.0)


def test_pullback_and_breakout_detected_with_levels():
    b = pullback_series()
    s = sc.detect(b, sc.indicators(b), len(b) - 1, CFG)
    assert [x["setup"] for x in s] == ["pullback"]
    assert s[0]["entry"] > b[-1]["h"] and s[0]["stop"] < min(x["l"] for x in b[-5:])
    assert (s[0]["t1"] - s[0]["entry"]) / s[0]["risk_per_share"] == pytest.approx(2.0, abs=0.02)
    b = breakout_series()
    s = sc.detect(b, sc.indicators(b), len(b) - 1, CFG)
    assert [x["setup"] for x in s] == ["breakout"]
    assert s[0]["entry"] > max(x["h"] for x in b[-20:])


def test_no_look_ahead():
    b = pullback_series()
    i = len(b) - 1
    future = b + [{"d": "2099-01-01", "o": 1, "h": 500, "l": 1, "c": 300, "v": 1}]
    assert sc.detect(b, sc.indicators(b), i, CFG) == sc.detect(future, sc.indicators(future), i, CFG)


def _bar(d, o, h, l, c):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": 1}


SIGNAL = {"setup": "pullback", "date": "x", "entry": 100.0, "limit": 100.5, "stop": 95.0, "t1": 110.0, "t2": 115.0, "atr": 2.0}


@pytest.mark.parametrize("path, r, last", [
    ([_bar("1", 99, 100.2, 98, 99.5), _bar("2", 99, 99.5, 94, 95)], -1.0, "stop"),
    ([_bar("1", 99, 100.2, 98, 99.5), _bar("2", 90, 92, 89, 91)], -2.0, "stop"),                 # res: a nyitason
    ([_bar("1", 99, 100.2, 99, 100), _bar("2", 101, 110.5, 101, 103), _bar("3", 103, 103.5, 99, 99.5)], 1.0, "breakeven"),
    ([_bar("1", 99, 100.2, 99, 100), _bar("2", 101, 110.5, 101, 109), _bar("3", 108, 108.5, 99, 99.5)], 1.5, "trail"),
    ([_bar("1", 99, 100.2, 99, 100), _bar("2", 101, 110.5, 101, 109), _bar("3", 109, 116, 108, 115)], 2.5, "t2"),
    ([_bar("1", 99, 100.2, 99, 100), _bar("2", 100, 111, 94, 105)], -1.0, "stop"),              # stop a cel elott
])
def test_trade_management(path, r, last):
    t = sc.simulate([_bar("s", 99, 99.5, 98, 99)] + path, 0, SIGNAL, CFG)
    assert sc.position_r(t) == pytest.approx(r)
    assert [e["reason"] for e in t["events"] if "qty" in e][-1] == last


def test_cancelled_orders():
    assert sc.simulate([_bar("s", 99, 99.5, 98, 99), _bar("1", 101, 103, 100.8, 102)], 0, SIGNAL, CFG) is None
    assert sc.simulate([_bar("s", 99, 99.5, 98, 99), _bar("1", 98, 99, 94, 95), _bar("2", 96, 101, 96, 100)], 0, SIGNAL, CFG) is None


def test_sessions_skip_nyse_holidays():
    assert [d.isoformat() for d in sc.next_sessions(date(2026, 11, 25), 2)] == ["2026-11-27", "2026-11-30"]


# ---------------------------------------------------------------------------
# Szolgaltatas: beallitas, szures, terv, naplo
def test_config_defaults_from_entry_and_validation():
    cfg = swing.config()
    assert cfg["account_usd"] > 0 and cfg["risk_pct"] > 0
    out = swing.save_config({"account_usd": 8000, "risk_pct": 0.5, "setups": ["pullback"]})
    assert (out["account_usd"], out["risk_pct"], out["setups"]) == (8000.0, 0.5, ["pullback"])
    with pytest.raises(ValueError):
        swing.save_config({"telegram_token": "x"})
    with pytest.raises(ValueError):
        swing.save_config({"risk_pct": 50})
    with pytest.raises(ValueError):
        swing.save_config({"setups": ["nincs-ilyen"]})


@pytest.fixture
def offline(monkeypatch):
    data = {"PB": pullback_series(), "BO": breakout_series(),
            "DN": _bars([150 - 0.2 * t for t in range(260)]), "QQQ": pullback_series()}
    monkeypatch.setattr(sc, "get_bars", lambda sym, rng="1y", refresh=False: data[sym])
    monkeypatch.setattr(sc, "load_universe", lambda cfg, refresh=False, log=print: [
        {"symbol": s, "name": s, "market_cap": 1e10, "sector": ""} for s in ("PB", "BO", "DN")])
    monkeypatch.setattr(sc, "earnings_calendar", lambda days, log=print: {"BO": "2026-11-12"})
    return data


def test_scan_plan_add_update_close(offline):
    msgs = []
    swing.save_config({"account_usd": 10000, "risk_pct": 1.0})
    swing.scan(300, progress=msgs.append, workers=1)
    p = swing.payload()
    got = {c["symbol"]: c for c in p["scan"]["candidates"]}
    assert set(got) == {"PB", "BO"} and got["BO"]["earnings"] == "2026-11-12"
    assert p["scan"]["regime"]["state"] == "up" and msgs

    plan = swing.plan("PB")
    c = got["PB"]
    assert plan["qty"] == min(math.floor(100 / (c["entry"] - c["stop"])), math.floor(2500 / c["limit"]))
    assert plan["tickets"][0].startswith(f"VÉTEL {plan['qty']} PB STOP-LIMIT")

    swing.add_trade({"symbol": "PB", "qty": plan["qty"], "fill": c["entry"], "date": offline["PB"][-1]["d"]})
    with pytest.raises(ValueError):
        swing.plan("PB")                                   # mar nyitott
    with pytest.raises(ValueError):
        swing.add_trade({"symbol": "PB", "qty": 1, "fill": 1})

    # uj napok: T1 felett zar, majd visszaesik a breakeven ala
    last = offline["PB"][-1]["d"]
    d1, d2 = (date.fromisoformat(last) + timedelta(days=k) for k in (3, 4))
    up = c["t1"] + 0.5
    offline["PB"] = offline["PB"] + [_bar(d1.isoformat(), c["entry"] + 0.5, up, c["entry"] + 0.2, c["entry"] + 1.0),
                                     _bar(d2.isoformat(), c["entry"], c["entry"] + 0.2, c["entry"] - 1.0, c["entry"] - 0.5)]
    rep = swing.update_trades()["report"][0]
    assert any("T1" in a for a in rep["actions"])
    t = swing.journal()["trades"][0]
    assert t["t1_hit"] and t["status"] == "closed"
    assert swing.payload()["stats"]["closed"] == 1


def test_manual_close_and_delete(offline):
    swing.add_trade({"symbol": "XYZ", "qty": 10, "fill": 20, "stop": 19, "date": "2026-11-02"})
    with pytest.raises(ValueError):
        swing.close_trade({"symbol": "XYZ", "price": 21, "qty": 11})
    swing.close_trade({"symbol": "XYZ", "price": 21, "qty": 4})
    t = swing.journal()["trades"][0]
    assert t["remaining"] == 6 and t["status"] == "open"
    swing.close_trade({"symbol": "XYZ", "price": 22})
    t = swing.journal()["trades"][0]
    assert t["status"] == "closed" and t["r"] == pytest.approx((4 * 1 + 6 * 2) / 10)
    swing.delete_trade({"symbol": "XYZ", "opened": "2026-11-02"})
    assert swing.journal()["trades"] == []


def test_add_trade_validation():
    with pytest.raises(ValueError):
        swing.add_trade({"symbol": "bad ticker", "qty": 1, "fill": 1, "stop": 0.5})
    with pytest.raises(ValueError):
        swing.add_trade({"symbol": "XYZ", "qty": 1, "fill": 10, "stop": 11})
    with pytest.raises(ValueError):
        swing.add_trade({"symbol": "XYZ", "qty": -1, "fill": 10, "stop": 9})


def test_backtest_runs_offline(offline):
    b = pullback_series()
    last = b[-1]["c"]
    b += [{"d": f"2099-02-{k:02d}", "o": last + 0.8 * k - 0.6, "h": last + 0.8 * k + 0.3, "l": last + 0.8 * k - 0.9,
           "c": last + 0.8 * k, "v": 3_000_000} for k in range(1, 25)]
    offline["PB"] = b
    swing.backtest(50, "5y", workers=1)
    out = swing.payload()["backtest"]
    assert out["all"]["trades"] >= 1 and "pullback" in out["by_setup"]
    with pytest.raises(ValueError):
        swing.backtest(50, "1d")


# ---------------------------------------------------------------------------
# Webes vegpontok
def test_webapp_swing_actions(offline):
    p = webapp.swing_payload()
    assert p["job"]["state"] in ("idle", "done") and p["open"] == []
    with pytest.raises(ValueError):
        webapp.swing_action("scan", {"max": 7})
    with pytest.raises(ValueError):
        webapp.swing_action("nope", {})
    with pytest.raises(ValueError):
        webapp.swing_action("plan", {"symbol": "<script>"})
    out = webapp.swing_action("config", {"values": {"account_usd": 5000}})
    assert out["config"]["account_usd"] == 5000.0
    out = webapp.swing_action("add", {"symbol": "XYZ", "qty": 3, "fill": 50, "stop": 48})
    assert out["open"][0]["symbol"] == "XYZ" and "brókernél" in out["message"]
