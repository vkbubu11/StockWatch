"""Feladattipusok (#49-#53): a keret (vegyes kor, a regi dontesi feladat valtozatlan), a szivargas (a felfedes elott
nincs papir, datum, nyers ar es jo valasz), a "minden szam a szamitasbol", es tipusonkent az elfogadasi feltetelek."""
from __future__ import annotations

import json
from collections import Counter

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry, i18n, short
from stockwatch.config import EntrySettings
from stockwatch.practice import kinds, sampler, session, synthetic
from stockwatch.practice.kinds import levels as levels_kind
from stockwatch.practice.kinds import pattern as pattern_kind
from stockwatch.practice.kinds import sizing as sizing_kind
from stockwatch.practice.kinds import stop as stop_kind
from stockwatch.practice.kinds.common import norm

from .guards import advice_hits, unexplained_numbers
from .test_practice import env, frames  # noqa: F401  (pytest fixture-ok)

ALL_KINDS = {"decision": 1, "stop": 1, "pattern": 1, "levels": 1, "sizing": 1}


def _round(env, kind: str, seed: int = 3, rounds: int = 4) -> tuple[str, list[dict]]:
    created = session.create(env["user"], env["settings"], seed=seed, rounds=rounds, kinds_mix={kind: 1})
    return created["id"], [session.task_payload(env["user"], created["id"], k) for k in range(rounds)]


def _answer(t: dict) -> dict:
    """Egy ervenyes valasz a feladat promptja szerint."""
    p, atr = t["prompt"], t["chart"]["atr"]
    if t["kind"] == "stop":
        return {"stop": round(100 - 2 * atr if p["side"] == "long" else 100 + 2 * atr, 2)}
    if t["kind"] == "pattern":
        return {"choice": p["options"][0]["key"]}
    if t["kind"] == "levels":
        if p["mode"] == "levels":
            return {"lines": [round(min(b["l"] for b in t["chart"]["bars"]), 2), 100.0]}
        return {"points": [[-60, 95.0], [-10, 98.0]]}
    if t["kind"] == "sizing":
        return {"shares": 10, "risk": 25.0} | ({"rr": 2.0} if p.get("target") else {})
    return {"action": "skip"}


def test_mixed_round_is_deterministic_and_the_decision_task_is_unchanged(env):  # noqa: F811
    a = session.create(env["user"], env["settings"], seed=11, rounds=10, kinds_mix=ALL_KINDS)
    b = session.create(env["user"], env["settings"], seed=11, rounds=10, kinds_mix=ALL_KINDS)
    kinds_a = [session.task_payload(env["user"], a["id"], k)["kind"] for k in range(10)]
    kinds_b = [session.task_payload(env["user"], b["id"], k)["kind"] for k in range(10)]
    assert kinds_a == kinds_b and len(set(kinds_a)) >= 3
    # tipus nelkul minden feladat a regi dontesi feladat, ugyanazzal a helyzetsorral, mint eddig
    plain = session.create(env["user"], env["settings"], seed=11, rounds=3)
    old = sampler.sample(env["pool"], 11, 3)
    stored = session._load(plain["id"], env["user"])["tasks"]
    assert [t["kind"] for t in stored] == ["decision"] * 3
    assert [(t["symbol"], t["date"]) for t in stored] == [(o["symbol"], o["date"]) for o in old]


@pytest.mark.parametrize("kind", list(kinds.KINDS))
def test_no_leak_before_the_answer(env, kind):  # noqa: F811
    sid, payloads = _round(env, kind)
    stored = session._load(sid, env["user"])["tasks"]
    for t, s in zip(payloads, stored):
        text = json.dumps(t)
        assert t["kind"] == kind and "reveal" not in t and "secret" not in text
        assert s["symbol"] not in text and s["date"] not in text
        assert "symbol" not in t and "date" not in t
        assert max(b["h"] for b in t["chart"]["bars"]) < 1000                 # normalizalt ar (a nyers ~2000+)
        if kind == "pattern":
            assert s["params"]["secret"]["answer"] in {o["key"] for o in t["prompt"]["options"]}


@pytest.mark.parametrize("kind", list(kinds.KINDS))
def test_evaluation_numbers_come_from_the_calculation_and_are_translated(env, kind):  # noqa: F811
    sid, payloads = _round(env, kind, rounds=3)
    for k, t in enumerate(payloads):
        out = session.decide(env["user"], sid, k, _answer(t))
        rev = out["reveal"]
        assert 0 <= rev["quality"] <= 1 and rev["points"]["points"] >= 0
        assert rev["review"]["verdict"]["category"] in ("good", "bad")
        for ln in rev["lines"]:
            strings = [a for n, a in enumerate(ln["args"]) if n not in ln["numeric"]]
            nums = {a for n, a in enumerate(ln["args"]) if n in ln["numeric"]}
            assert unexplained_numbers(ln["text"], strings, nums) == [], ln["text"]
            for lang in ("en", "de", "es"):
                text = i18n.translate(ln["text"], lang)
                assert text != ln["text"] and not advice_hits(text), (lang, ln["text"])
    # ugyanarra a valaszra ujra ugyanaz az ertekeles
    again = session.decide(env["user"], _round(env, kind, rounds=3)[0], 0, _answer(payloads[0]))
    first = session.task_payload(env["user"], sid, 0)["reveal"]
    assert again["reveal"]["quality"] == first["quality"] and again["reveal"]["lines"] == first["lines"]


def test_summary_counts_decisions_and_kinds_separately(env):  # noqa: F811
    sid = session.create(env["user"], env["settings"], seed=11, rounds=6, kinds_mix=ALL_KINDS)["id"]
    for k in range(6):
        session.decide(env["user"], sid, k, _answer(session.task_payload(env["user"], sid, k)))
    s = session.summary_payload(env["user"], sid)
    decisions = sum(1 for t in s["tasks"] if t["kind"] == "decision")
    assert s["summary"]["decided"] == decisions
    assert sum(v["n"] for v in s["kinds"].values()) == 6 - decisions


# --- „Tedd le a stopot” (#50) ---------------------------------------------------
def test_stop_structure_uses_only_data_until_the_decision_day(env):  # noqa: F811
    df = env["frames"]["ALFA"].loc[:sampler.VISIBLE_END]
    i = 400
    scale = 100 / float(df["close"].iloc[i])
    atr = 2.0
    before = stop_kind.structural_level(df, i, "long", scale, atr)
    future = df.copy()
    future.iloc[i + 1:, :4] = future.iloc[i + 1:, :4] * 0.5               # a jovo teljesen mas
    assert stop_kind.structural_level(future, i, "long", scale, atr) == before


def test_stop_quality_rewards_the_stop_behind_structure_even_if_it_loses(env):  # noqa: F811
    sid, payloads = _round(env, "stop", seed=5, rounds=6)
    for k, t in enumerate(payloads):
        task = session._load(sid, env["user"])["tasks"][k]
        df, i = session._frame(task, env["settings"])
        scale, atr = norm(df, i)
        side = t["prompt"]["side"]
        level = stop_kind.structural_level(df, i, side, scale, atr)
        if not level:
            continue
        sign = 1 if side == "long" else -1
        behind = round(level["price"] - sign * 0.5 * atr, 2)
        tight = round(100 - sign * 0.3 * atr, 2)
        good = stop_kind.evaluate(task, df, i, {"stop": behind}, env["settings"])
        bad = stop_kind.evaluate(task, df, i, {"stop": tight}, env["settings"])
        assert good["quality"] > bad["quality"]
        return
    pytest.skip("nem volt szerkezeti szint a mintaban")


# --- „Melyik alakzat ez?” (#51) -------------------------------------------------
def test_pattern_answer_is_the_embedded_pattern_and_its_position_is_uniform(env):  # noqa: F811
    positions = Counter()
    for seed in range(200):
        made = pattern_kind.make({}, np.random.default_rng(seed), env["settings"])
        secret, options = made["params"]["secret"], [o["key"] for o in made["params"]["prompt"]["options"]]
        df, embedded = synthetic.generate(made["symbol"])
        assert any(e[0] == secret["answer"] and e[3] == secret["pivot"] for e in embedded)
        assert len(set(options)) == 4 and all(synthetic.DIRECTION[o] == synthetic.DIRECTION[secret["answer"]] for o in options)
        positions[options.index(secret["answer"])] += 1
    assert set(positions) == {0, 1, 2, 3} and min(positions.values()) >= 30         # ~50 / hely
    again = pattern_kind.make({}, np.random.default_rng(7), env["settings"])
    assert again == pattern_kind.make({}, np.random.default_rng(7), env["settings"])


# --- szint / trendvonal (#52) ----------------------------------------------------
def test_reference_levels_use_only_past_data_and_tolerance_is_a_setting(env):  # noqa: F811
    df = env["frames"]["BRVO"].loc[:sampler.VISIBLE_END]
    i = 600
    scale = 100 / float(df["close"].iloc[i])
    ref = levels_kind.reference_levels(df, i, env["settings"], scale, 0, 1000)
    future = df.copy()
    future.iloc[i + 1:, :4] = future.iloc[i + 1:, :4] * 1.7
    assert levels_kind.reference_levels(future, i, env["settings"], scale, 0, 1000) == ref
    if not ref:
        pytest.skip("nincs referencia-szint")
    task = {"params": {"prompt": {"mode": "levels", "side": "lows", "max": 3}}}
    near = {"lines": [round(ref[0]["v"] + 0.3, 2)]}
    loose = env["settings"].model_copy(deep=True)
    loose.practice.levels_tolerance_atr = 50.0
    strict = env["settings"].model_copy(deep=True)
    strict.practice.levels_tolerance_atr = 0.0
    assert levels_kind.evaluate(task, df, i, near, loose)["quality"] >= levels_kind.evaluate(task, df, i, near, strict)["quality"]


def test_levels_answers_are_validated(env):  # noqa: F811
    prompt = {"mode": "levels", "max": 3}
    with pytest.raises(ValueError):
        levels_kind.validate({"lines": []}, prompt)
    with pytest.raises(ValueError):
        levels_kind.validate({"lines": [1, 2, 3, 4]}, prompt)
    trend = {"mode": "trend", "side": "lows"}
    with pytest.raises(ValueError):
        levels_kind.validate({"points": [[-5, 90], [-5, 95]]}, trend)
    assert levels_kind.validate({"points": [[-5, 95], [-50, 90]]}, trend) == {"points": [[-50, 90.0], [-5, 95.0]]}


# --- pozicioméret (#53) ----------------------------------------------------------
def test_sizing_answer_matches_the_existing_position_size_function(env):  # noqa: F811
    zeros = 0
    for seed in range(60):
        pick = sampler.sample(env["pool"], seed, 1)[0]
        made = sizing_kind.make(pick, np.random.default_rng(seed), env["settings"])
        p = made["params"]["prompt"]
        cfg = EntrySettings(account_size=p["account"], risk_per_trade_pct=p["risk_pct"], max_position_pct=100.0)
        fn = entry.position_size if p["side"] == "long" else short.position_size
        e = sizing_kind.expected(p)
        assert e["shares"] == fn(p["entry"], p["stop"], cfg)
        # a tokehatar nelkul is ugyanannyi: tisztan a kockazatbol
        assert e["shares"] == int(p["account"] * p["risk_pct"] / 100 // abs(p["entry"] - p["stop"]))
        zeros += e["shares"] == 0
        ok = {"shares": e["shares"], "risk": e["risk"]} | ({"rr": e["rr"]} if p.get("target") else {})
        assert sizing_kind.evaluate(made, None, 0, sizing_kind.validate(ok, p), env["settings"])["quality"] == 1.0
    assert zeros >= 1                                                    # a „tul tag stop” valtozat is elofordul


def test_sizing_validation_rejects_bad_numbers():
    p = {"target": None}
    for bad in ({"shares": -1, "risk": 1}, {"shares": 1.5, "risk": 1}, {"shares": "x", "risk": 1}, {"shares": 1, "risk": "nan"}):
        with pytest.raises(ValueError):
            sizing_kind.validate(bad, p)
    assert sizing_kind.validate({"shares": "12", "risk": "30,5"}, p) == {"shares": 12, "risk": 30.5}
