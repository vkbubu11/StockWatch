"""Formaciofelismero reteg.

A detektorok importalaskor regisztraljak magukat, a `detect()` pedig
mindegyiket lefuttatja egyetlen, kozosen szamolt kontextuson.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..config import PatternSettings, StructureSettings
from ..structure import alternating, find_zigzag_pivots
from .base import Context, Detector, PatternMatch, all_detectors, register

# Az importok regisztraljak a detektorokat - a sorrend nem szamit.
from . import candles, continuation, reversal  # noqa: E402,F401

log = logging.getLogger(__name__)

__all__ = [
    "Context",
    "Detector",
    "PatternMatch",
    "all_detectors",
    "build_context",
    "detect",
    "register",
]


def build_context(
    symbol: str,
    df: pd.DataFrame,
    patterns_cfg: PatternSettings,
    structure_cfg: StructureSettings,
    confirmed_only: bool = False,
) -> Context:
    """Egyszer szamolja ki a pivotokat, amit aztan minden detektor hasznal.

    A `confirmed_only` a backtest kapcsoloja: ott a meg meg nem erositett
    utolso pivot hasznalata look-ahead szivargast okozna.
    """
    pivots = find_zigzag_pivots(df, structure_cfg.zigzag_atr_mult, structure_cfg.atr_period)
    if confirmed_only:
        pivots = [p for p in pivots if p.confirmed]
    return Context(symbol=symbol, df=df, pivots=alternating(pivots), cfg=patterns_cfg)


def detect(
    symbol: str,
    df: pd.DataFrame,
    patterns_cfg: PatternSettings,
    structure_cfg: StructureSettings,
    confirmed_only: bool = False,
) -> list[PatternMatch]:
    """Minden regisztralt detektor futtatasa, minoseg szerint rendezve.

    Egy detektor hibaja nem akaszthatja meg a tobbit - egy elszalt
    haromszog-kereses miatt ne maradjon el a fej-vall jelzes.
    """
    if df is None or len(df) < 30:
        return []

    ctx = build_context(symbol, df, patterns_cfg, structure_cfg, confirmed_only)
    if len(ctx.pivots) < 3:
        return []

    matches: list[PatternMatch] = []
    for detector in all_detectors():
        try:
            matches.extend(detector(ctx))
        except Exception:
            log.exception("A(z) %s detektor hibara futott (%s)", detector.__name__, symbol)

    matches.sort(key=lambda m: m.quality, reverse=True)
    return matches


def strongest(matches: list[PatternMatch]) -> PatternMatch | None:
    """A legmegbizhatobb aktiv minta.

    Az aktivalt (triggered) minta mindig elonyt elvez a meg formalodoval
    szemben, mert ott mar tortent tenyleges arfolyam-esemeny.
    """
    if not matches:
        return None
    active = [m for m in matches if m.status in ("forming", "triggered")]
    if not active:
        return None
    return max(active, key=lambda m: (m.status == "triggered", m.quality))
