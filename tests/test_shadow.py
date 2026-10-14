"""Gepi tanulas arnyek-uzem: modell-mentes, modok, naplo, kimenetel, osszevetes."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stockwatch import lab, lab_ml, shadow
from stockwatch.config import LabSettings, Settings
from tests.test_lab import _rows


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "LAB_DIR", tmp_path / "lab")
    monkeypatch.setattr(shadow, "_logged_ids", None)
    shadow._model_cache.update(mtime=None, payload=None)
    return Settings()


def _settings(mode: str) -> Settings:
    return Settings(lab=LabSettings(shadow=mode))


def _save_model(level: str) -> None:
    report = {"trained": "2026-10-13T10:00:00+00:00", "level": level, "verdict": "x", "overall": None}
    assert lab_ml.save_final_model(_rows(signal=True, n=1200), report, entry_cfg={})


def test_modes(env):
    assert shadow.active_model(_settings("on")) is None                  # nincs modell
    _save_model("no")
    assert shadow.active_model(_settings("auto")) is None                # nem bizonyitott -> keszenlet
    assert shadow.active_model(_settings("on")) is not None
    assert shadow.active_model(_settings("off")) is None
    _save_model("better")
    payload = shadow.active_model(_settings("auto"))
    assert payload is not None and payload["level"] == "better"
    # a tanitott jel (rsi) a becslesben is megjelenik; hianyzo jellemzo (NaN) nem hiba
    high = lab_ml.predict(payload, {"rsi": 2.0}, "Bull zaszlo")
    low = lab_ml.predict(payload, {"rsi": -2.0}, "Bull zaszlo")
    assert high > low


def _signal(sid: str, pick: bool = True, qualifies: bool = True, day: str = "2026-01-05") -> SimpleNamespace:
    return SimpleNamespace(
        signal_id=sid, symbol="TEST", as_of=pd.Timestamp(day), kind="breakout", score=60.0, price=100.0,
        pattern=SimpleNamespace(name="Bull zaszlo"), qualifies=lambda cfg: qualifies,
        plan=SimpleNamespace(stop=95.0, target=110.0, atr=2.0, horizon=10),
        ml={"pred_r": 0.4 if pick else -0.1, "threshold": 0.2, "pick": pick, "model": "m", "level": "better"})


def test_log_dedupe_and_cap(env, monkeypatch):
    assert shadow.log_signals([_signal("a"), _signal("b")], env) == 2
    assert shadow.log_signals([_signal("a")], env) == 0                    # ugyanaz a jelzes egyszer
    assert shadow.log_signals([SimpleNamespace(ml=None)], env) == 0         # becsles nelkul nem naploz
    monkeypatch.setattr(shadow, "SHADOW_MAX", 3)
    shadow.log_signals([_signal(f"s{k}") for k in range(1100)], env)
    assert len(shadow._read()) == 3                                         # plafon


def _frame(path: list[tuple]) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-05", periods=len(path))
    o, h, l, c = zip(*path)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1e6}, index=idx)


def _record(**plan) -> dict:
    return {"date": "2026-01-05", "plan": {"stop": 95.0, "target": 110.0, "atr": 2.0, "horizon": 10, **plan},
            "exit": {"mode": "target", "partial_r": 1.0, "trail_atr": 3.0, "gap_pct": 1.0, "slippage_bps": 0}}


def test_outcome_simulation():
    win = _frame([(100, 100, 100, 100), (100, 101, 99, 100), (101, 111, 100, 110)] + [(110, 110, 110, 110)] * 12)
    out = shadow.outcome(_record(), win)
    assert out["traded"] and out["r"] == pytest.approx(2.0) and out["reason"] == "target"
    gap = _frame([(100, 100, 100, 100), (105, 106, 104, 105)] + [(105, 105, 105, 105)] * 12)
    assert shadow.outcome(_record(), gap)["traded"] is False                # tul nagy gap: nincs belepes
    running = _frame([(100, 100, 100, 100), (100, 101, 99, 100), (100, 102, 99, 101)])
    assert shadow.outcome(_record(), running) is None                       # meg fut: nincs kimenetel


def test_report_groups(env, monkeypatch):
    shadow.log_signals([_signal("p1", pick=True), _signal("p2", pick=True), _signal("s1", pick=False)], env)
    records = shadow._read()
    for record, r in zip(records, (1.5, 0.5, -1.0)):
        record["outcome"] = {"traded": True, "r": r}
    shadow._write(records)
    rep = shadow.report(_settings("auto"))
    g = rep["groups"]
    assert (g["pick"]["n"], g["skip"]["n"], g["all"]["n"]) == (2, 1, 3)
    assert g["pick"]["ev"] == pytest.approx(1.0) and g["skip"]["ev"] == pytest.approx(-1.0)
    assert rep["predictions"] == 3 and rep["resolved"] == 3 and not rep["active"]
