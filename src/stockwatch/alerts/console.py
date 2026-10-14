"""Konzolra irt riasztas, ANSI szinekkel.

WINDOWS KODOLAS
---------------
A Windows konzol alapertelmezesben nem UTF-8 (magyar rendszeren cp1250),
es egy emoji kiirasa UnicodeEncodeError-ral elszall. Ez elo hibakent jott
elo: a monitor elso eles futasan minden konzol-riasztas elbukott.

Ket vedelem van beepitve:
  1. indulaskor megprobaljuk UTF-8-ra allitani a kimenetet,
  2. ha ez nem megy, ASCII jelolesekre valtunk ([+], [-], [!]).

Igy a jelzes barmilyen konzolon olvashato marad.
"""
from __future__ import annotations

import sys

from .base import Alert, AlertChannel

GREEN, RED, YELLOW, BOLD, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[0m"

# Az emoji ASCII megfeleloi, ha a konzol nem birja
ASCII_MARKERS = {
    "new": "[*]",
    "triggered": "[!]",
    "target_hit": "[+]",
    "failed": "[-]",
    "entry_breakout": "[LONG]",
    "entry_retest": "[LONG]",
    "entry_setup": "[FIGYELD]",
    "pos_entered": "[POZ+]",
    "pos_skipped": "[POZ-]",
    "pos_partial": "[FELE EL]",
    "pos_breakeven": "[0R]",
    "pos_stop": "[STOP]",
    "pos_trail": "[TRAIL]",
    "pos_target": "[CEL]",
    "pos_timeout": "[IDO]",
}


def _supports_unicode() -> bool:
    """Ki tudja-e irni a konzol a hasznalt jeleket."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "🚀🔻🎯❌🔍🟢👀📥💰🛑⏰⚖️⏭️🏁".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _try_enable_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


class ConsoleChannel(AlertChannel):
    name = "console"

    def __init__(self, colour: bool = True) -> None:
        self.colour = colour
        if not _supports_unicode():
            _try_enable_utf8()
        self.unicode_ok = _supports_unicode()

    def send(self, alert: Alert) -> bool:
        if alert.is_entry or alert.is_position:
            direction = "bullish"
        else:
            direction = alert.forecast.direction if alert.forecast is not None else "neutral"
        tint = GREEN if direction == "bullish" else RED if direction == "bearish" else YELLOW

        marker = alert.emoji if self.unicode_ok else ASCII_MARKERS.get(alert.event, "[*]")
        header = f"{marker} {alert.title}"
        if self.colour:
            header = f"{BOLD}{tint}{header}{RESET}"

        body = alert.body if self.unicode_ok else alert.body.encode("ascii", "replace").decode("ascii")

        print()
        print("=" * 70)
        print(header)
        print("-" * 70)
        print(body)
        print("=" * 70)
        return True
