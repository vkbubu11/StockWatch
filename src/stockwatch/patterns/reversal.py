"""Fordulos formaciok: dupla/tripla alj es teto, fej-vall es forditottja.

Ezek a minták trendvegen jelennek meg, es iranyvaltast jeleznek. Mindegyik
ugyanarra a logikara epul: egy adott pivot-mintazatot keresunk, kiszamoljuk
a nyakvonalat, es a nyakvonal attorese a belepesi jelzes.

A celar mindenutt a klasszikus "measured move": a formacio magassagat
vetitjuk le a kitoresi pontbol.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..structure import Pivot
from .base import (
    Context,
    PatternMatch,
    breakout_index_line,
    duration_score,
    pct_diff,
    prior_move_atr,
    recent_enough,
    register,
    resolve_status,
    similar,
    symmetry_score,
    volume_confirmation,
    volume_dryup,
)


def _line_through(p1: Pivot, p2: Pivot, length: int) -> np.ndarray:
    """Ket pivoton atmeno egyenes ertekei minden gyertyara."""
    if p2.index == p1.index:
        return np.full(length, p1.price, dtype="float64")
    slope = (p2.price - p1.price) / (p2.index - p1.index)
    intercept = p1.price - slope * p1.index
    return slope * np.arange(length, dtype="float64") + intercept


def _windows(pivots: list[Pivot], size: int):
    """Csuszoablak a pivotsorozaton, a legfrissebbel kezdve.

    A legfrissebb minta a legfontosabb, ezert visszafele haladunk - igy az
    elso talalat mindig a legaktualisabb.
    """
    for start in range(len(pivots) - size, -1, -1):
        yield pivots[start : start + size]


# ---------------------------------------------------------------------------
# Dupla alj / dupla teto
# ---------------------------------------------------------------------------
@register
def double_bottom(ctx: Context) -> list[PatternMatch]:
    """L - H - L mintazat, ahol a ket alj gyakorlatilag azonos szinten van.

    Bullish fordulos minta: a piac ketszer probalta attorni ugyanazt a
    szintet lefele, es mindketszer visszapattant.
    """
    return _double(ctx, bottom=True)


@register
def double_top(ctx: Context) -> list[PatternMatch]:
    """H - L - H mintazat: ketszer elakadt az ar ugyanannal a szintnel."""
    return _double(ctx, bottom=False)


def _double(ctx: Context, bottom: bool) -> list[PatternMatch]:
    matches: list[PatternMatch] = []
    want = "L" if bottom else "H"

    for window in _windows(ctx.pivots, 3):
        first, middle, second = window
        if first.kind != want or second.kind != want:
            continue
        if not recent_enough(ctx, second.index):
            continue

        span = second.index - first.index
        if not (ctx.cfg.min_pattern_bars <= span <= ctx.cfg.max_pattern_bars):
            continue

        # FORDULOS minta: csak akkor ervenyes, ha volt mit megforditani.
        # Enelkul a veletlen zajban talalt minden "dupla alj" atmenne.
        prior = prior_move_atr(ctx, first.index)
        if bottom and prior > -2.5:
            continue
        if not bottom and prior < 2.5:
            continue

        # A ket szelsoertek azonos szinten van-e.
        # A turés a szorosabb a ket kriterium kozul: ATR-arany VAGY 2.5
        # szazalek - kulonben volatilis papiron barmi "azonos szintnek" latszik.
        tolerance = min(ctx.tolerance(second.index), second.price * 0.025)
        if not similar(first.price, second.price, tolerance):
            continue

        neckline = middle.price
        base_price = (first.price + second.price) / 2
        height = abs(neckline - base_price)

        # A kozbenso visszapattanas legyen erdemi, kulonben csak zaj
        if height < 2.0 * ctx.atr_at(middle.index) or pct_diff(neckline, base_price) < 3.0:
            continue

        direction = "bullish" if bottom else "bearish"
        target = neckline + height if bottom else neckline - height
        invalidation = min(first.price, second.price) if bottom else max(first.price, second.price)

        status, trigger_date = resolve_status(ctx, second.index, neckline, direction)

        quality = _quality(
            ctx,
            symmetry=symmetry_score(
                abs(neckline - first.price), abs(neckline - second.price)
            ),
            duration=duration_score(span, 15, 90),
            start=first.index,
            end=second.index,
            trigger_index=ctx.df.index.get_loc(trigger_date) if trigger_date is not None else None,
            height_atr=height / ctx.atr_at(second.index),
        )
        if quality < ctx.cfg.min_quality:
            continue

        name = "Dupla alj" if bottom else "Dupla teto"
        matches.append(
            PatternMatch(
                symbol=ctx.symbol,
                name=name,
                direction=direction,
                status=status,
                quality=quality,
                trigger_level=neckline,
                target=target,
                invalidation=invalidation,
                start_date=first.date,
                end_date=second.date,
                trigger_date=trigger_date,
                key_points=[(p.date, p.price) for p in window],
                notes=f"Nyakvonal {neckline:.2f}, a formacio magassaga {height:.2f}",
                extras={"neckline": neckline, "kind": "double"},
            )
        )
        break  # csak a legfrissebb dupla mintat jelentjuk papironkent

    return matches


# ---------------------------------------------------------------------------
# Tripla alj / tripla teto
# ---------------------------------------------------------------------------
@register
def triple_bottom(ctx: Context) -> list[PatternMatch]:
    return _triple(ctx, bottom=True)


@register
def triple_top(ctx: Context) -> list[PatternMatch]:
    return _triple(ctx, bottom=False)


def _triple(ctx: Context, bottom: bool) -> list[PatternMatch]:
    """L-H-L-H-L (vagy forditva): harom azonos szintu szelsoertek.

    Erosebb jelzes, mint a dupla valtozat - a szint haromszor tartott.
    """
    matches: list[PatternMatch] = []
    want = "L" if bottom else "H"

    for window in _windows(ctx.pivots, 5):
        extremes = [window[0], window[2], window[4]]
        middles = [window[1], window[3]]

        if any(p.kind != want for p in extremes):
            continue
        if not recent_enough(ctx, extremes[-1].index):
            continue

        span = extremes[-1].index - extremes[0].index
        if not (ctx.cfg.min_pattern_bars <= span <= ctx.cfg.max_pattern_bars):
            continue

        prior = prior_move_atr(ctx, extremes[0].index)
        if bottom and prior > -2.5:
            continue
        if not bottom and prior < 2.5:
            continue

        tolerance = min(ctx.tolerance(extremes[-1].index), extremes[-1].price * 0.025)
        reference = extremes[0].price
        if not all(similar(p.price, reference, tolerance) for p in extremes[1:]):
            continue

        # A nyakvonal a ket kozbenso fordulopont kozul a szelsosegesebb
        neckline = max(p.price for p in middles) if bottom else min(p.price for p in middles)
        base_price = sum(p.price for p in extremes) / 3
        height = abs(neckline - base_price)

        if height < 2.0 * ctx.atr_at(extremes[-1].index):
            continue

        direction = "bullish" if bottom else "bearish"
        target = neckline + height if bottom else neckline - height
        invalidation = (
            min(p.price for p in extremes) if bottom else max(p.price for p in extremes)
        )

        status, trigger_date = resolve_status(ctx, extremes[-1].index, neckline, direction)

        prices = [p.price for p in extremes]
        quality = _quality(
            ctx,
            symmetry=symmetry_score(
                min(prices) if bottom else max(prices),
                max(prices) if bottom else min(prices),
            ),
            duration=duration_score(span, 25, 120),
            start=extremes[0].index,
            end=extremes[-1].index,
            trigger_index=ctx.df.index.get_loc(trigger_date) if trigger_date is not None else None,
            height_atr=height / ctx.atr_at(extremes[-1].index),
        )
        if quality < ctx.cfg.min_quality:
            continue

        name = "Tripla alj" if bottom else "Tripla teto"
        matches.append(
            PatternMatch(
                symbol=ctx.symbol,
                name=name,
                direction=direction,
                status=status,
                quality=quality,
                trigger_level=neckline,
                target=target,
                invalidation=invalidation,
                start_date=extremes[0].date,
                end_date=extremes[-1].date,
                trigger_date=trigger_date,
                key_points=[(p.date, p.price) for p in window],
                notes=f"Harom teszt {reference:.2f} korul, nyakvonal {neckline:.2f}",
                extras={"neckline": neckline, "kind": "triple"},
            )
        )
        break

    return matches


# ---------------------------------------------------------------------------
# Fej-vall es forditott fej-vall
# ---------------------------------------------------------------------------
@register
def head_and_shoulders(ctx: Context) -> list[PatternMatch]:
    """H-L-H-L-H, ahol a kozepso csucs (a fej) a legmagasabb.

    A legismertebb bearish fordulos minta. A nyakvonal a ket volgyet koti
    ossze, es DOLT is lehet - ezert vonalkent kezeljuk, nem fix szintkent.
    """
    return _head_shoulders(ctx, inverse=False)


@register
def inverse_head_and_shoulders(ctx: Context) -> list[PatternMatch]:
    """A fej-vall tukorkepe: bullish fordulos minta."""
    return _head_shoulders(ctx, inverse=True)


def _head_shoulders(ctx: Context, inverse: bool) -> list[PatternMatch]:
    matches: list[PatternMatch] = []
    shoulder_kind = "L" if inverse else "H"
    valley_kind = "H" if inverse else "L"

    for window in _windows(ctx.pivots, 5):
        left, valley1, head, valley2, right = window

        if left.kind != shoulder_kind or head.kind != shoulder_kind or right.kind != shoulder_kind:
            continue
        if valley1.kind != valley_kind or valley2.kind != valley_kind:
            continue
        if not recent_enough(ctx, right.index):
            continue

        span = right.index - left.index
        if not (ctx.cfg.min_pattern_bars <= span <= ctx.cfg.max_pattern_bars):
            continue

        # Fordulos minta: emelkedes utan jon a fej-vall, eses utan a forditottja
        prior = prior_move_atr(ctx, left.index)
        if inverse and prior > -2.0:
            continue
        if not inverse and prior < 2.0:
            continue

        # A fejnek egyertelmuen ki kell emelkednie mindket vallbol
        if inverse:
            head_is_extreme = head.price < left.price and head.price < right.price
        else:
            head_is_extreme = head.price > left.price and head.price > right.price
        if not head_is_extreme:
            continue

        if min(pct_diff(head.price, left.price), pct_diff(head.price, right.price)) < 2.0:
            continue

        # A ket vall nagyjabol azonos magassagu legyen
        if pct_diff(left.price, right.price) > 8.0:
            continue

        # Nyakvonal a ket volgyon at; a tul meredek nyakvonal ervenytelen
        neckline = _line_through(valley1, valley2, len(ctx.df))
        neckline_at_right = float(neckline[right.index])
        slope_pct = (valley2.price - valley1.price) / max(valley2.index - valley1.index, 1)
        slope_pct = slope_pct / max(abs(neckline_at_right), 1e-9) * 100.0
        if abs(slope_pct) > 0.35:
            continue

        neckline_at_head = float(neckline[head.index])
        height = abs(head.price - neckline_at_head)
        if height < 2.0 * ctx.atr_at(head.index):
            continue

        direction = "bullish" if inverse else "bearish"

        # A trigger a nyakvonal AKTUALIS erteke - dolt vonalnal ez naponta valtozik
        trigger_level = float(neckline[ctx.last_index])
        breakout = breakout_index_line(ctx.df, neckline, right.index, direction)

        if breakout is not None:
            status = "triggered"
            trigger_date = ctx.date(breakout)
            reference = float(neckline[breakout])
        else:
            status = "forming"
            trigger_date = None
            reference = trigger_level

        target = reference + height if inverse else reference - height
        invalidation = right.price

        quality = _quality(
            ctx,
            symmetry=symmetry_score(
                abs(head.price - left.price), abs(head.price - right.price)
            ),
            duration=duration_score(span, 25, 120),
            start=left.index,
            end=right.index,
            trigger_index=breakout,
            height_atr=height / ctx.atr_at(right.index),
        )
        # A valltavolsagok szimmetriaja kulon fontos ennel a mintanal
        time_symmetry = symmetry_score(head.index - left.index, right.index - head.index)
        quality = quality * 0.8 + time_symmetry * 0.2

        if quality < ctx.cfg.min_quality:
            continue

        name = "Forditott fej-vall" if inverse else "Fej-vall"
        matches.append(
            PatternMatch(
                symbol=ctx.symbol,
                name=name,
                direction=direction,
                status=status,
                quality=quality,
                trigger_level=trigger_level,
                target=target,
                invalidation=invalidation,
                start_date=left.date,
                end_date=right.date,
                trigger_date=trigger_date,
                key_points=[(p.date, p.price) for p in window],
                notes=(
                    f"Fej {head.price:.2f}, vallak {left.price:.2f} / {right.price:.2f}, "
                    f"nyakvonal most {trigger_level:.2f}"
                ),
                extras={
                    "neckline_points": [
                        (valley1.date, valley1.price),
                        (valley2.date, valley2.price),
                    ],
                    "neckline_now": trigger_level,
                    "kind": "hs",
                },
            )
        )
        break

    return matches


# ---------------------------------------------------------------------------
# Kozos minosegi pontozas
# ---------------------------------------------------------------------------
def _quality(
    ctx: Context,
    symmetry: float,
    duration: float,
    start: int,
    end: int,
    trigger_index: int | None,
    height_atr: float,
) -> float:
    """A formacio minosege 0-1 kozott, ot tenyezobol.

    A kitoresi volumen csak akkor szamit, ha mar volt kitores; formalodo
    mintanal helyette semleges 0.5-tel szamolunk, hogy ne buntessuk azt,
    ami meg nem tortenhetett meg.
    """
    dryup = volume_dryup(ctx.df, start, end)
    breakout_volume = volume_confirmation(ctx.df, trigger_index) if trigger_index else 0.5

    # A tul lapos minta zaj, a tul nagy pedig gyanusan ritka; 3-8 ATR az ideali
    height = float(np.clip((height_atr - 1.5) / 4.0, 0.0, 1.0))

    base = (
        0.30 * duration
        + 0.30 * height
        + 0.20 * dryup
        + 0.20 * breakout_volume
    )

    # A szimmetria SZORZOKENT hat, nem osszeadando tagkent. Egy aszimmetrikus
    # minta nem "javithato ki" jo idotartammal es volumennel - ha a geometria
    # nem stimmel, az egesz talalat gyenge. Osszeadaskent minden komponens
    # kozepes erteke is 0.8 folott vegzett, ami hasznalhatatlanul lapos.
    return float(np.clip(base * (0.45 + 0.55 * symmetry), 0.0, 1.0))
