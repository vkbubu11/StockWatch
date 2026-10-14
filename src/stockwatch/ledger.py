"""Jelzesnaplo - minden belepesi dontes visszakeresheto es visszajatszhato.

Miert kell? A `signals.jsonl` csak a KIKULDOTT ertesiteseket tartja, rotal, es
nem tudja, milyen beallitassal, kodverzioval es adattal szuletett a dontes. Igy
nem merheto, hogy egy szuro jo vagy rossz trade-eket dob-e ki, es egy regi
jelzes sem reprodukalhato.

A naplo (data/ledger/):

  signals-EEEE-HH.jsonl(.gz)   minden LEZART gyertyan kiertekelt belepesi jelolt,
                               a dontessel es az okaval (kikuldve / kihagyva / elutasitva),
                               a szurok mert ertekeivel, a pontszam-bontassal, a
                               beallitas- es kodverzioval, az adat ujjlenyomataval
  snapshots/<jel-id>.csv.gz    a KIKULDOTT jelzesekhez a dontesnel hasznalt teljes
                               gyertyasor (+ a benchmark) - ebbol bitre pontosan
                               visszajatszhato (a yfinance osztalek utan visszamenoleg
                               korrigalja az arakat, a mostani adatbol ez nem menne)
  configs/<hash>.json          minden beallitas-verzio egyszer
  calibrations/<hash>.json     minden kalibracio-verzio egyszer
  config_changes.jsonl         ki / mikor / mit valtoztatott a beallitasokon

Lemezplafon: a lezart honapok tomoritve, `MONTHS_KEPT` honap utan torlodnek;
a pillanatkepek `SNAPSHOT_MAX_MB` felett a legregebbitol torlodnek; a valtozasnaplo
`CHANGES_KEPT` sor.
"""
from __future__ import annotations

import functools
import gzip
import hashlib
import io
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, PROJECT_ROOT, Settings

log = logging.getLogger(__name__)

SCHEMA = 1
LEDGER_DIR = DATA_DIR / "ledger"
SNAPSHOT_DIR = LEDGER_DIR / "snapshots"
CONFIG_STORE = LEDGER_DIR / "configs"
CALIBRATION_STORE = LEDGER_DIR / "calibrations"
CHANGES_PATH = LEDGER_DIR / "config_changes.jsonl"

MONTHS_KEPT = 36
SNAPSHOT_MAX_MB = 50
CHANGES_KEPT = 2000
OHLCV = ["open", "high", "low", "close", "volume"]

DECISION_LABELS = {
    "alerted": "kikuldve",
    "skipped": "kihagyva",
    "rejected": "elutasitva",
    "confirmed": "megerositve",    # nyitas elotti ujraellenorzes: a veglegesitett adaton is megall
    "revoked": "visszavonva",      # nyitas elotti ujraellenorzes: a veglegesitett adaton mar nem all meg
    "manual": "kezzel bekuldve",   # a Paper trading oldalrol, a monitor ertesitese nelkul
}


# ---------------------------------------------------------------------------
# Verziok es ujjlenyomatok
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def code_version() -> str:
    """git commit (ha git repo), kulonben a forraskod tartalmanak hash-e."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=PROJECT_ROOT,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            dirty = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=PROJECT_ROOT,
                                   capture_output=True, text=True, timeout=5).stdout.strip()
            return f"git-{out.stdout.strip()}{'-modositott' if dirty else ''}"
    except (OSError, subprocess.SubprocessError):
        pass
    digest = hashlib.sha1()
    for path in sorted((PROJECT_ROOT / "src" / "stockwatch").rglob("*.py")):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return f"src-{digest.hexdigest()[:12]}"


def config_hash(settings: Settings) -> str:
    """A beallitas-verzio azonositoja; a teljes beallitast egyszer el is tarolja."""
    raw = settings.model_dump_json()
    key = hashlib.sha1(raw.encode()).hexdigest()[:12]
    _store_once(CONFIG_STORE / f"{key}.json", raw)
    return key


def calibration_version(calibration) -> str:
    """A kalibracio-verzio azonositoja (tartalom-hash); egyszer eltarolja."""
    data = getattr(calibration, "data", None) or {}
    if not data:
        return "nincs"
    raw = json.dumps(data, sort_keys=True, default=str)
    key = hashlib.sha1(raw.encode()).hexdigest()[:12]
    _store_once(CALIBRATION_STORE / f"{key}.json", raw)
    return key


def window_hash(df: pd.DataFrame) -> str:
    """A dontesnel hasznalt gyertyasor ujjlenyomata (4 tizedesre kerekitve)."""
    if df is None or df.empty:
        return ""
    frame = df[OHLCV].round(4)
    payload = frame.to_csv(date_format="%Y-%m-%d", float_format="%.4f").encode()
    return hashlib.sha1(payload).hexdigest()[:16]


def _store_once(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_config(key: str) -> Settings | None:
    path = CONFIG_STORE / f"{key}.json"
    return Settings.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def load_calibration(key: str):
    from .forecast import Calibration

    path = CALIBRATION_STORE / f"{key}.json"
    return Calibration(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


# ---------------------------------------------------------------------------
# Pillanatkepek
# ---------------------------------------------------------------------------
def _snapshot_path(signal_id: str, part: str) -> Path:
    return SNAPSHOT_DIR / f"{signal_id}.{part}.csv.gz"


def save_snapshot(signal_id: str, raw: pd.DataFrame, benchmark: pd.DataFrame | None) -> bool:
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        for part, frame in (("bars", raw), ("bench", benchmark)):
            if frame is None or frame.empty:
                continue
            out = frame[OHLCV].copy()
            out.index.name = "date"
            buffer = io.StringIO()
            out.to_csv(buffer, date_format="%Y-%m-%d")
            _snapshot_path(signal_id, part).write_bytes(gzip.compress(buffer.getvalue().encode()))
        return True
    except OSError:
        log.exception("A jelzes pillanatkepe nem mentheto: %s", signal_id)
        return False


def load_snapshot(signal_id: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    frames = []
    for part in ("bars", "bench"):
        path = _snapshot_path(signal_id, part)
        if not path.exists():
            frames.append(None)
            continue
        frame = pd.read_csv(io.BytesIO(gzip.decompress(path.read_bytes())), parse_dates=["date"], index_col="date")
        frame.index = pd.DatetimeIndex(frame.index)
        frames.append(frame[OHLCV])
    return frames[0], frames[1]


# ---------------------------------------------------------------------------
# A naplo
# ---------------------------------------------------------------------------
def _month_path(stamp: datetime) -> Path:
    return LEDGER_DIR / f"signals-{stamp:%Y-%m}.jsonl"


def _iter_files(newest_first: bool = True) -> list[Path]:
    if not LEDGER_DIR.exists():
        return []
    files = list(LEDGER_DIR.glob("signals-*.jsonl")) + list(LEDGER_DIR.glob("signals-*.jsonl.gz"))
    return sorted(files, key=lambda p: p.name.split(".")[0], reverse=newest_first)


def _read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace").splitlines()


def iter_records(newest_first: bool = True):
    for path in _iter_files(newest_first):
        lines = _read_lines(path)
        for line in (reversed(lines) if newest_first else lines):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def find(signal_id: str) -> list[dict]:
    """Az adott jelzes minden naplobejegyzese, a legfrissebb elol."""
    return [r for r in iter_records() if r.get("signal_id") == signal_id]


def alert_key(raw: dict) -> tuple[str, str, str] | None:
    """Melyik jelzeshez tartozik egy kikuldott ertesites: (papir, jelzesnap, alakzat).

    A regi, azonosito nelkul irt ertesitesekhez kell. A paper trade es a kovetett trade
    azonositoja is ez a harmas ("PAPIR|NAP|ALAKZAT"); az elokeszulet nem trade, kimarad.
    """
    info = raw.get("entry") or {}
    ref = str(info.get("broker_trade") or info.get("position") or "")
    if ref.count("|") == 2:
        symbol, as_of, pattern = ref.split("|")
        return symbol, as_of, pattern
    event = str(raw.get("event") or "")
    symbol = str(raw.get("symbol") or "").upper()
    as_of = str(info.get("as_of") or raw.get("as_of") or "")[:10]
    pattern = info.get("pattern") or (raw.get("pattern") or {}).get("name")
    if event.startswith("entry_") and event != "entry_setup" and symbol and as_of and pattern:
        return symbol, as_of, pattern
    return None


def lookup_ids(wanted: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], str]:
    """Jelazonosito a regi, azonosito nelkul irt bejegyzesekhez (ertesites, kovetett trade).

    `wanted`: {(papir, jelzesnap, alakzat)}. Csak a kitoreses / visszateszt jelolteket nezi
    (az elokeszulet nem trade), es a naplot csak a legkorabbi jelzesnap honapjatol olvassa.
    """
    if not wanted:
        return {}
    first = min(key[1] for key in wanted)[:7]
    found: dict[tuple[str, str, str], str] = {}
    for path in _iter_files(newest_first=False):
        if path.name.split(".")[0].removeprefix("signals-") < first:
            continue
        for line in _read_lines(path):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (rec.get("symbol"), rec.get("as_of"), (rec.get("pattern") or {}).get("name"))
            if rec.get("kind") != "setup" and rec.get("signal_id") and key in wanted:
                found.setdefault(key, rec["signal_id"])
        if len(found) == len(wanted):
            break
    return found


class Ledger:
    """Hozzafuzo jelzesnaplo egy futashoz (monitor-kor, indexszkenneles)."""

    def __init__(self, settings: Settings, calibration=None, source: str = "monitor",
                 read_only: bool = False) -> None:
        self.settings = settings
        self.source = source
        self.read_only = read_only
        self.versions = {
            "code": code_version(),
            "config": config_hash(settings) if not read_only else "",
            "calibration": calibration_version(calibration) if (calibration and not read_only) else "nincs",
        }
        self._seen: set[tuple[str, str]] | None = None
        self.written = 0

    def _seen_keys(self, path: Path) -> set[tuple[str, str]]:
        """Ugyanaz a jelzes ugyanazzal a dontessel csak egyszer kerul be (pl. ujrafuttataskor)."""
        if self._seen is None:
            self._seen = set()
            if path.exists():
                for line in _read_lines(path):
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._seen.add((rec.get("signal_id", ""), rec.get("decision", "")))
        return self._seen

    def record(self, signal, decision: str, reason: str, *, raw: pd.DataFrame,
               benchmark: pd.DataFrame | None = None, mode: str = "watchlist",
               snapshot: bool = False, regime_source: str | None = None) -> dict | None:
        """`regime_source`: melyik rezsimet nezte a dontes ("QQQ" / "^GSPC" / "self" = a papir sajat
        trendje). A `benchmark` a papir indexe (relativ ero) - a pillanatkep ezt menti."""
        if self.read_only:
            return None
        now = datetime.now(timezone.utc)
        path = _month_path(now)
        seen = self._seen_keys(path)
        key = (signal.signal_id, decision)
        if key in seen:
            return None

        cfg = self.settings.entry
        pattern = signal.pattern
        last = raw.iloc[-1] if raw is not None and not raw.empty else None
        rec = {
            "schema": SCHEMA,
            "signal_id": signal.signal_id,
            "strategy": getattr(signal, "strategy", "breakout"),
            "recorded_at": now.isoformat(timespec="seconds"),
            "source": self.source,
            "mode": mode,
            "symbol": signal.symbol,
            "as_of": f"{signal.as_of:%Y-%m-%d}",
            "kind": signal.kind,
            "decision": decision,
            "reason": reason,
            "qualifies": signal.qualifies(cfg),
            "price": round(signal.price, 4),
            "score": signal.score,
            "pattern": {
                "name": pattern.name, "direction": pattern.direction, "status": pattern.status,
                "quality": round(pattern.quality, 4), "trigger_level": round(pattern.trigger_level, 4),
                "target": round(pattern.target, 4), "invalidation": round(pattern.invalidation, 4),
                "start_date": f"{pattern.start_date:%Y-%m-%d}", "end_date": f"{pattern.end_date:%Y-%m-%d}",
                "trigger_date": f"{pattern.trigger_date:%Y-%m-%d}" if pattern.trigger_date is not None else None,
                "fingerprint": pattern.fingerprint(),
            },
            "plan": signal.plan.to_dict() | {"atr": round(signal.plan.atr, 4)},
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in signal.checks],
            "score_parts": [{"name": p.name, "points": round(p.points, 2), "max": p.maximum, "reason": p.reason}
                            for p in signal.score_parts],
            "history": signal.history_text,
            "earnings_date": signal.earnings_date.isoformat() if signal.earnings_date else None,
            "warnings": signal.warnings,
            "regime": regime_source,
            "versions": self.versions,
            "data": {
                "bars": int(len(raw)) if raw is not None else 0,
                "first": f"{raw.index[0]:%Y-%m-%d}" if raw is not None and not raw.empty else None,
                "last": f"{raw.index[-1]:%Y-%m-%d}" if raw is not None and not raw.empty else None,
                "window_hash": window_hash(raw),
                "bench_hash": window_hash(benchmark) if benchmark is not None else "",
                "last_bar": {k: round(float(last[k]), 4) for k in OHLCV} if last is not None else None,
            },
            "snapshot": bool(snapshot and save_snapshot(signal.signal_id, raw, benchmark)),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        seen.add(key)
        self.written += 1
        return rec


def append_followup(base: dict, decision: str, reason: str, extra: dict | None = None,
                    source: str = "revalidate") -> dict:
    """Egy mar naplozott jelzes kesobbi allapota (megerositve / visszavonva) - ugyanazzal az azonositoval."""
    now = datetime.now(timezone.utc)
    rec = {k: base.get(k) for k in ("schema", "signal_id", "strategy", "mode", "symbol", "as_of", "kind", "price",
                                     "score", "pattern", "plan", "versions", "data", "regime")}
    rec.update({"recorded_at": now.isoformat(timespec="seconds"), "source": source, "decision": decision,
                "reason": reason, "qualifies": base.get("qualifies"), "checks": base.get("checks"),
                "score_parts": base.get("score_parts"), "snapshot": False, "followup": extra or {}})
    path = _month_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


# ---------------------------------------------------------------------------
# Beallitas-valtozasok
# ---------------------------------------------------------------------------
def log_config_change(before: Settings, after: Settings, source: str = "web") -> dict | None:
    """Mi valtozott a beallitasokban (szekcio.kulcs: regi -> uj)."""
    old, new = before.model_dump(), after.model_dump()
    changes = {}
    for section, values in new.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            prev = (old.get(section) or {}).get(key)
            if prev != value:
                changes[f"{section}.{key}"] = [prev, value]
    if not changes:
        return None
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
           "config": config_hash(after), "previous": config_hash(before), "changes": changes}
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with CHANGES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


def log_watchlist_change(added: list[str], removed: list[str], source: str = "web") -> dict:
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
           "changes": {"watchlist": {"hozzaadva": added, "torolve": removed}}}
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with CHANGES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def config_changes(limit: int = 100) -> list[dict]:
    if not CHANGES_PATH.exists():
        return []
    out = []
    for line in reversed(CHANGES_PATH.read_text(encoding="utf-8").splitlines()[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# Takaritas (housekeeping)
# ---------------------------------------------------------------------------
def prune(now: datetime | None = None) -> dict:
    """Lezart honapok tomoritese, regi honapok / pillanatkepek torlese, valtozasnaplo vagasa."""
    now = now or datetime.now(timezone.utc)
    current = _month_path(now).name
    result = {"compressed": 0, "removed_months": 0, "removed_snapshots": 0}
    if not LEDGER_DIR.exists():
        return result
    for path in LEDGER_DIR.glob("signals-*.jsonl"):
        if path.name != current:
            gz = path.with_suffix(".jsonl.gz")
            gz.write_bytes(gzip.compress(path.read_bytes()))
            path.unlink()
            result["compressed"] += 1
    months = sorted(LEDGER_DIR.glob("signals-*.jsonl.gz"), key=lambda p: p.name, reverse=True)
    for path in months[MONTHS_KEPT:]:
        path.unlink(missing_ok=True)
        result["removed_months"] += 1
    if SNAPSHOT_DIR.exists():
        snaps = sorted(SNAPSHOT_DIR.glob("*.csv.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for path in snaps:
            total += path.stat().st_size
            if total > SNAPSHOT_MAX_MB * 1024 * 1024:
                path.unlink(missing_ok=True)
                result["removed_snapshots"] += 1
    if CHANGES_PATH.exists():
        lines = CHANGES_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > CHANGES_KEPT:
            CHANGES_PATH.write_text("\n".join(lines[-CHANGES_KEPT:]) + "\n", encoding="utf-8")
    return result
