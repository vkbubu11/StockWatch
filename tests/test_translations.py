"""Angol / nemet / spanyol felulet: bongeszoben (Playwright) angolul vegigjar minden oldalt es allapotot, es
nem maradhat forditatlan szoveg (az i18n.js window.__i18nMissing listaja). Mivel a katalogus minden sora mind a
negy nyelvet tartalmazza (test_i18n), az angol bejaras a nemetet es a spanyolt is lefedi; a nemet es a spanyol
nyelvet egy-egy oldal kulon is ellenorzi.

I18N_MISSING=<fajl>: a forditatlan szovegek kiirasa (a katalogus bovitesehez).
"""
from __future__ import annotations

import os
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from stockwatch import accounts, daily, db, google_auth, groups, webapp, webauth
from stockwatch.practice import session

from .test_practice import PW, TICKERS, env, frames  # noqa: F401  (pytest fixture-ok)
from .test_responsive import browser, sync_api  # noqa: F401

NOTICES = sorted(webapp.NOTICES)
# nyelvfuggetlen jelolesek (mutatok, ido): ami csak ezekbol es szamokbol all, nem kell forditani
TECH = r"\b(ATR|[SE]MA\d+|RSI|New York|AM|PM)\b"


@pytest.fixture
def site(env, monkeypatch):  # noqa: F811
    real = webapp.config.env_value
    monkeypatch.setattr(webapp.config, "env_value", lambda k: "hello@example.com" if k == "CONTACT_EMAIL" else real(k))
    monkeypatch.setattr(google_auth, "configured", lambda: True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _page(browser, base, lang="en", token=None):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="en-US")
    page.add_init_script(f"try {{ localStorage.setItem('sw-lang', '{lang}'); }} catch (e) {{}}")
    if token:
        page.context.add_cookies([{"name": webauth.COOKIE, "value": token, "url": base}])
    return page


def _settle(page):
    page.wait_for_function("() => !document.querySelector('#view .loading') && !document.documentElement.classList.contains('i18n-loading')",
                           timeout=15000)
    page.wait_for_timeout(300)


def _missing(page) -> set[str]:
    return set(page.evaluate("() => [...(window.__i18nMissing || [])]"))


def _visit(page, url, out, settle=True):
    page.goto("about:blank")                       # mindig teljes betoltes (ugyanaz a hash nem toltene ujra)
    page.goto(url)
    if settle:
        _settle(page)
    else:
        page.wait_for_timeout(600)
    out |= _missing(page)


def test_every_page_is_translated(site, browser, env, outbox):  # noqa: F811
    missing: set[str] = set()
    # --- nyilvanos oldalak, urlap-visszajelzesek, urlap-hibak
    page = _page(browser, site)
    for path in ["/", "/login", "/register", "/forgot", "/resend", "/reset?token=x", "/aszf", "/adatkezeles", "/sutik",
                 *[f"/login?e={code}" for code in NOTICES], "/register?e=googleterms", "/forgot?e=badlink"]:
        _visit(page, site + path, missing, settle=False)
    for fields in ({"email": "anna@example.com", "password": "rossz-jelszo-123"},):
        page.goto(site + "/login")
        page.fill("#email", fields["email"])
        page.fill("#password", fields["password"])
        page.click("form[action='/login'] button[type=submit]")
        page.wait_for_timeout(600)
        missing |= _missing(page)
    page.goto(site + "/register")
    page.fill("#email", "uj@example.com")
    page.fill("#password", "1234567890")                                   # kiszivargott jelszo: hiba
    page.check("form[action='/register'] input[name=terms]")
    page.check("form[action='/register'] input[name=age]")
    page.click("form[action='/register'] button[type=submit]")
    page.wait_for_timeout(600)
    missing |= _missing(page)
    page.close()

    # --- egy uj (meg be nem vezetett) felhasznalo, minden szinttel nyitva
    user = env["user"]
    with db.connection() as conn, conn.cursor() as cur:
        conn.execute("UPDATE users SET onboarded_at = NULL WHERE id = %s", (user,))
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 0.8, now())",
                        [(user, n) for n in range(1, 10)])
    token = accounts.login("anna@example.com", PW, "127.0.0.1")
    page = _page(browser, site, token=token)
    for step in range(3):
        _visit(page, f"{site}/#/welcome/{step}", missing)
    page.click("#trial")
    page.wait_for_selector("#pr-dform")
    _settle(page)
    page.fill("input[name=stop]", "150")                                   # ervenytelen stop: hibauzenet
    page.click("#pr-dform button[type=submit]")
    page.wait_for_timeout(800)
    missing |= _missing(page)
    page.fill("input[name=stop]", "95")
    page.click("#pr-dform button[type=submit]")
    page.click("#pr-skipanim")
    page.wait_for_selector(".pr-verdict-label")
    page.wait_for_timeout(300)
    missing |= _missing(page)
    page.click("#pr-next")                                                  # az osszegzes
    _settle(page)
    missing |= _missing(page)
    for level in range(1, 11):
        _visit(page, f"{site}/#/learn/{level}", missing)
    lesson = learning_round(user, env)
    for path in ("/#/learn", "/#/practice", f"/#/practice/{lesson}", "/#/daily", "/#/leaderboard", "/#/profile", "/#/info"):
        _visit(page, site + path, missing)
    # napi kihivas: folyamatban (rejtett), majd kesz
    sid = daily.start(user, env["settings"])["id"]
    _visit(page, site + "/#/daily", missing)
    _visit(page, f"{site}/#/practice/{sid}", missing)
    page.click("#pr-dform button[type=submit]")
    page.wait_for_selector("#pr-next")
    missing |= _missing(page)
    for k in range(1, session.DAILY_ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    for path in ("/#/daily", f"/#/practice/{sid}", "/#/leaderboard"):
        _visit(page, site + path, missing)
    # csoportok, becenev, hibauzenetek a profilban es a ranglistan
    g = groups.create(user, "Kurzus")
    _visit(page, site + "/#/leaderboard", missing)
    page.select_option("#lb-group", str(g["id"]))
    _settle(page)
    page.fill("#group-join input[name=code]", "rosszkod")
    page.click("#group-join button[type=submit]")
    page.wait_for_timeout(600)
    missing |= _missing(page)
    _visit(page, site + "/#/profile", missing)
    page.fill("#nick-form input[name=nickname]", "x")
    page.click("#nick-form button[type=submit]")
    page.wait_for_timeout(600)
    missing |= _missing(page)
    page.fill("#delete-form input[name=password]", "rossz-jelszo-123")
    page.once("dialog", lambda d: d.accept())
    page.click("#delete-form button[type=submit]")
    page.wait_for_timeout(800)
    missing |= _missing(page)
    page.close()

    # a bongeszo altal formazott datum / ido (pl. "10/22/2026, 04:16 PM") es a tesztadat tickerei nem a mi szovegunk
    missing = {m for m in missing if re.search(r"[A-Za-zÀ-ž]{2}", m) and not re.fullmatch(r"[\d/:,. ]+(AM|PM)?", m)
               and m not in TICKERS and re.search(r"[A-Za-zÀ-ž]{2}", re.sub(TECH, "", m))}
    dump = os.environ.get("I18N_MISSING")
    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(missing)))
    assert not missing, f"{len(missing)} forditatlan szoveg, pl.: {sorted(missing)[:15]}"


def learning_round(user, env) -> str:  # noqa: F811
    """Egy befejezett leckekor (az osszegzes a szint eredmenyevel)."""
    from stockwatch import learning

    created = learning.start(user, 1, env["settings"])
    for k in range(created["rounds"]):
        session.decide(user, created["id"], k, {"action": "skip"})
    return created["id"]


@pytest.mark.parametrize("lang, word", [("de", "Lernen"), ("es", "Aprender")])
def test_german_and_spanish_menus(site, browser, env, lang, word):  # noqa: F811
    accounts.mark_onboarded(env["user"])
    page = _page(browser, site, lang=lang, token=accounts.login("anna@example.com", PW, "127.0.0.1"))
    page.goto(site + "/#/learn")
    _settle(page)
    assert page.evaluate("document.documentElement.lang") == lang
    assert word in page.inner_text(".tabs") and page.query_selector(".lang-select")
    page.close()


def test_phone_card_labels_are_translated(site, browser, env):  # noqa: F811
    # telefonon a tablazat kartyakka valik; a cimke (data-label) a mar leforditott fejlecbol jon
    user = env["user"]
    accounts.mark_onboarded(user)
    sid = daily.start(user, env["settings"])["id"]
    for k in range(session.DAILY_ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    page = _page(browser, site, token=accounts.login("anna@example.com", PW, "127.0.0.1"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site + "/#/leaderboard")
    _settle(page)
    labels = page.evaluate("[...document.querySelectorAll('table.cards-on-phone tbody tr:first-child td')].map(td => td.dataset.label)")
    assert labels == ["Rank", "Nickname", "Score", "Days"]
    page.close()


def test_account_emails_follow_the_interface_language(neon, outbox):
    accounts.register("anna@example.com", PW, True, True, "1.1.1.1", "https://x.example", "en")
    subject, body = outbox[-1][1], outbox[-1][2]
    assert subject == "StockWatch – confirm your email address"
    assert "https://x.example/verify?token=" in body and "registration" in body and "regisztr" not in body
    accounts.request_reset("anna@example.com", "1.1.1.1", "https://x.example", "de")
    assert "Passwort" in outbox[-1][1] and "/reset?token=" in outbox[-1][2]
