"""Alpaca PAPER kereskedes - a swing long jelzesek eles (de nem valodi penzes) tesztje.

A belepesi jelzes kereskedesi tervet a rendszer bracket megbizasokkent kuldi be:

    partial mod (alap), >= 2 darab:
        A bracket: a pozicio fele   - limit belepo, cel = +1R,        stop = a terv stopja
        B bracket: a masik fele     - limit belepo, cel = a minta celja, stop = a terv stopja
    target mod (vagy 1 darab):
        egyetlen bracket a teljes poziciora a celig

A cel es a stop az Alpacan elo megbizas: napkozben, az ar elerese utan azonnal
teljesul, nem a nap vegen. A szinkronizalas (a monitor nyitvatartas alatt percenkent,
zarva 15 percenkent es zaras utan; a feluleten gombbal is) ugyanazt hajtja vegre, amit a
backtest es a virtualis kovetes feltetelez:

    * ha az A bracket celja teljesult, a B bracket stopjat a belepore huzza (nullszaldo)
    * ha a belepo-limit a jelzes utani kereskedesi napon nem teljesult, visszavonja
      (a jelzes a masnapi nyitasra szolt, kesobb mar nem ervenyes). A kitores belepoje a
      backtestben CSAK a nyitoaron teljesulhet (ha a nyito a limit folott van, nincs belepes):
      ezert ha a nyitas utan `broker.entry_window_min` perccel sem teljesult, visszavonja -
      kulonben a nyitas utan visszaeso (gap-fade) papirok teljesulnenek, amit a backtest nem merte
    * az idokorlat (a minta hosszabol szamolt horizont) utolso napjan meg a zaras elotti
      percekben piaci aron zarja a maradekot (a backtest is a napi zaron lep ki)

BIZTONSAG
---------
  * KIZAROLAG a paper endpoint engedelyezett (paper-api.alpaca.markets) - valodi
    penzes szamlara a kod nem tud megbizast kuldeni
  * a kulcsok a .env-ben vannak; a naploba es a feluletre soha nem kerulnek
  * duplikatum ellen: determinisztikus client_order_id, papironkent egy trade,
    `broker.max_open_trades` felso korlat
  * MARGIN NELKUL: csak a szabad keszpenzbol vesz (keszpenz - fuggo vetelek). Az Alpaca paper
    szamla 4x-es margint ad (vasarloero = 4 x equity) - azt a rendszer nem hasznalja
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import requests

from . import entry as entry_mod
from . import sessions, store
from .config import Settings, alpaca_credentials
from .locks import LockTimeout, file_lock, write_atomic
from .trade_mgmt import max_hold

log = logging.getLogger(__name__)

PAPER_HOST = "paper-api.alpaca.markets"
PAPER_ENDPOINT = f"https://{PAPER_HOST}"
MARKET_TZ = "America/New_York"
TIMEOUT = 15
CLOSE_WINDOW_MIN = 10           # az idokorlatos zaras ennyi perccel a piaczaras elott indul
MAX_CLOSED = 500
MAX_NOTES = 20

ACTIVE = ("submitted", "open")
OPEN_ORDER = ("new", "accepted", "pending_new", "accepted_for_bidding", "held", "partially_filled",
              "pending_replace", "pending_cancel", "calculated", "stopped", "suspended")

EVENT_LABELS = {
    "broker_submitted": "megbizas beküldve (paper)",
    "broker_filled": "BELEPES teljesult (paper)",
    "broker_partial": "+1R: a fele eladva (paper)",
    "broker_breakeven": "stop a belepore huzva (paper)",
    "broker_stop": "STOP teljesult (paper)",
    "broker_target": "CEL teljesult (paper)",
    "broker_timeout": "idokorlat - zaras (paper)",
    "broker_expired": "a belepo nem teljesult - visszavonva (paper)",
    "broker_closed": "trade lezarva (paper)",
    "broker_skipped": "nincs szabad hely - nem vasarolt (paper)",
    "broker_error": "HIBA a paper kereskedesben",
    "broker_exit_signal": "KILEPESI JEL - nyitaskor piaci aron zar (paper)",
    "broker_exit": "kilepes elkuldve piaci aron (paper)",
    "broker_exit_retry": "a kilepo megbizas nem teljesult - ujra bekuldve (paper)",
}


class BrokerError(Exception):
    """Az Alpaca elutasitotta a kerest, vagy a sajat vedelmeink allitottak meg."""


class NoRoom(BrokerError):
    """A jelzes ervenyes, de a portfolioban nincs hely neki (max. trade-szam / szabad keret)."""


def pending_buys(open_orders: list[dict]) -> float:
    """A fuggo (meg nem teljesult) vetelek osszege: limit x hatralevo darab."""
    total = 0.0
    for o in open_orders:
        if o.get("side") != "buy":
            continue
        left = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        total += max(left, 0.0) * float(o.get("limit_price") or 0)
    return total


def free_cash(account: dict, open_orders: list[dict]) -> float:
    """Szabad keret margin NELKUL: keszpenz minusz a fuggo vetelek (legfeljebb a vasarloero)."""
    cash = float(account.get("cash") or 0)
    power = float(account.get("buying_power") or 0)
    return max(min(cash - pending_buys(open_orders), power), 0.0)


def normalise_endpoint(url: str | None) -> str:
    """Csak a paper endpoint fogadhato el - akarhogy is irjak be."""
    raw = (url or PAPER_ENDPOINT).strip().rstrip("/")
    if raw.endswith("/v2"):
        raw = raw[:-3]
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != PAPER_HOST or parsed.path not in ("", "/"):
        raise BrokerError(
            f"Csak az Alpaca PAPER endpoint engedelyezett ({PAPER_ENDPOINT}). "
            "Valodi penzes szamlara a rendszer nem kuld megbizast."
        )
    return PAPER_ENDPOINT


def price(value: float) -> float:
    """Alpaca artick: 1 $ felett 2 tizedes, alatta 4."""
    return round(value, 2 if value >= 1 else 4)


# ---------------------------------------------------------------------------
# REST kliens
# ---------------------------------------------------------------------------
class AlpacaClient:
    def __init__(self, endpoint: str, key: str, secret: str, session: requests.Session | None = None) -> None:
        self.base = normalise_endpoint(endpoint)
        self.session = session or requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    @classmethod
    def from_env(cls) -> "AlpacaClient | None":
        endpoint, key, secret = alpaca_credentials()
        if not key or not secret:
            return None
        return cls(endpoint or PAPER_ENDPOINT, key, secret)

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        try:
            r = self.session.request(method, f"{self.base}/v2{path}", params=params, json=body, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise BrokerError(f"Az Alpaca nem erheto el ({type(exc).__name__})") from None
        if r.status_code in (401, 403):
            raise BrokerError("Az Alpaca elutasitotta a kulcsot (401/403) - ellenorizd a Key / Secret parost.")
        if r.status_code >= 400:
            try:
                message = r.json().get("message", r.text)
            except ValueError:
                message = r.text
            raise BrokerError(f"Alpaca {r.status_code}: {str(message)[:300]}")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    def account(self) -> dict:
        return self._req("GET", "/account")

    def clock(self) -> dict:
        return self._req("GET", "/clock")

    def positions(self) -> list[dict]:
        return self._req("GET", "/positions") or []

    def orders(self, status: str = "open", limit: int = 100) -> list[dict]:
        return self._req("GET", "/orders", params={"status": status, "limit": limit, "nested": "true"}) or []

    def get_order(self, order_id: str) -> dict:
        """Az aktualis megbizas - ha kozben lecsereltek, a csereorderet adja."""
        order = self._req("GET", f"/orders/{order_id}", params={"nested": "true"})
        for _ in range(5):
            if order.get("status") != "replaced" or not order.get("replaced_by"):
                break
            order = self._req("GET", f"/orders/{order['replaced_by']}", params={"nested": "true"})
        return order

    def submit_order(self, body: dict) -> dict:
        return self._req("POST", "/orders", body=body)

    def replace_order(self, order_id: str, **fields) -> dict:
        return self._req("PATCH", f"/orders/{order_id}", body={k: str(v) for k, v in fields.items()})

    def cancel_order(self, order_id: str) -> None:
        self._req("DELETE", f"/orders/{order_id}")

    def portfolio_history(self, period: str = "all", timeframe: str = "1D") -> dict:
        """A szamla equity-tortenete (a nyitas ota: period=all)."""
        return self._req("GET", "/account/portfolio/history",
                         params={"period": period, "timeframe": timeframe}) or {}


# ---------------------------------------------------------------------------
# Nyilvantartas (data/broker.json)
# ---------------------------------------------------------------------------
@dataclass
class Leg:
    name: str                       # "partial" | "target"
    qty: int
    take_profit: float
    order_id: str = ""              # a bracket szulo (belepo) orderje
    tp_id: str = ""
    sl_id: str = ""
    entry_status: str = "new"
    filled_qty: int = 0
    entry_fill: float | None = None
    exit_order_id: str = ""         # idokorlat / kezi zaras market orderje
    exit_intent: str = ""           # miert kuldtuk az exit_order-t (timeout | manual)
    exit_price: float | None = None
    exit_reason: str = ""           # take_profit | stop | timeout | manual | not_filled
    exit_ts: str = ""               # a kilepes teljesulesenek pontos ideje (Alpaca, ISO UTC)

    @property
    def done(self) -> bool:
        return bool(self.exit_reason)


@dataclass
class BrokerTrade:
    id: str
    symbol: str
    pattern: str
    kind: str
    signal_date: str
    entry_limit: float
    stop: float
    target: float
    partial_price: float | None
    shares: int
    horizon: int
    exit_mode: str
    status: str = "submitted"       # submitted | open | closed | expired | cancelled | error
    legs: list[dict] = field(default_factory=list)
    submitted_at: str = ""
    filled_at: str | None = None     # a belepes napja (New York, YYYY-MM-DD)
    filled_ts: str | None = None     # a belepes teljesulesenek pontos ideje (Alpaca, ISO UTC)
    entry_price: float | None = None
    closed_at: str | None = None
    realised_pnl: float | None = None
    breakeven_done: bool = False
    notes: list[str] = field(default_factory=list)
    signal_id: str = ""             # kozos jelazonosito (ledger.py)
    strategy: str = "breakout"      # melyik strategia kuldte - a szinkron e szerint kezeli (valtas utan is)
    params: dict | None = None      # Visszahuzas: a kilepesi szabalyok a bekuldeskor (config.PullbackParams)
    exit_pending: str = ""          # Visszahuzas: a zaron szuletett kilepesi jel (signal | time) - nyitaskor zar
    commission_pct: float = 0.0     # kotesi dij oldalankent a bekuldeskor (broker.commission_pct) - a P/L-bol levonva

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE

    def leg_objs(self) -> list[Leg]:
        return [Leg(**leg) for leg in self.legs]

    def set_legs(self, legs: list[Leg]) -> None:
        self.legs = [asdict(leg) for leg in legs]

    def note(self, text: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.notes = (self.notes + [f"{stamp} {text}"])[-MAX_NOTES:]

    @property
    def risk_amount(self) -> float:
        return (self.entry_limit - self.stop) * self.shares


@contextmanager
def book_lock(settings: Settings, timeout: float = 120.0):
    """Folyamatok kozotti zar a broker.json olvas-modosit-ir ciklusahoz.

    A monitor es a webes felulet kulon folyamat: zar nelkul az egyik mentese
    felulirhatna a masik kozben bekuldott trade-jet - es az Alpacan elo megbizas
    kikerulne a nyilvantartasbol. Hasznalat: `with book_lock(s): book = BrokerBook(s) ... book.save()`.
    """
    try:
        with file_lock(settings.broker_path, timeout):
            yield
    except LockTimeout:
        raise BrokerError("A paper nyilvantartas foglalt (zar) - probald ujra.") from None


class BrokerBook:
    def __init__(self, settings: Settings, read_only: bool = False) -> None:
        self.path = settings.broker_path
        self.read_only = read_only
        self.trades: dict[str, BrokerTrade] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in raw.get("trades", {}).items():
                    self.trades[key] = BrokerTrade(**value)
            except (json.JSONDecodeError, TypeError):
                log.warning("Serult broker.json - uresen indulunk.")

    def active(self, symbol: str | None = None) -> list[BrokerTrade]:
        return [t for t in self.trades.values()
                if t.is_active and (symbol is None or t.symbol == symbol.upper())]

    def get(self, trade_id: str) -> BrokerTrade:
        if trade_id not in self.trades:
            raise BrokerError(f"Nincs ilyen trade: {trade_id}")
        return self.trades[trade_id]

    def save(self) -> None:
        if self.read_only:
            return
        closed = sorted((t for t in self.trades.values() if not t.is_active),
                        key=lambda t: t.closed_at or t.submitted_at, reverse=True)
        drop = {t.id for t in closed[MAX_CLOSED:]}
        self.trades = {k: v for k, v in self.trades.items() if k not in drop}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"trades": {k: asdict(v) for k, v in self.trades.items()}}
        write_atomic(self.path, json.dumps(payload, indent=1, ensure_ascii=False))   # a felulet soha nem lat felig irt fajlt

    def stats(self) -> dict:
        closed = [t for t in self.trades.values() if t.status == "closed" and t.realised_pnl is not None]
        if not closed:
            return {"n": 0}
        wins = sum(t.realised_pnl > 0 for t in closed)
        return {"n": len(closed), "win": wins / len(closed), "pnl": sum(t.realised_pnl for t in closed)}


# ---------------------------------------------------------------------------
# Bekuldes
# ---------------------------------------------------------------------------
def trade_id(signal) -> str:
    return signal.trade_key


def submit(signal, settings: Settings, client: AlpacaClient, book: BrokerBook) -> BrokerTrade:
    """Egy belepesi jelzes bekuldese bracket megbizaskent. BrokerError, ha nem lehet."""
    cfg = settings.entry
    if signal.kind == "setup":
        raise BrokerError("Az elokeszulet meg nem vetel - csak kitores / visszateszt kuldheto be.")
    if not signal.qualifies(cfg):
        raise BrokerError("A jelzes nem felel meg a szuroknek / a min. pontszamnak.")
    if cfg.exit_mode not in ("partial", "target"):
        raise BrokerError(f"A(z) '{cfg.exit_mode}' kilepesi mod (kovetett stop) a paper kereskedesben "
                          "meg nem tamogatott - allitsd 'partial' vagy 'target' modra.")
    tid = trade_id(signal)
    plan = signal.plan
    limit, stop, target = price(plan.entry_limit), price(plan.stop), price(plan.target)
    shares = _checked_shares(signal, settings, client, book, risk_mult=cfg.risk_mult(signal.score))
    if not target > limit + 0.01:
        raise BrokerError("A cel nincs a belepo-limit felett.")

    legs: list[Leg] = []
    partial = price(plan.partial_price) if plan.uses_partial else None
    if cfg.exit_mode == "partial" and shares >= 2 and partial and limit + 0.01 < partial < target:
        half = shares // 2
        legs = [Leg("partial", half, partial), Leg("target", shares - half, target)]
    else:
        legs = [Leg("target", shares, target)]

    trade = BrokerTrade(
        id=tid, symbol=signal.symbol, pattern=signal.pattern.name, kind=signal.kind,
        signal_date=f"{signal.as_of:%Y-%m-%d}", entry_limit=limit, stop=stop, target=target,
        partial_price=partial if len(legs) == 2 else None, shares=shares, horizon=plan.horizon or 40,
        exit_mode=cfg.exit_mode, submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        signal_id=getattr(signal, "signal_id", ""), strategy=getattr(signal, "strategy", "breakout"),
        commission_pct=settings.broker.commission_pct,
    )
    prefix = "sw-" + hashlib.sha1(tid.encode()).hexdigest()[:16]
    placed: list[Leg] = []
    try:
        for leg in legs:
            order = client.submit_order({
                "symbol": signal.symbol, "qty": str(leg.qty), "side": "buy", "type": "limit",
                "time_in_force": "gtc", "limit_price": str(limit), "order_class": "bracket",
                "take_profit": {"limit_price": str(leg.take_profit)},
                "stop_loss": {"stop_price": str(stop)},
                "client_order_id": f"{prefix}-{leg.name}",
            })
            leg.order_id = order["id"]
            leg.entry_status = order.get("status", "new")
            _read_children(leg, order)
            placed.append(leg)
    except BrokerError:
        for leg in placed:                       # ne maradjon fel bracket a levegoben
            try:
                client.cancel_order(leg.order_id)
            except BrokerError:
                log.exception("A felig bekuldott bracket visszavonasa nem sikerult")
        raise
    trade.set_legs(legs)
    trade.note(f"bekuldve: {shares} db, limit {limit}, stop {stop}, "
               + ", ".join(f"{leg.name} {leg.qty} db -> {leg.take_profit}" for leg in legs))
    book.trades[tid] = trade
    return trade


def occupied_slots(book: BrokerBook) -> list[BrokerTrade]:
    """A max. nyitott trade-szamba szamito trade-ek: a nyitottak ES a fuggok (a Visszahuzas -5%-os limitje
    is). Igy egy esesnapon sem nyilhat a korlatnal tobb pozicio, akkor sem, ha minden fuggo limit egyszerre
    teljesul. A validalas szerint ez a gyengebb valtozat (a limitek tobbsege nem teljesul, megis helyet
    foglal) - tudatos dontes a szigoru korlat javara."""
    return book.active()


def _checked_shares(signal, settings: Settings, client: AlpacaClient, book: BrokerBook,
                    risk_mult: float = 1.0) -> int:
    """A bekuldes kozos vedelmei (duplikatum, egy trade / papir, max. nyitott, szamla, szabad keret)
    es a darabszam. NoRoom: nincs hely (ertesites, nem hiba); BrokerError: nem kuldheto be."""
    cfg = settings.entry
    if trade_id(signal) in book.trades:
        raise BrokerError("Ezt a jelzest mar bekuldtuk.")
    if book.active(signal.symbol):
        raise BrokerError(f"{signal.symbol}: mar van aktiv paper trade ezen a papiron.")
    if len(occupied_slots(book)) >= settings.broker.max_open_trades:
        raise NoRoom(f"Elerted a max. nyitott paper trade-ek szamat ({settings.broker.max_open_trades}).")

    account = client.account()
    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        raise BrokerError(f"A paper szamla nem kereskedheto (status: {account.get('status')}).")
    if any(p.get("symbol") == signal.symbol for p in client.positions()):
        raise BrokerError(f"{signal.symbol}: a paper szamlan mar van pozicio ebben a papirban.")
    open_orders = client.orders("open")
    if any(o.get("symbol") == signal.symbol for o in open_orders):
        raise BrokerError(f"{signal.symbol}: a paper szamlan mar van nyitott megbizas erre a papirra.")

    plan = signal.plan
    limit, stop = price(plan.entry_limit), price(plan.stop)
    shares = plan.shares
    if settings.broker.size_from_account:
        equity = float(account.get("equity") or 0)
        risk = cfg.risk_per_trade_pct * risk_mult                    # eros jelzes: nagyobb kockazat
        shares = entry_mod.position_size(plan.entry, plan.stop,
                                         cfg.model_copy(update={"account_size": equity, "risk_per_trade_pct": risk}))
    if shares < 1:
        raise BrokerError("0 darab jonne ki (tul nagy stop vagy tul kicsi szamla).")
    free = free_cash(account, open_orders)
    if shares * limit > free:
        raise NoRoom(f"Nincs eleg szabad keret (margin nelkul): {shares * limit:,.0f} $ kellene, "
                     f"{free:,.0f} $ szabad (keszpenz - fuggo vetelek).")
    if not stop < limit - 0.01:
        raise BrokerError("A stop nincs a belepo-limit alatt.")
    return shares


def submit_pullback(signal, settings: Settings, client: AlpacaClient, book: BrokerBook, params) -> BrokerTrade:
    """Visszahuzas: OTO megbizas - veteli limit (a jelzes utani napra) + GTC stop. Nincs cel-limit:
    a kilepes indikatoros (a zaron szuletett jel utan nyitaskor piaci aron, lasd _sync_trade)."""
    if signal.kind != "pullback":
        raise BrokerError("Nem visszahuzas-jelzes.")
    if not signal.passed:
        raise BrokerError("A jelzes nem felel meg a szabalyoknak.")
    tid = trade_id(signal)
    plan = signal.plan
    limit, stop = price(plan.entry_limit), price(plan.stop)
    shares = _checked_shares(signal, settings, client, book)
    leg = Leg("target", shares, 0.0)
    order = client.submit_order({
        "symbol": signal.symbol, "qty": str(shares), "side": "buy", "type": "limit", "time_in_force": "gtc",
        "limit_price": str(limit), "order_class": "oto", "stop_loss": {"stop_price": str(stop)},
        "client_order_id": "sw-" + hashlib.sha1(tid.encode()).hexdigest()[:16] + "-pb",
    })
    leg.order_id, leg.entry_status = order["id"], order.get("status", "new")
    _read_children(leg, order)
    trade = BrokerTrade(
        id=tid, symbol=signal.symbol, pattern=signal.pattern.name, kind=signal.kind,
        signal_date=f"{signal.as_of:%Y-%m-%d}", entry_limit=limit, stop=stop, target=price(plan.target),
        partial_price=None, shares=shares, horizon=params.time_stop, exit_mode="indicator",
        submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        signal_id=signal.signal_id, strategy=signal.strategy, params=params.model_dump(),
        commission_pct=settings.broker.commission_pct,
    )
    trade.set_legs([leg])
    trade.note(f"bekuldve (OTO): {shares} db, limit {limit}, stop {stop}; kilepes: ConnorsRSI > "
               f"{params.exit_crsi:g} utan nyitaskor, legkesobb {params.time_stop} nap")
    book.trades[tid] = trade
    return trade


def submit_momentum(signal, settings: Settings, client: AlpacaClient, book: BrokerBook, params) -> BrokerTrade:
    """Momentum (12-1): piaci vetel a rebalanszt koveto NYITASKOR (a zaras utan bekuldott napi piaci
    megbizast az Alpaca a nyitasra allitja sorba). Darabszam: a paper toke / top_n, legfeljebb a szabad
    keszpenz. Stop csak `stop_pct` > 0 eseten (OTO). A kilepest a havi rebalansz jeloli ki (exit_pending),
    es a nyitoaukcion megy (preopen)."""
    if signal.kind != "momentum":
        raise BrokerError("Nem momentum-jelzes.")
    tid = trade_id(signal)
    if tid in book.trades:
        raise BrokerError("Ezt a jelzest mar bekuldtuk.")
    if book.active(signal.symbol):
        raise BrokerError(f"{signal.symbol}: mar van aktiv paper trade ezen a papiron.")
    held = [t for t in book.active() if t.strategy == "momentum"]
    if len(held) >= params.top_n:
        raise NoRoom(f"A momentum-portfolio tele van ({params.top_n} papir).")
    account = client.account()
    if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
        raise BrokerError(f"A paper szamla nem kereskedheto (status: {account.get('status')}).")
    if any(p.get("symbol") == signal.symbol for p in client.positions()):
        raise BrokerError(f"{signal.symbol}: a paper szamlan mar van pozicio ebben a papirban.")
    open_orders = client.orders("open")
    if any(o.get("symbol") == signal.symbol for o in open_orders):
        raise BrokerError(f"{signal.symbol}: a paper szamlan mar van nyitott megbizas erre a papirra.")

    close = float(signal.price)
    equity = float(account.get("equity") or 0) if settings.broker.size_from_account else settings.entry.account_size
    budget = min(equity / params.top_n, free_cash(account, open_orders) / 1.02)   # 2% tartalek a nyitoresre
    shares = int(budget // close) if close > 0 else 0
    if shares < 1:
        raise NoRoom(f"Nincs eleg szabad keret egy darabra sem ({budget:,.0f} $, ar {close:.2f} $).")
    payload = {"symbol": signal.symbol, "qty": str(shares), "side": "buy", "type": "market", "time_in_force": "day",
               "client_order_id": "sw-" + hashlib.sha1(tid.encode()).hexdigest()[:16] + "-mo"}
    stop = price(signal.plan.stop) if params.stop_pct > 0 else 0.0
    if stop:
        payload.update(order_class="oto", stop_loss={"stop_price": str(stop)})
    order = client.submit_order(payload)
    leg = Leg("target", shares, 0.0)
    leg.order_id, leg.entry_status = order["id"], order.get("status", "new")
    _read_children(leg, order)
    trade = BrokerTrade(
        id=tid, symbol=signal.symbol, pattern=signal.pattern.name, kind=signal.kind,
        signal_date=f"{signal.as_of:%Y-%m-%d}", entry_limit=price(close), stop=price(signal.plan.stop),
        target=price(close), partial_price=None, shares=shares, horizon=0, exit_mode="indicator",
        submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        signal_id=signal.signal_id, strategy="momentum", params=params.model_dump(),
        commission_pct=settings.broker.commission_pct,
    )
    trade.set_legs([leg])
    trade.note(f"bekuldve: {shares} db piaci vetel nyitaskor (~{100 / params.top_n:.0f}% toke)"
               + (f", stop {stop}" if stop else ", stop nelkul") + "; kilepes: ha kiesik a legjobb "
               f"{params.buffer} kozul a havi rebalanszon")
    book.trades[tid] = trade
    return trade


def mark_momentum_exits(book: BrokerBook, symbols: list[str], day: str) -> list[BrokerTrade]:
    """A havi rebalansz kilepesi dontese a paper trade-ekre: holnap a nyitoaukcion (preopen) eladjuk."""
    out = []
    for trade in book.active():
        if trade.strategy == "momentum" and trade.symbol in symbols and trade.status == "open" and not trade.exit_pending:
            trade.exit_pending = "rotation"
            trade.note(f"havi rebalansz ({day}): kiesett a legjobbak kozul - eladas a kovetkezo nyitaskor")
            out.append(trade)
    return out


def pullback_exit_due(trade: BrokerTrade, bars: pd.DataFrame) -> str:
    """Visszahuzas: a LEZART napi gyertyan teljesult-e a kilepesi feltetel ("signal" | "time" | "")."""
    from . import meanrev
    from .config import PullbackParams

    if bars.empty or not trade.filled_at or trade.strategy == "momentum":
        return ""                               # a momentum kilepeset a havi rebalansz jeloli ki
    params = PullbackParams(**trade.params) if trade.params else PullbackParams()
    feat = meanrev.features(bars, params)
    if bool(meanrev.exit_flags(feat, params).iloc[-1]):
        return "signal"
    return "time" if bars_held(trade, bars) >= params.time_stop else ""


def _read_children(leg: Leg, order: dict) -> None:
    for child in order.get("legs") or []:
        if child.get("type") in ("stop", "stop_limit"):
            leg.sl_id = child["id"]
        elif child.get("type") == "limit":
            leg.tp_id = child["id"]


# ---------------------------------------------------------------------------
# Szinkronizalas
# ---------------------------------------------------------------------------
def _fill_price(order: dict) -> float | None:
    value = order.get("filled_avg_price")
    return float(value) if value not in (None, "") else None


def _fill_ts(order: dict) -> str | None:
    """A teljesules pontos ideje ISO UTC-ben (az Alpaca `filled_at`-jebol)."""
    stamp = order.get("filled_at")
    if not stamp:
        return None
    return pd.Timestamp(stamp).tz_convert("UTC").isoformat(timespec="seconds")


def _fill_date(order: dict) -> str | None:
    stamp = order.get("filled_at")
    if not stamp:
        return None
    return pd.Timestamp(stamp).tz_convert(MARKET_TZ).strftime("%Y-%m-%d")


def _today() -> pd.Timestamp:
    return pd.Timestamp.now(tz=MARKET_TZ).normalize().tz_localize(None)


def entry_session_over(trade: BrokerTrade, bars: pd.DataFrame, market_open: bool) -> bool:
    """Lezajlott-e mar a jelzes utani elso kereskedesi nap (a belepo napja)."""
    after = bars.index[bars.index > pd.Timestamp(trade.signal_date)]
    if len(after) == 0:
        return False
    first = after[0]
    return first < _today() or not market_open


def bars_held(trade: BrokerTrade, bars: pd.DataFrame, include_today: bool = False) -> int:
    """A belepes napja is szamit (ugyanugy, mint a trade_mgmt-ben).

    `include_today`: nyitvatartas alatt a mai, meg alakulo nap is szamit - a cache-ben
    lehet, hogy meg nincs benne (az indexpapirok csak zaras utan frissulnek)."""
    if not trade.filled_at:
        return 0
    held = bars.index >= pd.Timestamp(trade.filled_at)
    if include_today:
        return int((held & (bars.index < _today())).sum()) + 1
    return int(held.sum())


def minutes_to_close(clock: dict) -> float | None:
    """Hany perc van a piaczarasig (az Alpaca oraja szerint - roviditett napokon is jo)."""
    try:
        return (pd.Timestamp(clock["next_close"]) - pd.Timestamp(clock["timestamp"])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        return None


def session_open_at(clock: dict) -> pd.Timestamp | None:
    """A mai nyitas idopontja (New York), ha a piac most nyitva van es az ora ismert."""
    if not clock.get("is_open") or not clock.get("timestamp"):
        return None
    try:
        now = pd.Timestamp(clock["timestamp"]).tz_convert(MARKET_TZ)
    except (TypeError, ValueError):
        return None
    return now.normalize() + pd.Timedelta(hours=9, minutes=30)


def entry_window_passed(trade: BrokerTrade, settings: Settings, clock: dict) -> bool:
    """Kitores: a nyitas utan `entry_window_min` perccel sem teljesult a (nyitas ELOTT bekuldott) belepo
    -> a nyito a limit folott volt, a backtest szerint nincs belepes. A napkozben kezzel bekuldott
    megbizasra nem vonatkozik."""
    window = settings.broker.entry_window_min
    opened = session_open_at(clock)
    if not window or opened is None or trade.exit_mode == "indicator" or not trade.submitted_at:
        return False
    now = pd.Timestamp(clock["timestamp"]).tz_convert(MARKET_TZ)
    submitted = pd.Timestamp(trade.submitted_at)
    submitted = (submitted.tz_localize("UTC") if submitted.tzinfo is None else submitted).tz_convert(MARKET_TZ)
    return submitted < opened and now >= opened + pd.Timedelta(minutes=window)


def sync(
    settings: Settings,
    client: AlpacaClient,
    book: BrokerBook,
    final_bar: bool = False,
) -> list[tuple[BrokerTrade, str]]:
    """A nyitott trade-ek allapotanak frissitese az Alpacabol + a kilepesi szabalyok.

    `final_bar`: lezart napi gyertyan fut-e. Az idokorlatot ekkor, vagy nyitvatartas
    alatt a zaras elotti utolso CLOSE_WINDOW_MIN percben nezzuk.
    """
    events: list[tuple[BrokerTrade, str]] = []
    if not book.active():
        return events
    clock = client.clock()
    market_open = bool(clock.get("is_open"))
    left = minutes_to_close(clock)
    closing = market_open and left is not None and left <= CLOSE_WINDOW_MIN
    for trade in book.active():
        try:
            events += _sync_trade(trade, settings, client, market_open, final_bar, closing, clock)
        except BrokerError as exc:
            trade.note(f"szinkron hiba: {exc}")
            log.warning("Paper trade szinkron hiba (%s): %s", trade.id, exc)
    return events


def _sync_trade(trade: BrokerTrade, settings: Settings, client: AlpacaClient,
                market_open: bool, final_bar: bool, closing: bool = False,
                clock: dict | None = None) -> list[tuple[BrokerTrade, str]]:
    events: list[tuple[BrokerTrade, str]] = []
    legs = trade.leg_objs()
    was_open = trade.status == "open"

    # 1. allapotok az Alpacabol
    for leg in legs:
        if leg.done:
            continue
        parent = client.get_order(leg.order_id)
        leg.entry_status = parent.get("status", leg.entry_status)
        leg.filled_qty = int(float(parent.get("filled_qty") or 0))
        leg.entry_fill = _fill_price(parent) or leg.entry_fill
        if parent.get("status") == "filled" and not trade.filled_at:
            trade.filled_at = _fill_date(parent)
        if leg.filled_qty and not trade.filled_ts:
            trade.filled_ts = _fill_ts(parent)
        if parent.get("status") in ("canceled", "expired", "rejected") and leg.filled_qty == 0:
            leg.exit_reason = "not_filled"
            continue
        if leg.filled_qty == 0:
            continue

        if leg.exit_order_id:                       # idokorlat / kezi zaras folyamatban
            exit_order = client.get_order(leg.exit_order_id)
            status = exit_order.get("status")
            if status == "filled":
                leg.exit_price = _fill_price(exit_order)
                leg.exit_reason = leg.exit_intent or "manual"
                leg.exit_ts = _fill_ts(exit_order) or ""
            elif status in ("canceled", "expired", "rejected") and not float(exit_order.get("filled_qty") or 0):
                # A cel / stop mar vissza van vonva: e nelkul a pozicio vedtelenul ragadna (pl. egy
                # nyitoaukcion nem teljesult OPG, vagy egy elutasitott eladas). Ujra piaci aron.
                order = client.submit_order({
                    "symbol": trade.symbol, "qty": str(leg.filled_qty), "side": "sell", "type": "market",
                    "time_in_force": "day",
                })
                trade.note(f"a kilepo megbizas {status} - ujra bekuldve piaci aron")
                leg.exit_order_id = order["id"]
                if order.get("status") == "filled":
                    leg.exit_price, leg.exit_reason = _fill_price(order), leg.exit_intent or "manual"
                events.append((trade, "broker_exit_retry"))
            elif status in ("canceled", "expired", "rejected"):
                trade.note(f"FIGYELEM: a kilepo megbizas reszben teljesult, majd {status} - kezi ellenorzes kell")
            continue
        reason = _check_children(client, leg)
        if reason == "take_profit":
            events.append((trade, "broker_partial" if leg.name == "partial" else "broker_target"))
        elif reason == "stop":
            events.append((trade, "broker_stop"))

    filled = [leg for leg in legs if leg.filled_qty > 0]
    if filled and not was_open:
        trade.status = "open"
        qty = sum(leg.filled_qty for leg in filled)
        trade.entry_price = sum((leg.entry_fill or 0) * leg.filled_qty for leg in filled) / qty
        trade.filled_at = trade.filled_at or _today().strftime("%Y-%m-%d")
        trade.note(f"belepes teljesult: {qty} db @ {trade.entry_price:.2f}")
        events.insert(0, (trade, "broker_filled"))

    # 1/b. kitores: a belepo csak a nyitaskor teljesulhet (mint a backtestben)
    if trade.status == "submitted" and clock is not None and entry_window_passed(trade, settings, clock):
        for leg in legs:
            if not leg.done and leg.filled_qty == 0:
                _cancel_quietly(client, leg.order_id)
        # a visszavonas pillanataban teljesulhetett: akkor a kovetkezo szinkron nyitottkent veszi at
        if any(float(client.get_order(leg.order_id).get("filled_qty") or 0) > 0 for leg in legs if not leg.done):
            trade.note("a belepo a visszavonas kozben teljesult - nyitott trade-kent kovetjuk")
            trade.set_legs(legs)
            return events
        for leg in legs:
            if not leg.done:
                leg.exit_reason = "not_filled"
        trade.set_legs(legs)
        trade.status = "expired"
        trade.closed_at = _today().strftime("%Y-%m-%d")
        trade.note(f"a nyitas utan {settings.broker.entry_window_min} perccel sem teljesult (a nyito a limit "
                   "folott volt) - visszavonva, mint a backtestben")
        return [(trade, "broker_expired")]

    bars = store.load(trade.symbol, settings)

    # 2. a belepo-limit csak a jelzes utani napra ervenyes
    if trade.status == "submitted" and not bars.empty and entry_session_over(trade, bars, market_open):
        for leg in legs:
            if not leg.done and leg.filled_qty == 0:
                _cancel_quietly(client, leg.order_id)
                leg.exit_reason = "not_filled"
        trade.set_legs(legs)
        trade.status = "expired"
        trade.closed_at = _today().strftime("%Y-%m-%d")
        trade.note("a belepo-limit a jelzes utani napon nem teljesult - visszavonva")
        return [(trade, "broker_expired")]

    # 3. nullszaldo: a fele +1R-nel kiment -> a masik fel stopja a belepore
    partial = next((leg for leg in legs if leg.name == "partial"), None)
    rest = next((leg for leg in legs if leg.name == "target"), None)
    if (partial and rest and partial.exit_reason == "take_profit" and not rest.done
            and not trade.breakeven_done and rest.sl_id and trade.entry_price):
        new_stop = price(rest.entry_fill or trade.entry_price)
        replaced = client.replace_order(rest.sl_id, stop_price=new_stop)
        rest.sl_id = replaced.get("id", rest.sl_id)
        trade.breakeven_done = True
        trade.note(f"stop a belepore huzva: {new_stop}")
        events.append((trade, "broker_breakeven"))

    # 4/b. Visszahuzas: a zaron szuletett kilepesi jel (CRSI / idokorlat) -> a kovetkezo NYITASKOR piaci aron
    if trade.exit_mode == "indicator":
        if trade.status == "open" and trade.exit_pending and market_open:
            if _close_legs(client, trade, legs, trade.exit_pending):
                trade.note(f"kilepes nyitaskor piaci aron ({trade.exit_pending})")
                events.append((trade, "broker_exit"))
        elif trade.status == "open" and final_bar and not trade.exit_pending:
            due = pullback_exit_due(trade, sessions.completed_bars(bars))
            if due:
                trade.exit_pending = due
                trade.note("kilepesi jel a zaron (" + ("ConnorsRSI" if due == "signal" else "idokorlat") + ")")
                events.append((trade, "broker_exit_signal"))
        trade.set_legs(legs)
        _maybe_close(trade, legs, events)
        return events

    # 4. idokorlat - az utolso napon meg zaras elott (piaci aron, mint a backtest napi zaroja);
    #    ha a monitor akkor nem futott, zaras utan (a megbizas a kovetkezo nyitason teljesul)
    if (trade.status == "open" and not bars.empty and (closing or final_bar)
            and bars_held(trade, bars, include_today=closing and not final_bar)
            >= max_hold(trade.horizon, trade.exit_mode)):
        if _close_legs(client, trade, legs, "timeout"):
            trade.note(f"idokorlat ({trade.horizon} nap) - zaras piaci aron")
            events.append((trade, "broker_timeout"))

    trade.set_legs(legs)
    _maybe_close(trade, legs, events)
    return events


def _check_children(client: AlpacaClient, leg: Leg) -> str:
    """A cel- es stop-lab allapota; ha valamelyik teljesult, rogziti. Visszaadja az okot."""
    for attr, reason in (("tp_id", "take_profit"), ("sl_id", "stop")):
        child_id = getattr(leg, attr)
        if not child_id or leg.exit_reason:
            continue
        child = client.get_order(child_id)
        setattr(leg, attr, child.get("id", child_id))     # ha kozben lecsereltek
        if child.get("status") == "filled":
            leg.exit_reason, leg.exit_price = reason, _fill_price(child)
            leg.exit_ts = _fill_ts(child) or ""
            return reason
    return ""


def _cancel_quietly(client: AlpacaClient, order_id: str) -> None:
    if not order_id:
        return
    try:
        client.cancel_order(order_id)
    except BrokerError as exc:
        log.info("Visszavonas nem sikerult (%s): %s", order_id, exc)


def _close_legs(client: AlpacaClient, trade: BrokerTrade, legs: list[Leg], reason: str,
                tif: str = "day") -> bool:
    """A meg nyitott labak: cel/stop visszavonasa, majd eladas piaci aron. `tif="opg"`: a nyitoaukcion."""
    acted = False
    for leg in legs:
        if leg.done or leg.exit_order_id:
            continue
        if leg.filled_qty == 0:
            _cancel_quietly(client, leg.order_id)
            leg.exit_reason = "not_filled"
            continue
        # Ha kozben a cel vagy a stop teljesult, nem adunk el meg egyszer (short lenne belole).
        if _check_children(client, leg):
            continue
        _cancel_quietly(client, leg.tp_id)
        _cancel_quietly(client, leg.sl_id)
        if _check_children(client, leg):          # a visszavonas pillanataban teljesult
            continue
        leg.exit_intent = reason
        order = client.submit_order({
            "symbol": trade.symbol, "qty": str(leg.filled_qty), "side": "sell", "type": "market",
            "time_in_force": tif,
        })
        leg.exit_order_id = order["id"]
        if order.get("status") == "filled":
            leg.exit_price = _fill_price(order)
        if leg.exit_price is not None:
            leg.exit_reason = reason
        acted = True
    return acted


def _maybe_close(trade: BrokerTrade, legs: list[Leg], events: list) -> None:
    if not legs or not all(leg.done for leg in legs):
        return
    traded = [leg for leg in legs if leg.filled_qty > 0]
    trade.closed_at = _today().strftime("%Y-%m-%d")
    if not traded:
        trade.status = "cancelled"
        return
    trade.status = "closed"
    gross = sum(((leg.exit_price or 0) - (leg.entry_fill or 0)) * leg.filled_qty for leg in traded)
    # a kotesi dij a vetel es az eladas erteke utan is (a csuszas mar benne van a valodi kitoltesi arakban)
    fee = trade.commission_pct / 100 * sum(((leg.exit_price or 0) + (leg.entry_fill or 0)) * leg.filled_qty
                                           for leg in traded)
    trade.realised_pnl = round(gross - fee, 2)
    trade.note(f"lezarva, eredmeny {trade.realised_pnl:+.2f} $" + (f" (kotesi dij: {fee:.2f} $)" if fee else ""))
    events.append((trade, "broker_closed"))


def preopen(settings: Settings, client: AlpacaClient, book: BrokerBook) -> list[tuple[BrokerTrade, str]]:
    """Nyitas elott (a monitor 9:05-kor): a Visszahuzas zaron szuletett kilepesi jele -> eladas a
    NYITOAUKCION (OPG piaci megbizas). A backtest pontosan a nyitoaron lep ki; a nyitas utani elso
    szinkronig varva (percekkel kesobb) mas aron teljesulne. Ha az OPG nem megy be, a nyitas utani
    szinkron piaci aron zar (mint eddig)."""
    events: list[tuple[BrokerTrade, str]] = []
    pending = [t for t in book.active() if t.exit_mode == "indicator" and t.status == "open" and t.exit_pending]
    if not pending or client.clock().get("is_open"):
        return events
    for trade in pending:
        legs = trade.leg_objs()
        try:
            if _close_legs(client, trade, legs, trade.exit_pending, tif="opg"):
                trade.note(f"kilepes a nyitoaukcion (OPG, {trade.exit_pending})")
                events.append((trade, "broker_exit"))
        except BrokerError as exc:
            trade.note(f"az OPG kilepes nem ment be ({exc}) - nyitaskor piaci aron zar")
            log.warning("OPG kilepes sikertelen (%s): %s", trade.id, exc)
        finally:
            trade.set_legs(legs)
    return events


def readiness(book: BrokerBook, settings: Settings) -> dict[str, dict]:
    """Eles kereskedes elotti kapu stratégiankent, a LEZART paper trade-ekbol.

    Csak akkor "ready", ha eleg trade van, a nyeresi arany, a profit-faktor es az osszesitett eredmeny
    is atmegy. A Wilson-intervallum mutatja, mennyire biztos a nyeresi arany (50 trade-nel +/-12%)."""
    from .entry_research import wilson

    cfg = settings.broker
    out: dict[str, dict] = {}
    closed = [t for t in book.trades.values() if t.status == "closed" and t.realised_pnl is not None]
    for strategy in sorted({t.strategy for t in closed}):
        trades = [t for t in closed if t.strategy == strategy]
        pnl = [t.realised_pnl for t in trades]
        wins = sum(p > 0 for p in pnl)
        gross_win, gross_loss = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p <= 0)
        pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
        pct = [t.realised_pnl / (t.entry_price * t.shares) * 100 for t in trades if t.entry_price and t.shares]
        lo, hi = wilson(wins, len(trades))
        n, win = len(trades), wins / len(trades)
        checks = [
            (f"legalabb {cfg.live_gate_min_trades} lezart trade ({n})", n >= cfg.live_gate_min_trades),
            (f"nyeresi arany >= {cfg.live_gate_win:.0%} ({win:.0%})", win >= cfg.live_gate_win),
            (f"profit-faktor >= {cfg.live_gate_pf:g} ({pf:.2f})", pf >= cfg.live_gate_pf),
            (f"osszesitett eredmeny > 0 ({sum(pnl):+,.0f} $)", sum(pnl) > 0),
        ]
        out[strategy] = {"n": n, "wins": wins, "win": win, "win_lo": lo, "win_hi": hi, "pf": pf,
                         "pnl": sum(pnl), "avg_pct": sum(pct) / len(pct) if pct else None,
                         "checks": checks, "ready": all(ok for _, ok in checks)}
    return out


def readiness_lines(book: BrokerBook, settings: Settings) -> list[str]:
    """Rovid szoveg (CLI, eletjel): stratégiankent az eles kapu allapota."""
    lines = []
    for strategy, r in readiness(book, settings).items():
        verdict = "ATMENT - elesitheto" if r["ready"] else "meg nem"
        avg = f", atlag {r['avg_pct']:+.2f}% / trade" if r["avg_pct"] is not None else ""
        lines.append(f"Eles kapu [{strategy}]: {verdict} | {r['n']} trade, nyero {r['win']:.0%} "
                     f"(95%: {r['win_lo']:.0%}-{r['win_hi']:.0%}), PF {r['pf']:.2f}{avg}")
    return lines


def close_trade(trade: BrokerTrade, client: AlpacaClient) -> None:
    """Kezi zaras / visszavonas a feluletrol."""
    legs = trade.leg_objs()
    if trade.status == "submitted":
        for leg in legs:
            _cancel_quietly(client, leg.order_id)
            leg.exit_reason = leg.exit_reason or "not_filled"
        trade.set_legs(legs)
        trade.status = "cancelled"
        trade.closed_at = _today().strftime("%Y-%m-%d")
        trade.note("kezzel visszavonva")
        return
    if trade.status != "open":
        raise BrokerError("Ez a trade mar nem aktiv.")
    _close_legs(client, trade, legs, "manual")
    trade.set_legs(legs)
    trade.note("kezi zaras piaci aron")
    _maybe_close(trade, legs, [])


# ---------------------------------------------------------------------------
# Szoveg
# ---------------------------------------------------------------------------
def format_stamp(iso: str | None) -> str:
    """Pontos idopont magyar idoben, zarojelben a New York-i ido (a piac ideje)."""
    if not iso:
        return ""
    try:
        ts = pd.Timestamp(iso)
    except (TypeError, ValueError):
        return str(iso)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return f"{ts.tz_convert('Europe/Budapest'):%Y-%m-%d %H:%M} (New York {ts.tz_convert(MARKET_TZ):%H:%M})"


def format_event(trade: BrokerTrade, event: str, extra: str = "") -> tuple[str, str]:
    title = f"{trade.symbol}: {trade.pattern} - {EVENT_LABELS.get(event, event)}"
    lines = [f"Jelzes napja: {trade.signal_date}  |  {trade.shares} db"]
    if trade.submitted_at:
        lines.append(f"Bekuldve: {format_stamp(trade.submitted_at)}")
    if trade.entry_price:
        lines.append(f"Belepo (teljesult): {trade.entry_price:.2f} $"
                     + (f"  |  {format_stamp(trade.filled_ts)}" if trade.filled_ts else ""))
    else:
        lines.append(f"Belepo-limit: {trade.entry_limit:.2f} $")
    if trade.strategy == "momentum":
        lines.append("Kilepes: a havi rebalanszon, ha kiesik a legjobbak kozul (nyitoaukcion)"
                     + (f" | stop: {trade.stop:.2f} $" if (trade.params or {}).get("stop_pct") else " | stop nincs"))
    else:
        lines.append(f"Stop: {trade.stop:.2f} $ | Cel: {trade.target:.2f} $"
                     + (f" | Fele: {trade.partial_price:.2f} $" if trade.partial_price else ""))
    if trade.realised_pnl is not None:
        lines.append(f"Eredmeny: {trade.realised_pnl:+,.2f} $")
    if extra:
        lines.append(extra)
    lines += ["", "Alpaca PAPER szamla - nem valodi penz. Nem befektetesi tanacs."]
    return title, "\n".join(lines)


def describe_credentials() -> dict:
    """A feluletnek: be van-e allitva - a titkot SOHA nem adjuk vissza."""
    endpoint, key, secret = alpaca_credentials()
    return {
        "configured": bool(key and secret),
        "endpoint": endpoint or PAPER_ENDPOINT,
        "key_hint": f"...{key[-4:]}" if key and len(key) >= 8 else ("beallitva" if key else None),
        "secret_set": bool(secret),
    }

