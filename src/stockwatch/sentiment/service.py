"""Hirek frissitese es a jelzesek kiegeszitese (arnyek-uzem: nem dont)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..config import Settings
from . import features, store
from .sources import alpaca, edgar, yahoo

log = logging.getLogger(__name__)

SOURCES = {"edgar": edgar, "yahoo": yahoo, "alpaca": alpaca}
CHECK_NAME = "Hirek"
EVENT_LABELS = {
    "csod": "csod", "going_concern": "mukodokepesseg kerdeses", "helyesbites": "konyvelesi helyesbites",
    "kivezetes": "tozsdei kivezetes", "kivezetesi_ertesites": "kivezetesi ertesites", "fda_negativ": "FDA-elutasitas",
    "short_jelentes": "short-jelentes / csalas vadja", "elorejelzes_csokkentes": "elorejelzes-csokkentes",
    "elorejelzes_emeles": "elorejelzes-emeles", "leminosites": "leminosites", "felminosites": "felminosites",
    "eredmeny_elmarad": "elmaradt a varakozastol", "eredmeny_felulmul": "felulmulta a varakozast",
    "kibocsatas": "reszvenykibocsatas", "hig_kibocsatas": "reszvenykibocsatas (8-K 3.02)",
    "osztalek_csokkentes": "osztalekcsokkentes", "per_vizsgalat": "per / hatosagi vizsgalat",
    "visszahivas": "termekvisszahivas", "vezeto_tavozik": "vezeto tavozik", "vezeto_valtas": "vezetovaltas (8-K 5.02)",
    "fda_pozitiv": "FDA-engedely", "leepites": "leepites", "felvasarlas": "felvasarlas / fuzio",
    "ertekvesztes": "ertekvesztes (8-K 2.06)", "hitel_gyorsitas": "hitel lejaratanak gyorsitasa (8-K 2.04)",
    "konyvvizsgalo_csere": "konyvvizsgalo-csere", "leepites_koltseg": "leepitesi koltseg (8-K 2.05)",
    "kiberbiztonsagi_incidens": "kiberbiztonsagi incidens",
}


def _company(symbol: str) -> str | None:
    return edgar.company_name(symbol)


def refresh(symbol: str, settings: Settings, since=None, sources: list[str] | None = None, force: bool = False) -> dict:
    """A papir hireinek frissitese a bekapcsolt forrasokbol (forrasonkent cache-idovel)."""
    symbol = symbol.upper()
    cfg = settings.sentiment
    since = pd.Timestamp(since, tz="UTC") if since is not None else \
        pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=cfg.history_days))
    since = since if since.tzinfo is not None else since.tz_localize("UTC")
    data = store.load(symbol)
    out = {}
    for name in sources or cfg.sources:
        mod = SOURCES[name]
        last = data["fetched"].get(name)
        cover = data["coverage"].get(name) or []
        fresh = last and pd.Timestamp(last) > pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=cfg.cache_minutes)
        deep_enough = any(pd.Timestamp(a) <= since for a, _ in cover) or name == "yahoo"
        if fresh and deep_enough and not force:
            out[name] = {"cached": True}
            continue
        try:
            if name == "yahoo":
                arts, coverage = mod.fetch(symbol, since, settings, company=_company(symbol))
            else:
                arts, coverage = mod.fetch(symbol, since, settings)
        except Exception as exc:
            log.warning("Hirforras hiba (%s, %s): %s", name, symbol, exc)
            out[name] = {"error": str(exc)[:200]}
            continue
        out[name] = store.merge(symbol, name, arts, coverage)
    return out


def features_for(symbol: str, day) -> dict:
    data = store.load(symbol)
    return features.daily(store.articles(symbol), day, data["coverage"])


def summary(f: dict | None) -> str:
    """Egysoros, ember altal olvashato osszefoglalo (szuro-reszlet, ertesites)."""
    if not f:
        return "nem ismert"
    if not f.get("coverage") and not f.get("count_3d"):
        return "nincs hirlefedettseg - nem ismert"
    if f.get("material_neg_3d"):
        m = f["material"][0]
        when = pd.Timestamp(m["published_at"]).tz_convert(features.MARKET_TZ)
        more = f" (+{len(f['material']) - 1} tovabbi)" if len(f["material"]) > 1 else ""
        return (f"LENYEGES NEGATIV: {EVENT_LABELS.get(m['event'], m['event'])} ({m['source']}, "
                f"{when:%m-%d %H:%M} NY){more} - 3 napban {f['count_3d']} hir")
    if not f.get("count_3d"):
        return "nincs relevans hir 3 napban" + ("" if f.get("coverage") else " (a lefedettseg hianyos)")
    tone = f.get("sent_mean_3d")
    word = "semleges" if tone is None or abs(tone) < 0.1 else ("pozitiv" if tone > 0 else "negativ")
    z = f" · volumen z={f['news_volume_z']:+.1f}" if f.get("news_volume_z") is not None else ""
    partial = "" if f.get("coverage") else " · reszleges lefedettseg"
    return f"3 napban {f['count_3d']} hir ({f['neg_3d']} negativ, {f['pos_3d']} pozitiv) · hangulat {word}{z}{partial}"


def annotate(signals: list, settings: Settings, refresh_news: bool = True) -> int:
    """A jelzesek kiegeszitese hir-jellemzokkel: `signal.news` + egy NEM DONTO "Hirek" szuro.
    Hibanal csendben kihagyja (a jelzes ettol nem bukhat el). Visszaadja a kiegeszitett jelzesek szamat."""
    cfg = settings.sentiment
    if not cfg.enabled or not signals:
        return 0
    from ..entry import Check

    done, refreshed = 0, set()
    for s in signals:
        if any(c.name == CHECK_NAME for c in s.checks):
            continue
        try:
            if refresh_news and s.symbol not in refreshed and len(refreshed) < cfg.max_symbols_per_cycle:
                refresh(s.symbol, settings)
                refreshed.add(s.symbol)
            f = features_for(s.symbol, s.as_of)
        except Exception as exc:
            log.warning("Hir-jellemzok (%s): %s", s.symbol, exc)
            continue
        s.news = f
        s.checks.append(Check(CHECK_NAME, None, summary(f) + " - tajekoztato, nem dont"))
        done += 1
    return done
