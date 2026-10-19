"""Kereskedesi naptar (#22): napi realizalt eredmeny forrasonkent.

  * a reszleges kilepes a sajat napjan konyvelodik (virtualis kovetes: TrackedPosition.legs)
  * paper: a labak osszege = a broker._maybe_close realised_pnl-je (ugyanaz a dij-keplet)
  * a heti es havi osszeg a napi ertekek osszege; kotes nelkuli nap, hetvege, unnepnap
  * szurok; a modul nem eri el a brokert; API es CSV
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from stockwatch import broker, pnl_calendar, positions, webapp, webauth
from stockwatch.config import Settings

from .guards import BROKER_REACHING, import_closure
from .test_entry import _future, _signal, _tracker, enrich, lenient, uptrend


def _pos(pid, **kw):
    base = {"id": pid, "symbol": "AAA", "pattern": "x", "signal_date": "2026-03-02", "entry_limit": 101.0,
            "planned_entry": 100.0, "stop": 95.0, "target": 115.0, "atr": 2.0, "shares": 10, "horizon": 20,
            "exit_mode": "partial", "partial_r": 1.0, "trail_atr": 3.0, "status": "closed", "entry_price": 100.0,
            "state": None, "source": "virtual", "strategy": "breakout"}
    return base | kw


# ---------------------------------------------------------------------------
# Forrasok
# ---------------------------------------------------------------------------
def test_virtual_partial_leg_is_booked_on_its_own_day():
    legs = [{"date": "2026-03-04", "fraction": 0.5, "r": 0.5}, {"date": "2026-03-09", "fraction": 0.5, "r": 1.0}]
    out = pnl_calendar.virtual_entries({"p1": _pos("p1", legs=legs, result_r=1.5, closed_at="2026-03-09")})
    assert [(e.date, e.kind, e.r, e.pnl) for e in out] == [("2026-03-04", "partial", 0.5, 25.0),
                                                            ("2026-03-09", "final", 1.0, 50.0)]
    assert sum(e.r for e in out) == pytest.approx(1.5) and all(e.simulated for e in out)


def test_virtual_old_positions_and_pullback_percent():
    old = _pos("p2", result_r=-1.0, closed_at="2026-03-06")                          # lab-adat nelkul
    pull = _pos("p3", strategy="pullback", result_pct=2.5, result_r=0.8, closed_at="2026-03-06", shares=4)
    skipped = _pos("p4", status="skipped", closed_at="2026-03-06")
    alpaca = _pos("p5", source="alpaca", result_r=1.0, closed_at="2026-03-06")       # a paper forrasban szamit
    out = {e.trade_id: e for e in pnl_calendar.virtual_entries({"p2": old, "p3": pull, "p4": skipped, "p5": alpaca})}
    assert set(out) == {"p2", "p3"}
    assert out["p2"].pnl == -50.0 and out["p3"].pnl == pytest.approx(4 * 100 * 0.025)


def test_tracker_records_the_partial_leg(tmp_path, monkeypatch):
    settings, tracker = _tracker(tmp_path, monkeypatch)
    df = enrich(uptrend())
    cfg = lenient()
    cfg.exit_mode = "partial"
    signal = _signal(df, cfg)
    pos = tracker.register(signal)
    close, risk = signal.price, signal.plan.risk_per_share
    e = close * 1.002
    later = _future(df, [(e, e * 1.003, e * 0.998, e * 1.001),                  # belepes
                         (e + 0.02, e + risk * 1.2, e + 0.01, e + risk),            # +1R -> a fele, a belepo felett marad
                         (e + risk, e + risk, e - 0.1, e - 0.05)])                  # masnap vissza -> nullszaldo
    tracker.update("TEST", later)
    days = [f"{d:%Y-%m-%d}" for d in later.index[-2:]]
    assert pos.status == "closed" and [leg["date"] for leg in pos.legs] == days
    assert sum(leg["r"] for leg in pos.legs) == pytest.approx(pos.result_r, abs=1e-3)
    assert sum(leg["fraction"] for leg in pos.legs) == pytest.approx(1.0)
    tracker.save()
    stored = json.loads(settings.positions_path.read_text())["positions"]
    entries = pnl_calendar.virtual_entries(stored)
    assert [x.date for x in entries] == days and [x.kind for x in entries] == ["partial", "final"]


def test_paper_legs_sum_to_the_broker_result():
    legs = [broker.Leg("partial", 5, 110.0, entry_status="filled", filled_qty=5, entry_fill=100.2, exit_price=110.0,
                       exit_reason="take_profit", exit_ts="2026-03-04T18:30:00Z"),
            broker.Leg("target", 5, 120.0, entry_status="filled", filled_qty=5, entry_fill=100.2, exit_price=100.2,
                       exit_reason="stop", exit_ts="2026-03-10T01:30:00Z")]                # este UTC = elozo NY nap
    trade = broker.BrokerTrade("t1", "BBB", "x", "breakout", "2026-03-02", 101.0, 95.0, 120.0, 110.0, 10, 20,
                               "partial", status="open", entry_price=100.2, commission_pct=0.1)
    trade.set_legs(legs)
    broker._maybe_close(trade, trade.leg_objs(), [])
    entries = pnl_calendar.paper_entries({"t1": json.loads(json.dumps(trade.__dict__))})
    assert [e.date for e in entries] == ["2026-03-04", "2026-03-09"] and [e.kind for e in entries] == ["partial", "final"]
    assert sum(e.pnl for e in entries) == pytest.approx(trade.realised_pnl, abs=0.011)
    assert entries[0].r == pytest.approx(entries[0].pnl / ((100.2 - 95.0) * 10), abs=1e-3)


def test_holdings_and_backtest_sources():
    h = {"h1": {"id": "h1", "symbol": "CCC", "entry_price": 50.0, "entry_date": "2026-02-02", "shares": 3, "stop": 45.0,
                "status": "closed", "closed_at": "2026-03-05", "close_price": 60.0},
         "h2": {"id": "h2", "symbol": "DDD", "entry_price": 50.0, "entry_date": "2026-02-02", "shares": 3, "status": "open"}}
    out = pnl_calendar.holding_entries(h)
    assert [(e.date, e.pnl, e.r, e.simulated) for e in out] == [("2026-03-05", 30.0, 2.0, False)]
    run = {"id": "abc", "symbol": "SPY", "strategy": "deep", "capital": 10000, "alloc_pct": 50,
           "trades": [{"status": "closed", "exit_date": "2026-03-05", "net_pct": 2.0, "r": 0.7},
                      {"status": "open", "exit_date": None, "net_pct": None}]}
    out = pnl_calendar.backtest_entries(run)
    assert [(e.date, e.pnl, e.r) for e in out] == [("2026-03-05", 100.0, 0.7)]


# ---------------------------------------------------------------------------
# Osszesites
# ---------------------------------------------------------------------------
def _entries():
    E = pnl_calendar.Entry
    return [E("2026-11-02", "virtual", "a", "AAA", "breakout", 100.0, 1.0, "final", True),
            E("2026-11-02", "paper", "b", "BBB", "pullback", -40.0, -0.5, "final", True),
            E("2026-11-04", "virtual", "c", "AAA", "breakout", 25.0, 0.5, "partial", True),
            E("2026-11-13", "holdings", "d", "CCC", "own", -60.0, None, "final", False),
            E("2026-11-30", "virtual", "e", "EEE", "breakout", None, 0.3, "final", True),
            E("2026-12-01", "virtual", "f", "AAA", "breakout", 999.0, 1.0, "final", True)]


def test_month_totals_are_the_sum_of_days():
    m = pnl_calendar.month(Settings(), 2026, 11, entries=_entries())
    cells = [c for w in m["weeks"] for c in w["days"] if c["in_month"] and c["value"]]
    assert {c["date"] for c in cells} == {"2026-11-02", "2026-11-04", "2026-11-13", "2026-11-30"}
    assert m["total"]["pnl"] == pytest.approx(sum(c["value"]["pnl"] or 0 for c in cells)) == 25.0
    assert m["total"]["r"] == pytest.approx(1.3) and m["total"]["trades"] == 4
    assert m["total"]["days_up"] == 2 and m["total"]["days_down"] == 1
    assert m["total"]["best"] == {"date": "2026-11-02", "pnl": 60.0} and m["total"]["worst"]["date"] == "2026-11-13"
    for w in m["weeks"]:
        inside = [c["value"] for c in w["days"] if c["in_month"] and c["value"]]
        assert (w["total"]["pnl"] or 0) == pytest.approx(sum(v["pnl"] or 0 for v in inside))
    day = next(c for w in m["weeks"] for c in w["days"] if c["date"] == "2026-11-02")["value"]
    assert day["pnl"] == 60.0 and day["pct"] == pytest.approx(60 / Settings().entry.account_size * 100, abs=1e-3)


def test_weekends_holidays_and_empty_days():
    m = pnl_calendar.month(Settings(), 2026, 11, entries=_entries())
    days = {c["date"]: c for w in m["weeks"] for c in w["days"]}
    assert all(pd.Timestamp(d).weekday() < 5 for d in days)                 # hetvege nincs a racsban
    assert days["2026-11-26"]["holiday"] and not days["2026-11-26"]["trading"]   # halaadas napja
    assert days["2026-11-03"]["value"] is None and days["2026-11-03"]["trading"]
    assert not days["2026-12-01"]["in_month"]                                # a szomszed honap nem szamit bele
    assert m["total"]["pnl"] == 25.0


def test_filters():
    only = pnl_calendar.month(Settings(), 2026, 11, strategy="breakout", entries=_entries())
    assert only["total"]["pnl"] == 125.0
    sym = pnl_calendar.month(Settings(), 2026, 11, symbol="ccc", entries=_entries())
    assert sym["total"]["pnl"] == -60.0
    with pytest.raises(ValueError):
        pnl_calendar.parse_sources("nincs")
    assert pnl_calendar.parse_sources("paper,holdings") == ("paper", "holdings")


def test_calendar_does_not_reach_the_broker():
    reach = import_closure("stockwatch.pnl_calendar")
    assert not reach & set(BROKER_REACHING), reach & set(BROKER_REACHING)


# ---------------------------------------------------------------------------
# API, CSV
# ---------------------------------------------------------------------------
@pytest.fixture
def server(tmp_path, monkeypatch):
    settings = Settings()
    for name, key, payload in (("positions_path", "positions", {"p1": _pos("p1", legs=[
            {"date": "2026-03-04", "fraction": 0.5, "r": 0.5}, {"date": "2026-03-09", "fraction": 0.5, "r": 1.0}],
            result_r=1.5, closed_at="2026-03-09")}), ("holdings_path", "holdings", {}), ("broker_path", "trades", {})):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({key: payload}))
        monkeypatch.setattr(Settings, name, property(lambda self, p=path: p))
    monkeypatch.setattr(webapp, "load_settings", lambda: settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(webapp.AppState(), webauth.Auth(password_hash=lambda: None)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    req = urllib.request.Request(url, headers={"X-StockWatch": "1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_api_month_day_year_and_csv(server):
    status, text = _get(server + "/api/calendar?year=2026&month=3")
    m = json.loads(text)
    assert status == 200 and m["total"]["pnl"] == 75.0 and m["available"]["virtual"] == 2 and m["simulated"]
    status, text = _get(server + "/api/calendar/day/2026-03-04")
    assert status == 200 and json.loads(text)["entries"][0]["kind"] == "partial"
    status, text = _get(server + "/api/calendar/year?year=2026")
    y = json.loads(text)
    assert status == 200 and y["months"][2]["total"]["pnl"] == 75.0 and len(y["months"]) == 12
    status, text = _get(server + "/api/calendar.csv")
    lines = text.strip().splitlines()
    assert status == 200 and lines[0].startswith("date,pnl_usd") and lines[1].startswith("2026-03-04,25.0")
    assert _get(server + "/api/calendar?sources=nincs")[0] == 400
    assert _get(server + "/api/calendar/day/2026-3-4")[0] == 400
    assert _get(server + "/api/calendar?symbol=../x")[0] == 400
