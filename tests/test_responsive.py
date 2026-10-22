"""Reszponziv felulet (#23, az oktatasi navigacioval #29): bongeszos (Playwright) ellenorzes minden fo nezetre.

  * nincs oldalszintu vizszintes gorgetes 360, 390, 768, 1024 es 1280 px szelessegen, vilagos es sotet temaban
  * telefonon minden nezet legfeljebb ket koppintassal elerheto (also sav + "Tovabbiak")
  * telefonon minden lathato interaktiv elem legalabb 44 x 44 px
  * kepernyokep minden nezetrol, ha a SCREENSHOT_DIR kornyezeti valtozo meg van adva (a CI artefaktkent menti)

Playwright (es Chromium) nelkul a teszt kimarad: pip install playwright && python -m playwright install chromium.
"""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from stockwatch import accounts, webapp, webauth
from stockwatch.practice import session

from .test_practice import PW, env, frames  # noqa: F401  (pytest fixture-ok)

sync_api = pytest.importorskip("playwright.sync_api")

WIDTHS = (360, 390, 768, 1024, 1280)
PHONE = 600
TOUCH = 44
INTERACTIVE = (".btn, .seg, .bottom-nav a, .bottom-nav button, .more-list a, .lang-select, "
               ".view select, .view input:not([type=checkbox]):not([type=radio]):not([type=hidden])")


_TOKEN: dict[str, str] = {}


def _new_page(browser, base: str, **kwargs):
    """Bejelentkezett oldal (a `ui_server` felhasznalojanak munkamenet-sutijevel)."""
    page = browser.new_page(**kwargs)
    page.context.add_cookies([{"name": webauth.COOKIE, "value": _TOKEN["value"], "url": base}])
    return page


@pytest.fixture
def ui_server(env):  # noqa: F811
    created = session.create(env["user"], env["settings"], seed=5, rounds=3)
    session.decide(env["user"], created["id"], 0, {"action": "skip"})     # a korok listaja es egy felfedett helyzet
    accounts.mark_onboarded(env["user"])                                  # a bevezeto (#39) mar megvolt
    _TOKEN["value"] = accounts.login("anna@example.com", PW, "127.0.0.1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                                     # nincs telepitett Chromium
            pytest.skip(f"Chromium nem indithato: {exc}")
        yield b
        b.close()


def _settle(page) -> None:
    page.wait_for_function("() => !document.querySelector('#view .loading') && !document.documentElement.classList.contains('i18n-loading')",
                           timeout=15000)
    page.wait_for_timeout(250)


def _routes(page) -> list[str]:
    return page.eval_on_selector_all(".tabs a", "els => els.map(a => a.getAttribute('href'))")


def _overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


def _small_targets(page) -> list[str]:
    return page.evaluate(f"""() => [...document.querySelectorAll({INTERACTIVE!r})].filter(el => {{
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') return false;
        if (r.bottom < 0 || r.top > window.innerHeight * 3) return false;
        const narrow = el.matches('.btn.icon, .seg') && r.width < {TOUCH} - 0.5;
        return r.height < {TOUCH} - 0.5 || narrow;
    }}).map(el => (el.id || el.className || el.tagName) + ' ' + Math.round(el.getBoundingClientRect().width) + 'x'
                  + Math.round(el.getBoundingClientRect().height) + ' ' + (el.textContent || '').trim().slice(0, 20))""")


@pytest.mark.parametrize("width", WIDTHS)
def test_every_view_fits_the_screen(ui_server, browser, width):
    shots = os.environ.get("SCREENSHOT_DIR")
    problems = []
    for scheme in ("light", "dark"):
        page = _new_page(browser, ui_server, viewport={"width": width, "height": 900}, color_scheme=scheme, locale="hu-HU",
                                has_touch=width <= PHONE)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(ui_server + "/#/")
        _settle(page)
        session_id = page.evaluate("fetch('/api/practice').then(r => r.json()).then(d => d.sessions[0].id)")
        for route in _routes(page) + [f"#/practice/{session_id}"]:
            page.evaluate(f"location.hash = {route!r}")
            _settle(page)
            over = _overflow(page)
            if over > 1:
                problems.append(f"{width}px {scheme} {route}: {over}px vizszintes gorgetes")
            if width <= PHONE and scheme == "light":
                small = _small_targets(page)
                if small:
                    problems.append(f"{width}px {route}: 44 px alatti erintesi felulet: {small[:5]}")
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                name = route.strip("#/").split("/")[0] + ("_task" if route.count("/") > 1 else "")
                page.screenshot(path=str(Path(shots) / f"{width}-{scheme}-{name}.png"), full_page=True)
        assert not errors, errors
        page.close()
    assert not problems, "\n".join(problems)


def test_phone_reaches_every_view_in_two_taps(ui_server, browser):
    page = _new_page(browser, ui_server, viewport={"width": 360, "height": 780}, locale="hu-HU", has_touch=True)
    page.goto(ui_server + "/#/")
    _settle(page)
    assert not page.is_visible(".tabs") and page.is_visible(".bottom-nav")
    routes = _routes(page)
    page.click("#more-btn")                                                   # 1. koppintas
    assert page.is_visible(".more-list")
    listed = page.eval_on_selector_all(".more-list a", "els => els.map(a => a.getAttribute('href'))")
    assert set(listed) == set(routes)
    assert set(routes) == {"#/learn", "#/practice", "#/daily", "#/leaderboard", "#/profile", "#/info"}
    page.click(".more-list a[href='#/info']")                                 # 2. koppintas
    _settle(page)
    assert page.evaluate("location.hash") == "#/info" and not page.is_visible(".more-list")
    assert page.get_attribute(".more-list a[href='#/info']", "aria-current") == "page"
    page.click(".bottom-nav a[href='#/practice']")                            # also sav: egy koppintas
    _settle(page)
    assert page.is_visible("#pr-form")
    page.close()


def test_desktop_keeps_the_top_navigation(ui_server, browser):
    page = _new_page(browser, ui_server, viewport={"width": 1280, "height": 900}, locale="hu-HU")
    page.goto(ui_server + "/#/")
    _settle(page)
    assert page.is_visible(".tabs") and not page.is_visible(".bottom-nav")
    # minden ful lathato (nem vagodik le): a jobb szeluk a kepernyon belul van
    rights = page.eval_on_selector_all(".tabs a", "els => els.map(a => a.getBoundingClientRect().right)")
    assert max(rights) <= 1280
    page.close()



def test_only_hungarian_for_launch(ui_server, browser):  # noqa: F811
    # korabban elmentett angol beallitas mellett is magyar, es nincs nyelvvalaszto (#46)
    page = _new_page(browser, ui_server, viewport={"width": 1280, "height": 900}, locale="en-US")
    page.add_init_script("try { localStorage.setItem('sw-lang', 'en'); } catch (e) {}")
    page.goto(ui_server + "/#/learn")
    _settle(page)
    assert page.evaluate("document.documentElement.lang") == "hu"
    assert page.query_selector(".lang-select") is None and "Tanulás" in page.inner_text("h1")
    page.close()
