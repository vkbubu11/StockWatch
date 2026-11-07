"""Swing fül: NASDAQ-szűrés, backteszt, méretezés és kötésnapló a webes felületnek.

A szabalyok a core.py-ban vannak (a swing-skill csomag swingcore.py-janak masolata), itt csak a
futtatas, a mentes es a felulet adatai. Minden a data/swing mappaba kerul:

    config.json      a swing beallitasai (hianyzo kulcs = alapertek; szamla/kockazat alapbol az entry-bol)
    candidates.json  a legutobbi szures eredmenye
    backtest.json    a legutobbi backteszt
    trades.json      a kotesnaplo
    cache/           napi gyertyak papironkent (a kovetkezo 16:00-s NY zarasig ervenyes)

Megbizast nem ad fel, a brokerhez nem nyul.
"""
from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from ..config import DATA_DIR, load_settings
from . import core as sc

SWING_DIR: Path = DATA_DIR / "swing"
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
GRADES = {"A": 0, "B": 1, "C": 2}

# a feluletrol allithato kulcsok: tipus, also, felso hatar
EDITABLE = {
    "account_usd": (float, 100, 1e9),
    "risk_pct": (float, 0.05, 5),
    "max_position_pct": (float, 1, 100),
    "max_total_risk_pct": (float, 0.1, 50),
    "fractional_shares": (bool, None, None),
    "commission_usd": (float, 0, 1000),
    "min_price": (float, 0, 10000),
    "min_dollar_volume": (float, 0, 1e11),
    "setups": (list, None, None),
    "t1_r": (float, 0.5, 10),
    "t2_r": (float, 0.5, 20),
    "trail_atr": (float, 0.5, 10),
    "time_stop_sessions": (int, 2, 100),
    "max_hold_sessions": (int, 2, 250),
    "earnings_days": (int, 0, 60),
}


def _use_dir() -> None:
    sc.BASE_DIR = str(SWING_DIR)


def _path(name: str) -> str:
    _use_dir()
    return sc.path(name)


# ---------------------------------------------------------------------------
# Beallitasok
# ---------------------------------------------------------------------------
def config() -> dict:
    """A swing beallitasai. Ha nincs sajat config.json, a szamlameret es a kockazat a StockWatch
    entry-beallitasabol jon (account_size, risk_per_trade_pct)."""
    _use_dir()
    cfg = sc.load_config(_path("config.json"))
    if sc.load_json(_path("config.json")) is None:
        try:
            entry = load_settings().entry
            cfg["account_usd"] = float(entry.account_size)
            cfg["risk_pct"] = float(entry.risk_per_trade_pct)
        except Exception:  # noqa: BLE001 - a swing a StockWatch beallitasa nelkul is mukodjon
            pass
    cfg.pop("_unknown_keys", None)
    return cfg


def save_config(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Nincs mit menteni.")
    current = sc.load_json(_path("config.json"), {}) or {}
    for key, raw in changes.items():
        if key not in EDITABLE:
            raise ValueError(f"Nem állítható: {key}")
        typ, lo, hi = EDITABLE[key]
        if typ is bool:
            value = bool(raw)
        elif typ is list:
            value = [str(x) for x in (raw or []) if str(x) in sc.SETUPS]
            if not value:
                raise ValueError("Legalább egy setup kell.")
        else:
            try:
                value = typ(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Érvénytelen érték: {key}") from None
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Érvénytelen érték: {key}")
            if not (lo <= value <= hi):
                raise ValueError(f"{key}: {lo} és {hi} között legyen")
        current[key] = value
    if not sc.load_json(_path("config.json")):         # az elso menteskor a tobbi kulcs is rogzul
        base = config()
        current = {k: base[k] for k in ("account_usd", "risk_pct")} | current
    if current.get("t2_r", sc.DEFAULT_CONFIG["t2_r"]) < current.get("t1_r", sc.DEFAULT_CONFIG["t1_r"]):
        raise ValueError("A T2 nem lehet kisebb a T1-nél.")
    sc.save_json(_path("config.json"), current)
    return config()


# ---------------------------------------------------------------------------
# Szures
# ---------------------------------------------------------------------------
def _analyse(sym: str, cfg: dict):
    bars = sc.get_bars(sym, cfg["data_range"])
    if len(bars) < cfg["min_history"]:
        return None, "rövid előzmény"
    ind = sc.indicators(bars)
    i = len(bars) - 1
    if bars[i]["c"] < cfg["min_price"]:
        return None, "ár"
    if (ind["dv20"][i] or 0) < cfg["min_dollar_volume"]:
        return None, "forgalom"
    return {"rs_raw": sc.rs_raw(ind, i), "setups": sc.detect(bars, ind, i, cfg), "ind": ind, "i": i,
            "last": bars[i]["d"]}, None


def scan(max_n: int = 300, symbols: list[str] | None = None, progress=lambda msg: None,
         workers: int = 4) -> dict:
    """NASDAQ (vagy a megadott papirok) atnezese; az eredmeny a candidates.json-ba kerul."""
    _use_dir()
    cfg = config()
    progress("Piaci rezsim (QQQ)...")
    try:
        reg = sc.regime(sc.get_bars(cfg["regime_symbol"], "2y"))
    except Exception as exc:  # noqa: BLE001
        reg = {"state": "unknown", "error": str(exc)[:120]}
    reg["symbol"] = cfg["regime_symbol"]

    if symbols:
        universe = [{"symbol": s, "name": "", "market_cap": None, "sector": ""} for s in symbols]
    else:
        progress("NASDAQ papírlista...")
        universe = sc.load_universe(cfg, False, lambda *a: None)
        universe.sort(key=lambda r: -(r.get("market_cap") or 0))
        if max_n:
            universe = universe[:max_n]
    meta = {r["symbol"]: r for r in universe}

    results, skipped, errors = {}, {}, {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_analyse, r["symbol"], cfg): r["symbol"] for r in universe}
        for n, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                res, why = fut.result()
                if res:
                    results[sym] = res
                else:
                    skipped[why] = skipped.get(why, 0) + 1
            except Exception as exc:  # noqa: BLE001
                errors[sym] = str(exc)[:120]
            if n % 25 == 0 or n == len(universe):
                progress(f"Szűrés: {n} / {len(universe)} papír")

    ranked = sorted((s for s in results if results[s]["rs_raw"] is not None), key=lambda s: results[s]["rs_raw"])
    rs_pct = {s: round(100 * (k + 1) / len(ranked), 1) for k, s in enumerate(ranked)} if ranked else {}
    cands = []
    for sym, res in results.items():
        for s in res["setups"]:
            score, grade = sc.score(s, rs_pct.get(sym, 50.0), res["ind"], res["i"])
            s.update({"symbol": sym, "name": meta.get(sym, {}).get("name", ""),
                      "sector": meta.get(sym, {}).get("sector", ""), "rs": rs_pct.get(sym, 50.0),
                      "score": score, "grade": grade,
                      "valid_until": sc.next_sessions(date.fromisoformat(s["date"]),
                                                      cfg["entry_valid_sessions"])[-1].isoformat()})
            cands.append(s)
    if cands:
        progress("Gyorsjelentés-naptár...")
        cal = sc.earnings_calendar(cfg["earnings_days"], lambda *a: None)
        for c in cands:
            c["earnings"] = cal.get(c["symbol"])
    cands.sort(key=lambda c: -c["score"])
    out = {"generated": sc.now_ny().isoformat(timespec="minutes"), "regime": reg, "universe": len(universe),
           "analysed": len(results), "skipped": skipped, "errors": len(errors),
           "data_through": max((r["last"] for r in results.values()), default=None),
           "max": max_n if not symbols else None, "candidates": cands}
    sc.save_json(_path("candidates.json"), out)
    return {"message": f"Kész: {len(cands)} setup {len(results)} likvid papírból"}


# ---------------------------------------------------------------------------
# Backteszt
# ---------------------------------------------------------------------------
def _bt_symbol(sym: str, bars: list[dict], cfg: dict) -> list[dict]:
    ind = sc.indicators(bars)
    trades, busy_until = [], -1
    for i in range(210, len(bars) - 1):
        if i <= busy_until:
            continue
        if (ind["dv20"][i] or 0) < cfg["min_dollar_volume"] or bars[i]["c"] < cfg["min_price"]:
            continue
        for s in sc.detect(bars, ind, i, cfg):
            s["symbol"] = sym
            t = sc.simulate(bars, i, s, cfg)
            if t is None:
                continue
            t["symbol"] = sym
            last = t.get("closed") or bars[-1]["d"]
            busy_until = next((k for k in range(i, len(bars)) if bars[k]["d"] >= last), len(bars))
            trades.append(t)
            break
    return trades


def bt_stats(trades: list[dict]) -> dict:
    rs = [t["r"] for t in trades]
    if not rs:
        return {"trades": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    streak = worst = 0
    eq = peak = dd = 0.0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    exits: dict[str, int] = {}
    for t in trades:
        ex = [e["reason"] for e in t["events"] if "qty" in e]
        key = ex[-1] if ex else t["status"]
        exits[key] = exits.get(key, 0) + 1
    return {"trades": len(rs), "win_rate": round(100 * len(wins) / len(rs), 1),
            "avg_r": round(sum(rs) / len(rs), 3),
            "avg_win_r": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "total_r": round(sum(rs), 1), "max_drawdown_r": round(dd, 1), "worst_losing_streak": worst,
            "exits": exits}


def backtest(max_n: int = 100, rng: str = "5y", progress=lambda msg: None, workers: int = 4) -> dict:
    _use_dir()
    cfg = config()
    if rng not in ("2y", "5y", "10y"):
        raise ValueError("Időtáv: 2y, 5y vagy 10y")
    progress("NASDAQ papírlista...")
    uni = sc.load_universe(cfg, False, lambda *a: None)
    uni.sort(key=lambda r: -(r.get("market_cap") or 0))
    syms = [r["symbol"] for r in uni[:max_n]]
    trades, errors = [], 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(sc.get_bars, s, rng): s for s in syms}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                trades.extend(_bt_symbol(futs[fut], fut.result(), cfg))
            except Exception:  # noqa: BLE001
                errors += 1
            if n % 10 == 0 or n == len(syms):
                progress(f"Backteszt: {n} / {len(syms)} papír, {len(trades)} kötés")
    trades.sort(key=lambda t: t["opened"])
    out = {"generated": sc.now_ny().isoformat(timespec="minutes"), "range": rng, "symbols": len(syms),
           "errors": errors, "all": bt_stats(trades),
           "by_setup": {name: bt_stats([t for t in trades if t["setup"] == name]) for name in cfg["setups"]},
           "rules": {k: cfg[k] for k in ("t1_r", "t2_r", "t1_fraction", "trail_atr", "time_stop_sessions",
                                         "max_hold_sessions", "entry_valid_sessions", "slippage_bps")},
           "recent": [{k: t.get(k) for k in ("symbol", "setup", "signal_date", "opened", "closed", "r")}
                      for t in trades[-30:]][::-1]}
    sc.save_json(_path("backtest.json"), out)
    return {"message": f"Kész: {out['all']['trades']} kötés, átlag {out['all'].get('avg_r', 0):+.2f}R"}


# ---------------------------------------------------------------------------
# Meretezes es megbizasi jegyek
# ---------------------------------------------------------------------------
def _open_risk(trades: list[dict]) -> float:
    return sum(max(0.0, t["entry"] - t["stop"]) * t["remaining"] for t in trades if t.get("status") == "open")


def size(c: dict, cfg: dict, risk_left: float) -> dict:
    account, risk_pct = float(cfg["account_usd"]), float(cfg["risk_pct"])
    per_share = c["entry"] - c["stop"]
    budget = min(account * risk_pct / 100, max(0.0, risk_left))
    out = {k: c.get(k) for k in ("symbol", "setup", "grade", "entry", "limit", "stop", "t1", "t2",
                                 "valid_until", "earnings", "atr")}
    out["limit"] = out["limit"] or c["entry"]
    out["warnings"] = []
    if per_share <= 0:
        out["qty"] = 0
        out["warnings"].append("A stop nincs az entry alatt.")
        return out
    qty = budget / per_share
    cap = account * cfg["max_position_pct"] / 100 / out["limit"]
    capped = qty > cap
    qty = min(qty, cap)
    qty = round(qty, 4) if cfg["fractional_shares"] else math.floor(qty)
    out.update({"qty": qty, "position_usd": round(qty * c["entry"], 2),
                "position_pct": round(100 * qty * c["entry"] / account, 1) if account else 0,
                "risk_usd": round(qty * per_share, 2), "reward_t1_usd": round(qty * (c["t1"] - c["entry"]), 2)})
    if capped:
        out["warnings"].append(f"A pozíció a számla {cfg['max_position_pct']:g}%-ánál le van vágva.")
    if qty == 0:
        out["warnings"].append("A kockázati keret egy darabra sem elég: hagyd ki, vagy emeld a számlát / kockázatot.")
    fees = 2 * cfg["commission_usd"]
    if qty and fees and fees > 0.1 * out["risk_usd"]:
        out["warnings"].append(f"A díj oda-vissza {fees:.2f} $, a kockázat 10%-ánál több: túl kicsi kötés.")
    if c.get("earnings"):
        out["warnings"].append(f"Gyorsjelentés: {c['earnings']} – a rés átugorhatja a stopot.")
    if budget < account * risk_pct / 100 - 0.005:
        out["warnings"].append("Csökkentve: elérte az összes nyitott kockázat határát.")
    q = out["qty"]
    q1 = q * cfg["t1_fraction"]
    q1 = round(q1, 4) if cfg["fractional_shares"] else math.floor(q1)
    sym = out["symbol"]
    out["tickets"] = [] if not q else [
        f"VÉTEL {q} {sym} STOP-LIMIT: stop {out['entry']:.2f}, limit {out['limit']:.2f}"
        + (f" – töröld, ha {out['valid_until']} zárásáig nem teljesül" if out.get("valid_until") else ""),
        f"Teljesülés után: ELADÁS {q} {sym} STOP {out['stop']:.2f}, GTC (védő stop)",
        (f"ELADÁS {q1} {sym} LIMIT {out['t1']:.2f}, GTC (T1); ha teljesül, a maradék stopja {out['entry']:.2f} (breakeven)"
         if q1 > 0 else f"T1-nél ({out['t1']:.2f}) a stopot tedd {out['entry']:.2f}-ra (breakeven)"),
        f"A maradék célja T2 {out['t2']:.2f}; a követő stopot a Swing fül „Napló frissítése” gombja mondja meg",
    ]
    return out


def plan(symbol: str) -> dict:
    _use_dir()
    cfg = config()
    data = sc.load_json(_path("candidates.json")) or {"candidates": []}
    c = next((x for x in data["candidates"] if x["symbol"] == symbol), None)
    if c is None:
        raise ValueError("Nincs ilyen jelölt a legutóbbi szűrésben.")
    trades = journal()["trades"]
    if any(t["symbol"] == symbol and t["status"] == "open" for t in trades):
        raise ValueError(f"{symbol} már nyitott a naplóban.")
    risk_left = float(cfg["account_usd"]) * cfg["max_total_risk_pct"] / 100 - _open_risk(trades)
    return size(c, cfg, risk_left)


# ---------------------------------------------------------------------------
# Kotesnaplo
# ---------------------------------------------------------------------------
def journal() -> dict:
    return sc.load_json(_path("trades.json"), {"trades": []}) or {"trades": []}


def _save_journal(j: dict) -> None:
    sc.save_json(_path("trades.json"), j)


def _positive(body: dict, key: str, required: bool = True) -> float | None:
    raw = body.get(key)
    if raw in (None, ""):
        if required:
            raise ValueError(f"Hiányzik: {key}")
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Érvénytelen szám: {key}") from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{key} legyen pozitív szám")
    return v


def add_trade(body: dict) -> dict:
    cfg = config()
    sym = str(body.get("symbol") or "").strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError("Érvénytelen ticker")
    qty, fill = _positive(body, "qty"), _positive(body, "fill")
    j = journal()
    if any(t["symbol"] == sym and t["status"] == "open" for t in j["trades"]):
        raise ValueError(f"{sym} már nyitott a naplóban.")
    cands = (sc.load_json(_path("candidates.json")) or {"candidates": []})["candidates"]
    c = next((x for x in cands if x["symbol"] == sym), None) or {}
    stop = _positive(body, "stop", required=False) or c.get("stop")
    if not stop:
        raise ValueError("Add meg a stopot (nincs a legutóbbi szűrésben).")
    if stop >= fill:
        raise ValueError("A stop legyen a vételi ár alatt.")
    r = fill - stop
    t1 = _positive(body, "t1", required=False) or c.get("t1") or round(fill + cfg["t1_r"] * r, 2)
    t2 = _positive(body, "t2", required=False) or c.get("t2") or round(fill + cfg["t2_r"] * r, 2)
    atr_ = c.get("atr")
    if not atr_:
        try:
            atr_ = round(sc.indicators(sc.get_bars(sym, cfg["data_range"]))["atr14"][-1], 2)
        except Exception:  # noqa: BLE001
            atr_ = round(r, 2)
    day = str(body.get("date") or sc.now_ny().date().isoformat())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Dátum: ÉÉÉÉ-HH-NN")
    pos = sc.new_position(sym, fill, float(stop), float(t1), float(t2), float(atr_), day, qty,
                          fractional=bool(cfg["fractional_shares"]))
    pos["setup"] = c.get("setup", "manual")
    pos["last_checked"] = day
    j["trades"].append(pos)
    _save_journal(j)
    return {"message": f"Naplózva: {sym} {qty:g} db @ {fill:g}, stop {stop:g}. A védő stopot most add fel a brókernél!"}


def update_trades(refresh: bool = False) -> dict:
    """Minden nyitott kotesen vegigjatssza az uj, lezart napokat; a teendoket listazza."""
    cfg = config()
    j = journal()
    report = []
    for t in j["trades"]:
        if t["status"] != "open":
            continue
        item = {"symbol": t["symbol"], "actions": []}
        try:
            bars = sc.get_bars(t["symbol"], cfg["data_range"], refresh)
        except Exception as exc:  # noqa: BLE001
            item["actions"].append(f"Nincs adat: {str(exc)[:80]}")
            report.append(item)
            continue
        new = [b for b in bars if b["d"] > t["last_checked"]]
        old_stop = t["stop"]
        for b in new:
            for e in sc.manage_step(t, b, cfg):
                if "qty" in e:
                    item["actions"].append(f"{e['date']}: {REASONS.get(e['reason'], e['reason'])} – "
                                           f"eladás {e['qty']:g} db ~{e['price']:.2f}")
            t["last_checked"] = b["d"]
            if t["status"] != "open":
                break
        if new:
            last = new[-1]["c"]
            item["close"] = last
            item["r_open"] = round((last - t["entry"]) / (t["entry"] - t["initial_stop"]), 2)
        if t["status"] == "open":
            if t["stop"] != old_stop:
                item["actions"].append(f"STOP ÁTHELYEZÉSE {old_stop:.2f} → {t['stop']:.2f} a maradék {t['remaining']:g} darabon")
            left = cfg["time_stop_sessions"] - t["sessions"]
            if not t["t1_hit"] and 0 < left <= 2:
                item["actions"].append(f"Időstop {left} kereskedési nap múlva, ha a T1 ({t['t1']:.2f}) nem jön el")
        else:
            t["r"] = round(sc.position_r(t), 3)
            item["actions"].append(f"Lezárva: {t['r']:+.2f}R")
        if not new:
            item["actions"].append(f"Nincs új lezárt nap {t['last_checked']} óta.")
        report.append(item)
    _save_journal(j)
    return {"report": report, "message": "A napló frissítve. Vesd össze a brókered teljesüléseivel!"}


REASONS = {"stop": "STOP", "breakeven": "BREAKEVEN STOP", "trail": "KÖVETŐ STOP", "t1": "T1", "t2": "T2",
           "time": "IDŐSTOP", "max_hold": "MAX. TARTÁS", "manual": "KÉZI"}


def close_trade(body: dict) -> dict:
    sym = str(body.get("symbol") or "").strip().upper()
    price = _positive(body, "price")
    j = journal()
    t = next((x for x in j["trades"] if x["symbol"] == sym and x["status"] == "open"), None)
    if not t:
        raise ValueError("Nincs ilyen nyitott kötés.")
    qty = _positive(body, "qty", required=False) or t["remaining"]
    if qty > t["remaining"] + 1e-9:
        raise ValueError(f"Legfeljebb {t['remaining']:g} darab adható el.")
    day = sc.now_ny().date().isoformat()
    t["events"].append({"date": day, "qty": qty, "price": price, "reason": "manual"})
    t["remaining"] = round(t["remaining"] - qty, 6)
    if t["remaining"] <= 1e-9:
        t["remaining"], t["status"], t["closed"] = 0, "closed", day
        t["r"] = round(sc.position_r(t), 3)
    _save_journal(j)
    return {"message": f"Rögzítve: {qty:g} db {sym} eladva @ {price:g}."
            + (f" Lezárva: {t['r']:+.2f}R." if t["status"] == "closed" else "")}


def delete_trade(body: dict) -> dict:
    sym = str(body.get("symbol") or "").strip().upper()
    opened = str(body.get("opened") or "")
    j = journal()
    keep = [t for t in j["trades"] if not (t["symbol"] == sym and t["opened"] == opened)]
    if len(keep) == len(j["trades"]):
        raise ValueError("Nincs ilyen kötés.")
    j["trades"] = keep
    _save_journal(j)
    return {"message": f"Törölve a naplóból: {sym} ({opened})."}


def journal_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t["status"] == "closed"]
    rs = [t.get("r", sc.position_r(t)) for t in closed]
    if not rs:
        return {"closed": 0}
    wins = [r for r in rs if r > 0]
    return {"closed": len(rs), "win_rate": round(100 * len(wins) / len(rs), 1),
            "avg_r": round(sum(rs) / len(rs), 2), "total_r": round(sum(rs), 2)}


# ---------------------------------------------------------------------------
# A felulet adatai (halozat nelkul)
# ---------------------------------------------------------------------------
def payload() -> dict:
    _use_dir()
    cfg = config()
    cands = sc.load_json(_path("candidates.json"))
    bt = sc.load_json(_path("backtest.json"))
    trades = journal()["trades"]
    keep = ("symbol", "setup", "entry", "initial_stop", "stop", "t1", "t2", "qty", "remaining", "t1_hit",
            "sessions", "opened", "closed", "status", "r", "last_checked", "events")
    account = float(cfg["account_usd"])
    return {
        "config": {k: cfg[k] for k in EDITABLE} | {"setups_all": list(sc.SETUPS)},
        "scan": cands,
        "backtest": bt,
        "open": [{k: t.get(k) for k in keep} for t in trades if t["status"] == "open"],
        "closed": [{k: t.get(k) for k in keep} for t in trades if t["status"] != "open"][-20:][::-1],
        "stats": journal_stats(trades),
        "risk": {"open_usd": round(_open_risk(trades), 2),
                 "limit_usd": round(account * cfg["max_total_risk_pct"] / 100, 2),
                 "per_trade_usd": round(account * cfg["risk_pct"] / 100, 2)},
        "market_open": sc.market_open(),
    }
