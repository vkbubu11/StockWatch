"""Vizsga es tanusitvany (#37): a feladatok az inditaskor rogzulnek, kozben nincs felfedes (a kod sem latszik, a kor
nem torolheto); a beadas / lejart ido utan az eredmeny egyszer rogzul; a sikeres vizsga tanusitvanya az ellenorzo
koddal nyilvanosan visszaellenorizheto, a sikertelene es az ismeretlen kod 404."""
from __future__ import annotations

import json

import pytest

from stockwatch import exams
from stockwatch.practice import session, store

from .test_practice import _call, env, frames, login_cookie, server  # noqa: F401  (pytest fixture-ok)
from .test_shares import _get


def test_exam_is_fixed_at_start_and_hidden_until_submitted(env):  # noqa: F811
    user = env["user"]
    created = exams.start(user, env["settings"])
    sid = created["id"]
    assert created["exam"] and created["code"] is None and created["rounds"] == exams.ROUNDS and created["deadline"]
    tasks = [(t["symbol"], t["date"]) for t in store.load_session(sid, user)["tasks"]]
    assert exams.start(user, env["settings"])["id"] == sid                           # a futo vizsga folytatodik
    out = session.decide(user, sid, 0, {"action": "skip"})
    assert out.get("hidden") and "reveal" not in out
    assert "reveal" not in session.task_payload(user, sid, 0) and session.summary_payload(user, sid)["hidden"]
    assert session.task_payload(user, sid, 0)["hints"] == "hidden"
    assert not store.delete_session(sid, user)                                       # nem torolheto
    assert exams.mine(user)["running"] == sid and exams.mine(user)["exams"][0]["finished"] is None
    for k in range(1, exams.ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    assert [(t["symbol"], t["date"]) for t in store.load_session(sid, user)["tasks"]] == tasks
    state = exams.mine(user)
    assert state["running"] is None and state["exams"][0]["finished"] and state["next_start"]
    assert "reveal" in session.task_payload(user, sid, 0)
    with pytest.raises(ValueError):
        exams.start(user, env["settings"])                                           # naponta egy


def test_certificate_is_verifiable_by_code(server, env, monkeypatch):  # noqa: F811
    user = env["user"]
    monkeypatch.setattr(exams, "PASS_SHARE", 0.0)                                    # a kuszob most mindenkit atenged
    status, body = _call(f"{server}/api/exams/start", {})
    sid = json.loads(body)["session"]["id"]
    for k in range(exams.ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    status, body = _call(f"{server}/api/exams")
    code = json.loads(body)["exams"][0]["code"]
    assert status == 200 and code
    cert = exams.certificate(code)
    assert cert["data"]["player"].startswith("Játékos #") and cert["data"]["levels_total"] >= 1
    import urllib.request

    req = urllib.request.Request(f"{server}/c/{code}", headers={"Cookie": "sw_lang=hu"})
    with urllib.request.urlopen(req, timeout=20) as res:
        text = res.read().decode("utf-8")
    assert code in text and "nem befektetési képesítés" in text and "anna@example.com" not in text
    status, page, _ = _get(f"{server}/c/{code}")                                     # suti nelkul: angol
    assert status == 200 and "not an investment qualification" in page.decode("utf-8")
    status, png, ctype = _get(f"{server}/c/{code}.png")
    assert status == 200 and ctype == "image/png" and png[:4] == b"\x89PNG"
    assert _get(f"{server}/c/abcdefghjkmn")[0] == 404


def test_expired_exam_is_settled_with_unanswered_tasks_failed(env, monkeypatch):  # noqa: F811
    user = env["user"]
    sid = exams.start(user, env["settings"])["id"]
    monkeypatch.setattr(session, "EXAM_MINUTES", 0)                                 # lejart az ido
    with pytest.raises(ValueError):
        session.decide(user, sid, 0, {"action": "skip"})
    e = exams.mine(user)["exams"][0]
    assert e["finished"] and e["score"] == 0 and e["passed"] is False and e["code"] is None


def test_admin_has_no_exam_cooldown(env):  # noqa: F811
    from stockwatch import admin

    user = env["user"]
    admin.grant("anna@example.com")
    sid = exams.start(user, env["settings"])["id"]
    for k in range(exams.ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    assert exams.mine(user)["next_start"] is None
    assert exams.start(user, env["settings"])["id"] != sid           # azonnal ujra indithato
