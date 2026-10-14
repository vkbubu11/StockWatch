"""Irany-elorejelzes: a jelek osszegzese egyetlen, ertelmezheto kimenette.

Ot fuggetlen komponenst sulyozunk ossze egy -1..+1 kompozit pontszamma:

  1. formacio      - a legerosebb aktiv minta iranya es minosege
  2. trend         - EMA-sorrend, EMA50 meredekseg, ADX
  3. momentum      - RSI, MACD hisztogram, RSI-divergencia
  4. szintek       - tavolsag a legkozelebbi supporttol / rezisztenciatol
  5. volumen       - OBV meredekseg, kitoresi volumen

FONTOS - a valoszinuseg NEM a pontszambol jon!

  A kompozit pontszam iranyt es meggyozodest fejez ki, nem valoszinuseget.
  A megjelenitett szazalek kizarolag a BACKTEST altal mert, historikus
  talalati aranybol szarmazik (calibration.json). Ha egy formaciora nincs
  eleg historikus minta, akkor "nincs eleg adat" szerepel - nem talalunk ki
  szamot. Ez a kulonbseg egy hasznalhato eszkoz es egy joslogep kozott.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import levels as levels_mod
from . import patterns as patterns_mod
from .config import Settings
from .patterns import candles
from .patterns.base import PatternMatch
from .structure import find_zigzag_pivots

log = logging.getLogger(__name__)

# A kompozit pontszam cimkehatarai
LABELS = (
    (0.45, "Eros emelkedo"),
    (0.15, "Emelkedo"),
    (-0.15, "Semleges"),
    (-0.45, "Csokkeno"),
    (-1.01, "Eros csokkeno"),
)


@dataclass
class Component:
    """Egy reszpontszam a magyarazathoz.

    A `reason` mezo teszi a rendszert atlathatova: a felhasznalo lathatja,
    MIERT ad a szoftver adott iranyt, nem csak azt, hogy mit.
    """

    name: str
    score: float          # -1..+1
    weight: float
    reason: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass
class Forecast:
    """Egy papir teljes elemzese."""

    symbol: str
    as_of: pd.Timestamp
    price: float
    score: float                       # -1..+1 kompozit
    label: str
    components: list[Component] = field(default_factory=list)

    pattern: PatternMatch | None = None
    probability: float | None = None   # kizarolag backtestbol; None = nincs eleg adat
    sample_size: int = 0
    expected_bars: int | None = None

    target: float | None = None
    invalidation: float | None = None
    risk_reward: float | None = None
    low_quality: bool = False          # R:R a kuszob alatt

    support: levels_mod.Level | None = None
    resistance: levels_mod.Level | None = None
    all_levels: list[levels_mod.Level] = field(default_factory=list)
    matches: list[PatternMatch] = field(default_factory=list)   # minden talalat (a belepesi motorhoz)
    warnings: list[str] = field(default_factory=list)

    @property
    def direction(self) -> str:
        if self.score > 0.15:
            return "bullish"
        if self.score < -0.15:
            return "bearish"
        return "neutral"

    @property
    def probability_text(self) -> str:
        """A valoszinuseg emberi olvasatra - vagy az oszinte "nem tudjuk"."""
        if self.probability is None:
            return "nincs eleg adat"
        return f"{self.probability:.0%} ({self.sample_size} historikus minta)"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "price": round(self.price, 4),
            "score": round(self.score, 3),
            "label": self.label,
            "direction": self.direction,
            "probability": self.probability,
            "probability_text": self.probability_text,
            "sample_size": self.sample_size,
            "expected_bars": self.expected_bars,
            "target": round(self.target, 4) if self.target else None,
            "invalidation": round(self.invalidation, 4) if self.invalidation else None,
            "risk_reward": round(self.risk_reward, 2) if self.risk_reward else None,
            "low_quality": self.low_quality,
            "pattern": self.pattern.to_dict() if self.pattern else None,
            "support": self.support.to_dict() if self.support else None,
            "resistance": self.resistance.to_dict() if self.resistance else None,
            "components": [
                {"name": c.name, "score": round(c.score, 3), "weight": c.weight, "reason": c.reason}
                for c in self.components
            ],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Kalibracio
# ---------------------------------------------------------------------------
class Calibration:
    """A backtest altal mert talalati aranyok.

    Ures kalibracio (meg nem futott backtest) eseten minden lekerdezes None-t
    ad - vagyis a rendszer bevallja, hogy nem tudja. Ez szandekos: inkabb ne
    mondjunk szamot, mint hogy kitalaljunk egyet.
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        if not path.exists():
            log.info("Nincs kalibracios fajl (%s) - valoszinuseg nelkul futunk.", path)
            return cls()
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            log.warning("Serult kalibracios fajl: %s", path)
            return cls()

    def lookup(self, pattern_name: str, direction: str, quality: float, min_samples: int):
        """Talalati arany, mintaszam es medián horizont egy formaciora.

        A minoseg szerinti bontast csak akkor hasznaljuk, ha abban is van eleg
        minta; kulonben visszaesunk az osszevont statisztikara.
        """
        entry = self.data.get("patterns", {}).get(f"{pattern_name}|{direction}")
        if not entry:
            return None, 0, None

        for bucket in entry.get("buckets", []):
            if bucket["min_quality"] <= quality < bucket["max_quality"]:
                if bucket["samples"] >= min_samples:
                    return bucket["hit_rate"], bucket["samples"], bucket.get("median_bars")
                break

        samples = entry.get("samples", 0)
        if samples >= min_samples:
            return entry.get("hit_rate"), samples, entry.get("median_bars")
        return None, samples, None


# ---------------------------------------------------------------------------
# Komponensek
# ---------------------------------------------------------------------------
def _pattern_component(match: PatternMatch | None, weights: dict) -> Component:
    if match is None:
        return Component("Formacio", 0.0, weights["pattern"], "Nincs aktiv formacio")

    sign = 1.0 if match.direction == "bullish" else -1.0 if match.direction == "bearish" else 0.0
    # Az aktivalt minta erosebb jelzes, mint a meg csak formalodo
    strength = match.quality * (1.0 if match.status == "triggered" else 0.6)

    return Component(
        "Formacio",
        sign * strength,
        weights["pattern"],
        f"{match.name} ({match.status}), minoseg {match.quality:.2f}",
    )


def _trend_component(df: pd.DataFrame, weights: dict) -> Component:
    last = df.iloc[-1]
    price = float(last["close"])
    parts: list[float] = []
    notes: list[str] = []

    ema20, ema50, ema200 = last.get("ema20"), last.get("ema50"), last.get("ema200")

    if pd.notna(ema20) and pd.notna(ema50) and pd.notna(ema200):
        if ema20 > ema50 > ema200 and price > ema20:
            parts.append(1.0)
            notes.append("teljes bullish EMA-sorrend")
        elif ema20 < ema50 < ema200 and price < ema20:
            parts.append(-1.0)
            notes.append("teljes bearish EMA-sorrend")
        else:
            partial = np.sign(price - ema50) * 0.4
            parts.append(float(partial))
            notes.append("vegyes EMA-kep")
    elif pd.notna(ema50):
        parts.append(float(np.sign(price - ema50)) * 0.5)
        notes.append("rovid historia, csak EMA50")

    slope = last.get("ema50_slope")
    if pd.notna(slope):
        parts.append(float(np.clip(slope / 8.0, -1.0, 1.0)))
        notes.append(f"EMA50 meredekseg {slope:+.1f}%")

    score = float(np.mean(parts)) if parts else 0.0

    # Az ADX nem iranyt ad, hanem a trend EROSSEGET - ezzel skalazunk.
    # Gyenge trendben (ADX < 20) a trendkomponens keveset er.
    adx = last.get("adx")
    if pd.notna(adx):
        score *= float(np.clip(adx / 25.0, 0.3, 1.2))
        notes.append(f"ADX {adx:.0f}")

    return Component("Trend", float(np.clip(score, -1, 1)), weights["trend"], ", ".join(notes))


def _momentum_component(df: pd.DataFrame, weights: dict) -> Component:
    last = df.iloc[-1]
    parts: list[float] = []
    notes: list[str] = []

    rsi = last.get("rsi")
    if pd.notna(rsi):
        # 50 korul semleges; a szelsoertekek fele no a jel
        parts.append(float(np.clip((rsi - 50) / 25.0, -1.0, 1.0)))
        if rsi > 70:
            notes.append(f"RSI {rsi:.0f} (tulvett)")
        elif rsi < 30:
            notes.append(f"RSI {rsi:.0f} (tuladott)")
        else:
            notes.append(f"RSI {rsi:.0f}")

    hist = last.get("hist")
    if pd.notna(hist) and pd.notna(last.get("atr")) and last["atr"] > 0:
        parts.append(float(np.clip(hist / (last["atr"] * 0.5), -1.0, 1.0)))
        notes.append(f"MACD hiszt. {hist:+.2f}")

    divergence, text = _rsi_divergence(df)
    if divergence:
        parts.append(divergence)
        notes.append(text)

    score = float(np.mean(parts)) if parts else 0.0
    return Component("Momentum", score, weights["momentum"], ", ".join(notes))


def _rsi_divergence(df: pd.DataFrame, lookback: int = 120) -> tuple[float, str]:
    """RSI-divergencia az utolso ket azonos tipusu swing pivot kozott.

    Bullish divergencia: az ar alacsonyabb aljat csinal, az RSI viszont
    magasabbat - a lefele nyomas fogy. Ez az egyik legkorabbi fordulos jel.
    """
    window = df.tail(lookback)
    if len(window) < 40 or "rsi" not in window.columns:
        return 0.0, ""

    pivots = [p for p in find_zigzag_pivots(window, 1.5, 14) if p.confirmed]
    rsi_values = window["rsi"].to_numpy()

    lows = [p for p in pivots if not p.is_high][-2:]
    if len(lows) == 2 and lows[0].price > 0:
        price_lower = lows[1].price < lows[0].price * 0.99
        rsi_higher = rsi_values[lows[1].index] > rsi_values[lows[0].index] + 2
        if price_lower and rsi_higher:
            return 0.7, "bullish RSI-divergencia"

    highs = [p for p in pivots if p.is_high][-2:]
    if len(highs) == 2 and highs[0].price > 0:
        price_higher = highs[1].price > highs[0].price * 1.01
        rsi_lower = rsi_values[highs[1].index] < rsi_values[highs[0].index] - 2
        if price_higher and rsi_lower:
            return -0.7, "bearish RSI-divergencia"

    return 0.0, ""


def _levels_component(
    price: float,
    support: levels_mod.Level | None,
    resistance: levels_mod.Level | None,
    atr: float,
    weights: dict,
) -> Component:
    """Eros support kozelsege bullish, eros rezisztencia kozelsege bearish.

    A hatas ATR-ben merve cseng le: egy 5 ATR-re levo szint ma mar nem
    befolyasolja az arat.
    """
    score = 0.0
    notes: list[str] = []

    if support is not None and atr > 0:
        distance_atr = (price - support.price) / atr
        proximity = float(np.clip(1.0 - distance_atr / 4.0, 0.0, 1.0))
        score += proximity * (support.score / 100.0)
        notes.append(f"support {support.price:.2f} ({distance_atr:.1f} ATR, {support.score:.0f}p)")

    if resistance is not None and atr > 0:
        distance_atr = (resistance.price - price) / atr
        proximity = float(np.clip(1.0 - distance_atr / 4.0, 0.0, 1.0))
        score -= proximity * (resistance.score / 100.0)
        notes.append(f"rezisztencia {resistance.price:.2f} ({distance_atr:.1f} ATR, {resistance.score:.0f}p)")

    if resistance is None:
        notes.append("nincs rezisztencia felette (csucson az ar)")
        score += 0.3

    return Component("Szintek", float(np.clip(score, -1, 1)), weights["levels"], ", ".join(notes))


def _volume_component(df: pd.DataFrame, weights: dict) -> Component:
    last = df.iloc[-1]
    parts: list[float] = []
    notes: list[str] = []

    obv_slope = last.get("obv_slope")
    if pd.notna(obv_slope):
        parts.append(float(np.clip(obv_slope / 20.0, -1.0, 1.0)))
        notes.append(f"OBV {obv_slope:+.1f}%")

    ratio = last.get("vol_ratio")
    if pd.notna(ratio):
        # A magas volumen az AKTUALIS gyertya iranyat erositi
        direction = np.sign(last["close"] - last["open"])
        parts.append(float(np.clip((ratio - 1.0), -1.0, 1.0) * direction))
        notes.append(f"volumen {ratio:.1f}x")

    score = float(np.mean(parts)) if parts else 0.0
    return Component("Volumen", score, weights["volume"], ", ".join(notes))


# ---------------------------------------------------------------------------
# Fo belepesi pont
# ---------------------------------------------------------------------------
def analyse(
    symbol: str,
    df: pd.DataFrame,
    settings: Settings,
    calibration: Calibration | None = None,
) -> Forecast:
    """Egy papir teljes elemzese: szintek, formaciok, elorejelzes."""
    calibration = calibration or Calibration()
    weights = settings.forecast.weights.normalised()

    price = float(df["close"].iloc[-1])
    atr = float(df["atr"].iloc[-1]) if pd.notna(df["atr"].iloc[-1]) else price * 0.02

    found_levels = levels_mod.find_levels(df, settings.levels, settings.structure.atr_period)
    support = levels_mod.nearest_support(found_levels)
    resistance = levels_mod.nearest_resistance(found_levels)

    matches = patterns_mod.detect(symbol, df, settings.patterns, settings.structure)
    best = patterns_mod.strongest(matches)

    components = [
        _pattern_component(best, weights),
        _trend_component(df, weights),
        _momentum_component(df, weights),
        _levels_component(price, support, resistance, atr, weights),
        _volume_component(df, weights),
    ]

    score = sum(c.contribution for c in components)

    # A gyertyaalakzatok csak finomhangolnak, sulyuk szandekosan kicsi
    if best is not None:
        candle_adjust = candles.confirmation_score(df, best.direction) * 0.05
        score += candle_adjust if best.direction == "bullish" else -candle_adjust

    score = float(np.clip(score, -1.0, 1.0))
    label = next(text for threshold, text in LABELS if score >= threshold)

    forecast = Forecast(
        symbol=symbol,
        as_of=df.index[-1],
        price=price,
        score=score,
        label=label,
        components=components,
        pattern=best,
        support=support,
        resistance=resistance,
        all_levels=found_levels,
        matches=matches,
    )

    if best is not None:
        forecast.target = best.target
        forecast.invalidation = best.invalidation
        forecast.risk_reward = best.risk_reward
        forecast.low_quality = best.risk_reward < settings.forecast.min_risk_reward

        hit_rate, samples, median_bars = calibration.lookup(
            best.name,
            best.direction,
            best.quality,
            settings.forecast.min_samples_for_probability,
        )
        forecast.probability = hit_rate
        forecast.sample_size = samples
        forecast.expected_bars = median_bars

        if hit_rate is None:
            forecast.warnings.append(
                f"Nincs eleg historikus minta ({samples} db) a valoszinuseg becslesehez - "
                "futtasd a backtestet."
            )
        if forecast.low_quality:
            forecast.warnings.append(
                f"Gyenge hozam/kockazat arany ({best.risk_reward:.2f}), "
                f"a kuszob {settings.forecast.min_risk_reward}"
            )
    else:
        # Formacio nelkul a legkozelebbi szintek adjak a technikai kereteket
        forecast.target = resistance.price if resistance else None
        forecast.invalidation = support.price if support else None

    return forecast
