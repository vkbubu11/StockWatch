"""Szektorvalaszto a foldalon (#74): szektor-besorolas, top 3 long / short, rangsor, kisérleti short."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from stockwatch import best, sectors, webapp, webauth
from stockwatch.config import Settings
from stockwatch.forecast import Calibration


# ---------------------------------------------------------------------------
# Szektor-besorolas
# ---------------------------------------------------------------------------
@pytest.fixture()
def cache(tmp_path, monkeypatch):
    path = tmp_path / "sector_cache.json"
    monkeypatch.setattr(sectors, "cache_path", lambda: path)
    return path


def test_sector_map_uses_gics_then_yahoo_then_other(cache):
    asked = []

    def lookup(symbol):
        asked.append(symbol)
        return {"ASML": sectors.normalize("Technology")}.get(symbol)

    base = {"AAPL": "Information Technology", "JPM": "Financials"}
    out = sectors.sector_map(["AAPL", "JPM", "ASML", "ZZZZ"], lookup=lookup, base=base)
    assert out == {"AAPL": "Information Technology", "JPM": "Financials",
                   "ASML": "Information Technology", "ZZZZ": sectors.OTHER}
    assert asked == ["ASML", "ZZZZ"]                          # az S&P 500 tagokat nem kerdezi
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["ASML"]["sector"] == "Information Technology" and saved["ZZZZ"]["sector"] is None

    # masodszor: a talalat es a friss negativ valasz is a gyorsitotarbol jon, nincs uj lekeres
    asked.clear()
    again = sectors.sector_map(["ASML", "ZZZZ"], lookup=lookup, base=base)
    assert again == {"ASML": "Information Technology", "ZZZZ": sectors.OTHER} and asked == []

    # 30 nap utan a negativ valaszt ujra megkerdezi
    later = datetime.now(timezone.utc) + sectors.NEGATIVE_TTL + timedelta(days=1)
    sectors.sector_map(["ZZZZ"], lookup=lookup, base=base, now=later)
    assert asked == ["ZZZZ"]


def test_sector_map_without_network_never_looks_up(cache):
    out = sectors.sector_map(["ASML"], allow_network=False, base={})
    assert out == {"ASML": sectors.OTHER} and not cache.exists()


def test_sector_map_caps_lookups_per_call(cache, monkeypatch):
    monkeypatch.setattr(sectors, "MAX_LOOKUPS", 2)
    asked = []
    sectors.sector_map(["A", "B", "C"], lookup=lambda s: asked.append(s), base={})
    assert asked == ["A", "B"]


def test_normalize_and_members():
    assert sectors.normalize("Healthcare") == "Health Care"
    assert sectors.normalize("Consumer Cyclical") == "Consumer Discretionary"
    assert sectors.normalize("Health  Care") == "Health Care"
    assert sectors.normalize("Valami") is None and sectors.normalize(None) is None
    mapping = {"A": "Energy", "B": sectors.OTHER, "C": "Energy"}
    assert sectors.members("Energy", mapping) == ["A", "C"]
    assert sectors.members(sectors.OTHER, mapping) == ["B"]
    assert len(sectors.SECTORS) == 11


def test_etf_status():
    idx = pd.bdate_range("2024-01-01", periods=260, name="date")
    up = pd.DataFrame({"close": np.linspace(100, 150, 260)}, index=idx)
    st = sectors.etf_status(up)
    assert st["above_sma200"] is True and st["month_pct"] > 0
    down = pd.DataFrame({"close": np.linspace(150, 100, 260)}, index=idx)
    assert sectors.etf_status(down)["above_sma200"] is False
    short = pd.DataFrame({"close": np.linspace(100, 110, 60)}, index=idx[:60])
    assert sectors.etf_status(short)["above_sma200"] is None          # nincs 200 nap: nem tudjuk
    assert sectors.etf_status(None) is None


# ---------------------------------------------------------------------------
# Historia es rangsor
# ---------------------------------------------------------------------------
def calibration(**rows) -> Calibration:
    return Calibration({"patterns": rows})


def test_history_stats_needs_enough_samples_and_gives_intervals():
    cal = calibration(**{"Csesze|bullish": {"samples": 100, "hit_rate": 0.6, "expectancy_r": 0.4,
                                            "expectancy_sd": 1.0, "expectancy_n": 100},
                         "Zaszlo|bullish": {"samples": 5, "hit_rate": 0.8, "expectancy_r": 1.0}})
    h = best.history_stats(cal, "Csesze", "bullish", 30)
    assert h["hit_low"] < 0.6 < h["hit_high"]
    assert h["hit_low"] == pytest.approx(0.502, abs=0.01) and h["hit_high"] == pytest.approx(0.691, abs=0.01)
    assert h["ev_low"] == pytest.approx(0.4 - 1.96 * 0.1, abs=1e-3)
    assert best.history_stats(cal, "Zaszlo", "bullish", 30) is None         # nincs eleg minta: nincs szam
    assert best.history_stats(cal, "Nincs", "bullish", 30) is None
    old = calibration(**{"Csesze|bullish": {"samples": 100, "hit_rate": 0.6, "expectancy_r": 0.4}})
    assert best.history_stats(old, "Csesze", "bullish", 30)["ev_low"] is None   # regi kalibracio: sav nelkul


def _c(symbol, score, ev_low=None, qualifies=True, kind="breakout", failed=()):
    return {"symbol": symbol, "score": score, "qualifies": qualifies, "kind": kind, "failed": list(failed),
            "hist": None if ev_low is None else {"ev_low": ev_low}}


def test_rank_ev_lower_bound_decides_within_five_points():
    ranked = best.rank([_c("A", 80, ev_low=-0.1), _c("B", 77, ev_low=0.2), _c("C", 70, ev_low=0.9)])
    assert [d["symbol"] for d in ranked] == ["B", "A", "C"]   # C 10 ponttal lemarad: a nagy EV sem hozza elore


def test_rank_missing_history_goes_behind_within_margin_only():
    ranked = best.rank([_c("A", 80), _c("B", 76, ev_low=-0.5), _c("C", 60, ev_low=1.0)])
    assert [d["symbol"] for d in ranked] == ["B", "A", "C"]
    assert [d["symbol"] for d in best.rank([_c("A", 90), _c("B", 70, ev_low=2.0)])] == ["A", "B"]


def test_side_top_three_never_padded_with_weaker():
    items = [_c("A", 70), _c("B", 65), _c("S", 99, kind="setup"), _c("N", 98, qualifies=False, failed=["Trend"])]
    side = best._side(items, top=3)
    assert [d["symbol"] for d in side["top"]] == ["A", "B"]   # csak ket belepo: nincs kitoltes
    assert side["best"]["symbol"] == "A" and side["others"] == []
    assert [d["symbol"] for d in side["setups"]] == ["S"] and [d["symbol"] for d in side["near"]] == ["N"]
    many = best._side([_c(s, 90 - 10 * k) for k, s in enumerate("ABCDE")], top=3)
    assert [d["symbol"] for d in many["top"]] == ["A", "B", "C"]
    assert [d["symbol"] for d in many["others"]] == ["D", "E"]


# ---------------------------------------------------------------------------
# find_best szektorral
# ---------------------------------------------------------------------------
def _bench(up: bool) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=260, name="date")
    close = np.linspace(100, 150, 260) if up else np.linspace(150, 100, 260)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1e6}, index=idx)


class _Ctx:
    def __init__(self, up: bool):
        self.benches = {"QQQ": _bench(up), "^GSPC": _bench(up)}


@pytest.fixture()
def offline(monkeypatch):
    """A find_best halozat es adat nelkul: a papirok listaja es a szektoruk a teszt adja."""
    seen = {}
    monkeypatch.setattr(best, "list_symbols", lambda name, settings, allow_network=True: ["A1", "E1", "A2", "X1"])
    monkeypatch.setattr(best.sectors, "sector_map", lambda symbols, allow_network=False: {
        "A1": "Information Technology", "E1": "Energy", "A2": "Information Technology", "X1": sectors.OTHER})

    def frame(symbol, settings):
        seen.setdefault("frames", []).append(symbol)
        return pd.DataFrame()                                  # ures: az elemzes kihagyja

    monkeypatch.setattr(best, "_frame", frame)
    monkeypatch.setattr(best, "_refresh", lambda symbols, settings: None)
    monkeypatch.setattr(best.market, "EarningsCalendar", lambda settings, allow_network=False: None)
    monkeypatch.setattr(best.monitor, "is_market_open", lambda: False)
    monkeypatch.setattr(best.store, "load", lambda symbol, settings=None, universe=False: _bench(True))
    return seen


def test_find_best_filters_to_sector_and_reports_bull_market(offline, monkeypatch):
    monkeypatch.setattr(best.market, "load_context", lambda settings, refresh=True: _Ctx(up=True))
    r = best.find_best(Settings(), refresh=False, sector="Information Technology")
    assert offline["frames"] == ["A1", "A2"]                   # csak a szektor tagjai
    assert r["sector"] == "Information Technology" and r["top_n"] == 3 and r["symbols"] == 2
    assert r["bull_market"] is True and r["short_blocked"] is True   # bikapiac, nincs short jelolt
    assert r["long"]["top"] == [] and r["short"]["top"] == []
    assert r["etf"]["symbol"] == "XLK" and r["etf"]["above_sma200"] is True


def test_find_best_other_sector_and_bear_market(offline, monkeypatch):
    monkeypatch.setattr(best.market, "load_context", lambda settings, refresh=True: _Ctx(up=False))
    r = best.find_best(Settings(), refresh=False, sector=sectors.OTHER)
    assert offline["frames"] == ["X1"] and r["etf"] is None
    assert r["bull_market"] is False and r["short_blocked"] is False


def test_find_best_rejects_unknown_sector(offline):
    with pytest.raises(ValueError):
        best.find_best(Settings(), refresh=False, sector="Valami")


def test_find_best_without_sector_keeps_old_shape(offline, monkeypatch):
    monkeypatch.setattr(best.market, "load_context", lambda settings, refresh=True: _Ctx(up=True))
    r = best.find_best(Settings(), refresh=False)
    assert offline["frames"] == ["A1", "E1", "A2", "X1"] and r["sector"] is None and r["top_n"] == 1
    assert set(r["long"]) >= {"best", "others", "setups", "near", "count", "top"}


# ---------------------------------------------------------------------------
# Webes vegpontok
# ---------------------------------------------------------------------------
@pytest.fixture()
def server():
    app = webapp.AppState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(app, webauth.Auth(password_hash=lambda: None)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", app
    httpd.shutdown()
    httpd.server_close()


def _call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data,
                                 headers={"X-StockWatch": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_post_best_rejects_unknown_sector(server, monkeypatch):
    url, app = server
    monkeypatch.setattr(app, "start_best", lambda sector=None: pytest.fail("nem indulhat el"))
    status, _ = _call(f"{url}/api/best", "POST", {"sector": "Valami"})
    assert status == 400


def test_get_best_returns_the_sectors_own_result(server):
    url, app = server
    app.best = {"sector": None}
    app.best_by_sector["Energy"] = {"sector": "Energy"}
    assert _call(f"{url}/api/best?sector=Energy")[1]["result"] == {"sector": "Energy"}
    assert _call(f"{url}/api/best?sector=Financials")[1]["result"] is None
    assert _call(f"{url}/api/best")[1]["result"] == {"sector": None}


def test_sectors_endpoint_is_offline_and_cached(server, monkeypatch):
    url, app = server
    calls = []
    monkeypatch.setattr(best, "list_symbols", lambda name, settings, allow_network=True: calls.append(allow_network) or ["A"])
    monkeypatch.setattr(sectors, "overview", lambda settings, symbols, allow_network=False: {
        "sectors": [{"sector": "Energy", "etf": "XLE", "count": 1, "etf_status": None}], "symbols": 1})
    status, body = _call(f"{url}/api/sectors")
    assert status == 200 and body["sectors"][0]["sector"] == "Energy" and calls == [False]
    _call(f"{url}/api/sectors")
    assert calls == [False]                                    # masodszor a gyorsitotarbol
