"""Jelzesek naplozasa rotalo JSONL fajlba.

Ez a csatorna mindig aktiv: ez adja a dashboard elozmeny-fulet es a kesobbi
utolagos ellenorzest ("mit jelzett a rendszer, es mi lett belole").
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import housekeeping
from ..config import Settings
from .base import Alert, AlertChannel


class JsonlChannel(AlertChannel):
    name = "jsonl"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, alert: Alert) -> bool:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": alert.event,
            "symbol": alert.symbol,
            "title": alert.title,
            **(alert.forecast.to_dict() if alert.forecast is not None else {}),
        }
        if alert.strategy:
            record["strategy"] = alert.strategy
        if alert.extra:
            record["entry"] = alert.extra
        housekeeping.append_jsonl(
            self.settings.signals_path,
            record,
            self.settings.storage.signals_max_mb,
            self.settings.storage.signals_backups,
        )
        return True
