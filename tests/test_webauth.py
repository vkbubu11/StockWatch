"""Bejelentkezesi segedek: jelszo-hash, gyakorisag-korlat (egyideju keresekkel is), suti, kliens-IP."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from stockwatch import webauth

PASSWORD = "helyes-lo-elem-tuzfal"


@pytest.fixture(scope="module")
def stored_hash():
    return webauth.hash_password(PASSWORD)


def test_hash_roundtrip_and_format(stored_hash):
    assert webauth.verify_password(PASSWORD, stored_hash)
    assert not webauth.verify_password(PASSWORD + "x", stored_hash)
    assert not webauth.verify_password("", stored_hash)
    assert not webauth.verify_password(PASSWORD, "serult")
    assert set(stored_hash) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:")
    assert webauth.hash_password(PASSWORD) != stored_hash                 # minden hash-nek sajat sava
    with pytest.raises(ValueError):
        webauth.hash_password("rovid")


def test_rate_limit_window_and_clear(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(webauth.time, "time", lambda: now[0])
    limit = webauth.RateLimit(2, 60)
    assert limit.hit("a") and limit.hit("a") and not limit.hit("a")
    assert limit.hit("b")                                                 # mas kulcs nem erintett
    now[0] += 61
    assert limit.hit("a")                                                 # lejart az ablak
    limit.clear("a")
    assert limit.hit("a") and limit.hit("a")


def test_rate_limit_holds_for_simultaneous_requests():
    limit = webauth.RateLimit(5, 60)
    barrier = threading.Barrier(20)
    allowed = []

    def attempt():
        barrier.wait()
        if limit.hit("6.6.6.6"):
            allowed.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(allowed) == 5                                               # nem 20 tipp egy ablakban


def _handler(ip, headers):
    return SimpleNamespace(client_address=(ip, 1234), headers=headers)


def test_client_ip_trusts_forwarded_header_only_from_local_proxy():
    fwd = {"X-Forwarded-For": "1.1.1.1, 203.0.113.9", "X-Forwarded-Proto": "https"}
    assert webauth.client_ip(_handler("127.0.0.1", fwd), True) == "203.0.113.9"
    assert webauth.is_https(_handler("127.0.0.1", fwd), True)
    assert webauth.client_ip(_handler("127.0.0.1", fwd), False) == "127.0.0.1"   # nem proxy-mod
    assert webauth.client_ip(_handler("8.8.8.8", fwd), True) == "8.8.8.8"        # nem a helyi proxytol jott
    assert not webauth.is_https(_handler("8.8.8.8", fwd), True)


def test_session_cookie_flags_and_parsing():
    cookie = webauth.session_cookie("abc", True, 3600)
    for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Max-Age=3600", "Path=/"):
        assert flag in cookie
    assert "Secure" not in webauth.session_cookie("abc", False, 3600)
    assert webauth.session_token(_handler("1.2.3.4", {"Cookie": f"x=1; {webauth.COOKIE}=tok"})) == "tok"
    assert webauth.session_token(_handler("1.2.3.4", {})) is None
