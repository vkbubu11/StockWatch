"""Napi, POINT-IN-TIME hir-jellemzok egy papirra.

A `t` napi dontes (zaras utan) csak a `t` nap 16:00 (New York) ELOTT megjelent hireket latja; a 16:00
utaniak a kovetkezo kereskedesi nap dontesehez tartoznak. Ugyanez az elv, mint az arfolyamnal.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .model import Article

MARKET_TZ = "America/New_York"
LOOKBACK_DAYS = 3                # kereskedesi nap
BASELINE_DAYS = 60               # a hirvolumen z-ertekehez
HALF_LIFE_DAYS = 1.0             # a frissebb hir nagyobb sullyal


def decision_cutoff(day) -> pd.Timestamp:
    """A `day` napi zaras (16:00 New York) UTC-ben."""
    d = pd.Timestamp(day).normalize()
    d = d.tz_localize(None) if d.tzinfo is not None else d
    return (d + pd.Timedelta(hours=16)).tz_localize(MARKET_TZ).tz_convert("UTC")


def decision_day(published: pd.Timestamp) -> pd.Timestamp:
    """Az elso kereskedesi nap, amelynek zarasi dontese mar latja a hirt (16:00 NY utan: a kovetkezo)."""
    t = published.tz_convert(MARKET_TZ)
    d = t.normalize().tz_localize(None)
    if t.hour >= 16 or d.weekday() >= 5:
        d = d + pd.offsets.BDay(1)
    return pd.Timestamp(d)


def window_start(day, days: int) -> pd.Timestamp:
    return decision_cutoff(pd.Timestamp(day).normalize() - pd.offsets.BDay(days))


def covered(coverage: dict, start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    """Mely forrasok fedik le teljesen a [start, end) idoszakot."""
    out = []
    for source, spans in (coverage or {}).items():
        if any(pd.Timestamp(a) <= start and pd.Timestamp(b) >= end for a, b in spans):
            out.append(source)
    return out


def _label_counts(arts: list[Article]) -> tuple[int, int, int]:
    neg = sum(1 for a in arts if a.score.get("label") == "negative")
    pos = sum(1 for a in arts if a.score.get("label") == "positive")
    return neg, pos, len(arts) - neg - pos


def daily(articles: list[Article], day, coverage: dict | None = None) -> dict:
    cutoff = decision_cutoff(day)
    start3, start1 = window_start(day, LOOKBACK_DAYS), window_start(day, 1)
    visible = [a for a in articles if a.published < cutoff]                      # SOHA a cutoff utaniak
    w3 = [a for a in visible if a.published >= start3]
    w1 = [a for a in w3 if a.published >= start1]
    sources3 = covered(coverage or {}, start3, cutoff)
    news3 = [a for a in w3 if a.kind == "news"]
    neg, pos, _ = _label_counts(w3)
    weights = [0.5 ** ((cutoff - a.published).total_seconds() / 86400 / HALF_LIFE_DAYS) for a in news3]
    sent = (float(np.dot(weights, [a.score.get("polarity", 0.0) for a in news3]) / sum(weights))
            if weights and sum(weights) > 0 else None)
    material = sorted((a for a in w3 if a.score.get("materiality", 0) >= 2 and a.score.get("direction", 0) < 0),
                      key=lambda a: (a.score["materiality"], a.published_at), reverse=True)
    # hirvolumen z-ertek: csak ha a teljes alapidoszak le van fedve (kulonben "nem ismert")
    base_start = window_start(day, BASELINE_DAYS)
    z = None
    if covered(coverage or {}, base_start, cutoff):
        days = pd.bdate_range(pd.Timestamp(day).normalize() - pd.offsets.BDay(BASELINE_DAYS), pd.Timestamp(day).normalize())
        counts = pd.Series(0, index=days)
        for a in visible:
            if a.kind == "news" and a.published >= base_start:
                d = decision_day(a.published)
                if d in counts.index:
                    counts[d] += 1
        hist, today = counts.iloc[:-1], counts.iloc[-1]
        sd = hist.std(ddof=1)
        z = float((today - hist.mean()) / sd) if sd and not math.isnan(sd) and sd > 0 else None
    return {
        "as_of": f"{pd.Timestamp(day):%Y-%m-%d}", "cutoff": cutoff.isoformat(),
        "coverage": bool(sources3), "sources": sources3,
        "count_1d": len(w1), "count_3d": len(w3), "news_3d": len(news3), "filings_3d": len(w3) - len(news3),
        "neg_3d": neg, "pos_3d": pos, "neg_share_3d": (neg / len(w3)) if w3 else None,
        "sent_mean_3d": None if sent is None else round(sent, 3), "news_volume_z": None if z is None else round(z, 2),
        "material_neg_3d": bool(material),
        "material": [{"event": a.score.get("event"), "materiality": a.score.get("materiality"), "title": a.title[:160],
                      "published_at": a.published_at, "source": a.source, "url": a.url} for a in material[:3]],
        "latest": [{"title": a.title[:160], "published_at": a.published_at, "label": a.score.get("label"),
                    "event": a.score.get("event"), "source": a.source, "url": a.url}
                   for a in sorted(w3, key=lambda a: a.published_at, reverse=True)[:5]],
    }
