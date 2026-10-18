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


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """A felhasznaloi munkaterulet (data/users) es a labor-munkasor (data/queue) a tesztekben ideiglenes."""
    from stockwatch import labqueue, workspace

    monkeypatch.setattr(workspace, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(labqueue, "QUEUE_DIR", tmp_path / "queue")


@pytest.fixture(autouse=True)
def _isolated_event_sources(tmp_path, monkeypatch):
    """A SEC-kereskorlat idobelyege es az esemenyforrasok (Form 4, indexvaltozasok) a tesztekben ideiglenesek."""
    from stockwatch.events.sources import form4, index_changes
    from stockwatch.sentiment.sources import edgar

    monkeypatch.setattr(edgar, "RATE_FILE", tmp_path / "news" / "_sec_rate")
    monkeypatch.setattr(form4, "FORM4_DIR", tmp_path / "events" / "form4")
    monkeypatch.setattr(index_changes, "EVENTS_DIR", tmp_path / "events")


@pytest.fixture(autouse=True)
def _isolated_practice(tmp_path, monkeypatch):
    """A gyakorlo mod kozos feladatkeszlete (data/practice) a tesztekben ideiglenes."""
    from stockwatch.practice import store

    monkeypatch.setattr(store, "POOL_DIR", tmp_path / "practice")
