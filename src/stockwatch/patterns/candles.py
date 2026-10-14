"""Gyertyaalakzatok - kizarolag MEGEROSITESKENT.

Ezek szandekosan NINCSENEK regisztralva detektorkent. Egy kalapacs onmagaban
nem ad kereskedesi jelzest: naponta tucatnyi keletkezik minden papiron, es a
talalati aranyuk onmagukban alig jobb az eremdobasnal.

Ertekuk akkor van, ha EGYBEESNEK valami massal - egy eros supporttal vagy
egy formacio kitoresevel. A pontozo motor igy hasznalja oket: kis pozitiv
vagy negativ korrekciokent, nem onallo jelzeskent.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class CandleSignal:
    name: str
    direction: str      # "bullish" vagy "bearish"
    strength: float     # 0-1
    date: pd.Timestamp


def _body(row) -> float:
    return abs(row["close"] - row["open"])


def _range(row) -> float:
    return max(row["high"] - row["low"], 1e-9)


def _upper_wick(row) -> float:
    return row["high"] - max(row["open"], row["close"])


def _lower_wick(row) -> float:
    return min(row["open"], row["close"]) - row["low"]


def detect(df: pd.DataFrame, index: int = -1) -> list[CandleSignal]:
    """A megadott gyertyan felismerheto alakzatok.

    Alapertelmezesben az utolso gyertyat vizsgalja.
    """
    if len(df) < 3:
        return []

    position = index if index >= 0 else len(df) + index
    if position < 1 or position >= len(df):
        return []

    current = df.iloc[position]
    previous = df.iloc[position - 1]
    date = df.index[position]

    signals: list[CandleSignal] = []
    body = _body(current)
    total_range = _range(current)
    body_ratio = body / total_range

    # --- Kalapacs: hosszu also arnyek, kicsi test a teteje kozeleben ---
    if _lower_wick(current) >= 2.0 * body and _upper_wick(current) <= body * 0.6 and body_ratio < 0.4:
        signals.append(CandleSignal("Kalapacs", "bullish", min(_lower_wick(current) / total_range, 1.0), date))

    # --- Hullocsillag: a kalapacs tukorkepe ---
    if _upper_wick(current) >= 2.0 * body and _lower_wick(current) <= body * 0.6 and body_ratio < 0.4:
        signals.append(CandleSignal("Hullocsillag", "bearish", min(_upper_wick(current) / total_range, 1.0), date))

    # --- Bekebelezo (engulfing): a mai test teljesen lefedi a tegnapit ---
    previous_body = _body(previous)
    if body > previous_body * 1.1 and previous_body > 0:
        bullish_engulf = (
            current["close"] > current["open"]
            and previous["close"] < previous["open"]
            and current["close"] >= previous["open"]
            and current["open"] <= previous["close"]
        )
        bearish_engulf = (
            current["close"] < current["open"]
            and previous["close"] > previous["open"]
            and current["close"] <= previous["open"]
            and current["open"] >= previous["close"]
        )
        strength = min(body / max(previous_body, 1e-9) / 2.0, 1.0)
        if bullish_engulf:
            signals.append(CandleSignal("Bullish bekebelezo", "bullish", strength, date))
        if bearish_engulf:
            signals.append(CandleSignal("Bearish bekebelezo", "bearish", strength, date))

    # --- Doji: gyakorlatilag nincs test - bizonytalansag, iranyfuggetlen ---
    if body_ratio < 0.08:
        direction = "bullish" if current["close"] >= previous["close"] else "bearish"
        signals.append(CandleSignal("Doji", direction, 0.3, date))

    return signals


def confirmation_score(df: pd.DataFrame, direction: str, index: int = -1) -> float:
    """A gyertyaalakzatok osszesitett tamogatasa egy iranyra, -1..+1.

    A visszaadott ertek szandekosan kicsi (max +-0.5 korul), mert ez csak
    finomhangolas a fo jelzes mellett.
    """
    signals = detect(df, index)
    if not signals:
        return 0.0

    score = 0.0
    for signal in signals:
        contribution = signal.strength * 0.5
        score += contribution if signal.direction == direction else -contribution

    return float(max(-1.0, min(1.0, score)))
