"""Admin felulet a bongeszoben (#58), telefonmeretben: TOTP-beallitas az oldalon, utana minden ful hiba nelkul
megjelenik, es az oldal oldalra nem gorog."""
from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from stockwatch import accounts, admin, webapp, webauth

from .conftest import make_user
from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)
from .test_responsive import browser, sync_api  # noqa: F401

PW = "helyes-lo-elem-tuzfal"


@pytest.fixture
def site(env):  # noqa: F811
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_admin_page_on_a_phone(site, browser, env):  # noqa: F811
    make_user("root@example.com")
    admin.grant("root@example.com")
    errors: list[str] = []
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("root@example.com", PW, "127.0.0.1"), "url": site}])
    page.goto(site + "/admin")
    page.wait_for_selector("#totp")
    secret = page.inner_text("#totp-key").replace(" ", "")              # negyes csoportokban latszik
    page.fill("input[name=code]", admin.totp(secret, int(time.time() // admin.TOTP_STEP)))
    page.click("#totp button[type=submit]")
    page.wait_for_selector(".adm-tabs")
    for name in ("overview", "users", "content", "community", "system", "audit"):
        page.click(f"[data-tab={name}]")
        page.wait_for_function("() => !document.querySelector('#adm-body .loading')")
        page.wait_for_timeout(200)
        assert not page.query_selector("#adm-body .msg.err"), name
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name
    page.click("[data-tab=users]")
    page.wait_for_selector("[data-user]")
    page.click("[data-user]")
    page.wait_for_selector("[data-uact=export]")
    assert not errors, errors
    page.close()
