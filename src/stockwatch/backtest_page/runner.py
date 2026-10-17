"""Egy Backteszt-futtatas: egy papir, egy strategia, egy idoszak.

EGYETLEN IGAZSAGFORRAS: a jelzesek es a kotesek ugyanazokbol a fuggvenyekbol jonnek, mint az eles
futasban es a kutatasban (meanrev.features / setup_mask / exit_flags / simulate_trade,
meanrev_research.candidates / portfolio / stats). Itt csak az adat, az idoszak es a kimenet mas.

BECSULETESSEG
  * az adat a vizsgalt idoszak vegen VAGODIK (a kesobbi gyertyak nem latszanak - a vegen nyitott
    kotes nyitottkent jelenik meg, nem "tudjuk, hogyan vegzodott")
  * alapbol a zarolt holdout elott er veget (entry_research.HOLDOUT_START); megnyitasa naplozva
  * a rezsim-index rovidebb tortenete, a hianyos gyorsjelentes-naptar, a mai indextagsag es a
    kis minta figyelmeztetesben jelenik meg
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import market, meanrev, performance
from .. import meanrev_research as mr
from ..config import PullbackParams, Settings, STRATEGY_LABELS
from ..entry_research import HOLDOUT_START, SPLIT_DATE, log_holdout_opening, wilson
from . import data

log = logging.getLogger(__name__)

PULLBACK_FAMILY = ("pullback", "deep", "etf", "sandbox")
MIN_TRADES = 30                         # ez alatt a kotesszam alatt figyelmeztetes: foleg zaj
DEFAULT_CAPITAL = 10_000.0


def strategy_choices(settings: Settings) -> list[dict]:
    """A feluletnek: mely strategiak futtathatok es milyen alapparameterekkel."""
    out = []
    for sid in ("pullback", "deep", "etf", "sandbox", "breakout"):
        switch = settings.strategies.get(sid)
        engine = settings.strategies.sandbox.engine if sid == "sandbox" else ("breakout" if sid == "breakout" else "pullback")
        ok = engine == "pullback"
        out.append({"id": sid, "label": STRATEGY_LABELS[sid], "engine": engine, "available": ok,
                    "note": None if ok else "A Kitores-motor backtestje a kovetkezo fazisban jon (labor-szolgaltatas).",
                    "params": switch.params.model_dump() if ok and hasattr(switch, "params") else None})
    return out


def resolve_params(settings: Settings, strategy: str, overrides: dict | None, cost_bps: float | None) -> PullbackParams:
    if strategy not in PULLBACK_FAMILY:
        if strategy == "breakout":
            raise ValueError("A Kitores-motor backtestje meg nem erheto el ezen az oldalon (a kovetkezo fazisban jon).")
        raise ValueError(f"Ismeretlen strategia: {strategy}")
    if strategy == "sandbox" and settings.strategies.sandbox.engine != "pullback":
        raise ValueError("A Homokozo most a Kitores-motorral fut - az meg nem futtathato ezen az oldalon.")
    base = settings.strategies.get(strategy).params.model_dump()
    overrides = dict(overrides or {})
    unknown = set(overrides) - set(base)
    if unknown:
        raise ValueError(f"Ismeretlen parameter: {', '.join(sorted(unknown))}")
    if cost_bps is not None:
        overrides["cost_bps"] = cost_bps
    for key, value in overrides.items():                  # tipus a strategia alapertekebol
        if isinstance(base[key], bool):
            overrides[key] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "igen", "yes")
        elif isinstance(base[key], int):
            overrides[key] = int(float(value))
        elif isinstance(base[key], float):
            overrides[key] = float(value)
    return PullbackParams(**(base | overrides))           # a teljes validacio lefut


def _context(settings: Settings, end: pd.Timestamp) -> tuple[market.MarketContext, dict]:
    """A rezsim indexei a Backteszt-tarbol (hosszu tortenet), a tagsag a mai listakbol."""
    base = market.load_context(settings, refresh=False, allow_network=False)
    benches, firsts = {}, {}
    for symbol in market.REGIME_INDEXES:
        frame = data.load(symbol)
        if frame.empty:
            frame = base.benches.get(symbol)
        frame = frame if frame is None else frame[frame.index <= end]
        benches[symbol] = frame
        firsts[symbol] = f"{frame.index[0]:%Y-%m-%d}" if frame is not None and not frame.empty else None
    return market.MarketContext(benches, base.nasdaq, base.sp500), firsts


def _equity(df: pd.DataFrame, trades: list[meanrev.Trade], start: pd.Timestamp, capital: float,
            alloc: float, cost_bps: float) -> tuple[pd.Series, pd.Series]:
    """Napi tokegorbe: a kotesben a toke `alloc` resze a papirral mozog, kotesen kivul keszpenz.
    A koltseg a belepes es a kilepes napjan vonodik le (oldalankent `cost_bps`)."""
    frame = df[df.index >= start]
    close = frame["close"]
    ret = pd.Series(0.0, index=frame.index)
    inpos = pd.Series(0.0, index=frame.index)
    c = cost_bps / 10_000
    pos = {d: i for i, d in enumerate(frame.index)}
    for t in trades:
        if t.entry_date is None or t.entry_date not in pos:
            continue
        j = pos[t.entry_date]
        k = pos.get(t.exit_date, len(frame) - 1) if t.exit_date is not None else len(frame) - 1
        for d in range(j, k + 1):
            prev = t.entry_price if d == j else close.iloc[d - 1]
            now = t.exit_price if (t.exit_date is not None and d == k) else close.iloc[d]
            r = now / prev - 1
            if d == j:
                r -= c
            if t.exit_date is not None and d == k:
                r -= c
            ret.iloc[d] += alloc * r
            inpos.iloc[d] = 1.0
    return capital * (1 + ret).cumprod(), inpos


def _period(orders: list[meanrev.Trade], params: PullbackParams, lo, hi) -> dict:
    sel = [t for t in orders if t.signal_date >= lo and t.signal_date <= hi]
    s = mr.stats(sel, sel, lo, hi, params)
    return {k: v for k, v in s.items() if k != "reasons"} | {"reasons": s.get("reasons") or {}}


def run(symbol: str, strategy: str, settings: Settings, start=None, end=None, overrides: dict | None = None,
        cost_bps: float | None = None, include_holdout: bool = False, capital: float = DEFAULT_CAPITAL,
        alloc_pct: float = 100.0, progress=None) -> dict:
    progress = progress or (lambda msg: None)
    symbol = data.clean_symbol(symbol)
    params = resolve_params(settings, strategy, overrides, cost_bps)
    if not 1 <= alloc_pct <= 100:
        raise ValueError("A pozicio merete a toke 1-100%-a lehet.")
    if not 100 <= capital <= 1e9:
        raise ValueError("Ervenytelen kezdotoke.")
    df = data.load(symbol)
    if df.empty:
        raise ValueError(f"Nincs letoltott adat: {symbol}.")
    data.touch(symbol)
    m = data.meta(symbol) or {}

    first_bar = df.index[0]
    start = max(pd.Timestamp(start).normalize(), first_bar) if start else first_bar
    hidden_end = HOLDOUT_START - pd.Timedelta(days=1)
    end = pd.Timestamp(end).normalize() if end else df.index[-1]
    if not include_holdout:
        end = min(end, hidden_end)
    end = min(end, df.index[-1])
    if end <= start:
        raise ValueError("A vizsgalt idoszak ures (a vege nem kesobbi a kezdetenel).")
    df = df[df.index <= end]                              # a kesobbi gyertyak NEM latszanak
    if len(df) < meanrev.WARMUP + 20:
        raise ValueError(f"Tul rovid tortenet: {len(df)} gyertya (legalabb {meanrev.WARMUP + 20} kell).")

    progress("Jellemzok es rezsim...")
    ctx, bench_first = _context(settings, end)
    feat = meanrev.features(df, params)
    regime = meanrev.regime_series(feat, ctx.regime_frame(symbol, df))
    earnings_dates, earnings_note = [], None
    if params.earnings_blackout_days > 0:
        try:
            hist = market.HistoricalEarnings(settings)
            earnings_dates = hist.dates(symbol)
            hist.save()
        except Exception as exc:                          # nem ismert -> a szuro nem blokkol
            log.warning("Gyorsjelentesi datumok (%s): %s", symbol, exc)
    sd = mr.SymbolData(feat, regime, earnings_dates)

    progress("Jelzesek es kotesek...")
    cands = [t for t in mr.candidates({symbol: sd}, params) if t.signal_date >= start]
    orders, _ = mr.portfolio(cands, params)
    orders.sort(key=lambda t: t.signal_date)
    holdout_opened = bool(include_holdout and end >= HOLDOUT_START)
    if holdout_opened:
        log_holdout_opening([f"backtest-page {symbol} {strategy}: " + str(params.model_dump())], 1, study="backtest-page")

    progress("Statisztika...")
    periods = {"all": _period(orders, params, start, end)}
    if start < SPLIT_DATE:
        periods["train"] = _period(orders, params, start, min(end, SPLIT_DATE - pd.Timedelta(days=1)))
    if end >= SPLIT_DATE and start < HOLDOUT_START:
        periods["test"] = _period(orders, params, max(start, SPLIT_DATE), min(end, hidden_end))
    if holdout_opened:
        periods["holdout"] = _period(orders, params, max(start, HOLDOUT_START), end)

    alloc = alloc_pct / 100
    equity, inpos = _equity(df, orders, start, capital, alloc, params.total_cost_bps)
    bh = capital * df["close"][df.index >= start] / float(df["close"][df.index >= start].iloc[0])
    strat_m, bh_m = performance.metrics(equity, inpos), performance.metrics(bh)
    years_strat, years_bh = performance.yearly_returns(equity), performance.yearly_returns(bh)
    closed = [t for t in orders if t.status == "closed"]
    by_year = {}
    for t in closed:
        by_year.setdefault(t.signal_date.year, []).append(t.net_pct)
    years = [{"year": y, "strategy": years_strat.get(y), "buy_hold": years_bh.get(y),
              "n": len(by_year.get(y, [])),
              "win": float(np.mean(np.array(by_year[y]) > 0)) if by_year.get(y) else None}
             for y in sorted(years_strat)]

    warnings = []
    n = periods["all"].get("n") or 0
    if n < MIN_TRADES:
        warnings.append(f"Csak {n} lezart kotes - kis minta, az eredmeny foleg zaj (a 95%-os sav szeles).")
    below = float((df["close"][df.index >= start] < params.min_price).mean()) if params.min_price > 0 else 0.0
    if below > 0.05:
        warnings.append(f"A korrigalt ar az idoszak {below * 100:.0f}%-aban a min. ar ({params.min_price:g} $) alatt van - "
                        "a split-korrekcio miatt a regi arak kisebbnek latszanak, mint akkor voltak, ezert ott nincs "
                        "jelzes. A teljes idoszakhoz allitsd a min. arat 0-ra.")
    for issue in (m.get("quality") or {}).get("issues", []):
        warnings.append(f"Adat: {issue}.")
    source = ctx.regime_source(symbol)
    if params.regime_filter:
        idx = source if source != market.SELF else None
        if idx and bench_first.get(idx) and pd.Timestamp(bench_first[idx]) + pd.Timedelta(days=300) > start:
            warnings.append(f"A rezsim indexe ({idx}) csak {bench_first[idx]}-tol ismert: elotte (+200 nap SMA) "
                            "a rezsim 'nem ismert', es nincs belepes.")
        if idx:
            warnings.append("A rezsim forrasa a MAI indextagsagbol jon (a papir regen nem biztos, hogy tag volt).")
    if params.earnings_blackout_days > 0:
        if not earnings_dates:
            earnings_note = "a gyorsjelentesi datumok nem ismertek - a tilalom nem blokkolt"
        elif min(earnings_dates) > start.date():
            earnings_note = f"a gyorsjelentesi datumok csak {min(earnings_dates)}-tol ismertek - elotte a tilalom nem blokkolt"
        if earnings_note:
            warnings.append(f"Gyorsjelentes-tilalom: {earnings_note}.")
    if holdout_opened:
        warnings.append(f"A ZAROLT holdout ({HOLDOUT_START:%Y-%m-%d}-tol) is benne van - a megnyitas naplozva.")
    warnings.append("Tulelesi torzitas: csak ma letezo papir valaszthato; egy papir nem bizonyit strategiat.")

    def ser(s: pd.Series, digits: int = 2) -> dict:
        s = performance.downsample(s)
        return {"t": [f"{d:%Y-%m-%d}" for d in s.index], "v": [round(float(v), digits) for v in s.to_numpy()]}

    price = df["close"][df.index >= start]
    dd = performance.drawdown(equity)
    trades_out = []
    for t in orders:
        d = t.to_dict()
        if t.status == "closed" and t.entry_price:
            d["r"] = round((t.exit_price - t.entry_price) / (t.entry_price - t.stop), 2) if t.entry_price > t.stop else None
        trades_out.append(d)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol, "strategy": strategy, "strategy_label": STRATEGY_LABELS[strategy],
        "start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}", "holdout": holdout_opened,
        "hidden_after": None if holdout_opened else f"{hidden_end:%Y-%m-%d}",
        "params": params.model_dump(), "overrides": overrides or {}, "capital": capital, "alloc_pct": alloc_pct,
        "regime_source": source, "data": {k: m.get(k) for k in ("first", "last", "bars", "fetched_at", "quality")},
        "periods": periods, "strategy_metrics": strat_m, "buy_hold_metrics": bh_m, "years": years,
        "win_ci": wilson(sum(1 for t in closed if t.net_pct > 0), len(closed)),
        "equity": ser(equity), "buy_hold": ser(bh), "drawdown": ser(dd),
        "price": ser(price), "trades": trades_out, "warnings": warnings,
        "open_at_end": sum(1 for t in orders if t.status == "open"),
    }


def summary_line(result: dict) -> str:
    a, sm, bm = result["periods"]["all"], result["strategy_metrics"], result["buy_hold_metrics"]

    def f(v, suffix="%", d=1):
        return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.{d}f}{suffix}"
    win = "-" if a.get("win") is None else f"{a['win'] * 100:.1f}%"
    return (f"{result['symbol']} {result['strategy_label']} {result['start']} -> {result['end']}: "
            f"{a.get('n', 0)} kotes, nyeresi arany {win}, atlag {f(a.get('ev'), '%', 2)}, "
            f"CAGR {f(sm.get('cagr'))} (vettem es tartottam {f(bm.get('cagr'))}), "
            f"max DD {f(sm.get('max_dd'))} ({f(bm.get('max_dd'))})")
