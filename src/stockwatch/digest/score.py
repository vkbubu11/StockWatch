"""Reggeli osszefoglalo - szabalyalapu fontossag es plafonok.

pontszam = suly[tipus] x modositok:
  * Sajat pozicio: x holding_mult (alap 1,5)
  * trigger-kozelseg: x 1 / max(ATR-tavolsag, 0,25) - minel kozelebb, annal fontosabb (legfeljebb x4)
  * lenyeges 8-K: x2;  gyorsjelentes 1 napon belul: x2
Plafon: papironkent `per_symbol` sor, osszesen `max_items`; a sorrend determinisztikus.
"""
from __future__ import annotations

from .detect import Event

WEIGHT_KEY = {"regime_change": "regime_change", "own_trend": "own_trend", "pattern_triggered": "pattern_triggered",
              "pattern_new": "pattern_new", "pattern_gone": "pattern_gone", "near_trigger": "near_trigger",
              "level_break": "level_break", "level_lost": "level_break", "earnings_soon": "earnings_soon",
              "filing_8k": "filing_8k", "sentiment_flip": "sentiment_flip", "material_news": "material_news",
              "candidate_in": "candidate_in", "candidate_out": "candidate_out"}
TYPE_ORDER = list(WEIGHT_KEY)          # egyenlo pontszamnal ez a sorrend


def score(events: list[Event], cfg) -> list[Event]:
    weights = cfg.weights.model_dump()
    for ev in events:
        value = weights[WEIGHT_KEY[ev.type]]
        if ev.type == "near_trigger":
            value *= 1 / max(float(ev.values.get("dist_atr") or 0.0), 0.25)
        elif ev.type == "filing_8k" and ev.values.get("material"):
            value *= 2
        elif ev.type == "earnings_soon" and (ev.values.get("days") or 0) <= 1:
            value *= 2
        if ev.holding:
            value *= cfg.holding_mult
        ev.score = round(value, 4)
    return events


def _key(ev: Event) -> tuple:
    return (-ev.score, ev.symbol or "", TYPE_ORDER.index(ev.type), repr(sorted(ev.values.items())))


def select(events: list[Event], cfg, symbols: list[str]) -> dict:
    """{market, items, omitted, unchanged}: a piaci esemenyek elol, utana a papirok - mindketto a plafonon belul."""
    market = sorted((e for e in events if e.symbol is None), key=_key)
    rest = sorted((e for e in events if e.symbol is not None), key=_key)
    room = max(cfg.max_items - len(market[: cfg.max_items]), 0)
    per_symbol: dict[str, int] = {}
    items, omitted = [], 0
    for ev in rest:
        if per_symbol.get(ev.symbol, 0) >= cfg.per_symbol or len(items) >= room:
            omitted += 1
            continue
        per_symbol[ev.symbol] = per_symbol.get(ev.symbol, 0) + 1
        items.append(ev)
    omitted += max(len(market) - cfg.max_items, 0)
    touched = {e.symbol for e in events if e.symbol is not None}
    return {"market": market[: cfg.max_items], "items": items, "omitted": omitted,
            "unchanged": sorted(s for s in symbols if s not in touched)}
