"""Az elso ertekelt dontesig (#39): egy uj felhasznalo a kezdooldalrol a regisztracion, az e-mail-megerositesen,
a belepesen, a 3 lepeses bevezeton es a probafeladaton at eljut az iteletig - bongeszoben (Playwright).

A "2 percen belul" becslese: kattintasonkent / mezonkent INTERACTION_S mp, plusz a bevezeto szovegenek elolvasasa
READ_WPM szo / perc tempoval - ennek 2 perc alatt kell maradnia.
LANDING_SHOTS=<mappa>: a feladat- es az iteletoldal kepernyokepe a kezdooldalhoz (app/web/img).
"""
from __future__ import annotations

import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from stockwatch import webapp

from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)
from .test_responsive import browser, sync_api  # noqa: F401

INTERACTION_S = 4
READ_WPM = 200
BUDGET_S = 120


@pytest.fixture
def server(env):  # noqa: F811
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_new_user_reaches_first_verdict_within_two_minutes(server, browser, outbox):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, locale="hu-HU", has_touch=True)
    page.add_init_script("try { localStorage.setItem('sw-lang', 'hu'); } catch (e) {}")   # a teszt magyar szovegeket nez
    actions = 0

    def act(fn, *args):
        nonlocal actions
        actions += 1
        fn(*args)

    page.goto(server + "/")
    act(page.click, "text=Ingyenes regisztráció")
    act(page.fill, "#email", "uj.tanulo@example.com")
    act(page.fill, "#password", "uj-tanulo-jelszo-42")
    act(page.check, "form[action='/register'] input[name=terms]")
    act(page.check, "form[action='/register'] input[name=age]")
    act(page.click, "form[action='/register'] button[type=submit]")
    page.wait_for_url("**/login?e=sent")
    link = re.search(r"/verify\?token=[\w-]+", outbox[-1][2]).group(0)
    act(page.goto, server + link)                                       # a levelben a linkre kattint
    act(page.fill, "#email", "uj.tanulo@example.com")
    act(page.fill, "#password", "uj-tanulo-jelszo-42")
    act(page.click, "form[action='/login'] button[type=submit]")
    page.wait_for_selector(".onboard")
    words = 0
    for _ in range(2):
        words += len(page.inner_text(".onboard").split())
        act(page.click, "text=Tovább")
    words += len(page.inner_text(".onboard").split())
    act(page.click, "#trial")
    page.wait_for_selector("#pr-dform")
    shots = os.environ.get("LANDING_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(300)
        page.screenshot(path=str(Path(shots) / "feladat.png"), full_page=False)
    act(page.click, "#pr-dform button[type=submit]")                    # az alapertelmezett long, 2 ATR stoppal
    act(page.click, "#pr-skipanim")
    page.wait_for_selector(".pr-verdict-label")
    assert page.inner_text(".pr-verdict-label") in ("Jó döntés", "Szerencsés", "Balszerencsés", "Rossz döntés")
    assert "pont" in page.inner_text(".pr-points")
    if shots:
        page.evaluate("document.querySelector('.pr-verdict').closest('section').scrollIntoView({block: 'start'})")
        page.screenshot(path=str(Path(shots) / "itelet.png"), full_page=False)
    estimate = actions * INTERACTION_S + words / READ_WPM * 60
    print(f"elso ertekelt dontesig: {actions} muvelet + {words} szo = ~{estimate:.0f} mp")
    assert estimate <= BUDGET_S, f"{actions} muvelet + {words} szo = {estimate:.0f} mp"
    page.close()
