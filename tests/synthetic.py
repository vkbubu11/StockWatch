"""Szintetikus arfolyamsorozatok ismert geometriaval.

A detektorok tesztelesenek egyetlen megbizhato modja: olyan sorozatot
generalunk, amiben MATEMATIKAILAG benne van a keresett formacio, es
megkoveteljuk, hogy a detektor megtalalja - ugyanakkor zajon es tiszta
trenden NE jelezzen semmit.

A hamis pozitiv itt a valodi kockazat: egy olyan rendszer, ami mindenben lat
fej-vallat, rosszabb a semminel.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build(
    points: list[tuple[int, float]],
    total_bars: int | None = None,
    noise_pct: float = 0.004,
    seed: int = 42,
    volume_profile: np.ndarray | None = None,
) -> pd.DataFrame:
    """OHLCV sorozat epitese kulcspontok kozotti linearis interpolacioval.

    A `points` (gyertya_index, ar) parokat var. A koztes napokat
    interpolaljuk, majd enyhe zajt adunk hozza, hogy realisztikus gyertyakat
    kapjunk - de a formacio geometriaja felismerheto maradjon.
    """
    rng = np.random.default_rng(seed)
    total_bars = total_bars or points[-1][0] + 1

    xs = np.array([p[0] for p in points], dtype="float64")
    ys = np.array([p[1] for p in points], dtype="float64")
    closes = np.interp(np.arange(total_bars), xs, ys)

    closes = closes * (1.0 + rng.normal(0, noise_pct, total_bars))

    opens = np.empty(total_bars)
    opens[0] = closes[0]
    opens[1:] = closes[:-1] * (1.0 + rng.normal(0, noise_pct * 0.5, total_bars - 1))

    spread = np.abs(rng.normal(0, noise_pct * 1.5, total_bars)) * closes
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread

    if volume_profile is None:
        volumes = rng.uniform(8e6, 1.2e7, total_bars)
    else:
        volumes = volume_profile

    index = pd.bdate_range("2020-01-01", periods=total_bars, name="date")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def _padding(start_price: float, bars: int) -> list[tuple[int, float]]:
    """Bevezeto oldalazas, hogy az indikatoroknak legyen felfutasi ideje."""
    return [(0, start_price), (bars, start_price)]


# ---------------------------------------------------------------------------
# Fordulos formaciok
# ---------------------------------------------------------------------------
def double_bottom(breakout: bool = True, pad: int = 60) -> pd.DataFrame:
    """Tiszta dupla alj: 100 -> 80 -> 92 -> 80 -> kitores 92 folott.

    A ket alj pontosan azonos szinten van, a nyakvonal 92.
    """
    points = _padding(100.0, pad) + [
        (pad + 20, 80.0),    # elso alj
        (pad + 35, 92.0),    # nyakvonal (kozbenso csucs)
        (pad + 50, 80.0),    # masodik alj, azonos szinten
    ]
    if breakout:
        points += [(pad + 62, 96.0), (pad + 75, 100.0)]
    else:
        points += [(pad + 62, 88.0)]
    return build(points)


def double_top(breakout: bool = True, pad: int = 60) -> pd.DataFrame:
    """A dupla alj tukorkepe."""
    points = _padding(80.0, pad) + [
        (pad + 20, 100.0),
        (pad + 35, 88.0),
        (pad + 50, 100.0),
    ]
    if breakout:
        points += [(pad + 62, 84.0), (pad + 75, 80.0)]
    else:
        points += [(pad + 62, 92.0)]
    return build(points)


def head_and_shoulders(breakout: bool = True, pad: int = 60) -> pd.DataFrame:
    """Szabalyos fej-vall vizszintes nyakvonallal.

    Bal vall 100, volgy 90, fej 112, volgy 90, jobb vall 100.
    Idoben is szimmetrikus: a fej egyenlo tavolsagra van a ket valltol.
    """
    points = _padding(85.0, pad) + [
        (pad + 15, 100.0),   # bal vall
        (pad + 25, 90.0),    # volgy
        (pad + 40, 112.0),   # fej
        (pad + 55, 90.0),    # volgy
        (pad + 70, 100.0),   # jobb vall
    ]
    if breakout:
        points += [(pad + 82, 86.0), (pad + 95, 78.0)]
    else:
        points += [(pad + 82, 95.0)]
    return build(points)


def inverse_head_and_shoulders(breakout: bool = True, pad: int = 60) -> pd.DataFrame:
    """A fej-vall tukorkepe."""
    points = _padding(115.0, pad) + [
        (pad + 15, 100.0),
        (pad + 25, 110.0),
        (pad + 40, 88.0),
        (pad + 55, 110.0),
        (pad + 70, 100.0),
    ]
    if breakout:
        points += [(pad + 82, 114.0), (pad + 95, 122.0)]
    else:
        points += [(pad + 82, 105.0)]
    return build(points)


def triple_bottom(pad: int = 60) -> pd.DataFrame:
    points = _padding(100.0, pad) + [
        (pad + 15, 80.0),
        (pad + 27, 91.0),
        (pad + 40, 80.0),
        (pad + 52, 91.0),
        (pad + 65, 80.0),
        (pad + 78, 95.0),
        (pad + 90, 101.0),
    ]
    return build(points)


# ---------------------------------------------------------------------------
# Folytatolagos formaciok
# ---------------------------------------------------------------------------
def ascending_triangle(pad: int = 60) -> pd.DataFrame:
    """Vizszintes tetovonal 100-nal, emelkedo aljak: 82 -> 88 -> 93."""
    points = _padding(75.0, pad) + [
        (pad + 10, 100.0),
        (pad + 22, 82.0),
        (pad + 34, 100.0),
        (pad + 46, 88.0),
        (pad + 58, 100.0),
        (pad + 68, 93.0),
        (pad + 78, 106.0),   # kitores
        (pad + 90, 114.0),
    ]
    return build(points)


def descending_triangle(pad: int = 60) -> pd.DataFrame:
    """Vizszintes aljvonal 80-nal, csokkeno csucsok: 100 -> 94 -> 88."""
    points = _padding(105.0, pad) + [
        (pad + 10, 80.0),
        (pad + 22, 100.0),
        (pad + 34, 80.0),
        (pad + 46, 94.0),
        (pad + 58, 80.0),
        (pad + 68, 88.0),
        (pad + 78, 74.0),    # letores
        (pad + 90, 66.0),
    ]
    return build(points)


def symmetrical_triangle(pad: int = 60) -> pd.DataFrame:
    """Mindket vonal konvergal: 105/75 -> 98/85 -> 94/90."""
    points = _padding(90.0, pad) + [
        (pad + 10, 105.0),
        (pad + 20, 75.0),
        (pad + 32, 98.0),
        (pad + 44, 85.0),
        (pad + 54, 94.0),
        (pad + 64, 90.0),
        (pad + 76, 102.0),   # felfele kitores
        (pad + 88, 112.0),
    ]
    return build(points)


def bull_flag(pad: int = 50) -> pd.DataFrame:
    """Meredek impulzus lab, majd enyhe ellenirany csokkeno volumennel."""
    total = pad + 95
    volumes = np.concatenate([
        np.full(pad, 9e6),
        np.full(20, 2.2e7),   # impulzus: magas volumen
        np.full(30, 6e6),     # zaszlo: szarad a volumen
        np.full(total - pad - 50, 2.0e7),  # kitores: ujra megugrik
    ])[:total]

    points = _padding(60.0, pad) + [
        (pad + 20, 95.0),    # impulzus lab
        (pad + 30, 89.0),    # zaszlo: enyhe visszahuzas
        (pad + 40, 91.0),
        (pad + 50, 86.0),
        (pad + 62, 99.0),    # kitores
        (pad + 80, 118.0),   # measured move
    ]
    return build(points, total_bars=total, volume_profile=volumes)


def rising_wedge(pad: int = 60) -> pd.DataFrame:
    """Mindket vonal emelkedik, de konvergal - bearish minta."""
    points = _padding(70.0, pad) + [
        (pad + 10, 90.0),
        (pad + 20, 80.0),
        (pad + 32, 96.0),
        (pad + 42, 89.0),
        (pad + 52, 100.0),
        (pad + 62, 96.0),
        (pad + 74, 88.0),    # letores
        (pad + 88, 78.0),
    ]
    return build(points)


# ---------------------------------------------------------------------------
# Negativ kontrollok - ezeken NEM szabad formaciot talalni
# ---------------------------------------------------------------------------
def random_walk(bars: int = 250, seed: int = 7, start: float = 100.0) -> pd.DataFrame:
    """Tiszta veletlen bolyongas. Minden itt talalt formacio hamis pozitiv."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.012, bars)
    closes = start * np.exp(np.cumsum(steps))

    opens = np.concatenate([[closes[0]], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.006, bars)) * closes
    index = pd.bdate_range("2020-01-01", periods=bars, name="date")

    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": rng.uniform(8e6, 1.2e7, bars),
        },
        index=index,
    )


def steady_trend(bars: int = 250, slope: float = 0.004, seed: int = 11) -> pd.DataFrame:
    """Egyenletes emelkedo trend, formacio nelkul."""
    return build(
        [(0, 100.0), (bars - 1, 100.0 * (1 + slope) ** bars)],
        total_bars=bars,
        noise_pct=0.006,
        seed=seed,
    )


def flat_line(bars: int = 250, price: float = 100.0) -> pd.DataFrame:
    """Szinte teljesen mozdulatlan ar - degeneralt eset a robusztussaghoz."""
    return build([(0, price), (bars - 1, price)], total_bars=bars, noise_pct=0.0008)
