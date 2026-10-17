"""Adatgyujtes a Backteszt oldalhoz: egy papir napi gyertyai a kert kezdodatumtol, a rezsim-indexekkel.

A tar KULON van az elo cache-tol (data/backtest/), mert az elo tarra a 10 eves megorzesi plafon
vonatkozik, ide pedig akar 30 ev is kerulhet. A letoltes a szokasos PriceProvider-en at megy
(a rendszer sehol nem hivja kozvetlenul a yfinance-t). Az arak split / osztalek szerint
korrigaltak (auto_adjust), igy a "vettem es tartottam" kozelitoleg teljes hozam.

Frissites: ha a papir mar megvan, csak a hianyzo eleje / vege jon le. Ha az atfedo napokon a
korrigalt zaro elter (osztalek utani visszamenoleges korrekcio), az egesz ujratoltodik.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import feed
from ..config import DATA_DIR, Settings
from ..locks import write_atomic

log = logging.getLogger(__name__)

BT_DIR = DATA_DIR / "backtest"
LIMIT_MB = 300                                   # az osszes papir adata egyutt
BENCHES = ("^GSPC", "QQQ", "SPY")                # rezsim (S&P 500, Nasdaq-100) es benchmark
MIN_START = pd.Timestamp("1980-01-01")
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")
JUMP_PCT = 30.0                                  # ennyi % feletti napi ugras gyanus (split nelkul)
LISTING_CACHE_MAX = 2000                         # a tozsdere lepes datumainak gyorsitotara (papir)


def clean_symbol(symbol: str) -> str:
    symbol = str(symbol or "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise ValueError(f"Ervenytelen ticker: {symbol or '(ures)'}")
    return symbol


def parse_start(value) -> pd.Timestamp:
    try:
        start = pd.Timestamp(value).normalize()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Ervenytelen kezdodatum: {value}") from exc
    if start < MIN_START:
        raise ValueError(f"A kezdodatum legkorabban {MIN_START:%Y-%m-%d} lehet.")
    if start > pd.Timestamp.today().normalize() - pd.Timedelta(days=30):
        raise ValueError("A kezdodatum legalabb 30 nappal a mai nap elott legyen.")
    return start


def _dir(symbol: str):
    return BT_DIR / symbol.replace("^", "_")


def _ohlcv_path(symbol: str):
    return _dir(symbol) / "ohlcv.csv.gz"


def _meta_path(symbol: str):
    return _dir(symbol) / "meta.json"


# ---------------------------------------------------------------------------
# Olvasas
# ---------------------------------------------------------------------------
def load(symbol: str) -> pd.DataFrame:
    path = _ohlcv_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.read_csv(path, parse_dates=["date"], index_col="date")
    return df.astype(float).sort_index()


def meta(symbol: str) -> dict | None:
    path = _meta_path(symbol)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def stored_symbols() -> list[dict]:
    """A letoltott papirok (a benchmarkok nelkul) - a felulet listajahoz."""
    if not BT_DIR.exists():
        return []
    out = []
    for path in sorted(BT_DIR.glob("*/meta.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("user") or m.get("symbol") not in BENCHES:
            out.append(m)
    return sorted(out, key=lambda m: m.get("last_used") or "", reverse=True)


def touch(symbol: str) -> None:
    m = meta(symbol)
    if m is not None:
        m["last_used"] = _now()
        write_atomic(_meta_path(symbol), json.dumps(m, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------------------
# Letoltes
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(symbol: str, start: pd.Timestamp, settings: Settings, provider=None) -> pd.DataFrame:
    provider = provider or feed.get_provider(settings)
    out = provider.fetch([symbol], start=start, end=pd.Timestamp.today().normalize() + pd.Timedelta(days=1))
    return out.get(symbol, pd.DataFrame())


def _consistent(old: pd.DataFrame, new: pd.DataFrame) -> bool:
    """Az atfedo napok korrigalt zaroi egyeznek-e (0,1%-on belul)."""
    common = old.index.intersection(new.index)
    if len(common) == 0:
        return False
    a, b = old.loc[common, "close"].to_numpy(), new.loc[common, "close"].to_numpy()
    return bool(np.all(np.abs(a / b - 1) < 0.001))


def update(symbol: str, start: pd.Timestamp, settings: Settings, provider=None, user: bool = False) -> dict:
    """A papir adata `start`-tol mostanaig. Visszaadja a meta-adatot (adatminoseggel)."""
    symbol = clean_symbol(symbol)
    provider = provider or feed.get_provider(settings)
    old = load(symbol)
    today = pd.Timestamp.today().normalize()
    full = old.empty or start < old.index[0] - pd.Timedelta(days=7)
    if not full:
        tail = _fetch(symbol, old.index[-1] - pd.Timedelta(days=10), settings, provider)
        if tail.empty:
            merged = old
        elif _consistent(old, tail):
            merged = pd.concat([old[old.index < tail.index[0]], tail])
        else:
            log.info("%s: a korrigalt arak megvaltoztak (osztalek / split) - teljes ujratoltes", symbol)
            full = True
    if full:
        merged = _fetch(symbol, start, settings, provider)
        if merged.empty:
            raise ValueError(f"Nincs elerheto adat erre a tickerre: {symbol}")
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    _dir(symbol).mkdir(parents=True, exist_ok=True)
    merged.to_csv(_ohlcv_path(symbol), index_label="date", compression="gzip")
    previous = meta(symbol) or {}
    requested = min(start, pd.Timestamp(previous.get("requested_start", start)))
    m = {"symbol": symbol, "requested_start": f"{requested:%Y-%m-%d}", "first": f"{merged.index[0]:%Y-%m-%d}",
         "last": f"{merged.index[-1]:%Y-%m-%d}", "bars": int(len(merged)), "source": getattr(provider, "name", "?"),
         "fetched_at": _now(), "last_used": _now(), "quality": quality(merged, requested),
         "stale": bool((today - merged.index[-1]).days > 5),
         "user": bool(user or previous.get("user"))}            # a felhasznalo kerte (nem csak benchmark)
    write_atomic(_meta_path(symbol), json.dumps(m, ensure_ascii=False, indent=1))
    return m


def _listing_path():
    return BT_DIR / "listing.json"


def listing_date(symbol: str, settings: Settings, provider=None) -> pd.Timestamp | None:
    """A papir tozsdere lepesenek napja (None, ha a szolgaltato nem tudja). A talalat gyorsitotarba kerul
    (data/backtest/listing.json, legfeljebb LISTING_CACHE_MAX papir), mert ez a datum nem valtozik."""
    symbol = clean_symbol(symbol)
    path = _listing_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    if symbol in cache:
        return pd.Timestamp(cache[symbol]["listed"])
    provider = provider or feed.get_provider(settings)
    getter = getattr(provider, "first_trade_date", None)
    listed = getter(symbol) if getter else None
    if listed is None:                                   # nem taroljuk: lehet atmeneti hiba is
        return None
    cache[symbol] = {"listed": f"{listed:%Y-%m-%d}", "checked": _now()}
    if len(cache) > LISTING_CACHE_MAX:
        cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("checked", ""))[-LISTING_CACHE_MAX:])
    BT_DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(cache, ensure_ascii=False))
    return listed


def earliest_start(symbol: str, settings: Settings, provider=None) -> tuple[pd.Timestamp, pd.Timestamp | None]:
    """(a legkorabbi valaszthato kezdodatum, a tozsdere lepes napja vagy None)."""
    listed = listing_date(symbol, settings, provider)
    return (max(MIN_START, listed) if listed is not None else MIN_START), listed


def collect(symbol: str, start, settings: Settings, provider=None, progress=None) -> dict:
    """A papir es a rezsim- / benchmark-indexek letoltese ugyanarra az idoszakra."""
    symbol, start = clean_symbol(symbol), parse_start(start)
    progress = progress or (lambda msg: None)
    progress(f"{symbol} letoltese {start:%Y-%m-%d}-tol...")
    main = update(symbol, start, settings, provider, user=True)
    for bench in BENCHES:
        if bench == symbol:
            continue
        progress(f"Rezsim / benchmark: {bench}...")
        try:
            update(bench, start, settings, provider)
        except ValueError as exc:
            log.warning("A(z) %s nem toltheto le: %s", bench, exc)
    prune(keep={symbol, *BENCHES})
    return main


# ---------------------------------------------------------------------------
# Adatminoseg
# ---------------------------------------------------------------------------
def _trading_days(first: pd.Timestamp, last: pd.Timestamp) -> pd.DatetimeIndex:
    try:
        import pandas_market_calendars as mcal

        return mcal.get_calendar("NYSE").valid_days(first, last).tz_localize(None).normalize()
    except Exception:                                    # naptar nelkul: munkanapok (unnepnapokkal tobb)
        return pd.bdate_range(first, last)


def quality(df: pd.DataFrame, requested: pd.Timestamp | None = None) -> dict:
    """Mennyire megbizhato az adat: elso nap, hianyzo napok, nulla volumen, gyanus ugrasok."""
    if df.empty:
        return {"issues": ["nincs adat"], "ok": False}
    first, last = df.index[0], df.index[-1]
    expected = _trading_days(first, last)
    missing = expected.difference(df.index)
    ret = df["close"].pct_change().abs() * 100
    jumps = ret[ret > JUMP_PCT]
    zero = int((df["volume"] <= 0).sum())
    issues = []
    if requested is not None and first > requested + pd.Timedelta(days=7):
        issues.append(f"a papir csak {first:%Y-%m-%d}-tol erheto el (kert: {requested:%Y-%m-%d})")
    if len(missing) > max(5, len(expected) * 0.002):
        issues.append(f"{len(missing)} hianyzo kereskedesi nap")
    if zero > max(5, len(df) * 0.01):
        issues.append(f"{zero} nap nulla volumennel")
    if len(jumps):
        issues.append(f"{len(jumps)} nap {JUMP_PCT:g}%-nal nagyobb mozgassal (split nelkul gyanus)")
    return {"ok": not issues, "issues": issues, "missing_days": int(len(missing)),
            "missing_sample": [f"{d:%Y-%m-%d}" for d in missing[:10]], "zero_volume_days": zero,
            "jumps": [{"date": f"{d:%Y-%m-%d}", "pct": round(float(v), 1)} for d, v in jumps.nlargest(10).items()]}


# ---------------------------------------------------------------------------
# Lemezplafon
# ---------------------------------------------------------------------------
def usage_bytes() -> int:
    return sum(p.stat().st_size for p in BT_DIR.rglob("*") if p.is_file()) if BT_DIR.exists() else 0


def prune(keep: set[str] | None = None) -> list[str]:
    """A legregebben hasznalt papirok torlese, amig a tar LIMIT_MB folott van (a benchmarkok maradnak)."""
    keep = set(keep or ()) | set(BENCHES)
    removed = []
    candidates = [m for m in stored_symbols() if m.get("symbol") not in keep]
    candidates.sort(key=lambda m: m.get("last_used") or "")
    while candidates and usage_bytes() > LIMIT_MB * 1024 * 1024:
        victim = candidates.pop(0)["symbol"]
        for path in _dir(victim).glob("*"):
            path.unlink(missing_ok=True)
        _dir(victim).rmdir()
        removed.append(victim)
    return removed
