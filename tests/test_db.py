"""Adatbazis (#30): a kapcsolati adat sehol nem latszik - adatbazis nelkul fut. A migraciot a Neonon a
`python cli.py db migrate` ellenorzi (masodszor futtatva "nincs uj migracio")."""
from __future__ import annotations

import logging

import pytest

from stockwatch import db

SECRETS = ("alice-db-user", "s3cr3t-pw-123", "127.0.0.1", "edu_secret_db")


@pytest.fixture(autouse=True)
def _closed_pool():
    db.close()                      # egy korabbi adatbazis-teszt poolja ne szoljon bele
    yield
    db.close()


def _use_url(monkeypatch, url):
    monkeypatch.setattr(db, "url", lambda: url)


def test_missing_url_is_a_clear_error(monkeypatch):
    _use_url(monkeypatch, None)
    with pytest.raises(db.DatabaseUnavailable, match="DATABASE_URL"):
        db.pool()


def test_connection_data_never_in_errors_or_logs(monkeypatch, caplog):
    # a 127.0.0.1:1 porton nincs szerver: a pool kapcsolodasi hibakat naploz, utana idotullepes
    _use_url(monkeypatch, "postgresql://alice-db-user:s3cr3t-pw-123@127.0.0.1:1/edu_secret_db"
                          "?sslmode=disable&connect_timeout=2")
    monkeypatch.setattr(db, "POOL_TIMEOUT", 6.0)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(db.DatabaseUnavailable) as exc:
        with db.connection():
            pass
    # Linuxon a pool hibasora a hostot is irja (Windowson csak "timeout") - a szurot kozvetlenul is probaljuk
    logging.getLogger("psycopg.pool").warning('connection to server at "127.0.0.1" failed for "alice-db-user"')
    assert "***" in caplog.text
    for value in SECRETS:
        assert value not in str(exc.value) and value not in caplog.text, value
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
