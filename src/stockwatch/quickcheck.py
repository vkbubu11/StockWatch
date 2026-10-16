"""Gyors kiertekeles: egy beirt papir allapota MOST - a fooldali "Papir kiertekelese" ablak.

Ugyanazokkal a fuggvenyekkel szamol, mint az ertesitesek (entry.py, meanrev.py), harom reteggel:

  * jelzesek     - amit a monitor most is jelezne (kitores, visszateszt, elokeszulet)
  * formalodo    - a bikas alakzatok, amelyek (meg) nem adnak jelzest: a triggertol tavolabb alakulo
                   minta, illetve a MA kitort, de meg meg nem erositett kitores. Ezeket ugyanazzal a
                   szurokeszlettel es pontozassal ertekeljuk, "ha most neznenk" alapon - NEM jelzes.
  * visszahuzas  - a Visszahuzas-csalad strategiai (RSI2): melyik szabaly teljesul most, es mennyi
                   hianyzik a jelzeshez.

Nyitvatartas alatt ket nezet keszul: "elo" (a mai, meg alakulo gyertyaval - mintha most zarna) es a
legutobbi LEZART nap (amit a monitor tenylegesen ertekel). Az elo nezet volumene reszleges.

Semmit nem kuld ki, nem kereskedik es nem naploz - csak megmutatja.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pandas as pd

from . import best, config, entry, indicators, market, meanrev, monitor, sessions, shadow
from .config import Settings, load_watchlist
from .forecast import Calibration, analyse

log = logging.getLogger(__name__)

MIN_BARS = 60                                   # ennel rovidebb historiaval az alakzatkereses ertelmetlen
PULLBACK_IDS = ("pullback", "deep", "etf")


def _brief(s: entry.EntrySignal, cfg) -> dict:
    d = s.to_dict()
    d.update(qualifies=s.qualifies(cfg), label=entry.KIND_LABELS.get(s.kind, s.kind),
             failed=[c.name for c in s.failed_checks], exit_lines=entry.exit_plan_lines(s.plan)[2:])
    return d


def _distance(match, close: float, atr: float) -> tuple[float | None, float | None]:
    """A trigger tavolsaga a zarotol ATR-ben es szazalekban (pozitiv: a trigger a zaro felett)."""
    if not close:
        return None, None
    gap = match.trigger_level - close
    return (round(gap / atr, 2) if atr > 0 else None), round(gap / close * 100, 2)


def _what_if(frame: pd.DataFrame, cfg):
    """A jelzest (meg) nem ado bikas mintak helyzete, "ha most neznenk"."""
    today = frame.index[-1]
    close = float(frame["close"].iloc[-1])

    def kind_of(match):
        if match.direction != "bullish" or entry.classify(match, frame, cfg):
            return None
        if match.status == "forming" and match.trigger_level >= close:
            return "setup"
        if match.status == "triggered" and match.trigger_date == today:
            return "breakout"                   # ma tort ki, a megerosites holnap jon
        return None

    return kind_of


def _patterns(matches, frame: pd.DataFrame) -> list[dict]:
    close, atr = float(frame["close"].iloc[-1]), entry._atr(frame)
    out = []
    for m in matches:
        if m.status not in ("forming", "triggered"):
            continue
        dist_atr, dist_pct = _distance(m, close, atr)
        out.append({"name": m.name, "direction": m.direction, "status": m.status, "quality": round(m.quality, 3),
                    "trigger": round(m.trigger_level, 2), "target": round(m.target, 2),
                    "invalidation": round(m.invalidation, 2), "risk_reward": round(m.risk_reward, 2),
                    "start": m.start_date, "end": m.end_date, "trigger_date": m.trigger_date,
                    "distance_atr": dist_atr, "distance_pct": dist_pct})
    return out


def _pullback(symbol: str, frame: pd.DataFrame, settings: Settings, ctx: market.MarketContext,
              earnings_date: date | None, universes: dict[str, set[str]]) -> list[dict]:
    """A Visszahuzas-csalad strategiai: szabalyonkent, mi teljesul most (a jelzes a ZARON dol el)."""
    out = []
    regime = ctx.regime(symbol, frame, frame.index[-1])
    for sid in PULLBACK_IDS:
        switch = settings.strategies.get(sid)
        params = switch.params
        row = {"strategy": sid, "label": config.STRATEGY_LABELS.get(sid, sid), "enabled": switch.enabled,
               "in_universe": symbol in universes.get(sid, set()), "rsi2_max": params.rsi2_max}
        if len(frame) < meanrev.WARMUP:
            out.append({**row, "error": f"tul rovid historia (min. {meanrev.WARMUP} nap)"})
            continue
        # Az RSI(2) kuszobot feloldjuk, hogy a tobbi szabaly allapota is latszodjon; a valodi
        # RSI(2)-feltetelt utana kulon ertekeljuk.
        loose = params.model_copy(update={"rsi2_max": 101.0})
        signal = meanrev.evaluate(symbol, frame, settings, loose, regime, earnings_date, sid)
        if signal is None:
            out.append({**row, "error": "nincs eleg adat (ATR)"})
            continue
        extras = signal.pattern.extras or {}
        rsi2 = float(extras.get("rsi2", 100.0))
        checks = [entry.Check("RSI(2)", rsi2 < params.rsi2_max, f"{rsi2:.1f} (jelzes {params.rsi2_max:g} alatt)")]
        checks += signal.checks[1:]
        failed = [c.name for c in checks if c.passed is False]
        plan = signal.plan
        out.append({**row, "passed": not failed, "failed": failed, "rsi2": round(rsi2, 2),
                    "crsi": extras.get("crsi"), "natr": extras.get("natr"),
                    "met": sum(1 for c in checks if c.passed is not False), "total": len(checks),
                    "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
                    "limit": round(plan.entry_limit, 2), "stop": round(plan.stop, 2), "shares": plan.shares,
                    "limit_pct": params.limit_pct})
    return out


def _view(symbol: str, frame: pd.DataFrame, settings: Settings, calibration: Calibration,
          ctx: market.MarketContext, earnings_date: date | None, universes: dict[str, set[str]],
          complete: bool) -> dict:
    cfg = settings.entry
    forecast = analyse(symbol, frame, settings, calibration)
    last = frame.iloc[-1]
    view = {
        "as_of": f"{frame.index[-1]:%Y-%m-%d}", "complete": complete, "price": float(last["close"]),
        "change_pct": (float(last["close"]) / float(frame["close"].iloc[-2]) - 1) * 100 if len(frame) > 1 else None,
        "volume_ratio": last.get("vol_ratio"), "rsi": last.get("rsi"), "atr": last.get("atr"),
        "atr_pct": last.get("atr_pct"), "adx": last.get("adx"),
        "forecast": {"label": forecast.label, "score": forecast.score, "direction": forecast.direction,
                     "pattern": forecast.pattern.name if forecast.pattern else None,
                     "probability": forecast.probability_text,
                     "components": [{"name": c.name, "score": c.score, "reason": c.reason}
                                    for c in forecast.components]},
        "signals": [], "forming": [], "patterns": _patterns(forecast.matches, frame),
        "pullback": _pullback(symbol, frame, settings, ctx, earnings_date, universes),
    }
    if len(frame) < MIN_BARS:
        return view

    bench = ctx.bench_for(symbol)
    common = dict(calibration=calibration, benchmark=bench, earnings_date=earnings_date,
                  support=forecast.support, resistance=forecast.resistance,
                  regime=ctx.regime(symbol, frame, frame.index[-1]))
    actual = entry.evaluate(symbol, frame, forecast.matches, settings, **common)
    forming = entry.evaluate(symbol, frame, forecast.matches, settings, kind_of=_what_if(frame, cfg), **common)
    shadow.score_signals(actual + forming, frame, settings, bench)
    for key, signals, hypothetical in (("signals", actual, False), ("forming", forming, True)):
        for s in signals:
            d = _brief(s, cfg)
            dist_atr, dist_pct = _distance(s.pattern, float(last["close"]), entry._atr(frame))
            d.update(hypothetical=hypothetical, distance_atr=dist_atr, distance_pct=dist_pct,
                     status=s.pattern.status)
            if hypothetical:
                d["qualifies"] = False          # "ha most neznenk" - nem jelzes
                d["would_pass"] = s.passed and s.score >= cfg.min_score
            view[key].append(d)
    return view


def evaluate_symbol(symbol: str, settings: Settings | None = None, refresh: bool = True) -> dict:
    """A papir teljes gyors kiertekelese. ValueError: nincs (eleg) adat."""
    config.load_settings.cache_clear()
    config.load_watchlist.cache_clear()
    settings = settings or config.load_settings()
    symbol = symbol.strip().upper()
    watch = set(load_watchlist().symbols)

    refresh_error = None
    if refresh:
        try:
            best._refresh([symbol], settings)
        except Exception as exc:                # a tarolt adattal megyunk tovabb
            log.warning("Gyors kiertekeles: %s frissitese sikertelen: %s", symbol, exc)
            refresh_error = f"{type(exc).__name__}: {exc}"[:200]
    df = best._frame(symbol, settings)
    if df.empty:
        raise ValueError(f"Nincs arfolyamadat: {symbol} (letezo ticker?)")
    if len(df) < MIN_BARS:
        raise ValueError(f"{symbol}: tul rovid historia ({len(df)} nap, min. {MIN_BARS})")

    enriched = indicators.enrich(df, settings.structure.atr_period)
    closed = sessions.completed_bars(enriched)
    ctx = market.load_context(settings, refresh=False)
    earnings = market.EarningsCalendar(settings, allow_network=refresh)
    earnings_date = earnings.next_date(symbol)
    calibration = Calibration.load(settings.calibration_path)
    universes = {sid: set(monitor.pullback_symbols(settings, allow_network=False, strategy=sid))
                 for sid in PULLBACK_IDS}

    views = {"closed": _view(symbol, closed, settings, calibration, ctx, earnings_date, universes, True)}
    if len(closed) < len(enriched):
        views["live"] = _view(symbol, enriched, settings, calibration, ctx, earnings_date, universes, False)

    index = ctx.index_of(symbol)
    notes = []
    if "live" in views:
        notes.append("A mai gyertya meg alakul: az 'Elo' nezet ugy szamol, mintha az ar most zarna. "
                     "A napi volumen reszleges, ezert a volumenszuro es -pont ott alulbecsult.")
    if refresh_error:
        notes.append(f"Az arfolyam frissitese nem sikerult, a tarolt adattal szamoltunk ({refresh_error}).")
    if not settings.entry.enabled or not settings.strategies.breakout.enabled:
        notes.append("A Kitores strategia ki van kapcsolva: a jelzeseket kiertekeljuk, de ertesites nem menne ki.")
    return {
        "symbol": symbol, "watched": symbol in watch,
        "index": market.INDEX_LABELS.get(index) if index else None,
        "earnings_date": earnings_date.isoformat() if earnings_date else None,
        "market_open": monitor.is_market_open(),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_score": settings.entry.min_score, "confirm_breakout": settings.entry.confirm_breakout,
        "setup_max_distance_atr": settings.entry.setup_max_distance_atr,
        "views": views, "notes": notes,
    }
