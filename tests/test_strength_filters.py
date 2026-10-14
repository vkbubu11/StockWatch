"""Az opcionalis momentum / erosseg szurok (52 hetes csucs, 12-1 momentum, relativ ero, osszehuzodas)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry
from stockwatch.config import EntrySettings


def frame(closes, spread=0.01) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range("2022-01-03", periods=len(closes))
    return pd.DataFrame({"open": closes, "high": closes * (1 + spread), "low": closes * (1 - spread),
                         "close": closes, "volume": 1e6}, index=index)


def test_high52_ratio():
    df = frame(np.r_[np.full(200, 100.0), np.full(60, 80.0)])
    assert entry.high52_ratio(df) == pytest.approx(80 / 101)
    assert entry.high52_ratio(frame(np.full(100, 50.0))) is None          # tul rovid


def test_momentum_skips_last_month():
    closes = np.r_[np.full(232, 100.0), np.full(21, 150.0)]                # csak az utolso honapban emelkedett
    assert entry.momentum_12_1(frame(closes)) == pytest.approx(0.0)
    closes = np.r_[np.linspace(100, 130, 232), np.full(21, 130.0)]
    assert entry.momentum_12_1(frame(closes)) == pytest.approx(30.0, abs=0.5)


def test_contraction_ratio():
    wide, tight = frame(np.full(120, 100.0), spread=0.02), frame(np.full(120, 100.0), spread=0.02)
    tight.loc[tight.index[-12:-2], "high"] = 100.5
    tight.loc[tight.index[-12:-2], "low"] = 99.5
    assert entry.contraction_ratio(wide) == pytest.approx(1.0)
    assert entry.contraction_ratio(tight) == pytest.approx(0.25)


def test_filters_off_by_default_and_block_when_on():
    df = frame(np.r_[np.full(200, 100.0), np.full(60, 80.0)])
    assert entry.strength_checks(df, EntrySettings(), rel_strength=-5.0) == []
    cfg = EntrySettings(min_high52_ratio=0.9, min_momentum_pct=0.0, min_rel_strength=0.0, max_contraction=0.8)
    checks = {c.name: c.passed for c in entry.strength_checks(df, cfg, rel_strength=-5.0)}
    assert checks == {"52 hetes csucs": False, "Momentum 12-1": False, "Relativ ero": False,
                      "Osszehuzodas": False}
    unknown = {c.name: c.passed for c in entry.strength_checks(df, cfg, rel_strength=None)}
    assert unknown["Relativ ero"] is None                                   # nem ellenorizheto != bukas
