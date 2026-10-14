"""Support es rezisztencia szintek meghatarozasa.

A szintek ot fuggetlen forrasbol allnak ossze, majd klaszterezes utan
pontszamot kapnak. A kulcs otlet a KONFLUENCIA: az a szint szamit erosnek,
ahol tobb fuggetlen ok is talalkozik - mondjuk egy regi swing alj, egy
volumencsucs es a 200 napos EMA ugyanott van.

Forrasok:
  1. swing pivotok (zigzag + fraktal)
  2. volumen profil (POC, value area, high-volume node-ok)
  3. mozgoatlagok (EMA20/50/200) mint dinamikus szint
  4. kerek szamok (pszichologiai szintek)
  5. korabbi periodus-szelsoertekek (52 hetes, havi, heti csucs/melypont)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from . import structure
from .config import LevelSettings

LevelKind = Literal["support", "resistance"]


@dataclass
class Candidate:
    """Egy szintjelolt, mielott klaszterbe kerulne."""

    price: float
    source: str
    weight: float = 1.0
    date: pd.Timestamp | None = None


@dataclass
class TouchEvent:
    """Egy alkalom, amikor az ar megerintette a szintet."""

    date: pd.Timestamp
    index: int
    from_above: bool      # felulrol kozelitett (support-teszt) vagy alulrol
    reaction_atr: float   # mekkorat fordult a szintrol, ATR-ben


@dataclass
class Level:
    """Egy kesz, pontozott ar-szint."""

    price: float
    kind: LevelKind
    score: float                       # 0-100: STRUKTURALIS erosseg
    touches: int
    sources: list[str] = field(default_factory=list)
    first_touch: pd.Timestamp | None = None
    last_touch: pd.Timestamp | None = None
    avg_reaction_atr: float = 0.0
    flipped: bool = False              # attort rezisztenciabol lett support (vagy forditva)
    zone_low: float = 0.0
    zone_high: float = 0.0
    distance_pct: float = 0.0          # tavolsag az aktualis artol, elojelesen
    relevance: float = 0.0             # score * kozelseg: ez alapjan rangsorolunk

    @property
    def label(self) -> str:
        base = "Support" if self.kind == "support" else "Rezisztencia"
        return f"{base} @ {self.price:.2f}"

    def to_dict(self) -> dict:
        return {
            "price": round(self.price, 4),
            "kind": self.kind,
            "score": round(self.score, 1),
            "relevance": round(self.relevance, 1),
            "touches": self.touches,
            "sources": self.sources,
            "first_touch": self.first_touch,
            "last_touch": self.last_touch,
            "avg_reaction_atr": round(self.avg_reaction_atr, 2),
            "flipped": self.flipped,
            "zone_low": round(self.zone_low, 4),
            "zone_high": round(self.zone_high, 4),
            "distance_pct": round(self.distance_pct, 2),
        }


# ---------------------------------------------------------------------------
# Jeloltek gyujtese
# ---------------------------------------------------------------------------
def _pivot_candidates(df: pd.DataFrame, cfg: LevelSettings, atr_period: int) -> list[Candidate]:
    """Swing pivotok - a legfontosabb forras.

    A zigzag adja a jelentos fordulopontokat, a fraktal a surubb halot.
    A zigzag nagyobb sulyt kap, mert ott bizonyitottan nagyot fordult az ar.
    """
    out: list[Candidate] = []

    for pivot in structure.find_zigzag_pivots(df, 1.5, atr_period):
        if not pivot.confirmed:
            continue
        out.append(Candidate(pivot.price, "swing", 2.0, pivot.date))

    for pivot in structure.find_fractal_pivots(df, 3, 3):
        if not pivot.confirmed:
            continue
        out.append(Candidate(pivot.price, "fraktal", 0.8, pivot.date))

    return out


def volume_profile(df: pd.DataFrame, bins: int = 50) -> pd.DataFrame:
    """Volumen profil: melyik arsavban forgott a legtobb reszveny.

    Minden gyertya volumenet egyenletesen szetteritjuk a sajat H-L
    tartomanyan - ez lenyegesen pontosabb, mint a zaroarhoz rendelni.
    """
    if df.empty:
        return pd.DataFrame(columns=["low", "high", "mid", "volume"])

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if not math.isfinite(price_min) or price_max <= price_min:
        return pd.DataFrame(columns=["low", "high", "mid", "volume"])

    edges = np.linspace(price_min, price_max, bins + 1)
    totals = np.zeros(bins, dtype="float64")

    lows = df["low"].to_numpy(dtype="float64")
    highs = df["high"].to_numpy(dtype="float64")
    volumes = df["volume"].to_numpy(dtype="float64")

    for low, high, volume in zip(lows, highs, volumes):
        if not math.isfinite(volume) or volume <= 0:
            continue
        # Melyik savokat erinti ez a gyertya
        first = np.searchsorted(edges, low, side="right") - 1
        last = np.searchsorted(edges, high, side="left") - 1
        first = max(0, min(first, bins - 1))
        last = max(0, min(last, bins - 1))
        span = last - first + 1
        totals[first : last + 1] += volume / span

    return pd.DataFrame(
        {
            "low": edges[:-1],
            "high": edges[1:],
            "mid": (edges[:-1] + edges[1:]) / 2,
            "volume": totals,
        }
    )


def _volume_candidates(df: pd.DataFrame, cfg: LevelSettings) -> list[Candidate]:
    """POC, value area hatarok es a high-volume node-ok."""
    profile = volume_profile(df, cfg.volume_profile_bins)
    if profile.empty or profile["volume"].sum() <= 0:
        return []

    out: list[Candidate] = []
    poc_row = profile.loc[profile["volume"].idxmax()]
    out.append(Candidate(float(poc_row["mid"]), "POC", 2.0))

    # Value area: a POC-bol kifele novesztve, amig a volumen 70 szazalekat lefedjuk
    order = profile["volume"].sort_values(ascending=False).index
    target = profile["volume"].sum() * 0.70
    accumulated = 0.0
    selected: list[int] = []
    for index in order:
        accumulated += profile.loc[index, "volume"]
        selected.append(index)
        if accumulated >= target:
            break

    if selected:
        va_low = float(profile.loc[min(selected), "low"])
        va_high = float(profile.loc[max(selected), "high"])
        out.append(Candidate(va_low, "value area", 1.2))
        out.append(Candidate(va_high, "value area", 1.2))

    # High-volume node-ok: lokalis volumencsucsok a profilban
    volumes = profile["volume"].to_numpy()
    threshold = np.percentile(volumes[volumes > 0], 80) if (volumes > 0).any() else 0
    for i in range(1, len(volumes) - 1):
        if volumes[i] >= threshold and volumes[i] > volumes[i - 1] and volumes[i] > volumes[i + 1]:
            out.append(Candidate(float(profile.loc[i, "mid"]), "volumen node", 1.0))

    return out


def _moving_average_candidates(df: pd.DataFrame) -> list[Candidate]:
    """Mozgoatlagok mint dinamikus szint.

    Kisebb sulyt kapnak, mert naprol napra elmozdulnak - nem "emlekszik" rajuk
    a piac ugy, mint egy fix arszintre.
    """
    out: list[Candidate] = []
    last = df.iloc[-1]
    for column, label, weight in (
        ("ema20", "EMA20", 0.8),
        ("ema50", "EMA50", 1.0),
        ("ema200", "EMA200", 1.5),
    ):
        value = last.get(column)
        if value is not None and pd.notna(value):
            out.append(Candidate(float(value), label, weight, df.index[-1]))
    return out


def _round_number_candidates(df: pd.DataFrame) -> list[Candidate]:
    """Pszichologiai kerek szamok, az arszinthez skalazott lepeskozzel."""
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    reference = float(df["close"].iloc[-1])

    if reference >= 500:
        step = 50.0
    elif reference >= 200:
        step = 10.0
    elif reference >= 50:
        step = 5.0
    elif reference >= 10:
        step = 1.0
    else:
        step = 0.5

    start = math.floor(price_min / step) * step
    out: list[Candidate] = []
    value = start
    while value <= price_max:
        if value >= price_min:
            out.append(Candidate(round(value, 4), "kerek szam", 0.6))
        value += step
    return out


def _period_extreme_candidates(df: pd.DataFrame) -> list[Candidate]:
    """52 hetes, havi es heti szelsoertekek."""
    out: list[Candidate] = []
    last_date = df.index[-1]

    windows = (
        ("52 hetes", 252, 2.0),
        ("havi", 21, 1.0),
        ("heti", 5, 0.7),
    )
    for label, bars, weight in windows:
        window = df.tail(bars)
        if window.empty:
            continue
        out.append(Candidate(float(window["high"].max()), f"{label} csucs", weight, last_date))
        out.append(Candidate(float(window["low"].min()), f"{label} melypont", weight, last_date))

    return out


# ---------------------------------------------------------------------------
# Klaszterezes es pontozas
# ---------------------------------------------------------------------------
def _cluster(candidates: list[Candidate], tolerance: float) -> list[list[Candidate]]:
    """Ar szerint rendezett jelolteket vonja ossze, ha kozelebb vannak
    egymashoz a turesnel.

    A klaszter akkor zarul, ha a kovetkezo jelolt tavolabb van a klaszter
    SULYOZOTT KOZEPETOL - igy egy hosszu lanc nem tud vegtelenul elkuszni.
    """
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda c: c.price)
    clusters: list[list[Candidate]] = [[ordered[0]]]

    for candidate in ordered[1:]:
        current = clusters[-1]
        centre = sum(c.price * c.weight for c in current) / sum(c.weight for c in current)
        if abs(candidate.price - centre) <= tolerance:
            current.append(candidate)
        else:
            clusters.append([candidate])

    return clusters


def _touch_events(
    df: pd.DataFrame, price: float, tolerance: float, atr: pd.Series
) -> list[TouchEvent]:
    """Megkeresi, mikor erintette az ar a szintet, es mekkorat fordult rola.

    Az egymast koveto erinto gyertyak EGY esemenynek szamitanak - kulonben egy
    het oldalazas a szinten tiz kulon "erintesnek" latszana.
    """
    zone_low, zone_high = price - tolerance, price + tolerance
    touching = (df["low"] <= zone_high) & (df["high"] >= zone_low)
    positions = np.flatnonzero(touching.to_numpy())
    if positions.size == 0:
        return []

    # Osszefuggo blokkok szetvalasztasa
    breaks = np.flatnonzero(np.diff(positions) > 1) + 1
    groups = np.split(positions, breaks)

    closes = df["close"].to_numpy(dtype="float64")
    highs = df["high"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    atr_values = atr.bfill().to_numpy(dtype="float64")
    n = len(df)

    events: list[TouchEvent] = []
    for group in groups:
        start, end = int(group[0]), int(group[-1])

        # Honnan erkezett az ar? A blokk elotti zaroar donti el.
        from_above = closes[start - 1] > price if start > 0 else closes[start] > price

        # Reakcio: az erintest koveto 10 gyertyan mekkorat mozdult el a szinttol
        horizon = min(end + 10, n - 1)
        if horizon > end:
            if from_above:
                extreme = float(np.max(highs[end + 1 : horizon + 1]))
                move = extreme - price
            else:
                extreme = float(np.min(lows[end + 1 : horizon + 1]))
                move = price - extreme
        else:
            move = 0.0

        scale = atr_values[end] if atr_values[end] > 0 else 1.0
        events.append(
            TouchEvent(
                date=df.index[start],
                index=start,
                from_above=from_above,
                reaction_atr=max(move / scale, 0.0),
            )
        )

    return events


def _detect_flip(
    df: pd.DataFrame,
    price: float,
    events: list[TouchEvent],
    atr: pd.Series,
) -> bool:
    """Valodi polaritasvaltas: HATAROZOTT attores, majd ujrateszt a masik oldalrol.

    Nem eleg, hogy a szintet valamikor felulrol is, alulrol is megerintettek -
    egy 500 gyertyas ablakban ez szinte minden szintre igaz, es ertektelenne
    teszi a jelolest (a gyakorlatban a szintek 90 szazalekat "flippelte" volna).

    Ket szigoritas teszi hasznalhatova:
      1. csak a LEGUTOLSO negy erintest nezzuk - a polaritasvaltas akkor
         szamit, ha a szint MOST viselkedik maskepp, nem ha ket eve attortek;
      2. a toreshez harom egymast koveto zaras kell a tuloldalon, legalabb
         1 ATR tavolsagra - ez mar valodi attores, nem atszuras.
    """
    recent = events[-4:]
    if len(recent) < 2:
        return False

    closes = df["close"].to_numpy(dtype="float64")
    atr_values = atr.bfill().to_numpy(dtype="float64")

    for previous, current in zip(recent, recent[1:]):
        if previous.from_above == current.from_above:
            continue

        segment = slice(previous.index, current.index + 1)
        margin = float(np.nanmedian(atr_values[segment]) or 0.0)
        if margin <= 0:
            continue

        # A korabbi erintes iranya donti el, merre kell attornie az arnak
        beyond = (
            closes[segment] < price - margin
            if previous.from_above
            else closes[segment] > price + margin
        )
        # Harom egymast koveto zaras a tuloldalon
        if beyond.size >= 3 and np.any(beyond[:-2] & beyond[1:-1] & beyond[2:]):
            return True

    return False


def _score(
    events: list[TouchEvent],
    sources: set[str],
    cluster_volume_rank: float,
    last_date: pd.Timestamp,
    halflife_days: int,
    lookback_bars: int,
) -> float:
    """0-100 STRUKTURALIS pontszam hat komponensbol.

    A sulyokat ugy valasztottuk, hogy az erintesek szama es a reakcio erossege
    domanaljon - ezek mondjak meg, hogy a piac tenylegesen reagal-e a szintre.

    A telitesi hatarok szandekosan magasak (8 erintes, 4 ATR reakcio): egy
    500 gyertyas ablakban szinte minden szint osszegyujt 5-6 erintest, es ha
    ott telitodnenk, minden szint 90+ pontot kapna - vagyis a pontszam nem
    kulonboztetne meg semmit.
    """
    if not events:
        return 0.0

    # 1. Erintesek szama
    touch_score = min(len(events) / 8.0, 1.0)

    # 2. Frissesseg - exponencialis lecsenges
    days_since = max((last_date - events[-1].date).days, 0)
    recency_score = math.exp(-days_since / max(halflife_days, 1))

    # 3. Reakcio erossege
    avg_reaction = sum(e.reaction_atr for e in events) / len(events)
    reaction_score = min(avg_reaction / 4.0, 1.0)

    # 4. Konfluencia - hany fuggetlen forras esik egybe
    confluence_score = min(len(sources) / 4.0, 1.0)

    # 5. Elettartam - mennyi idot fog at a szint
    span_bars = events[-1].index - events[0].index
    span_score = min(span_bars / max(lookback_bars * 0.6, 1), 1.0)

    # 6. Volumen a szinten
    volume_score = cluster_volume_rank

    total = (
        0.25 * touch_score
        + 0.15 * recency_score
        + 0.20 * reaction_score
        + 0.15 * confluence_score
        + 0.10 * span_score
        + 0.15 * volume_score
    )
    return round(total * 100, 2)


def _merge_overlapping(levels: list[Level], tolerance: float) -> list[Level]:
    """Az egymasba ero szintek osszevonasa.

    A klaszterezes utan is maradhatnak gyakorlatilag azonos szintek (pl. 305,
    310 es 317 ugyanaz a kinalati zona). Ezeket osszevonjuk: az erosebb nyer,
    de atveszi a gyengebb forrasait es kiterjeszti a zonajat.
    """
    if not levels:
        return []

    merged: list[Level] = []
    for level in sorted(levels, key=lambda l: l.score, reverse=True):
        overlapping = next(
            (m for m in merged if abs(m.price - level.price) <= tolerance * 1.5), None
        )
        if overlapping is None:
            merged.append(level)
            continue

        overlapping.sources = sorted(set(overlapping.sources) | set(level.sources))
        overlapping.zone_low = min(overlapping.zone_low, level.zone_low)
        overlapping.zone_high = max(overlapping.zone_high, level.zone_high)
        overlapping.touches = max(overlapping.touches, level.touches)
        overlapping.flipped = overlapping.flipped or level.flipped

    return merged


def find_levels(
    df: pd.DataFrame,
    cfg: LevelSettings,
    atr_period: int = 14,
) -> list[Level]:
    """A teljes szintkereso futtatasa egy bovitett OHLCV tablan.

    A `df`-nek tartalmaznia kell az indicators.enrich() altal adott
    oszlopokat (atr, ema20/50/200).
    """
    if df.empty or len(df) < 20:
        return []

    window = df.tail(cfg.lookback_bars).copy()
    current_price = float(window["close"].iloc[-1])
    last_date = window.index[-1]

    atr_series = window["atr"] if "atr" in window.columns else None
    if atr_series is None:
        from . import indicators
        atr_series = indicators.atr(window, atr_period)

    atr_now = float(atr_series.bfill().iloc[-1])
    tolerance = max(cfg.cluster_atr_mult * atr_now, current_price * cfg.cluster_min_pct)

    candidates: list[Candidate] = []
    candidates += _pivot_candidates(window, cfg, atr_period)
    candidates += _volume_candidates(window, cfg)
    candidates += _moving_average_candidates(window)
    if cfg.round_number_enabled:
        candidates += _round_number_candidates(window)
    candidates += _period_extreme_candidates(window)

    clusters = _cluster(candidates, tolerance)
    if not clusters:
        return []

    # A volumen-rangsorhoz a klaszter kornyeken forgott volument nezzuk
    profile = volume_profile(window, cfg.volume_profile_bins)
    max_bin_volume = float(profile["volume"].max()) if not profile.empty else 0.0

    levels: list[Level] = []
    for cluster in clusters:
        total_weight = sum(c.weight for c in cluster)
        price = sum(c.price * c.weight for c in cluster) / total_weight
        sources = sorted({c.source for c in cluster})

        events = _touch_events(window, price, tolerance, atr_series)
        if not events:
            continue

        volume_rank = 0.0
        if max_bin_volume > 0 and not profile.empty:
            nearest = (profile["mid"] - price).abs().idxmin()
            volume_rank = float(profile.loc[nearest, "volume"]) / max_bin_volume

        score = _score(
            events, set(sources), volume_rank, last_date,
            cfg.recency_halflife_days, cfg.lookback_bars,
        )

        flipped = _detect_flip(window, price, events, atr_series)
        if flipped:
            # A polaritast valtott szintek a gyakorlatban megbizhatobbak
            score = min(score * 1.15, 100.0)

        distance_pct = (price - current_price) / current_price * 100.0

        levels.append(
            Level(
                price=price,
                kind="support" if price < current_price else "resistance",
                score=score,
                touches=len(events),
                sources=sources,
                first_touch=events[0].date,
                last_touch=events[-1].date,
                avg_reaction_atr=sum(e.reaction_atr for e in events) / len(events),
                flipped=flipped,
                zone_low=price - tolerance,
                zone_high=price + tolerance,
                distance_pct=distance_pct,
                # Relevancia = strukturalis erosseg * kozelseg. Egy 30 szazalekkal
                # lejjebb levo, betonbiztos szint strukturalisan eros, de swing
                # idotavon hasznalhatatlan - a rangsorolasnak ezt tukroznie kell.
                relevance=score * math.exp(-abs(distance_pct) / 12.0),
            )
        )

    levels = _merge_overlapping(levels, tolerance)
    levels.sort(key=lambda level: level.relevance, reverse=True)
    return levels[: cfg.max_levels]


def nearest_support(levels: list[Level]) -> Level | None:
    """A legkozelebbi support az aktualis ar ALATT (a legkisebb tavolsagu)."""
    supports = [level for level in levels if level.kind == "support"]
    return max(supports, key=lambda level: level.price) if supports else None


def nearest_resistance(levels: list[Level]) -> Level | None:
    """A legkozelebbi rezisztencia az aktualis ar FOLOTT."""
    resistances = [level for level in levels if level.kind == "resistance"]
    return min(resistances, key=lambda level: level.price) if resistances else None


def strongest(levels: list[Level], kind: LevelKind | None = None) -> Level | None:
    pool = [l for l in levels if kind is None or l.kind == kind]
    return max(pool, key=lambda level: level.score) if pool else None
