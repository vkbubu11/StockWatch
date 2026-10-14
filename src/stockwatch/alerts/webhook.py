"""Altalanos "incoming webhook" csatorna: Google Chat, Slack, Discord, Teams-szeru
vegpontok, amelyek egy egyszeru JSON uzenetet varnak.

Beallitas: a webhook URL-t a .env fajlba ird (SOHA ne a settings.yaml-ba,
mert az URL maga a jelszo):

    ALERT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/...

A formatumot az URL-bol talaljuk ki:
  * Discord  -> {"content": ...}   (2000 karakteres korlat)
  * minden mas -> {"text": ...}    (Google Chat, Slack, Mattermost)
"""
from __future__ import annotations

import logging
import time

import requests

from ..config import Settings, webhook_url
from .base import Alert, AlertChannel

log = logging.getLogger(__name__)

TIMEOUT = 15
DISCORD_LIMIT = 2000
DEFAULT_LIMIT = 4000


class WebhookChannel(AlertChannel):
    name = "webhook"

    def __init__(self, settings: Settings, url: str | None = None) -> None:
        self.settings = settings
        self.url = url if url is not None else webhook_url()
        self.enabled = bool(self.url)
        self._last_sent = 0.0

    @property
    def is_discord(self) -> bool:
        return bool(self.url) and ("discord.com" in self.url or "discordapp.com" in self.url)

    def payload(self, alert: Alert) -> dict:
        text = f"{alert.emoji} *{alert.title}*\n```\n{alert.body}\n```"
        if self.is_discord:
            text = text.replace(f"*{alert.title}*", f"**{alert.title}**")
            return {"content": _truncate(text, DISCORD_LIMIT)}
        return {"text": _truncate(text, DEFAULT_LIMIT)}

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False

        minimum = self.settings.alerts.min_seconds_between
        elapsed = time.monotonic() - self._last_sent
        if elapsed < minimum:
            time.sleep(minimum - elapsed)
        self._last_sent = time.monotonic()

        try:
            response = requests.post(self.url, json=self.payload(alert), timeout=TIMEOUT)
            if response.status_code < 300:
                return True
            # Az URL (= titok) nem kerulhet a naploba, csak a statuszkod
            log.error("Webhook hiba: HTTP %s", response.status_code)
        except requests.RequestException as exc:
            log.error("Webhook halozati hiba: %s", type(exc).__name__)
        return False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...(rovidítve)\n```"
