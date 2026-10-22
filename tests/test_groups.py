"""Privat csoport-ranglista (#47): letrehozas, csatlakozas koddal, kilepes, torles, korlatok, a csoport-ranglistan
csak a tagok, nem tag nem lathatja, a kodtalalgatas korlatos."""
from __future__ import annotations

import json

import pytest

from stockwatch import daily, groups

from .conftest import make_user
from .test_daily import DAY, _score
from .test_webapp import PW, _form, _req, port  # noqa: F401  (pytest fixture)


def test_create_join_leave_delete(neon):
    anna, bela = make_user(), make_user("bela@example.com")
    g = groups.create(anna, "  Pénzügy   BSc 2026 ")
    assert g["name"] == "Pénzügy BSc 2026" and g["owner"] and g["members"] == 1 and len(g["code"]) == groups.CODE_LEN
    joined = groups.join(bela, g["code"].upper())
    assert joined["id"] == g["id"] and not joined["owner"] and joined["members"] == 2
    assert groups.join(bela, g["code"])["members"] == 2                                     # masodszor: nincs duplikalt tag
    with pytest.raises(LookupError):
        groups.join(bela, "nincsilyen")
    with pytest.raises(ValueError, match="létrehozó"):
        groups.leave(anna, g["id"])
    groups.leave(bela, g["id"])
    assert groups.mine(bela) == []
    with pytest.raises(LookupError):
        groups.delete(bela, g["id"])                                                       # csak a letrehozo torolhet
    groups.delete(anna, g["id"])
    assert groups.mine(anna) == []
    for bad in ("ab", "x" * 41, "<script>"):
        with pytest.raises(ValueError):
            groups.create(anna, bad)


def test_limits(neon, monkeypatch):
    anna, bela, cili = make_user(), make_user("bela@example.com"), make_user("cili@example.com")
    monkeypatch.setattr(groups, "MAX_OWNED", 1)
    g = groups.create(anna, "Egyetlen")
    with pytest.raises(ValueError, match="saját"):
        groups.create(anna, "Masodik")
    monkeypatch.setattr(groups, "MAX_MEMBERS", 2)
    groups.join(bela, g["code"])
    with pytest.raises(ValueError, match="megtelt"):
        groups.join(cili, g["code"])
    for _ in range(groups.JOIN_LIMIT.limit - 1):                                           # cili 1 kiserlete mar elment
        with pytest.raises(LookupError):
            groups.join(cili, "rosszkodx")
    with pytest.raises(ValueError, match="Túl sok"):
        groups.join(cili, g["code"])                                                       # helyes koddal sem tovabb


def test_group_leaderboard_only_members(neon):
    anna, bela, cili = make_user(), make_user("bela@example.com"), make_user("cili@example.com")
    _score(anna, "2026-10-22", [10])
    _score(bela, "2026-10-22", [7])
    _score(cili, "2026-10-22", [10, 10])
    g = groups.create(anna, "Kurzus")
    groups.join(bela, g["code"])
    board = daily.leaderboard("day", anna, DAY, group=g["id"])
    assert [r["points"] for r in board["rows"]] == [10, 7] and board["me"]["rank"] == 1        # cili nincs benne
    assert [r["points"] for r in daily.leaderboard("day", anna, DAY)["rows"]] == [20, 10, 7]
    with pytest.raises(LookupError):
        daily.leaderboard("day", cili, DAY, group=g["id"])                                   # nem tag nem lathatja


def test_groups_over_http(neon, port):  # noqa: F811
    anna = make_user()
    make_user("cili@example.com")
    g = groups.create(anna, "Kurzus")
    login = lambda email: {"Cookie": _form(port, "/login", {"email": email, "password": PW}).getheader("Set-Cookie").split(";")[0],  # noqa: E731
                           "X-StockWatch": "1"}
    a, c = login("anna@example.com"), login("cili@example.com")
    assert _req(port, "GET", f"/api/leaderboard?period=week&group={g['id']}", headers=c).status == 404
    assert _req(port, "GET", f"/api/leaderboard?period=week&group={g['id']}", headers=a).status == 200
    assert _req(port, "GET", "/api/leaderboard?period=week&group=abc", headers=a).status == 400
    joined = _req(port, "POST", "/api/groups/join", json.dumps({"code": g["code"]}), c)
    assert joined.status == 200 and json.loads(joined.text)["name"] == "Kurzus"
    assert [x["name"] for x in json.loads(_req(port, "GET", "/api/groups", headers=c).text)] == ["Kurzus"]
    assert _req(port, "POST", "/api/groups/delete", json.dumps({"id": g["id"]}), c).status == 404   # nem a letrehozo
