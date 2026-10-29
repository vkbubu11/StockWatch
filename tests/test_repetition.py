"""Hibak ismetlese (#36): rossz / szerencses dontes vagy szoros stop utan a tipus masnap esedekes, es az ismetlo
kor ugyanabbol a tipusbol ad (masik) helyzetet; a jo dontes duplazza az idokozt, 16 nap folott kikerul; a be nem
adott napi kihivas nem szamit."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from stockwatch import daily, db, progress, repetition
from stockwatch.practice import session, store

from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

DAY = date(2026, 10, 29)


def _row(user: int, cat: str = "strategy", src: str = "pullback"):
    with db.connection() as conn:
        return conn.execute("SELECT interval_days, due, misses FROM review_queue WHERE user_id = %s AND category = %s "
                            "AND source = %s", (user, cat, src)).fetchone()


def test_interval_grows_with_good_decisions_and_resets_on_a_miss(neon):
    from .conftest import make_user

    user = make_user()
    with db.connection() as conn:
        repetition.update(conn, user, "strategy", "pullback", "bad", [], DAY)
        repetition.update(conn, user, "strategy", "breakout", "good", [], DAY)        # jo dontes: nem kerul be
    assert _row(user) == (1, DAY + timedelta(days=1), 1) and _row(user, src="breakout") is None
    def upd(verdict, flags=()):                                                   # kulon tranzakcio (a _row latja)
        with db.connection() as conn:
            repetition.update(conn, user, "strategy", "pullback", verdict, list(flags), DAY)

    for n in (2, 4, 8, 16):
        upd("unlucky")
        assert _row(user)[:2] == (n, DAY + timedelta(days=n))
    upd("good", [{"key": "tight"}])                                               # szoros stop: ujra 1
    assert _row(user) == (1, DAY + timedelta(days=1), 2)
    for _ in range(5):
        upd("good")
    assert _row(user) is None                                                     # 16 nap folott kikerult


def test_tight_stop_queues_the_type_and_review_round_gives_similar_tasks(env, monkeypatch):  # noqa: F811
    user = env["user"]
    sid = session.create(user, env["settings"], seed=5, rounds=1)["id"]
    t = session.task_payload(user, sid, 0)
    atr = t["chart"]["atr"]
    out = session.decide(user, sid, 0, {"action": "long", "stop": round(100 - 0.2 * atr, 2)})
    task = store.load_session(sid, user)["tasks"][0]
    flags = out["reveal"]["review"]["verdict"]["flags"]
    queued = [(q["category"], q["source"]) for q in repetition.queue(user)]
    if out["reveal"]["review"]["verdict"]["category"] in repetition.MISS or any(f["key"] == "tight" for f in flags):
        assert queued == [(task["category"], task["source"])]
    else:
        pytest.skip("a helyzet nem adott hibas iteletet / szoros stopot")
    with pytest.raises(ValueError):
        repetition.start(user, env["settings"])                                   # meg nem esedekes (holnap)
    monkeypatch.setattr(progress, "today", lambda now=None: date.today() + timedelta(days=1))
    review = repetition.start(user, env["settings"])
    tasks = store.load_session(review["id"], user)["tasks"]
    assert tasks and all((x["category"], x["source"]) == (task["category"], task["source"]) for x in tasks)
    assert {(x["symbol"], x["date"]) for x in tasks} != {(task["symbol"], task["date"])}   # nem ugyanaz a helyzet


def test_hidden_daily_does_not_touch_the_queue(env):  # noqa: F811
    user = env["user"]
    sid = daily.start(user, env["settings"])["id"]
    atr = session.task_payload(user, sid, 0)["chart"]["atr"]
    session.decide(user, sid, 0, {"action": "long", "stop": round(100 - 0.2 * atr, 2)})
    assert repetition.queue(user) == [] and repetition.weaknesses(user) == []
