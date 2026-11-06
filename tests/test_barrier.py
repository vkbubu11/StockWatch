"""Az analitikus celar-valoszinuseg (barrier.py): a tukrozesi zart alak egyezik a szimulacioval; a stopos tablazat
messzi stopnal a zart alakhoz tart, szimmetrikus gataknal ~fele-fele; a HAR a valodi volatilitast talalja meg."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockwatch import barrier


def test_touch_probability_matches_simulation_and_stop_table_limits():
    rng = np.random.default_rng(3)
    w = np.cumsum(rng.normal(0, np.sqrt(1 / 2000), (20000, 2000)), axis=1)
    sim = (w.max(axis=1) >= 1.0).mean()
    assert abs(barrier.touch_prob(np.array([1.0]))[0] - sim) < 0.02                       # 2(1-Phi(1)) = 0,317
    far = barrier.target_before_stop(np.array([1.0]), np.array([4.0]))[0]
    assert abs(far - barrier.touch_prob(np.array([1.0]))[0]) < 0.02                        # messzi stop: nincs hatasa
    sym = barrier.target_before_stop(np.array([0.3]), np.array([0.3]))[0]
    assert 0.44 < sym < 0.52                                                                # kozeli, szimmetrikus gatak
    tight = barrier.target_before_stop(np.array([1.0]), np.array([0.5]))[0]
    assert tight < far                                                                      # szukebb stop: kisebb esely


def test_har_recovers_the_true_volatility():
    rng = np.random.default_rng(5)
    rows = []
    for k in range(20):
        sig = 0.01 + 0.02 * (k % 4) / 3                                                    # papironkent mas szint
        r = rng.normal(0, sig, 1500)
        c = 100 * np.exp(np.cumsum(r))
        o = np.r_[100, c[:-1]]
        spread = np.abs(rng.normal(0, sig * 0.5, 1500))
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * np.exp(spread), "low": np.minimum(o, c) * np.exp(-spread),
                           "close": c}, index=pd.bdate_range("2005-01-03", periods=1500))
        v = barrier.daily_variance(df)
        rows.append(pd.concat([barrier.har_regressors(v), barrier.har_targets(v)], axis=1).assign(sig=sig))
    x = pd.concat(rows).dropna()
    coef = barrier.fit_har(x)
    pred = barrier.har_sigma(x, np.full(len(x), 20.0), coef)
    for sig in x["sig"].unique():                                                          # papironkent a szintet koveti
        m = (x["sig"] == sig).to_numpy()
        assert 0.6 < np.median(pred[m]) / sig < 1.6
