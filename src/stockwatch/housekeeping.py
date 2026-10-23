"""Lemezhasznalat korlatozasa.

A szolgaltatas honapokig futhat felugyelet nelkul, ezert minden irott fajlnak
felso korlatja van. Ez a modul tartja be ezeket a korlatokat:

  * signals.jsonl    - meretalapu rotacio, korlatozott szamu archivummal
  * logs/app.log     - stdlib RotatingFileHandler
  * data/ohlcv/*.csv - retention ablakra vagva
  * state.json       - a lezart / elevult bejegyzesek torlese

Igy az allandosult labnyom ~35 MB, es nem no tovabb.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import backup, store
from .config import DATA_DIR, LOG_DIR, Settings, ensure_dirs, load_settings

log = logging.getLogger(__name__)

MB = 1024 * 1024
ACTIVE_STATE_MAX_DAYS = 365      # ennel regebbi, le nem zarult formacio-bejegyzes torlodik
UNIVERSE_STALE_DAYS = 30         # a mar nem hasznalt universe-fajlok ennyi nap utan torlodnek
PRACTICE_LIMIT_MB = 5             # a gyakorlo feladatkeszlet (practice/store.POOL_LIMIT_MB); a korok az adatbazisban


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(settings: Settings | None = None, verbose: bool = False) -> None:
    """Konzol + rotalo fajl naplozas. Ketszeri hivas nem duplikal handlert."""
    settings = settings or load_settings()
    ensure_dirs()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    if any(getattr(h, "_stockwatch", False) for h in root.handlers):
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-22s %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console._stockwatch = True  # type: ignore[attr-defined]
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_path,
        maxBytes=int(settings.storage.log_max_mb * MB),
        backupCount=settings.storage.log_backups,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    file_handler._stockwatch = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    # A yfinance/urllib3 reszletes naplozasa csak zajt csinalna.
    for noisy in ("urllib3", "yfinance", "peewee", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# JSONL rotacio
# ---------------------------------------------------------------------------
def append_jsonl(path: Path, record: dict, max_mb: float, backups: int) -> None:
    """Egy sor hozzafuzese, meretalapu rotacioval.

    A rotacio az iras ELOTT fut, hogy a fajl merete sose lepje tul erdemben
    a limitet. A backups-nal regebbi archivum torlodik.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max_mb * MB)

    if path.exists() and path.stat().st_size >= max_bytes:
        rotate(path, backups)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def rotate(path: Path, backups: int) -> None:
    """signals.jsonl -> signals.1.jsonl -> signals.2.jsonl -> ... -> torles."""
    if backups <= 0:
        path.unlink(missing_ok=True)
        return

    _archive_path(path, backups).unlink(missing_ok=True)

    for index in range(backups - 1, 0, -1):
        src = _archive_path(path, index)
        if src.exists():
            src.replace(_archive_path(path, index + 1))

    if path.exists():
        path.replace(_archive_path(path, 1))


def _archive_path(path: Path, index: int) -> Path:
    """signals.jsonl + 2 -> signals.2.jsonl

    Szandekosan nem `with_suffix`, mert az a TELJES kiterjesztest cserelne,
    es a tobbszoros pont (signals.1.jsonl) osszezavarna.
    """
    return path.parent / f"{path.stem}.{index}{path.suffix}"


def read_jsonl(
    path: Path, limit: int | None = None, include_backups: bool = True
) -> list[dict]:
    """Rekordok beolvasasa, legfrissebb eloszor. A serult sorokat atlepi."""
    files = [path]
    if include_backups:
        files += [
            _archive_path(path, i)
            for i in range(1, 100)
            if _archive_path(path, i).exists()
        ]

    records: list[dict] = []
    for file in files:
        if not file.exists():
            continue
        batch: list[dict] = []
        with file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    batch.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # A fajlon belul is a legfrissebb keruljon elore.
        batch.reverse()
        records.extend(batch)
        if limit and len(records) >= limit:
            break

    return records[:limit] if limit else records


# ---------------------------------------------------------------------------
# Lemezhasznalat
# ---------------------------------------------------------------------------
@dataclass
class UsageEntry:
    label: str
    bytes_used: int
    limit_bytes: int | None

    @property
    def mb(self) -> float:
        return self.bytes_used / MB

    @property
    def pct_of_limit(self) -> float | None:
        if not self.limit_bytes:
            return None
        return self.bytes_used / self.limit_bytes * 100.0


def _dir_size(path: Path, pattern: str = "**/*") -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.glob(pattern) if p.is_file())


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def disk_usage(settings: Settings | None = None) -> list[UsageEntry]:
    """Fajlonkenti bontasu lemezhasznalat, a limitekhez viszonyitva."""
    settings = settings or load_settings()
    storage = settings.storage

    signals_limit = int(storage.signals_max_mb * (storage.signals_backups + 1) * MB)
    log_limit = int(storage.log_max_mb * (storage.log_backups + 1) * MB)

    return [
        UsageEntry("OHLCV cache (data/ohlcv)", _dir_size(settings.ohlcv_dir), None),
        UsageEntry("Jelzesek (signals*.jsonl)", _dir_size(DATA_DIR, "signals*.jsonl"), signals_limit),
        UsageEntry("Allapot (state.json)", _file_size(settings.state_path), None),
        UsageEntry("Kalibracio (calibration.json)", _file_size(settings.calibration_path), None),
        UsageEntry("Naplok (logs/)", _dir_size(LOG_DIR), log_limit),
        UsageEntry("Backtest univerzum (data/universe)", _dir_size(settings.universe_dir), None),
        UsageEntry("Kutatasi cache (data/research)", _dir_size(DATA_DIR / "research"), None),
        UsageEntry("Visszahuzas-kutatas (data/research/meanrev)", _dir_size(DATA_DIR / "research" / "meanrev"),
                   20 * MB),
        UsageEntry("Jelzesnaplo (data/ledger)", _dir_size(DATA_DIR / "ledger"), None),
        UsageEntry("Indexosszetetel (index_members.json)", _file_size(settings.index_members_path), None),
        UsageEntry("Indexszkenneles eredmenye (index_scan.json)", _file_size(settings.index_scan_path), None),
        UsageEntry("Riasztasi cooldown (entry_state.json)", _file_size(settings.entry_state_path), None),
        UsageEntry("Earnings cache (earnings.json)", _file_size(settings.earnings_path), None),
        UsageEntry("Gyakorlo feladatkeszlet (data/practice)", _practice_bytes(), int(PRACTICE_LIMIT_MB * MB)),
        UsageEntry("Adatbazis-mentesek (data/backup)", backup.usage_bytes(), int(backup.LIMIT_MB * MB)),
    ]


def _practice_bytes() -> int:
    from .practice import store as practice_store

    return practice_store.pool_bytes()


def total_usage_mb(settings: Settings | None = None) -> float:
    settings = settings or load_settings()
    return (_dir_size(DATA_DIR) + _dir_size(LOG_DIR)) / MB


# ---------------------------------------------------------------------------
# Takaritas
# ---------------------------------------------------------------------------
def prune_state(settings: Settings | None = None) -> int:
    """A lezart es elevult jelzesbejegyzesek torlese a state.json-bol.

    Az aktiv (forming/triggered) bejegyzesek sokaig maradnak -
    egy fel eve formalodo csesze-ful meg mindig ervenyes minta lehet.
    """
    settings = settings or load_settings()
    path = settings.state_path
    if not path.exists():
        return 0

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Serult state.json, ujrainditas uresen.")
        path.write_text("{}", encoding="utf-8")
        return 0

    signals = state.get("signals", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.storage.state_retention_days)
    active_cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVE_STATE_MAX_DAYS)
    kept: dict[str, dict] = {}
    removed = 0

    for key, entry in signals.items():
        if entry.get("status") in ("forming", "triggered"):
            # Egy minta, ami ennyi ido alatt sem zarult le, mar nem el - kulonben
            # (sok papirnal) a soha le nem zarulo bejegyzesek korlatlanul gyulnenek.
            first = _parse_stamp(entry.get("first_seen"))
            if first is not None and first < active_cutoff:
                removed += 1
            else:
                kept[key] = entry
            continue

        stamp = _parse_stamp(entry.get("updated_at"))
        if stamp is None or stamp < cutoff:
            removed += 1
        else:
            kept[key] = entry

    state["signals"] = kept
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return removed


def _parse_stamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def trim_ohlcv(settings: Settings | None = None) -> int:
    """A cache-elt CSV-k vagasa a retention ablakra."""
    settings = settings or load_settings()
    trimmed_rows = 0

    for symbol in store.cached_symbols(settings):
        df = store.load(symbol, settings)
        if df.empty:
            continue
        kept = store.apply_retention(df, settings.storage.ohlcv_retention_years)
        if len(kept) < len(df):
            trimmed_rows += len(df) - len(kept)
            store.save(symbol, kept, settings)

    return trimmed_rows


def prune_universe(settings: Settings | None = None) -> int:
    """A tomoritett cache-bol torli, amire mar semmi nem hivatkozik.

    Megmarad: a kutatasi univerzum (config/universe.yaml), a benchmark, es a
    bekapcsolt indexek tagjai. Ami kiesett egy indexbol (vagy kikapcsoltad az
    indexet), az `UNIVERSE_STALE_DAYS` nap utan torlodik - igy a lemezhasznalat
    a bekapcsolt indexek meretehez igazodik, es nem gyulik.
    """
    import time

    import yaml

    from . import config as config_mod
    from . import indexes
    from .config import CONFIG_DIR

    settings = settings or load_settings()
    folder = settings.universe_dir
    if not folder.exists():
        return 0
    from .market import REGIME_INDEXES

    keep = {settings.entry.benchmark.upper(), *REGIME_INDEXES}
    research = CONFIG_DIR / "universe.yaml"
    if research.exists():
        raw = yaml.safe_load(research.read_text(encoding="utf-8")) or {}
        keep |= set(config_mod.yaml_tickers(raw.get("symbols", [])))
    keep |= set(indexes.IndexMembers(settings).symbols(allow_network=False))
    limit = time.time() - UNIVERSE_STALE_DAYS * 86400
    removed = 0
    for path in folder.glob("*.csv.gz"):
        symbol = path.name[: -len(".csv.gz")].upper()
        if symbol not in keep and path.stat().st_mtime < limit:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _prune_ledger() -> dict:
    from . import ledger

    try:
        return ledger.prune()
    except OSError:
        log.exception("A jelzesnaplo takaritasa nem sikerult")
        return {}


def enforce_jsonl_limits(settings: Settings | None = None) -> None:
    """Rotaciot kenyszerit, ha a jelzesfajl tullepte a limitet."""
    settings = settings or load_settings()
    path = settings.signals_path

    if _file_size(path) >= int(settings.storage.signals_max_mb * MB):
        rotate(path, settings.storage.signals_backups)

    # A limit folotti archivumok torlese (pl. ha csokkentettek a beallitast).
    for index in range(settings.storage.signals_backups + 1, 100):
        _archive_path(path, index).unlink(missing_ok=True)


def run(settings: Settings | None = None) -> dict:
    """A napi takaritas - a scheduler ezt hivja piaczaras utan."""
    settings = settings or load_settings()
    ensure_dirs()

    result: dict = {
        "trimmed_ohlcv_rows": trim_ohlcv(settings),
        "pruned_state_entries": prune_state(settings),
        "pruned_universe_files": prune_universe(settings),
        "ledger": _prune_ledger(),
        "pruned_backups": len(backup.prune()),
    }
    enforce_jsonl_limits(settings)

    total = total_usage_mb(settings)
    result["total_mb"] = round(total, 2)
    result["over_limit"] = total > settings.storage.max_data_mb

    if result["over_limit"]:
        log.warning(
            "A lemezhasznalat (%.1f MB) tullepte a %.0f MB kuszobot - ellenorizd a "
            "data/universe konyvtarat vagy csokkentsd a retention ablakot.",
            total,
            settings.storage.max_data_mb,
        )
    else:
        log.info(
            "Takaritas kesz. Lemezhasznalat: %.1f MB (limit %.0f MB).",
            total,
            settings.storage.max_data_mb,
        )

    return result
