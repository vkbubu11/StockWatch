"""Historikus kalibracio: ez teszi az elorejelzest hitelesse.

A rendszer NEM talal ki valoszinusegeket. Vegigjatsszuk a historiat,
minden gyertyanal ujra lefuttatjuk a detektorokat - kizarolag az addigi
adatbol -, majd elore kovetjuk, hogy a minta elerte-e a celt az invalidacio
elott. Az igy mert talalati arany kerul a felhasznalo ele.

LOOK-AHEAD VEDELEM - a modul legfontosabb resze:

  1. `confirmed_only=True`: a meg nem erositett (utolso) pivot nem hasznalhato.
     Enelkul a detektor "latna" a jovobeli visszafordulast.
  2. A detektor csak a `df.iloc[:i+1]` szeletet kapja meg.
  3. Az indikatorokat egyszer szamoljuk a teljes sorozaton, de mindegyik
     kauzalis (ewm, rolling), igy az i. erteke csak az i-ig terjedo adatbol
     szarmazik - a szeletels utan is helyes marad.

  Ha a merte talalati arany 90 szazalek folott van, az NEM siker, hanem
  szivargas. A jozansagi ellenorzes a 40-65 szazalekos sav.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from . import indicators, patterns, store
from .config import Settings, load_settings
from .patterns.base import PatternMatch

log = logging.getLogger(__name__)

Outcome = Literal["target_hit", "invalidated", "timeout"]

# A detektoroknak elegendo elozmeny, de nem a teljes historia: a leghosszabb
# formacio 180 gyertya, igy 400 boven fedezi - es ezzel a backtest futasideje
# linearis marad, nem negyzetes.
DETECTION_WINDOW = 400


@dataclass
class Trade:
    """Egy triggerelt formacio es a kimenetele."""

    symbol: str
    name: str
    direction: str
    quality: float
    trigger_date: pd.Timestamp
    trigger_price: float
    target: float
    invalidation: float
    outcome: Outcome
    bars_to_outcome: int
    mfe_r: float          # maximum favorable excursion, R-ben
    mae_r: float          # maximum adverse excursion, R-ben

    @property
    def is_hit(self) -> bool:
        return self.outcome == "target_hit"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "direction": self.direction,
            "quality": round(self.quality, 3),
            "trigger_date": self.trigger_date,
            "trigger_price": round(self.trigger_price, 4),
            "outcome": self.outcome,
            "bars_to_outcome": self.bars_to_outcome,
            "mfe_r": round(self.mfe_r, 2),
            "mae_r": round(self.mae_r, 2),
        }


@dataclass
class PatternStats:
    """Osszesitett statisztika egy formaciotipusra."""

    name: str
    direction: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def samples(self) -> int:
        return len(self.trades)

    @property
    def hit_rate(self) -> float:
        """Talalati arany a LEZART ugyletekre.

        A timeout-tal vegzodo esetek nem szamitanak sem talalatnak, sem
        bukasnak - ezeket kulon jelentjuk, hogy ne torzitsanak.
        """
        resolved = [t for t in self.trades if t.outcome != "timeout"]
        if not resolved:
            return 0.0
        return sum(t.is_hit for t in resolved) / len(resolved)

    @property
    def resolved_samples(self) -> int:
        return sum(1 for t in self.trades if t.outcome != "timeout")

    @property
    def timeout_rate(self) -> float:
        return sum(t.outcome == "timeout" for t in self.trades) / max(self.samples, 1)

    @property
    def median_bars(self) -> int | None:
        hits = [t.bars_to_outcome for t in self.trades if t.is_hit]
        return int(statistics.median(hits)) if hits else None

    def _r_values(self) -> list[float]:
        """Ugyletenkenti eredmeny R-ben: talalatnal a teljes R:R, bukasnal -1, idokorlatnal 0."""
        values = []
        for trade in self.trades:
            risk = abs(trade.trigger_price - trade.invalidation)
            if risk <= 0:
                continue
            reward = abs(trade.target - trade.trigger_price) / risk
            values.append(reward if trade.is_hit else (-1.0 if trade.outcome == "invalidated" else 0.0))
        return values

    @property
    def expectancy_r(self) -> float:
        """Varhato ertek R-ben: talalatnal a teljes R:R, bukasnal -1."""
        values = self._r_values()
        return float(np.mean(values)) if values else 0.0

    @property
    def expectancy_sd(self) -> float | None:
        """Az ugyletenkenti R szorasa - ebbol a varhato ertek 95%-os savja (a "Legjobb belepo" rangsorahoz)."""
        values = self._r_values()
        return float(np.std(values, ddof=1)) if len(values) >= 2 else None

    def bucket_stats(self, edges: list[float]) -> list[dict]:
        """Minoseg szerinti bontas: jobb minta valoban jobb talalati aranyt ad-e?"""
        bounds = sorted(edges) + [1.01]
        out = []
        for low, high in zip(bounds, bounds[1:]):
            subset = [t for t in self.trades if low <= t.quality < high]
            resolved = [t for t in subset if t.outcome != "timeout"]
            out.append(
                {
                    "min_quality": low,
                    "max_quality": high,
                    "samples": len(resolved),
                    "hit_rate": (sum(t.is_hit for t in resolved) / len(resolved)) if resolved else 0.0,
                    "median_bars": (
                        int(statistics.median([t.bars_to_outcome for t in resolved if t.is_hit]))
                        if any(t.is_hit for t in resolved)
                        else None
                    ),
                }
            )
        return out

    def to_dict(self, edges: list[float]) -> dict:
        return {
            "name": self.name,
            "direction": self.direction,
            "samples": self.resolved_samples,
            "total_detections": self.samples,
            "hit_rate": round(self.hit_rate, 4),
            "timeout_rate": round(self.timeout_rate, 4),
            "median_bars": self.median_bars,
            "expectancy_r": round(self.expectancy_r, 3),
            "expectancy_sd": None if self.expectancy_sd is None else round(self.expectancy_sd, 3),
            "expectancy_n": len(self._r_values()),
            "buckets": self.bucket_stats(edges),
        }


# ---------------------------------------------------------------------------
# Kimenetel kovetese
# ---------------------------------------------------------------------------
def _track(
    df: pd.DataFrame, match: PatternMatch, trigger_index: int, horizon: int
) -> Trade | None:
    """Elore koveti a mintat: cel vagy invalidacio ert-e elobb.

    Intraday extremumokat nezunk (high/low), nem zaroarat: a stopot es a celt
    a valosagban is az intraday mozgas uti meg.
    """
    end = min(trigger_index + horizon, len(df) - 1)
    if end <= trigger_index:
        return None

    entry = float(df["close"].iloc[trigger_index])
    risk = abs(entry - match.invalidation)
    if risk <= 0:
        return None

    highs = df["high"].to_numpy(dtype="float64")[trigger_index + 1 : end + 1]
    lows = df["low"].to_numpy(dtype="float64")[trigger_index + 1 : end + 1]
    if highs.size == 0:
        return None

    bullish = match.direction == "bullish"

    if bullish:
        hit_positions = np.flatnonzero(highs >= match.target)
        stop_positions = np.flatnonzero(lows <= match.invalidation)
        mfe = (float(np.max(highs)) - entry) / risk
        mae = (entry - float(np.min(lows))) / risk
    else:
        hit_positions = np.flatnonzero(lows <= match.target)
        stop_positions = np.flatnonzero(highs >= match.invalidation)
        mfe = (entry - float(np.min(lows))) / risk
        mae = (float(np.max(highs)) - entry) / risk

    first_hit = int(hit_positions[0]) if hit_positions.size else None
    first_stop = int(stop_positions[0]) if stop_positions.size else None

    if first_hit is not None and (first_stop is None or first_hit <= first_stop):
        outcome, bars = "target_hit", first_hit + 1
    elif first_stop is not None:
        outcome, bars = "invalidated", first_stop + 1
    else:
        outcome, bars = "timeout", end - trigger_index

    return Trade(
        symbol=match.symbol,
        name=match.name,
        direction=match.direction,
        quality=match.quality,
        trigger_date=df.index[trigger_index],
        trigger_price=entry,
        target=match.target,
        invalidation=match.invalidation,
        outcome=outcome,
        bars_to_outcome=bars,
        mfe_r=mfe,
        mae_r=mae,
    )


@dataclass
class TriggerEvent:
    """Egy EPPEN AZNAP kitort formacio a historiaban.

    Ezt kapja a kalibracio (`run_symbol`) es az ML-adatkeszlet is - igy a ketto
    garantaltan ugyanazt a look-ahead-vedett detektalast hasznalja.
    """

    match: PatternMatch
    trigger_index: int     # pozicio a TELJES `enriched` sorozatban
    horizon: int           # max. tartasi ido gyertyaban


def iter_triggers(
    symbol: str,
    enriched: pd.DataFrame,
    settings: Settings,
    start_offset: int = 250,
    begin: int | None = None,
    seen: set[str] | None = None,
):
    """Vegigjatssza a historiat, es minden friss kitorest visszaad.

    Gyertyankent ujra detektalunk, de csak az addigi adatbol - ez a lassu, de
    egyetlen becsuletes modszer. Az `enriched` az `indicators.enrich()` kimenete.

    Folytatas (a strategia-labor naponta csak az uj gyertyakat dolgozza fel): `begin`
    az elso feldolgozando gyertya, `seen` a korabban mar visszaadott kitoresek
    ujjlenyomata (helyben bovul). A felismeres egy DETECTION_WINDOW hosszu csuszo
    ablakon fut, igy a folytatas ugyanazt adja, mint a teljes ujrafuttatas.
    """
    if len(enriched) < start_offset + 60:
        log.warning("%s: tul keves adat a backtesthez (%d gyertya)", symbol, len(enriched))
        return

    seen = set() if seen is None else seen
    cfg = settings.backtest

    for i in range(max(start_offset, begin or 0), len(enriched)):
        window_start = max(0, i - DETECTION_WINDOW)
        window = enriched.iloc[window_start : i + 1]

        found = patterns.detect(
            symbol, window, settings.patterns, settings.structure, confirmed_only=True
        )

        for match in found:
            if match.status != "triggered" or match.trigger_date is None:
                continue

            # KRITIKUS look-ahead vedelem.
            #
            # A detektor akkor is megtalalja a mintat, ha a kitores mar 30
            # gyertyaval korabban megtortent. Ha ilyenkor a REGEBBI kitoresi
            # napra konyvelnenk a belepest, olyan tudassal lepnenk be, ami
            # akkor meg nem letezett - es eppen azokat a mintakat valogatnank
            # ki, amelyek kozben bejottek (a megbukottak addigra mar mas
            # allapotba kerultek vagy eltuntek).
            #
            # Ez nem elmeleti aggaly: pontosan emiatt mert a fej-vall eloszor
            # 100 szazalekos talalati aranyt 30 mintan.
            #
            # A becsuletes szimulacio: csak azt az ugyletet konyveljuk, amelyik
            # EPPEN MA torte at a szintet.
            if match.trigger_date != window.index[-1]:
                continue

            key = match.fingerprint()
            if key in seen:
                continue
            seen.add(key)

            # A trigger pozicioja a TELJES sorozatban
            trigger_index = int(enriched.index.get_loc(match.trigger_date))

            pattern_bars = max(
                int(enriched.index.get_loc(match.end_date))
                - int(enriched.index.get_loc(match.start_date)),
                5,
            )
            horizon = int(min(pattern_bars * cfg.max_horizon_mult, cfg.max_horizon_bars))
            yield TriggerEvent(match=match, trigger_index=trigger_index, horizon=horizon)


def run_symbol(
    symbol: str,
    df: pd.DataFrame,
    settings: Settings,
    start_offset: int = 250,
) -> list[Trade]:
    """Egy papir teljes historiajanak vegigjatszasa (kalibraciohoz)."""
    enriched = indicators.enrich(df, settings.structure.atr_period)
    trades: list[Trade] = []

    for event in iter_triggers(symbol, enriched, settings, start_offset):
        trade = _track(enriched, event.match, event.trigger_index, event.horizon)
        if trade is not None:
            trades.append(trade)

    log.info("%s: %d triggerelt formacio a historiaban", symbol, len(trades))
    return trades


def aggregate(trades: list[Trade], settings: Settings) -> dict[str, PatternStats]:
    stats: dict[str, PatternStats] = {}
    for trade in trades:
        key = f"{trade.name}|{trade.direction}"
        if key not in stats:
            stats[key] = PatternStats(trade.name, trade.direction)
        stats[key].trades.append(trade)
    return stats


def sanity_check(stats: dict[str, PatternStats]) -> list[str]:
    """Jozansagi ellenorzes a look-ahead szivargas kiszurasere.

    Egy klasszikus chart formacio valos talalati aranya 40-65 szazalek kozott
    van. A 85 szazalek folotti ertek nem zsenialitas, hanem hiba - szinte
    biztosan jovobeli informacio szivargott a detektorba.
    """
    warnings: list[str] = []
    for key, stat in stats.items():
        if stat.resolved_samples < 10:
            continue
        if stat.hit_rate > 0.85:
            warnings.append(
                f"GYANUS: {key} talalati aranya {stat.hit_rate:.0%} "
                f"({stat.resolved_samples} minta) - valoszinuleg look-ahead szivargas!"
            )
        elif stat.hit_rate < 0.15:
            warnings.append(
                f"GYANUS: {key} talalati aranya {stat.hit_rate:.0%} - "
                "forditott logika vagy hibas celar?"
            )
    return warnings


def run(
    symbols: list[str],
    settings: Settings | None = None,
    walk_forward: bool = False,
) -> dict:
    """Teljes kalibracios futas; a `data/calibration.json`-t irja."""
    settings = settings or load_settings()
    all_trades: list[Trade] = []

    for symbol in symbols:
        df = store.load(symbol, settings)
        if df.empty:
            log.warning("Nincs cache-elt adat: %s - futtasd elobb a fetch parancsot.", symbol)
            continue
        all_trades.extend(run_symbol(symbol, df, settings))

    if not all_trades:
        log.error("Nem talaltunk egyetlen triggerelt formaciot sem.")
        return {"patterns": {}, "warnings": ["Nincs adat a kalibraciohoz."]}

    stats = aggregate(all_trades, settings)
    warnings = sanity_check(stats)
    for warning in warnings:
        log.warning(warning)

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "symbols": symbols,
        "total_trades": len(all_trades),
        "patterns": {
            key: stat.to_dict(settings.backtest.quality_buckets) for key, stat in stats.items()
        },
        "warnings": warnings,
    }

    if walk_forward:
        payload["walk_forward"] = _walk_forward(all_trades, settings)

    settings.calibration_path.parent.mkdir(parents=True, exist_ok=True)
    settings.calibration_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    log.info("Kalibracio mentve: %s (%d ugylet)", settings.calibration_path, len(all_trades))
    return payload


def _walk_forward(trades: list[Trade], settings: Settings) -> dict:
    """Idoben kettevagva kalibralunk es validalunk.

    Ha a regebbi felen mert talalati arany erdemben eltér az ujabb felen
    mertol, a statisztika nem stabil - a szazalekok nem altalanosithatok a
    jovobe. Ezt jobb tudni, mint hinni a szamokban.
    """
    ordered = sorted(trades, key=lambda t: t.trigger_date)
    midpoint = len(ordered) // 2
    if midpoint < 20:
        return {"status": "tul keves ugylet a walk-forward vizsgalathoz"}

    train = aggregate(ordered[:midpoint], settings)
    test = aggregate(ordered[midpoint:], settings)

    comparison = {}
    for key in set(train) & set(test):
        train_stat, test_stat = train[key], test[key]
        if train_stat.resolved_samples < 10 or test_stat.resolved_samples < 10:
            continue
        drift = abs(train_stat.hit_rate - test_stat.hit_rate)
        comparison[key] = {
            "train_hit_rate": round(train_stat.hit_rate, 3),
            "test_hit_rate": round(test_stat.hit_rate, 3),
            "drift": round(drift, 3),
            "stable": drift < 0.15,
        }

    return comparison
