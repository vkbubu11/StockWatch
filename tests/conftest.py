"""Kozos teszt-beallitas: a tesztek SOHA nem irhatnak a valodi jelzesnaploba (data/ledger)."""
from __future__ import annotations

import pytest

from stockwatch import ledger


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    base = tmp_path / "ledger"
    monkeypatch.setattr(ledger, "LEDGER_DIR", base)
    monkeypatch.setattr(ledger, "SNAPSHOT_DIR", base / "snapshots")
    monkeypatch.setattr(ledger, "CONFIG_STORE", base / "configs")
    monkeypatch.setattr(ledger, "CALIBRATION_STORE", base / "calibrations")
    monkeypatch.setattr(ledger, "CHANGES_PATH", base / "config_changes.jsonl")
    return base


@pytest.fixture(autouse=True)
def _isolated_broker_book(tmp_path, monkeypatch):
    """A kovetes betolteskor a paper nyilvantartast olvassa - a tesztek a valodit soha."""
    from stockwatch.config import Settings

    monkeypatch.setattr(Settings, "broker_path", property(lambda self: tmp_path / "broker-isolated.json"))


@pytest.fixture(autouse=True)
def _isolated_news(tmp_path, monkeypatch):
    """A tesztek nem mennek halozatra hirekert, es nem irnak a valodi hirtarba (data/news)."""
    from stockwatch.sentiment import service, store

    monkeypatch.setattr(store, "NEWS_DIR", tmp_path / "news")
    monkeypatch.setattr(service, "refresh", lambda *a, **k: {})
