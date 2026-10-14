"""A helyi webes felulet tesztjei: YAML-szerkesztes, JSON, HTTP-vedelmek."""
from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from stockwatch import webapp, webauth


def test_set_yaml_key_keeps_comments_and_sections():
    lines = [
        "alerts:",
        "  pattern_events: false",
        "entry:",
        "  min_score: 50             # 0-100; a pontszam",
        "  account_size: 10000",
        "",
        "# --- kovetkezo ---",
        "other:",
        "  min_score: 1",
    ]
    lines = webapp._set_yaml_key(lines, "entry", "min_score", "65")
    lines = webapp._set_yaml_key(lines, "entry", "exit_mode", "trail")
    lines = webapp._set_yaml_key(lines, "alerts", "pattern_events", "true")
    text = "\n".join(lines)
    assert "  min_score: 65" in text and "# 0-100; a pontszam" in text
    assert "  min_score: 1" in text                       # a masik szekcio erintetlen
    assert text.index("exit_mode: trail") < text.index("# --- kovetkezo ---")
    assert "pattern_events: true" in text


def test_clean_handles_pandas_and_nan():
    out = webapp._clean({"a": np.float64("nan"), "b": np.int64(3), "c": pd.Timestamp("2025-06-03"),
                         "d": np.bool_(True), "e": [math.inf]})
    assert out == {"a": None, "b": 3, "c": "2025-06-03", "d": True, "e": [None]}
    json.dumps(out)


def test_update_settings_rejects_unknown_and_invalid():
    with pytest.raises(ValueError):
        webapp.update_settings({"telegram_token": "x"})
    with pytest.raises(ValueError):
        webapp.update_settings({"risk_per_trade_pct": 50})


def test_watchlist_validation():
    with pytest.raises(ValueError):
        webapp.update_watchlist(["AAPL", "<script>"])
    with pytest.raises(ValueError):
        webapp.update_watchlist([])


@pytest.fixture()
def server():
    no_password = webauth.Auth(password_hash=lambda: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(webapp.AppState(), no_password))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _status(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers


def test_http_security(server):
    status, headers = _status(server + "/")
    assert status == 200 and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    # POST egyedi fejlec nelkul: tiltva (CSRF ellen)
    assert _status(server + "/api/refresh", "POST", data=b"{}")[0] == 403
    # ervenytelen ticker, utvonal-bejaras
    assert _status(server + "/api/symbol/..%2F..%2Fetc")[0] == 400
    assert _status(server + "/%2e%2e/config/settings.yaml")[0] == 404
    assert _status(server + "/nincs-ilyen.js")[0] == 404


# ---------------------------------------------------------------------------
# Indexszkenneles: bekapcsolaskor azonnal, utana minden zaras utan
# ---------------------------------------------------------------------------
from stockwatch.config import ScanSettings, Settings  # noqa: E402


def _ny(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="America/New_York")


@pytest.fixture()
def scan_settings(tmp_path, monkeypatch):
    s = Settings(scan=ScanSettings(sp500=True))
    monkeypatch.setattr(type(s), "index_scan_path", property(lambda self: tmp_path / "index_scan.json"))
    return s


def test_close_scan_due_only_after_close_on_weekdays(scan_settings):
    assert webapp.index_scan_due(scan_settings, _ny("2026-10-12 16:25"))          # hetfo zaras utan
    assert not webapp.index_scan_due(scan_settings, _ny("2026-10-12 15:59"))      # meg nyitva
    assert not webapp.index_scan_due(scan_settings, _ny("2026-10-11 17:00"))      # vasarnap
    off = scan_settings.model_copy(update={"scan": ScanSettings()})
    assert not webapp.index_scan_due(off, _ny("2026-10-12 16:25"))                # nincs index


def test_close_scan_not_repeated_after_todays_run(scan_settings):
    path = scan_settings.index_scan_path
    path.write_text(json.dumps({"computed_at": "2026-10-12T20:17:00+00:00"}))      # 16:17 NY: a monitor mar futott
    assert not webapp.index_scan_due(scan_settings, _ny("2026-10-12 16:25"))
    path.write_text(json.dumps({"computed_at": "2026-10-09T20:17:00+00:00"}))      # pentek: elavult
    assert webapp.index_scan_due(scan_settings, _ny("2026-10-12 16:25"))


def test_enabling_an_index_starts_scan_immediately(monkeypatch):
    state = {"scan": ScanSettings()}
    monkeypatch.setattr(webapp, "load_settings", lambda: Settings(scan=state["scan"]))

    def fake_update(values):
        state["scan"] = ScanSettings(**{**state["scan"].model_dump(), **values})
        return {"values": values}

    started = []
    monkeypatch.setattr(webapp, "update_settings", fake_update)
    app = webapp.AppState()
    monkeypatch.setattr(app, "start_index_scan", lambda alerts=False: started.append(alerts) or True)

    assert "index_scan_started" not in webapp.apply_settings(app, {"index_setups": True})
    res = webapp.apply_settings(app, {"sp500": True})
    assert res["index_scan_started"] and started == [True]                       # riasztassal
    webapp.apply_settings(app, {"sp500": True})                                   # mar be volt kapcsolva
    assert started == [True]
