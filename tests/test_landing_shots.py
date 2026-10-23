"""A kezdooldal kepernyokepei nyelvenkent (app/web/img/feladat-<nyelv>.png, itelet-<nyelv>.png): ugyanaz a
rogzitett helyzet mind a negy nyelven, telefonmeretben. Csak kerésre fut:

    LANDING_SHOTS=app/web/img pytest tests/test_landing_shots.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from stockwatch import accounts, webauth
from stockwatch.practice import session

from .test_onboarding_e2e import server  # noqa: F401  (pytest fixture)
from .test_practice import PW, env, frames  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401

SHOTS = os.environ.get("LANDING_SHOTS")


def _good_seed(env) -> int:
    """Az elso helyzet, ahol az alapertelmezett dontes (long, kovetkezo nyitas, 2 ATR stop, 10 nap) jo dontes -
    a kezdooldalon ez mutat jol."""
    for seed in range(1, 200):
        sid = session.create(env["user"], env["settings"], seed=seed, rounds=1)["id"]
        atr = session.task_payload(env["user"], sid, 0)["chart"]["atr"] or 1
        done = session.decide(env["user"], sid, 0, {"action": "long", "entry": "open", "stop": round(100 - 2 * atr, 2), "days": 10})
        session.delete(env["user"], sid)
        if done["reveal"]["review"]["verdict"]["category"] == "good":
            return seed
    raise AssertionError("nincs jo dontesu helyzet az elso 200 seedben")


@pytest.mark.skipif(not SHOTS, reason="csak LANDING_SHOTS=<mappa> mellett")
@pytest.mark.parametrize("lang", ["en", "hu", "de", "es"])
def test_landing_screenshots(server, browser, env, lang):  # noqa: F811
    accounts.mark_onboarded(env["user"])
    seed = _good_seed(env)                                             # mindegyik nyelven ugyanaz a helyzet
    sid = session.create(env["user"], env["settings"], seed=seed, rounds=1)["id"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="en-US", has_touch=True)
    page.add_init_script(f"try {{ localStorage.setItem('sw-lang', '{lang}'); }} catch (e) {{}}")
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"), "url": server}])
    page.goto(f"{server}/#/practice/{sid}")
    page.wait_for_selector("#pr-dform")
    page.wait_for_function("() => !document.documentElement.classList.contains('i18n-loading')")
    page.wait_for_timeout(300)
    out = Path(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"feladat-{lang}.png"))
    page.click("#pr-dform button[type=submit]")                       # az alapertelmezett long, 2 ATR stoppal
    page.click("#pr-skipanim")
    page.wait_for_selector(".pr-verdict-label")
    page.evaluate("document.querySelector('.pr-verdict').closest('section').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(300)
    page.screenshot(path=str(out / f"itelet-{lang}.png"))
    page.close()
