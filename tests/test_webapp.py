"""Az oktatasi webes felulet tesztjei: JSON, HTTP-vedelmek, a fiok-folyam urlapjai (regisztracio -> level ->
ellenorzes -> belepes -> suti), kijelentkezes, adatbazis nelkuli hibak."""
from __future__ import annotations

import http.client
import json
import math
import re
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import pytest

from stockwatch import db, webapp, webauth

from .conftest import make_user

PW = "helyes-lo-elem-tuzfal"


def test_clean_handles_pandas_and_nan():
    out = webapp._clean({"a": np.float64("nan"), "b": np.int64(3), "c": pd.Timestamp("2025-06-03"),
                         "d": np.bool_(True), "e": [math.inf]})
    assert out == {"a": None, "b": 3, "c": "2025-06-03", "d": True, "e": [None]}
    json.dumps(out)


@pytest.fixture()
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler(trust_proxy=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request(method, path, body=body, headers=headers or {})
    res = conn.getresponse()
    res.text = res.read().decode("utf-8", "replace")
    conn.close()
    return res


def _form(port, path, fields, headers=None):
    return _req(port, "POST", path, urlencode(fields), {"Content-Type": "application/x-www-form-urlencoded",
                                                        "X-Forwarded-For": "203.0.113.9", **(headers or {})})


def test_http_security_without_login(port):
    res = _req(port, "GET", "/")                                       # nyilvanos kezdooldal (#39)
    assert res.status == 200 and 'href="/register"' in res.text and "app.js" not in res.text
    shot = _req(port, "GET", "/img/feladat.png")                       # a kezdooldal kepei is nyilvanosak
    assert shot.status == 200 and shot.getheader("Content-Type") == "image/png"
    assert _req(port, "GET", "/img/../app.js").status in (303, 404)
    assert _req(port, "GET", "/app.js").status == 303                  # a tobbi oldal nem nyilvanos
    assert _req(port, "GET", "/index.html").status == 303
    assert _req(port, "GET", "/api/practice").status == 401
    login = _req(port, "GET", "/login")
    assert login.status == 200 and "frame-ancestors 'none'" in login.getheader("Content-Security-Policy")
    assert 'href="/resend"' in login.text                                  # "Nem kaptad meg a levelet?" (#44)
    for public in ("/register", "/forgot", "/resend", "/style.css", "/i18n.js", "/i18n/en.json"):
        assert _req(port, "GET", public).status == 200, public
    assert _req(port, "GET", "/i18n/xx.json").status == 404
    assert _req(port, "GET", "/auth.html").status == 303                # a sablon nem fajl, bejelentkezve sem
    # POST egyedi fejlec nelkul tiltva (CSRF ellen), fejleccel is csak bejelentkezve
    assert _req(port, "POST", "/api/practice/session", b"{}").status == 403
    assert _req(port, "POST", "/api/practice/session", b"{}", {"X-StockWatch": "1"}).status == 401


def test_database_outage_is_a_clean_503(port):
    # a teszt adatbazis nelkul fut (conftest): a valasz altalanos, kapcsolati adat es nyomkovetes nelkul
    db.close()                                                         # egy korabbi teszt poolja ne szoljon bele
    res = _req(port, "GET", "/api/practice", headers={"Cookie": f"{webauth.COOKIE}=valami"})
    assert res.status == 503 and "adatbazis" in res.text and "Traceback" not in res.text


def test_account_flow_over_http(neon, outbox, port):
    res = _form(port, "/register", {"email": "Anna@Example.com", "password": PW, "terms": "1", "age": "1"})
    assert res.status == 303 and res.getheader("Location") == "/login?e=sent"
    link = re.search(r"/verify\?token=[\w-]+", outbox[-1][2]).group(0)
    assert outbox[-1][2].startswith("A regisztráció") and "http://127.0.0.1:" in outbox[-1][2]
    early = _form(port, "/login", {"email": "anna@example.com", "password": PW})
    assert early.status == 400 and "erősítsd meg" in early.text and early.getheader("Set-Cookie") is None
    assert _req(port, "GET", link).getheader("Location") == "/login?e=verified"
    assert _req(port, "GET", link).getheader("Location") == "/login?e=badlink"
    bad = _form(port, "/login", {"email": "anna@example.com", "password": "rossz-jelszo-123"})
    assert bad.status == 400 and "Hibás e-mail-cím vagy jelszó" in bad.text
    ok = _form(port, "/login", {"email": "anna@example.com", "password": PW}, {"X-Forwarded-Proto": "https"})
    cookie = ok.getheader("Set-Cookie")
    assert ok.status == 303 and ok.getheader("Location") == "/"
    for flag in ("HttpOnly", "SameSite=Strict", "Secure"):
        assert flag in cookie
    session = {"Cookie": cookie.split(";", 1)[0]}
    app = _req(port, "GET", "/", headers=session)
    assert app.status == 200 and "app.js" in app.text                  # bejelentkezve a felulet
    me = _req(port, "GET", "/api/session", headers=session)
    assert {k: v for k, v in json.loads(me.text).items() if k in ("email", "onboarded", "nickname")} == {
        "email": "anna@example.com", "onboarded": False, "nickname": None}                      # bevezeto jon
    assert _req(port, "POST", "/api/onboarded", b"{}", {**session, "X-StockWatch": "1"}).status == 200
    assert json.loads(_req(port, "GET", "/api/session", headers=session).text)["onboarded"] is True
    out = _req(port, "POST", "/logout", b"{}", {**session, "X-StockWatch": "1"})
    assert out.status == 200 and "Max-Age=0" in out.getheader("Set-Cookie")
    assert _req(port, "GET", "/api/session", headers=session).status == 401


def test_forms_reject_foreign_origin_and_bad_input(neon, outbox, port):
    make_user()
    foreign = _form(port, "/login", {"email": "anna@example.com", "password": PW}, {"Origin": "https://evil.example"})
    assert foreign.status == 400 and foreign.getheader("Set-Cookie") is None
    weak = _form(port, "/register", {"email": "uj@example.com", "password": "1234567890", "terms": "1", "age": "1"})
    assert weak.status == 400 and "kiszivárgott" in weak.text and outbox == []
    echoed = _form(port, "/login", {"email": "<script>x</script>", "password": "a"}).text
    assert "<script>x" not in echoed and "&lt;script&gt;x" in echoed


def test_password_reset_over_http(neon, outbox, port):
    make_user()
    assert _form(port, "/forgot", {"email": "anna@example.com"}).getheader("Location") == "/login?e=resetsent"
    token = re.search(r"token=([\w-]+)", outbox[-1][2]).group(1)
    page = _req(port, "GET", f"/reset?token={token}")
    assert page.status == 200 and f'value="{token}"' in page.text and page.getheader("Referrer-Policy") == "no-referrer"
    done = _form(port, "/reset", {"token": token, "password": "uj-hosszu-jelszo-42"})
    assert done.getheader("Location") == "/login?e=reset"
    again = _form(port, "/reset", {"token": token, "password": "masik-hosszu-jelszo"})
    assert again.getheader("Location") == "/forgot?e=badlink"


def test_logout_everywhere(neon, port):
    make_user()
    cookies = [_form(port, "/login", {"email": "anna@example.com", "password": PW}).getheader("Set-Cookie").split(";")[0]
               for _ in range(2)]
    assert _req(port, "POST", "/api/logout-all", b"{}", {"Cookie": cookies[0], "X-StockWatch": "1"}).status == 200
    assert all(_req(port, "GET", "/api/session", headers={"Cookie": c}).status == 401 for c in cookies)


DISCLAIMER = "Nem befektetési tanács"


def test_legal_pages_are_public_and_every_page_has_the_disclaimer(neon, port):
    for path in ("/aszf", "/adatkezeles", "/sutik", "/", "/login", "/register", "/forgot"):
        res = _req(port, "GET", path)
        assert res.status == 200 and DISCLAIMER in res.text, path
    assert "16" in _req(port, "GET", "/aszf").text and "NAIH" in _req(port, "GET", "/adatkezeles").text
    make_user()
    cookie = _form(port, "/login", {"email": "anna@example.com", "password": PW}).getheader("Set-Cookie").split(";")[0]
    assert DISCLAIMER in _req(port, "GET", "/", headers={"Cookie": cookie}).text            # a felulet is


def test_export_and_delete_over_http(neon, port):
    make_user()
    cookie = _form(port, "/login", {"email": "anna@example.com", "password": PW}).getheader("Set-Cookie").split(";")[0]
    session = {"Cookie": cookie, "X-StockWatch": "1"}
    export = _req(port, "GET", "/api/account/export", headers=session)
    assert export.status == 200 and "attachment" in export.getheader("Content-Disposition")
    assert json.loads(export.text)["account"]["email"] == "anna@example.com"
    assert _req(port, "POST", "/api/account/delete", json.dumps({"password": "rossz-jelszo-123"}), session).status == 400
    done = _req(port, "POST", "/api/account/delete", json.dumps({"password": PW}), session)
    assert done.status == 200 and "Max-Age=0" in done.getheader("Set-Cookie")
    assert _req(port, "GET", "/api/session", headers=session).status == 401
    assert _form(port, "/login", {"email": "anna@example.com", "password": PW}).status == 400


def test_lessons_over_http(neon, port):
    make_user()
    cookie = _form(port, "/login", {"email": "anna@example.com", "password": PW}).getheader("Set-Cookie").split(";")[0]
    session = {"Cookie": cookie, "X-StockWatch": "1"}
    levels = json.loads(_req(port, "GET", "/api/lessons", headers=session).text)
    assert len(levels) == 10 and levels[0]["unlocked"] and not levels[1]["unlocked"]
    first = json.loads(_req(port, "GET", "/api/lessons/1", headers=session).text)
    assert first["title"] and len(first["figure"]["bars"]) == 120
    assert _req(port, "GET", "/api/lessons/2", headers=session).status == 403            # zarolt
    assert _req(port, "POST", "/api/lessons/2/start", b"{}", session).status == 400
    assert _req(port, "GET", "/api/lessons/11", headers=session).status == 404



def test_resend_over_http(neon, outbox, port):
    make_user(verified=False)
    res = _form(port, "/resend", {"email": "anna@example.com"})
    assert res.getheader("Location") == "/login?e=resent" and "/verify?token=" in outbox[-1][2]
    early = _form(port, "/login", {"email": "anna@example.com", "password": PW})
    assert "spam" in early.text and 'href="/resend"' in early.text



def test_contact_email_on_legal_pages_and_session(neon, port, monkeypatch):
    real = webapp.config.env_value
    monkeypatch.setattr(webapp.config, "env_value", lambda key: "hello@gyakorlas.example" if key == "CONTACT_EMAIL" else real(key))
    for page in ("/aszf", "/adatkezeles"):
        text = _req(port, "GET", page).text
        assert "hello@gyakorlas.example" in text and "[KAPCSOLATI E-MAIL]" not in text
    make_user()
    cookie = _form(port, "/login", {"email": "anna@example.com", "password": PW}).getheader("Set-Cookie").split(";")[0]
    assert json.loads(_req(port, "GET", "/api/session", headers={"Cookie": cookie}).text)["contact"] == "hello@gyakorlas.example"
    assert "github.com" not in (webapp.WEB_DIR / "app.js").read_text(encoding="utf-8")       # nincs GitHub-link (#45)
