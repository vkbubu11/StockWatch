"""Reggeli osszefoglalo - allapot-pillanatkep es valtozaserzekeles.

POINT-IN-TIME: a pillanatkep csak TAROLT adatbol all:
  * LEZART napi gyertyak (sessions.completed_bars) - a meg alakulo mai gyertya kimarad
  * a cache-elt gyorsjelentes-naptar (halozat nelkul)
  * a hirtar a dontesi nap 16:00-s (NY) hataraval (sentiment.features.daily)
  * a jelzesnaplo (ledger) es a momentum-rangsor: a strategiai jelolt-tagsag NEM ujraszamolt jelzes,
    hanem amit a monitor a zaron eldontott (egyetlen igazsagforras)

A valtozas az elozo osszefoglalo pillanatkepehez kepest szamit. Elso futaskor (nincs elozo) nincs
valtozas, csak allapot.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import pandas as pd

from .. import ledger, market
from ..config import Settings

log = logging.getLogger(__name__)

REGIME_PERIOD = 200            # market.market_regime: zaro > SMA200 (a papir sajat trendjenel is ez)
MARKET = (("QQQ", "Nasdaq-100 (QQQ)"), ("^GSPC", "S&P 500"))
PATTERN_STATUSES = ("forming", "triggered")
SAME_PATTERN_DAYS = 5          # ugyanaz a minta, ha a nev / irany egyezik es a kezdete ennyi napon belul van
FILING_DAYS = 14               # a pillanatkep ennyi napra visszamenoleg tarolja a 8-K azonositokat
NEW_FILING_DAYS = 4            # "uj" bejelentes: ennyi napnal nem regebbi (a kesve letoltott regit nem hozzuk)
NEAR_STEP_ATR = 0.5            # a trigger fele ennyivel kozeledett -> ujra szol
MATERIAL_ITEMS = frozenset({"1.03", "2.04", "2.06", "3.01", "4.01", "4.02"})   # csod, gyorsitas, ertekvesztes,
#                                                   kivezetes, konyvvizsgalo-csere, konyvelesi helyesbites
LEDGER_LOOKBACK_DAYS = 10


@dataclass
class Event:
    type: str
    symbol: str | None                  # None: piaci szintu esemeny
    values: dict = field(default_factory=dict)
    holding: bool = False
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(d["type"], d.get("symbol"), dict(d.get("values") or {}), bool(d.get("holding")),
                   float(d.get("score") or 0.0))


# ---------------------------------------------------------------------------
# Pillanatkep
# ---------------------------------------------------------------------------
def _num(value, digits: int = 4):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else round(out, digits)


def _sma(close: pd.Series, period: int):
    return _num(close.rolling(period, min_periods=period).mean().iloc[-1]) if len(close) else None


def pattern_states(forecast, close: float, atr: float | None) -> list[dict]:
    out, seen = [], set()
    for m in getattr(forecast, "matches", None) or []:
        if m.status not in PATTERN_STATUSES or m.direction not in ("bullish", "bearish"):
            continue
        key = (m.name, m.direction, f"{m.start_date:%Y-%m-%d}")
        if key in seen:
            continue
        seen.add(key)
        dist = None
        if atr:
            dist = (m.trigger_level - close) / atr if m.direction == "bullish" else (close - m.trigger_level) / atr
        out.append({"name": m.name, "direction": m.direction, "status": m.status,
                    "trigger": _num(m.trigger_level), "start": key[2], "dist_atr": _num(dist, 3)})
    return out


def filing_states(articles, now: pd.Timestamp) -> list[dict]:
    """A legutobbi FILING_DAYS nap 8-K bejelentesei (azonosito, tetelek, lenyeges-e)."""
    since = now - pd.Timedelta(days=FILING_DAYS)
    out = []
    for a in articles or []:
        if a.kind != "filing" or not str(a.form).startswith("8-K"):
            continue
        published = pd.Timestamp(a.published_at)
        published = published if published.tzinfo else published.tz_localize("UTC")
        if not since <= published <= now:
            continue
        items = [str(i) for i in (a.items or [])]
        material = bool(MATERIAL_ITEMS & set(items)) or (a.score or {}).get("materiality", 0) >= 2
        out.append({"id": a.id, "form": a.form, "published_at": published.isoformat(), "items": items,
                    "material": material})
    return sorted(out, key=lambda f: f["published_at"])


def sentiment_state(features: dict | None, deadband: float) -> dict:
    if not features or (not features.get("coverage") and not features.get("count_3d")):
        return {"state": "unknown", "mean": None, "material_neg": False, "material_event": None}
    mean = features.get("sent_mean_3d")
    state = "none" if mean is None else ("neutral" if abs(mean) < deadband else ("pos" if mean > 0 else "neg"))
    material = features.get("material") or []
    return {"state": state, "mean": mean, "material_neg": bool(features.get("material_neg_3d")),
            "material_event": material[0].get("event") if material else None}


def symbol_state(symbol: str, enriched: pd.DataFrame, forecast, *, holding: bool, earnings_date,
                 for_day: pd.Timestamp, filings: list[dict], sentiment: dict, candidates: list[str]) -> dict:
    """Egy papir allapota a LEZART utolso gyertyan (`enriched`: indicators.enrich kimenete)."""
    last = enriched.iloc[-1]
    close = _num(last["close"])
    atr = _num(last.get("atr"))
    support = getattr(forecast, "support", None)
    resistance = getattr(forecast, "resistance", None)
    days = (pd.Timestamp(earnings_date) - pd.Timestamp(for_day).normalize()).days if earnings_date else None
    sma = _sma(enriched["close"].astype(float), REGIME_PERIOD)
    return {
        "bar": f"{enriched.index[-1]:%Y-%m-%d}", "holding": bool(holding), "close": close, "atr": atr,
        "sma": sma, "trend_ok": None if sma is None or close is None else bool(close > sma),
        "patterns": pattern_states(forecast, close, atr),
        "support": _num(support.price) if support is not None else None,
        "resistance": _num(resistance.price) if resistance is not None else None,
        "earnings": f"{pd.Timestamp(earnings_date):%Y-%m-%d}" if earnings_date else None,
        "earnings_days": days if days is None or days >= 0 else None,
        "filings": filings, "sentiment": sentiment, "candidates": sorted(candidates),
    }


def market_state(frames: dict[str, pd.DataFrame]) -> dict:
    """QQQ / S&P 500: zaro es SMA200 a LEZART utolso gyertyan (ugyanaz, mint market.market_regime)."""
    out = {}
    for symbol, label in MARKET:
        df = frames.get(symbol)
        if df is None or df.empty or len(df) < REGIME_PERIOD:
            continue
        as_of = df.index[-1]
        regime = market.market_regime(df, as_of, subject=label)
        if regime.ok is None:
            continue
        out[symbol] = {"label": label, "bar": f"{as_of:%Y-%m-%d}", "ok": bool(regime.ok),
                       "close": _num(regime.close), "sma": _num(regime.sma200)}
    return out


def ledger_candidates(session: str, symbols: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """A jelzesnaplo `session` napi dontesei: papir -> strategiak, amelyeknek a szuroin atment (qualifies).
    A masodik elem a strategiak, amelyekrol VAN bejegyzes azon a napon - a tobbirol nem tudunk semmit
    (pl. nem futott a monitor), ezekre nem jelzunk be- / kikerulest."""
    limit = (pd.Timestamp(session) - pd.Timedelta(days=LEDGER_LOOKBACK_DAYS)).isoformat()
    out: dict[str, set[str]] = {}
    known: set[str] = set()
    for rec in ledger.iter_records(newest_first=True):
        if str(rec.get("recorded_at", "")) < limit:
            break
        if rec.get("as_of") != session:
            continue
        strategy = rec.get("strategy") or "breakout"
        known.add(strategy)
        if rec.get("symbol") in symbols and rec.get("qualifies"):
            out.setdefault(rec["symbol"], set()).add(strategy)
    return {s: sorted(v) for s, v in out.items()}, sorted(known)


def momentum_candidates(settings: Settings, session: str) -> set[str] | None:
    """A momentum top N (rangsor a zaron). None: nincs aznapi rangsor."""
    path = settings.momentum_state_path
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None
    if not state or state.get("as_of") != session:
        return None
    top_n = int((state.get("params") or {}).get("top_n") or settings.strategies.momentum.params.top_n)
    return {t["symbol"] for t in state.get("top") or [] if int(t.get("rank", 10 ** 6)) <= top_n}


# ---------------------------------------------------------------------------
# Valtozaserzekeles
# ---------------------------------------------------------------------------
def _same_pattern(a: dict, b: dict) -> bool:
    if a["name"] != b["name"] or a["direction"] != b["direction"]:
        return False
    return abs((pd.Timestamp(a["start"]) - pd.Timestamp(b["start"])).days) <= SAME_PATTERN_DAYS


def _near(p: dict, limit: float) -> bool:
    return p["status"] == "forming" and p.get("dist_atr") is not None and 0 <= p["dist_atr"] <= limit


def _pattern_events(sym: str, prev: dict, cur: dict, limit: float, holding: bool, earnings: int | None) -> list[Event]:
    out, matched = [], set()
    for c in cur["patterns"]:
        p = next((p for i, p in enumerate(prev["patterns"]) if i not in matched and _same_pattern(p, c)), None)
        if p is not None:
            matched.add(prev["patterns"].index(p))
        values = {"name": c["name"], "direction": c["direction"], "trigger": c["trigger"]}
        if c["status"] == "triggered" and (p is None or p["status"] == "forming"):
            out.append(Event("pattern_triggered", sym, values, holding))
        elif _near(c, limit) and (p is None or not _near(p, limit)
                                  or (p.get("dist_atr") or 0) - c["dist_atr"] >= NEAR_STEP_ATR):
            out.append(Event("near_trigger", sym, values | {"dist_atr": c["dist_atr"], "earnings_days": earnings},
                             holding))
        elif p is None:
            out.append(Event("pattern_new", sym, values, holding))
    for i, p in enumerate(prev["patterns"]):
        if i not in matched:
            out.append(Event("pattern_gone", sym, {"name": p["name"], "direction": p["direction"]}, holding))
    return out


def diff(prev: dict | None, cur: dict, cfg) -> list[Event]:
    """Esemenyek az elozo pillanatkephez kepest. `cfg`: config.DigestSettings."""
    if not prev:
        return []
    events: list[Event] = []
    for symbol, c in cur.get("market", {}).items():
        p = prev.get("market", {}).get(symbol)
        if p and c["bar"] > p["bar"] and p["ok"] != c["ok"]:
            events.append(Event("regime_change", None, {"label": c["label"], "up": c["ok"], "close": c["close"],
                                                        "period": REGIME_PERIOD, "sma": c["sma"]}))
    known = set(prev.get("strategies_known") or []) & set(cur.get("strategies_known") or [])
    for sym, c in cur.get("symbols", {}).items():
        p = prev.get("symbols", {}).get(sym)
        if p is None:                       # most kerult a listara: nincs mihez kepest
            continue
        holding = bool(c.get("holding"))
        new_bar = c["bar"] > p["bar"]
        earnings = c.get("earnings_days")
        if new_bar:
            if p.get("trend_ok") is not None and c.get("trend_ok") is not None and p["trend_ok"] != c["trend_ok"]:
                events.append(Event("own_trend", sym, {"up": c["trend_ok"], "close": c["close"],
                                                       "period": REGIME_PERIOD, "sma": c["sma"]}, holding))
            soon = earnings if earnings is not None and earnings <= max(cfg.earnings_days) else None
            events += _pattern_events(sym, p, c, cfg.near_trigger_atr, holding, soon)
            res, sup = p.get("resistance"), p.get("support")
            if res is not None and p["close"] <= res < c["close"]:
                events.append(Event("level_break", sym, {"close": c["close"], "level": res}, holding))
            if sup is not None and p["close"] >= sup > c["close"]:
                events.append(Event("level_lost", sym, {"close": c["close"], "level": sup}, holding))
        if earnings is not None:
            before = p.get("earnings_days") if p.get("earnings") == c.get("earnings") else None
            crossed = [t for t in cfg.earnings_days if earnings <= t and (before is None or before > t)]
            # a trigger-kozelseg sora mar emliti a jelentest: ne legyen ketszer
            mentioned = any(e.symbol == sym and e.type == "near_trigger" and e.values.get("earnings_days") is not None
                            for e in events)
            if crossed and not mentioned:
                events.append(Event("earnings_soon", sym, {"days": earnings, "date": c["earnings"]}, holding))
        seen = {f["id"] for f in p.get("filings") or []}
        horizon = (pd.Timestamp(cur["created_at"]) - pd.Timedelta(days=NEW_FILING_DAYS)).isoformat()
        for f in c.get("filings") or []:
            if f["id"] not in seen and f["published_at"] >= horizon:
                events.append(Event("filing_8k", sym, {"form": f.get("form") or "8-K", "items": f["items"],
                                                       "material": f["material"]}, holding))
        ps, cs = p.get("sentiment") or {}, c.get("sentiment") or {}
        if {ps.get("state"), cs.get("state")} == {"pos", "neg"}:
            events.append(Event("sentiment_flip", sym, {"up": cs["state"] == "pos", "mean": cs["mean"],
                                                        "days": cur.get("sentiment_days")}, holding))
        if cs.get("material_neg") and not ps.get("material_neg"):
            events.append(Event("material_news", sym, {"event": cs.get("material_event")}, holding))
        before, after = set(p.get("candidates") or []) & known, set(c.get("candidates") or []) & known
        for strategy in sorted(after - before):
            events.append(Event("candidate_in", sym, {"strategy": strategy}, holding))
        for strategy in sorted(before - after):
            events.append(Event("candidate_out", sym, {"strategy": strategy}, holding))
    return events
