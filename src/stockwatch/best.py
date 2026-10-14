""""Legjobb belepo most" - a feluleti gomb: az adott pillanatban legjobb long es short belepo a
beallitasokban valasztott listan (scan.best_list).

  * a legfrissebb arakkal szamol: letolti a mai (nyitvatartas alatt meg alakulo) gyertyat is
  * long: a belepesi motor (entry.py) - ugyanazok a szurok, pontszam, terv, mint az ertesiteseknel
  * short: a KISERLETI short motor (short.py) - nincs backtesttel igazolva
  * nagy listan (indexek, ~520 papir) a teljes elemzes a gyenge szerveren tul lassu lenne: ilyenkor
    az esti indexszkenneles legjobb jeloltjeit (long es short) szamolja ujra elo arral, a watchlisttel
    egyutt. Ha meg nem volt indexszkenneles, a teljes listat nezi at (lassu, de mukodik).

Semmit nem kuld ki es nem kereskedik - csak megmutatja.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from . import entry, feed, indicators, market, monitor, shadow, store
from . import short as short_mod
from .config import Settings, load_watchlist
from .forecast import Calibration, analyse

log = logging.getLogger(__name__)

LISTS = {"watchlist": "Watchlist", "nasdaq100": "Nasdaq-100", "sp500": "S&P 500",
         "indexes": "Nasdaq-100 + S&P 500", "all": "Watchlist + Nasdaq-100 + S&P 500"}
LIVE_LIMIT = 80          # ennel tobb papirnal az esti eredmenyek legjobbjait frissitjuk elo
SHORTLIST = 25           # oldalankent ennyi jelolt az esti eredmenybol
OTHERS = 5               # a legjobb mellett ennyi tovabbi jelolt / majdnem-jelolt


def list_symbols(name: str, settings: Settings) -> list[str]:
    from . import indexes

    watch = list(load_watchlist().symbols)
    if name == "watchlist":
        return watch
    members = indexes.IndexMembers(settings)
    n100 = members.members("nasdaq100", allow_network=True)
    sp500 = members.members("sp500", allow_network=True)
    if name == "nasdaq100":
        return n100
    if name == "sp500":
        return sp500
    union = list(dict.fromkeys(n100 + sp500))
    return union if name == "indexes" else list(dict.fromkeys(watch + union))


def shortlist(settings: Settings, symbols: list[str]) -> list[str]:
    """Az esti indexszkenneles legjobb long es short jeloltjei (a lista tagjai kozul)."""
    path = settings.index_scan_path
    if not path.exists():
        return []
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    allowed = set(symbols)

    def top(items: list[dict]) -> list[str]:
        ranked = sorted((d for d in items if d.get("symbol") in allowed),
                        key=lambda d: (bool(d.get("qualifies")), not d.get("failed"), d.get("score", 0)), reverse=True)
        return [d["symbol"] for d in ranked][:SHORTLIST]

    return list(dict.fromkeys(top(result.get("signals") or []) + top(result.get("shorts") or [])))


def _refresh(symbols: list[str], settings: Settings) -> None:
    watch = set(load_watchlist().symbols)
    own = [s for s in symbols if s in watch]
    rest = [s for s in symbols if s not in watch]
    if own:
        feed.update(own, settings)
    if rest:
        feed.update(rest, settings, universe=True)


def _frame(symbol: str, settings: Settings) -> pd.DataFrame:
    df = store.load(symbol, settings)
    return df if not df.empty else store.load(symbol, settings, universe=True)


def _side(items: list[dict]) -> dict:
    """A legjobb belepo + tovabbi jeloltek + majdnem-jeloltek (miert buktak el)."""
    entries = [d for d in items if d["qualifies"] and d["kind"] not in ("setup",)]
    setups = [d for d in items if d["qualifies"] and d["kind"] == "setup"]
    near = [d for d in items if not d["qualifies"]]
    key = lambda d: d["score"]                                   # noqa: E731
    entries.sort(key=key, reverse=True)
    setups.sort(key=key, reverse=True)
    near.sort(key=lambda d: (len(d.get("failed") or []), -d["score"]))
    return {"best": entries[0] if entries else None, "others": entries[1:1 + OTHERS],
            "setups": setups[:OTHERS], "near": near[:OTHERS], "count": len(entries)}


def find_best(settings: Settings, progress=None, refresh: bool = True) -> dict:
    progress = progress or (lambda message: None)
    name = settings.scan.best_list
    symbols = list_symbols(name, settings)
    mode, candidates = "live", symbols
    if len(symbols) > LIVE_LIMIT:
        picked = shortlist(settings, symbols)
        if picked:
            mode = "shortlist"
            candidates = list(dict.fromkeys(picked + [s for s in load_watchlist().symbols if s in set(symbols)]))
        else:
            mode = "full"
    if refresh:
        progress(f"Friss arak: {len(candidates)} papir...")
        _refresh(candidates, settings)
    ctx = market.load_context(settings, refresh=refresh)
    earnings = market.EarningsCalendar(settings, allow_network=refresh)
    calibration = Calibration.load(settings.calibration_path)
    cfg = settings.entry

    longs, shorts, last_bar = [], [], None
    for n, symbol in enumerate(candidates, 1):
        if n % 10 == 1:
            progress(f"Elemzes: {n}/{len(candidates)} ({symbol})...")
        try:
            df = _frame(symbol, settings)
            if df.empty or len(df) < 220:
                continue
            enriched = indicators.enrich(df, settings.structure.atr_period)
            last_bar = max(last_bar, enriched.index[-1]) if last_bar is not None else enriched.index[-1]
            forecast = analyse(symbol, enriched, settings, calibration)
            bench = ctx.bench_for(symbol)
            signals = monitor._evaluate_entries(symbol, enriched, forecast, settings, calibration, ctx, earnings)
            shadow.score_signals(signals, enriched, settings, bench)
            for s in signals:
                d = s.to_dict()
                d.update(side="long", qualifies=s.qualifies(cfg), failed=[c.name for c in s.failed_checks],
                         label=entry.KIND_LABELS.get(s.kind, s.kind), exit_lines=entry.exit_plan_lines(s.plan)[2:])
                longs.append(d)
            bear = [m for m in forecast.matches if short_mod.classify(m, enriched, cfg)]
            if bear:
                date = earnings.next_date(symbol) if earnings is not None else None
                shorts += [s.to_dict(cfg) for s in short_mod.evaluate(symbol, enriched, bear, settings, bench, date)]
        except Exception:
            log.exception("Legjobb belepo: %s elemzese hibara futott", symbol)

    open_now = monitor.is_market_open()
    return {
        "list": name, "label": LISTS.get(name, name), "symbols": len(symbols), "evaluated": len(candidates),
        "mode": mode, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_bar": f"{last_bar:%Y-%m-%d}" if last_bar is not None else None, "market_open": open_now,
        "long": _side(longs), "short": _side(shorts),
        "notes": [n for n in (
            "A piac nyitva van: a mai gyertya meg alakul, a jelzes zarasig valtozhat." if open_now else None,
            f"Nagy lista: az esti indexszkenneles legjobb {len(candidates)} jeloltjet szamoltuk ujra friss arral."
            if mode == "shortlist" else None,
            "Meg nem volt indexszkenneles: a teljes listat neztuk at (ezert tartott tovabb)." if mode == "full" else None,
            "A short oldal KISERLETI: nincs backtesttel igazolva, a paper trading nem kuldi be.",
        ) if n],
    }
