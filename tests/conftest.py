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
def _isolated_practice(tmp_path, monkeypatch):
    """A gyakorlo mod kozos feladatkeszlete (data/practice) es a Kitores-forras (data/lab) a tesztekben ideiglenes."""
    from stockwatch.practice import breakouts, store

    monkeypatch.setattr(store, "POOL_DIR", tmp_path / "practice")
    monkeypatch.setattr(breakouts, "LAB_DIR", tmp_path / "lab")


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """A tesztek soha nem a .env DATABASE_URL-jehez kapcsolodnak - adatbazis csak a `neon` fixture-rel."""
    from stockwatch import db

    monkeypatch.setattr(db, "url", lambda: None)


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """A kimeno levelek (SMTP soha): [(cimzett, targy, szoveg)]."""
    from stockwatch import accounts

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(accounts.mailer, "send", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    from stockwatch import accounts, webauth

    for name in ("IP_LOGIN", "ACCOUNT_LOGIN", "IP_REGISTER", "IP_RESET", "ACCOUNT_RESET"):
        old = getattr(accounts, name)
        monkeypatch.setattr(accounts, name, webauth.RateLimit(old.limit, old.window))


@pytest.fixture(scope="session")
def _neon_url():
    """Ideiglenes sema a Neonon (a DATABASE_URL kozvetlen, nem pooled vegpontjan - a pooler nem enged
    search_path-ot); a migraciok lefutnak benne, a tesztek vegen torlodik. A public semat nem erinti.
    TEST_DATABASE_URL: mas adatbazis. Egyik sincs (pl. CI titok nelkul): az adatbazis-tesztek kimaradnak."""
    import os
    import secrets

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from stockwatch import config, db

    url = os.environ.get("TEST_DATABASE_URL") or config.env_value("DATABASE_URL")
    if not url:
        pytest.skip("Nincs DATABASE_URL / TEST_DATABASE_URL - az adatbazis-tesztek kimaradnak")
    params = conninfo_to_dict(url)
    params["host"] = str(params.get("host") or "").replace("-pooler", "")
    base = make_conninfo(**params)
    schema = f"sw_test_{secrets.token_hex(4)}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    test_url = make_conninfo(base, options=f"-c search_path={schema}")
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(db, "url", lambda: test_url)
            db.migrate()
        yield test_url
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def neon(_neon_url, monkeypatch):
    """Adatbazis a teszthez (ures tablakkal indul)."""
    from stockwatch import db

    monkeypatch.setattr(db, "url", lambda: _neon_url)
    yield
    with db.connection() as conn:
        conn.execute("TRUNCATE users, practice_sessions RESTART IDENTITY CASCADE")


def make_user(email: str = "anna@example.com", password: str = "helyes-lo-elem-tuzfal", verified: bool = True) -> int:
    """Kozvetlenul az adatbazisba (a regisztracio folyamatat a test_accounts.py teszteli)."""
    from stockwatch import accounts, db, webauth

    with db.connection() as conn:
        return conn.execute("INSERT INTO users (email, password_hash, email_verified_at, terms_version, terms_accepted_at) "
                            "VALUES (%s, %s, CASE WHEN %s THEN now() END, %s, now()) RETURNING id",
                            (email, webauth.hash_password(password), verified, accounts.TERMS_VERSION)).fetchone()[0]


@pytest.fixture
def user(neon) -> int:
    return make_user()
