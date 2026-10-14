"""Strategia-labor: novekmenyes kitores-gyujtes, kiserlet-tar, becsuletes osszegzes, gepi tanulas."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry_research as er
from stockwatch import lab, lab_ml, store, webapp
from stockwatch.config import EntrySettings, Settings
from tests.synthetic import random_walk


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "LAB_DIR", tmp_path / "lab")
    settings = Settings()
    monkeypatch.setattr(type(settings), "universe_dir", property(lambda self: tmp_path / "universe"))
    monkeypatch.setattr(type(settings), "ohlcv_dir", property(lambda self: tmp_path / "ohlcv"))
    lab._last_dates.clear()
    return settings


def _events(symbol: str) -> list[tuple]:
    return [(str(d.date()), m.name, round(m.trigger_level, 4)) for d, _, m in lab.load_cache(symbol)["events"]]


# ---------------------------------------------------------------------------
# Kitoresek: a folytatas ugyanazt adja, mint a teljes ujrafuttatas
# ---------------------------------------------------------------------------
def test_incremental_collection_matches_full_run(env):
    df = random_walk(bars=560, seed=3)
    raw = env.model_dump()
    store.save("FULL", df, env, universe=True)
    assert lab.collect_symbol("FULL", raw)[1] == "uj"
    full = _events("FULL")
    assert full, "a tesztsorban legyen legalabb egy kitores"

    store.save("INC", df.iloc[:-60], env, universe=True)
    lab._last_dates.clear()
    lab.collect_symbol("INC", raw)
    store.save("INC", df, env, universe=True)
    lab._last_dates.clear()
    assert lab.needs_collection("INC", env, lab.settings_key(env))
    assert lab.collect_symbol("INC", raw)[1] == "frissitve"
    assert _events("INC") == full
    assert lab.collect_symbol("INC", raw)[1] == "valtozatlan"


def test_rewritten_history_triggers_full_recompute(env):
    """Osztalek-korrekcio: a multbeli arak atirodnak -> az adott papirt ujra kell szamolni."""
    df = random_walk(bars=400, seed=5)
    raw = env.model_dump()
    store.save("DIV", df.iloc[:-5], env, universe=True)
    lab.collect_symbol("DIV", raw)
    adjusted = df.copy()
    adjusted[["open", "high", "low", "close"]] *= 0.99
    store.save("DIV", adjusted, env, universe=True)
    lab._last_dates.clear()
    assert lab.collect_symbol("DIV", raw)[1] == "uj"


def test_failing_symbol_is_not_retried_every_pass(env):
    store.save("SHORT", random_walk(bars=100), env, universe=True)      # 320 gyertya alatt
    assert lab.collect_symbol("SHORT", env.model_dump())[1] == "hiba"
    assert not lab.needs_collection("SHORT", env, lab.settings_key(env))


# ---------------------------------------------------------------------------
# Kiserletek
# ---------------------------------------------------------------------------
def test_validate_entry():
    base = EntrySettings()
    cfg = lab.validate_entry({"min_stop_atr": "2.5", "regime_filter": False, "min_momentum_pct": None}, base)
    assert cfg.min_stop_atr == 2.5 and cfg.regime_filter is False and cfg.min_momentum_pct is None
    for bad in ({"telegram": 1}, {"min_stop_atr": 99}, {"rsi_min": 80, "rsi_max": 70}, {"exit_mode": "x"}):
        with pytest.raises(ValueError):
            lab.validate_entry(bad, base)


def test_seed_cap_and_delete(env, monkeypatch):
    lab.seed(env)
    data = lab.load_store()
    assert data["experiments"][0]["live"] and len(data["experiments"]) == 1 + len(er.study_grid(env.entry))
    lab.seed(env)                                                          # masodszor nem dupláz
    assert len(lab.load_store()["experiments"]) == len(data["experiments"])
    monkeypatch.setattr(lab, "MAX_EXPERIMENTS", len(data["experiments"]))
    with pytest.raises(ValueError):
        lab.add_experiment("tul sok", "indexes", EntrySettings())
    live_id = data["experiments"][0]["id"]
    with pytest.raises(ValueError):
        webapp.lab_action("delete", {"id": live_id})
    with pytest.raises(ValueError):
        webapp.lab_action("holdout", {"id": live_id})                       # megerosites nelkul


def _result(day: str, r: float) -> er.Result:
    return er.Result("X", "Bull zaszlo", pd.Timestamp(day), "confirm", r, "target", 5)


def test_summary_hides_holdout_until_opened():
    results = [_result("2019-05-01", 1.0), _result("2023-05-01", -1.0),
               _result("2025-06-01", 2.0), _result("2026-11-02", 0.5)]
    s = lab.summarize(results, created="2026-10-13T10:00:00+00:00")
    assert (s["train"]["n"], s["test"]["n"], s["holdout"]["n"], s["forward"]["n"]) == (1, 1, 1, 1)
    assert s["all"]["n"] == 2                                              # a latszo idoszakok
    exp = {"id": "abc", "summary": s, "holdout_opened": None}
    assert "holdout" not in lab.public_summary(exp)["summary"]
    exp["holdout_opened"] = "2026-10-14"
    assert lab.public_summary(exp)["summary"]["holdout"]["n"] == 1


def test_results_roundtrip_and_cap(env, monkeypatch):
    monkeypatch.setattr(lab, "MAX_TRADES", 3)
    lab.save_results("abc", [_result(f"2020-01-0{d}", d / 10) for d in range(1, 6)])
    back = lab.load_results("abc")
    assert len(back) == 3 and back[-1].r == pytest.approx(0.5)            # a legfrissebbek maradnak


# ---------------------------------------------------------------------------
# Gepi tanulas: csak a multbol tanul, es eszreveszi a valodi jelet
# ---------------------------------------------------------------------------
def _rows(signal: bool, n: int = 3000, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2014-01-01", "2024-12-31")
    rows = []
    for k in range(n):
        d = dates[rng.integers(len(dates))]
        x = rng.normal()
        r = (0.6 * x if signal else 0.0) + rng.normal(0, 1.0)
        row = {name: rng.normal() for name in lab_ml.NUMERIC}
        row.update(rsi=x, symbol=f"S{k % 50}", pattern="Bull zaszlo", date=d, exit_date=d + pd.Timedelta(days=10),
                   r=float(r), reason="target")
        rows.append(row)
    return rows


def test_walk_forward_finds_real_signal():
    report = lab_ml.walk_forward(_rows(signal=True), live_keys=set())
    o = report["overall"]
    assert o["ic"] > 0.15 and o["ml"]["ev"] > o["all"]["ev"] + 0.2
    assert report["features"][0]["name"] == "RSI"
    assert all(y["year"] < er.HOLDOUT_START.year + 1 for y in report["years"])


def test_walk_forward_no_signal_no_edge():
    o = lab_ml.walk_forward(_rows(signal=False, seed=2), live_keys=set())["overall"]
    assert abs(o["ic"]) < 0.06
    assert o["ml"]["ev_lo"] < 0 < o["ml"]["ev_hi"] or abs(o["ml"]["ev"] - o["all"]["ev"]) < 0.1


def test_partial_last_bar_rescans_only_the_last_day(env, monkeypatch):
    """Napkozben mentett, meg alakulo gyertya: zaras utan csak az utolso nap kell ujra, nem a 10 ev."""
    df = random_walk(bars=560, seed=3)
    raw = env.model_dump()
    store.save("REF", df, env, universe=True)
    lab.collect_symbol("REF", raw)
    partial = df.copy()
    partial.iloc[-1, partial.columns.get_loc("close")] *= 1.01         # napkozbeni ar
    for legacy in (False, True):                                        # True: regi cache, anchor_prev nelkul
        name = f"P{int(legacy)}"
        store.save(name, partial, env, universe=True)
        lab._last_dates.clear()
        lab.collect_symbol(name, raw)
        if legacy:
            cache = lab.load_cache(name)
            cache.pop("anchor_prev")
            lab._write_cache(name, cache)
        store.save(name, df, env, universe=True)                         # a vegleges zaro
        lab._last_dates.clear()
        assert lab.collect_symbol(name, raw)[1] == "utolso nap ujra"
        assert _events(name) == _events("REF")


def test_forming_bar_is_ignored(env, monkeypatch):
    df = random_walk(bars=400, seed=5)
    store.save("LIVE", df, env, universe=True)
    last = df.index[-1]
    monkeypatch.setattr(lab.sessions, "session_closed", lambda day, now=None: pd.Timestamp(day) < last)
    assert lab.load_frame("LIVE", env).index[-1] == df.index[-2]
    assert lab.data_last_date("LIVE", env) == df.index[-2]


def test_boost_scales_result_r():
    cfg = EntrySettings(boost_score=70, boost_risk_mult=1.5)
    assert cfg.risk_mult(69.9) == 1.0 and cfg.risk_mult(70) == 1.5 and cfg.risk_mult(None) == 1.0
    assert EntrySettings().risk_mult(99) == 1.0                            # alapbol ki
