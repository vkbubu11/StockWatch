"""Megoszthato eredmenykartya (#57): csak a beadott napi kihivas / teljesitett szint oszthato meg; a kartyan es az
oldalon nincs papirnev, datum-helyzet es arfolyam, a figyelmeztetes benne van; visszavonas utan a link es a kep
404; mas nem vonhatja vissza; a megosztott oldalrol jott regisztracio forrasa merheto."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from stockwatch import accounts, daily, db, shares, webapp
from stockwatch.practice import session, store

from .conftest import make_user
from .test_practice import _call, env, frames, login_cookie, server  # noqa: F401  (pytest fixture-ok)


def _get(url: str) -> tuple[int, bytes, str]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=20) as res:
            return res.status, res.read(), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), ""


def test_daily_share_card_page_and_revoke(server, env):  # noqa: F811
    user = env["user"]
    sid = daily.start(user, env["settings"])["id"]
    day = store.load_session(sid, user)["daily"]
    status, _ = _call(f"{server}/api/share", {"kind": "daily", "ref": day})
    assert status == 400                                                   # beadas elott nem
    for k in range(session.DAILY_ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    status, body = _call(f"{server}/api/share", {"kind": "daily", "ref": day})
    assert status == 200
    share_id = json.loads(body)["id"]
    share = shares.get(share_id)
    assert share["data"]["total"] == session.DAILY_ROUNDS and len(share["data"]["marks"]) == session.DAILY_ROUNDS
    assert shares.lines(share, "hu")["disclaimer"] == shares.DISCLAIMER
    assert "@" not in json.dumps(share["data"])                             # e-mail-cim soha

    status, page, _ = _get(f"{server}/s/{share_id}")                        # bejelentkezes nelkul
    text = page.decode("utf-8")
    assert status == 200 and f"/s/{share_id}.png" in text and 'property="og:image"' in text
    assert "/register?src=share" in text and "Not investment advice" in text   # suti nelkul angolul
    for t in store.load_session(sid, user)["tasks"]:                         # a helyzetek nem latszanak
        assert t["symbol"] not in text and t["date"] not in text
    status, png, ctype = _get(f"{server}/s/{share_id}.png")
    assert status == 200 and ctype == "image/png" and Image.open(io.BytesIO(png)).size == (1200, 630)

    make_user("bela@example.com")
    assert _call(f"{server}/api/share/revoke", {"id": share_id}, login_cookie("bela@example.com"))[0] == 404
    assert _call(f"{server}/api/share/revoke", {"id": share_id})[0] == 200
    assert _get(f"{server}/s/{share_id}")[0] == 404 and _get(f"{server}/s/{share_id}.png")[0] == 404
    assert shares.mine(user) == [] and accounts.export(user)["shares"][0]["revoked_at"] is not None   # GDPR-export
    again = json.loads(_call(f"{server}/api/share", {"kind": "daily", "ref": day})[1])["id"]
    assert again != share_id and _get(f"{server}/s/{share_id}")[0] == 404  # a regi link halott marad


def test_level_share_only_when_completed_and_group_code_only_for_members(env):  # noqa: F811
    from stockwatch import groups

    user = env["user"]
    with pytest.raises(ValueError):
        shares.create(user, "level", "1", "hu")
    with db.connection() as conn:
        conn.execute("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, 1, 0.8, now())",
                     (user,))
    other = groups.create(make_user("cili@example.com"), "Idegen")
    with pytest.raises(LookupError):
        shares.create(user, "level", "1", "hu", group=other["id"])
    mine = groups.create(user, "Kurzus")
    share = shares.get(shares.create(user, "level", "1", "en", group=mine["id"])["id"])
    assert share["data"]["group"] == {"name": "Kurzus", "code": mine["code"]} and share["data"]["best"] == 0.8
    assert shares.lines(share, "en")["title"] == "Level 1: completed"
    with pytest.raises(ValueError):
        shares.create(user, "portfolio", "1", "hu")


def test_register_source_from_share(neon, outbox):
    _, form = webapp.auth_form("register", source="share")
    assert 'name="src" value="share"' in form
    assert 'name="src"' not in webapp.auth_form("register", source="<x>")[1]   # csak ismert ertek
    accounts.register("uj@example.com", "helyes-lo-elem-tuzfal", True, True, "203.0.113.7", "http://x", source="share")
    with db.connection() as conn:
        assert conn.execute("SELECT props->>'source' FROM events WHERE name = 'register'").fetchone()[0] == "share"
