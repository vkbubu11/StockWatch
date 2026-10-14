"""A folyamatos figyelo ciklus.

Feladatai:
  * a piac nyitvatartasahoz igazodo utemezes,
  * inkrementalis adatfrissites,
  * elemzes es jelzeskeszites,
  * ALLAPOTGEP: csak valodi valtozasnal riaszt,
  * napi takaritas, hogy a lemezhasznalat ne nojon.

Az allapotgep a legfontosabb resz. Nelkule a monitor 15 percenkent ujra
kikuldene ugyanazt a formaciot, es hasznalhatatlanul zajossa valna. Egy
jelzes eleteben negy allapot van:

    forming  ->  triggered  ->  target_hit
                            ->  failed

Riasztas kizarolag ALLAPOTVALTASKOR megy ki.

SWING LONG BELEPESI JELZESEK (entry.py)
---------------------------------------
A formacio-allapotok mellett (vagy helyett, lasd `alerts.pattern_events`)
a monitor minden korben lefuttatja a belepesi motort is. Belepesi riasztas
KIZAROLAG lezart napi gyertyan megy ki (zaras utani futas), mert egy napkozbeni
"kitores" zarasig meg visszaeshet - es a hamis kitoresek nagy resze pont ilyen.
Ugyanarra a mintara `entry.cooldown_days` napig nincs ismetles.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from . import (
    broker, charting, config, entry, feed, heartbeat, holdings, housekeeping, indicators, ledger, market,
    positions, sessions, shadow,
)
from .alerts import Alert, Dispatcher, format_body, format_title
from .config import Settings, ensure_dirs, load_settings, load_watchlist
from .forecast import Calibration, Forecast, analyse

log = logging.getLogger(__name__)

MARKET_TZ = "America/New_York"


@dataclass
class StateEntry:
    """Egy nyomon kovetett jelzes allapota."""

    fingerprint: str
    symbol: str
    name: str
    direction: str
    status: str
    trigger_level: float
    target: float
    invalidation: float
    first_seen: str
    updated_at: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class SignalState:
    """A jelzesek allapotanak tarolasa (data/state.json).

    Szandekosan nagyon kicsi fajl: csak az aktiv jelzesek maradnak benne, a
    lezartakat a housekeeping 90 nap utan kitakaritja.
    """

    def __init__(self, settings: Settings, read_only: bool = False) -> None:
        self.settings = settings
        self.path = settings.state_path
        # Dry-run modban NEM irunk vissza. Kulonben egy probafutas "elhasznalna"
        # a jelzeseket: az allapot atallna, es a kesobbi eles futas mar nem
        # latna valtozast, tehat nem riasztana.
        self.read_only = read_only
        self.entries: dict[str, StateEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("Serult state.json - uresen indulunk.")
            return
        for key, value in raw.get("signals", {}).items():
            try:
                self.entries[key] = StateEntry(**value)
            except TypeError:
                continue   # regi sema, eldobjuk

    def save(self) -> None:
        if self.read_only:
            log.debug("Dry-run: az allapot nem kerul mentesre.")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "signals": {k: v.to_dict() for k, v in self.entries.items()},
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def transition(self, forecast: Forecast) -> str | None:
        """Megallapitja, kell-e riasztani. None = nincs valtozas.

        Visszaadott esemenyek: "new", "triggered", "target_hit", "failed".
        """
        match = forecast.pattern
        if match is None:
            return None

        key = match.fingerprint()
        now = datetime.now(timezone.utc).isoformat()
        existing = self.entries.get(key)

        if existing is None:
            self.entries[key] = StateEntry(
                fingerprint=key,
                symbol=match.symbol,
                name=match.name,
                direction=match.direction,
                status=match.status,
                trigger_level=match.trigger_level,
                target=match.target,
                invalidation=match.invalidation,
                first_seen=now,
                updated_at=now,
            )
            # Az azonnal aktivalt minta rogton kitoresi jelzes
            return "triggered" if match.status == "triggered" else "new"

        if existing.status == match.status:
            return None   # nincs valtozas - EZ akadalyozza meg a spammelest

        existing.status = match.status
        existing.updated_at = now
        return "triggered" if match.status == "triggered" else match.status

    def check_outcomes(self, symbol: str, df: pd.DataFrame) -> list[tuple[StateEntry, str]]:
        """Az aktivalt jelzesek lezarasa: elerte-e a celt vagy a stopot.

        Az utolso gyertya intraday szelsoertekeit nezzuk, mert a valosagban
        is azok utik meg a celt vagy a stopot, nem a zaroar.
        """
        if df.empty:
            return []

        last = df.iloc[-1]
        high, low = float(last["high"]), float(last["low"])
        now = datetime.now(timezone.utc).isoformat()
        closed: list[tuple[StateEntry, str]] = []

        for entry in self.entries.values():
            if entry.symbol != symbol or entry.status != "triggered":
                continue

            if entry.direction == "bullish":
                hit, failed = high >= entry.target, low <= entry.invalidation
            else:
                hit, failed = low <= entry.target, high >= entry.invalidation

            # Ha ugyanazon a gyertyan mindketto teljesult, ovatosak vagyunk:
            # a bukast konyveljuk, mert nem tudjuk, melyik tortent elobb.
            if failed:
                entry.status, entry.updated_at = "failed", now
                closed.append((entry, "failed"))
            elif hit:
                entry.status, entry.updated_at = "target_hit", now
                closed.append((entry, "target_hit"))

        return closed


# ---------------------------------------------------------------------------
# Elemzesi ciklus
# ---------------------------------------------------------------------------
def scan(
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    dispatcher: Dispatcher | None = None,
    state: SignalState | None = None,
    send_alerts: bool = True,
    final_bar: bool = True,
    entry_state: entry.EntryState | None = None,
    entry_sink: list[entry.EntrySignal] | None = None,
    refresh: bool = True,
    tracker: positions.PositionTracker | None = None,
    index_mode: bool = False,
    short_sink: list | None = None,
) -> list[Forecast]:
    """Egy teljes korbejaras: adat -> elemzes -> jelzes.

    `index_mode`: az indexszkenneles (Nasdaq-100 / S&P 500) kore - az adat a
    tomoritett `data/universe` cache-be megy, nincs formacio-allapotgep (csak ha
    a regi alakzat-ertesitesek be vannak kapcsolva), es az elokeszulet-jelzes
    csak `scan.index_setups` eseten megy ki (500 papiron sok zaj lenne).

    `final_bar`: lezart-e mar a mai napi gyertya. Belepesi riasztas csak
    lezart gyertyan megy ki.
    `entry_sink`: ha megadod, ide gyujti a kiertekelt belepesi jelzeseket
    (a CLI `entries` parancsa hasznalja).
    """
    settings = settings or load_settings()
    symbols = symbols or default_symbols(settings)
    ensure_dirs()

    calibration = Calibration.load(settings.calibration_path)
    state = state if state is not None else SignalState(settings)

    if refresh:
        data = feed.update(symbols, settings, universe=index_mode)
    else:
        from . import store

        data = {s: store.load(s, settings, universe=index_mode) for s in symbols}
    results: list[Forecast] = []

    entries_on = settings.entry.enabled
    # rezsim es relativ ero papironkent: Nasdaq-100 -> QQQ, csak S&P 500 -> ^GSPC, egyik sem -> sajat trend
    ctx = market.load_context(settings, refresh=refresh) if entries_on else None
    # Dry-run / csak-elemzes eseten SEMMILYEN allapotot nem irunk vissza, kulonben
    # egy probafutas "elhasznalna" a jelzeseket es a poziciokovetest.
    read_only = (not send_alerts) or bool(getattr(dispatcher, "dry_run", False))
    # Az earnings cache nem allapot, csak lekeresi gyorsitotar: azt csak a
    # kifejezett dry-run nem irja.
    earnings = (
        market.EarningsCalendar(
            settings, allow_network=refresh,
            read_only=bool(getattr(dispatcher, "dry_run", False)),
        )
        if entries_on else None
    )
    if entries_on and entry_state is None:
        entry_state = entry.EntryState(settings, read_only=read_only)
    tracking = entries_on and settings.entry.track_positions and send_alerts
    if tracking and tracker is None:
        tracker = positions.PositionTracker(settings, read_only=read_only)
    if tracking:
        # a paper trade-del rendelkezo jelzeseket NEM leptetjuk virtualisan - elobb atvesszuk az allapotukat
        reconcile_tracker(tracker, settings)
    # A sajat poziciokat mindig ujraolvassuk: a feluletrol kozben valtozhattak.
    # (Az indexkorben nincs: a sajat poziciok papirjai a watchlist-korben futnak.)
    own = holdings.HoldingStore(settings, read_only=read_only) if send_alerts and not index_mode else None
    alerts_cfg = settings.alerts
    legacy = alerts_cfg.pattern_events
    # short jelzes (medves alakzat lefele torese) - csak lezart gyertyan, mint a long belepes
    short_path = alerts_cfg.want_short and not legacy
    pattern_states = (legacy or not index_mode or alerts_cfg.want_short) and (legacy or not short_path or final_bar)
    if own is not None and earnings is None:
        earnings = market.EarningsCalendar(settings, allow_network=refresh, read_only=read_only)
    if own is not None and ctx is None:
        ctx = market.load_context(settings, refresh=refresh)

    breakout_on = settings.strategies.breakout.enabled       # a "Kitores" strategia (Strategiak ful)
    sandbox_cfg = settings.sandbox_breakout() if final_bar else None   # homokozo a kitores-motorral
    to_broker: list[tuple[entry.EntrySignal, Forecast]] = []
    # jelzesnaplo: csak az eles, lezart gyertyas dontesekrol (dry-run / csak-elemzes nem ir)
    journal = (ledger.Ledger(settings, calibration, source="index" if index_mode else "monitor")
               if final_bar and send_alerts and not read_only and entries_on else None)
    for symbol in symbols:
        raw = data.get(symbol)
        if raw is not None and final_bar:
            # vegleges dontes csak LEZART gyertyan: a meg alakulo mai gyertya kimarad
            raw = sessions.completed_bars(raw)
        if raw is None or raw.empty:
            continue

        try:
            enriched = indicators.enrich(raw, settings.structure.atr_period)
            forecast = analyse(symbol, enriched, settings, calibration)
        except Exception:
            log.exception("Az elemzes hibara futott: %s", symbol)
            continue

        results.append(forecast)
        benchmark = ctx.bench_for(symbol) if ctx is not None else None     # a papir indexe

        signals: list[entry.EntrySignal] = []
        if entries_on:
            try:
                signals = _evaluate_entries(symbol, enriched, forecast, settings, calibration,
                                            ctx, earnings)
            except Exception:
                log.exception("A belepesi motor hibara futott: %s", symbol)
            if signals:
                # gepi tanulas arnyek-uzemben: becsles a jelzes melle, naplozas zaras utan - nem dont
                try:
                    shadow.score_signals(signals, enriched, settings, benchmark)
                    if final_bar and not read_only:
                        shadow.log_signals(signals, settings)
                except Exception:
                    log.exception("Az arnyek-becsles hibara futott: %s", symbol)
            if entry_sink is not None:
                entry_sink.extend(signals)
            if short_sink is not None:
                # kiserleti short motor (short.py): a "Legjobb belepo most" gomb jeloltlistajahoz
                try:
                    from . import short as short_mod

                    short_sink.extend(short_mod.evaluate(symbol, enriched, forecast.matches, settings, benchmark))
                except Exception:
                    log.exception("A short motor hibara futott: %s", symbol)

        if not send_alerts or dispatcher is None:
            continue

        # 1. A mar aktiv jelzesek lezarasa (formacio-allapotgep)
        closed, event = [], None
        if pattern_states:
            closed = state.check_outcomes(symbol, enriched)
            event = state.transition(forecast)
        for entry_rec, outcome in closed:
            if pattern_alert_allowed(alerts_cfg, entry_rec.direction, outcome):
                _emit(dispatcher, forecast, enriched, outcome, settings)
        # 2. Uj vagy allapotot valtott minta
        if event and forecast.pattern is not None:
            fresh = _fresh_breakout(forecast.pattern, enriched)
            if pattern_alert_allowed(alerts_cfg, forecast.pattern.direction, event, fresh):
                _emit(dispatcher, forecast, enriched, event, settings)

        # 3. A mar jelzett trade-ek kovetese (reszleges kilepes, stop, cel) - csak lezart gyertyan
        if final_bar and tracking and tracker is not None:
            for position, event in tracker.update(symbol, enriched):
                title, body = positions.format_event(position, event)
                dispatcher.dispatch(
                    Alert(symbol=symbol, title=title, body=body, forecast=forecast,
                          event=event, extra={"position": position.id, "status": position.status,
                                              "result_r": position.result_r, "signal_id": position.signal_id},
                          strategy=position.strategy)
                )

        # 4. Sajat (kezzel felvett) poziciok kiertekelese - csak lezart gyertyan
        if final_bar and own is not None and own.open(symbol):
            _check_holdings(own, symbol, enriched, forecast, settings, dispatcher,
                            benchmark, earnings)

        # 5. Swing long belepesi jelzesek - csak lezart gyertyan. Minden jelolt dontese
        #    (kikuldve / kihagyva / elutasitva, okkal) a jelzesnaploba kerul. A homokozo, ha a
        #    kitores-motorral fut, ugyanazokon az alakzatokon a sajat szuroivel dont.
        runs = [("breakout", settings, signals)] if breakout_on else []
        if sandbox_cfg is not None and entries_on and entry_state is not None:
            try:
                sandbox_signals = _evaluate_entries(symbol, enriched, forecast, sandbox_cfg, calibration, ctx, earnings)
                for signal in sandbox_signals:
                    signal.strategy = "sandbox"
                runs.append(("sandbox", sandbox_cfg, sandbox_signals))
            except Exception:
                log.exception("A homokozo kitores-motorja hibara futott: %s", symbol)
        for strategy_id, cfg_s, run_signals in (runs if final_bar and entry_state is not None else []):
            # Ha a papirban mar van a strategianak kovetett nyitott/fuggo trade-je vagy sajat pozicio,
            # nem jelzunk ujabb belepest (nem bovitunk ra) - az elokeszulet mehet. A strategiak fuggetlenek.
            busy = bool(tracking and tracker is not None
                        and any(p.strategy == strategy_id for p in tracker.active(symbol)))
            busy = busy or bool(own is not None and own.open(symbol))
            for signal in run_signals:
                decision, reason = entry_decision(signal, cfg_s, entry_state, busy=busy,
                                                  index_mode=index_mode)
                if decision == "alerted":
                    _emit_entry(dispatcher, forecast, enriched, signal, cfg_s, final_bar)
                    entry_state.mark(signal)
                    if tracking and tracker is not None:
                        tracker.register(signal)
                    if signal.kind != "setup":
                        to_broker.append((signal, forecast))
                elif decision == "skipped":
                    log.info("%s: %s - %s", symbol, signal.pattern.name, reason)
                if journal is not None:
                    try:
                        journal.record(signal, decision, reason, raw=raw, benchmark=benchmark,
                                       mode="index" if index_mode else "watchlist",
                                       snapshot=decision == "alerted",
                                       regime_source=ctx.regime_source(symbol) if ctx is not None else None)
                    except Exception:
                        log.exception("A jelzesnaplo irasa nem sikerult: %s", symbol)

    if to_broker and not read_only and dispatcher is not None:
        submit_to_broker(to_broker, dispatcher)
        if tracking:
            reconcile_tracker(tracker, settings)      # a frissen bekuldott trade-ek mar "Alpaca" forrasuak

    state.save()
    if entry_state is not None:
        entry_state.save()
    if tracker is not None:
        tracker.save()
    if own is not None:
        own.save()
    return results


# ---------------------------------------------------------------------------
# Alpaca paper kereskedes
# ---------------------------------------------------------------------------
def _fresh_settings() -> Settings:
    """A feluletrol futas kozben is atallithato (pl. auto_trade) - mindig frissen olvassuk."""
    config.load_settings.cache_clear()
    return load_settings()


def submit_to_broker(signals: list, dispatcher: Dispatcher) -> None:
    """A kikuldott belepesi jelzesek bekuldese a paper szamlara, ha az auto_trade be van kapcsolva."""
    settings = _fresh_settings()
    if not settings.broker.auto_trade:
        return
    # csak a paper fiokhoz kotott strategia jelzesei (a tobbi csak ertesit es virtualisan kovet)
    signals = [(s, f) for s, f in signals if getattr(s, "strategy", "breakout") == settings.strategies.paper]
    if not signals:
        return
    client = broker.AlpacaClient.from_env()
    if client is None:
        log.warning("auto_trade be van kapcsolva, de nincs Alpaca kulcs a beallitasokban.")
        return
    with broker.book_lock(settings):
        book = broker.BrokerBook(settings)
        for signal, forecast in signals:
            try:
                if signal.kind == "pullback":
                    trade = broker.submit_pullback(signal, settings, client, book,
                                                   settings.strategies.get(signal.strategy).params)
                else:
                    trade = broker.submit(signal, settings, client, book)
                title, body = broker.format_event(trade, "broker_submitted")
                dispatcher.dispatch(Alert(symbol=trade.symbol, title=title, body=body, forecast=forecast,
                                          event="broker_submitted",
                                          extra={"broker_trade": trade.id, "signal_id": trade.signal_id},
                                          strategy=trade.strategy))
            except broker.NoRoom as exc:
                log.info("Paper bekuldes kihagyva (%s): %s", signal.symbol, exc)
                dispatcher.dispatch(Alert(
                    symbol=signal.symbol, title=f"{signal.symbol}: {broker.EVENT_LABELS['broker_skipped']}",
                    body=f"A belepesi jelzes ervenyes, de a portfolioban nincs hely neki:\n{exc}",
                    forecast=forecast, event="broker_skipped", extra={"signal_id": signal.signal_id},
                    strategy=signal.strategy))
            except broker.BrokerError as exc:
                log.warning("Paper bekuldes sikertelen (%s): %s", signal.symbol, exc)
                dispatcher.dispatch(Alert(
                    symbol=signal.symbol, title=f"{signal.symbol}: {broker.EVENT_LABELS['broker_error']}",
                    body=f"A belepesi jelzes nem ment be a paper szamlara:\n{exc}", forecast=forecast,
                    event="broker_error", extra={"signal_id": signal.signal_id}, strategy=signal.strategy))
            finally:
                book.save()


def reconcile_tracker(tracker: positions.PositionTracker | None, settings: Settings) -> None:
    """A kovetett jelzesek allapota a paper trade-ek szerint (a Jelzett trade-ek = Alpaca)."""
    if tracker is None:
        return
    for position in tracker.sync_from_broker():
        log.info("Kovetett jelzes az Alpaca szerint: %s -> %s (%s)", position.id, position.status,
                 position.broker_status)


def broker_cycle(dispatcher: Dispatcher | None, final_bar: bool, read_only: bool = False,
                 tracker: positions.PositionTracker | None = None) -> int:
    """A paper trade-ek szinkronizalasa es az esemenyek kikuldese. Az esemenyek szamat adja.

    `tracker`: ha megadod, a kovetett jelzesek allapotat is az Alpacahoz igazitja es menti.
    """
    client = broker.AlpacaClient.from_env()
    if client is None:
        return 0
    settings = _fresh_settings()
    if dispatcher is not None:
        dispatcher.settings = settings             # a feluletrol valtozhatott (pl. strategia-ertesites)
    with broker.book_lock(settings):
        book = broker.BrokerBook(settings, read_only=read_only)
        if not book.active():
            events = []
        else:
            try:
                events = broker.sync(settings, client, book, final_bar=final_bar)
            except broker.BrokerError as exc:
                log.warning("Paper szinkron sikertelen: %s", exc)
                return 0
            book.save()
    if tracker is not None:
        reconcile_tracker(tracker, settings)
        tracker.save()
    if dispatcher is not None:
        for trade, event in events:
            title, body = broker.format_event(trade, event)
            dispatcher.dispatch(Alert(symbol=trade.symbol, title=title, body=body, forecast=None,
                                      event=event, extra={"broker_trade": trade.id, "status": trade.status,
                                                          "pnl": trade.realised_pnl, "signal_id": trade.signal_id},
                                      strategy=trade.strategy))
    return len(events)


# ---------------------------------------------------------------------------
# Indexszkenneles (Nasdaq-100 / S&P 500)
# ---------------------------------------------------------------------------
def index_symbols(settings: Settings, allow_network: bool = True) -> list[str]:
    """A bekapcsolt indexek tagjai, a watchlist-korben amugy is futo papirok nelkul."""
    from . import indexes

    own = set(default_symbols(settings))
    return [s for s in indexes.IndexMembers(settings).symbols(allow_network) if s not in own]


def index_scan(
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    send_alerts: bool = True,
    entry_state: entry.EntryState | None = None,
    tracker: positions.PositionTracker | None = None,
    refresh: bool = True,
    state: SignalState | None = None,
) -> dict:
    """Az indexek atnezese lezart napi gyertyan + az eredmeny mentese a feluletnek.

    Visszaadja (es a `data/index_scan.json`-be irja) a kiertekelt belepesi
    helyzeteket - a felulet ebbol mutatja az indexjeloltek tablazatat.
    """
    settings = settings or _fresh_settings()
    symbols = index_symbols(settings, allow_network=refresh)
    result = {"computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "indexes": [n for n in ("nasdaq100", "sp500") if getattr(settings.scan, n)],
              "symbols": len(symbols), "analysed": 0, "last_bar": None, "signals": []}
    if not symbols:
        _save_index_scan(settings, result)
        return result

    log.info("Indexszkenneles: %d papir...", len(symbols))
    sink: list[entry.EntrySignal] = []
    shorts: list = []
    forecasts = scan(settings, symbols=symbols, dispatcher=dispatcher, send_alerts=send_alerts,
                     final_bar=True, entry_state=entry_state, entry_sink=sink, refresh=refresh,
                     tracker=tracker, index_mode=True, state=state, short_sink=shorts)
    result["analysed"] = len(forecasts)
    result["last_bar"] = max((f"{f.as_of:%Y-%m-%d}" for f in forecasts), default=None)
    cfg = settings.entry
    result["signals"] = sorted(
        ({**s.to_dict(), "qualifies": s.qualifies(cfg), "failed": [c.name for c in s.failed_checks],
          "label": entry.KIND_LABELS.get(s.kind, s.kind),
          "exit_lines": entry.exit_plan_lines(s.plan)[2:],
          "muted": s.kind == "setup" and not settings.scan.index_setups}
         for s in sink),
        key=lambda d: (d["qualifies"] and not d["muted"], d["score"]), reverse=True,
    )
    # a short jeloltek (kiserleti) - a "Legjobb belepo most" gomb a legjobbakat elo arral ujraszamolja
    result["shorts"] = [s.to_dict(cfg) for s in sorted(shorts, key=lambda s: (s.passed, s.score), reverse=True)[:60]]
    _save_index_scan(settings, result)
    log.info("Indexszkenneles kesz: %d elemezve, %d belepesi helyzet.", result["analysed"], len(sink))
    return result


def _save_index_scan(settings: Settings, result: dict) -> None:
    path = settings.index_scan_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")


def default_symbols(settings: Settings) -> list[str]:
    """A watchlist, kiegeszitve a sajat nyitott poziciok papirjaival."""
    symbols = list(load_watchlist().symbols)
    for symbol in holdings.HoldingStore(settings, read_only=True).symbols():
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _check_holdings(
    own: holdings.HoldingStore,
    symbol: str,
    enriched: pd.DataFrame,
    forecast: Forecast,
    settings: Settings,
    dispatcher: Dispatcher,
    benchmark: pd.DataFrame | None,
    earnings: market.EarningsCalendar | None,
) -> None:
    earnings_date = earnings.next_date(symbol) if earnings is not None else None
    for holding in own.open(symbol):
        try:
            ev = holdings.evaluate(holding, enriched, forecast, settings, benchmark, earnings_date)
        except Exception:
            log.exception("A sajat pozicio kiertekelese hibara futott: %s", holding.id)
            continue
        for event in holdings.alert_events(ev):
            title, body = holdings.format_event(ev, event)
            dispatcher.dispatch(
                Alert(symbol=symbol, title=title, body=body, forecast=forecast, event=event,
                      extra={"holding": holding.id, "verdict": ev.verdict, "pnl_pct": round(ev.pnl_pct, 2)})
            )


def entry_decision(signal: entry.EntrySignal, settings: Settings, entry_state: entry.EntryState,
                   busy: bool = False, index_mode: bool = False) -> tuple[str, str]:
    """Mi lesz egy belepesi jelolttel, es miert.

    alerted  - kimegy az ertesites
    skipped  - minden szuron atment, de nem kuldjuk (irany, ismetlesvedelem, nyitott trade...)
    rejected - nem felelt meg (bukott szuro vagy a kuszob alatti pontszam)
    """
    cfg = settings.entry
    if not signal.qualifies(cfg):
        if signal.failed_checks:
            return "rejected", "bukott szuro: " + ", ".join(c.name for c in signal.failed_checks)
        if signal.score < cfg.min_score:
            return "rejected", f"pontszam {signal.score:.0f} < kuszob {cfg.min_score:.0f}"
        return "rejected", "elokeszulet-jelzes kikapcsolva"
    if not settings.alerts.want_long:
        return "skipped", "a long irany ki van kapcsolva"
    if index_mode and signal.kind == "setup" and not settings.scan.index_setups:
        return "skipped", "elokeszulet az indexpapirokon elnemitva"
    if busy and signal.kind != "setup":
        return "skipped", "mar van nyitott trade vagy sajat pozicio a papirban"
    if not entry_state.should_alert(signal):
        return "skipped", f"ismetlesvedelem ({cfg.cooldown_days} nap)"
    return "alerted", "minden szuro atment, pontszam a kuszob felett"


def pattern_alert_allowed(alerts_cfg, direction: str, event: str, fresh: bool = True) -> bool:
    """Mehet-e alakzat-ertesites az `alerts.direction` beallitas szerint?

    * regi alakzat-ertesitesek (`pattern_events`): minden allapot, de csak a
      beallitott irany(ok)ba
    * kulonben short iranyban: a medves alakzat FRISS lefele torese, es a mar
      jelzett short kimenete (cel / bukas)
    """
    side_ok = ((direction == "bullish" and alerts_cfg.want_long)
               or (direction == "bearish" and alerts_cfg.want_short))
    if alerts_cfg.pattern_events:
        return side_ok
    if direction != "bearish" or not alerts_cfg.want_short:
        return False
    if event == "triggered":
        return fresh
    return event in ("target_hit", "failed")


def _fresh_breakout(match, df: pd.DataFrame) -> bool:
    """A kitores a legutobbi ket gyertya valamelyiken tortent (nem egy regi torest jelzunk)."""
    if match.trigger_date is None or len(df) < 2:
        return False
    return match.trigger_date >= df.index[-2]


def _evaluate_entries(
    symbol: str,
    enriched: pd.DataFrame,
    forecast: Forecast,
    settings: Settings,
    calibration: Calibration,
    ctx: market.MarketContext | None,
    earnings: market.EarningsCalendar | None,
) -> list[entry.EntrySignal]:
    """A kitoreses motor jelzesei egy papirra. `ctx`: papironkenti rezsim + relativ ero indexe."""
    # Az earnings-datumot csak akkor kerdezzuk le, ha van mit kiertekelni -
    # igy nem hivjuk a Yahoot minden korben minden papirra.
    candidates = [m for m in forecast.matches if entry.classify(m, enriched, settings.entry)]
    if not candidates:
        return []
    earnings_date = earnings.next_date(symbol) if earnings is not None else None
    return entry.evaluate(
        symbol,
        enriched,
        candidates,
        settings,
        calibration=calibration,
        benchmark=ctx.bench_for(symbol) if ctx is not None else None,
        earnings_date=earnings_date,
        support=forecast.support,
        resistance=forecast.resistance,
        regime=ctx.regime(symbol, enriched, enriched.index[-1]) if ctx is not None else None,
    )


def _emit_entry(
    dispatcher: Dispatcher,
    forecast: Forecast,
    df: pd.DataFrame,
    signal: entry.EntrySignal,
    settings: Settings,
    final_bar: bool,
) -> None:
    # A charton a JELZETT minta latszodjon, ne feltetlenul a "legerosebb"
    view = dataclasses.replace(forecast, pattern=signal.pattern)
    chart_png = None
    if settings.alerts.send_chart:
        try:
            chart_png = charting.to_png(charting.build_chart(view, df))
        except Exception:
            log.exception("A chart elkeszitese nem sikerult: %s", forecast.symbol)

    dispatcher.dispatch(
        Alert(
            symbol=signal.symbol,
            title=entry.format_title(signal),
            body=entry.format_body(signal, final_bar=final_bar),
            forecast=view,
            event=f"entry_{signal.kind}",
            chart_png=chart_png,
            extra=signal.to_dict(),
            strategy=signal.strategy,
        )
    )


def _emit(
    dispatcher: Dispatcher,
    forecast: Forecast,
    df: pd.DataFrame,
    event: str,
    settings: Settings,
) -> None:
    chart_png = None
    if settings.alerts.send_chart:
        try:
            chart_png = charting.to_png(charting.build_chart(forecast, df))
        except Exception:
            log.exception("A chart elkeszitese nem sikerult: %s", forecast.symbol)

    dispatcher.dispatch(
        Alert(
            symbol=forecast.symbol,
            title=format_title(forecast, event),
            body=format_body(forecast, event),
            forecast=forecast,
            event=event,
            chart_png=chart_png,
        )
    )


# ---------------------------------------------------------------------------
# Visszahuzas (RSI2) - a teljes lista a zaron, rangsorolva, napi limittel
# ---------------------------------------------------------------------------
PULLBACK_STRATEGIES = ("pullback", "sandbox")


def pullback_symbols(settings: Settings, allow_network: bool = True) -> list[str]:
    """Watchlist + a Nasdaq-100 es az S&P 500 MINDEN tagja (a strategia a ket index osszevont listajan fut)."""
    from . import indexes

    members = indexes.IndexMembers(settings)
    out = list(default_symbols(settings))
    for name in ("nasdaq100", "sp500"):
        out += members.members(name, allow_network=allow_network)
    return list(dict.fromkeys(out))


def pullback_cycle(settings: Settings, strategy: str, params, dispatcher: Dispatcher | None,
                   tracker: positions.PositionTracker | None, entry_state: entry.EntryState | None,
                   read_only: bool = False, refresh: bool = True) -> dict:
    """A Visszahuzas strategia (vagy a homokozo visszahuzas-motorja) a zaron.

    Ugyanazok a szabalyok, mint a kutatasban (meanrev.py): a jelolteket a legalacsonyabb RSI(2)
    szerint rangsorolja; naponta legfeljebb `max_signals_per_day`, osszesen `max_open` nyitott kotes.
    Minden jelolt dontese (kikuldve / kihagyva / elutasitva, okkal) a jelzesnaploba kerul."""
    from . import meanrev, store

    symbols = pullback_symbols(settings, allow_network=refresh)
    if refresh:
        # az indexszkenneles csak a bekapcsolt indexeket frissiti - a tobbi tag adata itt
        scanned = set(default_symbols(settings))
        if settings.scan.nasdaq100 or settings.scan.sp500:
            scanned |= set(index_symbols(settings, allow_network=False))
        missing = [s for s in symbols if s not in scanned]
        for start in range(0, len(missing), 100):
            try:
                feed.update(missing[start:start + 100], settings, universe=True)
            except Exception:
                log.exception("Visszahuzas: adatfrissites sikertelen (%d papir)", len(missing[start:start + 100]))
    ctx = market.load_context(settings, refresh=refresh)
    reference = ctx.benches.get(market.NASDAQ_INDEX)
    ref_day = reference.index[-1] if reference is not None and not reference.empty else None
    earnings = market.EarningsCalendar(settings, allow_network=refresh,
                                       read_only=bool(getattr(dispatcher, "dry_run", False)))
    own = holdings.HoldingStore(settings, read_only=True)
    watch = set(load_watchlist().symbols)

    found, frames = [], {}
    for symbol in symbols:
        df = sessions.completed_bars(store.load(symbol, settings))
        if df is None or len(df) < meanrev.WARMUP:
            continue
        if ref_day is not None and df.index[-1] < ref_day:
            continue                                     # elavult adat - nem a mai jelzes
        if tracker is not None:
            # a strategia nyitott virtualis kotesei (idempotens: csak allapotvaltaskor ad esemenyt)
            for position, event in tracker.update(symbol, df, strategy=strategy):
                if dispatcher is None:
                    continue
                title, body = positions.format_event(position, event)
                dispatcher.dispatch(Alert(symbol=symbol, title=title, body=body, forecast=None, event=event,
                                          extra={"position": position.id, "status": position.status,
                                                 "result_r": position.result_r, "result_pct": position.result_pct,
                                                 "signal_id": position.signal_id}, strategy=position.strategy))
        regime = ctx.regime(symbol, df, df.index[-1])
        signal = meanrev.evaluate(symbol, df, settings, params, regime, None, strategy)
        if signal is None:
            continue
        if params.earnings_blackout_days > 0:
            signal = meanrev.evaluate(symbol, df, settings, params, regime, earnings.next_date(symbol), strategy)
        found.append(signal)
        frames[symbol] = df

    journal = None if read_only else ledger.Ledger(settings, None, source="monitor")
    active = [p for p in (tracker.active() if tracker is not None else []) if p.strategy == strategy]
    # a napi limitbe a ma mar kikuldottek is beleszamitanak (pl. ujrainditas utani potlo futas)
    day = max((s.as_of for s in found), default=None)
    sent_today = sum(1 for p in (tracker.positions.values() if tracker is not None else [])
                     if p.strategy == strategy and day is not None and p.signal_date == f"{day:%Y-%m-%d}")
    room = max(0, min(params.daily_cap - sent_today, params.open_cap - len(active)))
    held = {p.symbol for p in active}
    ranked = sorted(found, key=lambda s: (not s.passed,) + meanrev.rank_key(s))
    alerted = []
    for rank, signal in enumerate(ranked, 1):
        symbol = signal.symbol
        if not signal.passed:
            decision, reason = "rejected", "bukott szuro: " + ", ".join(c.name for c in signal.failed_checks)
        elif symbol in held or own.open(symbol):
            decision, reason = "skipped", "mar van nyitott kotes vagy sajat pozicio a papirban"
        elif entry_state is not None and not entry_state.should_alert(signal):
            decision, reason = "skipped", "ismetlesvedelem"
        elif room <= 0:
            decision, reason = "skipped", f"napi limit / nincs szabad hely (rang: {rank}.)"
        else:
            decision, reason = "alerted", f"minden szabaly teljesult (rang: {rank}.)"
            room -= 1
        if journal is not None:
            try:
                journal.record(signal, decision, reason, raw=frames[symbol], benchmark=ctx.bench_for(symbol),
                               mode="watchlist" if symbol in watch else "index", snapshot=decision == "alerted",
                               regime_source=ctx.regime_source(symbol))
            except Exception:
                log.exception("A jelzesnaplo irasa nem sikerult: %s", symbol)
        if decision != "alerted":
            continue
        alerted.append(signal)
        if dispatcher is not None:
            dispatcher.dispatch(Alert(symbol=symbol, title=meanrev.format_title(signal),
                                      body=meanrev.format_body(signal, params), forecast=None,
                                      event="entry_pullback", chart_png=_pullback_chart(signal, frames[symbol], settings),
                                      extra=signal.to_dict(), strategy=strategy))
        if entry_state is not None:
            entry_state.mark(signal)
        if tracker is not None:
            tracker.register(signal, params)
    if alerted and not read_only and dispatcher is not None and strategy in config.PAPER_STRATEGIES:
        submit_to_broker([(s, None) for s in alerted], dispatcher)      # csak ha ez a paper fiokhoz kotott
        if tracker is not None:
            reconcile_tracker(tracker, settings)
    if entry_state is not None:
        entry_state.save()
    if tracker is not None:
        tracker.save()
    log.info("%s: %d jelolt, %d kikuldve", strategy, len(found), len(alerted))
    return {"strategy": strategy, "candidates": len(found), "passed": sum(s.passed for s in found),
            "alerted": [s.symbol for s in alerted]}


def _pullback_chart(signal, df: pd.DataFrame, settings: Settings) -> bytes | None:
    """Chart a jelzeshez: a limit (trigger), a stop es a jelzeskori zaro vonalaval."""
    if not settings.alerts.send_chart:
        return None
    try:
        enriched = indicators.enrich(df, settings.structure.atr_period)
        forecast = analyse(signal.symbol, enriched, settings, Calibration.load(settings.calibration_path))
        return charting.to_png(charting.build_chart(dataclasses.replace(forecast, pattern=signal.pattern), enriched))
    except Exception:
        log.exception("A chart elkeszitese nem sikerult: %s", signal.symbol)
        return None


# ---------------------------------------------------------------------------
# Utemezes
# ---------------------------------------------------------------------------
def is_market_open(now: pd.Timestamp | None = None) -> bool:
    """Nyitva van-e a NASDAQ. Unnepnapokat is kezel, ha elerheto a naptar."""
    now = now or pd.Timestamp.now(tz=MARKET_TZ)

    if now.weekday() >= 5:
        return False

    try:
        import pandas_market_calendars as mcal

        calendar = mcal.get_calendar("NASDAQ")
        schedule = calendar.schedule(start_date=now.date(), end_date=now.date())
        if schedule.empty:
            return False
        open_time = schedule.iloc[0]["market_open"].tz_convert(MARKET_TZ)
        close_time = schedule.iloc[0]["market_close"].tz_convert(MARKET_TZ)
        return open_time <= now <= close_time
    except Exception:
        # A naptar hianya ne allitsa meg a monitort - visszaesunk a fix idore
        log.debug("Piaci naptar nem elerheto, fix nyitvatartassal szamolunk.")
        return now.replace(hour=9, minute=30) <= now <= now.replace(hour=16, minute=0)


def schedule_triggers(settings: Settings) -> dict:
    """Az utemezes - KIFEJEZETTEN New York-i idoben.

    Az APScheduler a trigger-objektum SAJAT idozonajat hasznalja (alapbol a gep helyi
    idejet), nem a scheduler-et. Idozona nelkul egy magyar gepen a "zaras utani"
    futas 16:15 MAGYAR idoben, nyitvatartas kozben futott le.
    """
    from apscheduler.triggers.cron import CronTrigger

    return {
        "intraday": CronTrigger(day_of_week="mon-fri", hour="9-15",
                                minute=f"*/{settings.data.refresh_minutes}", timezone=MARKET_TZ),
        # paper trade-ek: cel / stop teljesules, nullszaldo, idokorlat zaras elott - percenkent
        "broker": CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*", timezone=MARKET_TZ),
        "preopen": CronTrigger(day_of_week="mon-fri", hour=9, minute=5, timezone=MARKET_TZ),
        "close": CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=MARKET_TZ),
    }


def run_forever(settings: Settings | None = None, dry_run: bool = False) -> None:
    """A folyamatos figyeles inditasa.

    Napi idotavon a jelzes CSAK a zarasnal vegleges, ezert ket utemet
    hasznalunk:
      * nyitvatartas alatt N percenkent: korai eszleles, de a napi gyertya meg
        alakul - ezt a jelzes szovege is jelzi,
      * zaras utan: a vegleges, megerositett elemzes.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    settings = settings or load_settings()
    housekeeping.setup_logging(settings)
    ensure_dirs()

    dispatcher = Dispatcher(settings, dry_run=dry_run)
    state = SignalState(settings, read_only=dry_run)
    entry_state = entry.EntryState(settings, read_only=dry_run)
    tracker = positions.PositionTracker(settings, read_only=dry_run)
    scheduler = BlockingScheduler(timezone=MARKET_TZ)

    def intraday_job() -> None:
        if not is_market_open():
            # zarva is szinkronizalunk: egy az Alpacan kezzel visszavont megbizas se
            # maradjon "belepesre var" allapotban a kovetkezo nyitasig. (A kovetett
            # jelzeseket itt nem irjuk - azt a zaras utani futas teszi, ugyanazon a szalon.)
            try:
                broker_cycle(dispatcher, final_bar=False, read_only=dry_run)
            except Exception:
                log.exception("Paper szinkron (piacon kivul) hibara futott")
            return
        log.info("Nyitvatartas alatti frissites (a napi gyertya meg alakul)...")
        # a paper trade-eket nyitvatartas alatt a percenkenti broker_job koveti
        scan(settings, dispatcher=dispatcher, state=state, final_bar=False,
             entry_state=entry_state, tracker=tracker)

    def broker_job() -> None:
        """Nyitvatartas alatt percenkent: a cel / stop az Alpacan azonnal teljesul, itt csak
        perceken belul jon rola ertesites, a masik fel stopja a belepore kerul, es az
        idokorlat utolso napjan meg zaras elott kilep. A kovetett jelzeseket nem irja
        (azt a 15 perces kor teszi), igy nem utkozik vele."""
        if not is_market_open():
            return
        try:
            broker_cycle(dispatcher, final_bar=False, read_only=dry_run)
        except Exception:
            log.exception("Paper szinkron (percenkenti) hibara futott")

    started = datetime.now(timezone.utc)
    errors = heartbeat.ErrorCounter()
    logging.getLogger().addHandler(errors)

    def close_job(missed_day: pd.Timestamp | None = None) -> None:
        """`missed_day`: egy kimaradt zaras utani futas potlasa (lasd heartbeat.missed_close)."""
        # a lepesek kulon vedettek: egy hiba nem viszi el a tobbit, es az eletjel mindig kimegy
        log.info("Piaczaras utani vegleges elemzes (belepesi jelzesek)...")
        day = (pd.Timestamp(missed_day).tz_localize(MARKET_TZ) if missed_day is not None
               else pd.Timestamp.now(tz=MARKET_TZ).normalize())
        report = heartbeat.DayReport(day=day, watchlist_total=len(default_symbols(settings)),
                                     late=missed_day is not None)
        try:
            results = scan(settings, dispatcher=dispatcher, state=state, final_bar=True,
                           entry_state=entry_state, tracker=tracker)
            report.watchlist_analysed = len(results)
            report.last_bar = max((pd.Timestamp(f.as_of) for f in results), default=None)
        except Exception:
            log.exception("A zaras utani elemzes hibara futott")
            report.failures.append("watchlist elemzes")
        fresh = _fresh_settings()
        if fresh.scan.nasdaq100 or fresh.scan.sp500:
            try:
                report.index = index_scan(fresh, dispatcher=dispatcher, send_alerts=True,
                                          entry_state=entry_state, tracker=tracker, state=state)
            except Exception:
                log.exception("Az indexszkenneles hibara futott")
                report.failures.append("indexszkenneles")
        # Visszahuzas (RSI2) - es a homokozo, ha a visszahuzas-motorral fut
        for sid in PULLBACK_STRATEGIES:
            switch = fresh.strategies.get(sid)
            if not switch.enabled or (sid == "sandbox" and switch.engine != "pullback"):
                continue
            try:
                dispatcher.settings = fresh
                pullback_cycle(fresh, sid, switch.params, dispatcher, tracker, entry_state, read_only=dry_run)
            except Exception:
                log.exception("A(z) %s strategia hibara futott", sid)
                report.failures.append(f"{config.STRATEGY_LABELS[sid]} jelzesek")
        try:
            report.broker_events = broker_cycle(dispatcher, final_bar=True, read_only=dry_run,
                                                 tracker=tracker)
        except Exception:
            log.exception("A paper szinkron hibara futott")
            report.failures.append("paper szinkron")
        try:
            log.info("Takaritas: %s", housekeeping.run(settings))
        except Exception:
            log.exception("A takaritas hibara futott")
            report.failures.append("takaritas")
        # sikertelen elemzest a kovetkezo inditas potol
        if not dry_run and not {"watchlist elemzes", "indexszkenneles"} & set(report.failures):
            heartbeat.mark_close_done(day)
        if fresh.alerts.heartbeat:
            try:
                # a triggerbol, nem a scheduler job-jabol: a potlas meg az indulas elott fut
                next_run = triggers["close"].get_next_fire_time(None, datetime.now(timezone.utc))
                dispatcher.broadcast(heartbeat.build(fresh, report, dispatcher.sent, errors, started,
                                                     next_run=next_run))
            except Exception:
                log.exception("Az eletjel nem ment ki")
        dispatcher.sent.clear()
        errors.reset()

    triggers = schedule_triggers(settings)
    scheduler.add_job(intraday_job, triggers["intraday"], id="intraday", max_instances=1, coalesce=True)
    scheduler.add_job(broker_job, triggers["broker"], id="broker", max_instances=1, coalesce=True)
    # a percenkenti job miatt az APScheduler "Running job" soraival telne a naplo
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

    def preopen_job() -> None:
        """Nyitas elott: az elozo esti jelzesek ujraellenorzese a veglegesitett adaton."""
        from . import revalidate

        try:
            results = revalidate.revalidate(_fresh_settings(), dispatcher=None if dry_run else dispatcher)
            if results:
                log.info("Nyitas elotti ellenorzes: %s",
                         ", ".join(f"{r['symbol']} {r['decision']}" for r in results))
        except Exception:
            log.exception("A nyitas elotti ellenorzes hibara futott")

    scheduler.add_job(preopen_job, triggers["preopen"], id="preopen", max_instances=1, coalesce=True)
    scheduler.add_job(close_job, triggers["close"], id="close", max_instances=1, coalesce=True)

    log.info(
        "Monitor elindult. Watchlist: %s | frissites: %d percenkent | dry-run: %s",
        ", ".join(load_watchlist().symbols),
        settings.data.refresh_minutes,
        dry_run,
    )
    log.info("Leallitas: Ctrl+C")

    if settings.alerts.heartbeat and not dry_run and heartbeat.start_notice_due():
        try:
            next_close = triggers["close"].get_next_fire_time(None, datetime.now(timezone.utc))
            dispatcher.broadcast(heartbeat.start_alert(len(default_symbols(settings)), next_close))
        except Exception:
            log.exception("Az indulasi uzenet nem ment ki")

    # Ha a legutobbi zaras utani futas kimaradt (a monitor / a szerver nem futott 16:15-kor),
    # most potoljuk: belepesi jelzes, ertesites, paper bekuldes. Az utemezo elott, hogy ne
    # fusson parhuzamosan a napkozbeni korrel.
    missed = None if dry_run else heartbeat.missed_close()
    if missed is not None:
        log.warning("Kimaradt a %s-i zaras utani futas (a monitor nem futott) - potlas most.",
                    f"{missed:%Y-%m-%d}")
        close_job(missed)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Monitor leallitva.")
