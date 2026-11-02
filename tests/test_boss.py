"""Fejezetzaro feladatsor (#79 b): mas piaci rezsimbol (kulon szintetikus papirok), a helyzetek nem fedik a szintek
feladatait (seed-teszt); a kovetkezo fejezet csak a fejezetzaro (>= 60% jo / balszerencses) utan nyilik, de aki mar
tovabb jutott, annak nem zarodik vissza; a regi szintetikus papirok sora valtozatlan."""
from __future__ import annotations

import pytest

from stockwatch import db, learning
from stockwatch.practice import boss, sampler, store, synthetic

from .test_learning import SETTINGS, _finished, pool  # noqa: F401  (pytest fixture)


def test_boss_situations_never_overlap_the_level_tasks(pool):  # noqa: F811
    pool_symbols = {p["symbol"] for cat in pool["points"].values() for p in cat} if isinstance(pool.get("points"), dict) \
        else {p["symbol"] for p in pool.get("points", [])}
    for chapter in boss.REGIMES:
        for seed in range(25):
            picks = boss.picks(chapter, seed)
            assert picks == boss.picks(chapter, seed)                                   # seedbol determinisztikus
            symbols = {p["symbol"] for p in picks}
            assert len(symbols) == boss.ROUNDS and all(s.startswith(f"SYN{boss.REGIMES[chapter]}") for s in symbols)
            assert not symbols & pool_symbols
            assert not any(s in synthetic.symbols(SETTINGS.practice.synthetic_symbols) for s in symbols)
            for p in picks:
                df = synthetic.frame(p["symbol"]).loc[:sampler.VISIBLE_END]
                assert sampler.eligible(df)[df.index.get_loc(p["date"])]               # ervenyes dontesi pont


def test_regimes_differ_from_the_lessons():
    import numpy as np

    vol = lambda s: float(synthetic.frame(s)["close"].pct_change().std())              # noqa: E731
    base = np.mean([vol(s) for s in synthetic.symbols(10)])
    assert np.mean([vol(f"SYNV{k:04d}") for k in range(1, 11)]) > 2 * base             # nagy volatilitas
    assert all(not synthetic.generate(f"SYNC{k:04d}")[1] for k in range(1, 6))         # erme-dobas: nincs alakzat
    trend = [float(np.log(synthetic.frame(f"SYND{k:04d}")["close"].iloc[-1] / synthetic.frame(f"SYND{k:04d}")["close"].iloc[0]))
             for k in range(1, 11)]
    assert np.mean(trend) < -0.5                                                         # tartos eso trend


def test_next_chapter_opens_only_after_the_boss(user, pool):  # noqa: F811
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 1, now())",
                        [(user, level) for level in (1, 2)])
    assert not learning.overview(user)[2]["boss"]["unlocked"]
    with pytest.raises(ValueError):
        learning.start_boss(user, 1, SETTINGS)
    done = learning.record(user, _finished(3, ["good"] * 6))
    assert done["passed"] and done["boss"] == 1 and done["next"] is None                # elobb a fejezetzaro
    ov = learning.overview(user)
    assert ov[2]["boss"]["unlocked"] and not ov[3]["unlocked"]
    created = learning.start_boss(user, 1, SETTINGS)
    sess = store.load_session(created["id"], user)
    assert sess["boss"] == 1 and created["code"] is None
    assert all(t["symbol"].startswith("SYNC") for t in sess["tasks"]) and len(sess["tasks"]) == boss.ROUNDS
    fail = learning.record_boss(user, {"boss": 1, "tasks": [{"decision": {"action": "skip"}, "result": {"review": {"verdict": {"category": v}}}}
                                                             for v in ["bad"] * 4 + ["good"] * 2]})
    assert not fail["passed"] and not learning.overview(user)[3]["unlocked"]
    ok = learning.record_boss(user, {"boss": 1, "tasks": [{"decision": {"action": "skip"}, "result": {"review": {"verdict": {"category": v}}}}
                                                           for v in ["good", "unlucky", "good", "bad", "good", "lucky"]]})
    assert ok["passed"] and ok["next"] == 4                                              # 4/6 >= 60%
    assert learning.overview(user)[3]["unlocked"] and learning.overview(user)[2]["boss"]["completed"]
    assert learning.record(user, _finished(3, ["good"] * 6))["next"] == 4              # mar teljesitett fejezetzaro


def test_earlier_progress_is_not_locked_again(user, pool):  # noqa: F811
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, %s, 1, now())",
                        [(user, level) for level in range(1, 6)])
    ov = learning.overview(user)
    assert all(ov[n]["unlocked"] for n in range(5))                                      # a 4-5. szint mar megvolt
    assert not ov[6]["unlocked"]                                                         # a 7. a 2. fejezetzaro utan
