""""Legjobb belepo most" - a feluleti gomb: az adott pillanatban legjobb long es short belepo a
beallitasokban valasztott listan (scan.best_list).

  * a legfrissebb arakkal szamol: letolti a mai (nyitvatartas alatt meg alakulo) gyertyat is
  * long: a belepesi motor (entry.py) - ugyanazok a szurok, pontszam, terv, mint az ertesiteseknel
  * short: a KISERLETI short motor (short.py) - nincs backtesttel igazolva
  * nagy listan (indexek, ~520 papir) a teljes elemzes a gyenge szerveren tul lassu lenne: ilyenkor
    az esti indexszkenneles legjobb jeloltjeit (long es short) szamolja ujra elo arral, a watchlisttel
    egyutt. Ha meg nem volt indexszkenneles, a teljes listat nezi at (lassu, de mukodik).

Szektorvalaszto (#74): `find_best(..., sector=...)` a lista adott GICS-szektorba eso tagjain keres, es
oldalankent a 3 legjobb belepot adja. Sorrend: a 0-100 pontszam; ha ket jelolt pontszama 5 ponton belul
van, az kerul elore, amelyiknek az alakzat-historiabol becsult varhato erteke (R) 95%-os also hatara
magasabb. Gyengebb jeloltet kitoltesre nem enged be. A short oldalra nincs igazolt historia.

Semmit nem kuld ki es nem kereskedik - csak megmutatja.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import pandas as pd

from . import entry, feed, indicators, market, monitor, sectors, shadow, store
from . import short as short_mod
from .config import Settings, load_watchlist
from .forecast import Calibration, analyse

log = logging.getLogger(__name__)

LISTS = {"watchlist": "Watchlist", "nasdaq100": "Nasdaq-100", "sp500": "S&P 500",
         "indexes": "Nasdaq-100 + S&P 500", "all": "Watchlist + Nasdaq-100 + S&P 500"}
LIVE_LIMIT = 80          # ennel tobb papirnal az esti eredmenyek legjobbjait frissitjuk elo
SHORTLIST = 25           # oldalankent ennyi jelolt az esti eredmenybol
OTHERS = 5               # a legjobb mellett ennyi tovabbi jelolt / majdnem-jelolt
TOP = 3                  # szektoronkent ennyi belepo oldalankent (#74)
SCORE_MARGIN = 5.0       # ennyi ponton belul a varhato ertek also hatara dont a sorrendrol


def list_symbols(name: str, settings: Settings, allow_network: bool = True) -> list[str]:
    from . import indexes

    watch = list(load_watchlist().symbols)
    if name == "watchlist":
        return watch
    members = indexes.IndexMembers(settings)
    n100 = members.members("nasdaq100", allow_network=allow_network)
    sp500 = members.members("sp500", allow_network=allow_network)
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


def history_stats(calibration: Calibration, pattern: str, direction: str, min_samples: int) -> dict | None:
    """Az alakzat backtestelt (geometriai) historiaja: nyeresi arany Wilson-savval, varhato ertek (R) 95%-os
    savval. Kevesebb mint `min_samples` mintanal None: nincs eleg adat (nem talalunk ki szamot)."""
    row = calibration.data.get("patterns", {}).get(f"{pattern}|{direction}")
    n = int(row.get("samples", 0)) if row else 0
    if not row or n < min_samples or row.get("hit_rate") is None:
        return None
    p = float(row["hit_rate"])
    z = 1.96
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    ev = float(row.get("expectancy_r", 0.0))
    sd, en = row.get("expectancy_sd"), int(row.get("expectancy_n") or 0)
    se = sd / math.sqrt(en) if sd is not None and en >= 2 else None
    return {"samples": n, "hit_rate": round(p, 4), "hit_low": round(max(centre - half, 0.0), 4),
            "hit_high": round(min(centre + half, 1.0), 4), "ev": round(ev, 3),
            "ev_low": None if se is None else round(ev - z * se, 3),
            "ev_high": None if se is None else round(ev + z * se, 3)}


def _ev_low(d: dict) -> float:
    h = d.get("hist") or {}
    return h["ev_low"] if h.get("ev_low") is not None else float("-inf")


def rank(entries: list[dict], margin: float = SCORE_MARGIN) -> list[dict]:
    """Sorrend: a pontszam dont; a legjobb pontszamtol `margin` ponton belul levok kozul az kerul elore, amelyiknek
    a varhato ertek also 95%-os hatara magasabb (egyenlosegnel a pontszam). Determinisztikus, mohó kivalasztas."""
    rest = sorted(entries, key=lambda d: (-d["score"], d.get("symbol", "")))
    out: list[dict] = []
    while rest:
        top = rest[0]["score"]
        close = [d for d in rest if top - d["score"] <= margin]
        pick = max(close, key=lambda d: (_ev_low(d), d["score"]))
        out.append(pick)
        rest.remove(pick)
    return out


def _side(items: list[dict], top: int = 1) -> dict:
    """A legjobb belepo(k) + tovabbi jeloltek + majdnem-jeloltek (miert buktak el).

    `top` > 1 (szektorvalaszto): a `top` lista legfeljebb ennyi belepoesre kesz jeloltet ad - ha kevesebb van,
    nem tolti fel gyengebbel (az elokeszuletek es a majdnem-jeloltek kulon listaban maradnak)."""
    entries = rank([d for d in items if d["qualifies"] and d["kind"] not in ("setup",)])
    setups = [d for d in items if d["qualifies"] and d["kind"] == "setup"]
    near = [d for d in items if not d["qualifies"]]
    setups.sort(key=lambda d: d["score"], reverse=True)
    near.sort(key=lambda d: (len(d.get("failed") or []), -d["score"]))
    return {"best": entries[0] if entries else None, "top": entries[:max(top, 1)],
            "others": entries[max(top, 1):max(top, 1) + OTHERS],
            "setups": setups[:OTHERS], "near": near[:OTHERS], "count": len(entries)}


def bull_market(ctx) -> bool | None:
    """Bikapiac: mindket rezsim-index (QQQ, S&P 500) zaroja az SMA200 felett. None: nincs adat."""
    oks = []
    for symbol in market.REGIME_INDEXES:
        frame = ctx.benches.get(symbol) if ctx is not None else None
        if frame is None or frame.empty:
            return None
        oks.append(market.market_regime(frame, frame.index[-1]).ok)
    if any(o is None for o in oks):
        return None
    return all(oks)


def find_best(settings: Settings, progress=None, refresh: bool = True, sector: str | None = None,
              top: int | None = None) -> dict:
    """`sector`: csak a lista adott GICS-szektorba eso tagjai (sectors.OTHER: a besorolatlanok); ilyenkor
    oldalankent `top` (alap: 3) belepot ad. Nelkule a regi viselkedes (1 legjobb + tovabbiak)."""
    progress = progress or (lambda message: None)
    name = settings.scan.best_list
    symbols = list_symbols(name, settings)
    if sector:
        if sector not in sectors.SECTOR_ETFS and sector != sectors.OTHER:
            raise ValueError(f"Ismeretlen szektor: {sector}")
        progress("Szektorok...")
        mapping = sectors.sector_map(symbols, allow_network=refresh)
        symbols = sectors.members(sector, mapping)
        top = top or TOP
    top = top or 1
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
    min_samples = settings.forecast.min_samples_for_probability
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
                         label=entry.KIND_LABELS.get(s.kind, s.kind), exit_lines=entry.exit_plan_lines(s.plan)[2:],
                         hist=history_stats(calibration, s.pattern.name, "bullish", min_samples))
                longs.append(d)
            bear = [m for m in forecast.matches if short_mod.classify(m, enriched, cfg)]
            if bear:
                date = earnings.next_date(symbol) if earnings is not None else None
                # a short motor nincs backtesttel igazolva: nincs "hist" (a felulet: "nincs igazolt adat")
                shorts += [s.to_dict(cfg) | {"hist": None}
                           for s in short_mod.evaluate(symbol, enriched, bear, settings, bench, date)]
        except Exception:
            log.exception("Legjobb belepo: %s elemzese hibara futott", symbol)

    open_now = monitor.is_market_open()
    bull = bull_market(ctx)
    etf = None
    if sector in sectors.SECTOR_ETFS:
        symbol = sectors.SECTOR_ETFS[sector]
        if refresh:
            try:
                feed.update([symbol], settings, universe=True)
            except Exception as exc:
                log.warning("Szektor-ETF (%s) frissitese sikertelen: %s", symbol, exc)
        etf = {"symbol": symbol, **(sectors.etf_status(store.load(symbol, settings, universe=True)) or {})}
    short_side = _side(shorts, top)
    return {
        "list": name, "label": LISTS.get(name, name), "symbols": len(symbols), "evaluated": len(candidates),
        "sector": sector, "top_n": top, "bull_market": bull, "etf": etf,
        "mode": mode, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_bar": f"{last_bar:%Y-%m-%d}" if last_bar is not None else None, "market_open": open_now,
        "long": _side(longs, top), "short": short_side,
        "short_blocked": bool(bull) and not short_side["count"],
        "notes": [n for n in (
            "A piac nyitva van: a mai gyertya meg alakul, a jelzes zarasig valtozhat." if open_now else None,
            f"Nagy lista: az esti indexszkenneles legjobb {len(candidates)} jeloltjet szamoltuk ujra friss arral."
            if mode == "shortlist" else None,
            "Meg nem volt indexszkenneles: a teljes listat neztuk at (ezert tartott tovabb)." if mode == "full" else None,
            "A short oldal KISERLETI: nincs backtesttel igazolva, a paper trading nem kuldi be.",
            "Ebben a szektorban nincs papir a valasztott listan." if sector and not symbols else None,
        ) if n],
    }
