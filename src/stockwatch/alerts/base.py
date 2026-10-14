"""Riasztasi csatornak kozos interfesze."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from ..forecast import Forecast


@dataclass
class Alert:
    """Egy kikuldendo ertesites."""

    symbol: str
    title: str
    body: str
    forecast: Forecast | None   # None: broker-esemeny elemzes nelkul
    event: str                 # "new", "triggered", "target_hit", "failed", "entry_*"
    chart_png: bytes | None = None
    extra: dict | None = None  # pl. a belepesi jelzes teljes tartalma (JSONL-be)
    strategy: str | None = None  # melyik strategia (config.STRATEGY_IDS); None: nem strategia-esemeny

    @property
    def is_entry(self) -> bool:
        return self.event.startswith("entry_")

    @property
    def is_position(self) -> bool:
        return self.event.startswith("pos_")

    @property
    def is_holding(self) -> bool:
        return self.event.startswith("hold_")

    @property
    def bullish(self) -> bool:
        return (self.is_entry or self.is_position
                or (self.forecast is not None and self.forecast.direction == "bullish"))

    @property
    def emoji(self) -> str:
        return {
            "new": "🔍",
            "triggered": "🚀" if self.bullish else "🔻",
            "target_hit": "🎯",
            "failed": "❌",
            "entry_breakout": "🟢",
            "entry_retest": "🟢",
            "entry_setup": "👀",
            "entry_revoked": "↩️",
            "entry_pullback": "🔵",
            "pos_exit_signal": "🔔",
            "pos_exit": "🏁",
            "broker_exit_signal": "🔔",
            "broker_exit": "🏁",
            "strategy_switch": "🔀",
            "pos_entered": "📥",
            "pos_skipped": "⏭️",
            "pos_partial": "💰",
            "pos_breakeven": "⚖️",
            "pos_stop": "🛑",
            "pos_trail": "🏁",
            "pos_target": "🎯",
            "pos_timeout": "⏰",
            "hold_stop": "🛑",
            "hold_target": "🎯",
            "hold_risk": "⚠️",
            "hold_earnings": "📅",
            "broker_submitted": "📤",
            "broker_filled": "📥",
            "broker_partial": "💰",
            "broker_breakeven": "⚖️",
            "broker_stop": "🛑",
            "broker_target": "🎯",
            "broker_timeout": "⏰",
            "broker_expired": "⏭️",
            "broker_closed": "🏁",
            "broker_skipped": "🚫",
            "broker_error": "❗",
            "heartbeat": "💓",
            "heartbeat_warn": "⚠️",
            "heartbeat_start": "🔄",
        }.get(self.event, "📊")


class AlertChannel(ABC):
    """Egy kimeneti csatorna (konzol, fajl, Telegram)."""

    name: str = "base"
    enabled: bool = True

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        """Igaz, ha sikerult kikuldeni."""


def format_body(forecast: Forecast, event: str) -> str:
    """Egyseges, ember altal olvashato jelzesszoveg.

    Minden csatorna ezt hasznalja, igy a Telegram uzenet es a konzolkimenet
    nem csuszhat szet.
    """
    lines: list[str] = []
    match = forecast.pattern

    if match is not None:
        lines.append(f"Formacio: {match.name} ({match.direction}), minoseg {match.quality:.0%}")
        lines.append(f"Trigger szint: {match.trigger_level:.2f}")
        lines.append(f"Celar: {match.target:.2f} ({match.target_pct:+.1f}%)")
        lines.append(f"Invalidacio (stop): {match.invalidation:.2f}")
        lines.append(f"Hozam/kockazat: {match.risk_reward:.2f}")

    lines.append(f"Aktualis ar: {forecast.price:.2f}")
    lines.append(f"Iranyjelzes: {forecast.label} ({forecast.score:+.2f})")
    lines.append(f"Historikus talalati arany: {forecast.probability_text}")

    if forecast.expected_bars:
        lines.append(f"Varhato horizont: ~{forecast.expected_bars} kereskedesi nap")

    if forecast.support:
        lines.append(
            f"Legkozelebbi support: {forecast.support.price:.2f} "
            f"({forecast.support.distance_pct:+.1f}%, {forecast.support.score:.0f} pont)"
        )
    if forecast.resistance:
        lines.append(
            f"Legkozelebbi rezisztencia: {forecast.resistance.price:.2f} "
            f"({forecast.resistance.distance_pct:+.1f}%, {forecast.resistance.score:.0f} pont)"
        )

    lines.append("")
    lines.append("Indoklas:")
    for component in forecast.components:
        if component.reason:
            lines.append(f"  - {component.name}: {component.score:+.2f} ({component.reason})")

    for warning in forecast.warnings:
        lines.append(f"FIGYELEM: {warning}")

    lines.append("")
    lines.append("Nem befektetesi tanacs.")
    return "\n".join(lines)


def format_title(forecast: Forecast, event: str) -> str:
    labels = {
        "new": "uj formacio",
        "triggered": "KITORES",
        "target_hit": "celar elerve",
        "failed": "minta megbukott",
    }
    name = forecast.pattern.name if forecast.pattern else forecast.label
    return f"{forecast.symbol}: {name} - {labels.get(event, event)}"
