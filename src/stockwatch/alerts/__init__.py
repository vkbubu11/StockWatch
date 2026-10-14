"""Riasztasi csatornak osszefogasa."""
from __future__ import annotations

import dataclasses
import logging
from collections import Counter
from datetime import datetime

from ..config import STRATEGY_LABELS, Settings
from .base import Alert, AlertChannel, format_body, format_title
from .console import ConsoleChannel
from .discord import DiscordChannel
from .jsonl import JsonlChannel
from .telegram import TelegramChannel
from .webhook import WebhookChannel

log = logging.getLogger(__name__)

__all__ = ["Alert", "AlertChannel", "Dispatcher", "format_body", "format_title"]


class Dispatcher:
    """Egy jelzest az osszes bekapcsolt csatornara kikuld.

    Egy csatorna hibaja nem akaszthatja meg a tobbit: ha a Telegram elerhetetlen,
    a jelzes akkor is bekerul a naploba.
    """

    def __init__(self, settings: Settings, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.channels: list[AlertChannel] = []
        self.sent: Counter[str] = Counter()

        cfg = settings.alerts
        if cfg.console:
            self.channels.append(ConsoleChannel())
        if cfg.jsonl:
            self.channels.append(JsonlChannel(settings))
        if cfg.telegram:
            channel = TelegramChannel(settings)
            if channel.enabled:
                self.channels.append(channel)
        discord = DiscordChannel(settings) if cfg.discord else None
        if discord is not None and discord.enabled:
            self.channels.append(discord)
        if cfg.webhook:
            hook = WebhookChannel(settings)
            # Discord cimet a sajat csatornaja visz - ne menjen ki ketszer
            if hook.enabled and not (discord is not None and hook.url == discord.url):
                self.channels.append(hook)

    def dispatch(self, alert: Alert) -> dict[str, bool]:
        self.sent[alert.event] += 1          # a napi eletjelhez (heartbeat.py)
        alert = self._labelled(alert)
        if self._in_quiet_hours() or not self._strategy_alerts(alert):
            log.info("Csendes idoszak / a strategia ertesitese kikapcsolva - csak naplozva: %s", alert.title)
            return {c.name: c.send(alert) for c in self.channels if c.name == "jsonl"}
        return self._send(alert, self.channels)

    @staticmethod
    def _labelled(alert: Alert) -> Alert:
        """A strategia neve a cim elejere: "[Visszahuzas (RSI2)] AAPL: ..." - igy latszik, melyik kuldte."""
        if not alert.strategy:
            return alert
        prefix = f"[{STRATEGY_LABELS.get(alert.strategy, alert.strategy)}] "
        return alert if alert.title.startswith(prefix) else dataclasses.replace(alert, title=prefix + alert.title)

    def _strategy_alerts(self, alert: Alert) -> bool:
        """Kuldhet-e a strategia Discordra / Telegramra (a homokozo alapbol nem). A naploba mindig kerul."""
        strategies = getattr(self.settings, "strategies", None)
        switch = getattr(strategies, alert.strategy, None) if (strategies and alert.strategy) else None
        return switch is None or bool(switch.alerts)

    def broadcast(self, alert: Alert) -> dict[str, bool]:
        """Rendszeruzenet (eletjel): minden csatornara a jelzesnaplo (jsonl) kivetelevel,
        a csendes idoszaktol fuggetlenul - nem kereskedesi jelzes, nem kerulhet a statisztikaba."""
        return self._send(alert, [c for c in self.channels if c.name != "jsonl"])

    def _send(self, alert: Alert, channels: list[AlertChannel]) -> dict[str, bool]:
        if self.dry_run:
            log.info("[DRY-RUN] Nem kuldunk ki semmit: %s", alert.title)
            return {}

        results = {}
        for channel in channels:
            try:
                results[channel.name] = channel.send(alert)
            except Exception:
                log.exception("A(z) %s csatorna hibara futott", channel.name)
                results[channel.name] = False
        return results

    def _in_quiet_hours(self) -> bool:
        """Pl. "22:00-07:00" - ejfelen atnyulo intervallumot is kezel."""
        now = datetime.now().time()
        for window in self.settings.alerts.quiet_hours:
            try:
                start_text, end_text = window.split("-")
                start = datetime.strptime(start_text.strip(), "%H:%M").time()
                end = datetime.strptime(end_text.strip(), "%H:%M").time()
            except ValueError:
                log.warning("Ertelmezhetetlen csendes idoszak: %s", window)
                continue
            if start <= end:
                if start <= now <= end:
                    return True
            elif now >= start or now <= end:
                return True
        return False
