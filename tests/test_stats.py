"""Statisztikam (#78): a mutatok egy rogzitett dontessoron a kezzel szamolt ertekkel egyeznek; a Monte Carlo seedelt
es gyors; keves adatnal uzenet; a kizart (stats_excluded) felhasznalo sajat oldala is mukodik; a bongeszoben a
chartok telefonon is olvashatok, vilagos es sotet temaban."""
from __future__ import annotations

import time
from datetime import date

import pytest

from stockwatch import accounts, db, stats, webauth
from stockwatch.practice import session

from .test_practice import PW, env, frames, server  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401

ROW = [1.0, -1.0, 2.0, -0.5, -1.0, -1.0, 3.0, 0.5, -1.0]


def test_metrics_match_hand_calculation():
    m = stats.metrics(ROW, decided=12, entered=10)
    assert m["closed"] == 9 and m["entry_rate"] == round(10 / 12, 3)
    assert m["win_rate"] == round(4 / 9, 3)
    assert m["avg_win"] == round((1 + 2 + 3 + 0.5) / 4, 3) and m["avg_loss"] == round(-4.5 / 5, 3)
    assert m["expectancy"] == round(2.0 / 9, 3) and m["total_r"] == 2.0
    assert m["profit_factor"] == round(6.5 / 4.5, 3)
    assert m["losing_streak"] == 3                                    # -0.5, -1, -1
    # toke-gorbe: 0, 1, 0, 2, 1.5, 0.5, -0.5, 2.5, 3, 2 -> csucs 2, melypont -0.5
    assert m["max_drawdown"] == 2.5
    assert stats.metrics([], 3, 0)["expectancy"] is None
    assert stats.metrics([1.0, 2.0], 2, 2)["profit_factor"] is None    # vesztes nelkul nincs ertelmezve


def test_histogram_bins_and_edges():
    h = stats.histogram([-5, -3, -2.5, 0, 0.99, 4.99, 5, 9])
    assert len(h) == len(stats.EDGES) + 1 and sum(h) == pytest.approx(1.0)
    assert h[0] == 1 / 8                                              # < -3
    assert h[1] == 2 / 8                                              # -3 ... -2 (a -3 ide tartozik)
    assert h[-1] == 2 / 8                                             # >= 5


def test_monte_carlo_is_seeded_and_fast():
    rs = ROW * 30
    t0 = time.perf_counter()
    a = stats.monte_carlo(rs)
    assert time.perf_counter() - t0 < 0.2                             # 1000 keveres < 200 ms
    assert a == stats.monte_carlo(rs)                                 # ugyanarra a bemenetre ugyanaz
    assert a["drawdown"]["5"] <= a["drawdown"]["50"] <= a["drawdown"]["95"]
    assert a["streak"]["5"] <= a["streak"]["50"] <= a["streak"]["95"]
    assert a["actual"] == {"drawdown": round(stats.max_drawdown(rs), 2), "streak": stats.losing_streak(rs)}
    assert stats.monte_carlo([-1.0] * 10)["streak"] == {"5": 10, "50": 10, "95": 10}


def _play(env, n: int, seed: int = 5) -> None:  # noqa: F811
    sid = session.create(env["user"], env["settings"], seed=seed, rounds=n)["id"]
    for k in range(n):
        atr = session.task_payload(env["user"], sid, k)["chart"]["atr"]
        session.decide(env["user"], sid, k, {"action": "long", "stop": round(100 - 2 * atr, 2), "days": 10})


def test_few_decisions_message_and_excluded_user_still_sees_own(env):  # noqa: F811
    _play(env, 5)
    with db.connection() as conn:
        conn.execute("UPDATE users SET stats_excluded = true WHERE id = %s", (env["user"],))
    s = stats.mine(env["user"])
    assert s["few"] and s["histogram"] is None and s["monte_carlo"] is None
    assert s["metrics"]["decisions"] == 5 and len(s["equity"]) == s["metrics"]["closed"]
    assert len(s["calendar"]) == stats.CAL_DAYS and sum(d["n"] for d in s["calendar"]) == 5
    assert s["breakdown"]["side"][0]["key"] == "long" and s["breakdown"]["side"][0]["n"] == 5
    assert stats.mine(env["user"], source="daily")["metrics"]["decisions"] == 0
    with pytest.raises(ValueError):
        stats.mine(env["user"], period="7")
    assert stats.calendar([], date(2026, 11, 2))[-1]["date"] == "2026-11-02"


def test_stats_page_on_phone(env, server, browser):  # noqa: F811
    _play(env, 20, seed=5)
    _play(env, 8, seed=6)
    s = stats.mine(env["user"])
    accounts.mark_onboarded(env["user"])
    for theme in ("light", "dark"):
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, locale="hu-HU")
        page.add_init_script(f"try {{ localStorage.setItem('sw-lang', 'hu'); localStorage.setItem('sw-theme', '{theme}'); }} catch (e) {{}}")
        page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"), "url": server}])
        page.goto(f"{server}/#/profile")
        page.click("a[href='#/stats']")
        page.wait_for_selector("h1:has-text('Statisztikám')")
        if s["few"]:
            assert "Még kevés a döntés" in page.inner_text("#view")
        else:
            assert page.locator(".st-chart").count() >= 2 and "Monte Carlo" in page.inner_text("#view")
        assert page.locator(".heat-cell:not(.out)").count() == stats.CAL_DAYS
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.click("[data-period='30']")
        page.wait_for_selector("[data-period='30'][aria-pressed=true]")
        page.select_option("#st-source", "daily")
        page.wait_for_function("() => document.querySelector('.kpi .value').textContent.trim() === '0'")
        page.close()
