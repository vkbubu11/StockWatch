"""Lemezhasznalat-korlatozas tesztje.

A felhasznaloi kovetelmeny: "ha hosszu ideig fut, akkor se arassza el a gepet
adatokkal". Ez a tesztfajl pontosan ezt ellenorzi - nem elmeletben, hanem
tenyleges fajlirassal.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from stockwatch import housekeeping, store
from stockwatch.config import Settings

MB = housekeeping.MB


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Izolalt beallitas ideiglenes konyvtarral.

    A modulszintu DATA_DIR / LOG_DIR konstansokat is atiranyitjuk, kulonben a
    teszt a valodi projekt adatait bantana.
    """
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    (data_dir / "ohlcv").mkdir(parents=True)
    (data_dir / "universe").mkdir(parents=True)
    log_dir.mkdir(parents=True)

    monkeypatch.setattr(housekeeping, "DATA_DIR", data_dir)
    monkeypatch.setattr(housekeeping, "LOG_DIR", log_dir)

    config = Settings()
    monkeypatch.setattr(type(config), "ohlcv_dir", property(lambda self: data_dir / "ohlcv"))
    monkeypatch.setattr(type(config), "universe_dir", property(lambda self: data_dir / "universe"))
    monkeypatch.setattr(type(config), "signals_path", property(lambda self: data_dir / "signals.jsonl"))
    monkeypatch.setattr(type(config), "state_path", property(lambda self: data_dir / "state.json"))
    monkeypatch.setattr(type(config), "calibration_path", property(lambda self: data_dir / "calibration.json"))
    monkeypatch.setattr(type(config), "log_path", property(lambda self: log_dir / "app.log"))
    return config


# ---------------------------------------------------------------------------
# A fo kovetelmeny: a jelzesfajl nem nohet korlatlanul
# ---------------------------------------------------------------------------
def test_signals_file_stays_within_limit(settings):
    """50 000 jelzes beirasa utan a data/ merete NEM lepheti tul a plafont.

    Ez a felhasznaloi kovetelmeny kozvetlen ellenorzese.
    """
    settings.storage.signals_max_mb = 0.1      # 100 KB, hogy a teszt gyors legyen
    settings.storage.signals_backups = 3

    record = {
        "timestamp": "2026-09-20T10:00:00Z",
        "event": "triggered",
        "symbol": "AAPL",
        "pattern": {"name": "Dupla alj", "quality": 0.81},
        "filler": "x" * 200,
    }

    for i in range(50_000):
        housekeeping.append_jsonl(
            settings.signals_path, {**record, "n": i},
            settings.storage.signals_max_mb, settings.storage.signals_backups,
        )

    # Plafon = (aktiv fajl + archivumok) * max meret, kis toleranciaval,
    # mert az utolso sor meg beleirodik a hataratlepes elott
    ceiling = settings.storage.signals_max_mb * (settings.storage.signals_backups + 1) * MB * 1.1
    actual = housekeeping._dir_size(housekeeping.DATA_DIR, "signals*.jsonl")

    assert actual <= ceiling, (
        f"A jelzesfajlok {actual / MB:.2f} MB-ot foglalnak, a plafon {ceiling / MB:.2f} MB"
    )

    # ...es a LEGFRISSEBB jelzeseknek meg kell maradniuk
    latest = housekeeping.read_jsonl(settings.signals_path, limit=1)
    assert latest, "A rotacio utan nem maradt olvashato jelzes"
    assert latest[0]["n"] == 49_999, "A legfrissebb jelzes elveszett a rotacio soran"


def test_rotation_keeps_only_configured_backups(settings):
    settings.storage.signals_max_mb = 0.01
    settings.storage.signals_backups = 2

    for i in range(3000):
        housekeeping.append_jsonl(
            settings.signals_path, {"n": i, "filler": "y" * 100},
            settings.storage.signals_max_mb, settings.storage.signals_backups,
        )

    archives = list(housekeeping.DATA_DIR.glob("signals.*.jsonl"))
    assert len(archives) <= settings.storage.signals_backups, (
        f"Tul sok archivum maradt: {[a.name for a in archives]}"
    )


def test_enforce_limits_removes_excess_archives(settings):
    """Ha csokkentjuk a beallitott archivumszamot, a folosleg torlodik."""
    settings.storage.signals_max_mb = 5
    settings.storage.signals_backups = 2

    for index in range(1, 6):
        (housekeeping.DATA_DIR / f"signals.{index}.jsonl").write_text("{}\n", encoding="utf-8")

    housekeeping.enforce_jsonl_limits(settings)

    remaining = sorted(p.name for p in housekeeping.DATA_DIR.glob("signals.*.jsonl"))
    assert remaining == ["signals.1.jsonl", "signals.2.jsonl"], remaining


# ---------------------------------------------------------------------------
# state.json pruning
# ---------------------------------------------------------------------------
def test_state_pruning_keeps_active_removes_stale(settings):
    settings.storage.state_retention_days = 90
    now = datetime.now(timezone.utc)

    settings.state_path.write_text(
        json.dumps(
            {
                "signals": {
                    "aktiv_regi": {
                        "status": "triggered",
                        "updated_at": (now - timedelta(days=400)).isoformat(),
                    },
                    "aktiv_formalodo": {
                        "status": "forming",
                        "updated_at": (now - timedelta(days=200)).isoformat(),
                    },
                    "lezart_friss": {
                        "status": "target_hit",
                        "updated_at": (now - timedelta(days=10)).isoformat(),
                    },
                    "lezart_regi": {
                        "status": "failed",
                        "updated_at": (now - timedelta(days=120)).isoformat(),
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    removed = housekeeping.prune_state(settings)
    kept = json.loads(settings.state_path.read_text(encoding="utf-8"))["signals"]

    assert removed == 1
    # Az aktiv bejegyzesek koruktol fuggetlenul maradnak: egy fel eve formalodo
    # csesze-ful meg mindig ervenyes minta lehet
    assert "aktiv_regi" in kept
    assert "aktiv_formalodo" in kept
    assert "lezart_friss" in kept
    assert "lezart_regi" not in kept


def test_corrupt_state_file_is_reset(settings):
    settings.state_path.write_text("{ ez nem ervenyes json", encoding="utf-8")
    assert housekeeping.prune_state(settings) == 0
    assert json.loads(settings.state_path.read_text(encoding="utf-8")) == {}


# ---------------------------------------------------------------------------
# OHLCV retention
# ---------------------------------------------------------------------------
def test_save_applies_retention_immediately(settings):
    """A mentes MAR vagja a retention ablakra - nem varunk a takaritasra.

    Igy a CSV sosem no tul a hataron, meg ket takaritas kozott sem.
    """
    settings.storage.ohlcv_retention_years = 10

    index = pd.bdate_range("2006-01-01", "2026-01-01", name="date")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1e6},
        index=index,
    )
    store.save("TEST", frame, settings)
    saved = store.load("TEST", settings)

    span_years = (saved.index.max() - saved.index.min()).days / 365.25
    assert span_years <= 10.1, f"A mentes utan {span_years:.1f} evnyi adat maradt"
    assert len(saved) < len(frame)


def test_housekeeping_trims_oversized_existing_file(settings):
    """A takaritas levagja a mar meglevo, tulmeretes CSV-t.

    A `store.save` megkerulesevel irunk nyers fajlt - ez szimulalja azt az
    esetet, amikor korabban hosszabb retention volt beallitva, vagy kezzel
    kerult oda az adat.
    """
    settings.storage.ohlcv_retention_years = 10

    index = pd.bdate_range("2006-01-01", "2026-01-01", name="date")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1e6},
        index=index,
    )
    frame.index.name = "date"
    frame.to_csv(settings.ohlcv_dir / "OLD.csv", date_format="%Y-%m-%d")

    before = len(store.load("OLD", settings))
    assert before == len(frame), "A nyers fajlnak vagatlanul kell betoltodnie"

    trimmed = housekeeping.trim_ohlcv(settings)
    after = store.load("OLD", settings)

    span_years = (after.index.max() - after.index.min()).days / 365.25
    assert span_years <= 10.1, f"A takaritas utan {span_years:.1f} ev maradt"
    assert len(after) < before
    assert trimmed == before - len(after)


def test_ohlcv_roundtrip_preserves_values(settings):
    index = pd.bdate_range("2024-01-01", periods=50, name="date")
    frame = pd.DataFrame(
        {
            "open": range(100, 150),
            "high": range(101, 151),
            "low": range(99, 149),
            "close": range(100, 150),
            "volume": [1e6] * 50,
        },
        index=index,
        dtype="float64",
    )
    store.save("RT", frame, settings)
    loaded = store.load("RT", settings)

    pd.testing.assert_frame_equal(frame, loaded, check_freq=False)


def test_merge_prefers_fresher_data(settings):
    """Utkozeskor az ujabb letoltes nyer - a szolgaltato utolag javithat."""
    index = pd.bdate_range("2024-01-01", periods=5, name="date")
    old = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index
    )
    fresh = pd.DataFrame(
        {"open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 2.0}, index=index[-2:]
    )

    merged = store.merge(old, fresh)
    assert len(merged) == 5
    assert merged["close"].iloc[-1] == 2.0
    assert merged["close"].iloc[0] == 1.0


# ---------------------------------------------------------------------------
# Osszesites
# ---------------------------------------------------------------------------
def test_full_housekeeping_run_reports_usage(settings):
    for i in range(500):
        housekeeping.append_jsonl(
            settings.signals_path, {"n": i}, settings.storage.signals_max_mb,
            settings.storage.signals_backups,
        )

    result = housekeeping.run(settings)

    assert "total_mb" in result
    assert result["over_limit"] is False
    assert result["total_mb"] < settings.storage.max_data_mb


def test_disk_usage_reports_every_component(settings):
    entries = housekeeping.disk_usage(settings)
    labels = [e.label for e in entries]

    assert any("OHLCV" in l for l in labels)
    assert any("Jelzesek" in l for l in labels)
    assert any("Naplok" in l for l in labels)
    # A limittel rendelkezo komponenseknel legyen kiszamolt kihasznaltsag
    for entry in entries:
        if entry.limit_bytes:
            assert entry.pct_of_limit is not None
