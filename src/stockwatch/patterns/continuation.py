"""Folytatolagos formaciok: haromszogek, ekek, zaszlok, teglalap, csesze-ful.

Ezek jellemzoen trend kozbeni pihenok: az ar konszolidal, majd a korabbi
irányban folytatja. A haromszogeket es ekeket trendvonal-illesztessel
ismerjuk fel (R^2 kuszobbel), a zaszlot impulzus + osszehuzodo konszolidacio
paroskent, a csesze-fult pedig a lekerekitett alj geometriajabol.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..structure import Pivot, TrendLine, fit_trendline
from .base import (
    Context,
    PatternMatch,
    breakout_index,
    breakout_index_line,
    duration_score,
    is_flat,
    pct_diff,
    prior_move_atr,
    recent_enough,
    register,
    similar,
    symmetry_score,
    volume_confirmation,
    volume_dryup,
)

# A haromszog ervenyes kitorese a formacio hosszanak ezen aranyaban tortenik.
# A csucsig elhuzodo minta elveszti az erejet - a piac "kifarad" benne.
APEX_MIN_RATIO = 0.45
APEX_MAX_RATIO = 0.85


def _line_array(line: TrendLine, length: int) -> np.ndarray:
    return line.slope * np.arange(length, dtype="float64") + line.intercept


def _prior_trend(ctx: Context, start_index: int, lookback: int = 25) -> str:
    """A formacio ELOTTI trend iranya.

    A folytatolagos mintaknal ez adja a varhato kitoresi iranyt: egy
    szimmetrikus haromszog onmagaban semleges, de emelkedo trendben bullish
    elfogultsagu.
    """
    reference = max(start_index - lookback, 0)
    if reference >= start_index:
        return "neutral"

    before = ctx.close(reference)
    at_start = ctx.close(start_index)
    change = (at_start - before) / before * 100.0

    if change > 4.0:
        return "bullish"
    if change < -4.0:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Haromszogek es ekek
# ---------------------------------------------------------------------------
@register
def triangles_and_wedges(ctx: Context) -> list[PatternMatch]:
    """Ket trendvonalat illeszt a csucsokra es az aljakra, majd a
    meredeksegek alapjan osztalyoz.

    Egyetlen detektor kezeli az osszes valtozatot, mert a geometria
    ugyanaz - csak a ket meredekseg viszonya kulonbozik.
    """
    pivots = ctx.pivots
    if len(pivots) < 6:
        return []

    best: PatternMatch | None = None

    # Az ablak VEGE is csuszik, nem csak a hossza. Ez azert kell, mert a
    # kitores utani pivotok mar nem reszei a formacionak: ha beleveszzuk oket
    # a trendvonal-illesztesbe, a vizszintes tetovonal "megdol", es a minta
    # felismerhetetlenne valik. A `recent_enough` gondoskodik rola, hogy igy
    # se jelentsunk regi, mar lefutott formaciot.
    for end in range(len(pivots), max(len(pivots) - 4, 5), -1):
        for size in range(6, min(end, 11) + 1):
            window = pivots[end - size : end]
            highs = [p for p in window if p.is_high]
            lows = [p for p in window if not p.is_high]

            # Mindket vonalhoz legalabb HAROM pont kell. Ket pontra definicio
            # szerint tokeletesen illeszkedik az egyenes (R2 = 1), igy a
            # ketpontos valtozat gyakorlatilag barmilyen zajt "haromszognek"
            # lat - ez volt a hamis pozitivok fo forrasa.
            if len(highs) < 3 or len(lows) < 3:
                continue

            start_index, end_index = window[0].index, window[-1].index
            span = end_index - start_index
            if not (ctx.cfg.min_pattern_bars <= span <= ctx.cfg.max_pattern_bars):
                continue
            if not recent_enough(ctx, end_index):
                continue

            upper = fit_trendline(highs)
            lower = fit_trendline(lows)
            if upper is None or lower is None:
                continue

            # Az illeszkedest NEM itt szurjuk: a helyes mercehez ismerni kell a
            # formacio magassagat, amit a _classify szamol ki.
            match = _classify(ctx, window, upper, lower, start_index, end_index, span)
            if match and (best is None or match.quality > best.quality):
                best = match

    return [best] if best else []


def _classify(
    ctx: Context,
    window: list[Pivot],
    upper: TrendLine,
    lower: TrendLine,
    start_index: int,
    end_index: int,
    span: int,
) -> PatternMatch | None:
    length = len(ctx.df)
    upper_values = _line_array(upper, length)
    lower_values = _line_array(lower, length)

    width_start = float(upper_values[start_index] - lower_values[start_index])
    width_end = float(upper_values[end_index] - lower_values[end_index])

    # A vonalak nem keresztezhetik egymast a formacion belul
    if width_start <= 0 or width_end <= 0:
        return None

    converging = width_end < width_start * 0.80
    parallel = 0.80 <= width_end / width_start <= 1.25

    # Az ekeknel jelentosen szigorubb a kovetelmeny. Ket azonos iranyba tarto,
    # enyhen kozeledo vonal barmilyen trendben eloall - tiszta emelkedo
    # trenden is "emelkedo eket" talaltunk, ami nyilvanvalo hamis pozitiv.
    # Valodi ekhez eros osszeszukules kell (a savszelesseg felezodese).
    strongly_converging = width_end < width_start * 0.55

    up_slope = upper.slope_pct_per_bar
    low_slope = lower.slope_pct_per_bar
    upper_flat, lower_flat = is_flat(up_slope), is_flat(low_slope)

    name: str
    direction: str
    if upper_flat and low_slope > 0.05 and converging:
        name, direction = "Emelkedo haromszog", "bullish"
    elif lower_flat and up_slope < -0.05 and converging:
        name, direction = "Csokkeno haromszog", "bearish"
    elif up_slope < -0.03 and low_slope > 0.03 and converging:
        name, direction = "Szimmetrikus haromszog", _prior_trend(ctx, start_index)
    elif up_slope > 0.05 and low_slope > 0.05 and strongly_converging:
        # Emelkedo ek: mindketto emelkedik, de az aljak gyorsabban - bearish
        name, direction = "Emelkedo ek", "bearish"
    elif up_slope < -0.05 and low_slope < -0.05 and strongly_converging:
        name, direction = "Eso ek", "bullish"
    elif upper_flat and lower_flat and parallel:
        name, direction = "Teglalap (range)", _prior_trend(ctx, start_index)
    else:
        return None

    # FOLYTATOLAGOS minta: definicio szerint szunet egy trendben. Ha nem volt
    # elotte erdemi elmozdulas, akkor nincs mit folytatni - a celalag zajban
    # talalt "haromszog" pont ezen bukik el.
    #
    # Ez elvi szuro, nem kuszobhangolas: a geometria onmagaban NEM tudja
    # megkulonboztetni a valodi haromszoget a veletlen bolyongasban eloallotol,
    # mert a veletlen bolyongas is termel valodi haromszogeket. A kontextus
    # viszont igen.
    prior = prior_move_atr(ctx, start_index)
    if abs(prior) < 2.0:
        return None
    if direction == "bullish" and prior < 0:
        return None
    if direction == "bearish" and prior > 0:
        return None

    # Apex-szabaly: hol metszenek a vonalak, es hol tartunk ehhez kepest
    if converging:
        slope_gap = upper.slope - lower.slope
        if abs(slope_gap) < 1e-12:
            return None
        apex_index = (lower.intercept - upper.intercept) / slope_gap
        apex_distance = apex_index - start_index
        if apex_distance <= 0:
            return None
        progress = (ctx.last_index - start_index) / apex_distance
        if progress > 1.0:
            return None   # mar tullepett a csucson, a minta kifutott
    else:
        apex_index, progress = float("nan"), 0.5

    height = width_start
    if height < 2.0 * ctx.atr_at(end_index):
        return None

    # Az illeszkedes merteke a formacio MAGASSAGAHOZ viszonyitva. Ez a helyes
    # merce: egy 20 pont magas haromszognel a 0.8 pontos szoras kivalo, egy 2
    # pont magasnal viszont ertelmetlen zaj. Skalafuggetlen, es - az R^2-tel
    # ellentetben - a vizszintes vonalakra is helyesen mukodik.
    fit_error = (upper.rmse + lower.rmse) / 2 / height
    if fit_error > ctx.cfg.trendline_max_error_ratio:
        return None

    # Trigger es kitores-kereses a megfelelo vonalon
    if direction == "bearish":
        line, trigger_direction = lower_values, "bearish"
    else:
        line, trigger_direction = upper_values, "bullish"

    breakout = breakout_index_line(ctx.df, line, end_index, trigger_direction)
    trigger_level = float(line[ctx.last_index])

    if breakout is not None:
        status, trigger_date = "triggered", ctx.date(breakout)
        reference = float(line[breakout])
        # Az apex-szabalyt a TENYLEGES kitoresre ellenorizzuk
        if converging and not (APEX_MIN_RATIO <= (breakout - start_index) / apex_distance <= APEX_MAX_RATIO):
            return None
    else:
        status, trigger_date, reference = "forming", None, trigger_level

    target = reference + height if trigger_direction == "bullish" else reference - height
    invalidation = (
        float(lower_values[ctx.last_index])
        if trigger_direction == "bullish"
        else float(upper_values[ctx.last_index])
    )

    # A vonalillesztes minosege SZORZOKENT hat. Osszeadando tagkent egy
    # kozepesen illeszkedo vonalparos is 0.85 fole jutott a tobbi komponensbol,
    # vagyis a pontszam nem kulonboztette meg a valodi haromszoget a zajtol.
    # Az illeszkedesi pontszam TELITODIK: a magassag 4 szazaleka alatti hiba
    # mar gyakorlatilag tokeletes vonal, nem jar erte levonas. Linearis
    # bunteteskent egy tankonyvi haromszog is csak 0.77-et kapott, ami a
    # kuszob ala huzta az egesz talalatot.
    max_error = ctx.cfg.trendline_max_error_ratio
    fit_quality = float(
        np.clip(1.0 - (fit_error - 0.04) / max(max_error - 0.04, 1e-9), 0.0, 1.0)
    )
    base = (
        0.30 * duration_score(span, 20, 100)
        + 0.25 * min(len(window) / 6.0, 1.0)
        + 0.20 * volume_dryup(ctx.df, start_index, end_index)
        + 0.25 * (volume_confirmation(ctx.df, breakout) if breakout else 0.5)
    )
    quality = float(np.clip(base * fit_quality, 0.0, 1.0))
    if quality < ctx.cfg.min_quality:
        return None

    return PatternMatch(
        symbol=ctx.symbol,
        name=name,
        direction=direction if direction != "neutral" else trigger_direction,
        status=status,
        quality=quality,
        trigger_level=trigger_level,
        target=target,
        invalidation=invalidation,
        start_date=ctx.date(start_index),
        end_date=ctx.date(end_index),
        trigger_date=trigger_date,
        key_points=[(p.date, p.price) for p in window],
        notes=(
            f"Vonalillesztes hibaja a magassag {fit_error:.1%}-a, "
            f"magassag {height:.2f}, elorehaladas a csucsig {progress:.0%}"
        ),
        extras={
            "upper_line": (float(upper.slope), float(upper.intercept)),
            "lower_line": (float(lower.slope), float(lower.intercept)),
            "line_start": start_index,
            "kind": "triangle",
        },
    )


# ---------------------------------------------------------------------------
# Zaszlo es pennant
# ---------------------------------------------------------------------------
@register
def flag(ctx: Context) -> list[PatternMatch]:
    """Meredek impulzus lab ("zaszlorud"), majd szuk konszolidacio.

    A klasszikus jellemzok, amiket megkovetelunk:
      * a rud legalabb 3 ATR elmozdulas,
      * a konszolidacio a rud kevesebb mint felet huzza vissza,
      * es rovidebb, mint maga a rud,
      * a volumen szarad a konszolidacio alatt.
    """
    df = ctx.df
    last = ctx.last_index
    if last < 60:
        return []

    highs = df["high"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    closes = df["close"].to_numpy(dtype="float64")

    best: PatternMatch | None = None

    # A konszolidacio a legutobbi gyertyaknal vegzodik (kulonben nem aktualis)
    for cons_end in range(last, max(last - 12, 30), -3):
        for cons_len in range(5, 31, 3):
            cons_start = cons_end - cons_len
            if cons_start < 25:
                continue

            for pole_len in range(6, 27, 3):
                pole_start = cons_start - pole_len
                if pole_start < 5:
                    continue

                pole_move = closes[cons_start] - closes[pole_start]
                atr_here = ctx.atr_at(cons_start)

                # A rud merete ALULROL es FELULROL is korlatos. A felso korlat
                # legalabb annyira fontos: egy 12 ATR-es "rud" mar nem zaszlo,
                # hanem parabolikus kifutas, es a measured move rea vetitve
                # irrealis celt ad (elo adaton +24 szazalekos celarat lattunk
                # egy honapos horizonton). 3-10 ATR a tankonyvi sav.
                if not (3.0 * atr_here <= abs(pole_move) <= 10.0 * atr_here):
                    continue

                bullish = pole_move > 0
                cons_high = float(np.max(highs[cons_start : cons_end + 1]))
                cons_low = float(np.min(lows[cons_start : cons_end + 1]))
                cons_range = cons_high - cons_low

                # A konszolidacio legyen erdemben szukebb, mint a rud
                if cons_range > abs(pole_move) * 0.55:
                    continue

                # Es ne huzza vissza a rud felenel tobbet
                retrace = (
                    closes[cons_start] - cons_low if bullish else cons_high - closes[cons_start]
                )
                if retrace > abs(pole_move) * 0.55:
                    continue

                # A zaszlo ellenirany vagy oldalazas, nem tovabbi impulzus
                cons_move = closes[cons_end] - closes[cons_start]
                if bullish and cons_move > abs(pole_move) * 0.25:
                    continue
                if not bullish and cons_move < -abs(pole_move) * 0.25:
                    continue

                direction = "bullish" if bullish else "bearish"
                trigger = cons_high if bullish else cons_low
                breakout = breakout_index(df, trigger, cons_end, direction)

                if breakout is not None:
                    status, trigger_date = "triggered", ctx.date(breakout)
                    reference = float(closes[breakout])
                else:
                    status, trigger_date, reference = "forming", None, trigger

                target = reference + pole_move
                invalidation = cons_low if bullish else cons_high

                dryup = volume_dryup(df, cons_start, cons_end)
                tightness = 1.0 - min(cons_range / max(abs(pole_move), 1e-9), 1.0)
                quality = float(
                    np.clip(
                        0.25 * tightness
                        + 0.20 * min(abs(pole_move) / (6.0 * atr_here), 1.0)
                        + 0.20 * dryup
                        + 0.15 * duration_score(cons_len, 6, 25)
                        + 0.20 * (volume_confirmation(df, breakout) if breakout else 0.5),
                        0.0,
                        1.0,
                    )
                )
                if quality < ctx.cfg.min_quality:
                    continue

                name = "Bull zaszlo" if bullish else "Bear zaszlo"
                candidate = PatternMatch(
                    symbol=ctx.symbol,
                    name=name,
                    direction=direction,
                    status=status,
                    quality=quality,
                    trigger_level=trigger,
                    target=target,
                    invalidation=invalidation,
                    start_date=ctx.date(pole_start),
                    end_date=ctx.date(cons_end),
                    trigger_date=trigger_date,
                    key_points=[
                        (ctx.date(pole_start), float(closes[pole_start])),
                        (ctx.date(cons_start), float(closes[cons_start])),
                        (ctx.date(cons_end), float(closes[cons_end])),
                    ],
                    notes=(
                        f"Zaszlorud {pole_move:+.2f} ({pole_len} gyertya), "
                        f"konszolidacio {cons_len} gyertya, savszelesseg {cons_range:.2f}"
                    ),
                    extras={
                        "pole": (pole_start, cons_start),
                        "consolidation": (cons_start, cons_end),
                        "cons_high": cons_high,
                        "cons_low": cons_low,
                        "kind": "flag",
                    },
                )
                if best is None or candidate.quality > best.quality:
                    best = candidate

    return [best] if best else []


# ---------------------------------------------------------------------------
# Csesze-ful
# ---------------------------------------------------------------------------
@register
def cup_and_handle(ctx: Context) -> list[PatternMatch]:
    """U-alaku bazis, majd sekely visszahuzas (a "ful").

    A legfontosabb megkulonbozteto jegy a LEKEREKITETT alj: egy eles V-alj
    nem csesze. Ezt azzal merjuk, hogy az ar mennyi idot toltott a melypont
    kozeleben - egy V-nel ez csak nehany gyertya.
    """
    pivots = ctx.pivots
    if len(pivots) < 3:
        return []

    df = ctx.df
    lows = df["low"].to_numpy(dtype="float64")
    highs = df["high"].to_numpy(dtype="float64")

    for start in range(len(pivots) - 3, -1, -1):
        left, bottom, right = pivots[start : start + 3]
        if not (left.is_high and not bottom.is_high and right.is_high):
            continue

        cup_width = right.index - left.index
        if not (30 <= cup_width <= ctx.cfg.max_pattern_bars):
            continue

        # A ket perem nagyjabol azonos magassagban
        if pct_diff(left.price, right.price) > 6.0:
            continue

        rim = (left.price + right.price) / 2
        depth = rim - bottom.price
        if depth <= 0:
            continue

        depth_ratio = depth / rim
        if not (0.10 <= depth_ratio <= 0.50):
            continue   # tul sekely vagy tul melly ahhoz, hogy csesze legyen

        # Lekerekitettseg: hany gyertya jart a melypont 1.5 ATR-es kornyeken
        zone = bottom.price + 1.5 * ctx.atr_at(bottom.index)
        near_bottom = int(np.sum(lows[left.index : right.index + 1] <= zone))
        roundness = near_bottom / cup_width
        if roundness < 0.15:
            continue   # eles V-alj, nem csesze

        # A ful: a jobb perem utani sekely visszahuzas
        handle_start = right.index
        handle_end = min(ctx.last_index, handle_start + 30)
        if handle_end - handle_start < 4:
            continue
        if not recent_enough(ctx, handle_end):
            continue

        handle_low = float(np.min(lows[handle_start : handle_end + 1]))
        handle_high = float(np.max(highs[handle_start : handle_end + 1]))
        handle_depth = rim - handle_low

        # A ful a csesze melysegenek legfeljebb harmadat huzza vissza
        if handle_depth > depth / 3.0 or handle_depth <= 0:
            continue

        trigger = max(handle_high, rim)
        breakout = breakout_index(df, trigger, handle_end, "bullish")

        if breakout is not None:
            status, trigger_date = "triggered", ctx.date(breakout)
            reference = float(df["close"].iloc[breakout])
        else:
            status, trigger_date, reference = "forming", None, trigger

        quality = float(
            np.clip(
                0.25 * symmetry_score(left.price, right.price)
                + 0.25 * min(roundness / 0.35, 1.0)
                + 0.15 * duration_score(cup_width, 40, 150)
                + 0.15 * volume_dryup(df, left.index, right.index)
                + 0.20 * (volume_confirmation(df, breakout) if breakout else 0.5),
                0.0,
                1.0,
            )
        )
        if quality < ctx.cfg.min_quality:
            continue

        return [
            PatternMatch(
                symbol=ctx.symbol,
                name="Csesze-ful",
                direction="bullish",
                status=status,
                quality=quality,
                trigger_level=trigger,
                target=reference + depth,
                invalidation=handle_low,
                start_date=left.date,
                end_date=ctx.date(handle_end),
                trigger_date=trigger_date,
                key_points=[
                    (left.date, left.price),
                    (bottom.date, bottom.price),
                    (right.date, right.price),
                    (ctx.date(handle_end), handle_low),
                ],
                notes=(
                    f"Csesze melysege {depth:.2f} ({depth_ratio:.0%}), "
                    f"lekerekitettseg {roundness:.0%}, ful melysege {handle_depth:.2f}"
                ),
                extras={
                    "rim": rim,
                    "bottom": bottom.price,
                    "handle": (handle_start, handle_end),
                    "kind": "cup",
                },
            )
        ]

    return []
