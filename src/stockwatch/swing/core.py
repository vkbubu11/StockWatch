"""Swing: univerzum, napi gyertyak, indikatorok, setup-felismeres es poziciokezeles.

A swing-skill csomag (scan / backtest / plan / track) swingcore.py-janak masolata - a ket
helyen ugyanazok a szabalyok. Csak standard konyvtar. Megbizast nem ad fel.
A webes felulet a BASE_DIR-t a data/swing mappara allitja (service.py).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone

try:  # Python 3.9+
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - very old Python or no tzdata
    NY = None

VERSION = "1.0.0"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) stockwatch-swing/" + VERSION

# --------------------------------------------------------------------------
# Config and paths
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    # account and risk (plan)
    "account_usd": 10000,
    "risk_pct": 1.0,              # risk per trade, % of account
    "max_position_pct": 25.0,     # max one position, % of account
    "max_total_risk_pct": 6.0,    # all open trades together
    "fractional_shares": False,
    "commission_usd": 0.0,        # per order, your broker's fee
    # universe filters (scan)
    "min_price": 10.0,
    "min_market_cap": 300_000_000,
    "min_dollar_volume": 20_000_000,   # 20-day average close*volume
    "min_history": 230,
    "setups": ["pullback", "breakout"],
    # setup rules
    "pullback_rsi_min": 35,
    "pullback_rsi_max": 60,
    "breakout_base_bars": 20,
    "breakout_max_depth_pct": 15.0,
    "breakout_near_pivot_pct": 5.0,
    "breakout_prior_advance_pct": 25.0,
    "min_stop_atr": 1.0,          # stop at least this many ATR below entry
    "max_stop_atr": 3.0,          # skip setups needing a wider stop
    "t1_r": 2.0,
    "t2_r": 3.0,
    "min_rr": 2.0,
    # execution and management (plan, track, backtest)
    "entry_valid_sessions": 2,    # cancel the buy-stop after this many sessions
    "max_chase_atr": 0.3,         # stop-limit: limit = entry + this * ATR
    "t1_fraction": 0.5,           # sell this share of the position at T1
    "trail_atr": 2.0,             # after T1: stop = highest close - trail_atr * ATR
    "time_stop_sessions": 10,     # exit if T1 not reached in this many sessions
    "max_hold_sessions": 30,
    "slippage_bps": 5,            # backtest cost per fill
    # misc
    "earnings_days": 14,          # flag earnings within this many calendar days
    "regime_symbol": "QQQ",
    "data_range": "1y",
    "backtest_range": "5y",
}


BASE_DIR: str | None = None   # a StockWatch beallitja: data/swing


def swing_dir() -> str:
    return BASE_DIR or os.environ.get("SWING_DIR", "swing")


def path(*parts: str) -> str:
    return os.path.join(swing_dir(), *parts)


def load_json(p: str, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(p: str, data) -> None:
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def load_config(p: str | None = None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    user = load_json(p or path("config.json"), {}) or {}
    unknown = [k for k in user if k not in DEFAULT_CONFIG and not k.startswith("_")]
    cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    cfg["_unknown_keys"] = unknown
    return cfg


# --------------------------------------------------------------------------
# Time and the NYSE calendar
# --------------------------------------------------------------------------

NYSE_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def now_ny() -> datetime:
    if NY is not None:
        return datetime.now(NY)
    return datetime.now(timezone.utc) - timedelta(hours=5)


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def next_sessions(d: date, n: int) -> list[date]:
    """The n trading sessions after day d."""
    out, cur = [], d
    while len(out) < n:
        cur += timedelta(days=1)
        if is_session(cur):
            out.append(cur)
    return out


def market_open(now: datetime | None = None) -> bool:
    now = now or now_ny()
    return is_session(now.date()) and dtime(9, 30) <= now.time() < dtime(16, 0)


def last_close(now: datetime | None = None) -> datetime:
    """The most recent 16:00 New York close at or before now."""
    now = now or now_ny()
    d = now.date()
    while True:
        if is_session(d):
            close = datetime.combine(d, dtime(16, 0), tzinfo=now.tzinfo)
            if close <= now:
                return close
        d -= timedelta(days=1)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

_rate_lock = threading.Lock()
_last_call = [0.0]
MIN_INTERVAL = 0.15  # seconds between requests, across threads


def http_get(url: str, headers: dict | None = None, timeout: int = 20, retries: int = 3) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last_err = None
    for attempt in range(retries):
        with _rate_lock:
            wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                break
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(3 * (attempt + 1) + random.random())
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"GET failed: {url} ({last_err})")


# --------------------------------------------------------------------------
# Universe: every NASDAQ-listed common stock
# --------------------------------------------------------------------------

NASDAQ_SCREENER = ("https://api.nasdaq.com/api/screener/stocks?tableonly=true"
                   "&limit=25&offset=0&exchange=NASDAQ&download=true")
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_SKIP_NAME = ("warrant", "right", " unit", "units", "preferred", "depositary share",
              "notes due", "%")


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "NA", "N/A", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _skip_name(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _SKIP_NAME)


def parse_nasdaq_screener(raw: dict) -> list[dict]:
    rows = ((raw or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym or any(ch in sym for ch in "^/ ") or _skip_name(r.get("name", "")):
            continue
        out.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
            "price": _num(r.get("lastsale")),
            "market_cap": _num(r.get("marketCap")),
            "sector": (r.get("sector") or "").strip(),
            "industry": (r.get("industry") or "").strip(),
        })
    return out


def parse_nasdaq_listed(text: str) -> list[dict]:
    out = []
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader, None) or []
    idx = {k: i for i, k in enumerate(header)}
    for row in reader:
        if not row or row[0].startswith("File Creation Time") or len(row) < len(header):
            continue
        g = lambda k: row[idx[k]].strip() if k in idx else ""
        if g("Test Issue") == "Y" or g("ETF") == "Y" or g("Financial Status") not in ("N", ""):
            continue
        name = g("Security Name")
        if _skip_name(name):
            continue
        out.append({"symbol": g("Symbol").upper(), "name": name, "price": None,
                    "market_cap": None, "sector": "", "industry": ""})
    return out


def load_universe(cfg: dict, refresh: bool = False, log=print) -> list[dict]:
    """NASDAQ common stocks, pre-filtered by price and market cap when known.
    Cached for the day in swing/universe.json."""
    cache = path("universe.json")
    today = now_ny().date().isoformat()
    cached = load_json(cache)
    if cached and cached.get("date") == today and not refresh:
        rows = cached["rows"]
    else:
        rows, source = [], ""
        try:
            raw = json.loads(http_get(NASDAQ_SCREENER, {"Accept": "application/json"}))
            rows, source = parse_nasdaq_screener(raw), "nasdaq-screener"
        except Exception as e:  # noqa: BLE001
            log(f"note: Nasdaq screener unavailable ({e}); falling back to nasdaqlisted.txt")
        if not rows:
            rows = parse_nasdaq_listed(http_get(NASDAQ_LISTED).decode("utf-8", "replace"))
            source = "nasdaqtrader"
        save_json(cache, {"date": today, "source": source, "rows": rows})
    keep = []
    for r in rows:
        if r.get("price") is not None and r["price"] < cfg["min_price"]:
            continue
        if r.get("market_cap") is not None and r["market_cap"] < cfg["min_market_cap"]:
            continue
        keep.append(r)
    return keep


# --------------------------------------------------------------------------
# Daily bars: Yahoo chart JSON, Stooq CSV as fallback, cached per close
# --------------------------------------------------------------------------

def yahoo_symbol(sym: str) -> str:
    return sym.replace(".", "-").replace("/", "-")


def parse_yahoo_chart(raw: dict) -> list[dict]:
    res = ((raw or {}).get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    tz = (r.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York"
    try:
        zone = ZoneInfo(tz) if NY is not None else None
    except Exception:  # noqa: BLE001
        zone = NY
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or c <= 0:
            continue
        d = datetime.fromtimestamp(t, zone).date() if zone else datetime.utcfromtimestamp(t).date()
        bars.append({"d": d.isoformat(), "o": round(o, 4), "h": round(h, 4), "l": round(l, 4),
                     "c": round(c, 4), "v": int(v or 0)})
    # de-duplicate dates (Yahoo can repeat the live bar)
    dedup = {}
    for b in bars:
        dedup[b["d"]] = b
    return [dedup[k] for k in sorted(dedup)]


def parse_stooq_csv(text: str) -> list[dict]:
    bars = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            bars.append({"d": row["Date"], "o": float(row["Open"]), "h": float(row["High"]),
                         "l": float(row["Low"]), "c": float(row["Close"]),
                         "v": int(float(row.get("Volume") or 0))})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(bars, key=lambda b: b["d"])


def fetch_bars(sym: str, rng: str = "1y") -> list[dict]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}"
           f"?range={rng}&interval=1d&includePrePost=false&events=div%2Csplits")
    try:
        bars = parse_yahoo_chart(json.loads(http_get(url, {"Accept": "application/json"})))
        if bars:
            return bars
    except Exception:  # noqa: BLE001
        pass
    url = f"https://stooq.com/q/d/l/?s={sym.lower().replace('.', '-')}.us&i=d"
    bars = parse_stooq_csv(http_get(url).decode("utf-8", "replace"))
    if rng.endswith("y") and rng[:-1].isdigit():
        cutoff = (now_ny().date() - timedelta(days=366 * int(rng[:-1]))).isoformat()
        bars = [b for b in bars if b["d"] >= cutoff]
    return bars


def drop_partial(bars: list[dict], now: datetime | None = None) -> list[dict]:
    """While the market is open, today's bar is still forming: drop it."""
    now = now or now_ny()
    if bars and market_open(now) and bars[-1]["d"] == now.date().isoformat():
        return bars[:-1]
    return bars


def get_bars(sym: str, rng: str = "1y", refresh: bool = False) -> list[dict]:
    """Completed daily bars, cached until the next 16:00 New York close."""
    p = path("cache", rng, yahoo_symbol(sym) + ".json")
    cached = load_json(p)
    now = now_ny()
    if cached and not refresh and cached.get("fetched_ts", 0) >= last_close(now).timestamp():
        return drop_partial(cached["bars"], now)
    bars = fetch_bars(sym, rng)
    save_json(p, {"symbol": sym, "fetched_ts": time.time(), "bars": bars})
    return drop_partial(bars, now)


# --------------------------------------------------------------------------
# Indicators (lists aligned with bars, None during warm-up)
# --------------------------------------------------------------------------

def sma(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n

    def val(g, l):
        return 100.0 if l == 0 else 100 - 100 / (1 + g / l)

    out[n] = val(ag, al)
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = val(ag, al)
    return out


def atr(bars: list[dict], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    if len(trs) < n:
        return out
    a = sum(trs[:n]) / n
    out[n - 1] = a
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def indicators(bars: list[dict]) -> dict:
    c = [b["c"] for b in bars]
    v = [float(b["v"]) for b in bars]
    dv = [b["c"] * b["v"] for b in bars]
    return {
        "close": c,
        "ema20": ema(c, 20),
        "sma50": sma(c, 50),
        "sma200": sma(c, 200),
        "rsi14": rsi(c, 14),
        "atr14": atr(bars, 14),
        "vol5": sma(v, 5),
        "vol10": sma(v, 10),
        "vol50": sma(v, 50),
        "dv20": sma(dv, 20),
    }


# --------------------------------------------------------------------------
# Setups. Each one looks only at bars[0..i] and returns entry/stop/targets
# for a buy-stop order on the next session(s).
# --------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _tick(px: float) -> float:
    return max(0.01, round(px * 0.001, 2))


def _levels(entry: float, stop: float, a: float, cfg: dict, kind: str, i: int, bars, notes: dict):
    risk = entry - stop
    if risk <= 0:
        return None
    t1 = entry + cfg["t1_r"] * risk
    t2 = entry + cfg["t2_r"] * risk
    return {
        "setup": kind,
        "date": bars[i]["d"],
        "close": round(bars[i]["c"], 2),
        "entry": round(entry, 2),
        "limit": round(entry + cfg["max_chase_atr"] * a, 2),
        "stop": round(stop, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "risk_per_share": round(risk, 2),
        "risk_pct": round(100 * risk / entry, 2),
        "atr": round(a, 2),
        "rr_t1": cfg["t1_r"],
        "rr_t2": cfg["t2_r"],
        **notes,
    }


def _stop_from(entry: float, structure_low: float, a: float, cfg: dict) -> float | None:
    stop = min(structure_low - 0.2 * a, entry - cfg["min_stop_atr"] * a)
    if entry - stop > cfg["max_stop_atr"] * a:
        return None
    return stop


def trend_ok(ind: dict, i: int) -> bool:
    c, s50, s200 = ind["close"][i], ind["sma50"][i], ind["sma200"][i]
    if None in (s50, s200) or i < 20 or ind["sma50"][i - 10] is None or ind["sma200"][i - 20] is None:
        return False
    return c > s50 > s200 and s50 > ind["sma50"][i - 10] and s200 >= ind["sma200"][i - 20]


def setup_pullback(bars, ind, i, cfg):
    """Uptrend, fresh high recently, quiet pullback to the 20-day EMA."""
    if i < 60 or not trend_ok(ind, i):
        return None
    a, e20, rsi14 = ind["atr14"][i], ind["ema20"][i], ind["rsi14"][i]
    if None in (a, e20, rsi14) or a <= 0:
        return None
    b = bars[i]
    hh60 = max(x["h"] for x in bars[i - 59:i + 1])
    hh15 = max(x["h"] for x in bars[i - 14:i + 1])
    if hh15 < 0.97 * hh60:                       # no recent high: not a pullback, a fade
        return None
    if not (b["l"] <= e20 + 0.25 * a and b["c"] >= ind["sma50"][i]):
        return None
    if b["c"] > hh15 - 1.0 * a:                  # has not pulled back enough
        return None
    if not (cfg["pullback_rsi_min"] <= rsi14 <= cfg["pullback_rsi_max"]):
        return None
    entry = b["h"] + _tick(b["h"])
    stop = _stop_from(entry, min(x["l"] for x in bars[i - 4:i + 1]), a, cfg)
    if stop is None:
        return None
    risk = entry - stop
    return _levels(entry, stop, a, cfg, "pullback", i, bars, {
        "resistance": round(hh60, 2),
        "room_r": round((hh60 - entry) / risk, 2),
        "why": f"uptrend, pulled back to the 20 EMA ({e20:.2f}), RSI {rsi14:.0f}",
    })


def setup_breakout(bars, ind, i, cfg):
    """Tight base just under its high after a strong advance: buy the break of the pivot."""
    n = cfg["breakout_base_bars"]
    if i < max(120, n + 10):
        return None
    c, s50, s200, a = ind["close"][i], ind["sma50"][i], ind["sma200"][i], ind["atr14"][i]
    if None in (s50, s200, a) or a <= 0 or ind["sma50"][i - 10] is None:
        return None
    if not (c > s50 and s50 > s200 and s50 >= ind["sma50"][i - 10]):
        return None
    base = bars[i - n + 1:i + 1]
    pivot = max(x["h"] for x in base)
    base_low = min(x["l"] for x in base)
    depth = (pivot - base_low) / pivot * 100
    if depth > cfg["breakout_max_depth_pct"]:
        return None
    if c < pivot * (1 - cfg["breakout_near_pivot_pct"] / 100):
        return None
    prior_low = min(x["l"] for x in bars[i - 100:i - n + 1])
    if pivot / prior_low - 1 < cfg["breakout_prior_advance_pct"] / 100:
        return None
    last10 = bars[i - 9:i + 1]
    r10 = max(x["h"] for x in last10) - min(x["l"] for x in last10)
    if pivot - base_low <= 0 or r10 / (pivot - base_low) > 0.6:     # no contraction
        return None
    v10, v50 = ind["vol10"][i], ind["vol50"][i]
    if v10 is None or v50 is None or v10 > v50:                         # volume not drying up
        return None
    entry = pivot + _tick(pivot)
    stop = _stop_from(entry, min(x["l"] for x in last10), a, cfg)
    if stop is None:
        return None
    risk = entry - stop
    hh252 = max(x["h"] for x in bars[max(0, i - 251):i + 1])
    room = 99.0 if pivot >= 0.99 * hh252 else (hh252 - entry) / risk
    return _levels(entry, stop, a, cfg, "breakout", i, bars, {
        "resistance": round(hh252, 2),
        "room_r": round(room, 2),
        "base_depth_pct": round(depth, 1),
        "measured_move": round(pivot + (pivot - base_low), 2),
        "why": f"{n}-day base {depth:.1f}% deep, range contracting, volume drying up",
    })


SETUPS = {"pullback": setup_pullback, "breakout": setup_breakout}


def detect(bars, ind, i, cfg) -> list[dict]:
    found = []
    for name in cfg["setups"]:
        fn = SETUPS.get(name)
        if fn is None:
            continue
        s = fn(bars, ind, i, cfg)
        if s and (s["t1"] - s["entry"]) / s["risk_per_share"] >= cfg["min_rr"] - 0.02:
            found.append(s)
    return found


def ret(ind: dict, i: int, n: int) -> float | None:
    if i - n < 0:
        return None
    return ind["close"][i] / ind["close"][i - n] - 1


def rs_raw(ind: dict, i: int) -> float | None:
    """IBD-style weighting: the latest quarter counts double."""
    parts = [ret(ind, i, 63), ret(ind, i, 126), ret(ind, i, 189), ret(ind, i, 252)]
    if parts[0] is None:
        return None
    w = [2, 1, 1, 1]
    num = sum(p * wt for p, wt in zip(parts, w) if p is not None)
    den = sum(wt for p, wt in zip(parts, w) if p is not None)
    return num / den


def score(setup: dict, rs_pct: float, ind: dict, i: int) -> tuple[float, str]:
    """0-100. Half relative strength, a quarter volume dry-up, a quarter room to resistance."""
    v5, v50 = ind["vol5"][i], ind["vol50"][i]
    dry = _clamp((1.2 - (v5 / v50 if v50 else 1.2)) / 0.6)
    room = _clamp(setup.get("room_r", 0) / 3)
    s = round(50 * rs_pct / 100 + 25 * dry + 25 * room, 1)
    grade = "A" if s >= 75 else "B" if s >= 60 else "C"
    return s, grade


def regime(bars: list[dict]) -> dict:
    if len(bars) < 210:
        return {"state": "unknown"}
    ind = indicators(bars)
    i = len(bars) - 1
    c, s50, s200 = ind["close"][i], ind["sma50"][i], ind["sma200"][i]
    state = "up" if c > s50 > s200 else "neutral" if c > s200 else "down"
    return {"state": state, "date": bars[i]["d"], "close": round(c, 2),
            "sma50": round(s50, 2), "sma200": round(s200, 2)}


# --------------------------------------------------------------------------
# Earnings dates (Nasdaq's public calendar), used to flag gap risk
# --------------------------------------------------------------------------

def earnings_calendar(days: int, log=print) -> dict:
    """symbol -> first report date in the next `days` calendar days."""
    cache = path("earnings.json")
    today = now_ny().date()
    cached = load_json(cache)
    if cached and cached.get("date") == today.isoformat() and cached.get("days", 0) >= days:
        return cached["map"]
    out: dict[str, str] = {}
    for k in range(days + 1):
        d = today + timedelta(days=k)
        if d.weekday() >= 5:
            continue
        try:
            raw = json.loads(http_get(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}",
                                      {"Accept": "application/json"}))
        except Exception as e:  # noqa: BLE001
            log(f"note: earnings calendar unavailable for {d} ({e})")
            continue
        for r in ((raw.get("data") or {}).get("rows") or []):
            sym = (r.get("symbol") or "").upper()
            if sym and sym not in out:
                out[sym] = d.isoformat()
    save_json(cache, {"date": today.isoformat(), "days": days, "map": out})
    return out


# --------------------------------------------------------------------------
# Position management: one daily bar at a time. Shared by backtest and track
# so the simulated rules are the rules you are told to follow.
# --------------------------------------------------------------------------

def new_position(symbol: str, fill: float, stop: float, t1: float, t2: float, atr_: float,
                 day: str, qty: float = 1.0, fractional: bool = True) -> dict:
    return {"symbol": symbol, "fractional": fractional, "entry": fill, "initial_stop": stop, "stop": stop, "t1": t1, "t2": t2,
            "atr": atr_, "qty": qty, "remaining": qty, "t1_hit": False, "sessions": 0,
            "highest_close": fill, "opened": day, "events": [], "status": "open"}


def manage_step(pos: dict, bar: dict, cfg: dict, fill_bar: bool = False) -> list[dict]:
    """Advance an open long by one completed daily bar. Conservative: when a
    stop and a target both sit inside the same bar, the stop is assumed first;
    on the fill day only the stop is checked."""
    ev: list[dict] = []
    if pos["remaining"] <= 0:
        return ev

    def exit_(qty, px, why):
        e = {"date": bar["d"], "qty": qty, "price": round(px, 2), "reason": why}
        ev.append(e)
        pos["events"].append(e)
        pos["remaining"] = round(pos["remaining"] - qty, 6)
        if pos["remaining"] <= 1e-9:
            pos["remaining"] = 0
            pos["status"] = "closed"
            pos["closed"] = bar["d"]

    if bar["l"] <= pos["stop"]:
        why = "stop" if not pos["t1_hit"] else ("breakeven" if pos["stop"] <= pos["entry"] + 1e-9 else "trail")
        exit_(pos["remaining"], min(bar["o"], pos["stop"]) if not fill_bar else pos["stop"], why)
        return ev
    if fill_bar:
        pos["highest_close"] = max(pos["highest_close"], bar["c"])
        return ev

    pos["sessions"] += 1
    if not pos["t1_hit"] and bar["h"] >= pos["t1"]:
        q = pos["qty"] * cfg["t1_fraction"]
        if not pos.get("fractional", True):
            q = math.floor(q)
        q = min(q, pos["remaining"])
        pos["t1_hit"] = True
        pos["stop"] = max(pos["stop"], pos["entry"])
        if q > 0:
            exit_(q, max(bar["o"], pos["t1"]), "t1")
    if pos["remaining"] > 0 and bar["h"] >= pos["t2"]:
        exit_(pos["remaining"], max(bar["o"], pos["t2"]), "t2")
        return ev

    pos["highest_close"] = max(pos["highest_close"], bar["c"])
    if pos["remaining"] > 0:
        if pos["t1_hit"]:
            trail = pos["highest_close"] - cfg["trail_atr"] * pos["atr"]
            if trail > pos["stop"]:
                pos["stop"] = round(trail, 2)
                ev.append({"date": bar["d"], "new_stop": pos["stop"], "reason": "trail"})
        elif pos["sessions"] >= cfg["time_stop_sessions"]:
            exit_(pos["remaining"], bar["c"], "time")
            return ev
        if pos["remaining"] > 0 and pos["sessions"] >= cfg["max_hold_sessions"]:
            exit_(pos["remaining"], bar["c"], "max_hold")
    return ev


def position_r(pos: dict, slippage_bps: float = 0.0) -> float:
    """Result in R (multiples of the initial risk per share), after slippage."""
    slip = slippage_bps / 10000
    entry = pos["entry"] * (1 + slip)
    risk = pos["entry"] - pos["initial_stop"]
    if risk <= 0:
        return 0.0
    pnl = 0.0
    for e in pos["events"]:
        if "qty" in e:
            pnl += e["qty"] * (e["price"] * (1 - slip) - entry)
    return pnl / (risk * pos["qty"])


def simulate(bars: list[dict], i: int, setup: dict, cfg: dict) -> dict | None:
    """Play one signal forward: buy-stop for entry_valid_sessions, then manage.
    Returns None when the order never fills (cancelled)."""
    entry, limit, stop = setup["entry"], setup["limit"], setup["stop"]
    for k in range(1, cfg["entry_valid_sessions"] + 1):
        j = i + k
        if j >= len(bars):
            return None
        b = bars[j]
        if b["o"] > limit:          # gapped past the limit: stop-limit does not fill
            return None
        if b["h"] >= entry:
            fill = max(b["o"], entry)
            pos = new_position(setup.get("symbol", ""), fill, stop, setup["t1"], setup["t2"], setup["atr"], b["d"])
            manage_step(pos, b, cfg, fill_bar=True)
            jj = j + 1
            while pos["remaining"] > 0 and jj < len(bars):
                manage_step(pos, bars[jj], cfg)
                jj += 1
            if pos["remaining"] > 0:
                pos["status"] = "open_at_end"
            pos["r"] = round(position_r(pos, cfg.get("slippage_bps", 0)), 3)
            pos["setup"] = setup["setup"]
            pos["signal_date"] = setup["date"]
            return pos
        if b["l"] <= stop:          # broke down before triggering: setup void
            return None
    return None
