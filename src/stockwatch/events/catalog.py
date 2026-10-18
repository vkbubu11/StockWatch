"""Esemenytipusok - mikor valt NYILVANOSSA az esemeny (ebbol a 0. nap), es mibol valasztjuk ki.

A 0. nap az elso kereskedesi nap, amelynek NYITASA elott az esemeny mar ismert volt ("open"), illetve a nap,
amelynek ZAROJAN valt ismertte ("close": 52 hetes csucs, sajat stratégia jelzese). A kivalasztas csak a
0. napig ismert adatot hasznalja. A 0. napi hozam (elozo zaro -> 0. napi zaro) a reakciot is tartalmazza; a
tanulmany ezert kulon kozli a +1. naptol mert ("utana") kumulalt abnormalis hozamot is.

Idopont nelkuli forras (csak datum): az esemeny a KOVETKEZO kereskedesi naptol szamit ismertnek
(konzervativ - igy biztosan nincs jovobe latas, a reakcio viszont egy nappal korabban is lehetett).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MARKET_TZ = "America/New_York"
OPEN = (9, 30)
YEAR = 252

TYPES = {
    "earnings": {"label": "Gyorsjelentés", "params": {"gap_pct": 2.0}},
    "filing_8k": {"label": "8-K bejelentés", "params": {"item": ""}},
    "high52": {"label": "Új 52 hetes csúcs", "params": {"quiet_days": 20}},
    "low52": {"label": "Új 52 hetes mélypont", "params": {"quiet_days": 20}},
    "gap": {"label": "Nagy nyitó gap", "params": {"gap_pct": 4.0}},
    "strategy": {"label": "Saját stratégia jelzése", "params": {"strategy": "pullback"}},
    "insider_buy": {"label": "Bennfentes vétel (Form 4)", "params": {"min_value": 100_000.0}},
    "insider_sell": {"label": "Bennfentes eladás (Form 4)", "params": {"min_value": 1_000_000.0, "exclude_plans": True}},
    "insider_cluster_buy": {"label": "Csoportos bennfentes vétel (cluster buy)",
                            "params": {"min_insiders": 3, "days": 10, "min_value": 0.0}},
    "sp500_add": {"label": "S&P 500 bekerülés", "params": {}},
    "sp500_remove": {"label": "S&P 500 kikerülés", "params": {}},
}
STRATEGIES = ("pullback", "deep", "etf", "breakout")


@dataclass
class Event:
    symbol: str
    day: pd.Timestamp                    # a 0. nap (kereskedesi nap, idozona nelkul)
    type: str
    timing: str = "open"                 # open: a 0. nap nyitasa elott ismert | close: a 0. nap zarojan
    exact: bool = True                   # pontos idopont (True) vagy csak datum (False -> kovetkezo nap)
    attrs: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Idopont -> 0. nap
# ---------------------------------------------------------------------------
def first_session_on_or_after(index: pd.DatetimeIndex, day) -> pd.Timestamp | None:
    pos = index.searchsorted(pd.Timestamp(day).normalize())
    return index[pos] if pos < len(index) else None


def day0_from_time(index: pd.DatetimeIndex, when: pd.Timestamp) -> pd.Timestamp | None:
    """Az elso kereskedesi nap, amelynek NYITASA (09:30 NY) az idopont utan van."""
    when = pd.Timestamp(when)
    when = when.tz_localize("UTC") if when.tzinfo is None else when
    ny = when.tz_convert(MARKET_TZ)
    day = ny.tz_localize(None).normalize()
    if (ny.hour, ny.minute) >= OPEN:
        day += pd.Timedelta(days=1)
    return first_session_on_or_after(index, day)


def day0_from_date(index: pd.DatetimeIndex, day) -> pd.Timestamp | None:
    """Csak datum ismert: a KOVETKEZO kereskedesi nap (konzervativ)."""
    return first_session_on_or_after(index, pd.Timestamp(day).normalize() + pd.Timedelta(days=1))


# ---------------------------------------------------------------------------
# Detektorok - mindegyik (papir, gyertyak, kontextus) -> esemenyek
# ---------------------------------------------------------------------------
def earnings(symbol: str, df: pd.DataFrame, ctx: dict, gap_pct: float = 2.0) -> list[Event]:
    """Gyorsjelentes. Ismert idopontnal: nyitas elott -> aznap, kulonben a kovetkezo nap. Ismeretlennel a
    kovetkezo kereskedesi nap. Irany: a 0. napi nyito gap (a 0. nap adata - ezert a bontasnal a "+1. naptol"
    mert eredmeny a mervado)."""
    out = []
    times = ctx.get("earnings_times", {}).get(symbol, {})
    for d in ctx.get("earnings_dates", {}).get(symbol, []):
        stamp = times.get(f"{pd.Timestamp(d):%Y-%m-%d}")
        if stamp:
            when = pd.Timestamp(f"{pd.Timestamp(d):%Y-%m-%d} {stamp}").tz_localize(MARKET_TZ)
            day, exact = day0_from_time(df.index, when), True
        else:
            day, exact = day0_from_date(df.index, d), False
        if day is None:
            continue
        i = df.index.get_loc(day)
        if i == 0:
            continue
        gap = float(df["open"].iloc[i] / df["close"].iloc[i - 1] - 1) * 100
        direction = "up" if gap >= gap_pct else ("down" if gap <= -gap_pct else "flat")
        out.append(Event(symbol, day, "earnings", "open", exact,
                         {"report_date": f"{pd.Timestamp(d):%Y-%m-%d}", "gap_pct": round(gap, 3), "direction": direction}))
    return out


def filing_8k(symbol: str, df: pd.DataFrame, ctx: dict, item: str = "") -> list[Event]:
    out = []
    for a in ctx.get("articles", {}).get(symbol, []):
        if a.kind != "filing" or not str(a.form).startswith("8-K"):
            continue
        if item and item not in (a.items or []):
            continue
        day = day0_from_time(df.index, pd.Timestamp(a.published_at))
        if day is not None:
            out.append(Event(symbol, day, "filing_8k", "open", True, {"items": list(a.items or [])}))
    return out


def _extreme(symbol: str, df: pd.DataFrame, kind: str, quiet_days: int) -> list[Event]:
    close = df["close"].astype(float)
    roll = close.rolling(YEAR, min_periods=YEAR)
    hit = (close >= roll.max()) if kind == "high52" else (close <= roll.min())
    hit = hit.fillna(False).to_numpy()
    out, last = [], -10 ** 9
    for i in np.flatnonzero(hit):
        if i - last > quiet_days:                      # az elso uj szelsoertek egy csendes szakasz utan
            out.append(Event(symbol, df.index[i], kind, "close", True, {}))
        last = i
    return out


def high52(symbol, df, ctx, quiet_days: int = 20):
    return _extreme(symbol, df, "high52", int(quiet_days))


def low52(symbol, df, ctx, quiet_days: int = 20):
    return _extreme(symbol, df, "low52", int(quiet_days))


def gap(symbol: str, df: pd.DataFrame, ctx: dict, gap_pct: float = 4.0) -> list[Event]:
    g = (df["open"].astype(float) / df["close"].astype(float).shift(1) - 1) * 100
    out = []
    for day, value in g[g.abs() >= gap_pct].items():
        out.append(Event(symbol, day, "gap", "open", True,
                         {"gap_pct": round(float(value), 3), "direction": "up" if value > 0 else "down"}))
    return out


def strategy(symbol: str, df: pd.DataFrame, ctx: dict, strategy: str = "pullback") -> list[Event]:
    """A sajat strategia jelzesei UGYANAZZAL a motorral, mint elesben (meanrev.setup_mask; Kitoresnel a labor
    kitores-cache-e). A jelzes a 0. nap ZAROJAN szuletik."""
    if strategy == "breakout":
        from .. import lab

        cache = lab.load_cache(symbol) or {}
        days = sorted({pd.Timestamp(t) for t, _, _ in cache.get("events", [])})
        return [Event(symbol, d, "strategy", "close", True, {"strategy": "breakout"}) for d in days if d in df.index]
    from .. import meanrev

    settings = ctx["settings"]
    params = settings.strategies.get(strategy).params
    if len(df) < meanrev.WARMUP + 2:
        return []
    feat = meanrev.features(df, params)
    mctx = ctx.get("market")
    regime = meanrev.regime_series(feat, mctx.regime_frame(symbol, df)) if mctx is not None else None
    mask = meanrev.setup_mask(feat, params, regime).to_numpy().copy()
    mask[: meanrev.WARMUP] = False
    dates = ctx.get("earnings_dates", {}).get(symbol, [])
    out = []
    for i in np.flatnonzero(mask):
        day = feat.index[i]
        if meanrev.earnings_blocked(day.date(), dates, params):
            continue
        out.append(Event(symbol, day, "strategy", "close", True,
                         {"strategy": strategy, "rsi2": round(float(feat["rsi2"].iloc[i]), 2)}))
    return out


def _form4_filings(symbol: str, df: pd.DataFrame, ctx: dict, code: str, exclude_plans: bool) -> list[dict]:
    rows = ctx.get("form4")
    if rows is None or rows.empty:
        return []
    rows = rows[(rows["symbol"] == symbol) & (rows["code"] == code)]
    if exclude_plans:
        rows = rows[~rows["plan_10b5_1"].astype(str).str.lower().isin(("true", "1"))]
    out = []
    for acc, part in rows.groupby("accession"):
        first = part.iloc[0]
        accepted = str(first.get("accepted") or "")
        if accepted and accepted.lower() != "nan":
            day, exact = day0_from_time(df.index, pd.Timestamp(accepted)), True
        else:
            day, exact = day0_from_date(df.index, first["filed"]), False
        if day is None:
            continue
        value = float(pd.to_numeric(part["value"], errors="coerce").fillna(0).sum())
        roles = [r for r, flag in (("igazgató", first["director"]), ("vezető", first["officer"]),
                                   ("10%-os tulajdonos", first["ten_pct"])) if bool(flag)]
        out.append({"day": day, "exact": exact, "value": value, "owner": str(first["owner"]), "roles": roles,
                    "accession": acc})
    return sorted(out, key=lambda r: (r["day"], r["accession"]))


def insider_buy(symbol, df, ctx, min_value: float = 100_000.0):
    return [Event(symbol, f["day"], "insider_buy", "open", f["exact"],
                  {"value": round(f["value"], 2), "roles": f["roles"]})
            for f in _form4_filings(symbol, df, ctx, "P", False) if f["value"] >= float(min_value)]


def insider_sell(symbol, df, ctx, min_value: float = 1_000_000.0, exclude_plans: bool = True):
    return [Event(symbol, f["day"], "insider_sell", "open", f["exact"],
                  {"value": round(f["value"], 2), "roles": f["roles"]})
            for f in _form4_filings(symbol, df, ctx, "S", bool(exclude_plans)) if f["value"] >= float(min_value)]


def insider_cluster_buy(symbol, df, ctx, min_insiders: int = 3, days: int = 10, min_value: float = 0.0):
    """Legalabb `min_insiders` kulonbozo bennfentes nyilt piaci vetele `days` kereskedesi napon belul; az
    esemeny akkor, amikor a kuszobot elero (utolso) vetel ismertte valik. Utana a csoport lezarul, amig
    `days` napig nincs uj vetel."""
    buys = [f for f in _form4_filings(symbol, df, ctx, "P", True) if f["value"] >= float(min_value)]
    pos = {d: i for i, d in enumerate(df.index)}
    buys = [b | {"i": pos[b["day"]]} for b in buys if b["day"] in pos]
    out, quiet_from = [], -1                     # a csoport ennyiedik napig tart (a kesobbi vetelek hosszabbitjak)
    for k, b in enumerate(buys):
        if b["i"] <= quiet_from:
            quiet_from = b["i"] + int(days)
            continue
        window = [x for x in buys[: k + 1] if x["i"] >= b["i"] - int(days)]
        owners = {x["owner"] for x in window}
        if len(owners) >= int(min_insiders):
            out.append(Event(symbol, b["day"], "insider_cluster_buy", "open", all(x["exact"] for x in window),
                             {"insiders": len(owners), "value": round(sum(x["value"] for x in window), 2)}))
            quiet_from = b["i"] + int(days)
    return out


def _index_change(symbol, df, ctx, side: str, kind: str):
    out = []
    for c in ctx.get("index_changes", []):
        if c.get(side) == symbol:
            day = first_session_on_or_after(df.index, c["date"])
            if day is not None:
                # a hatalyos datum ELORE ismert (bejelentve): a 0. nap maga a hatalyos nap, nem "csak datum"
                out.append(Event(symbol, day, kind, "open", True, {"effective": c["date"], "reason": c.get("reason", "")}))
    return out


def sp500_add(symbol, df, ctx):
    return _index_change(symbol, df, ctx, "added", "sp500_add")


def sp500_remove(symbol, df, ctx):
    return _index_change(symbol, df, ctx, "removed", "sp500_remove")


DETECTORS = {"earnings": earnings, "filing_8k": filing_8k, "high52": high52, "low52": low52, "gap": gap,
             "strategy": strategy, "insider_buy": insider_buy, "insider_sell": insider_sell,
             "insider_cluster_buy": insider_cluster_buy, "sp500_add": sp500_add, "sp500_remove": sp500_remove}


def detect(kind: str, symbol: str, df: pd.DataFrame, ctx: dict, params: dict) -> list[Event]:
    if kind not in DETECTORS:
        raise ValueError(f"Ismeretlen esemenytipus: {kind}")
    allowed = TYPES[kind]["params"]
    unknown = set(params) - set(allowed)
    if unknown:
        raise ValueError(f"Ismeretlen parameter ({kind}): {', '.join(sorted(unknown))}")
    return DETECTORS[kind](symbol, df, ctx, **{k: params.get(k, v) for k, v in allowed.items()})
