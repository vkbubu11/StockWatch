"""Telegram bot csatorna.

A chart kepet MEMORIABOL kuldjuk (a plotly `to_image` bajtjaibol), nem
ideiglenes fajlbol - igy hiaba fut a monitor honapokig, nem hagy maga utan
szemetet a lemezen.

Beallitas:
  1. Hozz letre egy botot a @BotFather-rel -> kapsz egy tokent
  2. Ird meg a botodnak egy uzenetet, majd nyisd meg:
     https://api.telegram.org/bot<TOKEN>/getUpdates  -> innen a chat id
  3. Ird be mindkettot a .env fajlba
"""
from __future__ import annotations

import logging
import time

import requests

from ..config import Settings, telegram_credentials
from .base import Alert, AlertChannel

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20
CAPTION_LIMIT = 1024       # a Telegram kepaláírás-korlatja
MESSAGE_LIMIT = 4096


class TelegramChannel(AlertChannel):
    name = "telegram"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token, self.chat_id = telegram_credentials()
        self.enabled = bool(self.token and self.chat_id)
        self._last_sent = 0.0

        if not self.enabled:
            log.warning(
                "A Telegram csatorna kikapcsolva: hianyzik a TELEGRAM_BOT_TOKEN "
                "vagy a TELEGRAM_CHAT_ID a .env fajlbol."
            )

    def send(self, alert: Alert) -> bool:
        if not self.enabled:
            return False

        self._respect_rate_limit()

        text = f"{alert.emoji} <b>{_escape(alert.title)}</b>\n\n<pre>{_escape(alert.body)}</pre>"

        if alert.chart_png and self.settings.alerts.send_chart:
            if len(text) <= CAPTION_LIMIT:
                return self._send_photo(alert.chart_png, text)
            # A belepesi jelzes hosszabb a kepalairas-korlatnal: a kep a cimmel
            # megy, a teljes szoveg kulon uzenetben - igy semmi nem vagodik le.
            caption = f"{alert.emoji} <b>{_escape(alert.title)}</b>"
            photo_ok = self._send_photo(alert.chart_png, _truncate_plain(caption, CAPTION_LIMIT))
            self._respect_rate_limit()
            return self._send_message(_truncate(text, MESSAGE_LIMIT)) and photo_ok
        return self._send_message(_truncate(text, MESSAGE_LIMIT))

    # -- belso -------------------------------------------------------------
    def _respect_rate_limit(self) -> None:
        minimum = self.settings.alerts.min_seconds_between
        elapsed = time.monotonic() - self._last_sent
        if elapsed < minimum:
            time.sleep(minimum - elapsed)
        self._last_sent = time.monotonic()

    def _send_message(self, text: str) -> bool:
        return self._post(
            "sendMessage",
            data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
        )

    def _send_photo(self, image: bytes, caption: str) -> bool:
        return self._post(
            "sendPhoto",
            data={"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("chart.png", image, "image/png")},
        )

    def _post(self, method: str, data: dict, files: dict | None = None) -> bool:
        url = API_BASE.format(token=self.token, method=method)
        try:
            response = requests.post(url, data=data, files=files, timeout=TIMEOUT)
            if response.status_code == 200:
                return True
            # A token sosem kerulhet a naplóba
            log.error("Telegram hiba (%s): %s", response.status_code, response.text[:200])
        except requests.RequestException as exc:
            log.error("Telegram halozati hiba: %s", self._redact(exc))
        return False

    def _redact(self, value: object) -> str:
        """A requests hibauzenete tartalmazza az URL-t - benne a tokennel."""
        text = str(value)
        return text.replace(self.token, "***") if self.token else text

    def test(self) -> bool:
        """Kapcsolat ellenorzese a getMe vegponttal."""
        if not self.enabled:
            log.error("Nincs beallitva Telegram token vagy chat id.")
            return False
        url = API_BASE.format(token=self.token, method="getMe")
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code == 200:
                name = response.json().get("result", {}).get("username", "?")
                log.info("Telegram kapcsolat rendben, bot: @%s", name)
                return True
            log.error("Telegram getMe hiba: %s", response.status_code)
        except requests.RequestException as exc:
            log.error("Telegram halozati hiba: %s", self._redact(exc))
        return False


def _truncate_plain(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 20] + "\n...(rovidítve)</pre>"
