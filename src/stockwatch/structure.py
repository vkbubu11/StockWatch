"""Piaci szerkezet: swing pivotok es trendvonalak.

Ez a modul a rendszer alapja - a support szintek es minden formacio
felismerese pivotokra epul, nem nyers gyertyakra. Ha itt hibazunk, minden
rateg felette rossz lesz.

Ket pivot-definiciot hasznalunk:
  * zigzag  - ATR-normalizalt visszahuzas erositi meg; skala-fuggetlen,
              ezert a formaciofelismeres ezt hasznalja
  * fraktal - N gyertya balra/jobbra lokalis szelsoertek; surubb halo,
              a szintkereses ezt is beveszi

FONTOS - look-ahead:
  Mindket modszer utolagos megerositest igenyel, ezert a LEGUTOLSO pivot meg
  nem vegleges. A confirmed mezo jelzi ezt. A backtest kizarolag megerositett
  pivotot hasznalhat, kulonben a talalati aranyok irrealisan jok lesznek - ez
  a leggyakoribb hibaforras az ilyen rendszerekben.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats

from . import indicators

PivotKind = Literal["H", "L"]


@dataclass(frozen=True)
class Pivot:
    """Egy swing fordulopont."""

    index: int                 # pozicio a DataFrame-ben
    date: pd.Timestamp
    price: float
    kind: PivotKind            # "H" = swing csucs, "L" = swing alj
    confirmed: bool            # False: meg alakul, nem hasznalhato backtestben

    @property
    def is_high(self) -> bool:
        return self.kind == "H"


def _atr_array(df: pd.DataFrame, period: int) -> np.ndarray:
    """ATR tomb, a bevezeto NaN-ok feltoltve.

    Igy a zigzag kuszob mar az elso gyertyaktol ertelmes erteket ad.
    """
    series = df["atr"] if "atr" in df.columns else indicators.atr(df, period)
    filled = series.bfill()
    if filled.isna().all():
        # Vegso menedek: az arszint 1 szazaleka
        filled = df["close"] * 0.01
    return filled.to_numpy(dtype="float64")


def find_zigzag_pivots(
    df: pd.DataFrame,
    atr_mult: float = 1.5,
    atr_period: int = 14,
) -> list[Pivot]:
    """ATR-normalizalt zigzag.

    Egy jelolt szelsoerteket akkor erositunk meg pivotkent, ha az ar
    atr_mult * ATR mertekben visszahuz rola. Mivel a kuszob ATR-ben van
    megadva, ugyanaz a beallitas mukodik egy 500 dollaros es egy 20 dollaros
    papiron is.
    """
    if len(df) < 3:
        return []

    highs = df["high"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    atr = _atr_array(df, atr_period)
    dates = df.index

    pivots: list[Pivot] = []
    direction = 0          # 0 = meg nem tudjuk, 1 = csucsot kovetunk, -1 = aljat
    hi_i, hi_p = 0, highs[0]
    lo_i, lo_p = 0, lows[0]

    for i in range(1, len(df)):
        # A jelolt szelsoertekek frissitese (csak az aktiv iranyban)
        if direction >= 0 and highs[i] >= hi_p:
            hi_i, hi_p = i, highs[i]
        if direction <= 0 and lows[i] <= lo_p:
            lo_i, lo_p = i, lows[i]

        # Csucs megerositese: eleg nagyot esett a jelolt csucsrol
        if direction >= 0 and i > hi_i and (hi_p - lows[i]) >= atr_mult * atr[hi_i]:
            pivots.append(Pivot(hi_i, dates[hi_i], float(hi_p), "H", True))
            direction = -1
            # Az uj alj-jelolt csak a csucs UTANI gyertyakbol johet
            segment = lows[hi_i + 1 : i + 1]
            offset = int(np.argmin(segment))
            lo_i, lo_p = hi_i + 1 + offset, float(segment[offset])
            continue

        # Alj megerositese: eleg nagyot emelkedett a jelolt aljrol
        if direction <= 0 and i > lo_i and (highs[i] - lo_p) >= atr_mult * atr[lo_i]:
            pivots.append(Pivot(lo_i, dates[lo_i], float(lo_p), "L", True))
            direction = 1
            segment = highs[lo_i + 1 : i + 1]
            offset = int(np.argmax(segment))
            hi_i, hi_p = lo_i + 1 + offset, float(segment[offset])

    # Az utolso, meg meg nem erositett szelsoertek - a formalodo mintakhoz
    # kell, de kulon jelolve, hogy a backtest kihagyhassa.
    if direction == 1:
        pivots.append(Pivot(hi_i, dates[hi_i], float(hi_p), "H", False))
    elif direction == -1:
        pivots.append(Pivot(lo_i, dates[lo_i], float(lo_p), "L", False))

    return pivots


def find_fractal_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[Pivot]:
    """Lokalis szelsoertekek N gyertyas bal/jobb ablakkal.

    Vektorizalt, mert a backtest tobb ezerszer hivja.
    """
    if len(df) < left + right + 1:
        return []

    window = left + right + 1
    highs, lows = df["high"], df["low"]

    # Az i. pozicion a [i-left, i+right] ablak szelsoerteke
    roll_max = highs.rolling(window, min_periods=window).max().shift(-right)
    roll_min = lows.rolling(window, min_periods=window).min().shift(-right)

    is_high = (highs >= roll_max) & roll_max.notna()
    is_low = (lows <= roll_min) & roll_min.notna()

    last_index = len(df) - 1
    pivots: list[Pivot] = []

    for position in np.flatnonzero(is_high.to_numpy()):
        position = int(position)
        pivots.append(
            Pivot(position, df.index[position], float(highs.iloc[position]), "H",
                  confirmed=position + right <= last_index)
        )
    for position in np.flatnonzero(is_low.to_numpy()):
        position = int(position)
        pivots.append(
            Pivot(position, df.index[position], float(lows.iloc[position]), "L",
                  confirmed=position + right <= last_index)
        )

    pivots.sort(key=lambda p: p.index)
    return pivots


def alternating(pivots: list[Pivot]) -> list[Pivot]:
    """Valtakozo H-L-H-L sorozatta tisztitja a pivotlistat.

    Ha egymas utan ket csucs all, a magasabbat tartjuk meg (ket alj eseten az
    alacsonyabbat). A formaciofelismeres tiszta valtakozast felteez.
    """
    cleaned: list[Pivot] = []
    for pivot in sorted(pivots, key=lambda p: p.index):
        if cleaned and cleaned[-1].kind == pivot.kind:
            previous = cleaned[-1]
            better = (
                pivot.price > previous.price if pivot.is_high else pivot.price < previous.price
            )
            if better:
                cleaned[-1] = pivot
        else:
            cleaned.append(pivot)
    return cleaned


def confirmed_only(pivots: list[Pivot]) -> list[Pivot]:
    """A backtest kotelezoen ezen keresztul kap pivotokat."""
    return [p for p in pivots if p.confirmed]


# ---------------------------------------------------------------------------
# Trendvonalak
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrendLine:
    """Pivotokra illesztett egyenes: ar = slope * index + intercept."""

    slope: float
    intercept: float
    r2: float
    rmse: float          # a reziduumok negyzetes atlaga, ARBAN
    points: int
    first_index: int
    last_index: int

    def value_at(self, index: float) -> float:
        return self.slope * index + self.intercept

    @property
    def slope_pct_per_bar(self) -> float:
        """Meredekseg a vonal kozepertekehez kepest, szazalekban.

        Igy osszehasonlithato kulonbozo arszintu papirok trendvonala, es
        eldontheto, hogy egy vonal vizszintesnek szamit-e.
        """
        mid_index = (self.first_index + self.last_index) / 2
        mid_value = self.value_at(mid_index)
        if mid_value == 0:
            return 0.0
        return self.slope / abs(mid_value) * 100.0


def fit_trendline(pivots: list[Pivot]) -> TrendLine | None:
    """Legkisebb negyzetes egyenes illesztese pivotokra.

    Az `rmse` mezo fontosabb, mint az `r2`!

    Egy VIZSZINTES trendvonalnal (pl. az emelkedo haromszog tetovonala) az R^2
    definicio szerint nullahoz tart, hiszen a szorast nem a meredekseg
    magyarazza - hiaba esik mind a harom csucs tokeletesen ugyanarra a szintre.
    Az R^2-re alapozott szures ezert pont a legtisztabb vizszintes vonalakat
    utasitana el. A reziduumok abszolut merete (rmse) viszont mindket esetben
    helyesen meri az illeszkedest, ezert a detektorok azt hasznaljak - a
    formacio magassagahoz normalizalva.
    """
    if len(pivots) < 2:
        return None

    x = np.array([p.index for p in pivots], dtype="float64")
    y = np.array([p.price for p in pivots], dtype="float64")

    if np.ptp(x) == 0:
        return None

    result = stats.linregress(x, y)
    # Tokeletes illeszkedesnel (2 pont) az rvalue 1.0; a NaN-t 0-ra visszuk.
    r_value = 0.0 if np.isnan(result.rvalue) else float(result.rvalue)

    predicted = result.slope * x + result.intercept
    rmse = float(np.sqrt(np.mean((y - predicted) ** 2)))

    return TrendLine(
        slope=float(result.slope),
        intercept=float(result.intercept),
        r2=r_value ** 2,
        rmse=rmse,
        points=len(pivots),
        first_index=int(x.min()),
        last_index=int(x.max()),
    )
