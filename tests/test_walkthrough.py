"""A lecke levezetese: minden leckehez van, a kiirt keplet a kiirt szamokbol kijon (kezzel ellenorizheto), a charton a
szintjei megjelennek, es a chart elemei folott (eger) a nevuk latszik."""
from __future__ import annotations

import copy

import pytest

from stockwatch import accounts, db, learning, webauth

from .test_admin_ui import site  # noqa: F401  (pytest fixture)
from .test_practice import env, frames  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401

PW = "helyes-lo-elem-tuzfal"
# kulcs -> (argumentumok) -> (bal oldal, jobb oldal): a kiirt egyenloseg ket oldala
CHECKS = {
    "l_body_up": lambda a: (abs(a[0] - a[1]), a[2]), "l_body_down": lambda a: (abs(a[0] - a[1]), a[2]),
    "l_range": lambda a: (a[0] - a[1], a[2]),
    "l_ema_step": lambda a: (a[0] * a[1] + a[2] * a[3], a[4]),
    "l_ema_dist": lambda a: ((a[0] - a[1]) / a[1] * 100, a[2]),
    "l_to_res": lambda a: ((a[0] - a[1]) / a[2], a[3]), "l_to_sup": lambda a: ((a[0] - a[1]) / a[2], a[3]),
    "l_res_above": lambda a: ((a[0] - a[1]) / a[2], a[3]),
    "l_db_depth": lambda a: (a[0] - a[1], a[2]), "l_db_target": lambda a: (a[0] + a[1], a[2]),
    "l_stop_long": lambda a: (a[2] - a[1] * a[0], a[3]), "l_stop_short": lambda a: (a[2] + a[1] * a[0], a[3]),
    "l_r": lambda a: (abs(a[0] - a[1]), a[2]),
    "l_target_long": lambda a: (a[1] + a[0] * a[2], a[3]), "l_target_short": lambda a: (a[1] - a[0] * a[2], a[3]),
    "l_short_pnl": lambda a: ((a[1] - a[0]) / a[2], a[3]),
    "l_rsi": lambda a: (100 - 100 / (1 + a[0] / a[1]), a[2]),
    "l_bo_stop": lambda a: (a[0] - a[1] * a[2], a[3]),
    "z_rps": lambda a: (abs(a[0] - a[1]), a[2]), "z_shares": lambda a: (a[0] / a[1], a[2]),
    "z_risk": lambda a: (a[0] * a[1], a[2]), "z_value": lambda a: (a[0] * a[1], a[2]),
}


@pytest.mark.parametrize("level", range(1, 11))                       # gyujteskor adatbazis nelkul
def test_every_lesson_has_a_checkable_walkthrough(level):
    w = learning.steps(level)
    assert w and w["lines"]
    checked = 0
    for line in w["lines"]:
        if line["key"] in CHECKS:
            left, right = CHECKS[line["key"]]([float(x) for x in line["args"]])
            assert abs(left - right) <= 0.011, (level, line["text"])           # a kiirt (kerekitett) szamokbol
            checked += 1
    assert checked or level in (1,) or w["levels"], level
    assert all(isinstance(lv["v"], float) and lv["label"] for lv in w["levels"])


def test_unknown_walkthrough_is_rejected():
    data = copy.deepcopy(list(learning.lessons()))
    data[0]["walkthrough"] = "nincs-ilyen"
    with pytest.raises(ValueError, match="walkthrough"):
        learning.validate(data)


def test_lesson_chart_names_elements_on_hover(site, browser, env):  # noqa: F811
    accounts.mark_onboarded(env["user"])
    with db.connection() as conn, conn.cursor() as cur:                                   # az 5. szint nyitva
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 1, now())",
                         [(env["user"], lv) for lv in range(1, 5)])
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"),
                               "url": site}])
    page.goto(site + "/#/learn/5")
    page.wait_for_selector("#lesson-chart .candle")
    assert page.locator(".walk-steps li").count() >= 4
    assert page.locator("#lesson-chart [data-tip^='l:']").count() >= 3                  # belepo, stop, cel a charton
    for selector, name in (("#lesson-chart .candle >> nth=-40", "candle"), ("#lesson-chart text.hlabel >> nth=0", "Level")):
        page.hover(selector, force=True)
        page.wait_for_function("() => !document.getElementById('tooltip').hidden")
        assert name in page.inner_text("#tooltip"), selector
        page.mouse.move(1, 1)
    # a vonal egy pontja (a befoglalo doboz kozepe nem esik a vonalra)
    x, y = page.evaluate("""() => { const p = document.querySelector("#lesson-chart [data-tip=ema20]");
        const pt = p.getPointAtLength(p.getTotalLength() * 0.3), m = p.getScreenCTM();
        return [pt.x * m.a + pt.y * m.c + m.e, pt.x * m.b + pt.y * m.d + m.f]; }""")
    page.mouse.move(x, y)
    page.wait_for_function("() => !document.getElementById('tooltip').hidden")
    assert "EMA20" in page.inner_text("#tooltip")
    page.mouse.move(1, 1)
    assert page.evaluate("document.getElementById('tooltip').hidden")
    page.close()
