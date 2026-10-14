"""A Visszahuzas (RSI2) strategia validalasa multbeli adaton - becsuletesen.

  1. Papironkent minden jelzesnap kotese lejatszva (meanrev.simulate_trade - ugyanaz, mint elesben).
  2. Portfolio-szimulacio datum szerint: a ZARON rangsorol (legalacsonyabb RSI(2), aztan nagyobb
     NATR), naponta legfeljebb `max_signals_per_day` megbizas, legfeljebb `max_open` nyitott /
     fuggo kotes, papironkent egy. Utolag NEM valogat a teljesultek kozul (az jovobe latas volna).
  3. Statisztika idoszakonkent: TANITO (2022 elott), VALIDACIO (2022 -> 2025-04), es a zarolt
     HOLDOUT csak `--holdout`-tal (minden megnyitas naplozodik).

FIGYELEM: csak a MOSTANI indextagokrol van adat - a kiesett papirok hianyoznak, ez felfele
torzit. A `--survivorship-check` a config/universe.yaml listan (benne csodbe ment papirokkal)
is lefuttatja, hogy lassuk a kulonbseget.

Kimenet: data/research/meanrev/ (report.md, summary.json, trades.csv.gz) - 20 MB-os plafonnal.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import market, meanrev, store
from .config import DATA_DIR, PullbackParams, Settings, load_settings
from .entry_research import HOLDOUT_START, SPLIT_DATE, log_holdout_opening, wilson

log = logging.getLogger(__name__)

OUT_DIR = DATA_DIR / "research" / "meanrev"
ETF_DIR = OUT_DIR / "etf"                     # az ETF visszahuzas validalasa
SANDBOX_DIR = OUT_DIR / "sandbox"             # a homokozo backtestje (a labor-szolgaltatas futtatja)
SANDBOX_REQUEST = SANDBOX_DIR / "request.json"
SANDBOX_STATUS = SANDBOX_DIR / "status.json"
LIMIT_MB = 20

# Elfogadasi feltetel (elore rogzitve - nem lazitjuk utolag)
GATE = {"win": 0.70, "per_week_min": 0.7, "per_week_max": 3.0, "pf": 1.3, "year_win": 0.60, "years_ok": 7,
        "win_2x_cost": 0.68}


# ---------------------------------------------------------------------------
# Adat
# ---------------------------------------------------------------------------
@dataclass
class SymbolData:
    feat: pd.DataFrame
    regime: pd.Series
    earnings: list = field(default_factory=list)


def load_universe(symbols: list[str], settings: Settings, params: PullbackParams,
                  ctx: market.MarketContext, earnings: market.HistoricalEarnings | None = None,
                  progress=None) -> dict[str, SymbolData]:
    out = {}
    for n, symbol in enumerate(symbols, 1):
        if progress and n % 50 == 1:
            progress(f"Adat: {n}/{len(symbols)} ({symbol})")
        df = store.load(symbol, settings, universe=True)
        if df.empty:
            df = store.load(symbol, settings)
        if len(df) < meanrev.WARMUP + 20:
            continue
        feat = meanrev.features(df, params)
        regime = meanrev.regime_series(feat, ctx.regime_frame(symbol, df))
        out[symbol] = SymbolData(feat, regime, earnings.dates(symbol) if earnings is not None else [])
    return out


# ---------------------------------------------------------------------------
# Jeloltek es portfolio
# ---------------------------------------------------------------------------
def candidates(data: dict[str, SymbolData], params: PullbackParams) -> list[meanrev.Trade]:
    """Minden jelzesnap kotese (meg portfolio-korlat nelkul)."""
    out = []
    for symbol, d in data.items():
        mask = meanrev.setup_mask(d.feat, params, d.regime).to_numpy().copy()
        mask[: meanrev.WARMUP] = False
        if not mask.any():
            continue
        exits = meanrev.exit_flags(d.feat, params).to_numpy()
        for i in np.flatnonzero(mask):
            day = d.feat.index[i].date()
            if meanrev.earnings_blocked(day, d.earnings, params):
                continue
            out.append(meanrev.simulate_trade(symbol, d.feat, exits, int(i), params))
    return out


def portfolio(cands: list[meanrev.Trade], params: PullbackParams) -> tuple[list[meanrev.Trade], dict]:
    """A napi rangsor es a slot-korlat alkalmazasa. Visszaadja a kivalasztott megbizasokat."""
    by_day: dict[pd.Timestamp, list[meanrev.Trade]] = {}
    for t in cands:
        by_day.setdefault(t.signal_date, []).append(t)
    busy: list[tuple[pd.Timestamp, str]] = []          # (felszabadul ekkor, papir)
    chosen, capped_days, days_with_cands = [], 0, 0
    for day in sorted(by_day):
        busy = [(free, sym) for free, sym in busy if free > day]
        held = {sym for _, sym in busy}
        room = min(params.daily_cap, params.open_cap - len(busy))
        ranked = sorted((t for t in by_day[day] if t.symbol not in held),
                        key=lambda t: (t.rsi2, -t.natr, t.symbol))
        days_with_cands += 1
        if len(ranked) > room:
            capped_days += 1
        for t in ranked[:max(room, 0)]:
            chosen.append(t)
            if t.status in ("closed",):
                free = t.exit_date                        # nyitaskor zart -> aznap zaron szabad
            elif t.status == "unfilled":
                free = day + pd.offsets.BDay(1)           # a limit csak masnap el
            else:
                free = pd.Timestamp.max                   # meg nyitott / fuggo
            busy.append((free, t.symbol))
    return chosen, {"days_with_candidates": days_with_cands, "capped_days": capped_days}


# ---------------------------------------------------------------------------
# Statisztika
# ---------------------------------------------------------------------------
def block_bootstrap_ci(trades: list[meanrev.Trade], n_boot: int = 2000, seed: int = 7) -> tuple:
    """Az atlagos netto % 95%-os intervalluma HAVI blokkokkal (a kotesek valsagban torlodnak)."""
    if len(trades) < 2:
        return None, None
    months: dict[str, list[float]] = {}
    for t in trades:
        months.setdefault(f"{t.exit_date:%Y-%m}", []).append(t.net_pct)
    blocks = [np.array(v) for v in months.values()]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(blocks), size=len(blocks))
        sample = np.concatenate([blocks[i] for i in pick])
        means.append(sample.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def stats(trades: list[meanrev.Trade], orders: list[meanrev.Trade], start, end, params: PullbackParams) -> dict:
    closed = sorted((t for t in trades if t.status == "closed"), key=lambda t: t.exit_date)
    weeks = max(len(pd.bdate_range(start, end)) / 5, 1e-9) if start is not None and end is not None else None
    fills = [t for t in orders if t.status in ("open", "closed")]
    out = {"orders": len(orders), "fills": len(fills), "n": len(closed),
           "orders_per_week": len(orders) / weeks if weeks else None,
           "fills_per_week": len(fills) / weeks if weeks else None,
           "fill_rate": len(fills) / len(orders) if orders else None}
    if not closed:
        return out | {"win": None}
    net = np.array([t.net_pct for t in closed])
    wins, losses = net[net > 0], net[net <= 0]
    lo, hi = wilson(int((net > 0).sum()), len(net))
    ev_lo, ev_hi = block_bootstrap_ci(closed)
    weight = 1 / (params.max_open or 10)                       # fix sulyu pozicio (korlat nelkul: 10%)
    equity = np.cumprod(1 + net / 100 * weight)
    peak = np.maximum.accumulate(np.r_[1.0, equity])
    dd = float(((peak - np.r_[1.0, equity]) / peak).max() * 100)
    reasons = pd.Series([t.reason for t in closed]).value_counts().to_dict()
    return out | {
        "win": float((net > 0).mean()), "win_lo": lo, "win_hi": hi,
        "ev": float(net.mean()), "ev_lo": ev_lo, "ev_hi": ev_hi,
        "avg_win": float(wins.mean()) if wins.size else None, "avg_loss": float(losses.mean()) if losses.size else None,
        "pf": float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else math.inf,
        "held": float(np.mean([t.held for t in closed])), "max_dd_pct": dd,
        "total_pct": float((equity[-1] - 1) * 100), "reasons": reasons,
        "stop_share": (reasons.get("stop", 0) + reasons.get("gap_stop", 0)) / len(closed),
    }


def split(orders: list[meanrev.Trade], params: PullbackParams, first, last, holdout: bool) -> dict:
    def period(lo, hi):
        sel = [t for t in orders if (lo is None or t.signal_date >= lo) and (hi is None or t.signal_date < hi)]
        start = lo if lo is not None else first
        end = (hi - pd.Timedelta(days=1)) if hi is not None else last
        return stats(sel, sel, start, end, params)

    out = {"train": period(None, SPLIT_DATE), "test": period(SPLIT_DATE, HOLDOUT_START),
           "visible": period(None, HOLDOUT_START)}
    if holdout:
        out["holdout"] = period(HOLDOUT_START, None)
    years = {}
    for t in orders:
        if t.status != "closed" or (not holdout and t.signal_date >= HOLDOUT_START):
            continue
        years.setdefault(t.signal_date.year, []).append(t.net_pct)
    out["years"] = {y: {"n": len(v), "win": float(np.mean(np.array(v) > 0)), "ev": float(np.mean(v))}
                    for y, v in sorted(years.items())}
    return out


def gate(res: dict, res_2x: dict | None) -> dict:
    """Az elore rogzitett elfogadasi feltetel - tanito ES validacios idoszakban kulon-kulon."""
    checks = []
    for name in ("train", "test"):
        s = res[name]
        checks.append((f"{name}: nyeresi arany >= {GATE['win']:.0%}", (s.get("win") or 0) >= GATE["win"]))
        checks.append((f"{name}: heti {GATE['per_week_min']}-{GATE['per_week_max']} kotes",
                       s.get("fills_per_week") is not None
                       and GATE["per_week_min"] <= s["fills_per_week"] <= GATE["per_week_max"]))
        checks.append((f"{name}: varhato ertek > 0 (bootstrap also hatar is)", (s.get("ev_lo") or -1) > 0))
        checks.append((f"{name}: profit-faktor >= {GATE['pf']}", (s.get("pf") or 0) >= GATE["pf"]))
    good_years = sum(1 for v in res["years"].values() if v["win"] >= GATE["year_win"])
    checks.append((f"legalabb {GATE['years_ok']} evben >= {GATE['year_win']:.0%}", good_years >= GATE["years_ok"]))
    if res_2x is not None:
        checks.append((f"2x koltseggel is >= {GATE['win_2x_cost']:.0%}",
                       (res_2x["visible"].get("win") or 0) >= GATE["win_2x_cost"]))
    return {"passed": all(ok for _, ok in checks), "checks": checks, "good_years": good_years}


# ---------------------------------------------------------------------------
# Futtatas
# ---------------------------------------------------------------------------
def neighbourhood(base: PullbackParams) -> list[tuple[str, PullbackParams]]:
    """Kis, elore megadott szomszedsag - NEM parametervalasztasra, hanem stabilitasvizsgalatra."""
    out = []
    if base.limit_pct < 3:                     # ETF: kisebb mozgas, sekelyebb limit, SMA-kilepes
        for rsi in (5.0, 10.0, 15.0):
            for lim in (0.5, 1.0, 1.5):
                for sma in (0, 5):
                    for crsi in (50.0, 70.0):
                        p = base.model_copy(update={"rsi2_max": rsi, "limit_pct": lim, "exit_sma": sma,
                                                    "exit_crsi": crsi})
                        out.append((f"RSI2<{rsi:g} limit {lim:g}% SMA{sma or '-'} CRSI>{crsi:g}", p))
        out.append(("napi / nyitott korlat nelkul (referencia)",
                    base.model_copy(update={"max_signals_per_day": 0, "max_open": 0})))
        out.append(("stop nelkul (csak referencia: 10 ATR)", base.model_copy(update={"stop_atr": 10.0})))
        return out
    for rsi in (2.0, 3.0):
        for lim in (4.0, 5.0, 6.0):
            for stop in (2.5, 3.0):
                for crsi in (40.0, 50.0, 60.0):
                    p = base.model_copy(update={"rsi2_max": rsi, "limit_pct": lim, "stop_atr": stop, "exit_crsi": crsi})
                    out.append((f"RSI2<{rsi:g} limit {lim:g}% stop {stop:g}ATR CRSI>{crsi:g}", p))
    out.append(("rezsim-szuro nelkul (referencia)", base.model_copy(update={"regime_filter": False})))
    out.append(("stop nelkul (csak referencia: 50 ATR)", base.model_copy(update={"stop_atr": 50.0})))
    return out


def evaluate_variant(data: dict[str, SymbolData], params: PullbackParams, holdout: bool) -> dict:
    cands = candidates(data, params)
    orders, meta = portfolio(cands, params)
    first = min(d.feat.index[meanrev.WARMUP] for d in data.values())
    last = max(d.feat.index[-1] for d in data.values())
    return {"split": split(orders, params, first, last, holdout), "meta": meta, "orders": orders,
            "candidates": len(cands)}


def run(symbols: list[str], settings: Settings | None = None, params: PullbackParams | None = None,
        holdout: bool = False, neighbourhood_check: bool = False, survivorship_symbols: list[str] | None = None,
        progress=print, out_dir=None, study: str = "meanrev") -> dict:
    settings = settings or load_settings()
    params = params or settings.strategies.pullback.params
    ctx = market.load_context(settings, refresh=True)
    earnings = market.HistoricalEarnings(settings) if params.earnings_blackout_days > 0 else None
    data = load_universe(symbols, settings, params, ctx, earnings, progress)
    if earnings is not None:
        earnings.save()
    if not data:
        raise ValueError("Nincs adat - futtasd: python cli.py fetch-universe")
    progress(f"Szimulacio: {len(data)} papir...")
    main = evaluate_variant(data, params, holdout)
    costly = evaluate_variant(data, params.model_copy(update={"cost_bps": params.cost_bps * 2}), False)
    report = {"params": params.model_dump(), "symbols": len(data), "holdout": holdout,
              "main": main["split"], "meta": main["meta"], "candidates": main["candidates"],
              "cost_2x": costly["split"]["visible"], "gate": gate(main["split"], costly["split"]),
              "earnings_known": sum(1 for d in data.values() if d.earnings) if earnings is not None else None}
    if holdout:
        log_holdout_opening([f"{study}: " + json.dumps(params.model_dump())], len(data), study=study)
    if neighbourhood_check:
        rows = []
        for name, p in neighbourhood(params):
            progress(f"Szomszed: {name}")
            r = evaluate_variant(data, p, False)["split"]
            rows.append({"name": name, "train": r["train"], "test": r["test"]})
        report["neighbourhood"] = rows
    if survivorship_symbols:
        progress(f"Survivorship-ellenorzes: {len(survivorship_symbols)} papir")
        other = load_universe(survivorship_symbols, settings, params, ctx, earnings, progress)
        if other:
            report["survivorship"] = {"symbols": len(other),
                                      "visible": evaluate_variant(other, params, False)["split"]["visible"]}
    save(report, main["orders"], out_dir or OUT_DIR)
    return report


# ---------------------------------------------------------------------------
# Kimenet (lemezplafonnal)
# ---------------------------------------------------------------------------
def _fmt(v, kind: str = "pct") -> str:
    if v is None:
        return "-"
    if kind == "rate":
        return f"{v * 100:.1f}%"
    if kind == "num":
        return f"{v:.2f}"
    return f"{v:+.2f}%"


def report_markdown(r: dict) -> str:
    p = r["params"]
    sma_exit = f"vagy zaro > SMA{p['exit_sma']}" if p.get("exit_sma") else ""
    lines = [
        "# Visszahuzas (RSI2) - validalas",
        "",
        f"Szabalyok: RSI(2) < {p['rsi2_max']:g}, zaro > SMA{p['trend_sma']}, rezsim: {'igen' if p['regime_filter'] else 'nem'}, "
        f"limit -{p['limit_pct']:g}%, stop {p['stop_atr']:g} ATR({p['atr_period']}), kilepes CRSI > {p['exit_crsi']:g} "
        f"{sma_exit} "
        f"(masnap nyitaskor), idokorlat {p['time_stop']} nap, napi max {p['max_signals_per_day'] or 'korlat nelkul'}, "
        f"max nyitott {p['max_open'] or 'korlat nelkul'}, koltseg {p['cost_bps']:g} bps / oldal.",
        f"Papirok: {r['symbols']} (csak a MOSTANI indextagok - felfele torzit).",
        "",
        "| Idoszak | Kotes | Heti | Nyeresi arany (95%) | Atlag netto | Atlag nyero / vesztes | PF | Max DD | Stop-arany |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, label in (("train", "Tanito (<2022)"), ("test", "Validacio (2022-2025.03)"),
                        ("visible", "Lathato osszesen"), ("holdout", "HOLDOUT (2025.04-)")):
        s = r["main"].get(name)
        if not s:
            continue
        lines.append(f"| {label} | {s['n']} | {_fmt(s.get('fills_per_week'), 'num')} | {_fmt(s.get('win'), 'rate')} "
                     f"({_fmt(s.get('win_lo'), 'rate')}-{_fmt(s.get('win_hi'), 'rate')}) | {_fmt(s.get('ev'))} "
                     f"| {_fmt(s.get('avg_win'))} / {_fmt(s.get('avg_loss'))} | {_fmt(s.get('pf'), 'num')} "
                     f"| {_fmt(s.get('max_dd_pct'), 'num')}% | {_fmt(s.get('stop_share'), 'rate')} |")
    lines += ["", "## Evenkent", "", "| Ev | Kotes | Nyeresi arany | Atlag netto |", "|---|---|---|---|"]
    for y, v in r["main"]["years"].items():
        lines.append(f"| {y} | {v['n']} | {_fmt(v['win'], 'rate')} | {_fmt(v['ev'])} |")
    lines += ["", "## Elfogadasi feltetel", ""]
    for name, ok in r["gate"]["checks"]:
        lines.append(f"- [{'x' if ok else ' '}] {name}")
    lines.append(f"\n**Eredmeny: {'ATMENT' if r['gate']['passed'] else 'NEM MENT AT'}**")
    lines.append(f"\n2x koltseggel (lathato): nyeresi arany {_fmt(r['cost_2x'].get('win'), 'rate')}, "
                 f"atlag {_fmt(r['cost_2x'].get('ev'))}")
    if r.get("survivorship"):
        s = r["survivorship"]["visible"]
        lines.append(f"\nSurvivorship-ellenorzes ({r['survivorship']['symbols']} papir, benne kiesett / csodbe "
                     f"ment papirok): {s['n']} kotes, nyeresi arany {_fmt(s.get('win'), 'rate')}, atlag {_fmt(s.get('ev'))}")
    if r.get("neighbourhood"):
        lines += ["", "## Stabilitas (szomszedos beallitasok)", "",
                  "| Valtozat | Tanito: kotes / nyeresi / atlag | Validacio: kotes / nyeresi / atlag |", "|---|---|---|"]
        for row in r["neighbourhood"]:
            tr, te = row["train"], row["test"]
            lines.append(f"| {row['name']} | {tr['n']} / {_fmt(tr.get('win'), 'rate')} / {_fmt(tr.get('ev'))} "
                         f"| {te['n']} / {_fmt(te.get('win'), 'rate')} / {_fmt(te.get('ev'))} |")
    return "\n".join(lines) + "\n"


def _default(o):
    if isinstance(o, (pd.Timestamp,)):
        return f"{o:%Y-%m-%d}"
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


def save(report: dict, orders: list[meanrev.Trade], out_dir=None) -> None:
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = json.loads(json.dumps(report, default=_default).replace("Infinity", "null"))
    clean["computed_at"] = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    (out_dir / "summary.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(report_markdown(clean), encoding="utf-8")
    frame = pd.DataFrame([t.to_dict() for t in orders])
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    (out_dir / "trades.csv.gz").write_bytes(gzip.compress(buffer.getvalue().encode()))
    prune()


def load_summary(out_dir=None) -> dict | None:
    path = (out_dir or OUT_DIR) / "summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Homokozo: a felulet kerest ir, a labor-szolgaltatas futtatja (nem a webes folyamat)
# ---------------------------------------------------------------------------
def request_sandbox(params: PullbackParams) -> dict:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    req = {"params": params.model_dump(), "requested_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
    SANDBOX_REQUEST.write_text(json.dumps(req), encoding="utf-8")
    _set_sandbox_status("queued", "sorban all - a labor-szolgaltatas hamarosan elkezdi")
    return sandbox_state()


def _set_sandbox_status(state: str, message: str) -> None:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    SANDBOX_STATUS.write_text(json.dumps({"state": state, "message": message,
                                          "at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}),
                              encoding="utf-8")


def sandbox_state() -> dict:
    try:
        status = json.loads(SANDBOX_STATUS.read_text(encoding="utf-8")) if SANDBOX_STATUS.exists() else None
    except (OSError, json.JSONDecodeError):
        status = None
    return {"status": status, "result": load_summary(SANDBOX_DIR)}


def run_sandbox_request(settings: Settings) -> bool:
    """A labor-szolgaltatas hivja: ha van fuggo homokozo-backtest, lefuttatja. Igaz, ha futott."""
    from . import indexes

    if not SANDBOX_REQUEST.exists():
        return False
    try:
        req = json.loads(SANDBOX_REQUEST.read_text(encoding="utf-8"))
        SANDBOX_REQUEST.unlink(missing_ok=True)
        params = PullbackParams(**req["params"])
        members = indexes.IndexMembers(settings)
        symbols = list(dict.fromkeys(members.members("nasdaq100", allow_network=False)
                                     + members.members("sp500", allow_network=False)))
        _set_sandbox_status("running", f"szamol: {len(symbols)} papir")
        run(symbols, settings, params, progress=lambda msg: _set_sandbox_status("running", msg), out_dir=SANDBOX_DIR)
        _set_sandbox_status("done", "kesz")
    except Exception as exc:
        log.exception("A homokozo backtestje hibara futott")
        _set_sandbox_status("error", str(exc)[:200])
    return True


def usage_bytes() -> int:
    return sum(p.stat().st_size for p in OUT_DIR.rglob("*") if p.is_file()) if OUT_DIR.exists() else 0


def prune() -> None:
    """Lemezplafon: a legregebbi fajlok torlodnek, ha a mappa LIMIT_MB folott van."""
    if not OUT_DIR.exists():
        return
    keep = {SANDBOX_REQUEST.name, SANDBOX_STATUS.name}          # apro vezerlofajlok
    files = sorted((p for p in OUT_DIR.rglob("*") if p.is_file() and p.name not in keep),
                   key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > LIMIT_MB * 1024 * 1024:
        victim = files.pop(0)
        total -= victim.stat().st_size
        victim.unlink(missing_ok=True)
