"""Magabiztossag es kalibracio (#54): tokeletesen kalibralt sor -> atlo; tul magabiztos / tul ovatos osszegzes; keves
dontesnel zaj-jelzes; a tulzott magabiztossag tobbe kerul; a magabiztossag az iteletet es a pontot nem
befolyasolja; a be nem adott napi kihivas nem szamit a gorbebe."""
from __future__ import annotations

import pytest

from stockwatch import calibration, daily
from stockwatch.practice import session

from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)


def _seq(rate_of) -> list[tuple[int, bool]]:
    out = []
    for c in (55, 65, 75, 85, 95):
        hits = round(rate_of(c) * 100)
        out += [(c, i < hits) for i in range(100)]
    return out


def test_perfectly_calibrated_sequence_is_the_diagonal():
    c = calibration.curve(_seq(lambda conf: conf / 100))
    assert [b["stated"] for b in c["buckets"]] == [b["actual"] for b in c["buckets"]] == [0.55, 0.65, 0.75, 0.85, 0.95]
    assert c["summary"] == "ok" and not c["noisy"]
    assert calibration.curve(_seq(lambda conf: conf / 100 - 0.2))["summary"] == "over"
    assert calibration.curve(_seq(lambda conf: min(1.0, conf / 100 + 0.1)))["summary"] == "under"
    few = calibration.curve([(80, True)] * 5)
    assert few["noisy"] and few["summary"] is None and few["n"] == 5


def test_overconfidence_costs_more_than_uncertainty():
    assert calibration.points(50, "bad")["points"] == calibration.points(50, "good")["points"] == 0
    assert calibration.points(95, "good")["points"] == 10 and calibration.points(95, "unlucky")["hit"]
    assert calibration.points(95, "bad")["points"] == -26 < calibration.points(70, "bad")["points"] < 0


def test_confidence_does_not_change_the_verdict_and_hidden_daily_is_excluded(env):  # noqa: F811
    user = env["user"]
    a = session.create(user, env["settings"], seed=11, rounds=1)["id"]
    b = session.create(user, env["settings"], seed=11, rounds=1)["id"]
    with pytest.raises(ValueError, match="magabiztosság"):
        session.decide(user, a, 0, {"action": "skip", "confidence": 40})
    with_conf = session.decide(user, a, 0, {"action": "skip", "confidence": 90})["reveal"]
    plain = session.decide(user, b, 0, {"action": "skip"})["reveal"]
    assert with_conf["review"]["verdict"] == plain["review"]["verdict"] and with_conf["points"] == plain["points"]
    assert with_conf["calibration"]["confidence"] == 90 and "calibration" not in plain
    assert calibration.for_user(user)["n"] == 1

    sid = daily.start(user, env["settings"])["id"]
    session.decide(user, sid, 0, {"action": "skip", "confidence": 80})
    assert calibration.for_user(user)["n"] == 1                           # beadas elott nem latszik
    for k in range(1, session.DAILY_ROUNDS):
        session.decide(user, sid, k, {"action": "skip"})
    assert calibration.for_user(user)["n"] == 2
