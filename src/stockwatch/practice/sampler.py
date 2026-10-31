"""Feladatkeszlet es seedelt mintavetel.

KESZLET (labor-munkasor vagy CLI, adat-ujjlenyomatonkent egyszer; a webes keres soha nem epit):
  * forras: a Backteszt oldal tara (elsobbseggel) es az elo univerzum-cache, a holdout elotti napon vagva
  * jelolt dontesi pont `t`: legalabb WARMUP_BARS bemelegito gyertya, es a t + FUTURE_BARS gyertya is a
    holdout ELOTT van; a lathato es a jovobeli ablakban nincs gyanus (split nelkuli) ugras es nulla volumen
  * kategoriak: "strategy" (a Visszahuzas-csalad setup-ja ugyanazzal a motorral, mint elesben, vagy a labor
    kitores-cache-enek kitorese), "pattern" (pont-idoben, csak az addigi adatbol felismert, formalodo bikas
    alakzat), "short" (short-gyanus: zaro < SMA200 es EMA50 < EMA200, lehetoleg formalodo vagy letort medves
    alakzattal - a megerositett pivotokbol), "nothing" (egyik sem). Az irany a feladat tipusabol nem talalhato
    ki: a kategoria csak a felfedeskor latszik, es mindegyikben lehet long, short vagy kihagyas a jo valasz.
  * papironkent legfeljebb nehany pont (veletlen, a tickerbol seedelve), kategoriankent plafonnal

MINTAVETEL: ugyanaz a seed es keszlet ugyanazt a helyzetsort adja (sulyozott kategoria; egy papir lehetoleg csak egyszer).
A megosztasi kod a seedet es a keszlet ujjlenyomatat tartalmazza, csak betukkel (szam nincs benne).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import indicators, market, meanrev, usage, workspace
from .. import meanrev_research as mr
from ..config import Settings, load_settings
from ..entry_research import HOLDOUT_START
from . import store

log = logging.getLogger(__name__)

POOL_VERSION = 2
VISIBLE_END = HOLDOUT_START - pd.Timedelta(days=1)
WARMUP_BARS = 250
VISIBLE_BARS = 120
MAX_DAYS = 30                                  # a leghosszabb idokorlat
FUTURE_BARS = MAX_DAYS + 1                     # az idokorlat utani nyitas is kell
JUMP_PCT = 30.0                                # backtest_page.data.JUMP_PCT: split nelkul gyanus napi mozgas
CATEGORIES = ("strategy", "pattern", "short", "nothing")
DEFAULT_MIX = {"strategy": 35, "pattern": 15, "short": 15, "nothing": 35}
PER_SYMBOL = {"strategy": 8, "short": 4, "nothing": 6}
PATTERN_PROBES = 8
CAP = {"strategy": 4000, "pattern": 2000, "short": 2000, "nothing": 4000}
DETECTION_WINDOW = 400                         # backtest.DETECTION_WINDOW
MAX_ROUNDS = 50
CODE_ALPHABET = store.ID_ALPHABET


# ---------------------------------------------------------------------------
# Adat
# ---------------------------------------------------------------------------
def load_prices(symbol: str, settings: Settings) -> pd.DataFrame | None:
    """A papir gyertyai a holdout elotti napig: a Backteszt oldal tara elsobbseggel, kulonben az univerzum-cache."""
    from ..backtest_page import data as bt_data

    try:
        df = bt_data.load(symbol)
    except (OSError, ValueError):
        df = pd.DataFrame()
    if df.empty:
        df = mr.load_raw(symbol, settings)
    if df is None or df.empty:
        return None
    df = df.loc[:VISIBLE_END]
    return None if df.empty else df[["open", "high", "low", "close", "volume"]].astype(float)


def eligible(df: pd.DataFrame) -> np.ndarray:
    """Pozicionkent: lehet-e dontesi pont (bemelegites, jovobeli ablak a holdout elott, tiszta ablak)."""
    n = len(df)
    ok = np.zeros(n, dtype=bool)
    if n < WARMUP_BARS + FUTURE_BARS + 1:
        return ok
    ret = df["close"].pct_change().abs().to_numpy() * 100
    bad = (np.nan_to_num(ret) > JUMP_PCT) | (df["volume"].to_numpy() <= 0)
    cum = np.r_[0, np.cumsum(bad)]
    t = np.arange(WARMUP_BARS, n - FUTURE_BARS)
    lo, hi = t - VISIBLE_BARS + 1, t + FUTURE_BARS
    ok[t] = (cum[hi + 1] - cum[lo]) == 0
    return ok


def universe(settings: Settings) -> list[str]:
    from .. import indexes
    from ..backtest_page import data as bt_data

    members = indexes.IndexMembers(settings)
    symbols = {s for name in ("nasdaq100", "sp500") for s in members.members(name, allow_network=False)}
    symbols |= {m["symbol"] for m in bt_data.stored_symbols() if m.get("symbol")}
    return sorted(symbols - set(bt_data.BENCHES))


def _rng(*parts) -> np.random.Generator:
    raw = "|".join(str(p) for p in (POOL_VERSION, *parts))
    return np.random.default_rng(int(hashlib.sha1(raw.encode()).hexdigest()[:12], 16))


def _pick(rng: np.random.Generator, positions: np.ndarray, k: int) -> list[int]:
    if len(positions) <= k:
        return [int(p) for p in positions]
    return sorted(int(p) for p in rng.choice(positions, size=k, replace=False))


def config_key(settings: Settings) -> str:
    raw = json.dumps({"v": POOL_VERSION, "pullback": settings.strategies.pullback.params.model_dump(),
                      "patterns": settings.patterns.model_dump(), "structure": settings.structure.model_dump()},
                     sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Kategoriak egy papiron
# ---------------------------------------------------------------------------
def pullback_positions(symbol: str, df: pd.DataFrame, settings: Settings, mctx: market.MarketContext | None,
                       earnings_dates: list | None) -> np.ndarray:
    """A Visszahuzas setup-ja (a Mely visszahuzas jelzese ugyanez, csak a stopja tagabb) - mint elesben."""
    params = settings.strategies.pullback.params
    if len(df) < meanrev.WARMUP + 2:
        return np.array([], dtype=int)
    feat = meanrev.features(df, params)
    regime = meanrev.regime_series(feat, mctx.regime_frame(symbol, df)) if mctx is not None else None
    mask = meanrev.setup_mask(feat, params, regime).to_numpy().copy()
    mask[: meanrev.WARMUP] = False
    out = [i for i in np.flatnonzero(mask)
           if not meanrev.earnings_blocked(feat.index[i].date(), earnings_dates, params)]
    return np.array(out, dtype=int)


def breakout_positions(symbol: str, df: pd.DataFrame) -> np.ndarray:
    """A labor kitores-cache-enek kitoresi napjai (a Kitores-strategia jelzesei)."""
    from .. import lab

    cache = lab.load_cache(symbol) or {}
    days = {pd.Timestamp(e[0]) for e in cache.get("events", [])}
    return np.array(sorted(int(df.index.get_loc(d)) for d in days if d in df.index), dtype=int)


def forming_pattern(symbol: str, enriched: pd.DataFrame, i: int, settings: Settings) -> str | None:
    """Formalodo bikas alakzat a t=i napon, CSAK az addigi adatbol (megerositett pivotok)."""
    from .. import patterns

    window = enriched.iloc[max(0, i - DETECTION_WINDOW): i + 1]
    found = patterns.detect(symbol, window, settings.patterns, settings.structure, confirmed_only=True)
    forming = [m for m in found if m.status == "forming" and m.direction == "bullish"]
    return forming[0].name if forming else None


def downtrend(df: pd.DataFrame) -> np.ndarray:
    """Pont-idoben: zaro < SMA200 es EMA50 < EMA200 (a short motor trend-szurojenek tukre)."""
    close = df["close"].astype(float)
    sma200 = close.rolling(200, min_periods=200).mean()
    mask = (close < sma200) & (indicators.ema(close, 50) < indicators.ema(close, 200))
    return mask.fillna(False).to_numpy()


def bearish_pattern(symbol: str, enriched: pd.DataFrame, i: int, settings: Settings) -> str | None:
    """Formalodo vagy az utolso ket napban letort medves alakzat a t=i napon, CSAK az addigi adatbol."""
    from .. import patterns

    window = enriched.iloc[max(0, i - DETECTION_WINDOW): i + 1]
    found = patterns.detect(symbol, window, settings.patterns, settings.structure, confirmed_only=True)
    recent = set(window.index[-2:])
    for m in found:
        if m.direction == "bearish" and (m.status == "forming" or (m.status == "triggered" and m.trigger_date in recent)):
            return m.name
    return None


def symbol_entries(symbol: str, df: pd.DataFrame, settings: Settings, mctx, earnings_dates) -> dict[str, list]:
    ok = eligible(df)
    out: dict[str, list] = {c: [] for c in CATEGORIES}
    if not ok.any():
        return out
    rng = _rng(symbol)
    pull = pullback_positions(symbol, df, settings, mctx, earnings_dates)
    brk = breakout_positions(symbol, df)
    signal = np.zeros(len(df), dtype=bool)
    signal[pull] = True
    signal[brk] = True
    sources = {int(i): "breakout" for i in brk} | {int(i): "pullback" for i in pull}    # mindketto: Visszahuzas
    strat = np.array(sorted(i for i in sources if ok[i]), dtype=int)
    day = lambda i: f"{df.index[i]:%Y-%m-%d}"                                     # noqa: E731
    out["strategy"] = [[symbol, day(i), sources[i]] for i in _pick(rng, strat, PER_SYMBOL["strategy"])]
    quiet = np.flatnonzero(ok & ~signal)
    out["nothing"] = [[symbol, day(i), ""] for i in _pick(rng, quiet, PER_SYMBOL["nothing"])]
    probes = _pick(rng, quiet, PATTERN_PROBES)
    down = np.flatnonzero(ok & ~signal & downtrend(df))
    short_probes = _pick(_rng(symbol, "short"), down, PATTERN_PROBES)
    enriched = indicators.enrich(df, settings.structure.atr_period) if probes or short_probes else None
    for i in probes:
        name = forming_pattern(symbol, enriched, i, settings)
        if name:
            out["pattern"].append([symbol, day(i), name])
    found, plain = [], []
    for i in short_probes:
        name = bearish_pattern(symbol, enriched, i, settings)
        (found if name else plain).append((i, name or "downtrend"))
    picked = (found + plain)[: PER_SYMBOL["short"]]
    out["short"] = [[symbol, day(i), name] for i, name in sorted(picked)]
    return out


# ---------------------------------------------------------------------------
# A keszlet
# ---------------------------------------------------------------------------
def _fingerprint_update(digest, symbol: str, df: pd.DataFrame) -> None:
    digest.update(f"{symbol}|{len(df)}|{df.index[0]:%Y-%m-%d}|{df.index[-1]:%Y-%m-%d}|"
                  f"{float(df['close'].iloc[-1]):.4f}|{float(df['close'].sum()):.2f}".encode())


def build(settings: Settings | None = None, symbols: list[str] | None = None, progress=None,
          max_seconds: float | None = None, force: bool = False) -> dict:
    """A feladatkeszlet felepitese es mentese. Ha a meglevo keszlet ugyanerre az adatra es beallitasra keszult
    (ujjlenyomat), nem epul ujra (`force` nelkul)."""
    settings = settings or load_settings()
    say = progress or (lambda msg: None)
    symbols = sorted(symbols if symbols is not None else universe(settings))
    if not symbols:
        raise ValueError("Nincs papir (nincs letoltott indexosszetetel / univerzum - python cli.py fetch-universe).")
    frames, digest = {}, hashlib.sha1(config_key(settings).encode())
    for n, symbol in enumerate(symbols, 1):
        if n % 100 == 1:
            say(f"adat: {n}/{len(symbols)}")
        df = load_prices(symbol, settings)
        if df is not None and len(df) >= WARMUP_BARS + FUTURE_BARS + 1:
            frames[symbol] = df
            _fingerprint_update(digest, symbol, df)
    if not frames:
        raise ValueError("Nincs eleg hosszu arfolyamadat a holdout elott (legalabb ~300 gyertya kell).")
    fingerprint = digest.hexdigest()[:16]
    current = store.load_pool()
    if current and current.get("fingerprint") == fingerprint and not force:
        say("a keszlet mar erre az adatra keszult")
        return current | {"unchanged": True}

    mctx = market.load_context(settings, refresh=False, allow_network=False)
    hist = market.HistoricalEarnings(settings, allow_network=False)
    entries: dict[str, list] = {c: [] for c in CATEGORIES}
    started, partial = time.monotonic(), False
    for n, (symbol, df) in enumerate(sorted(frames.items()), 1):
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            partial = True
            say(f"az idokeret elfogyott: {n - 1}/{len(frames)} papir")
            break
        if n % 25 == 1:
            say(f"helyzetek: {n}/{len(frames)} ({symbol})")
        try:
            got = symbol_entries(symbol, df, settings, mctx, hist.dates(symbol))
        except Exception:
            log.exception("Gyakorlo keszlet: %s kimarad", symbol)
            continue
        for c in CATEGORIES:
            entries[c] += got[c]
    for c in CATEGORIES:
        if len(entries[c]) > CAP[c]:
            keep = _pick(_rng("cap", c), np.arange(len(entries[c])), CAP[c])
            entries[c] = [entries[c][i] for i in keep]
    pool = {"version": POOL_VERSION, "fingerprint": fingerprint, "config_key": config_key(settings),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": len(frames),
            "partial": partial, "visible_end": f"{VISIBLE_END:%Y-%m-%d}",
            "counts": {c: len(entries[c]) for c in CATEGORIES}, "entries": entries}
    pool["bytes"] = store.save_pool(pool)
    say(f"kesz: {pool['counts']}")
    return pool


def pool_info(pool: dict | None) -> dict | None:
    if not pool:
        return None
    path = store.pool_path()
    return {k: pool.get(k) for k in ("fingerprint", "created", "symbols", "partial", "counts", "visible_end")} | {
        "bytes": path.stat().st_size if path.exists() else None}


# ---------------------------------------------------------------------------
# Mintavetel
# ---------------------------------------------------------------------------
def normalize_mix(mix: dict | None) -> dict[str, float]:
    mix = {c: float((mix or DEFAULT_MIX).get(c, 0) or 0) for c in CATEGORIES}
    if any(v < 0 for v in mix.values()) or sum(mix.values()) <= 0:
        raise ValueError("mix: nemnegativ sulyok, legalabb egy pozitiv")
    total = sum(mix.values())
    return {c: v / total for c, v in mix.items()}


def sample(pool: dict, seed: int, rounds: int = 20, mix: dict | None = None) -> list[dict]:
    """`rounds` helyzet a keszletbol, determinisztikusan (seed). Egy papir lehetoleg csak egyszer (kis keszletnel masik napon ismetlodhet)."""
    if not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError(f"rounds: 1 es {MAX_ROUNDS} kozott")
    weights = normalize_mix(mix)
    lists = {c: list(pool["entries"].get(c) or []) for c in CATEGORIES}
    rng = np.random.default_rng(seed)
    order = {c: list(rng.permutation(len(lists[c]))) for c in CATEGORIES}
    used_symbols: set[str] = set()
    used_points: set[tuple[str, str]] = set()
    out = []
    while len(out) < rounds:
        cats = [c for c in CATEGORIES if order[c] and weights[c] > 0]
        if not cats:
            cats = [c for c in CATEGORIES if order[c]]
        if not cats:
            break
        p = np.array([weights[c] for c in cats])
        p = p / p.sum() if p.sum() > 0 else np.full(len(cats), 1 / len(cats))
        cat = cats[int(rng.choice(len(cats), p=p))]
        # elobb uj papir; ha a kategoriaban mar mind szerepelt, ismetlodhet (masik napon)
        pos = next((n for n, idx in enumerate(order[cat]) if lists[cat][idx][0] not in used_symbols), 0)
        symbol, day, source = lists[cat][order[cat].pop(pos)]
        if (symbol, day) in used_points:
            continue
        used_symbols.add(symbol)
        used_points.add((symbol, day))
        out.append({"symbol": symbol, "date": day, "category": cat, "source": source})
    if len(out) < rounds:
        raise ValueError(f"A keszletben csak {len(out)} helyzet van.")
    return out


def _encode(n: int, width: int) -> str:
    base, out = len(CODE_ALPHABET), []
    for _ in range(width):
        n, r = divmod(n, base)
        out.append(CODE_ALPHABET[r])
    if n:
        raise ValueError("a szam nem fer a kodba")
    return "".join(reversed(out))


def _decode(text: str) -> int:
    n = 0
    for ch in text:
        pos = CODE_ALPHABET.find(ch)
        if pos < 0:
            raise ValueError("Ervenytelen megosztasi kod.")
        n = n * len(CODE_ALPHABET) + pos
    return n


SEED_MAX = 2 ** 31


def share_code(seed: int, fingerprint: str, rounds: int) -> str:
    return f"{_encode(seed, 7)}-{_encode(int(fingerprint[:8], 16), 8)}-{_encode(rounds, 2)}"


def parse_code(code: str) -> tuple[int, int, int]:
    """(seed, a keszlet-ujjlenyomat eleje szamkent, korok szama)."""
    parts = str(code or "").strip().lower().split("-")
    if len(parts) != 3 or [len(p) for p in parts] != [7, 8, 2]:
        raise ValueError("Ervenytelen megosztasi kod.")
    seed, fp, rounds = (_decode(p) for p in parts)
    if seed >= SEED_MAX or not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError("Ervenytelen megosztasi kod.")
    return seed, fp, rounds


# ---------------------------------------------------------------------------
# Munkasor
# ---------------------------------------------------------------------------
def submit(ws: str = workspace.DEFAULT, force: bool = False) -> dict:
    from .. import labqueue

    if any(j["status"] in ("queued", "running") for j in labqueue.jobs(ws, "practice_pool")):
        raise ValueError("A feladatkeszlet mar sorban all / epul.")
    return labqueue.submit(ws, "practice_pool", {"force": bool(force)})


def run_job(job: dict, settings: Settings) -> dict:
    from .. import labqueue

    with usage.meter(job["workspace"], "practice"):
        pool = build(settings, force=bool(job["payload"].get("force")),
                     progress=lambda msg: labqueue.update(job, message=msg[:200]))
    if pool.get("unchanged"):
        return {"message": "valtozatlan: a keszlet mar erre az adatra keszult"}
    return {"message": f"kesz: {sum(pool['counts'].values())} helyzet, {pool['symbols']} papir",
            "fingerprint": pool["fingerprint"]}
