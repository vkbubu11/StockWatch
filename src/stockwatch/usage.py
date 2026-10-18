"""Felhasznalonkenti meres: CPU-ido es futasszam fajtankent, havonta (SaaS-elokeszites).

Ma csak mer, nem tilt. A SAAS-DESIGN 9. fejezetenek kvotai erre epulnek (`allow`).
Tarolas: data/users/<munkaterulet>/usage.json, legfeljebb MAX_MONTHS honap.

    with usage.meter("admin", "digest"):
        ...                                  # a blokk CPU-ideje es egy futas konyvelodik
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import workspace
from .locks import file_lock, write_atomic

log = logging.getLogger(__name__)

KINDS = ("digest", "agent", "event_study", "practice", "publish_refresh")
MAX_MONTHS = 24


def _path(ws: str):
    return workspace.user_root(ws) / "usage.json"


def _month(now: datetime | None = None) -> str:
    return f"{(now or datetime.now(timezone.utc)):%Y-%m}"


def load(ws: str = workspace.DEFAULT) -> dict:
    path = _path(ws)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        log.warning("Serult usage.json (%s) - uresen indul.", ws)
        return {}


def record(ws: str, kind: str, cpu_seconds: float, runs: int = 1, now: datetime | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"Ismeretlen meresi fajta: {kind}")
    path = _path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = load(ws)
        month = data.setdefault(_month(now), {})
        row = month.setdefault(kind, {"cpu_seconds": 0.0, "runs": 0})
        row["cpu_seconds"] = round(row["cpu_seconds"] + max(float(cpu_seconds), 0.0), 3)
        row["runs"] += int(runs)
        for old in sorted(data)[:-MAX_MONTHS]:
            data.pop(old, None)
        write_atomic(path, json.dumps(data, indent=1, sort_keys=True))
    return row


def month_summary(ws: str = workspace.DEFAULT, month: str | None = None) -> dict:
    return load(ws).get(month or _month(), {})


def allow(ws: str, kind: str) -> bool:
    """A kvota helye (SAAS-DESIGN 9.). Ma minden futas engedelyezett."""
    return True


@contextmanager
def meter(ws: str, kind: str):
    """A blokk CPU-idejet (process_time) es egy futast konyvel; a meres hibaja nem allitja meg a munkat."""
    start = time.process_time()
    try:
        yield
    finally:
        try:
            record(ws, kind, time.process_time() - start)
        except Exception:
            log.exception("A felhasznalas merese nem sikerult (%s, %s)", ws, kind)
