"""A korabbi ertesitesek utolagos ertekelese: mennyire volt jo a jelzes?

Minden ertesitesnel a jelzeskori arat a jelzes UTANI gyertyakkal vetjuk ossze.
Ketfele jelzes van, es mindkettot a sajat logikaja szerint ertekeljuk:

  belepesi jelzes (entry_breakout / entry_retest, mindig LONG)
      Ugyanaz a szimulacio, mint a backtestben es a virtualis kovetesben
      (trade_mgmt.PositionState): masnapi nyito, ha a limit alatt van; a fele
      +1R-nel, a stop a belepore, a maradek a celig vagy az idokorlatig.
      Eredmeny R-ben es dollarban (a jelzett darabszammal).

  alakzat-ertesites (new / triggered, es az elokeszulet - LONG vagy SHORT)
      A jelzeskori arrol indulva: mit ert el elobb az ar, a celt vagy az
      ervenytelenitesi szintet? (Ugyanazon a gyertyan mindketto = bukas,
      pesszimistan, mint a backtestben.) Mellette: valtozas a jelzes ota,
      iranyhelyesen (shortnal az eses a nyereseg), es a legjobb / legrosszabb
      kilenges (MFE / MAE).

Minosites:  jo (cel elerve / pozitiv lezart R)  rossz (stop / bukas)
            folyamatban (meg egyik sem)  nem ertekelheto (nincs adat, nem
            jelzes, nem lett belepes)

A kezelesi ertesitesek (pozicio-kovetes, sajat pozicio, paper trade) nem
jelzesek - azokat "egyeb" kategoriaba soroljuk, minosites nelkul.
"""
from __future__ import annotations

import pandas as pd

from .config import Settings
from .trade_mgmt import PositionState, max_hold

ENTRY_TRADES = ("entry_breakout", "entry_retest")
PATTERN_EVENTS = ("new", "triggered", "entry_setup")
OUTCOME_EVENTS = ("target_hit", "failed")


def category(record: dict) -> tuple[str, str]:
    """(kategoria, irany): kategoria = signal | outcome | other; irany = long | short | ''."""
    event = record.get("event", "")
    if event == "entry_revoked":
        return "other", ""            # egy korabbi jelzes visszavonasa, nem uj jelzes
    if event.startswith("entry_"):
        return "signal", "long"
    if event in PATTERN_EVENTS or event in OUTCOME_EVENTS:
        pattern = record.get("pattern") or {}
        direction = pattern.get("direction") or record.get("direction")
        side = "long" if direction == "bullish" else "short" if direction == "bearish" else ""
        return ("signal" if event in PATTERN_EVENTS else "outcome"), side
    return "other", ""


def _signal_date(record: dict) -> pd.Timestamp | None:
    entry = record.get("entry") or {}
    raw = entry.get("as_of") or record.get("as_of")
    if not raw:
        stamp = record.get("timestamp")
        if not stamp:
            return None
        return pd.Timestamp(stamp).tz_convert("America/New_York").normalize().tz_localize(None)
    return pd.Timestamp(str(raw)[:10])


STRENGTH_LABELS = {"strong": "Eros", "medium": "Kozepes", "weak": "Gyenge"}


def strength(record: dict, settings: Settings) -> dict:
    """Mennyire eros volt a jel a kikuldeskor - a rendszer SAJAT, mar meglevo mercevel:

      belepesi jelzes:  a 0-100-as belepesi pontszam (entry.score);
                        a `min_score` alatt gyenge, 70-tol eros
      alakzat:          az alakzat geometriai minosege (0-100%); a sav-hatarok a
                        backtest minosegi savjai (backtest.quality_buckets: 70% / 85%)

    A reszletek (iranyjelzes, tortenti talalati arany) a `strength_detail`-ben vannak.
    """
    event = record.get("event", "")
    entry = record.get("entry") or {}
    if event.startswith("entry_") and _num(entry.get("score")) is not None:
        value = _num(entry.get("score"))
        cut_low = settings.entry.min_score
        label = "strong" if value >= max(70.0, cut_low) else "medium" if value >= cut_low else "weak"
        detail = [f"Belepesi pontszam {value:.0f}/100 (riasztasi kuszob {cut_low:.0f})"]
        if entry.get("pattern_quality") is not None:
            detail.append(f"alakzat-minoseg {float(entry['pattern_quality']):.0%}")
        if entry.get("history"):
            detail.append(f"tortenti: {entry['history']}")
        return {"strength": value, "strength_label": label, "strength_kind": "entry_score",
                "strength_detail": " | ".join(detail)}

    pattern = record.get("pattern") or {}
    quality = _num(pattern.get("quality"))
    if (event in PATTERN_EVENTS or event in OUTCOME_EVENTS) and quality is not None:
        buckets = settings.backtest.quality_buckets
        mid, high = (buckets[1], buckets[2]) if len(buckets) >= 3 else (0.7, 0.85)
        label = "strong" if quality >= high else "medium" if quality >= mid else "weak"
        detail = [f"Alakzat-minoseg {quality:.0%} (savok: {mid:.0%} / {high:.0%})"]
        score = _num(record.get("score"))
        if score is not None:
            detail.append(f"iranyjelzes {record.get('label', '')} {score:+.2f}")
        if record.get("probability_text"):
            detail.append(f"tortenti talalat: {record['probability_text']}")
        return {"strength": quality * 100, "strength_label": label, "strength_kind": "pattern_quality",
                "strength_detail": " | ".join(detail)}
    return {"strength": None, "strength_label": "", "strength_kind": "", "strength_detail": ""}


def review(record: dict, bars: pd.DataFrame | None, settings: Settings) -> dict:
    """Egy ertesites ertekelese. `bars`: a papir napi gyertyai (indikatorokkal, ha van ATR)."""
    kind, side = category(record)
    out = {"category": kind, "side": side, "signal_price": _num(record.get("price")),
           "now_price": None, "now_date": None, "change_pct": None, "pnl_usd": None, "r": None,
           "mfe_pct": None, "mae_pct": None, "days": None, "grade": "na", "outcome": "",
           **strength(record, settings)}
    if kind != "signal":
        return out
    signal_date = _signal_date(record)
    if bars is None or bars.empty or signal_date is None:
        out["outcome"] = "nincs arfolyam-adat"
        return out

    last = bars.iloc[-1]
    out["now_price"], out["now_date"] = float(last["close"]), f"{bars.index[-1]:%Y-%m-%d}"
    after = bars[bars.index > signal_date]
    out["days"] = len(after)
    if record.get("event") == "entry_pullback":
        return _review_pullback(record, bars, signal_date, settings, out)
    if record.get("event") in ENTRY_TRADES:
        return _review_entry(record, after, settings, out)
    return _review_pattern(record, after, side, out)


def _review_entry(record: dict, after: pd.DataFrame, settings: Settings, out: dict) -> dict:
    entry = record.get("entry") or {}
    plan = entry.get("plan") or {}
    stop, target, limit = _num(plan.get("stop")), _num(plan.get("target")), _num(plan.get("entry_limit"))
    shares = int(plan.get("shares") or 0)
    if out["signal_price"] is None:
        out["signal_price"] = _num(entry.get("price"))
    if None in (stop, target, limit):
        out["outcome"] = "hianyos terv"
        return out
    if after.empty:
        out["grade"], out["outcome"] = "open", "a belepes napja meg nem jott el"
        return out

    first = after.iloc[0]
    o = float(first["open"])
    if o > limit or o <= stop:
        out["outcome"] = f"nem lett belepes (nyito {o:.2f}, limit {limit:.2f})"
        return out

    cfg = settings.entry
    atr = float(first["atr"]) if "atr" in after.columns and pd.notna(first.get("atr")) else (o - stop) / 2
    mode = plan.get("exit_mode") or cfg.exit_mode
    state = PositionState(entry=o, stop=stop, initial_stop=stop, target=target, atr=atr, mode=mode,
                          partial_r=cfg.partial_r, trail_atr=cfg.trail_atr)
    horizon = max_hold(int(plan.get("horizon") or 40), mode)
    for i, (_, bar) in enumerate(after.iterrows()):
        state.step(float(bar["open"]), float(bar["high"]), float(bar["low"]), first=(i == 0))
        if not state.closed and state.bars_held >= horizon:
            state.timeout(float(bar["close"]))
        if state.closed:
            break

    risk = o - stop
    if state.closed:
        r = state.realised_r
        reasons = {"target": "cel elerve", "stop": "stop", "breakeven": "fele +1R-nel, maradek nullszaldon",
                   "trail": "kovetett stop", "timeout": "idokorlat"}
        out["outcome"] = reasons.get(state.reason, state.reason)
        if state.partial_done and state.reason == "target":
            out["outcome"] = "fele +1R-nel, maradek a celnal"
        out["grade"] = "good" if r > 0 else "bad" if r < 0 else "flat"
    else:
        r = state.realised_r + state.remaining * (out["now_price"] - o) / risk
        out["outcome"] = "nyitott" + (" (fele mar +1R-nel eladva)" if state.partial_done else "")
        out["grade"] = "open"
    out["r"] = r
    out["change_pct"] = r * risk / o * 100
    out["pnl_usd"] = r * risk * shares if shares else None
    out["entry_fill"] = o
    _excursions(after, o, "long", out)
    return out


def _review_pullback(record: dict, bars: pd.DataFrame, signal_date: pd.Timestamp, settings: Settings,
                     out: dict) -> dict:
    """Visszahuzas: ugyanaz a szimulacio, mint a kutatasban es a virtualis kovetesben (meanrev.simulate_trade),
    a kikuldott limit- es stop-szintekkel."""
    from . import meanrev

    entry = record.get("entry") or {}
    plan = entry.get("plan") or {}
    limit, stop, shares = _num(plan.get("entry_limit")), _num(plan.get("stop")), int(plan.get("shares") or 0)
    out["signal_price"] = out["signal_price"] or _num(entry.get("price"))
    if limit is None or stop is None or signal_date not in bars.index:
        out["outcome"] = "hianyos terv vagy adat"
        return out
    strategy = record.get("strategy") or entry.get("strategy") or "pullback"
    params = settings.strategies.get(strategy).params if strategy in ("pullback", "deep", "etf", "sandbox") else None
    if params is None:
        return out
    feat = meanrev.features(bars, params)
    i = int(bars.index.get_loc(signal_date))
    t = meanrev.simulate_trade(record.get("symbol", ""), feat, meanrev.exit_flags(feat, params).to_numpy(), i,
                               params, levels=(limit, stop))
    if t.status == "pending":
        out["grade"], out["outcome"] = "open", "a veteli limit holnapra szol"
        return out
    if t.status == "unfilled":
        out["outcome"] = f"nem lett belepes (a limit {limit:.2f} nem teljesult)"
        return out
    risk = t.entry_price - stop
    after = bars[bars.index >= t.entry_date]
    if t.status == "closed":
        r = (t.exit_price - t.entry_price) / risk if risk > 0 else None
        out["change_pct"] = t.net_pct
        reasons = {"signal": "kilepes a jelre (ConnorsRSI)", "time": "idokorlat", "stop": "stop", "gap_stop": "stop (gap)"}
        out["outcome"] = f"{reasons.get(t.reason, t.reason)}, {t.held} nap"
        out["grade"] = "good" if t.net_pct > 0 else "bad"
        out["pnl_usd"] = (t.exit_price - t.entry_price) * shares if shares else None
        after = after[after.index <= t.exit_date]
    else:
        r = (out["now_price"] - t.entry_price) / risk if risk > 0 else None
        out["change_pct"] = (out["now_price"] / t.entry_price - 1) * 100
        out["outcome"] = "nyitott" + (" - holnap nyitaskor kilep" if t.reason else "")
        out["grade"] = "open"
        out["pnl_usd"] = (out["now_price"] - t.entry_price) * shares if shares else None
    out["r"], out["entry_fill"] = r, t.entry_price
    _excursions(after, t.entry_price, "long", out)
    return out


def _review_pattern(record: dict, after: pd.DataFrame, side: str, out: dict) -> dict:
    price = out["signal_price"]
    pattern = record.get("pattern") or {}
    entry = record.get("entry") or {}
    plan = entry.get("plan") or {}
    target = _num(plan.get("target")) or _num(pattern.get("target")) or _num(record.get("target"))
    invalid = (_num(plan.get("stop")) or _num(pattern.get("invalidation"))
               or _num(record.get("invalidation")))
    if price is None or side == "":
        out["outcome"] = "nincs irany vagy ar"
        return out
    sign = 1 if side == "long" else -1
    out["change_pct"] = sign * (out["now_price"] / price - 1) * 100
    if invalid is not None and abs(price - invalid) > 0:
        out["r"] = sign * (out["now_price"] - price) / abs(price - invalid)
    _excursions(after, price, side, out)

    if invalid is not None and (price <= invalid if side == "long" else price >= invalid):
        out["grade"] = "bad"
        out["outcome"] = f"mar a jelzeskor az ervenytelenitesi szint ({invalid:.2f}) rossz oldalan volt"
        return out

    out["grade"], out["outcome"] = "open", "folyamatban - se cel, se bukas"
    for date, bar in after.iterrows():
        high, low = float(bar["high"]), float(bar["low"])
        failed = invalid is not None and (low <= invalid if side == "long" else high >= invalid)
        hit = target is not None and (high >= target if side == "long" else low <= target)
        if failed:                                   # ugyanazon a gyertyan: pesszimistan bukas
            out["grade"], out["outcome"] = "bad", f"ervenytelenitve {date:%Y-%m-%d} ({invalid:.2f})"
            break
        if hit:
            out["grade"], out["outcome"] = "good", f"cel elerve {date:%Y-%m-%d} ({target:.2f})"
            break
    if after.empty:
        out["outcome"] = "meg nincs uj gyertya a jelzes ota"
    return out


def _excursions(after: pd.DataFrame, price: float, side: str, out: dict) -> None:
    if after.empty or not price:
        return
    hi, lo = float(after["high"].max()), float(after["low"].min())
    if side == "long":
        out["mfe_pct"], out["mae_pct"] = (hi / price - 1) * 100, (lo / price - 1) * 100
    else:
        out["mfe_pct"], out["mae_pct"] = (1 - lo / price) * 100, (1 - hi / price) * 100


def summary(rows: list[dict]) -> dict:
    """Osszesites a jelzesekre: talalati arany a lezart (jo/rossz) jelzeseken."""
    signals = [r for r in rows if r["category"] == "signal"]
    decided = [r for r in signals if r["grade"] in ("good", "bad")]
    good = sum(r["grade"] == "good" for r in decided)
    changes = [r["change_pct"] for r in signals if r["change_pct"] is not None]
    by_strength = {}
    for label in STRENGTH_LABELS:
        tier = [r for r in decided if r.get("strength_label") == label]
        wins = sum(r["grade"] == "good" for r in tier)
        by_strength[label] = {"decided": len(tier), "good": wins,
                              "hit_rate": wins / len(tier) if tier else None}
    return {
        "signals": len(signals),
        "decided": len(decided),
        "good": good,
        "hit_rate": good / len(decided) if decided else None,
        "open": sum(r["grade"] == "open" for r in signals),
        "avg_change_pct": sum(changes) / len(changes) if changes else None,
        "by_strength": by_strength,
    }


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None
