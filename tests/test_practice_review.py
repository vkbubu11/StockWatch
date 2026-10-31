"""Gyakorlo mod: short belepes (#24), itelet (#25), levezetes (#26).

  * short: a validacio tukrozott, a szimulacio a long motor tukorkepe (stop, cel, nyitasi res, azonos gyertya,
    idokorlat), a long viselkedes es a regi ("enter") dontesek valtozatlanok
  * itelet: determinisztikus, a negy kategoria, a szabaly szerinti resz csak a dontes napjaig ismert adatbol
  * levezetes: minden szam a szamitasbol, az R ujraszamolva egyezik, nincs ajanlo szohasznalat, minden nyelven
    fordul; a "jobb stop" csak az elore rogzitett racsbol, utolagoskent jelolve
"""
from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pytest

from stockwatch import i18n, meanrev
from stockwatch.config import PracticeSettings, PullbackParams, Settings
from stockwatch.practice import review, sampler, scoring, session, store

from .guards import advice_hits, unexplained_numbers
from .test_practice import TICKERS, _call, env, frames, server  # noqa: F401  (pytest fixture-ok)


def _bars(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range("2020-01-01", periods=len(rows)))
    df["atr"], df["rsi2"], df["natr"] = 1.0, 1.0, 1.0
    return df


def _flip(rows: list[list[float]], k: float = 300.0) -> list[list[float]]:
    """Tukrozes: p' = k - p; a csucs es a melypont helyet cserel."""
    return [[k - o, k - lo, k - h, k - c] for o, h, lo, c in rows]


# ---------------------------------------------------------------------------
# #24 Short: validacio es tukrozott szimulacio
# ---------------------------------------------------------------------------
def test_short_decision_is_validated_mirrored():
    ok = session.Decision(action="short", entry="open", stop=104, target=95, days=5)
    assert ok.action == "short"
    with pytest.raises(ValueError):
        session.Decision(action="short", entry="open", stop=96, days=5)              # stop a belepo alatt
    with pytest.raises(ValueError):
        session.Decision(action="short", entry="open", stop=104, target=105, days=5)  # cel a belepo felett
    with pytest.raises(ValueError):
        session.Decision(action="short", entry="limit", limit=101, stop=100.5, days=5)
    assert session.Decision(action="short", entry="limit", limit=101, stop=104, target=97, days=5).limit == 101
    assert session.Decision(action="enter", entry="open", stop=95, days=5).action == "long"   # regi nev


@pytest.mark.parametrize("rows, levels, target", [
    ([[100, 101, 99, 100], [100, 101, 99.5, 100], [100, 111, 94, 100], [100, 101, 99, 100]], (math.inf, 95.0), 110.0),
    ([[100, 101, 99, 100], [100, 101, 99.5, 100], [112, 113, 111, 112], [100, 101, 99, 100]], (math.inf, 95.0), 110.0),
    ([[100, 101, 99, 100], [100, 101, 99.5, 100], [100, 111, 99, 105], [100, 101, 99, 100]], (math.inf, 95.0), 110.0),
    ([[100, 101, 99, 100], [100, 101, 99.5, 100], [93, 94, 90, 92], [100, 101, 99, 100]], (math.inf, 95.0), 110.0),
    ([[100, 101, 99, 100], [100, 112, 98, 100], [100, 101, 99.5, 100], [100, 101, 99.5, 100]], (99.0, 90.0), 110.0),
    ([[100, 101, 99, 100], [100, 101, 99.5, 100], [100, 101, 99.5, 100], [100, 101, 99.5, 100]], (math.inf, 95.0), None),
])
def test_short_simulation_is_the_mirror_of_long(rows, levels, target):
    """A short a long tukorkepe: tukrozott gyertyakon tukrozott szintekkel ugyanaz az ok, nap es (tukrozott) ar."""
    k = 300.0
    days = 2
    long_trade = scoring.simulate(_bars(rows), 0, levels[0], levels[1], target, days, 0.0)
    limit = levels[0] if math.isinf(levels[0]) else k - levels[0]
    short_trade = scoring.simulate(_bars(_flip(rows, k)), 0, limit, k - levels[1],
                                   None if target is None else k - target, days, 0.0, side="short")
    assert (short_trade.status, short_trade.reason, short_trade.exit_date, short_trade.held) == \
           (long_trade.status, long_trade.reason, long_trade.exit_date, long_trade.held)
    if long_trade.entry_price is not None:
        assert short_trade.entry_price == pytest.approx(k - long_trade.entry_price)
    if long_trade.exit_price is not None:
        assert short_trade.exit_price == pytest.approx(k - long_trade.exit_price)
        long_r = scoring.r_multiple(long_trade.entry_price, long_trade.exit_price, levels[1], 100.0, 0.0)
        short_r = scoring.r_multiple(short_trade.entry_price, short_trade.exit_price, k - levels[1], k - 100.0, 0.0, "short")
        assert short_r == pytest.approx(long_r)


def test_short_same_candle_stop_wins_and_gap_fills_at_open():
    rows = [[100, 101, 99, 100], [100, 101, 99.5, 100], [100, 106, 89, 100], [100, 101, 99, 100]]
    trade = scoring.simulate(_bars(rows), 0, math.inf, 105.0, 90.0, 5, 0.0, side="short")
    assert (trade.reason, trade.exit_price) == ("stop", 105.0)
    gap = [[100, 101, 99, 100], [100, 101, 99.5, 100], [107, 108, 106, 107], [100, 101, 99, 100]]
    trade = scoring.simulate(_bars(gap), 0, math.inf, 105.0, 90.0, 5, 0.0, side="short")
    assert (trade.reason, trade.exit_price) == ("gap_stop", 107.0)


def test_short_borrow_fee_reduces_the_result():
    rows = [[100, 101, 99, 100], [100, 101, 99.5, 100], [99, 99.5, 97, 98], [98, 98.5, 97, 98], [97, 97.5, 96, 97]]
    trade = scoring.simulate(_bars(rows), 0, math.inf, 105.0, None, 3, 5.0, side="short", borrow=10.0)
    free = scoring.simulate(_bars(rows), 0, math.inf, 105.0, None, 3, 5.0, side="short")
    assert trade.status == free.status == "closed"
    with_fee = scoring.derive(trade.entry_price, trade.exit_price, 105.0, 100.0, 5.0, "short", trade.held, 10.0)
    no_fee = scoring.derive(free.entry_price, free.exit_price, 105.0, 100.0, 5.0, "short", free.held, 0.0)
    assert with_fee["borrow"] == pytest.approx(trade.entry_price * 10 / 10_000 * trade.held)
    assert with_fee["r"] == pytest.approx(no_fee["r"] - with_fee["borrow"] / with_fee["risk"])
    assert trade.net_pct == pytest.approx(free.net_pct - 0.1 * trade.held)


def test_long_r_formula_is_unchanged():
    for entry, exit_, stop, cost in ((100, 104, 98, 5), (100, 97, 98, 0), (95, 96, 98, 3)):
        c = cost / 10_000
        risk = entry - stop if entry - stop > 0 else 100 - stop
        assert scoring.r_multiple(entry, exit_, stop, 100, cost) == pytest.approx((exit_ * (1 - c) - entry * (1 + c)) / risk)


def test_long_practice_simulation_unchanged_by_the_side_parameter():
    df = frames()["ALFA"]
    feat = meanrev.features(df, PullbackParams())
    for i in range(300, 900, 37):
        close = float(feat["close"].iloc[i])
        a = scoring.simulate(feat, i, math.inf, close * 0.95, close * 1.08, 10, 5.0).to_dict()
        b = scoring.simulate(feat, i, math.inf, close * 0.95, close * 1.08, 10, 5.0, side="long").to_dict()
        assert a == b


def test_pool_has_short_type_points_before_the_holdout(env):
    pool = env["pool"]
    assert pool["version"] == sampler.POOL_VERSION == 3 and pool["counts"]["short"] > 0
    for symbol, day, source in pool["entries"]["short"]:
        df = env["frames"][symbol].loc[:sampler.VISIBLE_END]
        i = df.index.get_loc(pd.Timestamp(day))
        assert sampler.downtrend(df.iloc[: i + 1])[-1] and source
    assert sampler.normalize_mix(None)["short"] > 0


def test_short_decision_end_to_end_and_hidden_before_reveal(env, server):
    status, text = _call(server + "/api/practice/session", {"rounds": 4, "seed": 3, "mix": {"short": 1}})
    created = json.loads(text)["session"]
    status, task_text = _call(server + f"/api/practice/session/{created['id']}/task/0")
    assert status == 200 and "short" not in task_text and "downtrend" not in task_text
    atr = json.loads(task_text)["chart"]["atr"]
    body = {"action": "short", "entry": "open", "stop": round(100 + 2 * atr, 2), "target": round(100 - 3 * atr, 2), "days": 10}
    status, text = _call(server + f"/api/practice/session/{created['id']}/task/0/decision", body)
    assert status == 200, text
    rev = json.loads(text)["reveal"]
    assert rev["decision"]["action"] == "short" and rev["user"]["side"] == "short" and rev["category"] == "short"
    assert rev["baseline"]["side"] == "short"
    assert {s["strategy"] for s in rev["strategies"]} == {"pullback", "deep", "breakout", "short"}
    assert next(s for s in rev["strategies"] if s["strategy"] == "short")["experimental"]


def test_short_strategy_comparison_uses_the_mirrored_engine(env, monkeypatch):
    """A kiserleti short motor egy (itt kitalalt) megerositett letoresenel a kotes = a tukrozott short szimulacio."""
    from stockwatch import patterns, short

    df = env["frames"]["ALFA"].loc[:sampler.VISIBLE_END]
    i = 700
    close = float(df["close"].iloc[i])

    class Fake:
        kind = "breakdown"
        checks = []
        pattern = type("P", (), {"name": "Medve zaszlo"})()
        plan = short.ShortPlan(entry=close, entry_limit=close * 0.5, stop=close * 1.04, target=close * 0.93,
                               atr=close * 0.02, shares=1, horizon=12)

        @staticmethod
        def qualifies(cfg):
            return True

    monkeypatch.setattr(patterns, "detect", lambda *a, **k: [type("M", (), {"direction": "bearish"})()])
    monkeypatch.setattr(short, "evaluate", lambda *a, **k: [Fake()])
    settings = env["settings"]
    out = scoring.short_compare("ALFA", df, i, 100 / close, settings, None, None, 5.0)
    feat = meanrev.features(df, settings.strategies.pullback.params)
    direct = scoring.simulate(feat, i, math.inf, close * 1.04, close * 0.93, 12, 5.0, side="short")
    assert out["signaled"] and out["experimental"] and out["status"] == direct.status
    if direct.status == "closed":
        assert out["r"] == pytest.approx(round(scoring.r_multiple(direct.entry_price, direct.exit_price, direct.stop,
                                                                  close, 5.0, "short", direct.held), 3))


# ---------------------------------------------------------------------------
# #25 Itelet
# ---------------------------------------------------------------------------
def _result(user_r=None, status="closed", strategies=(), side="long", stop_atr=2.0, rr=None):
    user = None if side is None else {"r": user_r, "status": status, "stop_atr": stop_atr, "rr": rr, "side": side}
    return {"user": user, "strategies": list(strategies)}


def _alts(long_r, short_r):
    return {"long": {"r": long_r}, "short": {"r": short_r}}


PULLBACK = {"strategy": "pullback", "signaled": True}
SHORT_OK = {"strategy": "short", "signaled": True, "status": "closed"}
SHORT_FILTERED = {"strategy": "short", "signaled": True, "status": "filtered"}


@pytest.mark.parametrize("decision, result, alts, category", [
    ({"action": "long"}, _result(1.2, strategies=[PULLBACK]), _alts(1.2, -1.0), "good"),
    ({"action": "long"}, _result(1.2), _alts(1.2, -1.0), "lucky"),
    ({"action": "long"}, _result(-1.0, strategies=[PULLBACK]), _alts(-1.0, 0.8), "unlucky"),
    ({"action": "long"}, _result(-1.0), _alts(-1.0, 0.8), "bad"),
    ({"action": "skip"}, _result(side=None), _alts(0.3, -0.5), "good"),
    ({"action": "skip"}, _result(side=None), _alts(2.5, -1.0), "unlucky"),
    ({"action": "skip"}, _result(side=None, strategies=[PULLBACK]), _alts(0.2, -1.0), "lucky"),
    ({"action": "short"}, _result(0.9, strategies=[SHORT_OK], side="short"), _alts(-1.0, 0.9), "good"),
    ({"action": "short"}, _result(0.9, strategies=[SHORT_FILTERED], side="short"), _alts(-1.0, 0.9), "lucky"),
    ({"action": "enter"}, _result(0.5, strategies=[PULLBACK, SHORT_OK]), _alts(0.5, -0.5), "good"),
])
def test_verdict_categories(decision, result, alts, category):
    ver = review.verdict(decision, result, alts, Settings())
    assert ver["category"] == category
    assert ver["best_r"] == max(0.0, *(v["r"] for v in alts.values()))


def test_verdict_flags():
    cfg = Settings(practice=PracticeSettings(tight_stop_atr=1.0, min_rr=1.5))
    ver = review.verdict({"action": "long"}, _result(0.4, stop_atr=0.6, rr=1.2), _alts(0.4, 0.1), cfg)
    assert [f["key"] for f in ver["flags"]] == ["tight", "rr"]
    ver = review.verdict({"action": "long"}, _result(None, status="unfilled"), _alts(0.4, 0.1), cfg)
    assert not ver["filled"] and ver["user_r"] == 0 and [f["key"] for f in ver["flags"]] == ["unfilled"]


def _decide(env, seed: int, k: int, body_for) -> dict:
    created = session.create(env["user"], env["settings"], seed=seed, rounds=max(k + 1, 3))
    atr = session.task_payload(env["user"], created["id"], k)["chart"]["atr"]
    return session.decide(env["user"], created["id"], k, body_for(atr))["reveal"]


BODIES = {
    "long_open": lambda atr: {"action": "long", "entry": "open", "stop": round(100 - 1.5 * atr, 2),
                              "target": round(100 + 3 * atr, 2), "days": 12},
    "long_limit": lambda atr: {"action": "long", "entry": "limit", "limit": 99.0, "stop": round(99 - 2 * atr, 2), "days": 20},
    "short_open": lambda atr: {"action": "short", "entry": "open", "stop": round(100 + 0.8 * atr, 2),
                               "target": round(100 - 2 * atr, 2), "days": 8},
    "short_limit": lambda atr: {"action": "short", "entry": "limit", "limit": 101.0, "stop": round(101 + 2 * atr, 2), "days": 15},
    "skip": lambda atr: {"action": "skip"},
}


def test_verdict_is_deterministic(env):
    a = _decide(env, 41, 1, BODIES["long_open"])["review"]
    b = _decide(env, 41, 1, BODIES["long_open"])["review"]
    assert a["verdict"] == b["verdict"] and a["steps"] == b["steps"]


def test_rules_and_context_use_only_data_up_to_the_decision(env):
    settings = env["settings"]
    df = env["frames"]["BRVO"].loc[:sampler.VISIBLE_END]
    i = 800
    changed = df.copy()
    changed.iloc[i + 1:, :4] *= 0.5                                     # a jovo ateirva
    for frame in (df, changed):
        frame.attrs = {}
    scale = 100 / float(df["close"].iloc[i])
    assert review.context("BRVO", df, i, scale, None, None) == review.context("BRVO", changed, i, scale, None, None)
    strategies = [{"strategy": "pullback", "signaled": False}, {"strategy": "deep", "signaled": False},
                  {"strategy": "breakout", "signaled": False}, {"strategy": "short", "signaled": False}]
    assert review.rules("BRVO", df, i, scale, settings, None, None, strategies) == \
           review.rules("BRVO", changed, i, scale, settings, None, None, strategies)


# ---------------------------------------------------------------------------
# #26 Levezetes
# ---------------------------------------------------------------------------
NEUTRAL = {"SMA200", "EMA50", "EMA200", "RSI(2)", "RSI(14)", "ATR(14)"}


def _all_lines(rev: dict) -> list[dict]:
    out = [rev["review"]["verdict_line"]]
    for step in rev["review"]["steps"]:
        out += [step["title"], *step["lines"]]
    return out


@pytest.mark.parametrize("name", list(BODIES))
@pytest.mark.parametrize("lang", ["hu", "en", "de", "es"])
def test_derivation_numbers_come_from_the_calculation(env, name, lang):
    rev = _decide(env, 52, 2, BODIES[name])
    for ln in _all_lines(rev):
        strings = [a for i, a in enumerate(ln["args"]) if i not in ln["numeric"]]
        numbers = {a for i, a in enumerate(ln["args"]) if i in ln["numeric"]}
        assert unexplained_numbers(ln["text"], strings, numbers) == [], ln["text"]
        text = i18n.translate(ln["text"], lang)
        assert not advice_hits(text), text
        neutral = ln["key"] == "x_value" and ln["args"][0] in NEUTRAL      # nyelvfuggetlen mutato-nev
        if lang != "hu" and not neutral:
            assert text != ln["text"], f"nincs forditas ({lang}): {ln['text']}"


@pytest.mark.parametrize("name", ["long_open", "long_limit", "short_open", "short_limit"])
def test_derivation_r_matches_the_simulation(env, name):
    rev = _decide(env, 63, 0, BODIES[name])
    user = rev["user"]
    trade = next(s for s in rev["review"]["steps"] if s["id"] == "trade")
    if user["status"] != "closed":
        assert not any(ln["key"] == "x_r" for ln in trade["lines"])
        return
    calc = user["calc"]
    assert calc["net"] / calc["risk"] == pytest.approx(user["r"], abs=2e-3)
    assert calc["net"] == pytest.approx(calc["gross"] - calc["cost"] - calc["borrow"], abs=1e-3)
    r_line = next(ln for ln in trade["lines"] if ln["key"] == "x_r")
    assert float(r_line["args"][2]) == pytest.approx(user["r"], abs=0.006)
    entry_k = user["entry_k"]
    assert entry_k is not None and entry_k >= 1
    assert user["exit_k"] is None or user["exit_k"] >= entry_k


def test_better_stop_only_from_the_fixed_grid():
    """Szoros stop kiall (1. nap minimuma 98,5), utana emelkedes: a racs tagabb stopja jobb, utolagoskent jelolve."""
    rows = [[100, 100.5, 99.5, 100]] + [[100, 100.6, 98.5, 99.2], [99.5, 102, 99.4, 101.8], [102, 104, 101.5, 103.8],
                                        [104, 106, 103.6, 105.5], [105.5, 106.2, 105, 106]] + [[106, 106.5, 105.5, 106]] * 10
    feat = _bars(rows)
    decision = {"action": "long", "entry": "open", "stop": 99.0, "target": None, "days": 4}
    trade = scoring.simulate(feat, 0, math.inf, 99.0, None, 4, 0.0)
    user_r = scoring.r_multiple(trade.entry_price, trade.exit_price, 99.0, 100.0, 0.0)
    assert trade.reason == "stop" and user_r < 0
    better = review.better_stop(feat, 0, decision, 1.0, 1.0, 0.0, None, 0.0, user_r, 0.5)
    assert better is not None and better["stop_atr"] in review.STOP_GRID and better["stop_atr"] >= 1.5
    assert better["hindsight"] and better["grid"] == review.GRID_LABEL and better["r"] - better["user_r"] >= 0.5
    assert review.better_stop(feat, 0, decision, 1.0, 1.0, 0.0, None, 0.0, 10.0, 0.5) is None   # nincs eleg javulas
    assert review.better_stop(feat, 0, {"action": "skip"}, 1.0, 1.0, 0.0, None, 0.0, 0.0, 0.5) is None


def test_summary_counts_sides_and_verdicts_and_old_decisions(env):
    created = session.create(env["user"], env["settings"], seed=88, rounds=5)
    for k, name in enumerate(["long_open", "short_open", "skip", "short_limit", "long_limit"]):
        atr = session.task_payload(env["user"], created["id"], k)["chart"]["atr"]
        session.decide(env["user"], created["id"], k, BODIES[name](atr))
    out = session.summary_payload(env["user"], created["id"])
    s = out["summary"]
    assert s["sides"]["long"]["entered"] == 2 and s["sides"]["short"]["entered"] == 2 and s["skipped"] == 1
    assert sum(s["verdicts"].values()) == s["reviewed"] == 5
    keys = {ln["key"] for ln in out["lines"]}
    assert {"sides", "verdicts"} <= keys
    assert [t["action"] for t in out["tasks"]] == ["long", "short", "skip", "short", "long"]
    # regi (itelet nelkuli, "enter") dontesek is osszegezhetok
    stored = store.load_session(created["id"], env["user"])
    for t in stored["tasks"]:
        t["result"].pop("review", None)
        if t["decision"]["action"] == "long":
            t["decision"]["action"] = "enter"
    old = scoring.summary(stored["tasks"])
    assert old["entered"] == 4 and old["reviewed"] == 0 and old["sides"]["long"]["entered"] == 2
    assert "verdicts" not in {ln["key"] for ln in session.lines(old)}


def test_new_templates_are_digit_free():
    from stockwatch.practice.templates import TEMPLATES

    formulas = {"l_ema_formula", "l_rsi", "l_r"}                    # keplet-allandok (a lecke levezetese)
    for key, template in TEMPLATES.items():
        text = re.sub(r"\{\d+(:n)?\}|EMA\d+|RSI\(\d\)", "", template)       # indikatornevek
        assert key in formulas or not re.search(r"\d", text), key
