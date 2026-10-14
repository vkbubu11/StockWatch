"""Discord csatorna (webhook): szines kartya (embed), alatta a chart kepe.

A Discordban a webhook egy "bot", ami egy csatornaba ir - token, szerver-
jogosultsag es allando kapcsolat nelkul. A chart kepet MEMORIABOL kuldjuk
(multipart), nem ideiglenes fajlbol.

Beallitas:
  1. Discord: Szerver beallitasok -> Integraciok -> Webhookok -> Uj webhook
     -> csatorna kivalasztasa -> "Webhook URL masolasa"
  2. python cli.py discord      (rejtett bevitel, ellenorzes, probauzenet)
     szerveren:  sudo stockwatch-cli discord

Az URL maga a jelszo: csak a titokfajlba kerul (DISCORD_WEBHOOK_URL), soha
nem a settings.yaml-ba es nem a naploba.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

from ..config import Settings, discord_webhook_url
from .base import Alert, AlertChannel

log = logging.getLogger(__name__)

TIMEOUT = 20
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
MAX_RETRY_WAIT = 30.0
URL_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")

GREEN, RED, AMBER, BLUE, GREY = 0x16875A, 0xC73E3A, 0xB7791F, 0x2D5FA8, 0x66706A
NEGATIVE = {"failed", "heartbeat_warn", "pos_stop", "hold_stop", "broker_stop", "broker_error", "hold_risk", "entry_revoked"}
WATCH = {"entry_setup", "new", "hold_earnings", "pos_timeout", "broker_timeout", "broker_expired", "pos_skipped",
         "broker_skipped"}
POSITIVE = {"heartbeat", "target_hit", "pos_target", "hold_target", "broker_target", "pos_partial", "broker_partial"}


def is_discord_url(url: str | None) -> bool:
    return bool(url) and bool(URL_RE.match(url.strip()))


def color_of(alert: Alert) -> int:
    if alert.event in NEGATIVE:
        return RED
    if alert.event in WATCH:
        return AMBER
    if alert.event in POSITIVE or alert.is_entry:
        return GREEN
    if alert.event == "triggered":
        return GREEN if alert.bullish else RED
    if alert.is_position or alert.event.startswith("broker_") or alert.event == "heartbeat_start":
        return BLUE
    return GREY


class DiscordChannel(AlertChannel):
    name = "discord"

    def __init__(self, settings: Settings, url: str | None = None) -> None:
        self.settings = settings
        self.url = (url if url is not None else discord_webhook_url()) or ""
        self.enabled = is_discord_url(self.url)
        self._last_sent = 0.0
        if self.url and not self.enabled:
            log.error("A DISCORD_WEBHOOK_URL nem ervenyes Discord webhook cim - a csatorna kikapcsolva.")

    def payload(self, alert: Alert, with_image: bool) -> dict:
        body = alert.body.replace("```", "'''")
        description = f"```\n{body}\n```"
        if len(description) > DESCRIPTION_LIMIT:
            description = f"```\n{body[: DESCRIPTION_LIMIT - 40]}\n...(rovidítve)\n```"
        embed = {
            "title": _cut(f"{alert.emoji} {alert.title}", TITLE_LIMIT),
            "description": description,
            "color": color_of(alert),
            "footer": {"text": "StockWatch · Nem befektetési tanács"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if with_image:
            embed["image"] = {"url": "attachment://chart.png"}
        # a jelzes szovege soha ne pingeljen (@everyone, szerepkorok)
        return {"username": "StockWatch", "embeds": [embed], "allowed_mentions": {"parse": []}}

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False
        self._respect_rate_limit()
        image = alert.chart_png if self.settings.alerts.send_chart else None
        payload = self.payload(alert, with_image=bool(image))
        for _ in range(2):                          # 429 eseten egy ujraprobalas
            status, retry_after = self._post(payload, image)
            if status is not None and status < 300:
                return True
            if status != 429:
                return False
            time.sleep(min(retry_after, MAX_RETRY_WAIT))
        return False

    def test(self) -> str | None:
        """A webhook neve, ha az URL el (GET a webhookra - nem kuld uzenetet)."""
        if not self.enabled:
            return None
        try:
            response = requests.get(self.url, timeout=TIMEOUT)
            if response.status_code == 200:
                return response.json().get("name") or "webhook"
            log.error("Discord webhook ellenorzes: HTTP %s", response.status_code)
        except (requests.RequestException, ValueError) as exc:
            log.error("Discord halozati hiba: %s", type(exc).__name__)
        return None

    # -- belso -------------------------------------------------------------
    def _respect_rate_limit(self) -> None:
        minimum = self.settings.alerts.min_seconds_between
        elapsed = time.monotonic() - self._last_sent
        if elapsed < minimum:
            time.sleep(minimum - elapsed)
        self._last_sent = time.monotonic()

    def _post(self, payload: dict, image: bytes | None) -> tuple[int | None, float]:
        url = self.url + "?wait=true"
        try:
            if image:
                response = requests.post(
                    url, data={"payload_json": json.dumps(payload)},
                    files={"files[0]": ("chart.png", image, "image/png")}, timeout=TIMEOUT)
            else:
                response = requests.post(url, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            # a kivetel szovege tartalmazhatja az URL-t (= a titkot): csak a tipusa megy a naploba
            log.error("Discord halozati hiba: %s", type(exc).__name__)
            return None, 0.0
        if response.status_code == 429:
            try:
                retry_after = float(response.json().get("retry_after", 2))
            except ValueError:
                retry_after = 2.0
            log.warning("Discord sebessegkorlat, ujraprobalas %.1f mp mulva", retry_after)
            return 429, retry_after
        if response.status_code >= 300:
            log.error("Discord hiba: HTTP %s %s", response.status_code, response.text[:200])
        return response.status_code, 0.0


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
