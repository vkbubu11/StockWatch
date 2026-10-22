"""Tanulasi ut (#33): a leckek tartalma (fajlbol, ajanlas nelkul, abraval), a celzott feladatsor illik a szint
temajahoz, a zarolas es a feloldas a haladas szerint."""
from __future__ import annotations

import pytest

from stockwatch import learning
from stockwatch.config import Settings
from stockwatch.practice import sampler, store

from .guards import advice_hits

SETTINGS = Settings(practice={"source": "synthetic", "synthetic_symbols": 30})


def test_every_level_has_a_lesson_figure_and_tasks_without_advice():
    levels = learning.lessons()
    assert [x["level"] for x in levels] == list(range(1, 11))
    for item in levels:
        assert item["title"] and item["paragraphs"] and item["points"] and item["caption"]
        assert set(item["tasks"]["only"]) <= set(sampler.CATEGORIES) and 1 <= item["tasks"]["rounds"] <= 10
        text = " ".join([item["title"], item["caption"], *item["paragraphs"], *item["points"]])
        assert not advice_hits(text), (item["level"], advice_hits(text))
        bars = learning.figure(item["level"])["bars"]
        assert len(bars) == sampler.VISIBLE_BARS and bars[-1]["c"] == 100.0


@pytest.fixture
def pool(user):
    return sampler.build(SETTINGS)


def test_targeted_tasks_match_the_level_theme(user, pool):
    with learning.db.connection() as conn, conn.cursor() as cur:    # minden szint nyitva a szuro-teszthez
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 1, now())",
                         [(user, level) for level in range(1, 10)])
    for item in learning.lessons():
        only = item["tasks"]["only"]
        created = learning.start(user, item["level"], SETTINGS)
        tasks = store.load_session(created["id"], user)["tasks"]
        assert len(tasks) == item["tasks"]["rounds"]
        for t in tasks:
            assert t["category"] in only, (item["level"], t["category"])
            assert only[t["category"]] is None or t["source"] in only[t["category"]], (item["level"], t["source"])


def _finished(level: int, verdicts: list[str]) -> dict:
    return {"lesson": level, "tasks": [{"decision": {"action": "skip"}, "result": {"review": {"verdict": {"category": v}}}}
                                       for v in verdicts]}


def test_levels_unlock_in_order(user, pool):
    overview = learning.overview(user)
    assert overview[0]["unlocked"] and not overview[1]["unlocked"]
    with pytest.raises(ValueError):
        learning.start(user, 2, SETTINGS)                            # zarolt
    with pytest.raises(ValueError):
        learning.page(user, 2)
    failed = learning.record(user, _finished(1, ["good", "bad", "lucky", "bad", "unlucky", "bad"]))
    assert failed["share"] == 0.333 and not failed["passed"] and failed["next"] is None
    assert not learning.overview(user)[1]["unlocked"]
    passed = learning.record(user, _finished(1, ["good", "good", "unlucky", "lucky", "good", "bad"]))
    assert passed["passed"] and passed["next"] == 2                  # 4/6 >= 60%
    overview = learning.overview(user)
    assert overview[0]["completed"] and overview[0]["best_share"] == pytest.approx(0.667, abs=1e-3)
    assert overview[1]["unlocked"] and not overview[2]["unlocked"]
    learning.record(user, _finished(1, ["bad"] * 6))                 # egy rosszabb kor nem veszi el a teljesitest
    assert learning.overview(user)[0]["completed"]
    assert learning.record(user, {"lesson": 1, "tasks": [{"decision": None}]}) is None   # befejezetlen kor
