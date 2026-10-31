"""Kereskedesi naptar (#22): a napi realizalt eredmeny (+ / -) havi naptarban, forrasonkent.

FORRASOK (csak OLVAS - a mentett allapotfajlokbol, a kereskedo modulok importja nelkul; ez a modul semmilyen
megbizasi utat nem er el):
  * virtual   - a jelzett trade-ek virtualis kovetese (data/positions.json, source=virtual). A reszleges kilepes
                (pl. a fele +1R-nel) a SAJAT napjan konyvelodik (TrackedPosition.legs); regi, lab-adat nelkuli
                kotesnel az egesz eredmeny a zaras napjan. Dollar: R x kockazat/db x darabszam (a jelzes terve);
                a Visszahuzas-csaladnal a netto % x belepo x darabszam.
  * paper     - az Alpaca paper trade-ek (data/broker.json): labankent a teljesulesi nap (New York), a kotesi dij
                levonasaval - ugyanaz a keplet, mint a broker._maybe_close (a labak osszege = realised_pnl).
  * holdings  - a Sajat poziciok lezart tetelei (data/holdings.json): (zaro ar - belepo) x darab.
  * backtest  - egy mentett Backteszt-futtatas kotesei: toke x allokacio x netto % (a kilepes napjan).

SZAMITAS: realizalt eredmeny a zaro lab napjan; $ / % / R (% = a nap eredmenye a szamla-meret szazalekaban:
entry.account_size, backtestnel a futtatas tokeje). A het es a honap osszege a napi ertekek osszege.
Hetvege nincs a racsban; a tozsdei unnepnap (NYSE) halvany. A virtualis / szimulalt forras mindig jelolve.
"""
from __future__ import annotations

import calendar as _cal
import csv
import io
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .config import Settings

log = logging.getLogger(__name__)

SOURCES = ("virtual", "paper", "holdings", "backtest")
DEFAULT_SOURCES = ("virtual", "paper", "holdings")
SOURCE_LABELS = {"virtual": "Virtuális követés", "paper": "Paper", "holdings": "Saját pozíciók", "backtest": "Backteszt-futtatás"}
SIMULATED = {"virtual": True, "paper": True, "holdings": False, "backtest": True}
MARKET_TZ = "America/New_York"


@dataclass
class Entry:
    """Egy realizalt resz (lab) egy napon."""

    date: str                    # YYYY-MM-DD (New York-i kereskedesi nap)
    source: str
    trade_id: str
    symbol: str
    strategy: str
    pnl: float | None            # USD
    r: float | None
    kind: str                    # partial | final
    simulated: bool
    entry: float | None = None
    exit: float | None = None
    reason: str = ""
    signal_id: str = ""
    fraction: float | None = None


# ---------------------------------------------------------------------------
# Forrasok
# ---------------------------------------------------------------------------
def _read(path: Path, key: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _ny_day(ts: str | None, fallback: str | None) -> str | None:
    if ts:
        try:
            stamp = pd.Timestamp(ts)
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
            return f"{stamp.tz_convert(MARKET_TZ):%Y-%m-%d}"
        except (ValueError, TypeError):
            pass
    return fallback


def virtual_entries(positions: dict) -> list[Entry]:
    out = []
    for pid, p in positions.items():
        if p.get("source", "virtual") != "virtual" or p.get("status") in ("pending", "skipped"):
            continue
        state = p.get("state") or {}
        entry = p.get("entry_price")
        initial = state.get("initial_stop", p.get("stop"))
        risk = (entry - initial) if entry is not None and initial is not None and entry > initial else None
        shares = p.get("shares") or 0
        base = dict(trade_id=pid, symbol=p.get("symbol", ""), strategy=p.get("strategy", "breakout"), source="virtual",
                    simulated=True, entry=entry, signal_id=p.get("signal_id", ""))
        legs = p.get("legs") or []
        closed = p.get("status") == "closed"
        if legs:
            for n, leg in enumerate(legs):
                final = closed and n == len(legs) - 1
                r = leg.get("r")
                out.append(Entry(date=leg["date"], pnl=round(r * risk * shares, 2) if risk and r is not None else None,
                                 r=r, kind="final" if final else "partial", fraction=leg.get("fraction"),
                                 reason=p.get("reason", "") if final else "partial", **base))
            continue
        if not closed or not p.get("closed_at"):
            continue
        r, pct = p.get("result_r"), p.get("result_pct")
        if pct is not None and entry:
            pnl = round(shares * entry * pct / 100, 2)
        elif r is not None and risk:
            pnl = round(r * risk * shares, 2)
        else:
            pnl = None
        out.append(Entry(date=p["closed_at"], pnl=pnl, r=r, kind="final", reason=p.get("reason", ""), **base))
    return out


def paper_entries(trades: dict) -> list[Entry]:
    """Labankent; a dij oldalankent `commission_pct` a belepo es a kilepo ertekere (mint a broker._maybe_close)."""
    out = []
    for tid, t in trades.items():
        legs = [leg for leg in (t.get("legs") or []) if (leg.get("filled_qty") or 0) > 0 and leg.get("exit_price") is not None]
        if not legs:
            continue
        qty = sum(leg["filled_qty"] for leg in t.get("legs") or [] if (leg.get("filled_qty") or 0) > 0)
        entry = t.get("entry_price")
        risk = (entry - t["stop"]) * qty if entry is not None and t.get("stop") is not None and entry > t["stop"] else None
        fee_pct = (t.get("commission_pct") or 0.0) / 100
        dated = sorted(((_ny_day(leg.get("exit_ts"), t.get("closed_at")), leg) for leg in legs), key=lambda x: x[0] or "")
        for n, (day, leg) in enumerate(dated):
            if day is None:
                continue
            fill = leg.get("entry_fill") or entry or 0.0
            pnl = (leg["exit_price"] - fill) * leg["filled_qty"] - fee_pct * (leg["exit_price"] + fill) * leg["filled_qty"]
            final = t.get("status") == "closed" and n == len(dated) - 1
            out.append(Entry(date=day, source="paper", trade_id=tid, symbol=t.get("symbol", ""),
                             strategy=t.get("strategy", "breakout"), pnl=round(pnl, 2),
                             r=round(pnl / risk, 3) if risk else None, kind="final" if final else "partial",
                             simulated=True, entry=fill, exit=leg["exit_price"], reason=leg.get("exit_reason", ""),
                             signal_id=t.get("signal_id", ""), fraction=round(leg["filled_qty"] / qty, 4) if qty else None))
    return out


def holding_entries(items: dict) -> list[Entry]:
    out = []
    for hid, h in items.items():
        if h.get("status") != "closed" or h.get("close_price") is None or not h.get("closed_at"):
            continue
        entry, close, stop = h["entry_price"], h["close_price"], h.get("stop")
        r = round((close - entry) / (entry - stop), 3) if stop is not None and entry > stop else None
        out.append(Entry(date=h["closed_at"][:10], source="holdings", trade_id=hid, symbol=h.get("symbol", ""),
                         strategy="own", pnl=round((close - entry) * h["shares"], 2), r=r, kind="final",
                         simulated=False, entry=entry, exit=close, reason="manual"))
    return out


def backtest_entries(run: dict | None) -> list[Entry]:
    if not run:
        return []
    capital, alloc = float(run.get("capital") or 0), float(run.get("alloc_pct") or 100) / 100
    out = []
    for n, t in enumerate(run.get("trades") or []):
        if t.get("status") != "closed" or not t.get("exit_date") or t.get("net_pct") is None:
            continue
        out.append(Entry(date=t["exit_date"], source="backtest", trade_id=f"{run.get('id', 'run')}:{n}",
                         symbol=run.get("symbol", ""), strategy=run.get("strategy", ""),
                         pnl=round(capital * alloc * t["net_pct"] / 100, 2), r=t.get("r"), kind="final", simulated=True,
                         entry=t.get("entry_price"), exit=t.get("exit_price"), reason=t.get("reason", "")))
    return out


def load_entries(settings: Settings, sources: tuple[str, ...] = DEFAULT_SOURCES, run_id: str | None = None) -> list[Entry]:
    out: list[Entry] = []
    if "virtual" in sources:
        out += virtual_entries(_read(settings.positions_path, "positions"))
    if "paper" in sources:
        out += paper_entries(_read(settings.broker_path, "trades"))
    if "holdings" in sources:
        out += holding_entries(_read(settings.holdings_path, "holdings"))
    if "backtest" in sources and run_id:
        from .backtest_page import runs

        run = runs.load(run_id)
        out += backtest_entries(run | {"id": run_id} if run else None)
    return sorted(out, key=lambda e: (e.date, e.source, e.symbol))


def filter_entries(entries: list[Entry], strategy: str | None = None, symbol: str | None = None) -> list[Entry]:
    return [e for e in entries if (not strategy or e.strategy == strategy) and (not symbol or e.symbol == symbol.upper())]


# ---------------------------------------------------------------------------
# Osszesites
# ---------------------------------------------------------------------------
@lru_cache(maxsize=16)
def trading_days(year: int) -> frozenset[str]:
    try:
        import pandas_market_calendars as mcal

        days = mcal.get_calendar("NYSE").valid_days(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
        return frozenset(f"{d:%Y-%m-%d}" for d in days)
    except Exception:                                       # naptar nelkul: minden hetkoznap kereskedesi nap
        log.warning("A tozsdei naptar nem erheto el - minden hetkoznap kereskedesi napnak szamit")
        days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
        return frozenset(f"{d:%Y-%m-%d}" for d in days)


def _blank() -> dict:
    return {"pnl": 0.0, "r": 0.0, "n": 0, "trades": 0, "wins": 0, "losses": 0, "pnl_known": 0, "r_known": 0}


def _add(acc: dict, e: Entry) -> None:
    acc["n"] += 1
    if e.kind == "final":
        acc["trades"] += 1
    if e.pnl is not None:
        acc["pnl"] += e.pnl
        acc["pnl_known"] += 1
    if e.r is not None:
        acc["r"] += e.r
        acc["r_known"] += 1


def _finish(acc: dict, account: float) -> dict:
    out = dict(acc)
    out["pnl"] = round(acc["pnl"], 2) if acc["pnl_known"] else None
    out["r"] = round(acc["r"], 3) if acc["r_known"] else None
    out["pct"] = round(acc["pnl"] / account * 100, 3) if acc["pnl_known"] and account > 0 else None
    return out


def daily(entries: list[Entry], account: float) -> dict[str, dict]:
    days: dict[str, dict] = {}
    for e in entries:
        acc = days.setdefault(e.date, _blank())
        _add(acc, e)
        if e.pnl is not None:
            acc["wins" if e.pnl > 0 else "losses"] += 1 if e.pnl != 0 else 0
    return {d: _finish(acc, account) for d, acc in days.items()}


def _sum(cells: list[dict], account: float) -> dict:
    acc = _blank()
    for c in cells:
        if c is None:
            continue
        acc["n"] += c["n"]
        acc["trades"] += c["trades"]
        acc["wins"] += c["wins"]
        acc["losses"] += c["losses"]
        if c["pnl"] is not None:
            acc["pnl"] += c["pnl"]
            acc["pnl_known"] += 1
        if c["r"] is not None:
            acc["r"] += c["r"]
            acc["r_known"] += 1
    return _finish(acc, account)


def account_size(settings: Settings, run: dict | None = None) -> float:
    if run and run.get("capital"):
        return float(run["capital"])
    return float(settings.entry.account_size)


def month(settings: Settings, year: int, mon: int, sources: tuple[str, ...] = DEFAULT_SOURCES,
          strategy: str | None = None, symbol: str | None = None, run_id: str | None = None,
          entries: list[Entry] | None = None) -> dict:
    """Havi naptar: hetek (hetfo-pentek), napi cellak, heti es havi osszeg."""
    if not 1 <= mon <= 12 or not 1970 <= year <= 2100:
        raise ValueError("Ervenytelen honap")
    all_entries = entries if entries is not None else load_entries(settings, sources, run_id)
    picked = filter_entries(all_entries, strategy, symbol)
    run = _run(run_id) if "backtest" in sources and run_id else None
    account = account_size(settings, run)
    by_day = daily(picked, account)
    trading = trading_days(year)
    weeks = []
    for week in _cal.Calendar(firstweekday=0).monthdatescalendar(year, mon):
        cells = []
        for d in week[:5]:
            key = f"{d:%Y-%m-%d}"
            if d.year != year:
                is_trading = key in trading_days(d.year)
            else:
                is_trading = key in trading
            cell = by_day.get(key)
            cells.append({"date": key, "day": d.day, "in_month": d.month == mon, "trading": is_trading,
                          "holiday": not is_trading} | ({"value": cell} if cell else {"value": None}))
        weeks.append({"days": cells, "total": _sum([c["value"] for c in cells if c["in_month"]], account)})
    month_cells = {k: v for k, v in by_day.items() if k.startswith(f"{year:04d}-{mon:02d}-")}
    total = _sum(list(month_cells.values()), account)
    known = [(k, v["pnl"]) for k, v in month_cells.items() if v["pnl"] is not None]
    total |= {"days_up": sum(1 for _, v in known if v > 0), "days_down": sum(1 for _, v in known if v < 0),
              "best": dict(zip(("date", "pnl"), max(known, key=lambda x: x[1]))) if known else None,
              "worst": dict(zip(("date", "pnl"), min(known, key=lambda x: x[1]))) if known else None}
    dates = [e.date for e in picked]
    return {"year": year, "month": mon, "sources": list(sources), "strategy": strategy, "symbol": symbol,
            "run": run_id, "account": account, "weeks": weeks, "total": total,
            "first": min(dates) if dates else None, "last": max(dates) if dates else None,
            "available": {s: sum(1 for e in all_entries if e.source == s) for s in SOURCES},
            "strategies": sorted({e.strategy for e in all_entries}), "symbols": sorted({e.symbol for e in all_entries}),
            "simulated": any(e.simulated for e in picked), "labels": SOURCE_LABELS}


def year_view(settings: Settings, year: int, sources: tuple[str, ...] = DEFAULT_SOURCES, strategy: str | None = None,
              symbol: str | None = None, run_id: str | None = None) -> dict:
    """Eves attekinto: 12 mini-naptar (napi eredmeny hoterkephez) + havi osszegek."""
    if not 1970 <= year <= 2100:
        raise ValueError("Ervenytelen ev")
    picked = filter_entries(load_entries(settings, sources, run_id), strategy, symbol)
    run = _run(run_id) if "backtest" in sources and run_id else None
    account = account_size(settings, run)
    by_day = {k: v for k, v in daily(picked, account).items() if k.startswith(f"{year:04d}-")}
    trading = trading_days(year)
    months = []
    for mon in range(1, 13):
        cells = {k: v for k, v in by_day.items() if k[5:7] == f"{mon:02d}"}
        first = date(year, mon, 1)
        days = [{"date": f"{first + timedelta(days=i):%Y-%m-%d}", "dow": (first + timedelta(days=i)).weekday()}
                for i in range(_cal.monthrange(year, mon)[1])]
        days = [d | {"trading": d["date"] in trading, "pnl": (cells.get(d["date"]) or {}).get("pnl"),
                     "r": (cells.get(d["date"]) or {}).get("r")} for d in days if d["dow"] < 5]
        months.append({"month": mon, "total": _sum(list(cells.values()), account), "days": days})
    return {"year": year, "months": months, "total": _sum(list(by_day.values()), account), "account": account}


def day_detail(settings: Settings, day: str, sources: tuple[str, ...] = DEFAULT_SOURCES, strategy: str | None = None,
               symbol: str | None = None, run_id: str | None = None) -> dict:
    datetime.strptime(day, "%Y-%m-%d")
    picked = [e for e in filter_entries(load_entries(settings, sources, run_id), strategy, symbol) if e.date == day]
    run = _run(run_id) if "backtest" in sources and run_id else None
    account = account_size(settings, run)
    return {"date": day, "entries": [asdict(e) for e in picked], "total": daily(picked, account).get(day)}


def to_csv(settings: Settings, sources: tuple[str, ...] = DEFAULT_SOURCES, strategy: str | None = None,
           symbol: str | None = None, run_id: str | None = None) -> str:
    picked = filter_entries(load_entries(settings, sources, run_id), strategy, symbol)
    run = _run(run_id) if "backtest" in sources and run_id else None
    rows = daily(picked, account_size(settings, run))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "pnl_usd", "pct_of_account", "r", "closed_trades", "legs", "sources"])
    for d in sorted(rows):
        v = rows[d]
        w.writerow([d, v["pnl"], v["pct"], v["r"], v["trades"], v["n"],
                    "|".join(sorted({e.source for e in picked if e.date == d}))])
    return buf.getvalue()


def _run(run_id: str | None) -> dict | None:
    if not run_id:
        return None
    from .backtest_page import runs

    return runs.load(run_id)


def parse_sources(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_SOURCES
    picked = tuple(s for s in raw.split(",") if s in SOURCES)
    if not picked:
        raise ValueError("Ismeretlen forras")
    return picked
