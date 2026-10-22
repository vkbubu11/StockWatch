"""Belepes Google-fiokkal (#43) a Google-valasz utanzataval: a claimek ellenorzese, a state egyszer hasznalhato es
a bongeszohoz kotott, nincs automatikus fiokatvetel, uj fiok csak az ASZF elfogadasaval, a kliens-titok sehol."""
from __future__ import annotations

import base64
import http.client
import io
import json
import logging
import threading
import time
import urllib.error
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from stockwatch import accounts, google_auth, webapp

from .conftest import make_user

CLIENT, SECRET = "client-123.apps.googleusercontent.com", "topsecret-xyz-987"
REAL_EXCHANGE = google_auth._exchange


@pytest.fixture(autouse=True)
def google_env(monkeypatch):
    real = google_auth.config.env_value
    keys = {"GOOGLE_CLIENT_ID": CLIENT, "GOOGLE_CLIENT_SECRET": SECRET}
    monkeypatch.setattr(google_auth.config, "env_value", lambda key: keys.get(key) or real(key))


def _token(**over) -> str:
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "exp": time.time() + 600, "nonce": "N",
              "email": "anna@gmail.com", "email_verified": True, "sub": "google-sub-1"} | over
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")     # noqa: E731
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


@pytest.mark.parametrize("bad", [{"aud": "masik-kliens"}, {"iss": "https://evil.example"}, {"exp": time.time() - 1},
                                 {"email_verified": False}, {"nonce": "masik"}, {"sub": ""}])
def test_claims_are_checked(bad):
    assert google_auth.claims(_token(), "N")["sub"] == "google-sub-1"
    with pytest.raises(google_auth.GoogleError):
        google_auth.claims(_token(**bad), "N")
    with pytest.raises(google_auth.GoogleError):
        google_auth.claims("nem.jwt", "N")


def _nonce(state: str) -> str:
    from stockwatch import db

    with db.connection() as conn:
        return conn.execute("SELECT nonce FROM oauth_states WHERE state_hash = %s", (google_auth._h(state),)).fetchone()[0]


def test_state_is_single_use_bound_to_browser_and_expires(neon):
    url, state = google_auth.begin("https://gy.example")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["openid email profile"] and q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
    assert q["redirect_uri"] == ["https://gy.example/auth/google/callback"] and SECRET not in url
    with pytest.raises(google_auth.GoogleError):
        google_auth.finish(state, "masik-suti", "code", "https://gy.example")          # mas bongeszo
    with pytest.raises(google_auth.GoogleError):
        google_auth.finish(state, state, "code", "https://gy.example")                # a state elhasznalva
    _, old = google_auth.begin("https://gy.example")
    from stockwatch import db

    with db.connection() as conn:
        assert old.encode() not in conn.execute("SELECT state_hash FROM oauth_states").fetchone()[0]   # csak hash
        conn.execute("UPDATE oauth_states SET created_at = now() - interval '11 minutes'")
    with pytest.raises(google_auth.GoogleError):
        google_auth.finish(old, old, "code", "https://gy.example")                    # lejart


def test_account_rules(neon):
    assert accounts.google_login("s1", "uj@gmail.com", None, False) == ("terms_required", None)
    kind, token = accounts.google_login("s1", "uj@gmail.com", None, True)
    user = accounts.session_user(token)
    assert kind == "session" and user["google"] and not user["has_password"]
    assert accounts.google_login("s1", "uj@gmail.com", None, False)[0] == "session"   # ismert sub: belepes
    assert accounts.login("uj@gmail.com", "barmilyen-jelszo-1", "1.1.1.1") is None    # jelszo nelkuli fiok
    anna = make_user("anna@gmail.com")
    assert accounts.google_login("s2", "Anna@gmail.com", None, True) == ("link_required", None)   # nincs atvetel
    assert accounts.google_login("s2", "anna@gmail.com", anna, False) == ("linked", None)        # jelszoval belepve
    assert accounts.session_user(accounts.google_login("s2", "anna@gmail.com", None, False)[1])["id"] == anna
    assert accounts.google_login("s1", "uj@gmail.com", anna, False) == ("taken", None)
    with pytest.raises(accounts.AccountError):
        accounts.unlink_google(user["id"])                                         # jelszo nelkul nem bonthato
    accounts.unlink_google(anna)
    assert not accounts.session_user(accounts.login("anna@gmail.com", "helyes-lo-elem-tuzfal", "1.1.1.1"))["google"]
    assert not accounts.delete_account(user["id"], "rossz@gmail.com")
    assert accounts.delete_account(user["id"], "UJ@gmail.com")                     # e-mail-cimmel erositi meg


@pytest.fixture()
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(trust_proxy=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    res.text = res.read().decode("utf-8", "replace")
    conn.close()
    return res


def test_google_flow_over_http(neon, port, monkeypatch, caplog):
    assert 'href="/auth/google/start"' in _get(port, "/login").text
    assert 'action="/auth/google/start"' in _get(port, "/register").text
    start = _get(port, "/auth/google/start?terms=1&age=1")
    state = parse_qs(urlparse(start.getheader("Location")).query)["state"][0]
    cookie = start.getheader("Set-Cookie")
    assert start.status == 303 and "SameSite=Lax" in cookie and "HttpOnly" in cookie
    nonce = _nonce(state)
    monkeypatch.setattr(google_auth, "_exchange", lambda code, verifier, uri: _token(nonce=nonce))
    assert _get(port, f"/auth/google/callback?state={state}&code=c").getheader("Location") == "/login?e=googlefail"  # suti nelkul
    start = _get(port, "/auth/google/start?terms=1&age=1")
    state = parse_qs(urlparse(start.getheader("Location")).query)["state"][0]
    nonce = _nonce(state)
    done = _get(port, f"/auth/google/callback?{urlencode({'state': state, 'code': 'c'})}",
                {"Cookie": start.getheader("Set-Cookie").split(";")[0]})
    session = [c for c in done.msg.get_all("Set-Cookie") if c.startswith("sw_session=")][0].split(";")[0]
    assert done.getheader("Location") == "/"
    me = json.loads(_get(port, "/api/session", {"Cookie": session}).text)
    assert me["email"] == "anna@gmail.com" and me["google"] and me["google_available"]
    # a token-csere hibaja (a valodi _exchange, a halozat utanozva): altalanos uzenet, a kliens-titok sehol
    def failing(req, timeout):
        assert SECRET.encode() in req.data                                  # a titok csak a Google-nek megy
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error": "invalid_grant"}'))
    monkeypatch.setattr(google_auth, "_exchange", REAL_EXCHANGE)
    monkeypatch.setattr(google_auth.urllib.request, "urlopen", failing)
    caplog.set_level(logging.DEBUG)
    start = _get(port, "/auth/google/start")
    state = parse_qs(urlparse(start.getheader("Location")).query)["state"][0]
    fail = _get(port, f"/auth/google/callback?state={state}&code=c", {"Cookie": start.getheader("Set-Cookie").split(";")[0]})
    assert fail.getheader("Location") == "/login?e=googlefail" and SECRET not in caplog.text and SECRET not in fail.text
    assert "HTTP 400" in caplog.text


def test_no_google_button_without_configuration(port, monkeypatch):
    monkeypatch.setattr(google_auth, "configured", lambda: False)
    assert "/auth/google/start" not in _get(port, "/login").text
    assert _get(port, "/auth/google/start").status == 404
