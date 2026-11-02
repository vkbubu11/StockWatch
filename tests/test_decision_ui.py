"""A dontesi urlap a bongeszoben: stop / cel / limit huzasa a charton (#77), billentyuparancsok (#79 d) es a telefonos
also gombsor (#79 e). Mezo <-> vonal ketiranyu kotes long es short esetén, ervenytelen allas nem kuldheto be,
billentyuvel is allithato, erintessel huzva az oldal nem gorget, a bekuldott ertek az, ami a vonalon latszik."""
from __future__ import annotations

import json

from stockwatch import accounts, webauth
from stockwatch.practice import session

from .test_onboarding_e2e import server  # noqa: F401  (pytest fixture)
from .test_practice import PW, env, frames  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401


def _page(browser, server, env, width=390):  # noqa: F811
    accounts.mark_onboarded(env["user"])
    page = browser.new_page(viewport={"width": width, "height": 844}, has_touch=True, locale="hu-HU")
    page.add_init_script("try { localStorage.setItem('sw-lang', 'hu'); } catch (e) {}")
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"), "url": server}])
    return page


def _open(page, server, env, rounds=1):  # noqa: F811
    sid = session.create(env["user"], env["settings"], seed=7, rounds=rounds)["id"]
    page.goto(f"{server}/#/practice/{sid}")
    page.wait_for_selector("#pr-chart [data-handle=stop]", state="attached")
    page.wait_for_function("() => !document.documentElement.classList.contains('i18n-loading')")
    page.wait_for_timeout(200)
    return sid


def _handle(page, key):
    return float(page.get_attribute(f"#pr-chart [data-handle={key}]", "data-v"))


def _center(page, key):
    box = page.locator(f"#pr-chart [data-handle={key}] circle").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _drag(page, key, dy, touch=False):
    x, y = _center(page, key)
    if not touch:
        page.mouse.move(x, y)
        page.mouse.down()
        for n in range(1, 6):
            page.mouse.move(x, y + dy * n / 5)
        page.mouse.up()
        return
    cdp = page.context.new_cdp_session(page)                          # valodi erintes (a Playwright csak koppintani tud)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for n in range(1, 6):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y + dy * n / 5}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def test_lines_and_fields_stay_in_sync_long_and_short(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, env)
    page.uncheck("input[name=magnet]")
    assert _handle(page, "stop") == float(page.input_value("input[name=stop]"))      # 2 ATR alapertek
    before = _handle(page, "stop")
    page.evaluate("window.scrollTo(0, 120)")
    scroll = page.evaluate("window.scrollY")
    _drag(page, "stop", 40, touch=True)
    assert page.evaluate("window.scrollY") == scroll                                  # huzas kozben nem gorget
    after = float(page.input_value("input[name=stop]"))
    assert after < before and _handle(page, "stop") == after                            # vonal -> mezo
    assert "ATR" in page.get_attribute("#pr-chart [data-handle=stop]", "aria-valuetext")
    page.fill("input[name=stop]", "96.5")                                               # mezo -> vonal
    assert _handle(page, "stop") == 96.5
    _drag(page, "target", -30)                                                           # a "Cél?" fogantyu huzasa beallitja a celt
    target = float(page.input_value("input[name=target]"))
    assert target > 100 and _handle(page, "target") == target
    assert " R" in page.text_content("#pr-chart [data-handle=target] .hinfo")
    page.locator("body").click(position={"x": 5, "y": 5})                               # a mezoben a billentyu nem el
    page.keyboard.press("s")                                                             # short: tukrozott alapertekek
    stop = float(page.input_value("input[name=stop]"))
    assert stop > 100 and _handle(page, "stop") == stop and page.input_value("input[name=target]") == ""
    _drag(page, "stop", -30)
    assert float(page.input_value("input[name=stop]")) > stop
    page.close()


def test_invalid_position_cannot_be_submitted(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, env)
    page.fill("input[name=stop]", "101")                                                 # long stop a belepo felett
    assert page.is_disabled("#pr-dform button[type=submit]")
    assert "Longnál a stop a belépő alatt legyen." in page.inner_text("#pr-err")
    assert page.get_attribute("#pr-chart [data-handle=stop]", "aria-invalid") == "true"
    assert "Stop ✗" in page.text_content("#pr-chart")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("s")                                                             # shortnal ugyanez tukrozve
    page.fill("input[name=stop]", "99")
    assert page.is_disabled("#pr-dform button[type=submit]") and "Shortnál" in page.inner_text("#pr-err")
    page.fill("input[name=stop]", "104")
    assert not page.is_disabled("#pr-dform button[type=submit]") and page.inner_text("#pr-err") == ""
    page.close()


def test_keyboard_adjusts_the_focused_line_and_submits_what_is_shown(server, browser, env):  # noqa: F811
    page = _page(browser, server, env, width=1200)
    sid = _open(page, server, env)
    atr = session.task_payload(env["user"], sid, 0)["chart"]["atr"]
    page.focus("#pr-chart [data-handle=stop]")
    before = _handle(page, "stop")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    assert _handle(page, "stop") == round(before + 0.2 * atr, 2)                        # 2 x 0,1 ATR, kerekitve
    assert page.evaluate("document.activeElement.dataset.handle") == "stop"            # az ujrarajzolas utan is fokuszban
    page.uncheck("input[name=magnet]")
    _drag(page, "stop", 23)
    shown = _handle(page, "stop")
    page.locator("body").click(position={"x": 5, "y": 5})
    with page.expect_request(lambda r: r.url.endswith("/decision")) as req:
        page.keyboard.press("Enter")                                                      # Enter = bekuldes
    assert json.loads(req.value.post_data)["stop"] == shown == float(page.input_value("input[name=stop]"))
    page.close()


def test_keyboard_only_playthrough(server, browser, env):  # noqa: F811
    page = _page(browser, server, env, width=1200)
    _open(page, server, env, rounds=2)
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("?")
    assert page.is_visible("#pr-keys")
    page.keyboard.press("k")                                                             # kihagyas
    assert page.input_value("input[name=action]") == "skip"
    page.keyboard.press("l")
    assert page.input_value("input[name=action]") == "long"
    page.keyboard.press("Enter")
    page.wait_for_selector("#pr-reveal .card")
    page.keyboard.press("Enter")                                                         # mutasd egyben
    page.wait_for_selector(".pr-verdict-label")
    page.keyboard.press("ArrowRight")                                                    # kovetkezo helyzet
    page.wait_for_selector("h1:has-text('Helyzet 2 / 2')")
    page.wait_for_selector("#pr-chart [data-handle=stop]", state="attached")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("k")
    page.keyboard.press("Enter")
    page.wait_for_selector("#pr-reveal .card")
    page.keyboard.press("Enter")
    page.wait_for_selector(".pr-verdict-label")
    page.fill("input[name=days]", "5") if page.is_enabled("input[name=days]") else None   # mezoben a billentyuk nem elnek
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("h1:has-text('A kör összegzése')")
    page.close()


def test_phone_dock_keeps_submit_reachable_without_scrolling(server, browser, env):  # noqa: F811
    page = _page(browser, server, env)
    _open(page, server, env)
    assert page.evaluate("window.scrollY") == 0
    box = page.locator("#pr-dform button[type=submit]").bounding_box()
    nav = page.locator(".bottom-nav").bounding_box()
    assert box["y"] + box["height"] <= nav["y"] + 1                                      # az also navigacio folott, latszik
    for side in ("skip", "long", "short"):
        b = page.locator(f".pr-dock [data-side={side}]").bounding_box()
        assert 0 <= b["y"] and b["y"] + b["height"] <= 844
    chart = page.locator("#pr-chart").bounding_box()
    dock = page.locator(".pr-dock").bounding_box()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")                       # a lap aljan semmit nem takar
    last = page.locator("#pr-decision").bounding_box()
    assert last["y"] + last["height"] <= page.locator(".pr-dock").bounding_box()["y"] + 1
    assert chart["y"] < dock["y"]
    page.click(".pr-dock button[type=submit]")
    page.wait_for_selector("#pr-reveal .card")
    page.close()


def test_weekly_chart_shows_nothing_after_the_decision_day():
    from stockwatch.practice import synthetic

    df = synthetic.frame("SYN0003")
    for i in (400, 900, 1500):
        w = session.weekly_chart(df, i)
        assert w == session.weekly_chart(df.iloc[: i + 1], i)                          # a jovo nem szamit bele
        assert w["bars"][-1]["i"] == 0 and w["bars"][-1]["c"] == 100.0                 # a dontes napjan zarul
        assert len(w["bars"]) == session.WEEKS and w["atr"] > 0
        last = df.iloc[i - session.WEEK + 1: i + 1]
        assert w["bars"][-1]["h"] == round(float(last["high"].max()) * 100 / float(df["close"].iloc[i]), 2)


def _geometry(page):
    return page.evaluate("""() => ({
        candles: [...document.querySelectorAll('#pr-chart .candle rect')].map(r => [r.getAttribute('x'), r.getAttribute('y'), r.getAttribute('height')]),
        lines: [...document.querySelectorAll('#pr-chart .hline')].map(l => l.getAttribute('y1')),
        axis: [...document.querySelectorAll('#pr-chart .yaxis text')].map(t => t.textContent) })""")


def test_axis_price_pct_atr_draw_the_same_geometry(server, browser, env):  # noqa: F811
    page = _page(browser, server, env, width=1200)
    _open(page, server, env)
    price = _geometry(page)
    assert all("%" not in a and "ATR" not in a for a in price["axis"])
    page.click("[data-axis=pct]")
    pct = _geometry(page)
    page.click("[data-axis=atr]")
    atr = _geometry(page)
    assert price["candles"] == pct["candles"] == atr["candles"] and price["lines"] == pct["lines"] == atr["lines"]
    assert all(a.endswith("%") for a in pct["axis"]) and all(a.endswith("ATR") for a in atr["axis"])
    assert any(a in ("0%", "0 ATR") for a in pct["axis"] + atr["axis"])                 # a dontes napi zaro = 0
    page.reload()
    page.wait_for_selector("#pr-chart [data-handle=stop]", state="attached")
    assert page.get_attribute("[data-axis=atr]", "aria-pressed") == "true"              # eszkozonkent megmarad
    page.check("#pr-weekly-on")                                                          # heti nezet
    assert page.locator("#pr-weekly-chart .candle").count() == session.WEEKS
    assert page.locator("#pr-weekly-chart .candle.future").count() == 0
    page.close()
