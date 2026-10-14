"""Az indexosszetetel (Nasdaq-100 / S&P 500) es a hozza tartozo takaritas tesztjei - halozat nelkul."""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from stockwatch import housekeeping, indexes
from stockwatch.config import ScanSettings, Settings

TABLE = """<html><body>
<table class="wikitable" id="other"><tr><th>Year</th></tr><tr><td>2020</td></tr></table>
<table class="wikitable sortable" id="constituents">
<tr><th>Company</th><th>Ticker</th><th>Sector</th></tr>
{rows}
</table></body></html>"""


def html(tickers: list[str]) -> str:
    rows = "".join(f"<tr><td>Ceg {t}</td><td><a href='#'>{t}</a></td><td>Tech</td></tr>" for t in tickers)
    return TABLE.format(rows=rows)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = Settings(scan=ScanSettings(nasdaq100=True, sp500=False))
    monkeypatch.setattr(type(s), "index_members_path", property(lambda self: tmp_path / "members.json"))
    monkeypatch.setattr(type(s), "universe_dir", property(lambda self: tmp_path / "universe"))
    return s


def test_parse_constituents_reads_ticker_column_and_normalises():
    symbols = indexes.parse_constituents(html(["AAPL", "BRK.B", "msft", "AAPL", "bad ticker!"]), ("Ticker",))
    assert symbols == ["AAPL", "BRK-B", "MSFT"]              # duplikatum es szemet nelkul


def test_parse_rejects_page_without_table():
    with pytest.raises(ValueError, match="constituents"):
        indexes.parse_constituents("<html><table id='x'></table></html>", ("Ticker",))
    with pytest.raises(ValueError, match="ticker-oszlop"):
        indexes.parse_constituents(html(["AAPL"]), ("Symbol",))


def test_fetch_rejects_implausible_count(monkeypatch):
    class Resp:
        text = html(["AAPL", "MSFT"])

        def raise_for_status(self):
            pass

    monkeypatch.setattr(indexes.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ValueError, match="kozott lenne"):
        indexes.fetch("sp500")


def test_members_cached_and_fallback_on_error(settings, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(name):
        calls["n"] += 1
        return [f"T{i}" for i in range(100)]

    monkeypatch.setattr(indexes, "fetch", fake_fetch)
    first = indexes.IndexMembers(settings).symbols()
    assert len(first) == 100 and calls["n"] == 1
    assert indexes.IndexMembers(settings).symbols() == first and calls["n"] == 1   # cache-bol

    # elavult lista + halozati hiba -> a mentett lista marad
    raw = json.loads(settings.index_members_path.read_text())
    raw["nasdaq100"]["fetched"] = (date.today() - timedelta(days=30)).isoformat()
    settings.index_members_path.write_text(json.dumps(raw))

    def broken(name):
        raise requests.ConnectionError("nincs halozat")

    monkeypatch.setattr(indexes, "fetch", broken)
    members = indexes.IndexMembers(settings)
    assert members.symbols() == first
    assert "nincs halozat" in members.status()[0]["error"]


def test_only_enabled_indexes_are_used(settings, monkeypatch):
    monkeypatch.setattr(indexes, "fetch", lambda name: ["AAA"] if name == "nasdaq100" else ["BBB"])
    assert indexes.IndexMembers(settings).symbols() == ["AAA"]
    both = settings.model_copy(update={"scan": ScanSettings(nasdaq100=True, sp500=True)})
    assert indexes.IndexMembers(both).symbols() == ["AAA", "BBB"]


def test_prune_universe_removes_only_stale_unused_files(settings, monkeypatch):
    monkeypatch.setattr(indexes, "fetch", lambda name: ["KEEP"])
    indexes.IndexMembers(settings).symbols()
    folder = settings.universe_dir
    folder.mkdir()
    old = time.time() - 60 * 86400
    for name in ("KEEP", "QQQ", "AAPL", "GONE", "FRESH"):         # AAPL: kutatasi univerzum
        (folder / f"{name}.csv.gz").write_bytes(b"x")
    for name in ("KEEP", "QQQ", "AAPL", "GONE"):
        os.utime(folder / f"{name}.csv.gz", (old, old))
    assert housekeeping.prune_universe(settings) == 1
    left = sorted(p.name for p in folder.iterdir())
    assert left == ["AAPL.csv.gz", "FRESH.csv.gz", "KEEP.csv.gz", "QQQ.csv.gz"]


def test_prune_state_drops_ancient_active_entries(tmp_path, monkeypatch):
    s = Settings()
    monkeypatch.setattr(type(s), "state_path", property(lambda self: tmp_path / "state.json"))
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=housekeeping.ACTIVE_STATE_MAX_DAYS + 5)).isoformat()
    s.state_path.write_text(json.dumps({"signals": {
        "a": {"status": "forming", "first_seen": old, "updated_at": old},
        "b": {"status": "forming", "first_seen": now.isoformat(), "updated_at": now.isoformat()},
    }}))
    assert housekeeping.prune_state(s) == 1
    assert list(json.loads(s.state_path.read_text())["signals"]) == ["b"]


def test_data_usage_lists_index_files():
    labels = " ".join(u.label for u in housekeeping.disk_usage(Settings()))
    assert "index_members.json" in labels and "index_scan.json" in labels
