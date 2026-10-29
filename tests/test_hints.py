"""Itelet-szempontok a dontes elott (#59): a lecke beallitasa szerint latszik / osszecsukva / rejtve, a napi
kihivasban soha; a dontes elotti payload csak a modot tartalmazza (semmi a helyzetrol); a felfedes ugyanazokat
a szempontokat ertekeli, ugyanabban a sorrendben."""
from __future__ import annotations

import copy

import pytest

from stockwatch import daily, learning
from stockwatch.practice import session

from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

KEYS = ["trend", "signal", "stop", "rr"]


def test_mode_follows_the_lesson_and_is_never_shown_in_the_daily_challenge():
    assert learning.hints({"daily": "2026-10-28", "lesson": 1}) == "hidden"
    assert [learning.hints({"lesson": n}) for n in (1, 3, 4, 6, 7, 10)] == ["show", "show", "toggle", "toggle", "hidden", "hidden"]
    assert learning.hints({}) == "toggle"                                # szabad gyakorlas: osszecsukva


def test_payload_carries_only_the_mode_and_reveal_rates_the_same_criteria(env):  # noqa: F811
    with learning.db.connection() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 1, now())",
                        [(env["user"], n) for n in range(1, 3)])
    sid = learning.start(env["user"], 2, env["settings"])["id"]
    t = next(session.task_payload(env["user"], sid, k) for k in range(6)
             if session.task_payload(env["user"], sid, k)["kind"] == "decision")
    assert t["hints"] == "show"
    plain = session.task_payload(env["user"], sid, t["k"])
    assert set(plain) == {"session", "k", "rounds", "task_id", "chart", "max_days", "decided", "kind", "prompt", "hints", "confidence"}
    skip = session.decide(env["user"], sid, t["k"], {"action": "skip"})["reveal"]["review"]["criteria"]
    assert [c["key"] for c in skip] == KEYS
    assert skip[0]["ok"] is None and skip[2]["ok"] is None and skip[3]["ok"] is None   # kihagyasnal nincs irany / stop
    assert skip[1]["ok"] in (True, False)
    daily_sid = daily.start(env["user"], env["settings"])["id"]
    assert session.task_payload(env["user"], daily_sid, 0)["hints"] == "hidden"


def test_long_decision_rates_stop_and_target(env):  # noqa: F811
    sid = session.create(env["user"], env["settings"], seed=4, rounds=1)["id"]
    atr = session.task_payload(env["user"], sid, 0)["chart"]["atr"]
    crit = session.decide(env["user"], sid, 0, {"action": "long", "stop": round(100 - 0.5 * atr, 2),
                                                 "target": round(100 + 2 * atr, 2)})["reveal"]["review"]["criteria"]
    by = {c["key"]: c["ok"] for c in crit}
    assert by["stop"] is False and by["rr"] is True                      # 0,5 ATR: szoros; 2 ATR cel / 0,5 ATR stop = 4


def test_invalid_hints_setting_is_rejected():
    data = copy.deepcopy(list(learning.lessons()))
    data[0]["tasks"]["hints"] = "mindig"
    with pytest.raises(ValueError, match="hints"):
        learning.validate(data)
