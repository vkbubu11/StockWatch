"""A feladattipusok kozos segedei: normalizalt ar (az utolso lathato zaro = 100), ATR, sablonos sor."""
from __future__ import annotations

import pandas as pd

from .. import scoring
from ..templates import Line


def norm(df: pd.DataFrame, i: int) -> tuple[float, float]:
    """(skala, ATR normalizalt aron) a dontes napjan - csak az addigi adatbol."""
    scale = 100.0 / float(df["close"].iloc[i])
    atr = float(scoring.atr_series(df.iloc[: i + 1]).iloc[-1]) * scale
    return scale, atr


def num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def line(key: str, *args) -> dict:
    """Sablonos sor: a szam argumentumok (int / float) formazva es "numeric"-kent jelolve."""
    text = [num(a) if isinstance(a, float) else str(a) for a in args]
    numeric = [k for k, a in enumerate(args) if isinstance(a, (int, float)) and not isinstance(a, bool)]
    return Line(key, text, numeric).to_dict()
