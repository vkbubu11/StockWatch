"""Momentum (12-1) - havi rotacio a legerosebb papirokba.

Forras: Kakushadze-Serur: 151 Trading Strategies (2018), 3.1 "ar-momentum": a papirokat a multbeli
hozamuk szerint rangsoroljuk (12 honapos ablak, a legutobbi honap NELKUL - az rovid tavon inkabb
visszafordul), es a legjobbakat tartjuk egy honapig. A rendszer csak long oldalt kereskedik.

SZABALYOK (config.MomentumParams, alapertekek)
----------------------------------------------
  Rebalansz: a honap UTOLSO kereskedesi napjanak zaroja.
    * pontszam: zaro(t-21) / zaro(t-252) - 1   (12 honap, az utolso ~1 honap kihagyva)
    * jogosult: zaro >= 5 $, 20 napos atlagos dollarforgalom >= 20 M$, eleg hosszu historia
    * a tartott papir MARAD, amig a rangja a legjobb 20-on (buffer) belul van; kulonben kilep
    * az ures helyekre (osszesen 10 = top_n) a legjobb rangu, meg nem tartott papirok jonnek
  Belepes / kilepes: a rebalanszt koveto NYITASON (piaci megbizas), egyenlo sullyal (tőke / top_n).
  Stop: alapbol nincs (a validacio szerint a 15-25%-os stop rontott); `stop_pct` > 0 eseten napkozben.

EGYETLEN IGAZSAGFORRAS: a kutatas (momentum_research.py), az eles futas (monitor.momentum_cycle) es a
virtualis kovetes (positions.py) is ezeket a fuggvenyeket hasznalja.

FIGYELEM: a momentum nagy visszaeseseket hozhat (a multbeli tesztben ~38-48%), es hirtelen piaci
fordulokor (pl. 2009 tavasza) a legerosebb papirok zuhannak a legnagyobbat. Nem befektetesi tanacs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from .config import MomentumParams, Settings

PATTERN_NAME = "Momentum (12-1)"
MARKET_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Pontszam es rangsor
# ---------------------------------------------------------------------------
def score_frame(close: pd.DataFrame, volume: pd.DataFrame | None, params: MomentumParams) -> pd.DataFrame:
    """Napi pontszam papironkent (oszlop = papir). NaN: nem jogosult aznap (rovid historia, olcso,
    illikvid). Csak a napig ismert adatbol szamol (nincs elorelatas)."""
    score = close.shift(params.skip) / close.shift(params.lookback) - 1
    ok = close >= params.min_price
    if volume is not None:
        dollar = (close * volume).rolling(20, min_periods=20).mean()
        ok &= dollar >= params.min_dollar_volume
    return score.where(ok)


def score_one(df: pd.DataFrame, params: MomentumParams) -> float | None:
    """Egy papir pontszama az utolso gyertyan (None: nem jogosult)."""
    if df is None or len(df) <= params.lookback:
        return None
    close = df[["close"]].astype(float)
    volume = df[["volume"]].astype(float).set_axis(["close"], axis=1)
    s = score_frame(close, volume, params)["close"].iloc[-1]
    return None if pd.isna(s) else float(s)


def ranking(scores: dict[str, float]) -> list[str]:
    """A jogosult papirok csokkeno pontszam szerint (azonos pontnal ABC - determinisztikus)."""
    return [s for s, _ in sorted(((s, v) for s, v in scores.items() if v is not None and not np.isnan(v)),
                                 key=lambda x: (-x[1], x[0]))]


@dataclass
class Decision:
    keep: list[str]
    sell: list[str]
    buy: list[str]
    ranks: dict[str, int]          # 1-tol

    def to_dict(self) -> dict:
        return asdict(self)


def decide(ranked: list[str], held: list[str], params: MomentumParams) -> Decision:
    """A rebalansz dontese: ki marad, ki megy, ki jon (lasd a modul leirasat)."""
    ranks = {s: i + 1 for i, s in enumerate(ranked)}
    keep = [s for s in held if ranks.get(s, 10**9) <= params.buffer]
    sell = [s for s in held if s not in keep]
    slots = max(params.top_n - len(keep), 0)
    buy = [s for s in ranked if s not in keep][:slots]
    return Decision(keep=keep, sell=sell, buy=buy, ranks=ranks)


# ---------------------------------------------------------------------------
# Naptar: a honap utolso kereskedesi napja
# ---------------------------------------------------------------------------
def _sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex | None:
    try:
        import pandas_market_calendars as mcal

        days = mcal.get_calendar("NYSE").valid_days(start_date=start, end_date=end)
        return pd.DatetimeIndex(days).tz_localize(None).normalize()
    except Exception:
        return None


def is_rebalance_day(day: pd.Timestamp | date) -> bool:
    """A honap utolso kereskedesi napja-e (NYSE naptar; ha nem erheto el: munkanap-kozelites)."""
    day = pd.Timestamp(day).normalize()
    if day.tzinfo is not None:
        day = day.tz_localize(None)
    days = _sessions(day, day + pd.Timedelta(days=10))
    if days is not None and len(days):
        if day not in days:
            return False
        later = days[days > day]
        return len(later) == 0 or later[0].month != day.month
    return day.weekday() < 5 and (day + pd.offsets.BDay(1)).month != day.month


def rebalance_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """A kutatashoz: a gyertyasor honaponkenti utolso napja (a teljes naptar = az adat napjai)."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).max().to_numpy())


# ---------------------------------------------------------------------------
# Egy kotes lejatszasa (virtualis kovetes) - ugyanaz a vegrehajtas, mint a kutatasban
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    symbol: str
    signal_date: pd.Timestamp
    status: str = "pending"              # pending | open | closed
    entry_date: pd.Timestamp | None = None
    entry_price: float | None = None
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""                     # rotation | stop | gap_stop
    held: int = 0
    gross_pct: float | None = None
    net_pct: float | None = None


def net_return(entry: float, exit_: float, cost_bps: float) -> tuple[float, float]:
    c = cost_bps / 10_000
    return (exit_ / entry - 1) * 100, (exit_ * (1 - c) / (entry * (1 + c)) - 1) * 100


def simulate_trade(symbol: str, df: pd.DataFrame, i: int, params: MomentumParams,
                   exit_signal: pd.Timestamp | None = None, entry_price: float | None = None) -> Trade:
    """A t=i napi (rebalansz) vetel kimenetele: belepes a kovetkezo NYITASON; kilepes az `exit_signal`
    napjat (a rebalansz zarojat) koveto nyitason, vagy a stopon (ha van). `entry_price`: a mar ismert
    (pl. paper) belepo - kulonben a nyito."""
    idx = df.index
    o, l = df["open"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    trade = Trade(symbol, idx[i])
    j = i + 1
    if j >= len(idx):
        return trade
    entry = float(entry_price) if entry_price else float(o[j])
    trade.status, trade.entry_date, trade.entry_price, trade.held = "open", idx[j], entry, 1
    stop = entry * (1 - params.stop_pct / 100) if params.stop_pct > 0 else None

    def close(k: int, price: float, reason: str) -> Trade:
        trade.status, trade.exit_date, trade.exit_price, trade.reason = "closed", idx[k], float(price), reason
        trade.gross_pct, trade.net_pct = net_return(entry, float(price), params.cost_bps)
        return trade

    for k in range(j, len(idx)):
        if k > j:
            if exit_signal is not None and idx[k - 1] >= exit_signal:
                return close(k, o[k], "rotation")              # a rebalansz zaroja utani nyitas
            trade.held += 1
            if stop is not None and o[k] <= stop:
                return close(k, o[k], "gap_stop")
        if stop is not None and l[k] <= stop:
            return close(k, stop, "stop")
    if exit_signal is not None and idx[-1] >= exit_signal:
        trade.reason = "rotation"                              # holnap nyitaskor zar
    return trade


# ---------------------------------------------------------------------------
# Eles jelzes
# ---------------------------------------------------------------------------
def make_signal(symbol: str, df: pd.DataFrame, settings: Settings, params: MomentumParams, rank: int,
                score: float, universe: int, equity: float | None = None):
    """Belepesi jelzes (EntrySignal) a rebalansz napjanak zarojabol. A darabszam: toke / top_n."""
    from . import entry
    from .patterns.base import PatternMatch

    as_of = df.index[-1]
    close = float(df["close"].iloc[-1])
    cfg = settings.entry
    account = equity or cfg.account_size
    shares = int(account / params.top_n // close) if close > 0 else 0
    stop = close * (1 - params.stop_pct / 100) if params.stop_pct > 0 else close * (1 - params.r_unit_pct / 100)
    match = PatternMatch(
        symbol=symbol, name=PATTERN_NAME, direction="bullish", status="triggered",
        quality=float(max(0.0, 1 - (rank - 1) / max(params.top_n, 1))),
        trigger_level=close, target=close, invalidation=stop,
        start_date=df.index[max(0, len(df) - 1 - params.lookback)], end_date=as_of, trigger_date=as_of,
        extras={"engine": "momentum", "rank": rank, "universe": universe, "score": round(score * 100, 2),
                "top_n": params.top_n, "buffer": params.buffer, "stop_pct": params.stop_pct},
    )
    plan = entry.TradePlan(entry=close, entry_limit=close, stop=stop, target=close, atr=0.0, shares=shares,
                           account_size=account, risk_pct=100 / params.top_n, exit_mode="indicator", horizon=0)
    # A validalt szabaly TISZTAN relativ: a legjobb `top_n` papir akkor is, ha a hozama negativ (a
    # rezsim- / abszolut momentum-szuro a 2017-2025-os adaton rontott). Az elojel csak tajekoztat.
    checks = [
        entry.Check("Rang", rank <= params.top_n, f"{rank}. / {universe} (kell: legjobb {params.top_n})"),
        entry.Check("12-1 havi hozam", True, f"{score * 100:+.1f}% (tajekoztato - a rang dont)"),
    ]
    signal = entry.EntrySignal(
        symbol=symbol, kind="momentum", as_of=as_of, price=close, pattern=match, plan=plan, checks=checks,
        score_parts=[entry.ScorePart("Momentum-rang", 100.0 if rank <= params.top_n else 0.0, 100,
                                     f"{rank}. hely a {universe} papirbol")],
        history_text=f"12-1 havi hozam {score * 100:+.1f}%, rang {rank}/{universe}", strategy="momentum",
    )
    if score <= 0:
        signal.warnings.append("A 12-1 havi hozam negativ - gyenge piac; a szabaly ilyenkor is a legjobbakat veszi.")
    if shares == 0:
        signal.warnings.append("A szamlameret mellett 0 darab jonne ki (drága papir / kicsi szamla).")
    return signal


def format_title(signal) -> str:
    x = signal.pattern.extras or {}
    return f"{signal.symbol}: MOMENTUM - vetel holnap nyitaskor ({x.get('rank')}. hely)"


def format_body(signal, params: MomentumParams) -> str:
    x, plan = signal.pattern.extras or {}, signal.plan
    lines = [
        f"Havi rebalansz: {signal.as_of:%Y-%m-%d}  |  zaro {signal.price:.2f} $",
        f"Rang: {x.get('rank')}. / {x.get('universe')}  |  12-1 havi hozam: {x.get('score'):+.1f}%",
        f"VETEL holnap NYITASKOR (piaci megbizas): {plan.shares} db  (~{100 / params.top_n:.0f}% toke)",
        f"Tartas: amig a rangja a legjobb {params.buffer}-on belul marad (havi ellenorzes, a honap utolso napjan).",
        (f"Stop: {plan.stop:.2f} $ (-{params.stop_pct:g}%)" if params.stop_pct > 0 else
         "Stop: nincs (a validacio szerint ront) - a kockazatot a 10 papiros szoras es a havi rotacio kezeli."),
    ]
    for w in signal.warnings:
        lines.append(f"Figyelem: {w}")
    lines += ["", "A momentum nagy visszaeseseket hozhat. Nem befektetesi tanacs."]
    return "\n".join(lines)
