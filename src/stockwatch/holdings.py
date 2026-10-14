"""Sajat, kezzel felvett poziciok - es azok technikai kiertekelese.

A `positions.py` a RENDSZER jelzeseit koveti virtualisan (az az elo
eredmenynaplo). Ez a modul mas: itt a felhasznalo a sajat, a brokernel mar
LETEZO poziciojat veszi fel (ticker, belepo, darab, opcionalisan stop/cel), es
a rendszer ugyanazzal a motorral ertekeli ki, amivel a belepeseket:

    * trend (SMA200, EMA50/EMA200, EMA20/EMA50)
    * momentum (RSI)
    * medves formaciok a charton (fej-vall, dupla teto, ...)
    * kozeli rezisztencia, a kovetkezo support
    * gyorsjelentes kozelsege, piaci rezsim, kompozit iranyjelzes
    * stop: van-e, hol van, kiutotte-e az ar

Az eredmeny egy osszesitett minosites (Rendben / Figyelj / Kockazatos) az
okokkal, plusz stop- es celar-javaslat. A monitor zaras utan ellenorzi, es
ertesit, ha a stopot vagy a celt elerte az ar, ha romlott a minosites, vagy ha
kozeleg a jelentes.

A sajat poziciok NEM kerulnek a rendszer eredmenynaplojaba - az a jelzesek
oszinte nyilvantartasa marad. Nem befektetesi tanacs: a rendszer nem tud a
szamladrol, csak azt latja, amit itt megadsz.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import market
from .config import Settings
from .forecast import Forecast

log = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")
MAX_OPEN = 50            # egyszerre ennyi nyitott sajat pozicio
MAX_CLOSED = 200         # lezart sajat poziciok megorzese (a legregebbiek esnek ki)

VERDICTS = {
    "ok": "Rendben",
    "warn": "Figyelj",
    "bad": "Kockazatos",
}
_RANK = {"ok": 0, "warn": 1, "bad": 2}


# ---------------------------------------------------------------------------
# Adatszerkezetek
# ---------------------------------------------------------------------------
@dataclass
class Holding:
    id: str
    symbol: str
    entry_price: float
    entry_date: str                  # YYYY-MM-DD
    shares: float
    stop: float | None = None
    target: float | None = None
    note: str = ""
    status: str = "open"             # open | closed
    created_at: str = ""
    closed_at: str | None = None
    close_price: float | None = None
    last_verdict: str | None = None  # az utolso ertesitesnel latott minosites
    alerted: dict[str, str] = field(default_factory=dict)   # esemeny -> datum (ismetlesvedelem)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def realised_pnl(self) -> float | None:
        if self.close_price is None:
            return None
        return (self.close_price - self.entry_price) * self.shares


@dataclass
class HoldingCheck:
    name: str
    level: str          # ok | warn | bad | info
    detail: str


@dataclass
class HoldingEvaluation:
    holding: Holding
    as_of: pd.Timestamp
    price: float
    atr: float
    highest: float                   # legmagasabb ar a belepes ota
    suggested_stop: float
    suggested_stop_text: str
    target: float | None
    target_source: str
    checks: list[HoldingCheck]
    earnings_date: date | None = None
    stop_hit: bool = False
    target_hit: bool = False
    label: str = ""                  # a kompozit iranyjelzes szovege

    @property
    def verdict(self) -> str:
        levels = [c.level for c in self.checks]
        if "bad" in levels:
            return "bad"
        if "warn" in levels:
            return "warn"
        return "ok"

    @property
    def verdict_text(self) -> str:
        return VERDICTS[self.verdict]

    @property
    def reasons(self) -> list[str]:
        """A minositest meghatarozo (bad/warn) szempontok, sulyossag szerint."""
        ordered = sorted((c for c in self.checks if c.level in ("bad", "warn")),
                         key=lambda c: -_RANK[c.level])
        return [f"{c.name}: {c.detail}" for c in ordered]

    @property
    def pnl(self) -> float:
        return (self.price - self.holding.entry_price) * self.holding.shares

    @property
    def pnl_pct(self) -> float:
        return (self.price / self.holding.entry_price - 1) * 100

    @property
    def value(self) -> float:
        return self.price * self.holding.shares

    @property
    def r_multiple(self) -> float | None:
        """Eredmeny R-ben - csak ha a stop a belepo alatt van (kulonben nincs 1R)."""
        stop = self.holding.stop
        if stop is None or stop >= self.holding.entry_price:
            return None
        return (self.price - self.holding.entry_price) / (self.holding.entry_price - stop)

    @property
    def pnl_at_stop(self) -> float | None:
        """Ha most kiutne a stop: ennyi lenne az eredmeny (a belepohoz kepest)."""
        if self.holding.stop is None:
            return None
        return (self.holding.stop - self.holding.entry_price) * self.holding.shares

    def to_dict(self) -> dict:
        h = self.holding
        return {
            **asdict(h),
            "as_of": self.as_of.strftime("%Y-%m-%d"),
            "price": self.price,
            "atr": self.atr,
            "value": self.value,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "r_multiple": self.r_multiple,
            "pnl_at_stop": self.pnl_at_stop,
            "highest": self.highest,
            "suggested_stop": self.suggested_stop,
            "suggested_stop_text": self.suggested_stop_text,
            "eval_target": self.target,
            "target_source": self.target_source,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "reasons": self.reasons,
            "checks": [asdict(c) for c in self.checks],
            "earnings_date": self.earnings_date.isoformat() if self.earnings_date else None,
            "stop_hit": self.stop_hit,
            "target_hit": self.target_hit,
            "label": self.label,
        }


# ---------------------------------------------------------------------------
# Kiertekeles
# ---------------------------------------------------------------------------
def evaluate(
    holding: Holding,
    df: pd.DataFrame,
    forecast: Forecast | None,
    settings: Settings,
    benchmark: pd.DataFrame | None = None,
    earnings_date: date | None = None,
) -> HoldingEvaluation:
    """Egy sajat pozicio kiertekelese az utolso lezart gyertyan.

    `df` az indikatorokkal dusitott sorozat (indicators.enrich).
    """
    cfg = settings.entry
    last = df.iloc[-1]
    as_of = df.index[-1]
    close = float(last["close"])
    atr = float(last["atr"]) if pd.notna(last.get("atr")) and last["atr"] > 0 else close * 0.02

    since = df[df.index >= pd.Timestamp(holding.entry_date)]
    highest = float(since["high"].max()) if not since.empty else close
    highest = max(highest, holding.entry_price, close)

    checks: list[HoldingCheck] = []

    # 1. Stop
    stop = holding.stop
    stop_hit = stop is not None and float(last["low"]) <= stop
    chandelier = highest - cfg.trail_atr * atr
    suggested = min(chandelier, close - atr)          # a napi zajnal kozelebb ne legyen
    suggested_text = (
        f"kovetett stop: a belepes ota mert csucs ({highest:.2f}) - {cfg.trail_atr:.1f} ATR"
    )
    if forecast is not None and forecast.support is not None and forecast.support.price < close:
        suggested_text += f"; legkozelebbi support: {forecast.support.price:.2f}"

    if stop is None:
        checks.append(HoldingCheck(
            "Stop", "warn", f"nincs megadva - javaslat: {suggested:.2f} $ ({suggested_text})"))
    elif stop_hit:
        checks.append(HoldingCheck(
            "Stop", "bad", f"az ar elerte a stopot ({stop:.2f} $, napi alj {float(last['low']):.2f} $)"))
    else:
        dist_atr = (close - stop) / atr
        detail = f"{stop:.2f} $ ({(stop / close - 1) * 100:+.1f}%, {dist_atr:.1f} ATR a zarotol)"
        if stop < suggested - 0.5 * atr and close > holding.entry_price:
            detail += f" - nyereseges pozicional feljebb huzhato, pl. {suggested:.2f} $-ra"
            checks.append(HoldingCheck("Stop", "info", detail))
        else:
            checks.append(HoldingCheck("Stop", "ok", detail))

    # 1b. Visszaeses a belepes ota mert csucsrol
    drop_atr = (highest - close) / atr
    if chandelier > close:
        checks.append(HoldingCheck(
            "Visszaeses", "warn",
            f"{drop_atr:.1f} ATR-rel ({(close / highest - 1) * 100:.1f}%) a belepes ota mert csucs "
            f"({highest:.2f}) alatt - egy {cfg.trail_atr:.0f} ATR-es kovetett stop mar kiutotte volna"))

    # 2. Hosszu tavu trend
    sma200 = df["close"].rolling(200, min_periods=200).mean().iloc[-1]
    ema50, ema200 = last.get("ema50"), last.get("ema200")
    if pd.isna(sma200) or pd.isna(ema50) or pd.isna(ema200):
        checks.append(HoldingCheck("Hosszu trend", "info", "tul rovid historia a 200 napos atlaghoz"))
    elif close < sma200:
        checks.append(HoldingCheck(
            "Hosszu trend", "bad", f"a zaro az SMA200 ({sma200:.2f}) alatt - az emelkedo trend megtort"))
    elif ema50 < ema200:
        checks.append(HoldingCheck(
            "Hosszu trend", "warn", f"zaro > SMA200 ({sma200:.2f}), de EMA50 < EMA200 - gyenge trend"))
    else:
        checks.append(HoldingCheck(
            "Hosszu trend", "ok", f"zaro > SMA200 ({sma200:.2f}), EMA50 > EMA200"))

    # 3. Rovid tavu trend
    ema20 = last.get("ema20")
    if pd.notna(ema20) and pd.notna(ema50):
        if close < ema50:
            checks.append(HoldingCheck(
                "Rovid trend", "warn", f"a zaro az EMA50 ({ema50:.2f}) alatt - lendulet-vesztes"))
        elif close < ema20:
            checks.append(HoldingCheck(
                "Rovid trend", "info", f"EMA20 ({ema20:.2f}) alatt, de EMA50 ({ema50:.2f}) felett"))
        else:
            checks.append(HoldingCheck("Rovid trend", "ok", f"EMA20 ({ema20:.2f}) felett"))

    # 4. Momentum
    rsi = last.get("rsi")
    if pd.notna(rsi):
        if rsi >= 75:
            checks.append(HoldingCheck(
                "RSI", "warn", f"{rsi:.0f} - tulvett, visszaeses johet; erdemes a stopot huzni"))
        elif rsi <= 35:
            checks.append(HoldingCheck("RSI", "warn", f"{rsi:.0f} - gyenge momentum"))
        else:
            checks.append(HoldingCheck("RSI", "ok", f"{rsi:.0f}"))

    # 5. Medves formaciok
    bearish = [m for m in (forecast.matches if forecast else []) if m.direction == "bearish"]
    triggered = [m for m in bearish if m.status == "triggered"]
    near = [m for m in bearish if m.status == "forming" and 0 <= (close - m.trigger_level) / atr <= 1.0]
    if triggered:
        m = max(triggered, key=lambda x: x.quality)
        checks.append(HoldingCheck(
            "Medves minta", "bad",
            f"{m.name} kitort lefele (trigger {m.trigger_level:.2f}, cel {m.target:.2f}, "
            f"minoseg {m.quality:.0%})"))
    elif near:
        m = max(near, key=lambda x: x.quality)
        checks.append(HoldingCheck(
            "Medves minta", "warn",
            f"{m.name} alakul, az ar 1 ATR-en belul a {m.trigger_level:.2f} triggertol"))
    else:
        checks.append(HoldingCheck("Medves minta", "ok", "nincs aktiv medves formacio"))

    # 6. Rezisztencia es cel
    target, target_source = _target(holding, forecast, close)
    target_hit = holding.target is not None and float(last["high"]) >= holding.target
    resistance = forecast.resistance if forecast else None
    if target_hit:
        checks.append(HoldingCheck("Cel", "info", f"az ar elerte a celt ({holding.target:.2f} $)"))
    if resistance is not None and (resistance.price - close) / atr <= 1.0:
        checks.append(HoldingCheck(
            "Rezisztencia", "warn",
            f"{resistance.price:.2f} $ kozel ({(resistance.price - close) / atr:.1f} ATR) - itt megakadhat"))
    elif resistance is not None:
        checks.append(HoldingCheck(
            "Rezisztencia", "ok", f"legkozelebbi: {resistance.price:.2f} $ ({resistance.distance_pct:+.1f}%)"))

    # 7. Gyorsjelentes - egy gap atugorhatja a stopot
    days = market.days_until(earnings_date, as_of.date())
    if days is None:
        checks.append(HoldingCheck("Earnings", "info", "a kovetkezo jelentes datuma nem ismert"))
    elif days <= cfg.earnings_blackout_days:
        checks.append(HoldingCheck(
            "Earnings", "warn",
            f"jelentes {days} nap mulva ({earnings_date:%Y-%m-%d}) - a gap atugorhatja a stopot"))
    else:
        checks.append(HoldingCheck("Earnings", "ok", f"jelentes {days} nap mulva"))

    # 8. Piaci rezsim
    if benchmark is not None and not benchmark.empty:
        regime = market.market_regime(benchmark, as_of)
        if regime.ok is False:
            checks.append(HoldingCheck(f"Piac ({cfg.benchmark})", "warn", regime.text))
        elif regime.ok:
            checks.append(HoldingCheck(f"Piac ({cfg.benchmark})", "ok", regime.text))

    # 9. Kompozit iranyjelzes
    label = ""
    if forecast is not None:
        label = forecast.label
        if forecast.score < -0.15:
            checks.append(HoldingCheck(
                "Iranyjelzes", "warn", f"{forecast.label} ({forecast.score:+.2f}) - medves osszkep"))
        else:
            checks.append(HoldingCheck("Iranyjelzes", "ok", f"{forecast.label} ({forecast.score:+.2f})"))

    return HoldingEvaluation(
        holding=holding,
        as_of=as_of,
        price=close,
        atr=atr,
        highest=highest,
        suggested_stop=float(suggested),
        suggested_stop_text=suggested_text,
        target=target,
        target_source=target_source,
        checks=checks,
        earnings_date=earnings_date,
        stop_hit=bool(stop_hit),
        target_hit=bool(target_hit),
        label=label,
    )


def evaluate_open(
    settings: Settings,
    own: "HoldingStore",
    forecasts: dict[str, Forecast] | None = None,
    symbol: str | None = None,
    allow_network: bool = False,
) -> list[HoldingEvaluation | Holding]:
    """Minden nyitott sajat pozicio kiertekelese a cache-elt adatokon.

    Ahol nincs arfolyam-adat, ott maga a `Holding` jon vissza (kiertekeles nelkul).
    A `forecasts` mar kiszamolt elemzeseket ad at (a webes felulet igy nem szamol duplan).
    """
    from . import indicators, store
    from .forecast import Calibration, analyse

    forecasts = dict(forecasts or {})
    bench = market.load_benchmark(settings, refresh=allow_network)
    calendar = market.EarningsCalendar(settings, allow_network=allow_network)
    calibration = None
    frames: dict[str, pd.DataFrame] = {}
    out: list[HoldingEvaluation | Holding] = []

    for h in own.open(symbol):
        if h.symbol not in frames:
            df = store.load(h.symbol, settings)
            frames[h.symbol] = indicators.enrich(df, settings.structure.atr_period) if not df.empty else df
        df = frames[h.symbol]
        if df.empty:
            out.append(h)
            continue
        if h.symbol not in forecasts:
            calibration = calibration or Calibration.load(settings.calibration_path)
            forecasts[h.symbol] = analyse(h.symbol, df, settings, calibration)
        out.append(evaluate(h, df, forecasts[h.symbol], settings, bench, calendar.next_date(h.symbol)))
    return out


def _target(holding: Holding, forecast: Forecast | None, close: float) -> tuple[float | None, str]:
    if holding.target is not None:
        return holding.target, "sajat"
    if forecast is not None:
        bullish = [m for m in forecast.matches if m.direction == "bullish" and m.target > close]
        if bullish:
            m = max(bullish, key=lambda x: x.quality)
            return float(m.target), f"{m.name} merete"
        if forecast.resistance is not None:
            return float(forecast.resistance.price), "legkozelebbi rezisztencia"
    return None, ""


# ---------------------------------------------------------------------------
# Tarolas (data/holdings.json - legfeljebb MAX_OPEN + MAX_CLOSED bejegyzes)
# ---------------------------------------------------------------------------
class HoldingStore:
    def __init__(self, settings: Settings, read_only: bool = False) -> None:
        self.path = settings.holdings_path
        self.read_only = read_only
        self.items: dict[str, Holding] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in raw.get("holdings", {}).items():
                    self.items[key] = Holding(**value)
            except (json.JSONDecodeError, TypeError):
                log.warning("Serult holdings.json - uresen indulunk.")

    def open(self, symbol: str | None = None) -> list[Holding]:
        return [
            h for h in self.items.values()
            if h.is_open and (symbol is None or h.symbol == symbol.upper())
        ]

    def symbols(self) -> list[str]:
        return sorted({h.symbol for h in self.open()})

    def get(self, holding_id: str) -> Holding:
        if holding_id not in self.items:
            raise ValueError(f"Nincs ilyen pozicio: {holding_id}")
        return self.items[holding_id]

    def add(
        self,
        symbol: str,
        entry_price: float,
        shares: float,
        entry_date: str | None = None,
        stop: float | None = None,
        target: float | None = None,
        note: str = "",
    ) -> Holding:
        symbol = symbol.strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ValueError(f"Ervenytelen ticker: {symbol}")
        if len(self.open()) >= MAX_OPEN:
            raise ValueError(f"Legfeljebb {MAX_OPEN} nyitott sajat pozicio tarolhato.")
        entry_date = entry_date or date.today().isoformat()
        values = _validate(entry_price, shares, entry_date, stop, target)
        now = datetime.now(timezone.utc)
        holding_id = f"{symbol}-{now:%Y%m%d}-{uuid.uuid4().hex[:6]}"
        while holding_id in self.items:
            holding_id = f"{symbol}-{now:%Y%m%d}-{uuid.uuid4().hex[:6]}"
        holding = Holding(
            id=holding_id,
            symbol=symbol,
            note=str(note or "")[:200],
            created_at=now.isoformat(timespec="seconds"),
            **values,
        )
        self.items[holding.id] = holding
        return holding

    def update(self, holding_id: str, **changes) -> Holding:
        h = self.get(holding_id)
        merged = {
            "entry_price": changes.get("entry_price", h.entry_price),
            "shares": changes.get("shares", h.shares),
            "entry_date": changes.get("entry_date", h.entry_date),
            "stop": changes["stop"] if "stop" in changes else h.stop,
            "target": changes["target"] if "target" in changes else h.target,
        }
        values = _validate(**merged)
        # Uj stop / cel: az arra vonatkozo ertesites ujra elmehet
        if values["stop"] != h.stop:
            h.alerted.pop("stop", None)
        if values["target"] != h.target:
            h.alerted.pop("target", None)
        for key, value in values.items():
            setattr(h, key, value)
        if "note" in changes:
            h.note = str(changes["note"] or "")[:200]
        return h

    def close(self, holding_id: str, price: float, on: str | None = None) -> Holding:
        h = self.get(holding_id)
        if not h.is_open:
            raise ValueError("Ez a pozicio mar le van zarva.")
        if not price or price <= 0:
            raise ValueError("A zaro ar legyen pozitiv.")
        h.status = "closed"
        h.close_price = float(price)
        h.closed_at = on or date.today().isoformat()
        return h

    def remove(self, holding_id: str) -> None:
        self.get(holding_id)
        del self.items[holding_id]

    def save(self) -> None:
        if self.read_only:
            return
        closed = sorted((h for h in self.items.values() if not h.is_open),
                        key=lambda h: h.closed_at or "", reverse=True)
        drop = {h.id for h in closed[MAX_CLOSED:]}
        self.items = {k: v for k, v in self.items.items() if k not in drop}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"holdings": {k: asdict(v) for k, v in self.items.items()}}
        self.path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def _optional_price(value) -> float | None:
    if value is None or value == "":
        return None
    v = float(value)
    if not np.isfinite(v) or v <= 0:
        raise ValueError("Az arak legyenek pozitivak.")
    return v


def _validate(entry_price, shares, entry_date, stop=None, target=None) -> dict:
    entry_price = float(entry_price)
    shares = float(shares)
    if not np.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("A belepo ar legyen pozitiv.")
    if not np.isfinite(shares) or shares <= 0:
        raise ValueError("A darabszam legyen pozitiv.")
    try:
        day = date.fromisoformat(str(entry_date))
    except ValueError:
        raise ValueError("A belepes datuma EEEE-HH-NN formatumu legyen.") from None
    if day > date.today():
        raise ValueError("A belepes datuma nem lehet a jovoben.")
    stop, target = _optional_price(stop), _optional_price(target)
    if stop is not None and target is not None and target <= stop:
        raise ValueError("A cel legyen a stop felett.")
    return {"entry_price": entry_price, "shares": shares, "entry_date": day.isoformat(),
            "stop": stop, "target": target}


# ---------------------------------------------------------------------------
# Ertesitesek (a monitor hivja, lezart napi gyertyan)
# ---------------------------------------------------------------------------
EVENT_LABELS = {
    "hold_stop": "STOP elerve",
    "hold_target": "CEL elerve",
    "hold_risk": "romlott a kep",
    "hold_earnings": "kozeleg a gyorsjelentes",
}


def alert_events(ev: HoldingEvaluation) -> list[str]:
    """Az ertesitendo esemenyek - mindegyik egyszer (a `holding.alerted` tarolja).

    A minosites romlasa csak az elozo ertesiteshez kepest szamit; az elso
    kiertekeles nem riaszt (azt a felhasznalo a feluleten mar latta).
    """
    h = ev.holding
    day = ev.as_of.strftime("%Y-%m-%d")
    events: list[str] = []

    if ev.stop_hit and "stop" not in h.alerted:
        h.alerted["stop"] = day
        events.append("hold_stop")
    if ev.target_hit and "target" not in h.alerted:
        h.alerted["target"] = day
        events.append("hold_target")

    verdict = ev.verdict
    if h.last_verdict is not None and _RANK[verdict] > _RANK[h.last_verdict] and not ev.stop_hit:
        events.append("hold_risk")
    h.last_verdict = verdict

    # ugyanaz a kuszob, mint a kiertekelesben (entry.earnings_blackout_days)
    earnings_soon = any(c.name == "Earnings" and c.level == "warn" for c in ev.checks)
    if ev.earnings_date is not None and earnings_soon:
        key = f"earnings|{ev.earnings_date.isoformat()}"
        if key not in h.alerted:
            h.alerted = {k: v for k, v in h.alerted.items() if not k.startswith("earnings|")}
            h.alerted[key] = day
            events.append("hold_earnings")
    return events


def format_event(ev: HoldingEvaluation, event: str) -> tuple[str, str]:
    h = ev.holding
    title = f"{h.symbol}: sajat pozicio - {EVENT_LABELS.get(event, event)}"
    lines = [
        f"Zaro ({ev.as_of:%Y-%m-%d}): {ev.price:.2f} $",
        f"Belepo: {h.entry_price:.2f} $ ({h.entry_date}), {h.shares:g} db",
        f"Eredmeny: {ev.pnl:+,.0f} $ ({ev.pnl_pct:+.1f}%)"
        + (f", {ev.r_multiple:+.2f}R" if ev.r_multiple is not None else ""),
    ]
    if event == "hold_stop":
        lines.append(f"A napi alj elerte a stopot ({h.stop:.2f} $).")
        lines.append("Ha a brokernel kiutott, zard le itt is (Poziciok oldal).")
    elif event == "hold_target":
        lines.append(f"A napi csucs elerte a celt ({h.target:.2f} $).")
        lines.append(f"Ha tartod tovabb: kovetett stop javaslat {ev.suggested_stop:.2f} $.")
    elif event == "hold_earnings":
        lines.append(f"Jelentes: {ev.earnings_date:%Y-%m-%d}. Egy gap atugorhatja a stopot.")
    lines.append("")
    lines.append(f"Minosites: {ev.verdict_text.upper()}")
    for reason in ev.reasons:
        lines.append(f"  - {reason}")
    if h.stop is None or ev.suggested_stop > (h.stop or 0):
        lines.append(f"Stop javaslat: {ev.suggested_stop:.2f} $ ({ev.suggested_stop_text})")
    lines.append("")
    lines.append("Nem befektetesi tanacs. A rendszer csak a megadott adatokat latja.")
    return title, "\n".join(lines)
