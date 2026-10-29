"""Parbaj (#55): mindket fel ugyanazokat a helyzeteket kapja ugyanabban a sorrendben; a masik fel eredmenye es
dontesei a sajat kor befejezese elott nem kerhetok le; a meghivo egyszer hasznalhato, visszavonhato es lejar;
a hatarido utan aki nem fejezte be, veszit; visszavago; jelvenyek."""
from __future__ import annotations

import pytest

from stockwatch import db, duels, progress
from stockwatch.practice import session, store

from .conftest import make_user
from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)
from .test_webapp import PW, _form, _req, port  # noqa: F401


def _tasks(sid: str, user: int) -> list:
    return [(t["symbol"], t["date"], t["kind"]) for t in store.load_session(sid, user)["tasks"]]


def _play(user: int, sid: str) -> None:
    for k in range(len(store.load_session(sid, user)["tasks"])):
        session.decide(user, sid, k, {"action": "skip"})


def test_same_situations_hidden_code_and_no_leak_before_finishing(env):  # noqa: F811
    anna, bela, cili = env["user"], make_user("bela@example.com"), make_user("cili@example.com")
    d = duels.create(anna, env["settings"], 5)
    assert d["session"]["code"] is None and d["session"]["duel"] == d["id"]       # a seed nem probalhato ki elore
    assert duels.view(cili, d["id"]) == duels.view(cili, d["id"]) and duels.view(cili, d["id"])["invite"]
    b = duels.accept(bela, d["id"], env["settings"])
    assert _tasks(d["session"]["id"], anna) == _tasks(b["session"]["id"], bela)
    assert duels.accept(bela, d["id"])["session"]["id"] == b["session"]["id"]    # ujra: ugyanaz a kor
    with pytest.raises(LookupError):
        duels.accept(cili, d["id"])                                               # egyszer hasznalhato
    with pytest.raises(LookupError):
        duels.view(cili, d["id"])

    _play(anna, d["session"]["id"])
    seen = duels.view(bela, d["id"])
    assert seen["other"] == {"accepted": True, "finished": True} and "compare" not in seen and seen["result"] is None
    with pytest.raises(LookupError):
        session.summary_payload(bela, d["session"]["id"])                         # a masik kore kozvetlenul sem
    assert not session.delete(anna, d["session"]["id"])                           # torles + ujrajatszas nem
    _play(bela, b["session"]["id"])
    done = duels.view(bela, d["id"])
    assert done["result"] == "draw" and len(done["compare"]) == 5                  # mindketten kihagytak: ugyanannyi pont
    assert done["compare"][0]["other"]["action"] == "skip"
    assert {b["key"] for b in progress.summary(anna)["badges"] if b["earned_at"]} >= {"first_duel"}


def test_revoke_expire_settle_and_rematch(env):  # noqa: F811
    anna, bela, cili = env["user"], make_user("bela@example.com"), make_user("cili@example.com")
    d = duels.create(anna, env["settings"], 5)
    with pytest.raises(LookupError):
        duels.revoke(bela, d["id"])                                               # csak a kihivo
    duels.revoke(anna, d["id"])
    with pytest.raises(LookupError):
        duels.accept(bela, d["id"])

    d = duels.create(anna, env["settings"], 10, {"decision": 1})
    with db.connection() as conn:
        conn.execute("UPDATE duels SET expires_at = now() - interval '1 minute' WHERE id = %s", (d["id"],))
    with pytest.raises(ValueError, match="lejárt"):
        duels.accept(bela, d["id"])

    d = duels.create(anna, env["settings"], 5)
    duels.accept(bela, d["id"], env["settings"])
    with pytest.raises(ValueError):
        duels.revoke(anna, d["id"])                                               # elfogadas utan nem
    _play(anna, d["session"]["id"])
    with db.connection() as conn:
        # a dontes ideje a szerver orajabol jon (nem a Neon now()): a hatarido pontosan az utolso dontes
        conn.execute("UPDATE duels SET expires_at = (SELECT max(decided_at) FROM practice_tasks WHERE session_id = %s) "
                     "WHERE id = %s", (d["session"]["id"], d["id"]))
    assert duels.view(anna, d["id"])["result"] == "win" and duels.view(bela, d["id"])["result"] == "loss"
    assert [x["result"] for x in duels.mine(bela)][:1] == ["loss"]

    r = duels.rematch(bela, d["id"], env["settings"])
    with pytest.raises(LookupError):
        duels.accept(cili, r["id"])                                               # a visszavago ellenfele kijelolt
    assert duels.view(anna, r["id"])["side"] == "opponent"
    duels.accept(anna, r["id"], env["settings"])
    with pytest.raises(ValueError):
        duels.create(anna, env["settings"], 7)


def test_invite_link_survives_login(neon, port):  # noqa: F811
    make_user()
    res = _req(port, "GET", "/d/abcdefghijkm")
    assert res.status == 303 and res.getheader("Location") == "/login?e=duel&next=/d/abcdefghijkm"
    assert 'name="next" value="/d/abcdefghijkm"' in _req(port, "GET", "/login?e=duel&next=/d/abcdefghijkm").text
    ok = _form(port, "/login", {"email": "anna@example.com", "password": PW, "next": "/d/abcdefghijkm"})
    assert ok.getheader("Location") == "/d/abcdefghijkm"
    evil = _form(port, "/login", {"email": "anna@example.com", "password": PW, "next": "https://evil.example/d/x"})
    assert evil.getheader("Location") == "/"
