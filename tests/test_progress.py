"""Pontozas, XP, sorozat, jelvenyek (#34): determinisztikus pont, a szerencses kevesebbet er a balszerencsesnel,
a sorozat a budapesti naptari nap szerint (fagyasztoval), a jelvenyek, es a dontes utan atlathato pont."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from stockwatch import progress
from stockwatch.config import Settings
from stockwatch.practice import session

from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

S = Settings()


def _v(category, *flags):
    return {"category": category, "flags": [{"key": f} for f in flags]}


def test_points_follow_decision_quality_not_luck():
    assert progress.score(_v("good"), S) == progress.score(_v("good"), S)              # determinisztikus
    pts = {c: progress.score(_v(c), S)["points"] for c in ("good", "unlucky", "lucky", "bad")}
    assert pts["good"] > pts["unlucky"] > pts["lucky"] > pts["bad"] == 0
    tight = progress.score(_v("good", "tight", "rr", "unfilled"), S)
    assert tight["points"] == 10 - 2 - 1 and [p["key"] for p in tight["parts"]] == ["good", "tight", "rr"]
    assert progress.score(_v("bad", "tight"), S)["points"] == 0                         # nem megy negativba
    with pytest.raises(ValueError):
        Settings(practice={"points": {"good": 10, "unlucky": 2, "lucky": 5, "bad": 0, "tight": -2, "rr": -1}})


def test_streak_with_freeze_day():
    d = date(2026, 10, 19)
    s, f = progress.next_streak(None, 0, True, d)
    assert (s, f) == (1, True)
    assert progress.next_streak(d, 1, True, d) == (1, True)                              # ugyanaz a nap
    assert progress.next_streak(d, 1, True, date(2026, 10, 20)) == (2, True)
    assert progress.next_streak(d, 3, True, date(2026, 10, 21)) == (4, False)            # egy kihagyott nap: fagyaszto
    assert progress.next_streak(d, 3, False, date(2026, 10, 21)) == (1, False)           # nincs fagyaszto: ujra
    assert progress.next_streak(d, 3, True, date(2026, 10, 22)) == (1, True)             # ket kihagyott nap: ujra
    assert progress.next_streak(d, 6, False, date(2026, 10, 20)) == (7, True)            # egy het: vissza a fagyaszto


def test_day_is_counted_in_budapest_time():
    late_utc = datetime(2026, 10, 19, 22, 30, tzinfo=timezone.utc)                       # Budapesten mar 20-a
    assert progress.today(late_utc) == date(2026, 10, 20)
    assert progress.today(datetime(2026, 7, 1, 21, 59, tzinfo=timezone.utc)) == date(2026, 7, 1)   # nyari ido: 23:59


def test_decision_awards_points_xp_and_badges(env):
    created = session.create(env["user"], env["settings"], seed=5, rounds=3)
    atr = session.task_payload(env["user"], created["id"], 0)["chart"]["atr"]
    out = session.decide(env["user"], created["id"], 0, {"action": "short", "entry": "open",
                                                          "stop": round(100 + 2 * atr, 2), "days": 10})
    pts = out["reveal"]["points"]
    assert pts["points"] == sum(p["value"] for p in pts["parts"]) and pts["parts"][0]["key"] == out["reveal"]["review"]["verdict"]["category"]
    assert out["progress"]["xp"] == pts["points"] and out["progress"]["streak"] == 1
    assert [b["key"] for b in out["progress"]["new_badges"]] == ["first_short"]
    second = session.decide(env["user"], created["id"], 1, {"action": "skip"})
    assert second["progress"]["xp"] == pts["points"] + second["reveal"]["points"]["points"]
    assert second["progress"]["new_badges"] == []                                        # egy jelveny egyszer jar
    summary = progress.summary(env["user"])
    assert summary["xp"] == second["progress"]["xp"] and summary["streak"] == 1
    assert {b["key"]: bool(b["earned_at"]) for b in summary["badges"]} == {
        "first_short": True, "good_skips_10": False, "wide_stops_20": False}


def _history(user, rows):
    """rows: (action, verdict, stop_atr) a legregebbitol a legujabbig."""
    from psycopg.types.json import Jsonb

    from stockwatch import db

    with db.connection() as conn:
        conn.execute("INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code) "
                     "VALUES ('abcdefghjkmn', %s, 1, %s, '{}', 'f', 'c')", (user, len(rows)))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO practice_tasks (session_id, k, task_id, symbol, day, category, source, decision, "
                            "verdict, stop_atr, decided_at) VALUES ('abcdefghjkmn', %s, 't', 'X', '2020-01-02', 'nothing', '', "
                            "%s, %s, %s, now() - make_interval(secs => %s))",
                            [(k, Jsonb({"action": a}), v, s, len(rows) - k) for k, (a, v, s) in enumerate(rows)])
        return progress._earned(conn, user)


def test_streak_badges_need_consecutive_decisions(user):
    assert "good_skips_10" in _history(user, [("long", "bad", 0.5)] + [("skip", "good", None)] * 10)


def test_wide_stop_badge(user):
    rows = [("long", "good", 0.5)] + [("long", "good", 1.2), ("skip", "good", None)] * 20
    assert _history(user, rows) == ["wide_stops_20"]


def test_badge_breaks_on_a_bad_skip(user):
    assert _history(user, [("skip", "good", None)] * 9 + [("skip", "lucky", None)]) == []
