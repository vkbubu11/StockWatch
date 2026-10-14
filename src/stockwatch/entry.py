"""Swing LONG belepesi motor.

A formaciodetektor megmondja, MI van a charton. Ez a modul azt donti el, hogy
ebbol MOST van-e jo long beszallasi lehetoseg - es ha igen, pontosan hol a
belepo, a stop, a cel es mekkora legyen a pozicio.

Harom belepesi helyzet:

  breakout  - MEGEROSITETT kitores (alapbeallitas): tegnap zart eloszor a
              trigger felett, es a mai zaro is felette maradt. Kikapcsolva
              (`confirm_breakout: false`) mar a kitoresi napon jelez.
  retest    - korabbi kitores utan az ar visszajott a trigger szinthez,
              megtartotta, es ma felfele fordult (`alert_retests`, alapbol ki)
  setup     - a minta meg alakul, az ar 1 ATR-en belul van a triggertol:
              "figyelj, holnap kitorhet" - ez meg NEM vetel

Ket reteg dont:

  1. KEMENY SZUROK - ha barmelyik megbukik, nincs jelzes (a CLI megmutatja,
     melyik bukott el es miert):
       trend, RSI-sav, kitoresi volumen, tulnyujtottsag, R/R, stop merete,
       piaci rezsim, earnings-tilalom
  2. PONTSZAM (0-100) - a szurokon atjutott jelzesek rangsorolasa,
     komponensenkenti indoklassal. `min_score` alatt nincs riasztas.

FONTOS: a pontszam NEM valoszinuseg. A historikus talalati aranyt a
kalibracio (backtest) adja, es a jelzes azt kulon kozli.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import numpy as np
import pandas as pd

from . import market
from .config import EntrySettings, Settings
from .forecast import Calibration
from .levels import Level
from .patterns.base import PatternMatch, volume_dryup

log = logging.getLogger(__name__)

EntryKind = Literal["breakout", "retest", "setup", "pullback"]

KIND_LABELS = {
    "breakout": "MEGEROSITETT KITORES - belepesi jelzes",
    "retest": "VISSZATESZT - belepesi jelzes",
    "setup": "ELOKESZULET - figyelj, meg nem vetel",
    "pullback": "VISSZAHUZAS - veteli limit holnapra",
}


# ---------------------------------------------------------------------------
# Adatszerkezetek
# ---------------------------------------------------------------------------
@dataclass
class Check:
    """Egy kemeny szuro eredmenye.

    `passed=None` azt jelenti: nem tudtuk ellenorizni (pl. nincs benchmark
    adat). Ez nem blokkol, de a jelzesben figyelmeztetes lesz belole.
    """

    name: str
    passed: bool | None
    detail: str

    def __post_init__(self) -> None:
        # A pandas/numpy osszehasonlitas np.bool_-t ad, amire az `is False`
        # HAMIS - a szuro csendben atengedne. Ezert mindig valodi bool-ra valtunk.
        if self.passed is not None:
            self.passed = bool(self.passed)


@dataclass
class ScorePart:
    name: str
    points: float
    maximum: float
    reason: str


@dataclass
class TradePlan:
    entry: float              # tervezett belepo (referencia)
    entry_limit: float        # e folott mar ne vegyunk (gap-vedelem)
    stop: float
    target: float
    atr: float
    shares: int
    account_size: float
    risk_pct: float
    exit_mode: str = "target"
    partial_r: float = 1.0
    trail_atr: float = 3.0
    horizon: int = 0          # max. tartas kereskedesi napokban (idokorlat)

    @property
    def partial_price(self) -> float:
        return self.entry + self.partial_r * self.risk_per_share

    @property
    def uses_partial(self) -> bool:
        return self.exit_mode in ("partial", "partial_trail")

    @property
    def trailing(self) -> bool:
        return self.exit_mode in ("trail", "partial_trail")

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop

    @property
    def risk_reward(self) -> float:
        risk = self.risk_per_share
        return (self.target - self.entry) / risk if risk > 0 else 0.0

    @property
    def stop_pct(self) -> float:
        return (self.entry - self.stop) / self.entry * 100 if self.entry else 0.0

    @property
    def target_pct(self) -> float:
        return (self.target - self.entry) / self.entry * 100 if self.entry else 0.0

    @property
    def position_value(self) -> float:
        return self.shares * self.entry

    @property
    def risk_amount(self) -> float:
        return self.shares * self.risk_per_share

    def to_dict(self) -> dict:
        return {
            "entry": round(self.entry, 2),
            "entry_limit": round(self.entry_limit, 2),
            "stop": round(self.stop, 2),
            "target": round(self.target, 2),
            "risk_reward": round(self.risk_reward, 2),
            "stop_pct": round(self.stop_pct, 2),
            "target_pct": round(self.target_pct, 2),
            "shares": self.shares,
            "position_value": round(self.position_value, 2),
            "risk_amount": round(self.risk_amount, 2),
            "exit_mode": self.exit_mode,
            "partial_price": round(self.partial_price, 2) if self.uses_partial else None,
            "horizon": self.horizon,
        }


@dataclass
class EntrySignal:
    symbol: str
    kind: EntryKind
    as_of: pd.Timestamp
    price: float
    pattern: PatternMatch
    plan: TradePlan
    checks: list[Check] = field(default_factory=list)
    score_parts: list[ScorePart] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    history_text: str = "nincs eleg adat"
    earnings_date: date | None = None
    ml: dict | None = None                  # gepi tanulas arnyek-becsles (shadow.py) - nem dont
    strategy: str = "breakout"              # melyik strategia kuldte (config.STRATEGY_IDS)
    # a kiertekeles pontos idopontja (UTC) - az elbukott jelolteknel is; a jelazonositoba NEM szamit bele
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def score(self) -> float:
        return round(sum(p.points for p in self.score_parts), 1)

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if c.passed is False]

    @property
    def passed(self) -> bool:
        return not self.failed_checks

    def qualifies(self, cfg: EntrySettings) -> bool:
        if not self.passed or self.score < cfg.min_score:
            return False
        return self.kind != "setup" or cfg.alert_setups

    @property
    def signal_id(self) -> str:
        """Kozos jelazonosito az egesz eletuton (naplo, ertesites, virtualis pozicio,
        paper trade, utolagos ertekeles). Determinisztikus: ugyanabbol az adatbol
        ugyanaz jon ki, igy a visszajatszas is ugyanazt az azonositot adja."""
        raw = f"{self.symbol}|{self.as_of:%Y-%m-%d}|{self.pattern.fingerprint()}|{self.kind}"
        if self.strategy != "breakout":          # a kitores azonositoi a regi adatokkal egyeznek
            raw += f"|{self.strategy}"
        return "sig-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def key(self) -> str:
        """Cooldown-kulcs: ugyanaz a minta ugyanabban a helyzetben (strategiankent)."""
        base = f"{self.pattern.fingerprint()}|{self.kind}"
        return base if self.strategy == "breakout" else f"{self.strategy}|{base}"

    @property
    def trade_key(self) -> str:
        """A kovetett trade / paper trade azonositoja (PAPIR|NAP|ALAKZAT[|STRATEGIA])."""
        base = f"{self.symbol}|{self.as_of:%Y-%m-%d}|{self.pattern.name}"
        return base if self.strategy == "breakout" else f"{base}|{self.strategy}"

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "kind": self.kind,
            "as_of": str(self.as_of.date()),
            "bar_time": self.as_of.isoformat() if self.as_of.time() != datetime.min.time() else None,
            "evaluated_at": self.evaluated_at.isoformat(timespec="seconds"),
            "price": round(self.price, 2),
            "score": self.score,
            "passed": self.passed,
            "pattern": self.pattern.name,
            "pattern_quality": round(self.pattern.quality, 3),
            "trigger_level": round(self.pattern.trigger_level, 2),
            "plan": self.plan.to_dict(),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "score_parts": [
                {"name": p.name, "points": round(p.points, 1), "max": p.maximum, "reason": p.reason}
                for p in self.score_parts
            ],
            "history": self.history_text,
            "earnings_date": self.earnings_date.isoformat() if self.earnings_date else None,
            "warnings": self.warnings,
            "ml": self.ml,
        }


# ---------------------------------------------------------------------------
# Helyzet felismerese
# ---------------------------------------------------------------------------
def classify(
    match: PatternMatch, df: pd.DataFrame, cfg: EntrySettings
) -> EntryKind | None:
    """Melyik belepesi helyzetben van a minta ma - vagy egyikben sem."""
    if match.direction != "bullish":
        return None

    last = len(df) - 1
    today = df.index[-1]
    close = float(df["close"].iloc[-1])
    atr = _atr(df)

    if match.status == "triggered" and match.trigger_date is not None:
        if cfg.confirm_breakout:
            # Megerositett kitores: TEGNAP tort ki, es a mai zaro is a szint felett van.
            # A kutatasban ez emelte a legtobbet a nyeresi aranyon (hamis kitoresek kiszurese).
            if len(df) >= 2 and match.trigger_date == df.index[-2] and close > match.trigger_level:
                return "breakout"
        elif match.trigger_date == today:
            return "breakout"
        if cfg.alert_retests and match.trigger_date != today and _is_retest(match, df, cfg):
            return "retest"
        return None

    if match.status == "forming" and atr > 0:
        distance = (match.trigger_level - close) / atr
        if 0 <= distance <= cfg.setup_max_distance_atr:
            return "setup"

    return None


def _is_retest(match: PatternMatch, df: pd.DataFrame, cfg: EntrySettings) -> bool:
    """Visszateszt: kitores -> visszahuzodas a szintig -> megtartas -> fordulas.

    Mind a negy feltetel kell:
      1. a kitores legfeljebb `retest_max_bars` napja volt, de nem tegnap-ma
      2. az utolso 3 napban valamelyik intraday alj a trigger zonaba ert
      3. a kitores ota egyetlen zaro sem ment a trigger szint ala
         (egy ilyen zaro mar hamis kitorest jelent, nem visszatesztet)
      4. a mai gyertya felfele zart, a trigger szint felett
    """
    try:
        trigger_pos = int(df.index.get_loc(match.trigger_date))
    except KeyError:
        return False

    last = len(df) - 1
    bars_since = last - trigger_pos
    if not 2 <= bars_since <= cfg.retest_max_bars:
        return False

    level = match.trigger_level
    zone_top = level + cfg.retest_zone_atr * _atr(df)

    recent_lows = df["low"].iloc[max(trigger_pos + 1, last - 2) : last + 1]
    touched = bool((recent_lows <= zone_top).any())

    closes_since = df["close"].iloc[trigger_pos + 1 : last + 1]
    held = bool((closes_since >= level).all())

    today = df.iloc[-1]
    turned_up = today["close"] > today["open"] and today["close"] > level

    return touched and held and bool(turned_up)


# ---------------------------------------------------------------------------
# Kereskedesi terv
# ---------------------------------------------------------------------------
def build_plan(
    match: PatternMatch, kind: EntryKind, df: pd.DataFrame, cfg: EntrySettings, horizon: int = 0
) -> TradePlan:
    """Belepo, stop, cel es pozicio meret.

    A stop a minta invalidacios szintje, DE legalabb `min_stop_atr` ATR-re a
    belepotol: egy tul szoros stopot a napi zaj ut ki, nem a tevedes.
    """
    close = float(df["close"].iloc[-1])
    atr = _atr(df)

    entry = match.trigger_level if kind == "setup" else close
    entry_limit = entry * (1 + cfg.entry_max_gap_pct / 100)
    stop = min(match.invalidation, entry - cfg.min_stop_atr * atr)
    target = match.target

    shares = position_size(entry, stop, cfg)
    return TradePlan(
        entry=entry,
        entry_limit=entry_limit,
        stop=stop,
        target=target,
        atr=atr,
        shares=shares,
        account_size=cfg.account_size,
        risk_pct=cfg.risk_per_trade_pct,
        exit_mode=cfg.exit_mode,
        partial_r=cfg.partial_r,
        trail_atr=cfg.trail_atr,
        horizon=horizon,
    )


def pattern_horizon(match: PatternMatch, df: pd.DataFrame, settings: Settings) -> int:
    """Max. tartas: a minta hosszanak tobbszorose - ugyanugy, mint a backtestben."""
    try:
        bars = int(df.index.get_loc(match.end_date)) - int(df.index.get_loc(match.start_date))
    except KeyError:
        bars = 20
    bars = max(bars, 5)
    cfg = settings.backtest
    return int(min(bars * cfg.max_horizon_mult, cfg.max_horizon_bars))


def position_size(entry: float, stop: float, cfg: EntrySettings) -> int:
    """Kockazat alapu meretezes: egy rossz trade legfeljebb a tokе X%-at viszi.

        darab = (szamla * kockazat%) / (belepo - stop)

    majd felulrol korlatozzuk, hogy egy papir ne vigye el a szamla nagy reszet.
    """
    risk_per_share = entry - stop
    if entry <= 0 or risk_per_share <= 0:
        return 0
    by_risk = cfg.account_size * cfg.risk_per_trade_pct / 100 / risk_per_share
    by_cap = cfg.account_size * cfg.max_position_pct / 100 / entry
    return max(int(math.floor(min(by_risk, by_cap))), 0)


# ---------------------------------------------------------------------------
# Kemeny szurok
# ---------------------------------------------------------------------------
def run_checks(
    match: PatternMatch,
    kind: EntryKind,
    df: pd.DataFrame,
    plan: TradePlan,
    cfg: EntrySettings,
    regime: market.Regime,
    earnings_days: int | None,
    rel_strength: float | None = None,
) -> list[Check]:
    last = df.iloc[-1]
    close = float(last["close"])
    checks: list[Check] = []

    # 1. Trend - ellentrendbe nem veszunk
    if cfg.require_uptrend:
        sma200 = df["close"].rolling(200, min_periods=200).mean().iloc[-1]
        ema50, ema200 = last.get("ema50"), last.get("ema200")
        if pd.isna(sma200) or pd.isna(ema50) or pd.isna(ema200):
            checks.append(Check("Trend", None, "tul rovid historia a 200 napos atlaghoz"))
        else:
            ok = close > sma200 and ema50 > ema200
            checks.append(
                Check(
                    "Trend",
                    ok,
                    f"zaro {'>' if close > sma200 else '<'} SMA200 ({sma200:.2f}), "
                    f"EMA50 {'>' if ema50 > ema200 else '<'} EMA200",
                )
            )

    # 2. RSI - ne legyen gyenge, de ne is legyen mar tulvett
    rsi = last.get("rsi")
    if pd.isna(rsi):
        checks.append(Check("RSI", None, "nincs RSI ertek"))
    else:
        ok = cfg.rsi_min <= rsi <= cfg.rsi_max
        checks.append(Check("RSI", ok, f"{rsi:.0f} (sav {cfg.rsi_min:.0f}-{cfg.rsi_max:.0f})"))

    # 3. Kitoresi volumen - csak a kitoresnek kell nagy volumen
    if kind == "breakout" and cfg.min_breakout_volume > 0:
        ratio = last.get("vol_ratio")
        if pd.isna(ratio):
            checks.append(Check("Volumen", None, "nincs volumenadat"))
        else:
            ok = ratio >= cfg.min_breakout_volume
            checks.append(
                Check("Volumen", ok, f"{ratio:.2f}x atlag (min. {cfg.min_breakout_volume:.1f}x)")
            )

    # 4. Tulnyujtottsag - a trigger felett messze mar ne uldozzuk
    if kind in ("breakout", "retest") and plan.atr > 0:
        extension = (close - match.trigger_level) / plan.atr
        ok = extension <= cfg.max_extension_atr
        checks.append(
            Check(
                "Tavolsag a triggertol",
                ok,
                f"{extension:.1f} ATR a trigger felett (max. {cfg.max_extension_atr:.1f})",
            )
        )

    # 5. Hozam/kockazat a TENYLEGES belepobol szamolva
    ok = plan.risk_reward >= cfg.min_risk_reward
    checks.append(
        Check("Hozam/kockazat", ok, f"1 : {plan.risk_reward:.2f} (min. 1 : {cfg.min_risk_reward:.1f})")
    )

    # 6. Stop merete - egy 20 szazalekos stop mar nem swing trade
    ok = 0 < plan.stop_pct <= cfg.max_stop_pct
    checks.append(Check("Stop merete", ok, f"{plan.stop_pct:.1f}% (max. {cfg.max_stop_pct:.0f}%)"))

    # 7. Piaci rezsim
    if cfg.regime_filter:
        checks.append(Check(regime.name or f"Piac ({cfg.benchmark})", regime.ok, regime.text))

    # 8. Earnings-tilalom
    if cfg.earnings_blackout_days > 0:
        if earnings_days is None:
            checks.append(
                Check(
                    "Earnings",
                    False if cfg.earnings_unknown_blocks else None,
                    "a kovetkezo jelentes datuma nem ismert",
                )
            )
        else:
            ok = earnings_days > cfg.earnings_blackout_days
            checks.append(
                Check(
                    "Earnings",
                    ok,
                    f"jelentes {earnings_days} nap mulva (tilalom {cfg.earnings_blackout_days} nap)",
                )
            )

    checks += strength_checks(df, cfg, rel_strength)
    return checks


def high52_ratio(df: pd.DataFrame) -> float | None:
    """Zaro / az utolso 252 kereskedesi nap legmagasabb ara (a mai nappal)."""
    if len(df) < 200:
        return None
    high = float(df["high"].iloc[-252:].max())
    return float(df["close"].iloc[-1]) / high if high > 0 else None


def momentum_12_1(df: pd.DataFrame) -> float | None:
    """12-1 honapos hozam %-ban: 12 honapja -> 1 honapja (a legutobbi honap kimarad,
    az ugyanis rovid tavon inkabb visszafordul - Jegadeesh-Titman)."""
    if len(df) < 253:
        return None
    closes = df["close"]
    return float((closes.iloc[-22] / closes.iloc[-253] - 1) * 100)


def contraction_ratio(df: pd.DataFrame) -> float | None:
    """A kitores elotti 10 nap atlagos napi sava (high-low)/zaro, osztva az azt megelozo 100 napeval.
    1 alatt: a volatilitas osszehuzodott a kitores elott (VCP / squeeze). Az utolso 2 nap (kitores
    + megerosites) kimarad."""
    if len(df) < 112:
        return None
    rng = (df["high"] - df["low"]) / df["close"]
    recent = float(rng.iloc[-12:-2].mean())
    base = float(rng.iloc[-112:-12].mean())
    return recent / base if base > 0 else None


def strength_checks(df: pd.DataFrame, cfg: EntrySettings, rel_strength: float | None) -> list[Check]:
    """Az opcionalis momentum / erosseg szurok (alapbol kikapcsolva)."""
    checks: list[Check] = []
    if cfg.min_high52_ratio > 0:
        ratio = high52_ratio(df)
        checks.append(Check("52 hetes csucs", None, "tul rovid historia") if ratio is None else
                      Check("52 hetes csucs", ratio >= cfg.min_high52_ratio,
                            f"a csucs {ratio:.0%}-an (min. {cfg.min_high52_ratio:.0%})"))
    if cfg.min_momentum_pct is not None:
        mom = momentum_12_1(df)
        checks.append(Check("Momentum 12-1", None, "tul rovid historia") if mom is None else
                      Check("Momentum 12-1", mom >= cfg.min_momentum_pct,
                            f"{mom:+.0f}% (min. {cfg.min_momentum_pct:+.0f}%)"))
    if cfg.min_rel_strength is not None:
        checks.append(Check("Relativ ero", None, f"nincs {cfg.benchmark} adat") if rel_strength is None else
                      Check("Relativ ero", rel_strength >= cfg.min_rel_strength,
                            f"{rel_strength:+.1f} pp a {cfg.benchmark}-hoz kepest 60 napra "
                            f"(min. {cfg.min_rel_strength:+.0f})"))
    if cfg.max_contraction > 0:
        ratio = contraction_ratio(df)
        checks.append(Check("Osszehuzodas", None, "tul rovid historia") if ratio is None else
                      Check("Osszehuzodas", ratio <= cfg.max_contraction,
                            f"kitores elotti sav {ratio:.2f}x a szokasoshoz (max. {cfg.max_contraction:.2f})"))
    return checks


# ---------------------------------------------------------------------------
# Pontozas
# ---------------------------------------------------------------------------
def score(
    match: PatternMatch,
    kind: EntryKind,
    df: pd.DataFrame,
    plan: TradePlan,
    settings: Settings,
    calibration: Calibration,
    rel_strength: float | None,
    support: Level | None,
    resistance: Level | None,
) -> tuple[list[ScorePart], str]:
    """Nyolc komponens, osszesen 100 pont. Visszaadja a historikus szoveget is."""
    last = df.iloc[-1]
    parts: list[ScorePart] = []

    # 1. Mintaminoseg (20)
    parts.append(
        ScorePart("Mintaminoseg", 20 * match.quality, 20, f"{match.name}, geometria {match.quality:.0%}")
    )

    # 2. Historikus eredmeny a kalibraciobol (15)
    entry = calibration.data.get("patterns", {}).get(f"{match.name}|{match.direction}")
    min_samples = settings.forecast.min_samples_for_probability
    if entry and entry.get("samples", 0) >= min_samples:
        expectancy = float(entry.get("expectancy_r", 0.0))
        points = 15 * float(np.clip((expectancy + 0.5) / 1.5, 0, 1))
        history = (
            f"{entry['hit_rate']:.0%} talalat, varhato ertek {expectancy:+.2f}R "
            f"({entry['samples']} historikus minta)"
        )
        parts.append(ScorePart("Historia", points, 15, history))
    else:
        samples = entry.get("samples", 0) if entry else 0
        history = f"nincs eleg adat ({samples} minta, min. {min_samples})"
        parts.append(ScorePart("Historia", 7.5, 15, history + " - semleges pont"))

    # 3. Trenderosseg (15)
    adx = last.get("adx")
    slope = last.get("ema50_slope")
    ema20 = last.get("ema20")
    adx_pts = 5 * float(np.clip((adx - 15) / 20, 0, 1)) if pd.notna(adx) else 2.5
    slope_pts = 5 * float(np.clip(slope / 8, 0, 1)) if pd.notna(slope) else 2.5
    above_pts = 5.0 if pd.notna(ema20) and last["close"] > ema20 else 0.0
    parts.append(
        ScorePart(
            "Trenderosseg",
            adx_pts + slope_pts + above_pts,
            15,
            f"ADX {adx:.0f}, EMA50 meredekseg {slope:+.1f}%" if pd.notna(adx) and pd.notna(slope)
            else "reszleges trendadat",
        )
    )

    # 4. Volumen (15): kitoresi volumen + a konszolidacio alatti szaradas
    ratio = last.get("vol_ratio")
    vol_pts = 10 * float(np.clip((ratio - 1.0) / 1.0, 0, 1)) if pd.notna(ratio) else 5.0
    try:
        start = int(df.index.get_loc(match.start_date))
        end = int(df.index.get_loc(match.end_date))
        dry = volume_dryup(df, start, end)
    except KeyError:
        dry = 0.5
    parts.append(
        ScorePart(
            "Volumen",
            vol_pts + 5 * dry,
            15,
            f"mai volumen {ratio:.1f}x, szaradas a mintaban {dry:.0%}" if pd.notna(ratio)
            else "nincs volumenadat",
        )
    )

    # 5. Relativ ero a benchmarkhoz (10)
    if rel_strength is None:
        parts.append(ScorePart("Relativ ero", 5.0, 10, "benchmark nelkul - semleges pont"))
    else:
        parts.append(
            ScorePart(
                "Relativ ero",
                10 * (float(np.clip(rel_strength / 15, -1, 1)) + 1) / 2,
                10,
                f"{rel_strength:+.1f} szazalekpont 60 nap alatt a {settings.entry.benchmark}-hoz kepest",
            )
        )

    # 6. RSI edes pont (10): 50-65 kozott teljes pont
    rsi = last.get("rsi")
    if pd.isna(rsi):
        rsi_pts, rsi_text = 5.0, "nincs RSI"
    elif 50 <= rsi <= 65:
        rsi_pts, rsi_text = 10.0, f"RSI {rsi:.0f} - idealis sav"
    elif rsi < 50:
        rsi_pts, rsi_text = 10 * float(np.clip((rsi - 40) / 10, 0, 1)), f"RSI {rsi:.0f} - gyengecske"
    else:
        rsi_pts, rsi_text = 10 * float(np.clip((75 - rsi) / 10, 0, 1)), f"RSI {rsi:.0f} - melegszik"
    parts.append(ScorePart("RSI", rsi_pts, 10, rsi_text))

    # 7. Hozam/kockazat (10): 1.5 -> 0 pont, 3.5 -> teljes pont
    rr = plan.risk_reward
    parts.append(
        ScorePart("Hozam/kockazat", 10 * float(np.clip((rr - 1.5) / 2.0, 0, 1)), 10, f"1 : {rr:.2f}")
    )

    # 8. Szintek (5): eros support a stop felett, nincs kozeli fal a cel utjaban
    lvl_pts, notes = 0.0, []
    atr = plan.atr
    if support is not None and atr > 0 and plan.stop < support.price <= plan.entry:
        lvl_pts += 2.5 * support.score / 100
        notes.append(f"support {support.price:.2f} a stop felett")
    if resistance is None:
        lvl_pts += 2.5
        notes.append("nincs rezisztencia felette")
    elif atr > 0 and (resistance.price - plan.entry) / atr > 1.0:
        lvl_pts += 2.5
        notes.append(f"rezisztencia {resistance.price:.2f} tavol")
    else:
        notes.append(f"rezisztencia {resistance.price:.2f} kozel - korai fal")
    parts.append(ScorePart("Szintek", lvl_pts, 5, ", ".join(notes)))

    return parts, history


# ---------------------------------------------------------------------------
# Fo belepesi pont
# ---------------------------------------------------------------------------
def evaluate(
    symbol: str,
    df: pd.DataFrame,
    matches: list[PatternMatch],
    settings: Settings,
    calibration: Calibration | None = None,
    benchmark: pd.DataFrame | None = None,
    earnings_date: date | None = None,
    support: Level | None = None,
    resistance: Level | None = None,
    today: date | None = None,
    regime: market.Regime | None = None,
) -> list[EntrySignal]:
    """Minden bikas mintat kiertekel; a meg nem felelteket is visszaadja
    (a `passed` es a `failed_checks` mondja meg, miert nem).

    `regime`: a papir sajat rezsimje (market.MarketContext.regime); ha nincs megadva, a `benchmark`-bol.
    A `benchmark` a relativ ero indexe."""
    cfg = settings.entry
    calibration = calibration or Calibration()
    if df.empty:
        return []

    as_of = df.index[-1]
    price = float(df["close"].iloc[-1])
    if not cfg.regime_filter:
        regime = market.Regime(True, None, None, "kikapcsolva")
    elif regime is None:
        regime = market.market_regime(benchmark, as_of)
    rel = market.relative_strength(df, benchmark, as_of) if benchmark is not None else None
    earnings_days = market.days_until(earnings_date, today or as_of.date())

    signals: list[EntrySignal] = []
    seen: set[str] = set()

    for match in matches:
        kind = classify(match, df, cfg)
        if kind is None:
            continue
        # Ugyanaz a minta tobbszor is visszajohet kisse mas geometriaval -
        # helyzetenkent csak a legjobb minosegut tartjuk meg.
        dedup = f"{match.name}|{kind}"
        if dedup in seen:
            continue
        seen.add(dedup)

        plan = build_plan(match, kind, df, cfg, horizon=pattern_horizon(match, df, settings))
        checks = run_checks(match, kind, df, plan, cfg, regime, earnings_days, rel_strength=rel)
        parts, history = score(
            match, kind, df, plan, settings, calibration, rel, support, resistance
        )
        mult = cfg.risk_mult(sum(p.points for p in parts))
        if mult != 1.0:                       # eros jelzes: nagyobb kockazat (entry.boost_score)
            boosted = cfg.model_copy(update={"risk_per_trade_pct": cfg.risk_per_trade_pct * mult})
            plan.shares = position_size(plan.entry, plan.stop, boosted)
            plan.risk_pct = boosted.risk_per_trade_pct

        signal = EntrySignal(
            symbol=symbol,
            kind=kind,
            as_of=as_of,
            price=price,
            pattern=match,
            plan=plan,
            checks=checks,
            score_parts=parts,
            history_text=history,
            earnings_date=earnings_date,
        )
        for check in checks:
            if check.passed is None:
                signal.warnings.append(f"Nem ellenorizheto - {check.name}: {check.detail}")
        if plan.shares == 0:
            signal.warnings.append("A szamlameret mellett 0 darab jonne ki - nagy a stop vagy kicsi a szamla.")
        signals.append(signal)

    signals.sort(key=lambda s: (s.passed, s.score), reverse=True)
    return signals


def _atr(df: pd.DataFrame) -> float:
    value = df["atr"].iloc[-1] if "atr" in df.columns else np.nan
    if pd.notna(value) and value > 0:
        return float(value)
    return float(df["close"].iloc[-1]) * 0.02


# ---------------------------------------------------------------------------
# Szoveges formatum
# ---------------------------------------------------------------------------
def format_title(signal: EntrySignal) -> str:
    return (
        f"{signal.symbol}: {signal.pattern.name} - {KIND_LABELS[signal.kind]} "
        f"({signal.score:.0f}/100)"
    )


def exit_plan_lines(plan: TradePlan) -> list[str]:
    """A kilepesi terv - ez adja a magasabb nyeresi aranyt, ezert kulon kiemelve."""
    lines = ["", "Kilepesi terv:"]
    half = plan.shares // 2
    if plan.uses_partial:
        lines.append(
            f"  1. Fele ({half} db) eladas +{plan.partial_r:.1f}R-nel: {plan.partial_price:.2f} $ "
            f"(+{(plan.partial_price / plan.entry - 1) * 100:.1f}%) - adj be ra limit megbizast"
        )
        lines.append(f"  2. Utana a stopot huzd a belepore ({plan.entry:.2f} $) - innen nem lehet veszteseg")
        if plan.trailing:
            lines.append(f"  3. A maradek kovetett stoppal: legmagasabb ar - {plan.trail_atr:.1f} ATR")
        else:
            lines.append(f"  3. A maradek a celig: {plan.target:.2f} $")
    elif plan.trailing:
        lines.append(f"  Nincs fix cel: kovetett stop = legmagasabb ar - {plan.trail_atr:.1f} ATR")
    else:
        lines.append(f"  A teljes pozicio a celig: {plan.target:.2f} $")
    if plan.horizon:
        lines.append(f"  Idokorlat: ~{plan.horizon} kereskedesi nap utan zarasra kilepes")
    lines.append("  A rendszer koveti a trade-et, es szol, ha lepni kell.")
    return lines


def format_body(signal: EntrySignal, final_bar: bool = True) -> str:
    plan = signal.plan
    lines = [
        f"Zaro ({signal.as_of:%Y-%m-%d}): {signal.price:.2f} $",
        f"Trigger szint: {signal.pattern.trigger_level:.2f} $",
    ]

    if signal.kind == "setup":
        lines.append(f"Vetel CSAK zaro kitoresnel a {plan.entry:.2f} $ felett")
    else:
        lines.append(f"Belepo: masnap nyitaskor, limit {plan.entry_limit:.2f} $ (felette ne ugorj utana)")

    lines += [
        f"Stop: {plan.stop:.2f} $ (-{plan.stop_pct:.1f}%, {plan.risk_per_share / plan.atr:.1f} ATR)",
        f"Cel: {plan.target:.2f} $ (+{plan.target_pct:.1f}%)",
        f"Hozam/kockazat: 1 : {plan.risk_reward:.2f}",
        f"Meret: {plan.shares} db = {plan.position_value:,.0f} $ "
        f"(kockazat {plan.risk_amount:,.0f} $ = {plan.risk_pct:.1f}% a {plan.account_size:,.0f} $ szamlabol)",
        f"Historia: {signal.history_text}",
    ]
    if signal.ml:
        lines.append(f"Gepi tanulas (arnyek-uzem, nem dont): becsult {signal.ml['pred_r']:+.2f}R - a modell "
                     f"{'VALASZTANA' if signal.ml['pick'] else 'nem valasztana'} "
                     f"(kuszob {signal.ml['threshold']:+.2f}R)")
    lines += exit_plan_lines(plan)

    if signal.earnings_date:
        days = market.days_until(signal.earnings_date, signal.as_of.date())
        lines.append(f"Kovetkezo jelentes: {signal.earnings_date:%Y-%m-%d} ({days} nap)")

    lines.append("")
    lines.append("Szurok:")
    for check in signal.checks:
        mark = {True: "OK ", False: "NEM", None: "?  "}[check.passed]
        lines.append(f"  [{mark}] {check.name}: {check.detail}")

    lines.append("")
    lines.append(f"Pontszam {signal.score:.0f}/100:")
    for part in signal.score_parts:
        lines.append(f"  {part.name} {part.points:.0f}/{part.maximum:.0f} - {part.reason}")

    for warning in signal.warnings:
        if not warning.startswith("Nem ellenorizheto"):   # a szurolistaban mar "?"-lel szerepel
            lines.append(f"FIGYELEM: {warning}")
    if not final_bar:
        lines.append("FIGYELEM: a napi gyertya meg nem zart le - a jelzes zarasig valtozhat.")

    lines.append("")
    lines.append("Nem befektetesi tanacs. A pontszam nem valoszinuseg.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ismetlesvedelem
# ---------------------------------------------------------------------------
class EntryState:
    """Ugyanarra a mintara es helyzetre `cooldown_days` napig nincs ujabb riasztas."""

    RETENTION_DAYS = 90

    def __init__(self, settings: Settings, read_only: bool = False) -> None:
        self.path = settings.entry_state_path
        self.cooldown = timedelta(days=settings.entry.cooldown_days)
        self.read_only = read_only
        self.sent: dict[str, str] = {}
        if self.path.exists():
            try:
                self.sent = json.loads(self.path.read_text(encoding="utf-8")).get("sent", {})
            except json.JSONDecodeError:
                log.warning("Serult entry_state.json - uresen indulunk.")

    def should_alert(self, signal: EntrySignal, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = self.sent.get(signal.key)
        if last is None:
            return True
        return now - datetime.fromisoformat(last) >= self.cooldown

    def mark(self, signal: EntrySignal, now: datetime | None = None) -> None:
        self.sent[signal.key] = (now or datetime.now(timezone.utc)).isoformat()

    def save(self) -> None:
        if self.read_only:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)
        self.sent = {k: v for k, v in self.sent.items() if datetime.fromisoformat(v) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sent": self.sent}, indent=1), encoding="utf-8")
