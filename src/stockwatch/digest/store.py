"""Reggeli osszefoglalo - archivum: data/users/<munkaterulet>/digest/<kereskedesi nap>.json.

Egy fajl egy nap (pillanatkep + kivalasztott esemenyek, ~20-60 KB), `keep_days` napig marad
(a housekeeping torli a regebbit).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from .. import workspace
from ..locks import write_atomic

log = logging.getLogger(__name__)

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def directory(ws: str = workspace.DEFAULT):
    return workspace.user_dir(ws, "digest")


def _path(day: str, ws: str):
    if not DAY_RE.match(day or ""):
        raise ValueError(f"Ervenytelen nap: {day!r}")
    return directory(ws) / f"{day}.json"


def save(record: dict, ws: str = workspace.DEFAULT) -> None:
    path = _path(record["for_day"], ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, ensure_ascii=False, indent=1, default=str))


def load(day: str, ws: str = workspace.DEFAULT) -> dict | None:
    path = _path(day, ws)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        log.warning("Serult osszefoglalo: %s", path)
        return None


def days(ws: str = workspace.DEFAULT) -> list[str]:
    folder = directory(ws)
    if not folder.exists():
        return []
    return sorted((p.stem for p in folder.glob("*.json") if DAY_RE.match(p.stem)), reverse=True)


def latest(ws: str = workspace.DEFAULT, before: str | None = None) -> dict | None:
    """A legutobbi osszefoglalo (`before` napnal korabbi, ha meg van adva)."""
    for day in days(ws):
        if before is None or day < before:
            record = load(day, ws)
            if record is not None:
                return record
    return None


def prune(keep_days: int, ws: str = workspace.DEFAULT, today: date | None = None) -> int:
    limit = ((today or date.today()) - timedelta(days=keep_days)).isoformat()
    removed = 0
    for day in days(ws):
        if day < limit:
            _path(day, ws).unlink(missing_ok=True)
            removed += 1
    return removed


def usage_bytes() -> int:
    """Minden munkaterulet osszefoglaloi (a data-usage sorahoz)."""
    root = workspace.USERS_DIR
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.glob("*/digest/*.json"))
