"""Heti ligak (#56): determinisztikus feljutas / kieses egyertelmu holtverseny-szaballyal, a het a budapesti vasarnap
ejfelkor zarul, a kilepett felhasznalo nem kerul csoportba, es ugyanannak a helyzetnek az ujrajatszasa nem szamit."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from stockwatch import db, leagues
from stockwatch.practice import session

from .conftest import make_user
from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

WEEK = date(2026, 10, 26)                          # hetfo; a het vege 2026-11-01 (vasarnap) 24:00 Budapest = 23:00 UTC
T0 = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)


def test_standings_are_deterministic_with_tie_break():
    rows = [(5, 30, T0), (3, 30, T0 + timedelta(hours=1)), (4, 30, T0), (9, 0, None), (1, 50, T0)]
    out = leagues.standings(rows, tier=2)
    assert [r[0] for r in out] == [1, 4, 5, 3, 9]   # pont; azonos pontnal aki elobb erte el; vegul az azonosito
    assert out == leagues.standings(list(reversed(rows)), tier=2)
    assert [r[2] for r in out] == [1, 2, 3, 4, 5]
    assert leagues.quotas(30, 2) == (7, 5) and leagues.quotas(30, 0) == (7, 0) and leagues.quotas(30, 4) == (0, 5)
    assert leagues.quotas(1, 1) == (1, 0) and leagues.quotas(6, 1) == (2, 1)
    big = leagues.standings([(i, 100 - i, T0) for i in range(30)], tier=2)
    assert [r[3] for r in big].count("up") == 7 and [r[3] for r in big[-5:]] == ["down"] * 5
    assert leagues.standings([(1, 0, None)], tier=1)[0][3] == "stay"     # pont nelkul nincs feljutas


def _decision(user: int, sid: str, k: int, when: datetime, points: int, symbol: str = "AAA", daily: date | None = None):
    with db.connection() as conn:
        conn.execute("INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code, daily, summary) "
                     "VALUES (%s, %s, 1, 1, '{}', 'f', 'c', %s, '{}') ON CONFLICT DO NOTHING", (sid, user, daily))
        conn.execute("INSERT INTO practice_tasks (session_id, k, task_id, symbol, day, category, source, decision, verdict, "
                     "points, decided_at) VALUES (%s, %s, 't', %s, '2020-01-02', 'nothing', '', '{}', 'good', %s, %s)",
                     (sid, k, symbol, points, when))


def _enter(user: int, tier: int, week: date = WEEK):
    with db.connection() as conn:
        conn.execute("INSERT INTO league_members (user_id, tier) VALUES (%s, %s)", (user, tier))
        conn.execute("INSERT INTO league_entries (week, user_id, tier, grp) VALUES (%s, %s, %s, 0)", (week, user, tier))


def test_week_closes_at_budapest_sunday_midnight_and_settles(neon):
    a, b, c = (make_user(f"{n}@example.com") for n in "abc")
    for u in (a, b, c):
        _enter(u, 1)
    _decision(a, "aaaaaaaaaaaa", 0, datetime(2026, 11, 1, 22, 59, 59, tzinfo=timezone.utc), 10)   # vasarnap 23:59:59
    _decision(b, "bbbbbbbbbbbb", 0, datetime(2026, 11, 1, 23, 0, 0, tzinfo=timezone.utc), 10)     # hetfo 00:00 - mar a kov. het
    _decision(c, "cccccccccccc", 0, T0, 4)
    _decision(c, "cccccccccccd", 0, T0 + timedelta(hours=1), 9)                                    # ugyanaz a helyzet ujra
    sunday = leagues.view(a, date(2026, 11, 1))
    assert [(r["xp"], r["me"]) for r in sunday["group"]] == [(10, True), (4, False), (0, False)]
    assert sunday["last"] is None
    out = leagues.view(a, date(2026, 11, 2))                                                       # az uj het lezarja a regit
    assert out["tier"] == 2 and out["group"] is None and out["last"]["outcome"] == "up" and out["last"]["new"]
    with db.connection() as conn:
        rows = conn.execute("SELECT e.user_id, e.xp, e.rank, e.outcome, m.tier FROM league_entries e JOIN league_members m "
                            "USING (user_id) ORDER BY e.rank").fetchall()
    assert rows == [(a, 10, 1, "up", 2), (c, 4, 2, "stay", 1), (b, 0, 3, "stay", 1)]
    leagues.seen(a)
    assert leagues.view(a, date(2026, 11, 2))["last"]["new"] is False
    assert leagues.view(a, date(2026, 11, 9))["last"]["week"] == WEEK                              # egyszer zarul


def test_hidden_daily_points_are_not_shown_until_submitted(neon):
    a = make_user()
    _enter(a, 0, leagues.week_of(date.today()))
    with db.connection() as conn:
        conn.execute("INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code, daily) "
                     "VALUES ('dddddddddddd', %s, 1, 5, '{}', 'f', 'c', current_date)", (a,))
        conn.execute("INSERT INTO practice_tasks (session_id, k, task_id, symbol, day, category, source, decision, verdict, "
                     "points, decided_at) VALUES ('dddddddddddd', 0, 't', 'X', '2020-01-02', 'nothing', '', '{}', 'good', 10, now())")
    view = leagues.view(a, date.today())
    assert view["group"][0]["xp"] == 0


def test_decision_joins_the_weekly_group_unless_opted_out(env):  # noqa: F811
    user = env["user"]
    leagues.set_opt_in(user, False)
    sid = session.create(user, env["settings"], seed=5, rounds=2)["id"]
    session.decide(user, sid, 0, {"action": "skip"})
    assert leagues.view(user)["group"] is None and leagues.view(user)["opted_out"]
    leagues.set_opt_in(user, True)
    session.decide(user, sid, 1, {"action": "skip"})
    group = leagues.view(user)["group"]
    assert len(group) == 1 and group[0]["me"]
    leagues.set_opt_in(user, False)                                                              # kilepes: azonnal kikerul
    assert leagues.view(user)["group"] is None
