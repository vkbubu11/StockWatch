"""Celar-valoszinuseg (docs/TARGET-PROBABILITY-DESIGN.md): a cel / stop elso elerese helyes (egy napon mindketto:
stop); a jellemzok csak a `t` napig ismert adatbol (a jovo nem valtoztat rajtuk); a fundamentum a beadas utani
naptol; a lekerdezes a tartomanyon kivul nem ad szamot; a tanitas idorendi es a holdoutot nem latja."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockwatch import target_prob as tp

from .synthetic import random_walk


def _walk(bars=1500, seed=3, start="2009-01-01"):
    df = random_walk(bars=bars, seed=seed)
    df.index = pd.bdate_range(start, periods=bars, name="date")
    return df


def test_first_touch_target_and_stop_with_pessimistic_same_day():
    high = np.array([10, 10.5, 11.2, 10.0, 12.5])
    low = np.array([10, 9.8, 10.9, 8.9, 12.0])
    d, s = np.array([1.0, 2.0, 5.0]), np.array([1.0, 1.0, 1.0])
    t, st = tp.first_touch(high, low, 0, 10.0, 1.0, 1, d, s, horizon=4)
    assert t.tolist() == [1, 3, 4] and st.tolist() == [2, 2, 2]                 # 11 a 2. napon; stop 9: a 3. napon
    t, st = tp.first_touch(high, low, 0, 10.0, 0.5, -1, np.array([1.0]), np.array([2.0]), horizon=4)
    assert t.tolist() == [2] and st.tolist() == [1]                             # short: cel 9.5 (3. nap), stop 11 (2.)
    both_h, both_l = np.array([10, 11.5]), np.array([10, 8.5])
    t, st = tp.first_touch(both_h, both_l, 0, 10.0, 1.0, 1, np.array([1.0]), np.array([1.0]), horizon=1)
    assert t[0] == st[0] == 0                                                   # egy napon mindketto -> stop (t < s hamis)


def test_features_do_not_see_the_future():
    df, bench = _walk(), _walk(seed=9)
    full = tp.daily_features(df, bench, None)
    cut = tp.daily_features(df.iloc[:900], bench.iloc[:900], None)
    pd.testing.assert_series_equal(full.iloc[899], cut.iloc[-1], check_names=False)


def test_fundamentals_known_only_after_filing():
    fund = {"eps": [["2019-01-01", "2019-03-31", 1.0, "2019-05-02", "q"], ["2020-01-01", "2020-03-31", 1.5, "2020-05-04", "q"]],
            "revenue": []}
    idx = pd.bdate_range("2020-04-30", "2020-05-08")
    f = tp.fundamental_series(fund, idx)
    assert f.loc["2020-05-04", "eps_yoy"] != 50.0 and f.loc["2020-05-05", "eps_yoy"] == 50.0


def test_training_is_chronological_and_query_has_a_range(monkeypatch):
    monkeypatch.setattr(tp, "DAYS_PER_SYMBOL", 60)
    rng = np.random.default_rng(1)
    parts = []
    for k in range(6):
        df = _walk(bars=4200, seed=20 + k, start="2008-01-01")
        parts.append(tp.samples(f"S{k}", tp.daily_features(df, None, None), df, rng))
    data = pd.concat(parts, ignore_index=True)
    assert data["day"].max() < tp.HOLDOUT_START and data["day"].min() >= tp.START
    assert set(data["hit"].unique()) <= {True, False}
    # tobb ido, kozelebbi cel -> gyakoribb talalat (a cimkek ertelmesek)
    near = data[(data["d"] <= 1) & (data["n"] >= 40) & (data["stop_d"] >= tp.NO_STOP)]["hit"].mean()
    far = data[(data["d"] >= 4) & (data["n"] <= 10) & (data["stop_d"] >= tp.NO_STOP)]["hit"].mean()
    assert near > 0.8 and far < 0.2
    result = tp.train(data)
    assert result["steps"][0]["group"] == "A" and result["rows"]["valid"] > 0
    df = _walk(bars=4200, seed=99, start="2008-01-01")
    today = tp.daily_features(df, None, None).iloc[-1]
    close = float(df["close"].iloc[-1])
    out = tp.query(result, today, close, "long", close + 1.0 * float(today["atr"]), 20)
    assert 0 <= out["probability"] <= 1 and out["d"] == 1.0
    longer = tp.query(result, today, close, "long", close + 1.0 * float(today["atr"]), 60)
    farther = tp.query(result, today, close, "long", close + 4.0 * float(today["atr"]), 20)
    assert longer["probability"] >= out["probability"] >= farther["probability"]        # monoton
    assert not tp.query(result, today, close, "long", close - 1, 20)["ok"]                # rossz oldalon a cel
    assert not tp.query(result, today, close, "long", close + 1, 200)["ok"]               # tul hosszu idotav


def test_estimate_endpoint_validates_and_answers(monkeypatch, tmp_path):
    from stockwatch import best, sessions
    from stockwatch.config import Settings

    monkeypatch.setattr(tp, "DAYS_PER_SYMBOL", 40)
    monkeypatch.setattr(tp, "MODEL_PATH", tmp_path / "model.pkl")
    rng = np.random.default_rng(2)
    data = pd.concat([tp.samples(f"S{k}", tp.daily_features(d, None, None), d, rng)
                      for k, d in enumerate(_walk(bars=4200, seed=40 + k, start="2008-01-01") for k in range(5))])
    tp.save(tp.train(data))
    df = _walk(bars=600, seed=77, start="2024-01-01")
    monkeypatch.setattr(best, "_frame", lambda symbol, settings: df)
    monkeypatch.setattr(sessions, "completed_bars", lambda frame, now=None: frame)
    close = float(df["close"].iloc[-1])
    out = tp.estimate("TEST", "long", close * 1.05, 20, None, Settings())
    assert out["as_of"] == f"{df.index[-1]:%Y-%m-%d}" and 0 <= out["base"] <= 1 and "news" in out
    for bad in (dict(side="up"), dict(target=float("nan")), dict(days=2.5), dict(target=-1)):
        args = dict(side="long", target=close * 1.05, days=20) | bad
        try:
            tp.estimate("TEST", args["side"], args["target"], args["days"], None, Settings())
        except ValueError:
            continue
        raise AssertionError(f"elfogadta: {bad}")
