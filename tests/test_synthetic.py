"""Szintetikus chartgenerator (#38): determinisztikus, a hozamstatisztika a valodi reszvenysorok savjaban
(stilizalt tenyek), a beepitett alakzatok legalabb DETECT_MIN reszet a felismero megtalalja, es a gyakorlo kor
a szintetikus keszlettel is mukodik (papir es datum nelkuli felfedessel)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import indicators, patterns
from stockwatch.config import Settings
from stockwatch.practice import sampler, session, store, synthetic

SAMPLE = synthetic.symbols(15)
DETECT_MIN = 0.40                    # a beepitett alakzatok ennyi reszet a felismero ugyanazon a neven talalja meg


def test_generation_is_deterministic_and_well_formed():
    a, b = synthetic.frame("SYN0007"), synthetic.frame("SYN0007")
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(synthetic.frame("SYN0008"))
    assert synthetic.END == sampler.VISIBLE_END and a.index[-1] == synthetic.END and len(a) == synthetic.N_BARS
    assert not a.isna().any().any() and (a["volume"] > 0).all()
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all() and (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    with pytest.raises(ValueError):
        synthetic.generate("AAPL")
    assert synthetic.is_synthetic("SYN0001") and not synthetic.is_synthetic("SYNX")


def test_return_statistics_are_in_the_band_of_real_stocks():
    for symbol in SAMPLE:
        r = np.diff(np.log(synthetic.frame(symbol)["close"].to_numpy()))
        a = np.abs(r)
        excess_kurtosis = ((r - r.mean()) ** 4).mean() / r.var() ** 2 - 3
        assert 0.007 < r.std() < 0.03, symbol                              # napi szoras: ~0,7-3%
        assert excess_kurtosis > 1, symbol                                 # vastag farok
        assert np.corrcoef(a[1:], a[:-1])[0, 1] > 0.05, symbol             # volatilitas-klaszterezes
        assert abs(np.corrcoef(r[1:], r[:-1])[0, 1]) < 0.2, symbol         # a hozam maga alig autokorrelalt
        assert a.max() < 0.3, symbol                                       # nincs split-szeru ugras


def test_detector_finds_the_embedded_patterns():
    settings = Settings()
    found_by_name: dict[str, int] = {}
    hits = total = 0
    for symbol in SAMPLE:
        df, embedded = synthetic.generate(symbol)
        enriched = indicators.enrich(df, settings.structure.atr_period)
        for name, _direction, start, pivot, _success in embedded:
            names = set()
            for extra in (6, 10, 14):                                      # nehany nappal az utolso pivot utan
                end = pivot + extra
                window = enriched.iloc[max(0, end - 400): end + 1]
                names |= {m.name for m in patterns.detect(symbol, window, settings.patterns, settings.structure,
                                                          confirmed_only=True) if m.end_date >= df.index[start]}
            total += 1
            hits += name in names
            found_by_name[name] = found_by_name.get(name, 0) + (name in names)
    assert hits / total >= DETECT_MIN, f"{hits}/{total}"
    assert set(found_by_name) == set(synthetic.TEMPLATES) and all(found_by_name.values()), found_by_name


def test_practice_round_on_the_synthetic_pool_hides_symbol_and_date(user):
    settings = Settings(practice={"source": "synthetic", "synthetic_symbols": 12})
    assert sampler.universe(settings) == synthetic.symbols(12)
    pool = sampler.build(settings)
    assert pool["counts"]["strategy"] > 0 and pool["counts"]["nothing"] > 0 and pool["counts"]["pattern"] > 0
    created = session.create(user, settings, seed=3, rounds=4)
    task = session.task_payload(user, created["id"], 0, settings)
    assert len(task["chart"]["bars"]) == sampler.VISIBLE_BARS
    reveal = session.decide(user, created["id"], 0, {"action": "skip"}, settings)["reveal"]
    assert reveal["symbol"] == "Szintetikus helyzet" and reveal["date"] is None
    assert store.load_session(created["id"], user)["tasks"][0]["symbol"].startswith("SYN")
    summary = session.summary_payload(user, created["id"])
    assert summary["tasks"][0]["symbol"] == "Szintetikus helyzet" and summary["tasks"][0]["date"] is None
