"""Nyitott pozicio kezelese gyertyarol gyertyara - EGY helyen.

Ugyanez a logika fut a kutatasban (entry_research) es az elo pozicio-
kovetesben (positions): ha a backtest azt mondja, hogy "+1R-nel a fele el,
a stop a belepore", akkor az elo ertesites pontosan ugyanakkor szol.

Kilepesi modok:
  target        teljes pozicio a celig
  partial       fele +`partial_r` R-nel, stop a belepore, a masik fele a celig
  trail         fix cel nelkul, kovetett stop: legmagasabb ar - `trail_atr` ATR
  partial_trail fele +1R-nel, stop a belepore, a maradek kovetett stoppal

Pesszimista szabalyok (mint a backtestben):
  * egy gyertyan eloszor a stopot nezzuk, csak utana a celt
  * gapnel a rosszabb nyitoaron toltunk
  * ha a reszleges profit napjan a belepo ala is lement: a masik fele nullszaldo
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

EXIT_MODES = ("target", "partial", "trail", "partial_trail")


@dataclass
class PositionState:
    entry: float
    stop: float                    # AKTUALIS stop (mozoghat)
    initial_stop: float
    target: float
    atr: float
    mode: str = "partial"
    partial_r: float = 1.0
    trail_atr: float = 3.0
    remaining: float = 1.0         # a pozicio hanyad resze van meg nyitva
    realised_r: float = 0.0
    highest: float = 0.0
    bars_held: int = 0
    closed: bool = False
    reason: str = ""
    partial_done: bool = False
    events: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in EXIT_MODES:
            raise ValueError(f"Ismeretlen kilepesi mod: {self.mode}")
        self.highest = self.highest or self.entry

    @property
    def risk(self) -> float:
        return self.entry - self.initial_stop

    @property
    def trailing(self) -> bool:
        return self.mode in ("trail", "partial_trail")

    @property
    def uses_partial(self) -> bool:
        return self.mode in ("partial", "partial_trail")

    @property
    def partial_price(self) -> float:
        return self.entry + self.partial_r * self.risk

    def to_dict(self) -> dict:
        return asdict(self)

    # ------------------------------------------------------------------
    def _close(self, price: float, reason: str) -> None:
        self.realised_r += self.remaining * (price - self.entry) / self.risk
        self.remaining = 0.0
        self.closed = True
        self.reason = reason

    def step(self, o: float, h: float, l: float, first: bool = False) -> list[str]:
        """Egy lezart napi gyertya feldolgozasa. Visszaadja az esemenyeket:
        "partial", "stop", "breakeven", "trail", "target"."""
        if self.closed or self.risk <= 0:
            return []
        self.bars_held += 1
        events: list[str] = []

        # 1. stop (a belepesi napon a nyito biztosan a stop felett volt)
        if l <= self.stop:
            fill = self.stop if first else min(o, self.stop)
            if self.uses_partial and self.stop >= self.entry:
                reason = "breakeven"
            elif self.trailing and self.stop > self.initial_stop:
                reason = "trail"
            else:
                reason = "stop"
            self._close(fill, reason)
            events.append(reason)
            self.events += events
            return events

        # 2. reszleges profit
        if self.uses_partial and not self.partial_done and h >= self.partial_price:
            fill = self.partial_price if first else max(o, self.partial_price)
            self.realised_r += 0.5 * (fill - self.entry) / self.risk
            self.remaining = 0.5
            self.partial_done = True
            self.stop = max(self.stop, self.entry)
            events.append("partial")
            if l <= self.entry:                  # pesszimista: ugyanazon a napon vissza
                self._close(self.entry, "breakeven")
                events.append("breakeven")
                self.events += events
                return events

        # 3. fix cel
        if not self.trailing and h >= self.target:
            fill = self.target if first else max(o, self.target)
            self._close(fill, "target")
            events.append("target")
            self.events += events
            return events

        # 4. kovetett stop a gyertya VEGEN frissul (a kovetkezo naptol el)
        self.highest = max(self.highest, h)
        if self.trailing:
            self.stop = max(self.stop, self.highest - self.trail_atr * self.atr)

        self.events += events
        return events

    def timeout(self, close: float) -> None:
        if not self.closed:
            self._close(close, "timeout")
            self.events.append("timeout")


def max_hold(horizon: int, mode: str) -> int:
    """Kovetett stopnal tobb ido kell a futashoz - dupla horizont."""
    return max(horizon, 1) * (2 if mode in ("trail", "partial_trail") else 1)
