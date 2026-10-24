"""Feladattipusok a bongeszoben (#50-#53), telefonmeretben, erintessel: a stop erintessel letheto es huzassal
modosithato, a szintek / trendvonal rajzolhato es visszavonhato, az alakzat valaszthato, a pozicioméret mezoi
szambillentyuzetet adnak; beadas utan ertekeles. Oldalra soha nem gorog."""
from __future__ import annotations

import pytest

from stockwatch import accounts, webauth
from stockwatch.practice import session

from .test_onboarding_e2e import server  # noqa: F401  (pytest fixture)
from .test_practice import PW, env, frames  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401


def _round(env, kind: str, want: dict | None = None) -> str:  # noqa: F811
    for seed in range(1, 80):
        sid = session.create(env["user"], env["settings"], seed=seed, rounds=1, kinds_mix={kind: 1})["id"]
        prompt = session.task_payload(env["user"], sid, 0)["prompt"]
        if not want or all(prompt.get(k) == v for k, v in want.items()):
            return sid
    pytest.skip(f"nincs ilyen feladat: {kind} {want}")


def _page(browser, server, env):  # noqa: F811
    accounts.mark_onboarded(env["user"])
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, locale="hu-HU")
    page.add_init_script("try { localStorage.setItem('sw-lang', 'hu'); } catch (e) {}")
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"), "url": server}])
    return page


def _open(page, server, sid):  # noqa: F811
    page.goto(f"{server}/#/practice/{sid}")
    page.wait_for_selector("#pr-dform")
    page.wait_for_function("() => !document.documentElement.classList.contains('i18n-loading')")
    page.wait_for_timeout(200)


def _chart(page, fx: float, fy: float) -> tuple[float, float]:
    box = page.locator("#pr-chart").bounding_box()
    return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy


def _submit(page):
    page.click("#pr-dform button[type=submit]")
    page.wait_for_selector(".pr-verdict-label")
    assert "Értékelés" in page.inner_text(".pr-verdict-label")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_stop_is_placed_by_touch_and_adjusted_by_dragging(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, _round(env, "stop", {"side": "long"}))
    page.touchscreen.tap(*_chart(page, 0.5, 0.7))
    first = page.input_value("input[name=stop]")
    assert first and "ATR" in page.inner_text("#pr-risk")
    x, y = _chart(page, 0.5, 0.7)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 25, steps=5)                                  # huzassal lejjebb
    page.mouse.up()
    assert float(page.input_value("input[name=stop]")) < float(first)
    _submit(page)
    page.close()


def test_pattern_choice(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, _round(env, "pattern"))
    assert page.locator("[data-choice]").count() == 4
    page.click("[data-choice] >> nth=1")
    assert page.get_attribute("[data-choice] >> nth=1", "aria-checked") == "true"
    _submit(page)
    page.close()


@pytest.mark.parametrize("mode", ["levels", "trend"])
def test_levels_and_trendline_are_drawn_and_undone(server, browser, env, mode):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, _round(env, "levels", {"mode": mode}))
    page.touchscreen.tap(*_chart(page, 0.3, 0.3))
    page.touchscreen.tap(*_chart(page, 0.8, 0.5))
    drawn = page.inner_text("#pr-drawn")
    assert ("Vonalak" in drawn and drawn.count(", ") == 1) if mode == "levels" else "2 / 2" in drawn   # magyar tizedesvesszo
    page.click("#pr-undo")
    assert page.inner_text("#pr-drawn") != drawn
    page.touchscreen.tap(*_chart(page, 0.8, 0.5))
    _submit(page)
    page.close()


def test_sizing_fields_give_a_numeric_keyboard(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, _round(env, "sizing"))
    assert page.get_attribute("input[name=shares]", "inputmode") == "numeric"
    assert page.get_attribute("input[name=risk]", "inputmode") == "decimal"
    page.fill("input[name=shares]", "10")
    page.fill("input[name=risk]", "25")
    if page.query_selector("input[name=rr]"):
        assert page.get_attribute("input[name=rr]", "inputmode") == "decimal"
        page.fill("input[name=rr]", "2")
    _submit(page)
    page.close()
