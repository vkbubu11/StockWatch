"""Kezdooldali kiprobalhato helyzet: regisztracio nelkul ugyanaz a helyzet, a dontes iteletet es levezetest kap, de
semmi nem mentodik; papirnev es datum a dontes elott nem latszik; IP-nkenti korlat; az admin osszegzes szamolja
(megnyitas, dontes, regisztracio utana), es a bongeszoben vegigjatszhato."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from stockwatch import analytics, db, webapp, webauth

from .test_admin_ui import site  # noqa: F401  (pytest fixture)
from .test_analytics import _wait_for
from .test_practice import env, frames, server  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401
from .test_translations import TECH


def _anon(url: str, body: dict | None = None) -> tuple[int, dict]:
    """Suti nelkul (nincs bejelentkezve)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"X-StockWatch": "1", "Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0)"})   # robot nem szamit
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_demo_without_login_gives_a_verdict_and_saves_nothing(server, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(webapp, "DEMO_LIMIT", webauth.RateLimit("ip-demo-test", 3, 3600))
    status, task = _anon(f"{server}/api/demo")
    assert status == 200 and task["chart"]["bars"][-1]["c"] == 100.0
    assert _anon(f"{server}/api/demo")[1] == task                                        # mindig ugyanaz a helyzet
    text = json.dumps(task)
    assert '"symbol"' not in text and '"date"' not in text                               # a dontes elott rejtett
    status, rev = _anon(f"{server}/api/demo/decide", {"action": "skip"})
    assert status == 200 and rev["review"]["verdict"]["category"] in ("good", "lucky", "unlucky", "bad")
    assert rev["future"] and rev["review"]["steps"]
    atr = task["chart"]["atr"]
    status, rev = _anon(f"{server}/api/demo/decide", {"action": "long", "stop": round(100 - 1.5 * atr, 2)})
    assert status == 200 and rev["decision"]["action"] == "long"
    assert _anon(f"{server}/api/demo/decide", {"action": "long", "stop": 150})[0] == 400   # rossz stop
    assert _anon(f"{server}/api/demo/decide", {"action": "skip"})[0] == 429              # IP-nkenti korlat
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM practice_sessions").fetchone()[0] == 0   # semmi nem mentodott
    assert len(_wait_for("demo_view", 2)) == 2 and len(_wait_for("demo_decide", 2)) == 2
    assert analytics.overview(1)["demo"] == {"views": 2, "decisions": 2, "registers": 0}


def test_register_source_from_demo(env):  # noqa: F811
    _, form = webapp.auth_form("register", source="demo")
    assert 'name="src" value="demo"' in form


def test_landing_demo_in_the_browser(site, browser, env):  # noqa: F811
    errors: list[str] = []
    page = browser.new_page(viewport={"width": 1000, "height": 1400})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(site + "/")
    page.wait_for_selector("#demo-form:not([hidden])")
    page.click("[data-side=long]")
    assert page.input_value("input[name=stop]")                                          # javasolt stop
    page.click("#demo-form button[type=submit]")
    page.wait_for_selector("#demo-result .pr-verdict")
    page.wait_for_timeout(300)
    text = page.inner_text("#demo-result").lower()                                    # a KPI-cimke nagybetus (CSS)
    assert "reference trade" in text and "sign-up" in text                               # angolul (alapnyelv)
    missing = [m for m in page.evaluate("[...(window.__i18nMissing || [])]")          # szam, mutatonev: nem szoveg
               if re.search(r"[A-Za-zÀ-ž]{2}", re.sub(TECH, "", m))]
    assert missing == [], missing
    assert page.locator("#pr-chart .candle.future").count() > 0                          # a jovo is latszik
    assert not errors, errors
    page.close()
