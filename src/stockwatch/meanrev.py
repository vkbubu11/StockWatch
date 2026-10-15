"""Visszahuzas (RSI2) - mely, rovid visszahuzodas vetele emelkedo trendben.

A kutatas (Connors / Alvarez RSI(2) es ConnorsRSI pullback) szerint egyedi
reszvenyeken ez a strategiacsalad ad magas nyeresi aranyt: emelkedo trendben egy
hirtelen, mely tuladottsag tobbnyire csak lelegzetvetel, es par napon belul visszapattan.

SZABALYOK (config.PullbackParams, alapertekek)
----------------------------------------------
  Jelzes a ZARON (t nap):
    * a papir rezsimje emelkedo (market.MarketContext: Nasdaq-100 -> QQQ,
      csak S&P 500 -> S&P 500 index, indexen kivul a papir sajat trendje)
    * zaro > SMA200 (a papir maga is emelkedo trendben)
    * RSI(2) < 2 (mely tuladottsag)
    * zaro >= 5 $, 20 napos atlagos dollarforgalom >= 20 M$
    * nincs gyorsjelentes 5 napon belul
  Belepes: a t+1 napra szolo, EGYNAPOS veteli limit 0,95 x zaro(t) aron. Csak akkor
    teljesul, ha a t+1 napi low a limit ala megy; az ar min(nyito, limit).
  Stop: limit - 3 x ATR(10), napkozben. Ha a nyito mar a stop alatt van, a nyiton zar.
  Kilepes: ha a zaron ConnorsRSI(3,2,100) > 50 -> masnap NYITASKOR. Idokorlat: 10 nap.

EGYETLEN IGAZSAGFORRAS
----------------------
A kutatas (meanrev_research.py) es az eles futas (monitor, virtualis kovetes) is
ezeket a fuggvenyeket hasznalja - ami a backtestben teljesul, az elesben is.
A szimulacio pesszimista: ha egy gyertyan a stop es a kilepesi jel is teljesulhetett,
a stopot konyveljuk; a kilepes a jel UTANI nyitason tortenik (a zarot nem latjuk elore).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from . import indicators, market
from .config import PullbackParams, Settings

PATTERN_NAME = "Visszahuzas (RSI2)"
WARMUP = 220                    # SMA200 + ConnorsRSI(100) bemelegites


# ---------------------------------------------------------------------------
# Jellemzok es feltetelek
# ---------------------------------------------------------------------------
def features(df: pd.DataFrame, params: PullbackParams) -> pd.DataFrame:
    """A dontesekhez szukseges oszlopok - csak a napig ismert adatbol (nincs elorelatas)."""
    close = df["close"].astype(float)
    out = pd.DataFrame(index=df.index)
    for col in ("open", "high", "low", "close"):
        out[col] = df[col].astype(float)
    out["sma_trend"] = indicators.sma(close, params.trend_sma)
    out["rsi2"] = indicators.rsi(close, 2)
    out["crsi"] = indicators.connors_rsi(close)
    if params.exit_sma:
        out["sma_exit"] = indicators.sma(close, params.exit_sma)
    out["atr"] = indicators.atr(df, params.atr_period)
    out["natr"] = out["atr"] / close * 100
    out["dollar_vol"] = (close * df["volume"].astype(float)).rolling(20, min_periods=20).mean()
    return out


def regime_series(feat: pd.DataFrame, regime_frame: pd.DataFrame | None) -> pd.Series:
    """Rezsim naponta: a rezsim-gyertyak (index vagy maga a papir) zaroja > SMA200 (ugyanaz, mint
    market.market_regime). Adat nelkul / tul rovid historiaval: False (nem tudjuk -> nem lepunk be)."""
    if regime_frame is None or regime_frame.empty:
        return pd.Series(False, index=feat.index)
    close = regime_frame["close"].astype(float)
    ok = (close > close.rolling(200, min_periods=200).mean()).astype(float)
    ok[close.rolling(200, min_periods=200).mean().isna()] = np.nan
    return ok.reindex(feat.index, method="ffill").fillna(0.0).astype(bool)


def setup_mask(feat: pd.DataFrame, params: PullbackParams, regime: pd.Series | None) -> pd.Series:
    mask = ((feat["close"] > feat["sma_trend"]) & (feat["rsi2"] < params.rsi2_max)
            & (feat["close"] >= params.min_price) & (feat["dollar_vol"] >= params.min_dollar_volume))
    if params.regime_filter and regime is not None:
        mask &= regime
    return mask.fillna(False)


def exit_flags(feat: pd.DataFrame, params: PullbackParams) -> pd.Series:
    flags = feat["crsi"] > params.exit_crsi
    if params.exit_sma:
        flags |= feat["close"] > feat["sma_exit"]
    return flags.fillna(False)


def order_levels(close: float, atr: float, params: PullbackParams) -> tuple[float, float]:
    """(veteli limit, stop) a jelzes napi zarojabol es ATR-jebol."""
    limit = close * (1 - params.limit_pct / 100)
    return limit, limit - params.stop_atr * atr


def earnings_blocked(signal_day: date, dates: list[date] | None, params: PullbackParams) -> bool | None:
    """Van-e gyorsjelentes a kovetkezo `earnings_blackout_days` napban. None: nem ismert."""
    if params.earnings_blackout_days <= 0:
        return False
    if not dates:
        return None
    days = market.next_earnings_days(dates, signal_day)
    return days is not None and days <= params.earnings_blackout_days


# ---------------------------------------------------------------------------
# Egy kotes lejatszasa
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    symbol: str
    signal_date: pd.Timestamp
    limit: float
    stop: float
    rsi2: float
    natr: float
    status: str = "pending"              # pending | unfilled | open | closed
    entry_date: pd.Timestamp | None = None
    entry_price: float | None = None
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""                     # signal | time | stop | gap_stop
    held: int = 0                        # kereskedesi napok a belepestol (a belepes napja is)
    gross_pct: float | None = None
    net_pct: float | None = None

    @property
    def win(self) -> bool:
        return self.net_pct is not None and self.net_pct > 0

    def to_dict(self) -> dict:
        out = asdict(self)
        for key in ("signal_date", "entry_date", "exit_date"):
            out[key] = f"{out[key]:%Y-%m-%d}" if out[key] is not None else None
        return out


def net_return(entry: float, exit_: float, cost_bps: float) -> tuple[float, float]:
    """(brutto %, netto %) - oldalankent `cost_bps` koltseg (jutalek + csuszas)."""
    c = cost_bps / 10_000
    return (exit_ / entry - 1) * 100, (exit_ * (1 - c) / (entry * (1 + c)) - 1) * 100


def simulate_trade(symbol: str, feat: pd.DataFrame, exits: np.ndarray, i: int, params: PullbackParams,
                   levels: tuple[float, float] | None = None) -> Trade:
    """A t=i napi jelzes kimenetele. `exits`: a kilepesi jel naponta (zaron). `levels`: a kikuldott
    (limit, stop) - elesben a jelzesben szereplo szintekkel kovetunk (osztalek-korrekcio utan is)."""
    idx = feat.index
    o, h, l = (feat[c].to_numpy() for c in ("open", "high", "low"))
    close_i, atr_i = float(feat["close"].iloc[i]), float(feat["atr"].iloc[i])
    limit, stop = levels or order_levels(close_i, atr_i, params)
    trade = Trade(symbol, idx[i], limit, stop, float(feat["rsi2"].iloc[i]), float(feat["natr"].iloc[i]))
    j = i + 1
    if j >= len(idx):
        return trade                                     # a limit holnapra szol - meg nem tudjuk
    if not l[j] < limit:
        trade.status = "unfilled"
        return trade
    entry = min(o[j], limit)
    trade.status, trade.entry_date, trade.entry_price, trade.held = "open", idx[j], entry, 1

    def close(k: int, price: float, reason: str) -> Trade:
        trade.status, trade.exit_date, trade.exit_price, trade.reason = "closed", idx[k], price, reason
        trade.gross_pct, trade.net_pct = net_return(entry, price, params.cost_bps)
        return trade

    # a belepes napja: nyito a stop alatt -> azonnal ki; kulonben napkozbeni stop (pesszimista)
    if o[j] <= stop:
        return close(j, o[j], "gap_stop")
    if l[j] <= stop:
        return close(j, stop, "stop")
    pending = "signal" if exits[j] else ("time" if trade.held >= params.time_stop else "")
    for k in range(j + 1, len(idx)):
        if pending:
            return close(k, o[k], pending)               # a zaron szuletett jel -> kovetkezo nyitas
        trade.held += 1
        if o[k] <= stop:
            return close(k, o[k], "gap_stop")
        if l[k] <= stop:
            return close(k, stop, "stop")
        pending = "signal" if exits[k] else ("time" if trade.held >= params.time_stop else "")
    trade.reason = pending                               # nyitott; ha van jel, holnap nyitaskor zar
    return trade


# ---------------------------------------------------------------------------
# Eles jelzes (monitor) - ugyanazokkal a feltetelekkel
# ---------------------------------------------------------------------------
def evaluate(symbol: str, df: pd.DataFrame, settings: Settings, params: PullbackParams,
             regime: market.Regime | None, earnings_date: date | None = None,
             strategy: str = "pullback"):
    """Jelzes a LEZART utolso gyertyan, ha az RSI(2) a kuszob alatt van (a tobbi feltetel
    a szurok kozott latszik, igy a naplo azt is mutatja, miert maradt ki). None: nincs jelolt."""
    from . import entry
    from .patterns.base import PatternMatch

    if df is None or len(df) < WARMUP:
        return None
    feat = features(df, params)
    last = feat.iloc[-1]
    if not last["rsi2"] < params.rsi2_max or pd.isna(last["atr"]):
        return None
    as_of, close, atr = feat.index[-1], float(last["close"]), float(last["atr"])
    limit, stop = order_levels(close, atr, params)

    checks = [
        entry.Check("RSI(2)", True, f"{last['rsi2']:.1f} < {params.rsi2_max:g}"),
        entry.Check(f"Trend (SMA{params.trend_sma})", bool(close > last["sma_trend"]),
                    f"zaro {close:.2f} / SMA{params.trend_sma} {last['sma_trend']:.2f}"),
        entry.Check("Ar", close >= params.min_price, f"{close:.2f} $ (min. {params.min_price:g})"),
        entry.Check("Dollarforgalom", bool(last["dollar_vol"] >= params.min_dollar_volume),
                    f"{last['dollar_vol'] / 1e6:.0f} M$ / nap (min. {params.min_dollar_volume / 1e6:.0f})"),
    ]
    if params.regime_filter:
        checks.append(entry.Check(regime.name or "Piac", regime.ok, regime.text) if regime is not None
                      else entry.Check("Piac", None, "rezsim nem ismert"))
    days = market.days_until(earnings_date, as_of.date())
    if params.earnings_blackout_days > 0:
        if days is None:
            checks.append(entry.Check("Gyorsjelentes", None, "a datum nem ismert"))
        else:
            checks.append(entry.Check("Gyorsjelentes", days > params.earnings_blackout_days,
                                      f"{days} nap mulva (tilalom: {params.earnings_blackout_days} nap)"))

    passed = all(c.passed is not False for c in checks)
    match = PatternMatch(
        symbol=symbol, name=PATTERN_NAME, direction="bullish", status="triggered",
        quality=float(max(0.0, 1 - last["rsi2"] / params.rsi2_max)),
        trigger_level=limit, target=close, invalidation=stop,
        start_date=as_of, end_date=as_of, trigger_date=as_of,
        extras={"engine": "meanrev", "rsi2": round(float(last["rsi2"]), 2), "crsi": round(float(last["crsi"]), 1),
                "natr": round(float(last["natr"]), 2), "exit_crsi": params.exit_crsi, "exit_sma": params.exit_sma,
                "time_stop": params.time_stop},
    )
    cfg = settings.entry
    plan = entry.TradePlan(entry=limit, entry_limit=limit, stop=stop, target=close, atr=atr,
                           shares=entry.position_size(limit, stop, cfg), account_size=cfg.account_size,
                           risk_pct=cfg.risk_per_trade_pct, exit_mode="indicator", horizon=params.time_stop)
    signal = entry.EntrySignal(
        symbol=symbol, kind="pullback", as_of=as_of, price=close, pattern=match, plan=plan, checks=checks,
        score_parts=[entry.ScorePart("Visszahuzas", 100.0 if passed else 0.0, 100,
                                     "minden szabaly teljesult" if passed else "valamelyik szabaly nem teljesult")],
        history_text=f"RSI(2) {last['rsi2']:.1f}, ConnorsRSI {last['crsi']:.0f}, NATR {last['natr']:.1f}%",
        earnings_date=earnings_date, strategy=strategy,
    )
    for check in checks:
        if check.passed is None:
            signal.warnings.append(f"Nem ellenorizheto - {check.name}: {check.detail}")
    return signal


def rank_key(signal) -> tuple:
    """A napi rangsor: a legalacsonyabb RSI(2) elol, egyenlonel a nagyobb NATR (ugyanaz, mint a kutatasban)."""
    extras = signal.pattern.extras or {}
    return (extras.get("rsi2", 100.0), -extras.get("natr", 0.0), signal.symbol)


def format_title(signal) -> str:
    return f"{signal.symbol}: VISSZAHUZAS - veteli limit holnapra {signal.plan.entry_limit:.2f} $"


def format_body(signal, params: PullbackParams) -> str:
    plan, extras = signal.plan, signal.pattern.extras or {}
    lines = [
        f"Jelzes napja: {signal.as_of:%Y-%m-%d}  |  zaro {signal.price:.2f} $",
        f"VETELI LIMIT holnapra (egynapos): {plan.entry_limit:.2f} $  (-{params.limit_pct:g}%)",
        f"Stop: {plan.stop:.2f} $  ({params.stop_atr:g} x ATR)  |  darab: {plan.shares}",
        f"Kilepes: ha a zaro ConnorsRSI {params.exit_crsi:g} fole megy"
        + (f" vagy a zaro az SMA{params.exit_sma} fole er" if params.exit_sma else "")
        + f" -> masnap NYITASKOR; legkesobb {params.time_stop} nap utan.",
        f"Most: RSI(2) {extras.get('rsi2')}, ConnorsRSI {extras.get('crsi')}, NATR {extras.get('natr')}%",
        "",
        "Ha a limit holnap nem teljesul, a jelzes ervenyet veszti (ne vegyel utana).",
    ]
    news = getattr(signal, "news", None)
    if news:
        from .sentiment.service import summary

        lines.append(f"Hirek (tajekoztato, nem dont): {summary(news)}")
    for w in signal.warnings:
        lines.append(f"Figyelem: {w}")
    lines += ["", "Nem befektetesi tanacs."]
    return "\n".join(lines)
