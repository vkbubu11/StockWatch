"""A webes felulet bejelentkezese: jelszo-hash, munkamenet, talalgatas elleni zar, HTTP-folyam."""
from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from stockwatch import config, webapp, webauth

PASSWORD = "helyes-lo-elem-tuzfal"


@pytest.fixture(scope="module")
def stored_hash():
    return webauth.hash_password(PASSWORD)


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(webauth, "FAIL_DELAY", 0)


def test_hash_roundtrip_and_format(stored_hash):
    assert webauth.verify_password(PASSWORD, stored_hash)
    assert not webauth.verify_password(PASSWORD + "x", stored_hash)
    assert not webauth.verify_password("", stored_hash)
    assert not webauth.verify_password(PASSWORD, "serult")
    assert set(stored_hash) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:")
    assert webauth.hash_password(PASSWORD) != stored_hash                 # minden hash-nek sajat sava
    with pytest.raises(ValueError):
        webauth.hash_password("rovid")


def test_session_lifecycle_and_password_change(stored_hash):
    current = {"hash": stored_hash}
    auth = webauth.Auth(password_hash=lambda: current["hash"])
    assert not auth.check(None)
    assert auth.login("1.2.3.4", "rossz-jelszo-123") is None
    token = auth.login("1.2.3.4", PASSWORD)
    assert token and auth.check(token) and not auth.check(token + "x")
    current["hash"] = webauth.hash_password("masik-jelszo-hosszu")       # jelszocsere -> kileptet
    assert not auth.check(token)
    token = auth.login("1.2.3.4", "masik-jelszo-hosszu")
    auth.logout(token)
    assert not auth.check(token)


def test_lockout_per_ip(stored_hash):
    auth = webauth.Auth(password_hash=lambda: stored_hash)
    for _ in range(webauth.MAX_FAILS):
        assert auth.login("6.6.6.6", "talalgatas-1234") is None
    with pytest.raises(webauth.LoginLocked):
        auth.login("6.6.6.6", PASSWORD)                                   # zarolva: helyes jelszoval sem
    assert auth.login("7.7.7.7", PASSWORD)                                # mas IP nem erintett


def test_required_without_password_denies_everything():
    assert webauth.Auth(password_hash=lambda: None).check(None)          # helyi mod: nyitott
    locked = webauth.Auth(password_hash=lambda: None, required=True)
    assert locked.enabled and not locked.check(None) and not locked.check("barmi")


def test_save_env_is_private_and_keeps_other_lines(tmp_path, monkeypatch):
    env = tmp_path / "stockwatch.env"
    env.write_text("# komment\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env)
    config.save_env({webauth.HASH_KEY: "scrypt:1:2:3:a:b"})
    text = env.read_text(encoding="utf-8")
    assert "# komment" in text and "TELEGRAM_CHAT_ID=42" in text
    assert config.env_value(webauth.HASH_KEY) == "scrypt:1:2:3:a:b"
    import os
    if os.name == "posix":
        assert env.stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------------
# HTTP: proxy mogotti, jelszavas felulet
# ---------------------------------------------------------------------------
@pytest.fixture()
def server(stored_hash):
    auth = webauth.Auth(password_hash=lambda: stored_hash, trust_proxy=True, required=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(webapp.AppState(), auth))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    res = conn.getresponse()
    res.read()
    conn.close()
    return res


def _login(port, password, ip="203.0.113.9"):
    return _req(port, "POST", "/login", urlencode({"password": password}),
                {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": ip,
                 "X-Forwarded-Proto": "https"})


def test_http_requires_login(server):
    assert _req(server, "GET", "/api/settings").status == 401
    res = _req(server, "GET", "/")
    assert res.status == 303 and res.getheader("Location") == "/login"
    assert _req(server, "GET", "/app.js").status == 303
    assert _req(server, "GET", "/login").status == 200
    assert _req(server, "GET", "/style.css").status == 200
    # bejelentkezes nelkul POST sem megy at, a fejleccel sem
    assert _req(server, "POST", "/api/refresh", b"{}", {"X-StockWatch": "1"}).status == 401


def test_http_login_flow(server):
    bad = _login(server, "rossz-jelszo-123")
    assert bad.status == 303 and bad.getheader("Location") == "/login?e=bad"
    assert bad.getheader("Set-Cookie") is None

    ok = _login(server, PASSWORD)
    cookie = ok.getheader("Set-Cookie")
    assert ok.status == 303 and ok.getheader("Location") == "/"
    for flag in ("HttpOnly", "SameSite=Strict", "Secure"):
        assert flag in cookie
    session = cookie.split(";", 1)[0]

    res = _req(server, "GET", "/api/session", headers={"Cookie": session})
    assert res.status == 200
    assert _req(server, "POST", "/logout", b"", {"Cookie": session, "X-StockWatch": "1"}).status == 200
    assert _req(server, "GET", "/api/session", headers={"Cookie": session}).status == 401


def test_http_login_rejects_foreign_origin(server):
    res = _req(server, "POST", "/login", urlencode({"password": PASSWORD}),
               {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://gonosz.example"})
    assert res.getheader("Location") == "/login?e=origin"


def test_http_lockout_uses_forwarded_ip(server):
    for _ in range(webauth.MAX_FAILS):
        _login(server, "talalgatas-1234", ip="198.51.100.1")
    assert _login(server, PASSWORD, ip="198.51.100.1").getheader("Location") == "/login?e=locked"
    assert _login(server, PASSWORD, ip="198.51.100.2").getheader("Location") == "/"
