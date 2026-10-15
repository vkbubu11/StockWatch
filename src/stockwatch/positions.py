"""A jelzett trade-ek kovetese - hogy a kilepesi terv tenyleg vegre is hajtodjon.

A reszleges kilepes (fele +1R-nel, a stop a belepore) adja a magasabb nyeresi
aranyt - de csak akkor, ha idoben lepsz. Ezert minden belepesi jelzes utan a
rendszer VIRTUALISAN koveti a trade-et a napi zaro gyertyakon, es ertesit:

    pos_entered    a jelzes utani nyitoaron belepes tortent (ha a limit alatt volt)
    pos_skipped    a nyito a limit felett / a stop alatt volt - a jelzes ervenytelen
    pos_partial    +1R elerve: add el a felet, huzd a stopot a belepore
    pos_breakeven  a maradek nullszaldon zart
    pos_stop       stop kiutve
    pos_trail      kovetett stop kiutve
    pos_target     cel elerve
    pos_timeout    idokorlat - zard a poziciot

Ha a jelzesbol Alpaca paper trade lett (broker.py, azonos azonosito), a kovetes
NEM virtualis: az allapot (bekuldve / teljesult / visszavonva / nem teljesult /
lezarva), a belepo, a stop es az eredmeny a paper trade-bol jon (reconcile),
igy a "Jelzett trade-ek" oldal mindig egyezik az Alpacaval. Ilyenkor a virtualis
gyertyas leptetes es a pos_* ertesitesek kimaradnak (a broker_* ertesitesek mennek).
A kezzel bekuldott paper trade (Paper trading oldal) is ide kerul, kovetett jelzes nelkul is.

Paper trade nelkul a kovetes azt feltetelezi, hogy a jelzes szerint leptel be. A
lezart trade-ek eredmenye (R-ben) igy egy elo, utolag nem szepitheto
nyilvantartast ad - lasd: `python cli.py positions`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .config import Settings
from .trade_mgmt import PositionState, max_hold

log = logging.getLogger(__name__)

EVENT_LABELS = {
    "pos_entered": "BELEPES megtortent",
    "pos_skipped": "nem volt belepes",
    "pos_partial": "+1R ELERVE - add el a felet, stop a belepore",
    "pos_breakeven": "maradek nullszaldon zarva",
    "pos_stop": "STOP kiutve",
    "pos_trail": "kovetett stop kiutve",
    "pos_target": "CEL elerve",
    "pos_timeout": "IDOKORLAT - zard a poziciot",
    "pos_exit_signal": "KILEPESI JEL - holnap nyitaskor add el",
    "pos_exit": "KILEPES nyitaskor",
}
PULLBACK_EXIT_EVENTS = {"signal": "pos_exit", "time": "pos_timeout", "stop": "pos_stop", "gap_stop": "pos_stop"}
PULLBACK_REASONS = {"signal": "signal", "time": "timeout", "stop": "stop", "gap_stop": "stop"}


@dataclass
class TrackedPosition:
    id: str
    symbol: str
    pattern: str
    signal_date: str               # a jelzes gyertyaja (YYYY-MM-DD)
    entry_limit: float
    planned_entry: float
    stop: float
    target: float
    atr: float
    shares: int
    horizon: int
    exit_mode: str
    partial_r: float
    trail_atr: float
    status: str = "pending"        # pending | open | closed | skipped
    entry_date: str | None = None
    entry_price: float | None = None
    last_date: str | None = None   # az utoljara feldolgozott gyertya
    state: dict | None = None      # PositionState
    result_r: float | None = None
    reason: str = ""
    closed_at: str | None = None
    notes: list[str] = field(default_factory=list)
    signal_id: str = ""            # kozos jelazonosito (ledger.py)
    source: str = "virtual"        # virtual | alpaca (a paper trade allapota az iranyado)
    broker_status: str = ""        # a paper trade allapota (broker.BrokerTrade.status)
    broker_partial: float | None = None   # a paper trade +1R-es cel-limitje
    strategy: str = "breakout"     # melyik strategia kuldte (config.STRATEGY_IDS)
    params: dict | None = None     # a Visszahuzas szabalyai a jelzeskor (config.PullbackParams)
    held: int = 0                  # tartott kereskedesi napok (indikatoros kilepesnel)
    exit_pending: str = ""         # a zaron szuletett kilepesi jel (signal | time) - holnap nyitaskor zar
    result_pct: float | None = None   # netto eredmeny %-ban (koltseg utan) - a nyerest ez donti el
    signal_at: str = ""            # mikor kuldte ki a rendszer a jelzest (ISO UTC)
    submitted_at: str = ""         # a paper megbizas bekuldese (ISO UTC)
    entry_at: str = ""             # a paper belepes teljesulese (ISO UTC, az Alpaca szerint)

    @property
    def is_active(self) -> bool:
        return self.status in ("pending", "open")

    def position_state(self) -> PositionState | None:
        return PositionState(**self.state) if self.state else None


class PositionTracker:
    MAX_CLOSED = 2000        # ~egy ev alatt nehany tucat trade keletkezik

    def __init__(self, settings: Settings, read_only: bool = False, reconcile: bool = True) -> None:
        self.settings = settings
        self.path = settings.positions_path
        self.read_only = read_only
        self.positions: dict[str, TrackedPosition] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in raw.get("positions", {}).items():
                    self.positions[key] = TrackedPosition(**value)
            except (json.JSONDecodeError, TypeError):
                log.warning("Serult positions.json - uresen indulunk.")
        if reconcile:
            self.sync_from_broker()      # minden olvaso (felulet, eletjel, CLI) az Alpaca allapotat latja

    def sync_from_broker(self) -> list[TrackedPosition]:
        """reconcile() a mentett paper nyilvantartasbol (data/broker.json), ha van."""
        if not self.settings.broker_path.exists():
            return []
        from .broker import BrokerBook       # broker -> entry -> ... nem importalja ezt a modult

        return self.reconcile(BrokerBook(self.settings, read_only=True).trades)

    # ------------------------------------------------------------------
    def register(self, signal, params=None) -> TrackedPosition | None:
        """Egy kikuldott belepesi jelzes felvetele kovetesre. `params`: a Visszahuzas szabalyai."""
        if signal.kind == "setup":
            return None                      # az elokeszulet meg nem vetel
        plan = signal.plan
        pid = signal.trade_key
        if pid in self.positions:
            return self.positions[pid]
        position = TrackedPosition(
            id=pid,
            symbol=signal.symbol,
            pattern=signal.pattern.name,
            signal_date=f"{signal.as_of:%Y-%m-%d}",
            entry_limit=plan.entry_limit,
            planned_entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            atr=plan.atr,
            shares=plan.shares,
            horizon=plan.horizon or 40,
            exit_mode=plan.exit_mode,
            partial_r=plan.partial_r,
            trail_atr=plan.trail_atr,
            last_date=f"{signal.as_of:%Y-%m-%d}",
            signal_id=getattr(signal, "signal_id", ""),
            strategy=getattr(signal, "strategy", "breakout"),
            params=params.model_dump() if params is not None else None,
            signal_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.positions[pid] = position
        return position

    def active(self, symbol: str | None = None) -> list[TrackedPosition]:
        return [
            p for p in self.positions.values()
            if p.is_active and (symbol is None or p.symbol == symbol.upper())
        ]

    # ------------------------------------------------------------------
    def update(self, symbol: str, df: pd.DataFrame, strategy: str | None = None) -> list[tuple[TrackedPosition, str]]:
        """A LEZART napi gyertyak feldolgozasa. Csak zaras utan hivd! `strategy`: csak ennek a kotesei."""
        events: list[tuple[TrackedPosition, str]] = []
        for position in self.active(symbol):
            if position.source == "alpaca" or strategy not in (None, position.strategy):
                continue                     # a paper trade allapota az iranyado (reconcile)
            if position.exit_mode == "indicator":
                events += self._advance_pullback(position, df)
            else:
                events += self._advance(position, df)
        return events

    def _advance_pullback(self, position: TrackedPosition, df: pd.DataFrame) -> list[tuple[TrackedPosition, str]]:
        """Visszahuzas: a kotes ujrajatszasa a jelzes napjatol UGYANAZZAL a fuggvennyel, mint a kutatasban
        (meanrev.simulate_trade), a kikuldott limit- es stop-szintekkel. Az esemenyek az allapotvaltasok."""
        from . import meanrev
        from .config import PullbackParams

        day = pd.Timestamp(position.signal_date)
        if day not in df.index:
            return []
        params = PullbackParams(**position.params) if position.params else PullbackParams()
        feat = meanrev.features(df, params)
        exits = meanrev.exit_flags(feat, params).to_numpy()
        i = int(df.index.get_loc(day))
        t = meanrev.simulate_trade(position.symbol, feat, exits, i, params,
                                   levels=(position.entry_limit, position.stop))
        out: list[tuple[TrackedPosition, str]] = []
        position.last_date = f"{df.index[-1]:%Y-%m-%d}"
        if t.status == "pending":
            return out
        if t.status == "unfilled":
            low = float(df["low"].iloc[i + 1])
            self._close_skipped(position, df.index[i + 1],
                                f"a veteli limit ({position.entry_limit:.2f}) nem teljesult (low {low:.2f})")
            return [(position, "pos_skipped")]
        if position.status == "pending":
            position.status, position.entry_date, position.entry_price = "open", f"{t.entry_date:%Y-%m-%d}", t.entry_price
            out.append((position, "pos_entered"))
        position.held = t.held
        if t.status == "open":
            if t.reason and position.exit_pending != t.reason:
                position.exit_pending = t.reason
                out.append((position, "pos_exit_signal"))
            return out
        risk = t.entry_price - position.stop
        position.status, position.closed_at = "closed", f"{t.exit_date:%Y-%m-%d}"
        position.reason = PULLBACK_REASONS.get(t.reason, t.reason)
        position.result_pct = round(t.net_pct, 3)
        position.result_r = round((t.exit_price - t.entry_price) / risk, 3) if risk > 0 else None
        position.notes = (position.notes + [f"kilepes {t.exit_date:%Y-%m-%d} @ {t.exit_price:.2f} ({t.reason})"])[-20:]
        out.append((position, PULLBACK_EXIT_EVENTS.get(t.reason, "pos_exit")))
        return out

    def reconcile(self, trades: dict) -> list[TrackedPosition]:
        """A paper trade-del rendelkezo jelzesek allapotanak atvetele (broker.json -> itt).

        Az a paper trade is felkerul, amelyhez nincs kovetett jelzes (pl. a Paper trading
        oldalrol kezzel bekuldott): a Jelzett trade-ek oldalon MINDEN paper trade latszik.

        `trades`: broker.BrokerBook.trades. Visszaadja a megvaltozott / uj poziciokat.
        """
        by_signal = {t.signal_id: t for t in trades.values() if getattr(t, "signal_id", "")}
        changed, mirrored = [], set()
        for position in self.positions.values():
            trade = trades.get(position.id) or (by_signal.get(position.signal_id) if position.signal_id else None)
            if trade is None:
                continue
            mirrored.add(trade.id)
            before = asdict(position)
            mirror_trade(position, trade)
            if asdict(position) != before:
                changed.append(position)
        for trade in trades.values():
            if trade.id in mirrored or trade.id in self.positions:
                continue
            position = position_from_trade(trade, self.settings)
            mirror_trade(position, trade)
            self.positions[position.id] = position
            changed.append(position)
        return changed

    def _advance(self, position: TrackedPosition, df: pd.DataFrame) -> list[tuple[TrackedPosition, str]]:
        out: list[tuple[TrackedPosition, str]] = []
        since = pd.Timestamp(position.last_date)
        new_bars = df[df.index > since]
        if new_bars.empty:
            return out

        state = position.position_state()
        for date, bar in new_bars.iterrows():
            o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
            first = False

            if position.status == "pending":
                if o > position.entry_limit:
                    self._close_skipped(position, date, f"nyito {o:.2f} a limit ({position.entry_limit:.2f}) felett")
                    out.append((position, "pos_skipped"))
                    return out
                if o <= position.stop:
                    self._close_skipped(position, date, f"nyito {o:.2f} a stop ({position.stop:.2f}) alatt")
                    out.append((position, "pos_skipped"))
                    return out
                state = PositionState(
                    entry=o, stop=position.stop, initial_stop=position.stop, target=position.target,
                    atr=position.atr, mode=position.exit_mode, partial_r=position.partial_r,
                    trail_atr=position.trail_atr,
                )
                position.status = "open"
                position.entry_date = f"{date:%Y-%m-%d}"
                position.entry_price = o
                out.append((position, "pos_entered"))
                first = True

            for event in state.step(o, h, l, first=first):
                out.append((position, f"pos_{event}"))
            if not state.closed and state.bars_held >= max_hold(position.horizon, position.exit_mode):
                state.timeout(c)
                out.append((position, "pos_timeout"))

            position.last_date = f"{date:%Y-%m-%d}"
            position.state = state.to_dict()
            if state.closed:
                position.status = "closed"
                position.result_r = round(state.realised_r, 3)
                position.reason = state.reason
                position.closed_at = f"{date:%Y-%m-%d}"
                break

        position.state = state.to_dict() if state else None
        return out

    def _close_skipped(self, position: TrackedPosition, date, note: str) -> None:
        position.status = "skipped"
        position.reason = note
        position.closed_at = f"{date:%Y-%m-%d}"
        position.last_date = f"{date:%Y-%m-%d}"

    # ------------------------------------------------------------------
    def save(self) -> None:
        if self.read_only:
            return
        # A lezart trade-ek az elo eredmenynaplo - megtartjuk oket, csak a
        # legregebbieket dobjuk el egy (bovon eleg) darabszam felett.
        closed = sorted(
            (p for p in self.positions.values() if not p.is_active),
            key=lambda p: p.closed_at or "", reverse=True,
        )
        drop = {p.id for p in closed[self.MAX_CLOSED:]}
        self.positions = {k: v for k, v in self.positions.items() if k not in drop}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"positions": {k: asdict(v) for k, v in self.positions.items()}}
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    def closed_stats(self, strategy: str | None = None) -> dict:
        closed = [p for p in self.positions.values()
                  if p.status == "closed" and p.result_r is not None and strategy in (None, p.strategy)]
        results = [p.result_r for p in closed]
        if not results:
            return {"n": 0}
        # nyero: a netto (koltseg utani) eredmeny pozitiv, ha ismert; kulonben az R
        wins = sum((p.result_pct > 0) if p.result_pct is not None else (p.result_r > 0) for p in closed)
        return {"n": len(results), "win": wins / len(results), "ev": sum(results) / len(results),
                "total": sum(results)}


# ---------------------------------------------------------------------------
# Paper trade -> kovetett jelzes
# ---------------------------------------------------------------------------
SKIP_REASONS = {
    "expired": "a belepo-limit nem teljesult - visszavonva (Alpaca)",
    "cancelled": "visszavonva (Alpaca)",
    "error": "hiba a paper kereskedesben (Alpaca)",
}


def _exit_reason(trade, legs: list) -> str:
    traded = [leg for leg in legs if leg.filled_qty > 0]
    reasons = {leg.exit_reason for leg in traded}
    if "signal" in reasons:
        return "signal"
    if "timeout" in reasons or "time" in reasons:
        return "timeout"
    if "manual" in reasons:
        return "manual"
    if reasons == {"take_profit"}:
        return "target"
    partial_hit = any(leg.name == "partial" and leg.exit_reason == "take_profit" for leg in traded)
    if partial_hit and trade.breakeven_done:
        return "breakeven"
    return "stop"


def _held_days(start: str | None, end: str | None) -> int:
    """Kereskedesi napok a belepestol (a belepes napja is szamit) - unnepnap nelkul kozelitve."""
    if not start:
        return 0
    stop = pd.Timestamp(end) if end else pd.Timestamp.now(tz="America/New_York").tz_localize(None)
    return max(int(len(pd.bdate_range(pd.Timestamp(start), stop.normalize()))), 0)


def position_from_trade(trade, settings: Settings) -> TrackedPosition:
    """Kovetett jelzes egy olyan paper trade-hez, amelyet nem a monitor jelzese inditott."""
    return TrackedPosition(
        id=trade.id, symbol=trade.symbol, pattern=trade.pattern, signal_date=trade.signal_date,
        entry_limit=trade.entry_limit, planned_entry=trade.entry_limit, stop=trade.stop, target=trade.target,
        atr=0.0, shares=trade.shares, horizon=trade.horizon, exit_mode=trade.exit_mode,
        partial_r=settings.entry.partial_r, trail_atr=settings.entry.trail_atr,
        last_date=trade.signal_date, signal_id=getattr(trade, "signal_id", ""),
        strategy=getattr(trade, "strategy", "breakout"), signal_at=trade.submitted_at,
    )


def mirror_trade(position: TrackedPosition, trade) -> None:
    """A kovetett jelzes allapota a paper trade (broker.BrokerTrade) szerint."""
    legs = trade.leg_objs()
    position.source = "alpaca"
    position.signal_id = position.signal_id or getattr(trade, "signal_id", "")
    position.exit_pending = getattr(trade, "exit_pending", "")
    position.broker_status = trade.status
    position.stop, position.target = trade.stop, trade.target
    position.broker_partial = trade.partial_price
    position.submitted_at = trade.submitted_at
    position.entry_at = getattr(trade, "filled_ts", None) or ""

    if trade.status == "submitted":
        position.status, position.reason, position.closed_at = "pending", "", None
        position.shares = trade.shares
        position.state = None
        return
    if trade.status in SKIP_REASONS or not trade.entry_price:
        position.status = "skipped"
        position.reason = SKIP_REASONS.get(trade.status, f"paper trade: {trade.status}")
        position.closed_at = trade.closed_at
        position.entry_price = position.entry_date = position.state = position.result_r = None
        return

    filled = sum(leg.filled_qty for leg in legs) or trade.shares
    entry = float(trade.entry_price)
    risk = entry - trade.stop if entry > trade.stop else trade.entry_limit - trade.stop
    partial_done = any(leg.name == "partial" and leg.exit_reason == "take_profit" for leg in legs)
    closed = trade.status == "closed"
    state = PositionState(
        entry=entry, stop=entry if trade.breakeven_done else trade.stop, initial_stop=trade.stop,
        target=trade.target, atr=position.atr, mode=trade.exit_mode if trade.exit_mode in ("partial", "target")
        else "target", partial_r=position.partial_r, trail_atr=position.trail_atr,
        bars_held=_held_days(trade.filled_at, trade.closed_at if closed else None),
        partial_done=partial_done, closed=closed,
    )
    position.entry_price, position.entry_date = entry, trade.filled_at
    position.shares = filled
    if closed:
        position.status = "closed"
        position.reason = _exit_reason(trade, legs)
        position.closed_at = trade.closed_at
        position.result_r = (round(trade.realised_pnl / (risk * filled), 3)
                             if trade.realised_pnl is not None and risk > 0 else None)
        position.result_pct = (round(trade.realised_pnl / (entry * filled) * 100, 3)
                               if trade.realised_pnl is not None and entry > 0 else None)
        state.reason, state.remaining = position.reason, 0.0
        state.realised_r = position.result_r or 0.0
    else:
        position.status, position.reason, position.closed_at, position.result_r = "open", "", None, None
    position.state = state.to_dict()


# ---------------------------------------------------------------------------
def format_event(position: TrackedPosition, event: str) -> tuple[str, str]:
    """(cim, szoveg) egy kovetesi esemenyhez."""
    title = f"{position.symbol}: {position.pattern} - {EVENT_LABELS.get(event, event)}"
    if position.exit_mode == "indicator":
        return title, _format_pullback(position, event)
    state = position.position_state()
    half = position.shares // 2
    lines: list[str] = [f"Jelzes napja: {position.signal_date}"]

    if event == "pos_skipped":
        lines.append(f"A jelzes ervenytelen: {position.reason}.")
        lines.append("Ne vegyel utana - a terv erre az arra mar nem ervenyes.")
    elif state is not None:
        risk = state.entry - state.initial_stop
        lines.append(f"Belepo (masnapi nyito): {state.entry:.2f} $ ({position.entry_date})")
        if event == "pos_entered":
            lines.append(f"Stop: {state.initial_stop:.2f} $ | Cel: {state.target:.2f} $")
            if state.uses_partial:
                lines.append(
                    f"Fele ({half} db) eladas: {state.partial_price:.2f} $ (+{position.partial_r:.1f}R) - "
                    "limit megbizas javasolt"
                )
        elif event == "pos_partial":
            lines.append(f"+{position.partial_r:.1f}R elerve ({state.partial_price:.2f} $).")
            lines.append(f"TEENDO: add el a felet ({half} db), a stopot huzd {state.entry:.2f} $-ra (belepo).")
            lines.append("Innentol a trade veszteseg nelkul zarul, ha visszafordul.")
        elif event in ("pos_stop", "pos_breakeven", "pos_trail", "pos_target", "pos_timeout"):
            if event == "pos_timeout":
                lines.append("TEENDO: zard a maradek poziciot (a trade kifutott az idokorlatbol).")
            lines.append(f"Eredmeny: {state.realised_r:+.2f}R  (1R = {risk:.2f} $ reszvenyenkent)")
            lines.append(f"Tartas: {state.bars_held} kereskedesi nap")
        if state.trailing and not state.closed and event != "pos_entered":
            lines.append(f"Aktualis kovetett stop: {state.stop:.2f} $")

    lines.append("")
    lines.append("A kovetes a jelzes szerinti belepest feltetelezi. Nem befektetesi tanacs.")
    return title, "\n".join(lines)


def _format_pullback(position: TrackedPosition, event: str) -> str:
    params = position.params or {}
    lines = [f"Jelzes napja: {position.signal_date}  |  {position.shares} db"]
    if event == "pos_skipped":
        lines += [f"A jelzes ervenytelen: {position.reason}.", "Ne vegyel utana - a limit csak egy napra szolt."]
    elif event == "pos_entered":
        lines += [f"Belepo (limit teljesult): {position.entry_price:.2f} $ ({position.entry_date})",
                  f"Stop: {position.stop:.2f} $",
                  f"Kilepes: ha a zaro ConnorsRSI {params.get('exit_crsi', 50):g} fole megy -> masnap nyitaskor; "
                  f"legkesobb {params.get('time_stop', 10)} nap utan."]
    elif event == "pos_exit_signal":
        why = "a ConnorsRSI a kuszob fole ment" if position.exit_pending == "signal" else "lejart az idokorlat"
        lines += [f"Belepo: {position.entry_price:.2f} $ ({position.entry_date}), tartas: {position.held} nap",
                  f"KILEPESI JEL a mai zaron ({why}).",
                  "TEENDO: holnap NYITASKOR add el (piaci / nyitoaras megbizassal)."]
    else:
        lines += [f"Belepo: {position.entry_price:.2f} $ ({position.entry_date})  |  zarva: {position.closed_at}",
                  f"Eredmeny: {position.result_pct:+.2f}% netto  ({position.result_r:+.2f}R)"
                  if position.result_pct is not None and position.result_r is not None else "Eredmeny: -",
                  f"Tartas: {position.held} kereskedesi nap"]
    lines += ["", "A kovetes a jelzes szerinti belepest feltetelezi. Nem befektetesi tanacs."]
    return "\n".join(lines)
