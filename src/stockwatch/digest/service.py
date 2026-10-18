"""Reggeli osszefoglalo - egy futas.

    python cli.py digest [--print]          (a monitor 06:30-kor, Europe/Budapest, maga is futtatja)

Lepesek: a watchlist + a Sajat poziciok pillanatkepe (detect) -> valtozasok az elozo osszefoglalohoz
kepest -> pontozas es plafon (score) -> mondatok (render) -> mentes (store) -> opcionalisan Discord a
meglevo ertesitesi retegen. Nehez szamitas nincs (papironkent egy elemzes a tarolt adaton); halozat csak
a 8-K bejelentesek frissitesehez kell (EDGAR, papironkent egy keres).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from .. import indicators, market, sessions, store as price_store, usage, workspace
from ..config import Settings, load_settings
from ..forecast import Calibration, analyse
from . import detect, render, score, store

log = logging.getLogger(__name__)

VERSION = 1
BUDAPEST_TZ = "Europe/Budapest"


def for_day(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """A kereskedesi nap (NY datum), amelyik ELE az osszefoglalo szol: zaras elott a mai, utana a kovetkezo."""
    now = pd.Timestamp(now).tz_convert(sessions.MARKET_TZ) if now is not None else sessions.now_ny()
    day = now.tz_localize(None).normalize()
    if (now.hour, now.minute) >= sessions.CLOSE_GRACE:
        day += pd.Timedelta(days=1)
    for _ in range(10):                         # hetvege, unnepnap
        if sessions.trading_day(day) is not False:
            return day
        day += pd.Timedelta(days=1)
    return day


def _frame(symbol: str, settings: Settings, now: pd.Timestamp, universe: bool = False) -> pd.DataFrame | None:
    df = price_store.load(symbol, settings, universe=universe)
    if df.empty and not universe:
        df = price_store.load(symbol, settings, universe=True)
    if not df.empty:                                    # multbeli `now` (ujrageneralas): kesobbi gyertya nem latszik
        ny = now.tz_convert(sessions.MARKET_TZ)
        df = sessions.completed_bars(df.loc[: ny.tz_localize(None)], ny)
    return None if df is None or df.empty else df


def _news(symbol: str, day: pd.Timestamp, now: pd.Timestamp, deadband: float) -> tuple[list[dict], dict]:
    from ..sentiment import features, store as news_store

    try:
        articles = news_store.articles(symbol)
        coverage = news_store.load(symbol).get("coverage") or {}
    except Exception:
        log.exception("Osszefoglalo: %s hirei nem olvashatok", symbol)
        return [], detect.sentiment_state(None, deadband)
    return (detect.filing_states(articles, now),
            detect.sentiment_state(features.daily(articles, day, coverage), deadband))


def refresh_filings(symbols: list[str], settings: Settings) -> int:
    """A 8-K bejelentesek frissitese (EDGAR, a sentiment reteg sajat kereskorlatjaval)."""
    cfg = settings.sentiment
    if not cfg.enabled or "edgar" not in cfg.sources:
        return 0
    from ..sentiment import service as news

    done = 0
    for symbol in symbols:
        try:
            news.refresh(symbol, settings, sources=["edgar"])
            done += 1
        except Exception:
            log.exception("Osszefoglalo: %s bejelentesei nem frissithetok", symbol)
    return done


def snapshot(settings: Settings, ws: str = workspace.DEFAULT, now: pd.Timestamp | None = None) -> dict:
    """A mai allapot - csak tarolt adatbol (lasd detect.py)."""
    from ..sentiment import features

    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now if now.tzinfo else now.tz_localize("UTC")
    cfg = settings.digest
    day = for_day(now)
    held = set(workspace.holding_symbols(settings, ws))
    symbols = list(dict.fromkeys(workspace.watchlist(ws) + sorted(held)))

    frames = {s: _frame(s, settings, now, universe=True) for s, _ in detect.MARKET}
    bars = {s: _frame(s, settings, now) for s in symbols}
    last_bars = [df.index[-1] for df in list(frames.values()) + list(bars.values()) if df is not None]
    as_of = max(last_bars) if last_bars else None
    session = f"{as_of:%Y-%m-%d}" if as_of is not None else None

    candidates, known = detect.ledger_candidates(session, set(symbols)) if session else ({}, [])
    momentum = detect.momentum_candidates(settings, session) if session else None
    if momentum is not None:
        known = sorted(set(known) | {"momentum"})
        for symbol in momentum & set(symbols):
            candidates[symbol] = sorted(set(candidates.get(symbol, [])) | {"momentum"})

    calibration = Calibration.load(settings.calibration_path)
    earnings = market.EarningsCalendar(settings, allow_network=False, read_only=True)
    states = {}
    for symbol in symbols:
        df = bars.get(symbol)
        if df is None or len(df) < 60:
            continue
        try:
            enriched = indicators.enrich(df, settings.structure.atr_period)
            forecast = analyse(symbol, enriched, settings, calibration)
            filings, sentiment = _news(symbol, enriched.index[-1], now, cfg.sentiment_deadband)
            states[symbol] = detect.symbol_state(
                symbol, enriched, forecast, holding=symbol in held,
                earnings_date=earnings.next_date(symbol, as_of=day.date()), for_day=day, filings=filings,
                sentiment=sentiment, candidates=candidates.get(symbol, []))
        except Exception:
            log.exception("Osszefoglalo: %s allapota nem keszult el", symbol)
    return {"version": VERSION, "workspace": ws, "for_day": f"{day:%Y-%m-%d}", "as_of": session,
            "created_at": now.tz_convert("UTC").isoformat(timespec="seconds"), "symbols_count": len(symbols),
            "market": detect.market_state({k: v for k, v in frames.items() if v is not None}),
            "symbols": states, "strategies_known": known, "sentiment_days": features.LOOKBACK_DAYS}


def agent_reference(ws: str, prev: dict | None) -> dict | None:
    """Az elozo osszefoglalo ota elkeszult kutatougynok-jelentes (ha van)."""
    try:
        from ..research_agent import store as agent_store

        st = agent_store.latest_done(ws, since=(prev or {}).get("created_at"))
    except Exception:
        log.exception("Osszefoglalo: az ugynok-jelentes nem olvashato")
        return None
    if not st:
        return None
    return {"run_id": st["run_id"], "brief_name": st.get("brief_name") or "-",
            "finalists": int(st.get("finalists") or 0), "tried": int(st.get("tried") or 0)}


def assemble(cur: dict, prev: dict | None, cfg) -> dict:
    """Valtozasok + pontozas + plafon + mondatok a pillanatkephez (tiszta fuggveny, a tesztek is ezt hivjak)."""
    events = score.score(detect.diff(prev, cur, cfg), cfg)
    selected = score.select(events, cfg, sorted(cur.get("symbols", {})))
    record = dict(cur, first=prev is None, previous=prev.get("for_day") if prev else None,
                  events=[e.to_dict() for e in events],
                  selected={"market": [e.to_dict() for e in selected["market"]],
                            "items": [e.to_dict() for e in selected["items"]],
                            "omitted": selected["omitted"], "unchanged": selected["unchanged"]})
    lines = render.render(record)
    record["lines"] = [ln.to_dict() for ln in lines]
    record["text"] = render.to_text(lines)
    return record


def deliver(record: dict, settings: Settings, force: bool = False) -> dict | None:
    """Discord (es a tobbi bekapcsolt csatorna) a meglevo ertesitesi retegen - a jelzesnaploba nem kerul."""
    from ..alerts import Alert, Dispatcher

    if not settings.digest.send_discord or (record.get("delivered") and not force):
        return None
    dispatcher = Dispatcher(settings)
    if dispatcher._in_quiet_hours():
        log.info("Osszefoglalo: csendes idoszak - nem kuldjuk ki (a webes feluleten megvan).")
        return {"skipped": "quiet_hours"}
    head, _, body = record["text"].partition("\n\n")
    result = dispatcher.broadcast(Alert(symbol="STOCKWATCH", title=head, body=body, forecast=None, event="digest"))
    return {"channels": result, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def build(settings: Settings | None = None, ws: str = workspace.DEFAULT, now: pd.Timestamp | None = None,
          refresh: bool = True, send: bool = True, force_send: bool = False) -> dict:
    settings = settings or load_settings()
    with usage.meter(ws, "digest"):
        if refresh:
            held = workspace.holding_symbols(settings, ws)
            refresh_filings(list(dict.fromkeys(workspace.watchlist(ws) + held)), settings)
        cur = snapshot(settings, ws, now)
        prev = store.latest(ws, before=cur["for_day"])
        old = store.load(cur["for_day"], ws)                # ugyanarra a napra ujrafuttatva: ne kuldje ujra
        cur["agent"] = agent_reference(ws, prev)
        record = assemble(cur, prev, settings.digest)
        record["delivered"] = (old or {}).get("delivered")
        if send:
            sent = deliver(record, settings, force=force_send)
            if sent is not None:
                record["delivered"] = sent
        store.save(record, ws)
        store.prune(settings.digest.keep_days, ws)
    return record


def todays_session(now: pd.Timestamp | None = None) -> str | None:
    """A budapesti reggelhez tartozo NY kereskedesi nap (YYYY-MM-DD), vagy None, ha aznap nincs kereskedes
    (hetvege, amerikai unnepnap) - ilyenkor nem keszul osszefoglalo."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now if now.tzinfo else now.tz_localize("UTC")
    local = f"{now.tz_convert(BUDAPEST_TZ):%Y-%m-%d}"
    return local if f"{for_day(now):%Y-%m-%d}" == local else None


def due_now(settings: Settings, ws: str = workspace.DEFAULT, now: pd.Timestamp | None = None) -> bool:
    """Indulaskor potolni kell-e: kereskedesi nap, mar elmult az idopont, meg nincs nyitas, es nincs kesz."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now if now.tzinfo else now.tz_localize("UTC")
    local, ny = now.tz_convert(BUDAPEST_TZ), now.tz_convert(sessions.MARKET_TZ)
    hour, minute = (int(x) for x in settings.digest.time.split(":"))
    day = todays_session(now)
    if not settings.digest.enabled or day is None or (local.hour, local.minute) < (hour, minute):
        return False
    if ny.tz_localize(None).normalize() == pd.Timestamp(day) and (ny.hour, ny.minute) >= (9, 30):
        return False                                    # a nyitas utan mar nem "reggeli"
    return store.load(day, ws) is None
