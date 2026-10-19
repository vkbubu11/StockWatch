"""Helyi webes felulet - sima HTML/CSS/JS, a Python standard konyvtaranak
webszerverevel. Nincs uj fuggoseg.

    python cli.py web            ->  http://127.0.0.1:8765

BIZTONSAG
---------
  * alapbol csak a 127.0.0.1 cimen figyel - a halozat tobbi gepe nem eri el
  * szerveren (`--behind-proxy`, HTTPS reverse proxy mogott) jelszavas bejelentkezes
    kotelezo, lasd webauth.py; jelszo nelkul ilyenkor semmit nem szolgal ki
  * a modosito (POST) keresek egyedi fejlecet varnak (X-StockWatch). Egy idegen
    weboldal a bongeszodbol ilyet nem tud kuldeni a localhostra (CORS preflight
    nelkul nem mehet at), igy nem tud a hatad mogott beallitast atirni
  * titkot (Telegram token, webhook URL) a felulet soha nem olvas es nem mutat; az
    Alpaca kulcsokat a Beallitasok fulon BE lehet irni (a .env-be kerulnek), de
    visszaolvasni nem - a felulet csak a kulcs utolso 4 karakteret mutatja

Ertesitest a felulet egy esetben kuld: az indexszkennelesnel (Nasdaq-100 / S&P 500),
amikor egy indexet bekapcsolsz, illetve zaras utan (16:20 NY), ha aznap a monitor meg
nem futtatta - igy eleg, ha a kettobol az egyik fut. Minden mas ertesites a
`python cli.py monitor` dolga. Kereskedni csak az Alpaca PAPER szamlan tud, kezi
gombnyomasra. A "Frissites" gomb csak letolti az uj adatot es ujraszamolja a jelolteket.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import re
import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from . import (
    broker, config, entry, feed, holdings, indicators, ledger, market, monitor, positions,
    sessions, signal_review, store, webauth,
)
from . import replay as replay_mod
from .alerts import Alert, Dispatcher
from .config import BENCHMARK_NAMES, CONFIG_DIR, PROJECT_ROOT, EntrySettings, load_settings, load_watchlist
from .forecast import Calibration, analyse
from .locks import LockTimeout

log = logging.getLogger(__name__)

WEB_DIR = PROJECT_ROOT / "app" / "web"
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")
CHART_BARS = 260

# A feluletrol allithato beallitasok: kulcs -> (szekcio, tipus)
EDITABLE = {
    "account_size": ("entry", float),
    "risk_per_trade_pct": ("entry", float),
    "max_position_pct": ("entry", float),
    "min_score": ("entry", float),
    "min_stop_atr": ("entry", float),
    "min_risk_reward": ("entry", float),
    "partial_r": ("entry", float),
    "exit_mode": ("entry", str),
    "confirm_breakout": ("entry", bool),
    "alert_setups": ("entry", bool),
    "alert_retests": ("entry", bool),
    "regime_filter": ("entry", bool),
    "earnings_blackout_days": ("entry", int),
    "pattern_events": ("alerts", bool),
    "direction": ("alerts", str),
    "language": ("alerts", str),
    "auto_trade": ("broker", bool),
    "size_from_account": ("broker", bool),
    "max_open_trades": ("broker", int),
    "commission_pct": ("broker", float),
    "nasdaq100": ("scan", bool),
    "sp500": ("scan", bool),
    "index_setups": ("scan", bool),
    "best_list": ("scan", str),
    "send_discord": ("digest", bool),
}


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def _clean(value):
    """pandas / numpy ertekek JSON-baratta alakitasa (NaN -> null)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d") if value == value.normalize() else value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else v
    return value


def _dumps(payload) -> bytes:
    return json.dumps(_clean(payload), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Allapot: a kiszamolt attekintes cache-elve, a frissites hatterszalon
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.overview: dict | None = None
        self.signals: dict[str, list[entry.EntrySignal]] = {}
        self.forecasts: dict = {}
        self.data_stamp: tuple | None = None
        self.job = {"state": "idle", "message": "", "started": None, "finished": None}
        self.best: dict | None = None          # a legutobbi "Legjobb belepo most" eredmeny

    # -- adat valtozasanak eszlelese (pl. a monitor kozben frissitett) ----
    def _stamp(self) -> tuple:
        settings = load_settings()
        files = sorted(settings.ohlcv_dir.glob("*.csv"))
        extra = [CONFIG_DIR / "settings.yaml", CONFIG_DIR / "watchlist.yaml", config.STRATEGIES_PATH,
                 settings.positions_path,
                 settings.calibration_path, settings.holdings_path, settings.index_scan_path]
        return tuple((p.name, p.stat().st_mtime) for p in files + extra if p.exists())

    def ensure(self) -> dict:
        stamp = self._stamp()
        with self.lock:
            if self.overview is None or stamp != self.data_stamp:
                self._compute()
                self.data_stamp = stamp
            return self.overview

    def _compute(self) -> None:
        config.load_settings.cache_clear()
        config.load_watchlist.cache_clear()
        settings = load_settings()
        symbols = load_watchlist().symbols

        sink: list[entry.EntrySignal] = []
        # a sajat poziciok papirjait is elemezzuk (a kiertekelesukhoz), de a
        # watchlist-tablaban csak a figyelt papirok szerepelnek
        forecasts = monitor.scan(
            settings, symbols=monitor.default_symbols(settings), send_alerts=False,
            entry_sink=sink, refresh=False,
        )
        self.forecasts = {f.symbol: f for f in forecasts}
        try:                    # a tarolt hirekbol (halozat nelkul) - a frissitest a monitor / a gomb vegzi
            from .sentiment import service as news

            news.annotate([s for s in sink if s.passed and s.kind != "setup" and s.news is None], settings,
                          refresh_news=False)
        except Exception:
            log.exception("A hir-jellemzok hibara futottak (felulet)")
        self.signals = {}
        for s in sink:
            self.signals.setdefault(s.symbol, []).append(s)

        ctx = market.load_context(settings, refresh=False)
        tracker = positions.PositionTracker(settings, read_only=True)
        own = holdings.HoldingStore(settings, read_only=True)

        rows = []
        for symbol in symbols:
            f = self.forecasts.get(symbol)
            if f is None:
                rows.append({"symbol": symbol, "missing": True})
                continue
            df = store.load(symbol, settings)
            change = None
            if len(df) >= 2:
                change = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100
            sigs = sorted(self.signals.get(symbol, []), key=lambda x: (x.qualifies(settings.entry), x.score),
                          reverse=True)
            best = sigs[0] if sigs else None
            rows.append({
                "symbol": symbol,
                "price": f.price,
                "change_pct": change,
                "as_of": f.as_of,
                "label": f.label,
                "score": f.score,
                "direction": f.direction,
                "pattern": f.pattern.name if f.pattern else None,
                "pattern_status": f.pattern.status if f.pattern else None,
                "pattern_direction": f.pattern.direction if f.pattern else None,
                "active_position": bool(tracker.active(symbol)),
                "own_position": bool(own.open(symbol)),
                "entry": _signal_brief(best, settings.entry) if best else None,
            })

        candidates = [
            _signal_brief(s, settings.entry)
            for sigs in self.signals.values() for s in sigs
        ]
        candidates.sort(key=lambda c: (c["qualifies"], c["score"]), reverse=True)

        self.overview = {
            "index": index_payload(settings),
            "pattern_stats": pattern_stats(settings),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "last_bar": max((r.get("as_of") for r in rows if r.get("as_of") is not None), default=None),
            "regime": regime_payload(ctx, settings),
            "rows": rows,
            "candidates": candidates,
            "settings": {
                "min_score": settings.entry.min_score,
                "exit_mode": settings.entry.exit_mode,
                "confirm_breakout": settings.entry.confirm_breakout,
                "account_size": settings.entry.account_size,
                "broker_configured": broker.describe_credentials()["configured"],
                "auto_trade": settings.broker.auto_trade,
            },
        }

    # -- nem figyelt papir (pl. indexjelolt) elemzese igeny szerint --------
    def analysis(self, symbol: str, enriched: pd.DataFrame | None = None):
        """(forecast, belepesi jelzesek) - a watchlistnel a cache-bol, mashol frissen."""
        self.ensure()
        if symbol in self.forecasts:
            return self.forecasts[symbol], self.signals.get(symbol, [])
        settings = load_settings()
        if enriched is None:
            df = store.load(symbol, settings)
            if df.empty:
                return None, []
            enriched = indicators.enrich(df, settings.structure.atr_period)
        # a jelzes csak lezart gyertyabol (a chart a mai, alakulo gyertyat is mutatja)
        enriched = sessions.completed_bars(enriched)
        calibration = Calibration.load(settings.calibration_path)
        forecast = analyse(symbol, enriched, settings, calibration)
        signals: list[entry.EntrySignal] = []
        if settings.entry.enabled:
            ctx = market.load_context(settings, refresh=False)
            earnings = market.EarningsCalendar(settings, allow_network=False)
            signals = monitor._evaluate_entries(symbol, enriched, forecast, settings, calibration,
                                                ctx, earnings)
        return forecast, signals

    def start_index_scan(self, alerts: bool = False) -> bool:
        """Indexszkenneles hatterszalon.

        `alerts=True` (index bekapcsolasakor es a zaras utani utemezett futasnal):
        ha a piac zarva van - tehat a napi gyertya lezart -, az ertesitesek is
        kimennek, ugyanugy, mint a monitorbol. Nyitvatartas alatt csak az eredmenyt
        frissiti; az ertesites zaras utan jon. A kezi "Szkenneles most" nem riaszt.
        """
        with self.lock:
            if self.job["state"] == "running":
                return False
            self.job = {"state": "running", "message": "Indexek atnezese (osszetetel, adatok, elemzes)...",
                        "started": datetime.now(timezone.utc).isoformat(), "finished": None}
        threading.Thread(target=self._index_scan, args=(alerts,), daemon=True).start()
        return True

    def _index_scan(self, alerts: bool = False) -> None:
        try:
            config.load_settings.cache_clear()
            settings = load_settings()
            if not (settings.scan.nasdaq100 or settings.scan.sp500):
                self._set_job("error", "Egy index sincs bekapcsolva (Beallitasok -> Atnezett papirok).")
                return
            send = alerts and not monitor.is_market_open()
            dispatcher = Dispatcher(settings) if send else None
            result = monitor.index_scan(settings, dispatcher=dispatcher, send_alerts=send)
            with self.lock:
                self.overview = None
            sent = sum(1 for x in result["signals"] if x["qualifies"] and not x["muted"])
            note = (f", {sent} belepesi jelzes - ertesites kikuldve" if send and sent
                    else " - ertesites zaras utan" if alerts and not send else "")
            self._set_job("done", f"Kesz: {result['analysed']} papir elemezve{note}.")
        except Exception as exc:
            log.exception("Indexszkenneles sikertelen")
            self._set_job("error", f"Hiba: {type(exc).__name__}: {exc}"[:300])

    # -- "Legjobb belepo most" (best.py) hatterszalon ---------------------
    def start_best(self) -> bool:
        with self.lock:
            if self.job["state"] == "running":
                return False
            self.job = {"state": "running", "message": "Legjobb belepo keresese...", "kind": "best",
                        "started": datetime.now(timezone.utc).isoformat(), "finished": None}
        threading.Thread(target=self._best, daemon=True).start()
        return True

    def _best(self) -> None:
        from . import best

        try:
            config.load_settings.cache_clear()
            config.load_watchlist.cache_clear()
            result = best.find_best(load_settings(), progress=lambda m: self._set_job("running", m))
            self.best = result
            parts = [f"{side}: {result[side]['best']['symbol']}" for side in ("long", "short") if result[side]["best"]]
            self._set_job("done", "Kesz - " + (", ".join(parts) if parts else "most nincs szuron atmeno belepo") + ".")
        except Exception as exc:
            log.exception("Legjobb belepo keresese sikertelen")
            self._set_job("error", f"Hiba: {type(exc).__name__}: {exc}"[:300])

    # -- frissites (halozat) hatterszalon ---------------------------------
    def start_refresh(self) -> bool:
        with self.lock:
            if self.job["state"] == "running":
                return False
            self.job = {"state": "running", "message": "Adatok letoltese...",
                        "started": datetime.now(timezone.utc).isoformat(), "finished": None}
        threading.Thread(target=self._refresh, daemon=True).start()
        return True

    def _refresh(self) -> None:
        try:
            config.load_settings.cache_clear()
            settings = load_settings()
            symbols = load_watchlist().symbols
            feed.update(symbols, settings)
            if settings.entry.regime_filter:
                self._set_job("running", f"Benchmark ({settings.entry.benchmark})...")
                feed.update([settings.entry.benchmark], settings, universe=True)
            own = holdings.HoldingStore(settings, read_only=True).symbols()
            extra = [s for s in own if s not in symbols]
            if extra:
                self._set_job("running", f"Sajat poziciok: {', '.join(extra)}...")
                feed.update(extra, settings)
            self._set_job("running", "Earnings datumok...")
            calendar = market.EarningsCalendar(settings, allow_network=True)
            for symbol in symbols + extra:
                calendar.next_date(symbol)
            self._set_job("running", "Elemzes...")
            with self.lock:
                self._compute()
                self.data_stamp = self._stamp()
            self._set_job("done", "Kesz.")
        except Exception as exc:  # a felulet ne alljon le egy halozati hiba miatt
            log.exception("Frissites sikertelen")
            self._set_job("error", f"Hiba: {type(exc).__name__}: {exc}"[:300])

    def _set_job(self, state: str, message: str) -> None:
        with self.lock:
            self.job["state"] = state
            self.job["message"] = message
            if state in ("done", "error"):
                self.job["finished"] = datetime.now(timezone.utc).isoformat()


def regime_payload(ctx: market.MarketContext, settings) -> dict:
    """A fejlec rezsim-jelzoje: mindket index (a papirok a sajat indexuket nezik)."""
    out = []
    for symbol in market.REGIME_INDEXES:
        frame = ctx.benches.get(symbol)
        reg = market.market_regime(frame, frame.index[-1]) if frame is not None and not frame.empty else None
        out.append({"symbol": "QQQ" if symbol == market.NASDAQ_INDEX else "SPX",
                    "name": market.INDEX_LABELS[symbol], "ok": reg.ok if reg else None,
                    "text": reg.text if reg else "nincs adat - futtass frissitest"})
    return {"enabled": settings.entry.regime_filter, "indexes": out,
            "ok": None if any(i["ok"] is None for i in out) else all(i["ok"] for i in out),
            "benchmark": " / ".join(i["symbol"] for i in out), "name": "",
            "text": " | ".join(f"{i['symbol']}: {i['text']}" for i in out)}


def pattern_stats(settings) -> dict:
    """A kalibracio (backtest) eredmenye alakzatonkent - az info-panelhez."""
    data = Calibration.load(settings.calibration_path).data.get("patterns", {})
    return {key: {"hit_rate": v.get("hit_rate"), "expectancy_r": v.get("expectancy_r"),
                  "samples": v.get("samples", 0)} for key, v in data.items()}


def index_payload(settings) -> dict:
    """A bekapcsolt indexek allapota + a legutobbi indexszkenneles eredmenye."""
    from . import indexes

    result = None
    if settings.index_scan_path.exists():
        try:
            result = json.loads(settings.index_scan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = None
    return {"members": indexes.IndexMembers(settings).status(), "result": result,
            "enabled": bool(settings.scan.nasdaq100 or settings.scan.sp500),
            "index_setups": settings.scan.index_setups}


def _signal_brief(s: entry.EntrySignal, cfg: EntrySettings) -> dict:
    d = s.to_dict()
    d["qualifies"] = s.qualifies(cfg)
    d["label"] = entry.KIND_LABELS.get(s.kind, s.kind)
    d["failed"] = [c.name for c in s.failed_checks]
    d["exit_lines"] = entry.exit_plan_lines(s.plan)[2:]
    return d


# ---------------------------------------------------------------------------
# Egy papir reszletei (chart adatokkal)
# ---------------------------------------------------------------------------
def symbol_detail(app: AppState, symbol: str) -> dict | None:
    app.ensure()
    settings = load_settings()
    df = store.load(symbol, settings)
    if df.empty:
        return None
    enriched = indicators.enrich(df, settings.structure.atr_period)
    view = enriched.iloc[-CHART_BARS:]

    candles = [
        {
            "t": idx.strftime("%Y-%m-%d"),
            "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume,
            "ema20": r.ema20, "ema50": r.ema50, "ema200": r.ema200,
        }
        for idx, r in view.iterrows()
    ]
    last = enriched.iloc[-1]
    forecast, signals = app.analysis(symbol, enriched)
    matches = forecast.matches
    first_date = view.index[0]
    shapes = [
        {
            "name": m.name, "direction": m.direction, "status": m.status, "quality": m.quality,
            "trigger": m.trigger_level, "target": m.target, "invalidation": m.invalidation,
            "start": m.start_date, "end": m.end_date, "trigger_date": m.trigger_date,
            "points": [(d.strftime("%Y-%m-%d"), p) for d, p in m.key_points if d >= first_date],
        }
        for m in matches
    ]
    tracker = positions.PositionTracker(settings, read_only=True)
    pos = [_position_dict(p) for p in tracker.positions.values() if p.symbol == symbol]

    return {
        "symbol": symbol,
        "candles": candles,
        "indicators": {
            "rsi": last.get("rsi"), "adx": last.get("adx"), "atr": last.get("atr"),
            "atr_pct": last.get("atr_pct"), "vol_ratio": last.get("vol_ratio"),
            "ema50_slope": last.get("ema50_slope"),
        },
        "forecast": {
            "label": forecast.label, "score": forecast.score,
            "components": [{"name": c.name, "score": c.score, "reason": c.reason} for c in forecast.components],
            "probability": forecast.probability_text,
        } if forecast else None,
        "levels": [lvl.to_dict() for lvl in (forecast.all_levels if forecast else [])],
        "patterns": shapes,
        "signals": [_signal_brief(s, settings.entry) for s in signals],
        "signal_texts": [
            {"title": entry.format_title(s), "body": entry.format_body(s)} for s in signals
        ],
        "positions": pos,
        "holdings": evaluate_holdings(app, symbol),
        "pattern_stats": pattern_stats(settings),
    }


def _position_dict(p: positions.TrackedPosition) -> dict:
    state = p.position_state()
    return {
        "id": p.id, "symbol": p.symbol, "pattern": p.pattern, "signal_date": p.signal_date,
        "status": p.status, "entry_date": p.entry_date, "entry_price": p.entry_price,
        "stop": state.stop if state else p.stop, "initial_stop": p.stop, "target": p.target,
        "partial_price": state.partial_price if state and state.uses_partial else None,
        "partial_done": bool(state and state.partial_done), "shares": p.shares,
        "result_r": p.result_r, "reason": p.reason, "closed_at": p.closed_at,
        "bars_held": state.bars_held if state else p.held, "horizon": p.horizon, "exit_mode": p.exit_mode,
        "result_pct": p.result_pct, "exit_pending": p.exit_pending,
        "signal_id": p.signal_id, "source": p.source, "broker_status": p.broker_status, "strategy": p.strategy,
        "signal_at": p.signal_at, "submitted_at": p.submitted_at, "entry_at": p.entry_at,
        **({"partial_price": p.broker_partial} if p.source == "alpaca" else {}),
    }


POSITIONS_SYNC_EVERY = 60.0      # mp: a Jelzett trade-ek oldal ennyinel regebbi paper allapotot frissit
_last_positions_sync = 0.0


def _sync_broker_if_stale(settings) -> str | None:
    """Ha van aktiv paper trade, az Alpaca friss allapota (legfeljebb percenkent). Hibaszoveg vagy None."""
    global _last_positions_sync
    if time.monotonic() - _last_positions_sync < POSITIONS_SYNC_EVERY:
        return None
    client = broker.AlpacaClient.from_env()
    if client is None or not broker.BrokerBook(settings, read_only=True).active():
        return None
    _last_positions_sync = time.monotonic()
    try:
        with BROKER_LOCK, broker.book_lock(settings, timeout=20):
            book = broker.BrokerBook(settings)
            broker.sync(settings, client, book, final_bar=False)
            book.save()
    except broker.BrokerError as exc:
        return str(exc)
    return None


def positions_payload() -> dict:
    """A jelzett trade-ek: ahol paper trade van, az Alpaca allapota (friss szinkron utan) az iranyado."""
    settings = load_settings()
    error = _sync_broker_if_stale(settings)
    tracker = positions.PositionTracker(settings, read_only=True)     # betolteskor az Alpaca szerint
    items = sorted(tracker.positions.values(), key=lambda p: p.signal_date, reverse=True)
    sid_of = {p.id: sid for sid, p in tracked_by_signal(tracker).items()}
    return {"positions": [_position_dict(p) | {"signal_id": sid_of.get(p.id, p.signal_id)} for p in items],
            "stats": tracker.closed_stats(), "broker_error": error,
            "by_strategy": {sid: tracker.closed_stats(sid) for sid in config.STRATEGY_IDS},
            "strategies": strategy_labels()}


# ---------------------------------------------------------------------------
# Kozos jelazonosito: Ertesitesek <-> Jelzett trade-ek <-> Jelzesnaplo
# ---------------------------------------------------------------------------
def resolve_signal_ids(keys: set[tuple[str, str, str]], tracker: positions.PositionTracker) -> dict:
    """(papir, jelzesnap, alakzat) -> jelazonosito a regi, azonosito nelkuli bejegyzesekhez:
    elobb a kovetett / paper trade-bol, aztan a jelzesnaplobol."""
    found = {}
    for p in tracker.positions.values():
        key = (p.symbol, p.signal_date, p.pattern)
        if p.signal_id and key in keys:
            found[key] = p.signal_id
    found.update(ledger.lookup_ids(keys - found.keys()))
    return found


def tracked_by_signal(tracker: positions.PositionTracker) -> dict[str, positions.TrackedPosition]:
    """jelazonosito -> kovetett trade (a regi, azonosito nelkuli trade-ek a naplo alapjan)."""
    out = {p.signal_id: p for p in tracker.positions.values() if p.signal_id}
    legacy = {(p.symbol, p.signal_date, p.pattern): p for p in tracker.positions.values() if not p.signal_id}
    for key, sid in ledger.lookup_ids(set(legacy)).items():
        out.setdefault(sid, legacy[key])
    return out


def strategy_labels() -> dict:
    """A felulet felirataihoz: azonosito -> nev, es melyik van a paper fiokhoz kotve."""
    settings = load_settings()
    return {"labels": dict(config.STRATEGY_LABELS), "paper": settings.strategies.paper,
            "auto_trade": settings.broker.auto_trade}


STRATEGY_EVENTS = ("entry_", "pos_", "broker_")


def alert_strategy(raw: dict) -> str | None:
    """Melyik strategia kuldte az ertesitest. A strategia-mezo elotti ertesitesek a kitoresei voltak;
    az alakzat- (short) es a sajat pozicios ertesitesek nem strategiahoz tartoznak."""
    if raw.get("strategy"):
        return raw["strategy"]
    info = raw.get("entry") or {}
    if info.get("strategy"):
        return info["strategy"]
    return "breakout" if str(raw.get("event") or "").startswith(STRATEGY_EVENTS) else None


def _trade_brief(p: positions.TrackedPosition | None) -> dict | None:
    if p is None:
        return None
    return {"status": p.status, "source": p.source, "broker_status": p.broker_status,
            "result_r": p.result_r, "reason": p.reason, "strategy": p.strategy}


# ---------------------------------------------------------------------------
# Sajat (kezzel felvett) poziciok
# ---------------------------------------------------------------------------
HOLDINGS_LOCK = threading.Lock()


def evaluate_holdings(app: AppState, symbol: str | None = None) -> list[dict]:
    """A nyitott sajat poziciok kiertekelese az utolso lezart gyertyan."""
    app.ensure()
    settings = load_settings()
    own = holdings.HoldingStore(settings, read_only=True)
    # halozat nelkul: az earnings datumokat a Frissites gomb tolti le
    out = [
        {**asdict(r), "missing": True} if isinstance(r, holdings.Holding) else r.to_dict()
        for r in holdings.evaluate_open(settings, own, app.forecasts, symbol=symbol)
    ]
    rank = {"bad": 0, "warn": 1, "ok": 2}
    out.sort(key=lambda d: (rank.get(d.get("verdict"), 3), d["symbol"]))
    return out


def holdings_payload(app: AppState) -> dict:
    settings = load_settings()
    own = holdings.HoldingStore(settings, read_only=True)
    items = evaluate_holdings(app)
    closed = sorted((h for h in own.items.values() if not h.is_open),
                    key=lambda h: h.closed_at or "", reverse=True)
    priced = [d for d in items if not d.get("missing")]
    return {
        "holdings": items,
        "closed": [{**asdict(h), "pnl": h.realised_pnl,
                    "pnl_pct": (h.close_price / h.entry_price - 1) * 100} for h in closed],
        "totals": {
            "value": sum(d["value"] for d in priced),
            "pnl": sum(d["pnl"] for d in priced),
            "cost": sum(d["entry_price"] * d["shares"] for d in priced),
            "realised": sum(h.realised_pnl or 0 for h in closed),
        },
    }


def _num(body: dict, key: str):
    value = body.get(key)
    return None if value in (None, "") else float(value)


def holding_action(app: AppState, action: str, body: dict) -> dict:
    settings = load_settings()
    with HOLDINGS_LOCK:
        own = holdings.HoldingStore(settings)
        if action == "add":
            symbol = str(body.get("symbol", "")).strip().upper()
            if not SYMBOL_RE.match(symbol):
                raise ValueError("Ervenytelen ticker")
            if store.load(symbol, settings).empty:
                # meg nincs adatunk erre a papirra: most toltjuk le
                if feed.update([symbol], settings).get(symbol) is None:
                    raise ValueError(f"Nem talalhato arfolyam-adat: {symbol}")
                market.EarningsCalendar(settings, allow_network=True).next_date(symbol)
            h = own.add(symbol, body.get("entry_price"), body.get("shares"),
                        body.get("entry_date") or None, _num(body, "stop"), _num(body, "target"),
                        body.get("note", ""))
        elif action == "update":
            changes = {k: body[k] for k in ("entry_price", "shares", "entry_date", "note") if k in body}
            for key in ("stop", "target"):
                if key in body:
                    changes[key] = _num(body, key)
            h = own.update(str(body.get("id")), **changes)
        elif action == "close":
            h = own.close(str(body.get("id")), _num(body, "price"), body.get("date") or None)
        elif action == "delete":
            own.remove(str(body.get("id")))
            h = None
        else:
            raise ValueError("Ismeretlen muvelet")
        own.save()
    return {"ok": True, "id": h.id if h else None, **holdings_payload(app)}


# ---------------------------------------------------------------------------
# Alpaca paper kereskedes
# ---------------------------------------------------------------------------
BROKER_LOCK = threading.Lock()
CREDENTIAL_RE = re.compile(r"^[A-Za-z0-9/+=_\-]{8,128}$")
ACCOUNT_FIELDS = ("status", "currency", "equity", "last_equity", "cash", "buying_power", "long_market_value",
                  "portfolio_value", "trading_blocked", "pattern_day_trader", "daytrade_count")


def _client() -> broker.AlpacaClient:
    client = broker.AlpacaClient.from_env()
    if client is None:
        raise ValueError("Nincs beallitva Alpaca kulcs - add meg a Beallitasok fulon.")
    return client


def _trade_dict(t: broker.BrokerTrade, settings=None) -> dict:
    out = {**asdict(t), "is_active": t.is_active, "risk_amount": t.risk_amount}
    if t.status == "submitted":
        out["waiting"] = _waiting_info(t, settings)
    return out


def _waiting_info(t: broker.BrokerTrade, settings=None) -> dict:
    """Miert var meg a belepo: a limit a jelzes napi zaro alatt van, es csak a jelzes utani kereskedesi
    napon ervenyes (kitoresnel csak a nyitaskor). Az utolso ismert ar a cache-bol (zaras utan friss)."""
    entry_day = pd.Timestamp(t.signal_date) + pd.offsets.BDay(1)      # unnepnapot nem ismer
    info = {"entry_day": f"{entry_day:%Y-%m-%d}", "limit_pct": (t.params or {}).get("limit_pct"),
            "last_price": None, "last_date": None, "gap_pct": None}
    try:
        bars = store.load(t.symbol, settings)
        if bars.empty:
            bars = store.load(t.symbol, settings, universe=True)
    except Exception:
        bars = pd.DataFrame()
    if not bars.empty and t.entry_limit:
        last = float(bars["close"].iloc[-1])
        info.update(last_price=last, last_date=f"{bars.index[-1]:%Y-%m-%d}",
                    gap_pct=(last / t.entry_limit - 1) * 100)
    return info


def broker_payload(app: AppState) -> dict:
    settings = load_settings()
    creds = broker.describe_credentials()
    book = broker.BrokerBook(settings, read_only=True)
    trades = sorted(book.trades.values(), key=lambda t: t.submitted_at, reverse=True)
    app.ensure()
    candidates = []
    for sigs in app.signals.values():
        for s in sigs:
            if s.kind != "setup" and s.qualifies(settings.entry):
                candidates.append({**_signal_brief(s, settings.entry),
                                   "sent": broker.trade_id(s) in book.trades})
    # az indexszkenneles belepesi jelzesei is bekuldhetok
    seen = {c["symbol"] for c in candidates}
    for d in (index_payload(settings)["result"] or {}).get("signals", []):
        if d["kind"] != "setup" and d["qualifies"] and d["symbol"] not in seen:
            candidates.append({**d, "index": True,
                               "sent": f"{d['symbol']}|{d['as_of']}|{d['pattern']}" in book.trades})
    out = {
        "credentials": creds,
        "settings": settings.broker.model_dump(),
        "exit_mode": settings.entry.exit_mode,
        "trades": [_trade_dict(t, settings) for t in trades],
        "stats": book.stats(),
        "candidates": candidates,
        "account": None, "clock": None, "positions": [], "orders": [], "error": None,
        "history": None, "pnl": None, "capital": None,
        "strategies": strategies_payload(),
    }
    if not creds["configured"]:
        return out
    try:
        client = _client()
        account = client.account()
        out["account"] = {k: account.get(k) for k in ACCOUNT_FIELDS}
        out["clock"] = client.clock()
        out["positions"] = [
            {k: p.get(k) for k in ("symbol", "qty", "avg_entry_price", "current_price", "market_value",
                                   "unrealized_pl", "unrealized_plpc")}
            for p in client.positions()
        ]
        out["history"], out["pnl"] = _broker_pnl(client, account, out["positions"], trades)
        by_symbol = {p["symbol"]: p for p in out["positions"]}
        for t in out["trades"]:
            pos = by_symbol.get(t["symbol"]) if t["status"] == "open" else None
            t["unrealized_pl"] = _float(pos.get("unrealized_pl")) if pos else None
            t["current_price"] = _float(pos.get("current_price")) if pos else None
        orders = []
        open_orders = client.orders("open")
        for o in open_orders:
            for item in [o] + list(o.get("legs") or []):
                orders.append({k: item.get(k) for k in ("id", "symbol", "side", "type", "qty", "limit_price",
                                                        "stop_price", "status", "submitted_at", "order_class")})
        out["orders"] = orders
        out["capital"] = capital_payload(account, out["positions"], open_orders)
    except broker.BrokerError as exc:
        out["error"] = str(exc)
    return out


def capital_payload(account: dict, positions: list[dict], open_orders: list[dict]) -> dict:
    """Mennyi tokét kot le a rendszer: pozicionkent, fuggo vetelben, es mennyi a szabad keret
    (margin nelkul - a beküldés is ehhez igazodik, lasd broker.free_cash)."""
    equity = _float(account.get("equity")) or 0.0
    held = [{"symbol": p["symbol"], "value": _float(p.get("market_value")) or 0.0} for p in positions]
    invested = sum(h["value"] for h in held)
    pending = broker.pending_buys(open_orders)
    share = (lambda v: v / equity * 100 if equity else None)
    return {
        "equity": equity,
        "cash": _float(account.get("cash")) or 0.0,
        "invested": invested, "invested_pct": share(invested),
        "pending": pending, "pending_pct": share(pending),
        "free": broker.free_cash(account, open_orders),
        "free_pct": share(broker.free_cash(account, open_orders)),
        "buying_power": _float(account.get("buying_power")),
        "positions": [{**h, "pct": share(h["value"])} for h in sorted(held, key=lambda h: -h["value"])],
    }


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _broker_pnl(client, account: dict, positions: list[dict], trades: list) -> tuple[dict | None, dict]:
    """A paper szamla eddigi eredmenye: a szamla egeszen (equity-tortenet) es a
    rendszer sajat trade-jein (realizalt + nyitott)."""
    equity = _float(account.get("equity")) or 0.0
    history = None
    try:
        raw = client.portfolio_history("all", "1D")
        points = [(t, _float(e)) for t, e in zip(raw.get("timestamp") or [], raw.get("equity") or [])]
        points = [(t, e) for t, e in points if e is not None and e > 0]
        if points:
            # az Alpaca napi pontjai New York-i ido szerinti kereskedesi napok (UTC-ben egy nappal
            # elcsusznanak: a hetfoi zaro keddnek latszana)
            history = {"t": [pd.Timestamp(t, unit="s", tz="UTC").tz_convert(monitor.MARKET_TZ).strftime("%Y-%m-%d")
                             for t, _ in points],
                       "equity": [e for _, e in points],
                       "base": _float(raw.get("base_value")) or points[0][1], "live": False}
            # a napi tortenet csak a lezart napokat adja: a mai, elo equity a vegere (ma vett papir is latszik)
            today = sessions.now_ny().strftime("%Y-%m-%d")
            if equity > 0 and history["t"][-1] < today:
                history["t"].append(today)
                history["equity"].append(equity)
                history["live"] = True
    except broker.BrokerError as exc:
        log.info("Portfolio history nem erheto el: %s", exc)
    base = history["base"] if history else None
    active = {t.symbol for t in trades if t.status == "open"}
    unrealised = sum(_float(p.get("unrealized_pl")) or 0.0 for p in positions if p.get("symbol") in active)
    realised = sum(t.realised_pnl or 0.0 for t in trades if t.status == "closed")
    return history, {
        "equity": equity,
        "base": base,
        "account_total": equity - base if base else None,
        "account_total_pct": (equity / base - 1) * 100 if base else None,
        "today": equity - (_float(account.get("last_equity")) or equity),
        "system_realised": realised,
        "system_unrealised": unrealised,
        "system_total": realised + unrealised,
        "closed_trades": sum(1 for t in trades if t.status == "closed"),
        "open_trades": len(active),
    }


def save_credentials(body: dict) -> dict:
    """Kapcsolodasi adatok mentese a .env-be - CSAK sikeres kapcsolat-teszt utan."""
    endpoint = broker.normalise_endpoint(body.get("endpoint"))
    _, current_key, current_secret = config.alpaca_credentials()
    key = str(body.get("key") or "").strip() or current_key
    secret = str(body.get("secret") or "").strip() or current_secret
    if not key or not secret:
        raise ValueError("Add meg az API Key-t es a Secret-et.")
    if not CREDENTIAL_RE.match(key) or not CREDENTIAL_RE.match(secret):
        raise ValueError("A Key / Secret formatuma ervenytelen (szokoz vagy tiltott karakter?).")
    account = broker.AlpacaClient(endpoint, key, secret).account()     # kapcsolat-teszt
    config.save_env({"ALPACA_ENDPOINT": endpoint, "ALPACA_KEY_ID": key, "ALPACA_SECRET_KEY": secret})
    return {"ok": True, "credentials": broker.describe_credentials(), "account_status": account.get("status")}


def broker_action(app: AppState, action: str, body: dict) -> dict:
    if action == "credentials":
        return save_credentials(body)
    if action == "forget":
        config.save_env({k: "" for k in config.ALPACA_KEYS})
        return {"ok": True, "credentials": broker.describe_credentials()}
    if action == "test":
        account = _client().account()
        return {"ok": True, "account_status": account.get("status"), "equity": account.get("equity")}

    settings = load_settings()
    client = _client()
    submitted = None
    with BROKER_LOCK, broker.book_lock(settings):
        book = broker.BrokerBook(settings)
        message = ""
        if action == "submit":
            symbol = str(body.get("symbol", "")).upper()
            app.ensure()
            forecast, signals = app.analysis(symbol)
            signal = next((s for s in signals
                           if s.kind == body.get("kind") and s.pattern.name == body.get("pattern")), None)
            if signal is None:
                raise ValueError("Ez a jelzes mar nem aktualis - frissitsd az oldalt.")
            trade = broker.submit(signal, settings, client, book)
            submitted = (signal, forecast, trade)
            message = f"{trade.symbol}: bekuldve, {trade.shares} db, limit {trade.entry_limit:.2f} $"
        elif action == "sync":
            final = not bool(client.clock().get("is_open"))
            events = broker.sync(settings, client, book, final_bar=final)
            message = f"Szinkronizalva - {len(events)} uj esemeny." if events else "Szinkronizalva - nincs valtozas."
        elif action == "close":
            broker.close_trade(book.get(str(body.get("id"))), client)
            message = "Zaras / visszavonas elkuldve."
        else:
            raise ValueError("Ismeretlen muvelet")
        book.save()
    if submitted:
        record_manual_submit(settings, *submitted)
    return {"ok": True, "message": message, **broker_payload(app)}


def record_manual_submit(settings, signal: entry.EntrySignal, forecast, trade: broker.BrokerTrade) -> None:
    """A kezi bekuldes is a kozos eletutra kerul, mint a monitore: jelzesnaplo (kezzel bekuldve)
    + ertesites (Paper: bekuldve). A Jelzett trade-ek a paper trade-bol magatol latja.
    A trade mar bent van az Alpacan - egy itteni hiba csak naplozodik."""
    mode = "watchlist" if signal.symbol in load_watchlist().symbols else "index"
    try:
        raw = store.load(signal.symbol, settings)
        if raw.empty:
            raw = store.load(signal.symbol, settings, universe=True)
        ledger.Ledger(settings, Calibration.load(settings.calibration_path), source="web").record(
            signal, "manual", "kezzel bekuldve a Paper trading oldalrol", raw=sessions.completed_bars(raw),
            benchmark=market.load_benchmark(settings, refresh=False), mode=mode, snapshot=True)
    except Exception:
        log.exception("A kezi bekuldes naplozasa nem sikerult: %s", signal.symbol)
    try:
        title, text = broker.format_event(trade, "broker_submitted", "Kezzel bekuldve a feluletrol.")
        view = dataclasses.replace(forecast, pattern=signal.pattern) if forecast is not None else None
        Dispatcher(settings).dispatch(Alert(symbol=trade.symbol, title=title, body=text, forecast=view,
                                            event="broker_submitted",
                                            extra={"broker_trade": trade.id, "signal_id": trade.signal_id},
                                            strategy=trade.strategy))
    except Exception:
        log.exception("A kezi bekuldes ertesitese nem sikerult: %s", signal.symbol)


# ---------------------------------------------------------------------------
# Strategia-labor (lab.py) - a felulet csak a kiserletlistat irja, a szamolast a hatterfolyamat vegzi
# ---------------------------------------------------------------------------
LAB_ID_RE = re.compile(r"^[0-9a-f]{10}$")
LAB_FIELD_LABELS = {
    "confirm_breakout": "Megerősítés (a kitörés utáni napon)", "alert_retests": "Visszateszt-belépés is",
    "min_stop_atr": "Stop távolság (ATR)", "exit_mode": "Kilépési mód", "partial_r": "Fele eladása (R)",
    "trail_atr": "Követett stop (ATR)", "min_risk_reward": "Min. hozam/kockázat", "rsi_min": "RSI min.",
    "rsi_max": "RSI max.", "max_extension_atr": "Max. távolság a triggertől (ATR)", "max_stop_pct": "Max. stop %",
    "min_score": "Min. pontszám", "min_breakout_volume": "Min. kitörési volumen (x átlag)",
    "regime_filter": "Piaci rezsim (index > SMA200)", "earnings_blackout_days": "Earnings-tilalom (nap)",
    "min_high52_ratio": "Min. 52 hetes csúcs arány (0 = ki)", "min_momentum_pct": "Min. 12–1 momentum % (üres = ki)",
    "min_rel_strength": "Min. relatív erő, pp (üres = ki)", "max_contraction": "Max. összehúzódás (0 = ki)",
    "boost_score": "Nagyobb méret e pontszámtól (0 = ki)", "boost_risk_mult": "Kockázat-szorzó (pl. 1,5)",
}


def _lab_diff(exp: dict, live: EntrySettings) -> list[dict]:
    from . import lab

    if exp.get("live"):
        return []
    current = live.model_dump()
    return [{"field": k, "label": LAB_FIELD_LABELS.get(k, k), "value": exp["entry"].get(k), "live": current.get(k)}
            for k in lab.EDITABLE if exp["entry"].get(k) != current.get(k)]


def lab_payload() -> dict:
    from . import entry_research as er
    from . import lab, lab_ml

    settings = load_settings()
    data = lab.load_store()
    live = next((e for e in data["experiments"] if e.get("live")), None)
    base = (live or {}).get("summary")
    experiments = []
    for exp in data["experiments"]:
        item = lab.public_summary(exp)
        item["diff"] = _lab_diff(exp, settings.entry)
        s = exp.get("summary")
        item["robust"] = bool(s and base and not exp.get("live") and s["all"]["n"]
                              and er.robust_winners([{"stats": s}], base))
        experiments.append(item)
    universes = {}
    for name, label in lab.UNIVERSES.items():
        try:
            universes[name] = {"label": label, "count": len(lab.universe_symbols(name, settings))}
        except Exception:
            universes[name] = {"label": label, "count": 0}
    return {
        "status": lab.read_status(),
        "experiments": experiments,
        "created_total": data.get("created_total", 0),
        "max_experiments": lab.MAX_EXPERIMENTS,
        "ml": {**(data.get("ml") or {}), "report": lab_ml.load_report(), "shadow": _shadow_report(settings)},
        "universes": universes,
        "fields": [{"field": k, "label": LAB_FIELD_LABELS.get(k, k), "type": t.__name__, "min": lo, "max": hi}
                   for k, (t, lo, hi) in lab.EDITABLE.items()],
        "defaults": {k: v for k, v in settings.entry.model_dump().items() if k in lab.EDITABLE},
        "holdout_start": f"{er.HOLDOUT_START:%Y-%m-%d}",
        "holdout_openings": er.holdout_openings(),
    }


def _shadow_report(settings) -> dict | None:
    from . import shadow

    try:
        return shadow.report(settings)
    except Exception:
        log.exception("Az arnyek-uzem allapota nem olvashato")
        return None


def lab_action(action: str, body: dict) -> dict:
    from . import lab

    exp_id = str(body.get("id") or "")
    if action in ("delete", "rerun", "holdout") and not LAB_ID_RE.match(exp_id):
        raise ValueError("Ervenytelen kiserlet-azonosito")
    if action == "experiment":
        cfg = lab.validate_entry(body.get("values") or {}, load_settings().entry)
        lab.add_experiment(str(body.get("name") or ""), str(body.get("universe") or "indexes"), cfg)
    elif action == "delete":
        def drop(data: dict) -> None:
            exp = next((e for e in data["experiments"] if e["id"] == exp_id), None)
            if exp is None:
                raise ValueError("Nincs ilyen kiserlet.")
            if exp.get("live"):
                raise ValueError("Az ELO beallitas kiserlete nem torolheto.")
            data["experiments"].remove(exp)
        lab.update_store(drop)
        lab.prune()
    elif action == "rerun":
        lab.update_store(lambda d: [e.update(status="queued") for e in d["experiments"] if e["id"] == exp_id])
    elif action == "holdout":
        if body.get("confirm") is not True:
            raise ValueError("A holdout megnyitasat meg kell erositeni.")
        lab.open_holdout(exp_id)
    elif action == "ml":
        lab.update_store(lambda d: d["ml"].update(pending=True, enabled=True))
    else:
        raise ValueError("Ismeretlen muvelet")
    return lab_payload()


def signals_payload(limit: int = 200) -> dict:
    """A kikuldott ertesitesek naploja (signals.jsonl), legfrissebb elol."""
    settings = load_settings()
    path = settings.signals_path
    records = []
    unlinked: list[tuple[dict, tuple[str, str, str]]] = []
    frames: dict[str, pd.DataFrame] = {}
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        for line in reversed(lines):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            symbol = str(raw.get("symbol") or "").upper()
            if symbol and symbol not in frames:
                df = store.load(symbol, settings)
                frames[symbol] = indicators.enrich(df, settings.structure.atr_period) if len(df) > 30 else df
            entry_info = raw.get("entry") or {}
            records.append({
                "timestamp": raw.get("timestamp"), "event": raw.get("event"),
                "symbol": symbol, "title": raw.get("title"),
                "price": raw.get("price"),
                "score": entry_info.get("score"),
                "shares": (entry_info.get("plan") or {}).get("shares"),
                "signal_id": entry_info.get("signal_id"),
                "strategy": alert_strategy(raw),
                **signal_review.review(raw, frames.get(symbol), settings),
            })
            key = None if records[-1]["signal_id"] else ledger.alert_key(raw)
            if key:
                unlinked.append((records[-1], key))
    if unlinked:
        tracker = positions.PositionTracker(settings, read_only=True)
        ids = resolve_signal_ids({key for _, key in unlinked}, tracker)
        for rec, key in unlinked:
            rec["signal_id"] = ids.get(key)
    return {"records": records, "summary": signal_review.summary(records), "strategies": strategy_labels()}


# ---------------------------------------------------------------------------
# Beallitasok olvasasa / irasa - a settings.yaml kommentjei megmaradnak
# ---------------------------------------------------------------------------
def settings_payload() -> dict:
    config.load_settings.cache_clear()
    settings = load_settings()
    values = {}
    for key, (section, _) in EDITABLE.items():
        values[key] = getattr(getattr(settings, section), key)
    return {"values": values, "watchlist": load_watchlist().symbols, "discord": discord_status()}


# ---------------------------------------------------------------------------
# Discord webhook a feluletrol - az URL maga a jelszo: csak a titokfajlba kerul, a felulet sosem kapja vissza
# ---------------------------------------------------------------------------
def discord_status() -> dict:
    url = config.discord_webhook_url() or ""
    from .alerts.discord import is_discord_url

    return {"configured": bool(url), "valid": is_discord_url(url),
            "hint": f"…/{url.rstrip('/').rsplit('/', 2)[-2]}/…{url[-4:]}" if is_discord_url(url) else "",
            "enabled": load_settings().alerts.discord}


def _discord_test_message(channel) -> bool:
    from .alerts import Alert
    from .i18n import translate

    lang = load_settings().alerts.language
    return channel.send(Alert(symbol="STOCKWATCH", title=translate("StockWatch: proba uzenet a feluletrol", lang),
                              body=translate("A Discord webhook mukodik - ide erkeznek az ertesitesek.", lang),
                              forecast=None, event="heartbeat"))


def discord_action(action: str, body: dict) -> dict:
    from .alerts.discord import DiscordChannel, is_discord_url

    settings = load_settings()
    if action == "save":
        url = str(body.get("url") or "").strip()
        if not is_discord_url(url):
            raise ValueError("Ez nem Discord webhook cim (https://discord.com/api/webhooks/<szam>/<token>).")
        channel = DiscordChannel(settings, url=url)
        name = channel.test()                       # GET a webhookra - uzenetet nem kuld
        if not name:
            raise ValueError("A Discord nem fogadta el a webhookot (torolve lett, vagy elirtad?).")
        config.save_env({"DISCORD_WEBHOOK_URL": url})
        sent = _discord_test_message(channel) if body.get("test") else None
        return {"ok": True, "name": name, "sent": sent, "discord": discord_status()}
    if action == "test":
        channel = DiscordChannel(settings)
        if not channel.enabled:
            raise ValueError("Nincs beallitva Discord webhook.")
        if not _discord_test_message(channel):
            raise ValueError("A probauzenet nem ment ki (lasd a naplot).")
        return {"ok": True, "discord": discord_status()}
    if action == "forget":
        config.save_env({"DISCORD_WEBHOOK_URL": ""})
        return {"ok": True, "discord": discord_status()}
    raise ValueError(f"Ismeretlen muvelet: {action}")


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value)) if abs(value) >= 100 else f"{value:.1f}"
    return str(value)


def update_settings(changes: dict) -> dict:
    """Soronkenti csere a settings.yaml megfelelo szekciojaban.

    Szandekosan nem irjuk ujra az egesz YAML-t: a kommentek dokumentaljak a
    beallitasokat, es azok elvesznenek.
    """
    parsed: dict[str, tuple[str, object]] = {}
    for key, raw in changes.items():
        if key not in EDITABLE:
            raise ValueError(f"Nem allithato kulcs: {key}")
        section, kind = EDITABLE[key]
        if kind is bool:
            value = bool(raw)
        elif kind is int:
            value = int(raw)
        elif kind is float:
            value = float(raw)
        else:
            value = str(raw)
        parsed[key] = (section, value)

    # validalas a pydantic modellel, MIELOTT barmit irnank
    current = load_settings()
    sections = {name: getattr(current, name).model_dump() for name in ("entry", "alerts", "broker", "scan", "digest")}
    for key, (section, value) in parsed.items():
        sections[section][key] = value
    entry_values = sections["entry"]
    EntrySettings(**entry_values)
    config.AlertSettings(**sections["alerts"])
    config.BrokerSettings(**sections["broker"])
    config.ScanSettings(**sections["scan"])
    config.DigestSettings(**sections["digest"])
    if not 1 <= sections["broker"]["max_open_trades"] <= 50:
        raise ValueError("A max. nyitott paper trade 1 es 50 kozott legyen.")
    if not 0 < entry_values["risk_per_trade_pct"] <= 5:
        raise ValueError("A kockazat / trade 0 es 5% kozott legyen.")
    if entry_values["account_size"] <= 0:
        raise ValueError("A szamlameret legyen pozitiv.")

    path = CONFIG_DIR / "settings.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, (section, value) in parsed.items():
        lines = _set_yaml_key(lines, section, key, _yaml_value(value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config.load_settings.cache_clear()
    return settings_payload()


# ---------------------------------------------------------------------------
# Jelzesnaplo, eletut, visszajatszas
# ---------------------------------------------------------------------------
SIGNAL_ID_RE = re.compile(r"^sig-[0-9a-f]{12}$")


def ledger_payload(limit: int = 300, decision: str | None = None, symbol: str | None = None,
                   strategy: str | None = None) -> dict:
    rows, counts = [], {"alerted": 0, "manual": 0, "skipped": 0, "rejected": 0}
    # mi lett a jelzesbol: a Jelzett trade-ek oldallal azonos allapot (Alpaca / virtualis)
    trades = tracked_by_signal(positions.PositionTracker(load_settings(), read_only=True))
    for rec in ledger.iter_records():
        if strategy and (rec.get("strategy") or "breakout") != strategy:
            continue
        counts[rec.get("decision", "")] = counts.get(rec.get("decision", ""), 0) + 1
        if decision and rec.get("decision") != decision:
            continue
        if symbol and rec.get("symbol") != symbol:
            continue
        if len(rows) < limit:
            rows.append({k: rec.get(k) for k in ("signal_id", "recorded_at", "as_of", "symbol", "kind", "decision",
                                                  "reason", "score", "price", "mode", "snapshot")}
                        | {"pattern": (rec.get("pattern") or {}).get("name"),
                           "failed": [c["name"] for c in rec.get("checks") or [] if c.get("passed") is False],
                           "config": (rec.get("versions") or {}).get("config"),
                           "strategy": rec.get("strategy") or "breakout",
                           "trade": _trade_brief(trades.get(rec.get("signal_id")))})
    return {"rows": rows, "counts": counts, "changes": ledger.config_changes(50),
            "code": ledger.code_version(), "strategies": strategy_labels()}


# ---------------------------------------------------------------------------
# Strategiak: aktiv (paper fiokhoz kotott), kutatasi, homokozo
# ---------------------------------------------------------------------------
BREAKOUT_SUMMARY = ("min_score", "confirm_breakout", "exit_mode", "partial_r", "min_stop_atr", "regime_filter",
                    "earnings_blackout_days")


# ---------------------------------------------------------------------------
# Backteszt oldal (egy papir teljes tortenete) - docs/BACKTEST-PAGE-DESIGN.md
# ---------------------------------------------------------------------------
_BT_JOBS = None


_DIGEST_JOBS = None


def digest_jobs():
    global _DIGEST_JOBS
    if _DIGEST_JOBS is None:
        from .backtest_page.jobs import Jobs

        _DIGEST_JOBS = Jobs()
    return _DIGEST_JOBS


def digest_payload(day: str | None = None) -> dict:
    """Reggeli osszefoglalo: a kert (alapbol a legutobbi) nap + az archivum listaja + a kapcsolo."""
    from .digest import store as digest_store

    settings = load_settings()
    days = digest_store.days()
    record = digest_store.load(day) if day else (digest_store.load(days[0]) if days else None)
    keep = ("for_day", "as_of", "created_at", "first", "previous", "symbols_count", "market", "lines", "selected",
            "delivered")
    return {"days": days, "record": {k: record.get(k) for k in keep} if record else None,
            "settings": {"send_discord": settings.digest.send_discord, "time": settings.digest.time,
                         "enabled": settings.digest.enabled, "discord": discord_status()["configured"]},
            "job": digest_jobs().snapshot()}


def digest_action(action: str) -> dict:
    if action != "run":
        raise ValueError("Ismeretlen muvelet")

    def work(progress):
        from .digest import service as digest

        progress("Pillanatkep es valtozasok...")
        record = digest.build(load_settings())
        return {"message": f"Kesz: {record['for_day']}"}

    if not digest_jobs().start("digest", "", work):
        raise ValueError("Mar fut egy osszefoglalo-keszites.")
    return digest_payload()


_EVENT_JOBS = None


def event_jobs():
    global _EVENT_JOBS
    if _EVENT_JOBS is None:
        from .backtest_page.jobs import Jobs

        _EVENT_JOBS = Jobs()
    return _EVENT_JOBS


def events_payload() -> dict:
    """Esemenytanulmanyok: tipusok, mentett tanulmanyok, a futo feladat es a munkasor, a forrasadat allapota."""
    from . import labqueue
    from .events import catalog
    from .events import service as ev_service
    from .events import store as ev_store
    from .events.sources import form4, index_changes

    return {"types": {k: {"label": v["label"], "params": v["params"]} for k, v in catalog.TYPES.items()},
            "strategies": list(catalog.STRATEGIES), "universes": ev_service.UNIVERSE_LABELS,
            "sectors": sorted(set(index_changes.load_sectors().values())),
            "studies": ev_store.listing(), "job": event_jobs().snapshot(),
            "queue": labqueue.jobs("admin", "event_study")[:10],
            "sources": {"form4": form4.coverage(), "index_changes": len(index_changes.load_changes())},
            "defaults": ev_service.StudySpec(type="earnings").model_dump(mode="json")}


def events_action(action: str, body: dict) -> dict:
    from .events import service as ev_service
    from .events import store as ev_store

    if action == "study":
        spec = ev_service.StudySpec(**(body.get("spec") or {}))
        if spec.universe in ev_service.INLINE_UNIVERSES:
            def work(progress):
                record = ev_service.run(spec, load_settings(), progress=progress)
                return {"message": f"Kesz: {record['result']['n']} esemeny", "run_id": record["id"]}

            if not event_jobs().start("event_study", spec.symbol or spec.universe, work):
                raise ValueError("Mar fut egy esemenytanulmany.")
            return events_payload() | {"message": "A tanulmany fut."}
        job = ev_service.submit(spec)
        return events_payload() | {"message": "A labor-szolgaltatas sorba allitotta (nagy univerzum).", "queued": job["id"]}
    if action == "delete":
        ev_store.delete(str(body.get("id") or ""))
        return events_payload() | {"message": "Torolve."}
    raise ValueError("Ismeretlen muvelet")


def agent_payload() -> dict:
    """Kutatougynok: briefek, futasok, a munkasor, es az urlap alapertekei / a keresheto ter."""
    from . import labqueue
    from .research_agent import space as agent_space
    from .research_agent import store as agent_store
    from .research_agent.brief import ENGINE_LABELS, UNIVERSE_LABELS, Brief

    dims = {"pullback": agent_space.PULLBACK_DIMS, "etf": agent_space.ETF_DIMS,
            "breakout": agent_space.BREAKOUT_DIMS}
    return {"briefs": agent_store.list_briefs(), "runs": agent_store.runs()[:30],
            "jobs": labqueue.jobs("admin", "agent")[:20], "defaults": Brief(name="Új kutatási cél").model_dump(),
            "engines": ENGINE_LABELS, "universes": UNIVERSE_LABELS,
            "fields": {k: [{"name": d.name, "label": agent_space.FIELD_LABELS.get(d.name, d.name),
                            "values": list(d.values)} for d in v] for k, v in dims.items()}}


def agent_run_payload(run_id: str) -> dict:
    from .research_agent import explain
    from .research_agent import store as agent_store

    status = agent_store.status(run_id)
    if status is None:
        raise ValueError("Nincs ilyen futas.")
    log_items = agent_store.read_log(run_id)
    timeline = [ln.to_dict() for ln in (explain.log_line(rec) for rec in log_items) if ln is not None]
    return {"status": status, "report": agent_store.load_report(run_id), "timeline": timeline[-400:],
            "log_count": len(log_items)}


def agent_action(action: str, body: dict) -> dict:
    from .research_agent import service as agent
    from .research_agent import store as agent_store
    from .research_agent.brief import Brief

    if action == "save":
        brief = Brief(**(body.get("brief") or {}))
        if body.get("replace") and body["replace"] != brief.id:
            agent_store.delete_brief(str(body["replace"]))
        agent_store.save_brief(brief)
        return agent_payload() | {"message": f"Mentve: {brief.name}", "brief_id": brief.id}
    if action == "delete":
        agent_store.delete_brief(str(body.get("brief_id") or ""))
        return agent_payload() | {"message": "Torolve."}
    if action == "run":
        out = agent.submit(str(body.get("brief_id") or ""), when=str(body.get("when") or "now"))
        return agent_payload() | {"message": "Sorba allitva.", "run_id": out["run_id"]}
    if action == "cancel":
        agent.cancel(str(body.get("run_id") or ""))
        return agent_payload() | {"message": "Visszavonva."}
    if action == "adopt":
        if body.get("confirm") is not True:
            raise ValueError("Az atvetelhez megerosites kell.")
        sandbox = agent.adopt(str(body.get("run_id") or ""), int(body.get("rank") or 0))
        return {"ok": True, "message": "A jelolt atkerult a Homokozoba (ertesites nelkul, paper-kotes nelkul).",
                "sandbox": sandbox}
    raise ValueError("Ismeretlen muvelet")


PRACTICE_SESSION_RE = re.compile(r"/api/practice/session/([a-z]{12})/(task/(\d{1,2})(/decision)?|summary)")


def practice_payload() -> dict:
    """Gyakorlo mod: a feladatkeszlet allapota, a korok (papir es datum nelkul), a munkasor, az alapertekek."""
    from . import lab, labqueue
    from .practice import sampler
    from .practice import session as practice
    from .practice import store as practice_store

    return {"pool": sampler.pool_info(practice_store.load_pool()), "sessions": practice.listing()[:30],
            "queue": labqueue.jobs("admin", "practice_pool")[:5], "lab_running": bool(lab.read_status().get("running")),
            "defaults": {"rounds": 20, "mix": sampler.DEFAULT_MIX, "max_days": sampler.MAX_DAYS,
                         "max_rounds": sampler.MAX_ROUNDS}}


def practice_action(action: str, body: dict) -> dict:
    from .practice import sampler
    from .practice import session as practice

    if action == "session":
        seed = body.get("seed")
        created = practice.create(load_settings(), seed=int(seed) if seed not in (None, "") else None,
                                  rounds=int(body.get("rounds") or 20), mix=body.get("mix") or None,
                                  code=str(body.get("code") or "").strip() or None)
        return {"session": created}
    if action == "pool":
        job = sampler.submit(force=bool(body.get("force")))
        return practice_payload() | {"message": "A labor-szolgaltatas sorba allitotta.", "queued": job["id"]}
    if action == "delete":
        practice.delete(str(body.get("id") or ""))
        return practice_payload() | {"message": "Torolve."}
    raise ValueError("Ismeretlen muvelet")


def backtest_jobs():
    global _BT_JOBS
    if _BT_JOBS is None:
        from .backtest_page.jobs import Jobs

        _BT_JOBS = Jobs()
    return _BT_JOBS


def news_payload(symbol: str, refresh: bool = False) -> dict:
    """Egy papir hirei (pontozva) es a mai point-in-time hir-jellemzok. GET: csak a tarolt adat."""
    from .sentiment import features as nf, service as news, store as news_store

    settings = load_settings()
    result = news.refresh(symbol, settings, force=True) if refresh else None
    data = news_store.load(symbol)
    arts = sorted(news_store.articles(symbol), key=lambda a: a.published_at, reverse=True)
    day = pd.Timestamp.now(tz=nf.MARKET_TZ).tz_localize(None).normalize()
    f = news.features_for(symbol, day)
    return {"symbol": symbol, "enabled": settings.sentiment.enabled, "features": f, "summary": news.summary(f),
            "coverage": data.get("coverage", {}), "fetched": data.get("fetched", {}), "refresh": result,
            "event_labels": news.EVENT_LABELS,
            "articles": [{k: a.to_dict()[k] for k in ("source", "kind", "published_at", "title", "url", "publisher",
                                                     "form", "items", "score", "also")} for a in arts[:60]]}


def backtest_payload() -> dict:
    from .backtest_page import data as bt_data, runner as bt_runner, runs as bt_runs
    from .entry_research import HOLDOUT_START

    settings = load_settings()
    return {"strategies": bt_runner.strategy_choices(settings), "symbols": bt_data.stored_symbols(),
            "runs": bt_runs.listing(), "job": backtest_jobs().snapshot(),
            "holdout_start": f"{HOLDOUT_START:%Y-%m-%d}", "usage_mb": round(bt_data.usage_bytes() / 1e6, 1),
            "limit_mb": bt_data.LIMIT_MB}


def backtest_listing(symbol: str) -> dict:
    """A kezdodatum-valaszto also hatara: a tozsdere lepes napja (de legkorabban 1980-01-01)."""
    from .backtest_page import data as bt_data

    first, listed = bt_data.earliest_start(symbol, load_settings())
    return {"symbol": symbol, "listed": f"{listed:%Y-%m-%d}" if listed is not None else None,
            "min": f"{first:%Y-%m-%d}"}


def backtest_action(action: str, body: dict) -> dict:
    from .backtest_page import data as bt_data, runner as bt_runner, runs as bt_runs

    jobs = backtest_jobs()
    if action == "data":
        symbol, start = bt_data.clean_symbol(body.get("symbol")), bt_data.parse_start(body.get("start"))

        def work(progress):
            m = bt_data.collect(symbol, start, load_settings(), progress=progress)
            note = f" - figyelem: {'; '.join(m['quality']['issues'])}" if m["quality"]["issues"] else ""
            return {"message": f"Kesz: {symbol} {m['first']} -> {m['last']} ({m['bars']} gyertya){note}."}
        started = jobs.start("data", symbol, work)
    elif action == "run":
        symbol = bt_data.clean_symbol(body.get("symbol"))
        start = bt_data.parse_start(body.get("start") or bt_data.MIN_START)
        strategy = str(body.get("strategy") or "")
        settings = load_settings()
        overrides = body.get("params") or {}
        if not isinstance(overrides, dict):
            raise ValueError("A parameterek formatuma hibas.")
        cost = body.get("cost_bps")
        cost = float(cost) if cost not in (None, "") else None
        bt_runner.resolve_params(settings, strategy, overrides, cost)      # azonnali hibauzenet, ha rossz
        kwargs = dict(end=body.get("end") or None, overrides=overrides,
                      cost_bps=cost, include_holdout=bool(body.get("include_holdout")),
                      capital=float(body.get("capital") or bt_runner.DEFAULT_CAPITAL),
                      alloc_pct=float(body.get("alloc_pct") or 100))

        def work(progress):
            # a futtatas elott mindig frissul az adat; ha mar megvan, csak a hianyzo eleje / vege jon le
            s = load_settings()
            begin = max(start, bt_data.earliest_start(symbol, s)[0])
            try:
                bt_data.collect(symbol, begin, s, progress=progress)
            except Exception as exc:
                if bt_data.load(symbol).empty:
                    raise ValueError(f"Nincs elerheto adat: {symbol} ({exc})") from exc
                log.warning("Backteszt: %s adatfrissitese nem sikerult, a meglevo adattal fut: %s", symbol, exc)
                progress("Az adat frissitese nem sikerult - a meglevo adattal fut.")
            result = bt_runner.run(symbol, strategy, s, start=f"{begin:%Y-%m-%d}", progress=progress, **kwargs)
            return {"run_id": bt_runs.save(result), "message": bt_runner.summary_line(result)}
        started = jobs.start("run", symbol, work)
    elif action == "delete":
        return {"deleted": bt_runs.delete(str(body.get("id") or "")), **backtest_payload()}
    else:
        raise ValueError(f"Ismeretlen muvelet: {action}")
    if not started:
        raise ValueError("Mar fut egy backteszt-feladat - varj, amig befejezodik.")
    return {"started": True, "job": jobs.snapshot()}


def strategies_payload() -> dict:
    from . import meanrev_research as mr
    from . import momentum_research

    settings = load_settings()
    cfg = settings.strategies
    tracker = positions.PositionTracker(settings, read_only=True)
    book = broker.BrokerBook(settings, read_only=True)
    summaries = {"pullback": mr.load_summary(), "deep": mr.load_summary(mr.DEEP_DIR),
                 "etf": mr.load_summary(mr.ETF_DIR), "momentum": momentum_research.load_summary(),
                 "sandbox": mr.load_summary(mr.SANDBOX_DIR)}
    out = []
    for sid in config.STRATEGY_IDS:
        switch = cfg.get(sid)
        engine = cfg.sandbox.engine if sid == "sandbox" else sid
        tracked = [p for p in tracker.positions.values() if p.strategy == sid]
        out.append({
            "id": sid, "label": config.STRATEGY_LABELS[sid], "engine": engine,
            "enabled": switch.enabled, "alerts": switch.alerts,
            "paper": cfg.paper == sid, "can_paper": sid in config.PAPER_STRATEGIES,
            "params": switch.params.model_dump() if hasattr(switch, "params") else None,
            "entry": ({k: getattr(settings.entry, k) for k in BREAKOUT_SUMMARY} if sid == "breakout"
                      else cfg.sandbox.entry if sid == "sandbox" else None),
            "symbols": list(cfg.etf.symbols) if sid == "etf" else None,
            "stats": tracker.closed_stats(sid),
            "open": sum(1 for p in tracked if p.is_active),
            "paper_open": sum(1 for t in book.trades.values() if t.strategy == sid and t.is_active),
            "research": _research_brief(summaries.get(sid)),
            "momentum": _momentum_state(settings) if sid == "momentum" else None,
        })
    return {"strategies": out, "paper": cfg.paper, "auto_trade": settings.broker.auto_trade}


def _research_brief(summary: dict | None) -> dict | None:
    """A validalas lenyege a strategia-kartyara (lathato idoszak: a zarolt holdout nelkul)."""
    if not summary:
        return None
    visible = (summary.get("main") or {}).get("visible") or {}
    if "cagr" in visible:                  # portfolio-strategia (momentum_research.py)
        return {"n": visible.get("trades"), "win": visible.get("trade_win"), "ev": visible.get("trade_avg"),
                "per_week": (visible.get("trades_per_year") or 0) / 52, "cagr": visible.get("cagr"),
                "sharpe": visible.get("sharpe"), "max_dd": visible.get("max_dd"), "excess": visible.get("excess"),
                "baseline_cagr": visible.get("baseline_cagr"),
                "passed": (summary.get("gate") or {}).get("passed"), "computed_at": summary.get("computed_at")}
    return {"n": visible.get("n"), "win": visible.get("win"), "ev": visible.get("ev"),
            "per_week": visible.get("fills_per_week"), "passed": (summary.get("gate") or {}).get("passed"),
            "computed_at": summary.get("computed_at")}


def _momentum_state(settings) -> dict | None:
    """A legutobbi napi momentum-rangsor (monitor.momentum_cycle irja)."""
    path = settings.momentum_state_path
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def strategies_action(body: dict) -> dict:
    """paper: melyik strategia legyen a paper fiokhoz kotve | update: egy strategia kapcsoloi / parameterei.
    Minden valtozas a valtozasnaploba kerul; a paper-valtasrol ertesites is megy."""
    before = load_settings()
    data = before.strategies.model_dump()
    action = body.get("action")
    if action == "paper":
        target = str(body.get("paper") or "")
        if target not in config.PAPER_STRATEGIES:
            raise ValueError("A homokozo nem kotheto a paper fiokhoz." if target == "sandbox"
                             else f"Ismeretlen strategia: {target}")
        if not data[target]["enabled"]:
            raise ValueError(f"A(z) {config.STRATEGY_LABELS[target]} strategia ki van kapcsolva - elobb kapcsold be.")
        data["paper"] = target
    elif action == "update":
        sid = str(body.get("strategy") or "")
        if sid not in config.STRATEGY_IDS:
            raise ValueError(f"Ismeretlen strategia: {sid}")
        values = body.get("values") or {}
        allowed = ({"enabled", "alerts"} | ({"params"} if sid != "breakout" else set())
                   | ({"engine", "entry"} if sid == "sandbox" else set())
                   | ({"symbols"} if sid == "etf" else set()))
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Nem allithato: {', '.join(sorted(unknown))}")
        if values.get("enabled") is False and data["paper"] == sid:
            raise ValueError("A paper fiokhoz kotott strategia nem kapcsolhato ki - elobb valts masikra.")
        if "entry" in values:
            from . import lab

            raw = {k: v for k, v in (values["entry"] or {}).items() if k in lab.EDITABLE}
            checked = lab.validate_entry(raw, before.entry)            # tipus- es tartomanyellenorzes
            values = {**values, "entry": {k: getattr(checked, k) for k in raw}}
        for key, value in values.items():
            data[sid][key] = {**data[sid][key], **value} if key == "params" else value
    else:
        raise ValueError("Ismeretlen muvelet")

    strategies = config.StrategiesSettings(**data)            # validalas MENTES ELOTT
    after = before.model_copy(update={"strategies": strategies})
    if ledger.log_config_change(before, after, source="web") is None:
        return strategies_payload()                           # nem valtozott semmi
    config.save_strategies(strategies)
    if action == "paper" and before.strategies.paper != strategies.paper:
        old, new = (config.STRATEGY_LABELS[x] for x in (before.strategies.paper, strategies.paper))
        lines = [f"A paper fiokhoz kotott strategia mostantol: {new}.",
                 f"A(z) {old} strategia meg nyitott paper kotesei a sajat szabalyaikkal zarulnak."]
        if not after.broker.auto_trade:
            lines.append("FIGYELEM: az automata bekuldes (auto_trade) ki van kapcsolva.")
        try:
            Dispatcher(after).dispatch(Alert(
                symbol="STOCKWATCH", title=f"Paper fiok: {old} -> {new}", body="\n".join(lines), forecast=None,
                event="strategy_switch", extra={"from": before.strategies.paper, "to": strategies.paper}))
        except Exception:
            log.exception("A strategia-valtas ertesitese nem ment ki")
    return strategies_payload()


def sandbox_payload() -> dict:
    """A Kutatas (homokozo) ful: a homokozo beallitasa, backtestje, elo jelzesei es kotesei."""
    from . import lab
    from . import meanrev_research as mr

    settings = load_settings()
    tracker = positions.PositionTracker(settings, read_only=True)
    sandbox = next(x for x in strategies_payload()["strategies"] if x["id"] == "sandbox")
    live_entry = settings.entry.model_dump()
    rows = ledger_payload(limit=60, strategy="sandbox")["rows"]
    items = sorted((p for p in tracker.positions.values() if p.strategy == "sandbox"),
                   key=lambda p: p.signal_date, reverse=True)
    main = mr.load_summary()
    return {
        "strategy": sandbox,
        "backtest": mr.sandbox_state(),
        "reference": {"params": main.get("params"), "gate": main.get("gate"), "main": main.get("main"),
                      "computed_at": main.get("computed_at")} if main else None,
        "pullback_params": settings.strategies.pullback.params.model_dump(),
        "entry_fields": [{"field": k, "label": LAB_FIELD_LABELS.get(k, k), "type": t.__name__, "min": lo, "max": hi,
                          "live": live_entry.get(k), "value": settings.strategies.sandbox.entry.get(k)}
                         for k, (t, lo, hi) in lab.EDITABLE.items()],
        "positions": [_position_dict(p) for p in items[:100]],
        "stats": tracker.closed_stats("sandbox"),
        "ledger": rows,
    }


def sandbox_action(body: dict) -> dict:
    from . import lab
    from . import meanrev_research as mr

    settings = load_settings()
    sandbox = settings.strategies.sandbox
    action = body.get("action")
    if action == "backtest":
        if sandbox.engine == "pullback":
            mr.request_sandbox(sandbox.params)
            message = "A backtest sorba allt - a labor-szolgaltatas nehany percen belul lefuttatja."
        else:
            cfg = EntrySettings(**(settings.entry.model_dump() | sandbox.entry))
            exp = lab.add_experiment(f"Homokozo {datetime.now():%m-%d %H:%M}", "indexes", cfg)
            message = f"Kiserlet a Laborban: {exp['name']} - az eredmeny a Labor fulon jelenik meg."
    elif action == "adopt":
        if sandbox.engine != "pullback":
            raise ValueError("Csak a visszahuzas-motor parameterei vehetok at a Visszahuzas strategiaba.")
        strategies_action({"action": "update", "strategy": "pullback",
                           "values": {"params": sandbox.params.model_dump()}})
        message = "A homokozo szabalyai atkerultek a Visszahuzas (RSI2) strategiaba."
    elif action == "reset":
        defaults = config.SandboxStrategy()
        strategies_action({"action": "update", "strategy": "sandbox",
                           "values": {"params": settings.strategies.pullback.params.model_dump(), "entry": {},
                                      "engine": defaults.engine}})
        message = "A homokozo visszaallitva a Visszahuzas strategia szabalyaira."
    else:
        raise ValueError("Ismeretlen muvelet")
    return {"ok": True, "message": message, **sandbox_payload()}


def apply_settings(app: AppState, values: dict) -> dict:
    """Beallitasok mentese; ha egy index MOST kapcsolt be, azonnal atnezzuk
    (nem kell a kovetkezo zarasig varni), utana minden zaras utan fut."""
    before_all = load_settings()
    before = before_all.scan
    payload = update_settings(values)
    after_all = load_settings()
    after = after_all.scan
    ledger.log_config_change(before_all, after_all, source="web")      # ki / mikor / mit valtoztatott
    newly = [n for n in ("nasdaq100", "sp500") if getattr(after, n) and not getattr(before, n)]
    if newly:
        payload["index_scan_started"] = app.start_index_scan(alerts=True)
        payload["job"] = app.job
    elif after_all.entry.benchmark != before_all.entry.benchmark:
        # mas index -> mas rezsim-benchmark (SPY / QQQ): az adata kell a fejlec kijelzesehez
        threading.Thread(target=_fetch_benchmark, args=(app, after_all), daemon=True).start()
    return payload


def _fetch_benchmark(app: AppState, settings) -> None:
    try:
        market.load_benchmark(settings, refresh=True)
        with app.lock:
            app.overview = None
    except Exception:
        log.exception("A benchmark (%s) letoltese nem sikerult", settings.entry.benchmark)


def _set_yaml_key(lines: list[str], section: str, key: str, value: str) -> list[str]:
    start = next((i for i, l in enumerate(lines) if re.match(rf"^{section}:\s*(#.*)?$", l)), None)
    if start is None:
        return lines + [f"{section}:", f"  {key}: {value}"]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_]", lines[i])), len(lines))
    pattern = re.compile(rf"^(\s+{re.escape(key)}:\s*)([^#]*?)(\s*#.*)?$")
    for i in range(start + 1, end):
        m = pattern.match(lines[i])
        if m:
            head, old_value, comment = m.group(1), m.group(2), m.group(3) or ""
            if comment:
                # a komment oszlopa maradjon a helyen, ha elfer
                width = len(head) + len(old_value)
                lines[i] = (head + value).ljust(width) + (comment if comment.startswith(" ") else " " + comment)
            else:
                lines[i] = head + value
            return lines
    # nincs meg ilyen kulcs: a szekcio vegere
    insert_at = end
    # a kovetkezo szekcio fejlec-kommentjei es az ures sorok ne keruljenek ele
    while insert_at > start + 1 and (not lines[insert_at - 1].strip() or lines[insert_at - 1].startswith("#")):
        insert_at -= 1
    lines.insert(insert_at, f"  {key}: {value}")
    return lines


def update_watchlist(symbols: list[str]) -> dict:
    cleaned = [s.strip().upper() for s in symbols if s and s.strip()]
    bad = [s for s in cleaned if not SYMBOL_RE.match(s)]
    if bad:
        raise ValueError(f"Ervenytelen ticker: {', '.join(bad)}")
    if not cleaned:
        raise ValueError("A watchlist nem lehet ures.")
    if len(cleaned) > 60:
        raise ValueError("Legfeljebb 60 papir figyelheto egyszerre.")
    previous = load_watchlist().symbols
    config.save_watchlist(cleaned)
    if previous != cleaned:
        added = [s for s in cleaned if s not in previous]
        removed = [s for s in previous if s not in cleaned]
        ledger.log_watchlist_change(added, removed, source="web")
    return settings_payload()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


LOGIN_MESSAGES = {
    "bad": "Hibás jelszó.",
    "locked": "Túl sok hibás próbálkozás. Próbáld újra 15 perc múlva.",
    "origin": "A kérés nem erről az oldalról jött.",
    "unset": "Nincs beállítva jelszó. A szerveren futtasd: python cli.py web-password",
}
PUBLIC_PATHS = ("/login", "/style.css", "/i18n.js")   # bejelentkezes nelkul is elerheto (+ /i18n/<nyelv>.json)


# Kereskedesi naptar (pnl_calendar.py, #22) - csak olvas, a mentett allapotfajlokbol
def calendar_query(query: str) -> dict:
    from . import pnl_calendar

    q = parse_qs(query)
    one = lambda k: (q.get(k, [""])[0] or "").strip()                                 # noqa: E731
    symbol = one("symbol").upper() or None
    if symbol and not SYMBOL_RE.match(symbol):
        raise ValueError("Ervenytelen ticker")
    run = one("run") or None
    strategy = one("strategy") or None
    if strategy and not re.fullmatch(r"[a-z_]{1,20}", strategy):
        raise ValueError("Ervenytelen strategia")
    return {"sources": pnl_calendar.parse_sources(one("sources") or None), "strategy": strategy, "symbol": symbol,
            "run_id": run}


def calendar_payload(query: str) -> dict:
    from . import pnl_calendar
    from .backtest_page import runs

    q = parse_qs(query)
    today = datetime.now()
    year = int((q.get("year", [""])[0] or today.year))
    mon = int((q.get("month", [""])[0] or today.month))
    args = calendar_query(query)
    out = pnl_calendar.month(load_settings(), year, mon, **args)
    out["runs"] = [{"id": r["id"], "symbol": r["symbol"], "strategy_label": r.get("strategy_label"),
                    "start": r.get("start"), "end": r.get("end")} for r in runs.listing()[:50]]
    return out


def make_handler(app: AppState, auth: webauth.Auth | None = None):
    auth = auth or webauth.Auth()

    class Handler(BaseHTTPRequestHandler):
        server_version = "StockWatch"

        def log_message(self, fmt, *args):  # a konzolt ne szemetelje tele
            log.debug("web: " + fmt, *args)

        # -- valaszok --------------------------------------------------
        def _headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if auth.is_https(self):
                self.send_header("Strict-Transport-Security", "max-age=31536000")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self._headers()
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, _dumps(payload), "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        # -- bejelentkezes ---------------------------------------------
        def _authorized(self) -> bool:
            return auth.check(webauth.session_token(self))

        def _login_page(self, query: str) -> None:
            if not auth.enabled:
                return self._redirect("/")
            code = parse_qs(query).get("e", [""])[0]
            if not auth.has_password:
                code = "unset"
            message = LOGIN_MESSAGES.get(code, "")
            html = (WEB_DIR / "login.html").read_text(encoding="utf-8")
            html = html.replace("<!--MESSAGE-->", f'<p class="login-error" role="alert">{message}</p>'
                                if message else "")
            self._send(200, html.encode("utf-8"), STATIC_TYPES[".html"])

        def _login(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                return self._error(413, "Tul nagy keres")
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            origin = self.headers.get("Origin")
            if origin and origin != "null" and urlparse(origin).netloc != self.headers.get("Host"):
                return self._redirect("/login?e=origin")
            password = form.get("password", [""])[0]
            try:
                token = auth.login(auth.client_ip(self), password)
            except webauth.LoginLocked:
                return self._redirect("/login?e=locked")
            if not token:
                return self._redirect("/login?e=bad")
            return self._redirect("/", webauth.session_cookie(token, auth.is_https(self)))

        def _logout(self) -> None:
            auth.logout(webauth.session_token(self))
            self.send_response(200)
            body = _dumps({"ok": True})
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            self.send_header("Set-Cookie", webauth.session_cookie("", auth.is_https(self), max_age=0))
            self.end_headers()
            self.wfile.write(body)

        # -- GET -------------------------------------------------------
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            if path == "/login":
                return self._login_page(url.query)
            public = path in PUBLIC_PATHS or re.fullmatch(r"/i18n/[a-z]{2}\.json", path)
            if not public and not self._authorized():
                if path.startswith("/api/"):
                    return self._error(401, "Bejelentkezes szukseges")
                return self._redirect("/login")
            try:
                if path == "/api/session":
                    return self._json({"auth": auth.enabled})
                if path == "/api/overview":
                    return self._json({**app.ensure(), "job": app.job})
                if path == "/api/job":
                    return self._json(app.job)
                if path.startswith("/api/symbol/"):
                    symbol = path.rsplit("/", 1)[-1].upper()
                    if not SYMBOL_RE.match(symbol):
                        return self._error(400, "Ervenytelen ticker")
                    detail = symbol_detail(app, symbol)
                    return self._json(detail) if detail else self._error(404, "Nincs adat erre a papirra")
                if path.startswith("/api/signal/"):
                    signal_id = path.rsplit("/", 1)[-1]
                    if not SIGNAL_ID_RE.match(signal_id):
                        return self._error(400, "Ervenytelen jelazonosito")
                    return self._json(replay_mod.trace(signal_id))
                if path == "/api/ledger":
                    q = parse_qs(url.query)
                    return self._json(ledger_payload(
                        limit=max(1, min(int(q.get("limit", ["300"])[0]), 2000)),
                        decision=(q.get("decision", [""])[0] or None),
                        symbol=(q.get("symbol", [""])[0].upper() or None),
                        strategy=(q.get("strategy", [""])[0] or None)))
                if path == "/api/positions":
                    return self._json(positions_payload())
                if path == "/api/strategies":
                    return self._json(strategies_payload())
                if path == "/api/sandbox":
                    return self._json(sandbox_payload())
                if path == "/api/holdings":
                    return self._json(holdings_payload(app))
                if path == "/api/broker":
                    return self._json(broker_payload(app))
                if path == "/api/signals":
                    limit = int(parse_qs(url.query).get("limit", ["200"])[0])
                    return self._json(signals_payload(max(1, min(limit, 1000))))
                if path == "/api/settings":
                    return self._json(settings_payload())
                if path == "/api/lab":
                    return self._json(lab_payload())
                if path.startswith("/api/news/"):
                    symbol = path.rsplit("/", 1)[-1].upper()
                    if not SYMBOL_RE.match(symbol):
                        return self._error(400, "Ervenytelen ticker")
                    return self._json(news_payload(symbol))
                if path == "/api/events":
                    return self._json(events_payload())
                m = re.fullmatch(r"/api/events/study/(\d{8}-[0-9a-f]{8})(\.csv|/event/(\d{1,5}))?", path)
                if m:
                    from .events import service as ev_service
                    from .events import store as ev_store

                    record = ev_store.load(m.group(1))
                    if record is None:
                        return self._error(404, "Nincs ilyen tanulmany")
                    if m.group(2) == ".csv":
                        return self._send(200, ev_service.to_csv(record).encode("utf-8"), "text/csv; charset=utf-8")
                    if m.group(3) is not None:
                        try:
                            return self._json(ev_service.event_detail(record, int(m.group(3))))
                        except ValueError as exc:
                            return self._error(404, str(exc))
                    return self._json(record)
                if path == "/api/agent":
                    return self._json(agent_payload())
                if path.startswith("/api/agent/run/"):
                    run_id = path.rsplit("/", 1)[-1]
                    if not re.fullmatch(r"\d{8}-[0-9a-f]{8}", run_id):
                        return self._error(400, "Ervenytelen futas-azonosito")
                    try:
                        return self._json(agent_run_payload(run_id))
                    except ValueError as exc:
                        return self._error(404, str(exc))
                if path == "/api/digest":
                    return self._json(digest_payload())
                if path.startswith("/api/digest/"):
                    day = path.rsplit("/", 1)[-1]
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                        return self._error(400, "Ervenytelen nap")
                    payload = digest_payload(day)
                    return self._json(payload) if payload["record"] else self._error(404, "Nincs ilyen osszefoglalo")
                if path in ("/api/calendar", "/api/calendar/year", "/api/calendar.csv") or path.startswith("/api/calendar/day/"):
                    from . import pnl_calendar

                    try:
                        if path == "/api/calendar":
                            return self._json(calendar_payload(url.query))
                        args = calendar_query(url.query)
                        if path == "/api/calendar/year":
                            year = int(parse_qs(url.query).get("year", [str(datetime.now().year)])[0])
                            return self._json(pnl_calendar.year_view(load_settings(), year, **args))
                        if path == "/api/calendar.csv":
                            return self._send(200, pnl_calendar.to_csv(load_settings(), **args).encode("utf-8"),
                                              "text/csv; charset=utf-8")
                        day = path.rsplit("/", 1)[-1]
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                            return self._error(400, "Ervenytelen nap")
                        return self._json(pnl_calendar.day_detail(load_settings(), day, **args))
                    except ValueError as exc:
                        return self._error(400, str(exc))
                if path == "/api/practice":
                    return self._json(practice_payload())
                m = PRACTICE_SESSION_RE.fullmatch(path)
                if m and not m.group(4):
                    from .practice import session as practice

                    try:
                        if m.group(2) == "summary":
                            return self._json(practice.summary_payload(m.group(1)))
                        return self._json(practice.task_payload(m.group(1), int(m.group(3))))
                    except LookupError as exc:
                        return self._error(404, str(exc))
                    except ValueError as exc:
                        return self._error(400, str(exc))
                if path == "/api/backtest":
                    return self._json(backtest_payload())
                if path == "/api/backtest/job":
                    return self._json(backtest_jobs().snapshot())
                if path == "/api/backtest/listing":
                    symbol = parse_qs(url.query).get("symbol", [""])[0].strip().upper()
                    if not SYMBOL_RE.match(symbol):
                        return self._error(400, "Ervenytelen ticker")
                    return self._json(backtest_listing(symbol))
                if path.startswith("/api/backtest/run/"):
                    from .backtest_page import runs as bt_runs

                    run_id = path.rsplit("/", 1)[-1]
                    if not bt_runs.RUN_ID_RE.match(run_id):
                        return self._error(400, "Ervenytelen futtatas-azonosito")
                    result = bt_runs.load(run_id)
                    return self._json(result) if result else self._error(404, "Nincs ilyen futtatas")
                if path == "/api/best":
                    return self._json({"result": app.best, "job": app.job})
                m = re.fullmatch(r"/i18n/([a-z]{2})\.json", path)
                if m:
                    from . import i18n

                    if m.group(1) not in config.LANGUAGES:
                        return self._error(404, "Nem talalhato")
                    return self._json(i18n.entries(m.group(1)))
                return self._static(path)
            except Exception as exc:
                log.exception("Hiba a keres kiszolgalasa kozben: %s", path)
                return self._error(500, f"{type(exc).__name__}: {exc}")

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB_DIR / name).resolve()
            # utvonal-bejaras ellen: csak az app/web mappabol szolgalunk ki
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                return self._error(404, "Nem talalhato")
            ctype = STATIC_TYPES.get(target.suffix, "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)

        # -- POST ------------------------------------------------------
        def _discard_body(self) -> None:
            """Elutasitas elott a (kis) keres-torzs kiolvasasa: Windowson az olvasatlan adattal lezart kapcsolat
            RST-t kuld, es a kliens a valasz helyett kapcsolat-megszakadast lat."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if 0 < length <= 64_000:
                self.rfile.read(length)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/login":
                return self._login()
            if self.headers.get("X-StockWatch") != "1":
                self._discard_body()
                return self._error(403, "Hianyzo X-StockWatch fejlec")
            if not self._authorized():
                self._discard_body()
                return self._error(401, "Bejelentkezes szukseges")
            if path == "/logout":
                return self._logout()
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 64_000:
                    return self._error(413, "Tul nagy keres")
                body = json.loads(self.rfile.read(length) or b"{}")
                if path.startswith("/api/replay/"):
                    signal_id = path.rsplit("/", 1)[-1]
                    if not SIGNAL_ID_RE.match(signal_id):
                        return self._error(400, "Ervenytelen jelazonosito")
                    return self._json(replay_mod.replay(signal_id))
                if path == "/api/scan/index":
                    started = app.start_index_scan()
                    return self._json({"started": started, "job": app.job}, 202 if started else 409)
                if path == "/api/best":
                    started = app.start_best()
                    return self._json({"started": started, "job": app.job}, 202 if started else 409)
                if path == "/api/refresh":
                    started = app.start_refresh()
                    return self._json({"started": started, "job": app.job}, 202 if started else 409)
                if path == "/api/settings":
                    return self._json(apply_settings(app, body.get("values", {})))
                if path == "/api/watchlist":
                    return self._json(update_watchlist(body.get("symbols", [])))
                if path == "/api/strategies":
                    return self._json(strategies_action(body))
                if path == "/api/sandbox":
                    return self._json(sandbox_action(body))
                if path.startswith("/api/broker/"):
                    return self._json(broker_action(app, path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/discord/"):
                    return self._json(discord_action(path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/holdings/"):
                    return self._json(holding_action(app, path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/lab/"):
                    return self._json(lab_action(path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/backtest/"):
                    return self._json(backtest_action(path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/digest/"):
                    return self._json(digest_action(path.rsplit("/", 1)[-1]))
                if path.startswith("/api/agent/"):
                    return self._json(agent_action(path.rsplit("/", 1)[-1], body))
                if path.startswith("/api/events/"):
                    return self._json(events_action(path.rsplit("/", 1)[-1], body))
                m = PRACTICE_SESSION_RE.fullmatch(path)
                if m and m.group(4):
                    from .practice import session as practice

                    try:
                        return self._json(practice.decide(m.group(1), int(m.group(3)), body))
                    except LookupError as exc:
                        return self._error(404, str(exc))
                if path.startswith("/api/practice/"):
                    return self._json(practice_action(path.rsplit("/", 1)[-1], body))
                if path == "/api/news/refresh":
                    symbol = str(body.get("symbol") or "").upper()
                    if not SYMBOL_RE.match(symbol):
                        return self._error(400, "Ervenytelen ticker")
                    return self._json(news_payload(symbol, refresh=True))
                if path == "/api/check":
                    from . import quickcheck

                    symbol = str(body.get("symbol") or "").strip().upper()
                    if not SYMBOL_RE.match(symbol):
                        return self._error(400, "Ervenytelen ticker")
                    return self._json(quickcheck.evaluate_symbol(symbol))
                return self._error(404, "Nem talalhato")
            except (ValueError, TypeError, broker.BrokerError, LockTimeout) as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                log.exception("POST hiba")
                return self._error(500, f"{type(exc).__name__}: {exc}")

    return Handler


CLOSE_SCAN_AFTER = (16, 20)      # New York-i ido: a monitor 16:15-os futasa utan


def index_scan_due(settings, now: pd.Timestamp | None = None) -> bool:
    """Kell-e most zaras utani indexszkenneles (mert aznap meg senki nem futtatta)?

    A monitor 16:15-kor fut; ha az mar elkeszitette a mai eredmenyt, a felulet
    nem ismetli meg. Ha a monitor nem fut, a felulet potolja - igy eleg, ha a
    kettobol az egyik fut.
    """
    if not (settings.scan.nasdaq100 or settings.scan.sp500):
        return False
    now = now or pd.Timestamp.now(tz=monitor.MARKET_TZ)
    if now.weekday() >= 5 or (now.hour, now.minute) < CLOSE_SCAN_AFTER:
        return False
    close = now.replace(hour=16, minute=15, second=0, microsecond=0)
    path = settings.index_scan_path
    if path.exists():
        try:
            done = pd.Timestamp(json.loads(path.read_text(encoding="utf-8"))["computed_at"])
            if done.tz_convert(monitor.MARKET_TZ) >= close:
                return False
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
    return True


def _close_scheduler(app: AppState) -> None:
    """Zaras utan (hetkoznap 16:20 NY) lefuttatja az indexszkennelest, ha esedekes."""
    while True:
        try:
            config.load_settings.cache_clear()
            if index_scan_due(load_settings()) and app.job["state"] != "running":
                log.info("Zaras utani indexszkenneles (a felulet utemezi)...")
                app.start_index_scan(alerts=True)
        except Exception:
            log.exception("A zaras utani utemezo hibara futott")
        time.sleep(60)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          behind_proxy: bool = False) -> None:
    local = host in ("127.0.0.1", "localhost", "::1")
    # halozatrol elerheto felulet csak jelszoval mehet
    auth = webauth.Auth(trust_proxy=behind_proxy, required=behind_proxy or not local)
    if auth.required and not config.env_value(webauth.HASH_KEY):
        raise SystemExit("A felulet halozatrol is elerheto lenne, de nincs jelszo beallitva.\n"
                         "Allits be egyet:  python cli.py web-password")
    if not local:
        log.warning("A felulet %s cimen figyel - a halozatrol is elerheto! Titkositatlan HTTP: "
                    "a jelszo es a sutik lehallgathatok. Javasolt: 127.0.0.1 + HTTPS proxy.", host)
    app = AppState()
    threading.Thread(target=_close_scheduler, args=(app,), daemon=True, name="close-scheduler").start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app, auth))
    url = f"http://{'127.0.0.1' if host in ('127.0.0.1', 'localhost') else host}:{port}"
    print(f"StockWatch felulet: {url}   (leallitas: Ctrl+C)")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nLeallitva.")
    finally:
        httpd.server_close()
