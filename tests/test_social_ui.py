"""Megosztas (#57) es parbaj (#55) a bongeszoben, telefonmeretben: a parbaj indithato a Napi kihivas oldalrol, a
meghivo linkje latszik; az ellenfel elfogadja, a sajat kore utan latja az osszevetest; a napi osszegzesben a
megosztas gomb linket ad. Oldalra soha nem gorog, JS-hiba nincs."""
from __future__ import annotations

from stockwatch import accounts, daily, duels, webauth
from stockwatch.practice import session

from .conftest import make_user
from .test_onboarding_e2e import server  # noqa: F401  (pytest fixture)
from .test_practice import PW, env, frames  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401


def _page(browser, server, email: str):  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, locale="hu-HU")
    page.add_init_script("try { localStorage.setItem('sw-lang', 'hu'); } catch (e) {}")
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login(email, PW, "127.0.0.1"), "url": server}])
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page, errors


def _skip_all(user: int, sid: str) -> None:
    for k in range(5):
        session.decide(user, sid, k, {"action": "skip"})


def test_duel_flow_and_share_button(server, browser, env):  # noqa: F811
    anna, bela = env["user"], make_user("bela@example.com", PW)
    for uid in (anna, bela):
        accounts.mark_onboarded(uid)
    page, errors = _page(browser, server, "anna@example.com")
    page.goto(f"{server}/#/daily")
    page.wait_for_selector("#duel-form")
    page.click("#duel-form button[type=submit]")
    page.wait_for_selector("#duel-copy")
    duel_id = page.url.rsplit("/", 1)[-1]
    assert f"/d/{duel_id}" in page.inner_text("main")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    other, other_errors = _page(browser, server, "bela@example.com")
    other.goto(f"{server}/d/{duel_id}")                                     # a meghivo link -> a parbaj oldala
    other.wait_for_selector("#duel-accept")
    other.click("#duel-accept")
    other.wait_for_selector("#pr-dform")
    mine = duels.view(anna, duel_id)["me"]["session"]
    _skip_all(anna, mine)
    _skip_all(bela, duels.view(bela, duel_id)["me"]["session"])
    other.goto(f"{server}/#/duel/{duel_id}")
    other.wait_for_selector("#duel-rematch")
    assert "Döntetlen" in other.inner_text("main") and other.locator("table tbody tr").count() == 5

    sid = daily.start(anna, env["settings"])["id"]
    _skip_all(anna, sid)
    page.goto(f"{server}/#/practice/{sid}")
    page.wait_for_selector("#share-btn")
    page.click("#share-btn")
    page.wait_for_selector("#share-msg a")
    assert "/s/" in page.inner_text("#share-msg")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert errors == [] and other_errors == []
