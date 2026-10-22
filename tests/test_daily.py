"""Napi kihivas es ranglista (#35): ugyanaz az 5 helyzet mindenkinek egy napon (masnap mas), egyszer jatszhato,
a felfedes a beadas utan, idokorlat, ranglista (becenev, e-mail soha, csak megerositett fiok)."""
from __future__ import annotations

from datetime import date

import pytest

from stockwatch import daily, db
from stockwatch.practice import session, store

from .conftest import make_user
from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

DAY = date(2026, 10, 22)


def _points(sid: str, user: int) -> list:
    return [(t["symbol"], t["date"]) for t in store.load_session(sid, user)["tasks"]]


def test_same_tasks_for_everyone_on_a_day_and_once_playable(env):
    anna, bela = env["user"], make_user("bela@example.com")
    a = daily.start(anna, env["settings"], DAY)
    b = daily.start(bela, env["settings"], DAY)
    assert a["rounds"] == b["rounds"] == session.DAILY_ROUNDS and a["daily"] == "2026-10-22"
    assert _points(a["id"], anna) == _points(b["id"], bela)
    assert daily.start(anna, env["settings"], DAY)["id"] == a["id"]                 # egyszer jatszhato
    tomorrow = daily.start(anna, env["settings"], date(2026, 10, 23))
    assert _points(tomorrow["id"], anna) != _points(a["id"], anna)


def test_reveal_only_after_submission(env):
    user = env["user"]
    sid = daily.start(user, env["settings"], DAY)["id"]
    for k in range(session.DAILY_ROUNDS - 1):
        out = session.decide(user, sid, k, {"action": "skip"})
        assert out["hidden"] and "reveal" not in out and "progress" not in out
        assert "reveal" not in session.task_payload(user, sid, k)
    assert session.summary_payload(user, sid) == {"session": session.public(store.load_session(sid, user)), "hidden": True}
    last = session.decide(user, sid, session.DAILY_ROUNDS - 1, {"action": "skip"})
    assert "reveal" in last and "reveal" in session.task_payload(user, sid, 0)
    assert len(session.summary_payload(user, sid)["tasks"]) == session.DAILY_ROUNDS


def test_time_limit(env):
    user = env["user"]
    sid = daily.start(user, env["settings"], DAY)["id"]
    session.decide(user, sid, 0, {"action": "skip"})
    with db.connection() as conn:
        conn.execute("UPDATE practice_sessions SET created_at = now() - interval '21 minutes' WHERE id = %s", (sid,))
    with pytest.raises(ValueError, match="lejart"):
        session.decide(user, sid, 1, {"action": "skip"})
    assert "summary" in session.summary_payload(user, sid)                           # lejart: az eredmeny latszik


def _score(user: int, day: str, points: list[int]) -> None:
    with db.connection() as conn:
        sid = store.new_id()
        conn.execute("INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code, daily) "
                     "VALUES (%s, %s, 1, %s, '{}', 'f', 'c', %s)", (sid, user, len(points), day))
        for k, p in enumerate(points):
            conn.execute("INSERT INTO practice_tasks (session_id, k, task_id, symbol, day, category, source, points) "
                         "VALUES (%s, %s, 't', 'X', '2020-01-02', 'nothing', '', %s)", (sid, k, p))


def test_leaderboard_periods_ranks_and_privacy(neon, monkeypatch):
    anna, bela, cili = make_user("anna@example.com"), make_user("bela@example.com"), make_user("cili@example.com")
    nobody = make_user("titok@example.com", verified=False)
    daily.set_nickname(anna, "Anna")
    _score(anna, "2026-10-22", [10, 7])          # ma 17
    _score(bela, "2026-10-22", [10, 10])         # ma 20
    _score(bela, "2026-09-30", [10])             # elozo honap
    _score(cili, "2026-10-19", [10, 10, 10])     # hetfo: e heti, nem mai
    _score(nobody, "2026-10-22", [10, 10, 10])   # ellenorizetlen: nem latszik
    day = daily.leaderboard("day", anna, DAY)
    assert [(r["rank"], r["points"]) for r in day["rows"]] == [(1, 20), (2, 17)]
    assert day["me"]["name"] == "Anna" and day["me"]["rank"] == 2
    assert day["rows"][0]["name"].startswith("Játékos #")
    week = daily.leaderboard("week", anna, DAY)
    assert [r["points"] for r in week["rows"]] == [30, 20, 17] and week["from"] == "2026-10-19"
    assert [r["points"] for r in daily.leaderboard("all", anna, DAY)["rows"]] == [30, 30, 17]
    assert [r["rank"] for r in daily.leaderboard("all", anna, DAY)["rows"]] == [1, 1, 3]     # holtverseny
    text = str(daily.leaderboard("all", anna, DAY))
    assert "@" not in text and "titok" not in text
    monkeypatch.setattr(daily, "TOP", 1)
    top1 = daily.leaderboard("week", anna, DAY)
    assert len(top1["rows"]) == 1 and top1["me"]["rank"] == 3                          # a sajat hely a top alatt is
    with pytest.raises(ValueError):
        daily.leaderboard("year", anna, DAY)


def test_nickname_rules(neon):
    anna, bela = make_user(), make_user("bela@example.com")
    assert daily.set_nickname(anna, "  Kis   Anna ") == "Kis Anna"
    for bad in ("ab", "x" * 21, "anna@example.com", "<b>"):
        with pytest.raises(ValueError):
            daily.set_nickname(bela, bad)
    with pytest.raises(ValueError, match="foglalt"):
        daily.set_nickname(bela, "kis anna")
