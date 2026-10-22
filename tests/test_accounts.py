"""Felhasznaloi fiokok (#31) a Neon ideiglenes semajaban: regisztracio -> ellenorzo level -> belepes, nincs
fiok-felsorolas, jelszo-visszaallitas zarja a regi munkameneteket, egyszer hasznalhato es lejaro linkek, korlatok."""
from __future__ import annotations

import re

import pytest

from stockwatch import accounts, db

from .conftest import make_user

PW = "helyes-lo-elem-tuzfal"
BASE = "https://gyakorlas.example"


def _token(outbox, n=-1) -> str:
    return re.search(r"token=([\w-]+)", outbox[n][2]).group(1)


def test_register_verify_login(neon, outbox):
    accounts.register("Anna@Example.com", PW, True, True, "1.1.1.1", BASE)
    assert len(outbox) == 1 and outbox[0][0] == "anna@example.com" and f"{BASE}/verify?token=" in outbox[0][2]
    with pytest.raises(accounts.AccountError, match="erősítsd meg"):        # ellenorzes nelkul nincs belepes
        accounts.login("anna@example.com", PW, "1.1.1.1")
    assert accounts.verify_email(_token(outbox))
    assert not accounts.verify_email(_token(outbox))                       # egyszer hasznalhato
    token = accounts.login("ANNA@example.com", PW, "1.1.1.1", "pytest")
    user = accounts.session_user(token)
    assert user and user["email"] == "anna@example.com"
    with db.connection() as conn:
        stored = conn.execute("SELECT password_hash, terms_version FROM users").fetchone()
        assert PW not in stored[0] and stored[1] == accounts.TERMS_VERSION
        assert token.encode() not in conn.execute("SELECT token_hash FROM sessions").fetchone()[0]   # csak a hash
    accounts.logout(token)
    assert accounts.session_user(token) is None


def test_register_does_not_reveal_existing_accounts(neon, outbox):
    make_user("kesz@example.com")
    accounts.register("kesz@example.com", PW, True, True, "1.1.1.1", BASE)   # ellenorzott: nincs level, nincs hiba
    assert outbox == []
    make_user("felig@example.com", verified=False)
    accounts.register("felig@example.com", PW, True, True, "1.1.1.1", BASE)  # ellenorizetlen: uj link
    assert len(outbox) == 1 and accounts.verify_email(_token(outbox))
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2


@pytest.mark.parametrize("password, terms, age, match", [
    ("rovid", True, True, "legalább"), ("1234567890", True, True, "kiszivárgott"),
    (PW, False, True, "ÁSZF"), (PW, True, False, "16"),
])
def test_register_rejects_bad_input(neon, outbox, password, terms, age, match):
    with pytest.raises(accounts.AccountError, match=match):
        accounts.register("uj@example.com", password, terms, age, "1.1.1.1", BASE)
    with pytest.raises(accounts.AccountError, match="e-mail"):
        accounts.register("nem-email", PW, True, True, "1.1.1.1", BASE)
    assert outbox == []


def test_wrong_password_and_unknown_email_look_the_same(neon):
    make_user()
    assert accounts.login("anna@example.com", "rossz-jelszo-123", "1.1.1.1") is None
    assert accounts.login("nincs@example.com", PW, "1.1.1.1") is None


def test_password_reset_closes_sessions_and_link_is_single_use(neon, outbox):
    uid = make_user()
    old = accounts.login("anna@example.com", PW, "1.1.1.1")
    accounts.request_reset("nincs@example.com", "1.1.1.1", BASE)            # ismeretlen cim: nincs level, nincs hiba
    assert outbox == []
    accounts.request_reset("anna@example.com", "1.1.1.1", BASE)
    token = _token(outbox)
    with pytest.raises(accounts.AccountError):
        accounts.reset_password(token, "rovid")                              # a link ettol meg felhasznalhato
    assert accounts.reset_password(token, "uj-hosszu-jelszo-42")
    assert not accounts.reset_password(token, "masik-hosszu-jelszo")         # egyszer hasznalhato
    assert accounts.session_user(old) is None                                # a regi munkamenet zarult
    assert accounts.login("anna@example.com", PW, "1.1.1.1") is None
    assert accounts.login("anna@example.com", "uj-hosszu-jelszo-42", "1.1.1.1")
    other = make_user("bela@example.com")
    accounts.request_reset("bela@example.com", "1.1.1.1", BASE)
    with db.connection() as conn:                                            # lejart link
        conn.execute("UPDATE email_tokens SET expires_at = now() - interval '1 minute' WHERE user_id = %s", (other,))
    assert not accounts.reset_password(_token(outbox), "uj-hosszu-jelszo-42")
    assert uid != other


def test_reset_link_also_verifies_the_email(neon, outbox):
    make_user(verified=False)
    accounts.request_reset("anna@example.com", "1.1.1.1", BASE)
    assert accounts.reset_password(_token(outbox), "uj-hosszu-jelszo-42")
    assert accounts.login("anna@example.com", "uj-hosszu-jelszo-42", "1.1.1.1")


def test_logout_everywhere(neon):
    uid = make_user()
    tokens = [accounts.login("anna@example.com", PW, f"1.1.1.{n}") for n in range(3)]
    accounts.logout_everywhere(uid)
    assert all(accounts.session_user(t) is None for t in tokens)


def test_rate_limits(neon):
    make_user()
    for _ in range(accounts.ACCOUNT_LOGIN.limit):
        accounts.login("anna@example.com", "rossz-jelszo-123", "2.2.2.2")
    with pytest.raises(accounts.RateLimited):                                # a fiok zarolva - helyes jelszoval is
        accounts.login("anna@example.com", PW, "3.3.3.3")
    for _ in range(accounts.IP_REGISTER.limit):
        accounts.register(f"x{_}@example.com", PW, True, True, "4.4.4.4", BASE)
    with pytest.raises(accounts.RateLimited):
        accounts.register("y@example.com", PW, True, True, "4.4.4.4", BASE)
    for _ in range(accounts.ACCOUNT_RESET.limit):
        accounts.request_reset("anna@example.com", f"5.5.5.{_}", BASE)
    with pytest.raises(accounts.RateLimited):
        accounts.request_reset("anna@example.com", "6.6.6.6", BASE)


def test_account_deletion_removes_everything(neon):
    uid = make_user()
    other = make_user("bela@example.com")
    token = accounts.login("anna@example.com", PW, "1.1.1.1")
    accounts.request_reset("anna@example.com", "1.1.1.1", BASE)
    with db.connection() as conn:
        conn.execute("INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code) "
                     "VALUES ('abcdefghjkmn', %s, 1, 1, '{}', 'f', 'c')", (uid,))
    assert not accounts.delete_account(uid, "rossz-jelszo-123")                # jelszo-megerosites kell
    assert accounts.delete_account(uid, PW)
    assert accounts.session_user(token) is None
    assert accounts.login("anna@example.com", PW, "1.1.1.1") is None
    with db.connection() as conn:
        for table in ("users", "sessions", "email_tokens", "practice_sessions"):
            column = "id" if table == "users" else "user_id"
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (uid,)).fetchone()[0] == 0, table
        assert conn.execute("SELECT count(*) FROM users WHERE id = %s", (other,)).fetchone()[0] == 1   # mas marad


def test_export_contains_own_data_without_secrets(neon):
    uid = make_user()
    make_user("bela@example.com")
    accounts.login("anna@example.com", PW, "1.1.1.1", "Firefox")
    data = accounts.export(uid)
    assert data["account"]["email"] == "anna@example.com" and data["account"]["terms_version"] == accounts.TERMS_VERSION
    assert data["sessions"][0]["user_agent"] == "Firefox" and data["practice"] == []
    text = str(data)
    assert "scrypt" not in text and "bela@" not in text and "token" not in text


def test_stale_unverified_registrations_are_removed(neon):
    old = make_user("regi@example.com", verified=False)
    kept = make_user("friss@example.com", verified=False)
    with db.connection() as conn:
        conn.execute("UPDATE users SET created_at = now() - interval '31 days' WHERE id = %s", (old,))
    accounts.register("uj@example.com", PW, True, True, "1.1.1.1", BASE)
    with db.connection() as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM users")}
    assert old not in ids and kept in ids


def test_resend_verification_link(neon, outbox):
    make_user("kesz@example.com")
    accounts.resend_verification("kesz@example.com", "1.1.1.1", BASE)          # megerositett: nincs level
    accounts.resend_verification("nincs@example.com", "1.1.1.1", BASE)        # nem letezik: nincs level, nincs hiba
    assert outbox == []
    accounts.register("uj@example.com", PW, True, True, "1.1.1.1", BASE)
    first = _token(outbox)
    accounts.resend_verification("UJ@example.com", "1.1.1.1", BASE)
    assert len(outbox) == 2 and outbox[-1][0] == "uj@example.com"
    assert not accounts.verify_email(first)                                     # a regi link mar nem ervenyes
    assert accounts.verify_email(_token(outbox))
    assert accounts.login("uj@example.com", PW, "1.1.1.1")


def test_resend_is_rate_limited(neon):
    make_user(verified=False)
    for n in range(accounts.ACCOUNT_RESEND.limit):
        accounts.resend_verification("anna@example.com", f"2.2.2.{n}", BASE)
    with pytest.raises(accounts.RateLimited):
        accounts.resend_verification("anna@example.com", "3.3.3.3", BASE)
