"""A jelzesnaplo, a kozos jelazonosito es a visszajatszas tesztjei."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

from stockwatch import entry, indicators, ledger, market, monitor, positions, replay
from stockwatch.config import Settings
from stockwatch.forecast import Calibration, analyse
from tests import synthetic
from tests.test_entry import lenient


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    base = tmp_path / "ledger"
    monkeypatch.setattr(ledger, "LEDGER_DIR", base)
    monkeypatch.setattr(ledger, "SNAPSHOT_DIR", base / "snapshots")
    monkeypatch.setattr(ledger, "CONFIG_STORE", base / "configs")
    monkeypatch.setattr(ledger, "CALIBRATION_STORE", base / "calibrations")
    monkeypatch.setattr(ledger, "CHANGES_PATH", base / "config_changes.jsonl")
    return base


def scenario():
    """Szintetikus bull zaszlo, amin a detektor valoban talal belepesi jeloltet."""
    cfg = lenient().model_copy(update={"min_score": 0, "setup_max_distance_atr": 5})
    settings = Settings(entry=cfg)
    raw = synthetic.bull_flag()
    enriched = indicators.enrich(raw, 14)
    forecast = analyse("TEST", enriched, settings)
    cands = [m for m in forecast.matches if entry.classify(m, enriched, cfg)]
    signals = entry.evaluate("TEST", enriched, cands, settings,
                             support=forecast.support, resistance=forecast.resistance)
    assert signals, "a szintetikus adaton kell jeloltnek lennie"
    return settings, raw, signals[0]


# ---------------------------------------------------------------------------
# Kozos jelazonosito
# ---------------------------------------------------------------------------
def test_signal_id_is_deterministic_and_propagates(tmp_path, monkeypatch):
    settings, raw, signal = scenario()
    _, _, again = scenario()
    assert signal.signal_id == again.signal_id and signal.signal_id.startswith("sig-")
    assert signal.to_dict()["signal_id"] == signal.signal_id

    monkeypatch.setattr(type(settings), "positions_path", property(lambda self: tmp_path / "pos.json"))
    tracker = positions.PositionTracker(settings)
    signal.kind = "breakout"                                  # az elokeszulet nem kerul kovetesre
    assert tracker.register(signal).signal_id == signal.signal_id


# ---------------------------------------------------------------------------
# Napló
# ---------------------------------------------------------------------------
def test_record_contains_versions_checks_and_data_fingerprint(journal_dir):
    settings, raw, signal = scenario()
    journal = ledger.Ledger(settings, Calibration())
    rec = journal.record(signal, "alerted", "minden szuro atment", raw=raw, snapshot=True)
    assert rec["signal_id"] == signal.signal_id and rec["decision"] == "alerted"
    assert rec["versions"]["config"] and rec["versions"]["code"] and rec["versions"]["calibration"] == "nincs"
    assert rec["checks"] and rec["score_parts"] and rec["plan"]["stop"] > 0
    assert rec["data"]["window_hash"] == ledger.window_hash(raw) and rec["data"]["last"] == f"{raw.index[-1]:%Y-%m-%d}"
    assert rec["snapshot"] is True
    assert (journal_dir / "configs" / f"{rec['versions']['config']}.json").exists()
    assert ledger.find(signal.signal_id)[0]["reason"] == "minden szuro atment"


def test_same_decision_recorded_once(journal_dir):
    settings, raw, signal = scenario()
    journal = ledger.Ledger(settings, Calibration())
    assert journal.record(signal, "skipped", "x", raw=raw) is not None
    assert journal.record(signal, "skipped", "x", raw=raw) is None
    # uj folyamat (pl. ujrainditas utan) sem duplikal
    assert ledger.Ledger(settings, Calibration()).record(signal, "skipped", "x", raw=raw) is None
    assert ledger.Ledger(settings, Calibration()).record(signal, "alerted", "y", raw=raw) is not None
    assert len(ledger.find(signal.signal_id)) == 2


def test_read_only_ledger_writes_nothing(journal_dir):
    settings, raw, signal = scenario()
    assert ledger.Ledger(settings, Calibration(), read_only=True).record(signal, "alerted", "x", raw=raw) is None
    assert not journal_dir.exists() or not list(journal_dir.glob("signals-*"))


def test_prune_compresses_old_months_and_caps_snapshots(journal_dir, monkeypatch):
    journal_dir.mkdir(parents=True)
    (journal_dir / "signals-2020-01.jsonl").write_text('{"signal_id": "sig-000000000001"}\n', encoding="utf-8")
    for month in range(1, 13):
        (journal_dir / f"signals-2019-{month:02d}.jsonl.gz").write_bytes(gzip.compress(b"{}\n"))
    monkeypatch.setattr(ledger, "MONTHS_KEPT", 5)
    snaps = journal_dir / "snapshots"
    snaps.mkdir()
    for i in range(4):
        path = snaps / f"sig-{i:012d}.bars.csv.gz"
        path.write_bytes(os.urandom(400_000))
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    monkeypatch.setattr(ledger, "SNAPSHOT_MAX_MB", 1)
    result = ledger.prune(datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert result["compressed"] == 1 and not (journal_dir / "signals-2020-01.jsonl").exists()
    assert len(list(journal_dir.glob("signals-*.jsonl.gz"))) == 5
    assert result["removed_snapshots"] == 2                    # a legregebbiek mennek
    assert (snaps / "sig-000000000003.bars.csv.gz").exists()
    # a tomoritett honapbol is visszakeresheto
    assert len(ledger.find("sig-000000000001")) == 1


def test_config_change_log(journal_dir):
    before = Settings()
    after = before.model_copy(update={"entry": before.entry.model_copy(update={"min_score": 65})})
    rec = ledger.log_config_change(before, after, source="web")
    assert rec["changes"] == {"entry.min_score": [50.0, 65]}
    assert ledger.log_config_change(after, after) is None
    assert ledger.config_changes()[0]["config"] == ledger.config_hash(after)


# ---------------------------------------------------------------------------
# Dontes
# ---------------------------------------------------------------------------
class FakeState:
    def __init__(self, allow=True):
        self.allow = allow

    def should_alert(self, signal):
        return self.allow


def test_entry_decision_reasons():
    settings, raw, signal = scenario()
    decide = monitor.entry_decision
    assert decide(signal, settings, FakeState())[0] == "alerted"
    assert decide(signal, settings, FakeState(False)) == ("skipped", "ismetlesvedelem (10 nap)")
    short_only = settings.model_copy(update={"alerts": settings.alerts.model_copy(update={"direction": "short"})})
    assert decide(signal, short_only, FakeState()) == ("skipped", "a long irany ki van kapcsolva")
    assert decide(signal, settings, FakeState(), index_mode=True)[1].startswith("elokeszulet")
    strict = settings.model_copy(update={"entry": settings.entry.model_copy(update={"min_score": 99})})
    decision, reason = decide(signal, strict, FakeState())
    assert decision == "rejected" and "kuszob" in reason
    signal.checks.append(entry.Check("Trend", False, "teszt"))
    assert decide(signal, settings, FakeState()) == ("rejected", "bukott szuro: Trend")


# ---------------------------------------------------------------------------
# Visszajatszas
# ---------------------------------------------------------------------------
def test_replay_from_snapshot_matches_exactly(journal_dir, monkeypatch):
    settings, raw, signal = scenario()
    ledger.Ledger(settings, Calibration()).record(signal, "alerted", "ok", raw=raw, snapshot=True)
    # a mostani adat mar mas (pl. osztalek-korrekcio) - a pillanatkep miatt ez nem szamit
    monkeypatch.setattr(replay.store, "load", lambda *a, **k: raw * 0.5)
    res = replay.replay(signal.signal_id)
    assert res["source"] == "pillanatkep" and res["same_data"] and res["found"]
    assert res["diffs"] == [] and res["verdict"].startswith("PONTOSAN EGYEZIK")


def test_replay_detects_price_adjustment_without_snapshot(journal_dir, monkeypatch):
    settings, raw, signal = scenario()
    ledger.Ledger(settings, Calibration()).record(signal, "skipped", "x", raw=raw, snapshot=False)
    adjusted = raw.copy()
    adjusted[["open", "high", "low", "close"]] *= 0.98         # visszamenoleges osztalek-korrekcio
    monkeypatch.setattr(replay.store, "load", lambda *a, **k: adjusted)
    monkeypatch.setattr(replay.market, "load_benchmark", lambda *a, **k: pd.DataFrame())
    res = replay.replay(signal.signal_id)
    assert res["source"] == "mostani adat" and not res["same_data"]
    assert res["scale"] == pytest.approx(0.98, abs=1e-6)
    assert res["found"]
    assert not res["diffs"] or "arkorrekcio" in res["verdict"]


def test_replay_unknown_signal(journal_dir):
    with pytest.raises(ValueError, match="Nincs ilyen jelzes"):
        replay.replay("sig-ffffffffffff")
