"""Gyertyankenti visszajatszas es poziciokezeles (#76): kezelesi modban az API soha nem ad ki tobb jovobeli gyertyat,
mint amennyit a felhasznalo mar latott; a kezelesi muvelet csak a kiadott gyertyakra epulhet (a jovore hivatkozo
keres 400); muvelet nelkul az eredmeny es az itelet bitre azonos a kezeles nelkulivel; a kezelt R a motorbol jon."""
from __future__ import annotations

import json

import pytest

from stockwatch.practice import replay, session, store

from .test_practice import _call, env, frames, server  # noqa: F401
from .test_responsive import browser, sync_api  # noqa: F401


def _long(task: dict) -> dict:
    atr = task["chart"]["atr"]
    return {"action": "long", "stop": round(100 - 2 * atr, 2), "days": 15}


def _managed(env, seed: int = 11) -> tuple[str, int]:  # noqa: F811
    """Egy kezelesi modu kor, az elso helyzetben long dontessel; (kor, k)."""
    sid = session.create(env["user"], env["settings"], seed=seed, rounds=3, manage=True)["id"]
    out = session.decide(env["user"], sid, 0, _long(session.task_payload(env["user"], sid, 0)))
    assert "replay" in out and "reveal" not in out
    return sid, 0


def _future_count(text: str) -> int:
    """A valaszban szereplo jovobeli gyertyak (pozitiv indexu "i") szama."""
    found: list[int] = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("i"), int) and x["i"] > 0 and "o" in x:
                found.append(x["i"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(json.loads(text))
    return len(set(found))


def test_api_never_sends_more_future_than_shown(env, server):  # noqa: F811
    status, text = _call(server + "/api/practice/session", {"rounds": 2, "seed": 11, "manage": True})
    sid = json.loads(text)["session"]["id"]
    status, text = _call(server + f"/api/practice/session/{sid}/task/0")
    assert _future_count(text) == 0                                             # dontes elott
    task = json.loads(text)
    status, text = _call(server + f"/api/practice/session/{sid}/task/0/decision", _long(task))
    assert status == 200, text
    body = json.loads(text)
    assert _future_count(text) == 0 and body["replay"]["shown"] == 0
    for leak in ('"review"', '"verdict"', '"symbol"', '"date"', '"strategies"', '"baseline"', '"mean_r": -', '"mean_r": 0'):
        assert leak not in text                                                 # az ered meny es a papir meg rejtett
    shown = 0
    for n in (1, 5, 1):
        status, text = _call(server + f"/api/practice/session/{sid}/task/0/step", {"n": n})
        assert status == 200, text
        shown += n
        assert _future_count(text) == shown == json.loads(text)["replay"]["shown"]
        status, again = _call(server + f"/api/practice/session/{sid}/task/0")       # ujratoltve sem tobb
        assert _future_count(again) == shown and "reveal" not in json.loads(again)
    status, listing = _call(server + "/api/practice")
    assert '"mean_r": null' in listing or sid not in listing
    status, text = _call(server + f"/api/practice/session/{sid}/task/0/step", {"n": 6})
    assert status == 400                                                        # egyszerre legfeljebb 5
    status, text = _call(server + f"/api/practice/session/{sid}/task/0/finish", {})
    assert status == 200 and json.loads(text)["reveal"]["future"]               # a vegen minden kiderul


def test_management_only_on_revealed_bars(env, server):  # noqa: F811
    sid, k = _managed(env)
    url = server + f"/api/practice/session/{sid}/task/{k}"
    _call(url + "/step", {"n": 2})
    st = session.task_payload(env["user"], sid, k)["replay"]
    pos = st["position"]
    assert pos["open"] and pos["entered"]
    assert _call(url + "/manage", {"type": "exit", "at": 5})[0] == 400              # a jovore hivatkozik
    assert _call(url + "/manage", {"type": "exit", "at": 1})[0] == 400              # regi allapotra hivatkozik
    assert _call(url + "/manage", {"type": "stop", "at": 2, "stop": pos["stop"] - 1})[0] == 400   # a pozicio ellen
    last = st["bars"][-1]["c"]
    assert _call(url + "/manage", {"type": "stop", "at": 2, "stop": last + 0.5})[0] == 400       # a zaro folott
    assert _call(url + "/manage", {"type": "bogus", "at": 2})[0] == 400
    better = round((pos["stop"] + min(last, pos["entry"])) / 2, 2)
    status, text = _call(url + "/manage", {"type": "stop", "at": 2, "stop": better})
    assert status == 200, text
    assert json.loads(text)["replay"]["position"]["stop"] == better
    status, text = _call(url + "/manage", {"type": "half", "at": 2})
    assert status == 200 and json.loads(text)["replay"]["position"]["halved"]
    assert _call(url + "/manage", {"type": "half", "at": 2})[0] == 400               # csak egyszer
    status, text = _call(url + "/manage", {"type": "exit", "at": 2})
    assert status == 200 and json.loads(text)["replay"]["position"]["exiting"]
    assert _call(url + "/manage", {"type": "exit", "at": 2})[0] == 400               # mar nincs nyitott pozicio


def test_without_actions_result_and_verdict_are_identical(env):  # noqa: F811
    plain = session.create(env["user"], env["settings"], seed=11, rounds=3)["id"]
    managed, k = _managed(env, seed=11)
    task = session.task_payload(env["user"], plain, 0)
    off = session.decide(env["user"], plain, 0, _long(task))["reveal"]
    session.replay_action(env["user"], managed, k, "step", {"n": 3})
    on = session.replay_action(env["user"], managed, k, "finish", {})["reveal"]
    assert "manage" not in on
    for key in ("user", "review", "strategies", "reference", "baseline", "future", "points"):
        assert on[key] == off[key], key
    assert not session.task_payload(env["user"], managed, k).get("replay")       # lezarult
    # kezeles nelkul (off) a regi viselkedes: a dontes azonnal felfed
    assert "replay" not in session.decide(env["user"], plain, 1, {"action": "skip"})


def test_managed_trade_comes_from_the_engine(env):  # noqa: F811
    sid, k = _managed(env)
    session.replay_action(env["user"], sid, k, "step", {"n": 2})
    st = session.task_payload(env["user"], sid, k)["replay"]
    if not st["position"]["open"]:
        pytest.skip("a kotes mar az elso napokban lezarult")
    session.replay_action(env["user"], sid, k, "manage", {"type": "half", "at": 2})
    session.replay_action(env["user"], sid, k, "step", {"n": 1})
    session.replay_action(env["user"], sid, k, "manage", {"type": "exit", "at": 3})
    out = session.replay_action(env["user"], sid, k, "finish", {})
    m = out["reveal"]["manage"]
    stored = store.load_session(sid, env["user"])["tasks"][k]
    future = stored["result"]["future"]
    assert m["user"]["exit"] == future[3]["o"] and m["user"]["reason"] == "manual"   # kiszallas: a kovetkezo nyitason
    assert m["r"] == pytest.approx(0.5 * m["half_r"] + 0.5 * (m["user"]["r"]), abs=2e-3)
    assert m["delta"] == pytest.approx(m["r"] - m["plan_r"], abs=2e-3)
    assert 0 <= m["points"] <= 10
    parts = out["reveal"]["points"]["parts"]
    assert parts[-1]["key"] == "manage" and stored["points"] == out["reveal"]["points"]["points"]
    assert m["best_r"] is None or m["best_r"] >= max(m["plan_r"] or -99, -99) - 0.5


def test_summary_waits_for_the_replay_and_daily_is_never_managed(env):  # noqa: F811
    sid, k = _managed(env)
    for n in (1, 2):
        session.decide(env["user"], sid, n, {"action": "skip"})
    s = session.summary_payload(env["user"], sid)
    assert s["replay"] == k and "summary" not in s and s["session"]["mean_r"] is None   # az eredmeny meg rejtett
    assert replay.active(store.load_session(sid, env["user"])["tasks"][k])              # es nem zarult le
    session.replay_action(env["user"], sid, k, "finish", {})
    assert "summary" in session.summary_payload(env["user"], sid)
    daily = session.create(env["user"], env["settings"], seed=3, rounds=1, daily="2026-11-02", manage=True)
    assert daily["manage"] is False


def test_replay_playable_on_phone_with_keys(env, server, browser):  # noqa: F811
    from stockwatch import accounts, webauth

    from .test_practice import PW

    sid = session.create(env["user"], env["settings"], seed=11, rounds=1, manage=True)["id"]
    accounts.mark_onboarded(env["user"])
    page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, locale="hu-HU")
    page.add_init_script("try { localStorage.setItem('sw-lang', 'hu'); } catch (e) {}")
    page.context.add_cookies([{"name": webauth.COOKIE, "value": accounts.login("anna@example.com", PW, "127.0.0.1"), "url": server}])
    page.goto(f"{server}/#/practice/{sid}")
    page.wait_for_selector("#pr-chart [data-handle=stop]", state="attached")
    page.click(".pr-dock button[type=submit]")
    page.wait_for_selector("#pr-manage")
    assert page.locator(".pr-replay-bar").bounding_box()["y"] < 844                    # a vezerlo alul latszik
    assert page.locator("#pr-chart .candle.future").count() == 0
    page.locator("#pr-manage h2").click()
    for n in (1, 2, 3):
        page.keyboard.press("ArrowRight")                                               # -> kovetkezo gyertya
        page.wait_for_function(f"() => document.querySelector('#pr-replay-pos').textContent.startsWith('{n} /')")
    assert page.locator("#pr-chart .candle.future").count() == 3
    if page.is_enabled("#pr-exit"):
        page.click("#pr-exit")
        page.wait_for_selector("#pr-pos li")
    page.locator("#pr-manage h2").click()
    page.keyboard.press("Enter")                                                         # mutasd egyben (vege)
    page.wait_for_selector(".pr-verdict-label")
    wide = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => !e.closest('.table-scroll') && e.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 8).map(e => e.tagName + '.' + e.className + '#' + e.id + ' ' + Math.round(e.getBoundingClientRect().right))""")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), wide
    page.close()
