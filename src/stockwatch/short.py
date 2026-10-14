"""Short belepesi motor - KISERLETI, a long motor (entry.py) tukorkepe.

FIGYELEM: a short oldal NINCS backtesttel igazolva. A korabbi alakzat-backtest szerint a medves
alakzatok gyengebbek (pl. bear zaszlo: 28% talalat, -0.16R), ezert a felulet "kiserleti" jelolessel
mutatja, es a paper trading NEM kuldi be. A celja: a "Legjobb belepo most" gomb short oldala
ugyanazzal a fegyelemmel (szurok, terv, meret) dolgozzon, mint a long.

Helyzetek (a long tukre):
  breakdown  a medves minta TEGNAP tort le, es a mai zaro is a szint alatt van (megerosites)
  setup      a minta meg alakul, az ar <= 1 ATR-re a trigger felett - meg nem eladas

Kemeny szurok (a long tukre; a kuszobok az EntrySettings-bol):
  Trend       zaro < SMA200 es EMA50 < EMA200
  RSI         100-rsi_max ... 100-rsi_min (alapbol 28-55): ne legyen eros, de ne is tulzott eladott
  Tavolsag    max. max_extension_atr ATR a trigger alatt (nem uldozzuk lefele)
  H/K         >= min_risk_reward, a tenyleges belepobol
  Stop        <= max_stop_pct
  Piac        benchmark (QQQ) SMA200 ALATT (bikapiacon a letoresek tobbsege elbukik)
  Earnings    jelentes elott nincs belepes
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import market
from .config import EntrySettings, Settings
from .entry import Check, ScorePart, _atr, pattern_horizon
from .patterns.base import PatternMatch

KIND_LABELS = {"breakdown": "Megerősített letörés", "setup": "Előkészület (short)"}


@dataclass
class ShortPlan:
    entry: float              # tervezett eladasi ar (referencia)
    entry_limit: float        # ennel olcsobban mar ne adjunk el (gap-vedelem)
    stop: float               # a belepo FELETT
    target: float             # a belepo ALATT
    atr: float
    shares: int
    horizon: int = 0

    @property
    def risk_per_share(self) -> float:
        return self.stop - self.entry

    @property
    def risk_reward(self) -> float:
        risk = self.risk_per_share
        return (self.entry - self.target) / risk if risk > 0 else 0.0

    @property
    def stop_pct(self) -> float:
        return self.risk_per_share / self.entry * 100 if self.entry else 0.0

    @property
    def target_pct(self) -> float:
        return (self.entry - self.target) / self.entry * 100 if self.entry else 0.0

    def to_dict(self) -> dict:
        return {"entry": round(self.entry, 2), "entry_limit": round(self.entry_limit, 2), "stop": round(self.stop, 2),
                "target": round(self.target, 2), "risk_reward": round(self.risk_reward, 2),
                "stop_pct": round(self.stop_pct, 2), "target_pct": round(self.target_pct, 2),
                "shares": self.shares, "position_value": round(self.shares * self.entry, 2),
                "risk_amount": round(self.shares * self.risk_per_share, 2), "horizon": self.horizon}


@dataclass
class ShortSignal:
    symbol: str
    kind: str
    as_of: pd.Timestamp
    price: float
    pattern: PatternMatch
    plan: ShortPlan
    checks: list[Check] = field(default_factory=list)
    score_parts: list[ScorePart] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    earnings_date: date | None = None

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
        return self.passed and self.score >= cfg.min_score and (self.kind != "setup" or cfg.alert_setups)

    def to_dict(self, cfg: EntrySettings) -> dict:
        return {
            "side": "short", "symbol": self.symbol, "kind": self.kind, "label": KIND_LABELS.get(self.kind, self.kind),
            "as_of": str(self.as_of.date()), "price": round(self.price, 2), "score": self.score,
            "passed": self.passed, "qualifies": self.qualifies(cfg), "pattern": self.pattern.name,
            "pattern_quality": round(self.pattern.quality, 3), "trigger_level": round(self.pattern.trigger_level, 2),
            "plan": self.plan.to_dict(),
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
            "failed": [c.name for c in self.failed_checks],
            "score_parts": [{"name": p.name, "points": round(p.points, 1), "max": p.maximum, "reason": p.reason}
                            for p in self.score_parts],
            "earnings_date": self.earnings_date.isoformat() if self.earnings_date else None,
            "warnings": self.warnings, "experimental": True,
        }


def classify(match: PatternMatch, df: pd.DataFrame, cfg: EntrySettings) -> str | None:
    if match.direction != "bearish" or len(df) < 2:
        return None
    close = float(df["close"].iloc[-1])
    if match.status == "triggered" and match.trigger_date is not None:
        if cfg.confirm_breakout:
            if match.trigger_date == df.index[-2] and close < match.trigger_level:
                return "breakdown"
        elif match.trigger_date == df.index[-1]:
            return "breakdown"
        return None
    atr = _atr(df)
    if match.status == "forming" and atr > 0:
        distance = (close - match.trigger_level) / atr
        if 0 <= distance <= cfg.setup_max_distance_atr:
            return "setup"
    return None


def position_size(entry: float, stop: float, cfg: EntrySettings) -> int:
    risk = stop - entry
    if entry <= 0 or risk <= 0:
        return 0
    by_risk = cfg.account_size * cfg.risk_per_trade_pct / 100 / risk
    by_cap = cfg.account_size * cfg.max_position_pct / 100 / entry
    return max(int(math.floor(min(by_risk, by_cap))), 0)


def build_plan(match: PatternMatch, kind: str, df: pd.DataFrame, cfg: EntrySettings, horizon: int = 0) -> ShortPlan:
    close = float(df["close"].iloc[-1])
    atr = _atr(df)
    entry = match.trigger_level if kind == "setup" else close
    stop = max(match.invalidation, entry + cfg.min_stop_atr * atr)
    return ShortPlan(entry=entry, entry_limit=entry * (1 - cfg.entry_max_gap_pct / 100), stop=stop,
                     target=match.target, atr=atr, shares=position_size(entry, stop, cfg), horizon=horizon)


def run_checks(match: PatternMatch, kind: str, df: pd.DataFrame, plan: ShortPlan, cfg: EntrySettings,
               regime: market.Regime | None, earnings_days: int | None) -> list[Check]:
    last = df.iloc[-1]
    close = float(last["close"])
    checks: list[Check] = []
    if cfg.require_uptrend:            # a long "emelkedo trend" kovetelmenyenek tukre
        sma200 = df["close"].rolling(200, min_periods=200).mean().iloc[-1]
        ema50, ema200 = last.get("ema50"), last.get("ema200")
        if pd.isna(sma200) or pd.isna(ema50) or pd.isna(ema200):
            checks.append(Check("Trend (lefele)", None, "tul rovid historia a 200 napos atlaghoz"))
        else:
            checks.append(Check("Trend (lefele)", bool(close < sma200 and ema50 < ema200),
                                f"zaro {'<' if close < sma200 else '>'} SMA200 ({sma200:.2f}), "
                                f"EMA50 {'<' if ema50 < ema200 else '>'} EMA200"))
    rsi = last.get("rsi")
    lo, hi = 100 - cfg.rsi_max, 100 - cfg.rsi_min
    checks.append(Check("RSI", None, "nincs RSI ertek") if pd.isna(rsi) else
                  Check("RSI", bool(lo <= rsi <= hi), f"{rsi:.0f} (sav {lo:.0f}-{hi:.0f})"))
    if kind == "breakdown" and plan.atr > 0:
        ext = (match.trigger_level - close) / plan.atr
        checks.append(Check("Tavolsag a triggertol", bool(ext <= cfg.max_extension_atr),
                            f"{ext:.1f} ATR a trigger alatt (max. {cfg.max_extension_atr:.1f})"))
    checks.append(Check("Hozam/kockazat", bool(plan.risk_reward >= cfg.min_risk_reward),
                        f"1 : {plan.risk_reward:.2f} (min. 1 : {cfg.min_risk_reward:.1f})"))
    checks.append(Check("Stop merete", bool(0 < plan.stop_pct <= cfg.max_stop_pct),
                        f"{plan.stop_pct:.1f}% (max. {cfg.max_stop_pct:.0f}%)"))
    if cfg.regime_filter:
        if regime is None or regime.ok is None:
            checks.append(Check(f"Piac ({cfg.benchmark})", None, "nincs benchmark adat"))
        else:
            checks.append(Check(f"Piac ({cfg.benchmark})", not regime.ok,
                                "a benchmark az SMA200 ALATT (short-barat)" if not regime.ok
                                else "a benchmark az SMA200 felett - bikapiacon a letoresek tobbsege elbukik"))
    if cfg.earnings_blackout_days > 0:
        checks.append(Check("Earnings", None, "a kovetkezo jelentes datuma nem ismert") if earnings_days is None else
                      Check("Earnings", earnings_days > cfg.earnings_blackout_days,
                            f"jelentes {earnings_days} nap mulva (tilalom {cfg.earnings_blackout_days} nap)"))
    return checks


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(match: PatternMatch, kind: str, df: pd.DataFrame, plan: ShortPlan, rel: float | None) -> list[ScorePart]:
    last = df.iloc[-1]
    adx, slope = last.get("adx"), last.get("ema50_slope")
    vol = df["vol_ratio"].loc[match.trigger_date] if match.trigger_date in df.index and "vol_ratio" in df else math.nan
    rsi = last.get("rsi")
    trend = (0 if pd.isna(adx) else _clamp(adx / 40) * 10) + (0 if pd.isna(slope) else _clamp(-slope / 3) * 10)
    rsi_pts = 0.0 if pd.isna(rsi) else (10.0 if 35 <= rsi <= 50 else max(0.0, 10 - abs(rsi - 42.5) / 2))
    return [
        ScorePart("Mintaminoseg", 25 * _clamp(match.quality), 25, f"{match.quality:.0%}"),
        ScorePart("Trenderosseg (lefele)", trend, 20, f"ADX {adx:.0f}, EMA50 meredekseg {slope:+.1f}%"
                  if not (pd.isna(adx) or pd.isna(slope)) else "nincs adat"),
        ScorePart("Letoresi volumen", 0.0 if pd.isna(vol) or kind == "setup" else 15 * _clamp(vol - 1), 15,
                  "elokeszulet" if kind == "setup" else (f"{vol:.2f}x atlag" if not pd.isna(vol) else "nincs adat")),
        ScorePart("Relativ gyengeseg", 0.0 if rel is None else 15 * _clamp(-rel / 10), 15,
                  "nincs benchmark" if rel is None else f"{rel:+.1f} pp a benchmarkhoz kepest (60 nap)"),
        ScorePart("RSI", rsi_pts, 10, "35-50 az idealis" if not pd.isna(rsi) else "nincs adat"),
        ScorePart("Hozam/kockazat", 15 * _clamp((plan.risk_reward - 1.5) / 2), 15, f"1 : {plan.risk_reward:.2f}"),
    ]


def evaluate(symbol: str, df: pd.DataFrame, matches: list[PatternMatch], settings: Settings,
             benchmark: pd.DataFrame | None = None, earnings_date: date | None = None) -> list[ShortSignal]:
    """Minden medves mintat kiertekel (a szuron elbukottakat is - a `failed_checks` mondja meg, miert)."""
    cfg = settings.entry
    if df.empty:
        return []
    as_of = df.index[-1]
    regime = market.market_regime(benchmark, as_of) if benchmark is not None else None
    rel = market.relative_strength(df, benchmark, as_of) if benchmark is not None else None
    earnings_days = market.days_until(earnings_date, as_of.date())
    out, seen = [], set()
    for match in matches:
        kind = classify(match, df, cfg)
        if kind is None or f"{match.name}|{kind}" in seen:
            continue
        seen.add(f"{match.name}|{kind}")
        plan = build_plan(match, kind, df, cfg, horizon=pattern_horizon(match, df, settings))
        if not plan.stop > plan.entry > plan.target:
            continue
        signal = ShortSignal(symbol, kind, as_of, float(df["close"].iloc[-1]), match, plan,
                             checks=run_checks(match, kind, df, plan, cfg, regime, earnings_days),
                             score_parts=score(match, kind, df, plan, rel), earnings_date=earnings_date)
        signal.warnings.append("KISERLETI: a short oldal nincs backtesttel igazolva, a paper trading nem kuldi be.")
        out.append(signal)
    out.sort(key=lambda s: (s.passed, s.score), reverse=True)
    return out
