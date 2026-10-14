"""A formaciofelismeres kozos szerzodese.

Minden detektor ugyanazt a `PatternMatch` objektumot adja vissza, igy a
pontozo motor, a dashboard, a riaszto es a backtest egyseges modon kezeli
mindegyik formaciot - egy uj mintatipus hozzaadasa nem igenyel valtoztatast
a rendszer tobbi reszen.

A detektorok PIVOTOKON dolgoznak, nem nyers gyertyakon. Ez teszi oket
robusztussa: egy fej-vall akkor is fej-vall, ha kozben van nehany zajos nap.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np
import pandas as pd

from ..config import PatternSettings
from ..structure import Pivot

Direction = Literal["bullish", "bearish", "neutral"]
Status = Literal["forming", "triggered", "target_hit", "failed"]

# Ennyi gyertyanal regebben vegzodott formaciot mar nem jelentunk aktivkent -
# egy fel eve lezarult haromszog nem ad mai belepesi jelzest.
#
# A 30 gyertya (kb. masfel honap) szandekosan nem szorosabb: egy kitores utan
# az ar meg napokig fut a cel fele, es ilyenkor is akarjuk latni a mintat -
# csak mar nem friss belepeskent, hanem "triggered" allapotban.
MAX_BARS_SINCE_END = 30


@dataclass
class PatternMatch:
    """Egy felismert chart formacio."""

    symbol: str
    name: str
    direction: Direction
    status: Status
    quality: float                              # 0-1 geometriai illeszkedes
    trigger_level: float                        # ennek attorese aktivalja
    target: float                               # measured move celar
    invalidation: float                         # itt bukott meg a minta
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    key_points: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    trigger_date: pd.Timestamp | None = None
    notes: str = ""
    extras: dict = field(default_factory=dict)  # rajzolashoz (nyakvonal, trendvonalak)

    @property
    def risk_reward(self) -> float:
        """Hozam/kockazat arany a trigger szinttol szamitva."""
        reward = abs(self.target - self.trigger_level)
        risk = abs(self.trigger_level - self.invalidation)
        return reward / risk if risk > 0 else 0.0

    @property
    def target_pct(self) -> float:
        if self.trigger_level == 0:
            return 0.0
        return (self.target - self.trigger_level) / self.trigger_level * 100.0

    @property
    def bars(self) -> int:
        return len(self.key_points)

    def fingerprint(self) -> str:
        """Stabil azonosito a duplikatum-szureshez.

        Az arszinteket kerekitjuk, kulonben egy centes elmozdulas mar uj
        jelzesnek latszana, es a monitor 15 percenkent ujrariasztana.
        """
        raw = (
            f"{self.symbol}|{self.name}|{self.direction}|"
            f"{self.trigger_level:.2f}|{self.start_date:%Y-%m-%d}"
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "direction": self.direction,
            "status": self.status,
            "quality": round(self.quality, 3),
            "trigger_level": round(self.trigger_level, 4),
            "target": round(self.target, 4),
            "invalidation": round(self.invalidation, 4),
            "risk_reward": round(self.risk_reward, 2),
            "target_pct": round(self.target_pct, 2),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "trigger_date": self.trigger_date,
            "key_points": [(d, round(p, 4)) for d, p in self.key_points],
            "notes": self.notes,
            "fingerprint": self.fingerprint(),
        }


@dataclass
class Context:
    """Minden, amit egy detektor kaphat.

    Egyszer szamoljuk ki, es minden detektor ugyanazt hasznalja - igy nem
    fut le tizszer ugyanaz a pivotkereses.
    """

    symbol: str
    df: pd.DataFrame          # indicators.enrich() kimenete
    pivots: list[Pivot]       # valtakozo H-L sorozat
    cfg: PatternSettings

    @property
    def last_index(self) -> int:
        return len(self.df) - 1

    @property
    def atr(self) -> float:
        value = self.df["atr"].iloc[-1]
        return float(value) if pd.notna(value) else float(self.df["close"].iloc[-1]) * 0.02

    def atr_at(self, index: int) -> float:
        value = self.df["atr"].iloc[index]
        if pd.notna(value) and value > 0:
            return float(value)
        return float(self.df["close"].iloc[index]) * 0.02

    def tolerance(self, index: int) -> float:
        """Az arak egyezesenek turése az adott ponton, ATR-ben skalazva."""
        return self.cfg.tolerance_atr_mult * self.atr_at(index)

    def date(self, index: int) -> pd.Timestamp:
        return self.df.index[index]

    def close(self, index: int) -> float:
        return float(self.df["close"].iloc[index])


# Egy detektor: kap egy kontextust, ad nulla vagy tobb talalatot.
Detector = Callable[[Context], list[PatternMatch]]

_REGISTRY: list[Detector] = []


def register(detector: Detector) -> Detector:
    """Dekorator: a detektor bekerul a globalis regiszterbe."""
    _REGISTRY.append(detector)
    return detector


def all_detectors() -> list[Detector]:
    return list(_REGISTRY)


# ---------------------------------------------------------------------------
# Kozos geometriai segedek
# ---------------------------------------------------------------------------
def pct_diff(a: float, b: float) -> float:
    """Ket ar elteresenek abszolut erteke szazalekban."""
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b) * 100.0


def similar(a: float, b: float, tolerance: float) -> bool:
    """Ket arszint gyakorlatilag azonos-e (abszolut turés)."""
    return abs(a - b) <= tolerance


def is_flat(slope_pct_per_bar: float, threshold: float = 0.08) -> bool:
    """Egy trendvonal vizszintesnek szamit-e.

    A kuszob gyertyankenti szazalekban ertendo: 0.08 azt jelenti, hogy 20
    gyertya alatt kevesebb mint 1.6 szazalekot mozdul a vonal.
    """
    return abs(slope_pct_per_bar) < threshold


def breakout_index(
    df: pd.DataFrame, level: float, start: int, direction: Direction
) -> int | None:
    """Az elso gyertya `start` utan, amely ZAROARON attori a szintet.

    Szandekosan zaroarat nezunk, nem intraday szurast: a napon beluli
    atszuras a leggyakoribb forrasa a hamis kitoresi jelzeseknek.
    """
    if start >= len(df) - 1:
        return None

    closes = df["close"].to_numpy(dtype="float64")[start + 1 :]
    if direction == "bullish":
        hits = np.flatnonzero(closes > level)
    elif direction == "bearish":
        hits = np.flatnonzero(closes < level)
    else:
        return None

    return int(hits[0]) + start + 1 if hits.size else None


def breakout_index_line(
    df: pd.DataFrame,
    line_values: np.ndarray,
    start: int,
    direction: Direction,
) -> int | None:
    """Ugyanaz, mint a `breakout_index`, de DOLT vonalhoz.

    A fej-vall nyakvonala es a haromszogek trendvonalai ritkan vizszintesek,
    ezert gyertyankent valtozo szinthez kell hasonlitani. A `line_values` a
    teljes DataFrame-mel azonos hosszu tomb.
    """
    if start >= len(df) - 1:
        return None

    closes = df["close"].to_numpy(dtype="float64")[start + 1 :]
    levels = line_values[start + 1 :]

    if direction == "bullish":
        hits = np.flatnonzero(closes > levels)
    elif direction == "bearish":
        hits = np.flatnonzero(closes < levels)
    else:
        return None

    return int(hits[0]) + start + 1 if hits.size else None


def volume_confirmation(df: pd.DataFrame, index: int) -> float:
    """Kitoresi volumen a 20 napos atlaghoz kepest, 0-1 pontszamma alakitva.

    Az 1.5x atlagos volumen szamit meggyozo kitoresnek; e folott telitodik.
    """
    if "vol_ratio" not in df.columns or index >= len(df):
        return 0.5
    ratio = df["vol_ratio"].iloc[index]
    if pd.isna(ratio):
        return 0.5
    return float(np.clip((ratio - 0.8) / 0.7, 0.0, 1.0))


def volume_dryup(df: pd.DataFrame, start: int, end: int) -> float:
    """A formalodas alatti volumen-szaradas, 0-1 pontszamkent.

    A klasszikus tankonyvi viselkedes: a konszolidacio alatt apad a volumen,
    majd a kitoresnel megugrik. Ez erosen novel egy minta megbizhatosagat.
    """
    if "volume" not in df.columns or end <= start or end >= len(df):
        return 0.5

    segment = df["volume"].iloc[start : end + 1]
    if len(segment) < 4 or segment.isna().all():
        return 0.5

    half = len(segment) // 2
    first_half = segment.iloc[:half].mean()
    second_half = segment.iloc[half:].mean()
    if not first_half or pd.isna(first_half) or pd.isna(second_half):
        return 0.5

    # 1.0, ha a masodik fel volumene feleakkora; 0.0, ha megduplazodott
    ratio = second_half / first_half
    return float(np.clip((1.3 - ratio) / 0.8, 0.0, 1.0))


def duration_score(bars: int, ideal_low: int, ideal_high: int) -> float:
    """Az idotartam pontszama: 1.0 az ideali savban, kifele lecseng."""
    if bars <= 0:
        return 0.0
    if ideal_low <= bars <= ideal_high:
        return 1.0
    if bars < ideal_low:
        return max(0.0, bars / ideal_low)
    return max(0.0, 1.0 - (bars - ideal_high) / (ideal_high * 2.0))


def symmetry_score(a: float, b: float) -> float:
    """Ket ertek (pl. ket vall magassaga) szimmetriaja, 0-1."""
    if a <= 0 or b <= 0:
        return 0.0
    return float(min(a, b) / max(a, b))


def resolve_status(
    ctx: Context, match_end: int, trigger: float, direction: Direction
) -> tuple[Status, pd.Timestamp | None]:
    """Eldonti, hogy a minta meg alakul, vagy mar aktivalodott.

    A `target_hit` / `failed` allapotot NEM itt allapitjuk meg: azt a monitor
    allapotgepe koveti a kesobbi gyertyakon, illetve a backtest szamolja ki.
    """
    index = breakout_index(ctx.df, trigger, match_end, direction)
    if index is None:
        return "forming", None
    return "triggered", ctx.date(index)


def recent_enough(ctx: Context, end_index: int) -> bool:
    """Eleg friss-e a formacio ahhoz, hogy mai jelzest adjon."""
    return ctx.last_index - end_index <= MAX_BARS_SINCE_END


def prior_move_atr(ctx: Context, start_index: int, lookback: int = 30) -> float:
    """A formacio ELOTTI elmozdulas ATR-ben, elojelesen.

    Ez a legerosebb hamis-pozitiv szuro. Egy dupla alj definicio szerint
    FORDULOS minta: csak akkor jelent valamit, ha elotte tenyleges esés volt.
    A veletlen zajban talalt "dupla alj" pont ezen bukik el - nincs mit
    megfordítani.
    """
    reference = max(start_index - lookback, 0)
    if reference >= start_index:
        return 0.0

    move = ctx.close(start_index) - ctx.close(reference)
    atr = ctx.atr_at(start_index)
    return move / atr if atr > 0 else 0.0
