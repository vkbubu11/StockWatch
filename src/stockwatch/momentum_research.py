"""A Momentum (12-1) strategia validalasa multbeli adaton - becsuletesen.

  1. Portfolio-szimulacio napi gyertyakon: a honap utolso napjanak ZAROJAN rangsorol (momentum.decide),
     a kotes a KOVETKEZO NYITASON megy (koltseggel). Uj papir: a pillanatnyi toke / top_n; a tartott
     papirok sulya sodrodik (nem igazitjuk vissza - forgas-takarekos, mint elesben).
  2. Osszehasonlitas UGYANANNAK az univerzumnak az egyenlo sulyu tartasaval (havi rebalansz): a tulelesi
     torzitas mindkettot emeli, a kulonbseg a valogatas erteke.
  3. Idoszakok: TANITO (2022 elott), VALIDACIO (2022 -> 2025-03), a zarolt HOLDOUT csak `--holdout`-tal
     (minden megnyitas naplozodik).

FIGYELEM: csak a MOSTANI indextagokrol van adat - felfele torzit. A `--survivorship-check` a
config/universe.yaml vegyes listajan (kiesett / csodbe ment papirokkal) is lefuttatja.

Kimenet: data/research/momentum/ (report.md, summary.json, trades.csv.gz) - 20 MB-os plafonnal.
"""
from __future__ import annotations

import gzip
import io
import json
import math

import numpy as np
import pandas as pd

from . import momentum, store
from .config import DATA_DIR, MomentumParams, Settings, load_settings
from .entry_research import HOLDOUT_START, SPLIT_DATE, log_holdout_opening, wilson

OUT_DIR = DATA_DIR / "research" / "momentum"
LIMIT_MB = 20
START = pd.Timestamp("2017-01-01")

# Elfogadasi feltetel (a 2026-10-i felterkepezes UTAN, de a vegleges futas ELOTT rogzitve)
GATE = {"excess": 0.0, "sharpe": 0.8, "excess_2x_cost": 0.0, "survivorship_excess": 0.0}


# ---------------------------------------------------------------------------
# Adat
# ---------------------------------------------------------------------------
def load_panel(symbols: list[str], settings: Settings, end: pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    """open / low / close / volume tablak (oszlop = papir). A tarolt adatbol, halozat nelkul."""
    frames = {}
    for s in symbols:
        df = store.load(s, settings, universe=True)
        if df.empty:
            df = store.load(s, settings)
        if df.empty:
            continue
        if end is not None:
            df = df[df.index <= end]
        frames[s] = df
    if not frames:
        return {}
    return {col: pd.DataFrame({s: f[col].astype(float) for s, f in frames.items()}).sort_index()
            for col in ("open", "low", "close", "volume")}


# ---------------------------------------------------------------------------
# Szimulacio
# ---------------------------------------------------------------------------
def simulate(panel: dict[str, pd.DataFrame], params: MomentumParams) -> dict:
    """Napi hozamsor (`returns`), kotesek (`trades`) es az egyenlo sulyu alap (`baseline`)."""
    close, open_, low = panel["close"], panel["open"], panel["low"]
    idx, names = close.index, list(close.columns)
    scores = momentum.score_frame(close, panel["volume"], params).to_numpy()
    o, lo, c = open_.to_numpy(), low.to_numpy(), close.to_numpy()
    reb = set(momentum.rebalance_days(idx))
    cost = params.total_cost_bps / 1e4

    held: dict[int, dict] = {}
    to_buy: list[int] = []
    to_sell: set[int] = set()
    rets, trades = [], []
    base_w: dict[int, float] = {}
    base_rets = []

    def book(j: int, p: dict, k: int, price: float, reason: str) -> None:
        gross, net = momentum.net_return(p["entry"], price, params.total_cost_bps)
        trades.append({"symbol": names[j], "signal_date": idx[p["signal"]], "entry_date": idx[p["k"]],
                       "entry_price": p["entry"], "exit_date": idx[k], "exit_price": price, "reason": reason,
                       "held": k - p["k"], "gross_pct": gross, "net_pct": net})

    for k in range(1, len(idx)):
        r = 0.0
        for j in sorted(to_sell):                                  # nyitaskor: eladasok
            p = held.pop(j)
            px = o[k, j] if not np.isnan(o[k, j]) else c[k - 1, j]
            r += p["w"] * (px / c[k - 1, j] - 1) - p["w"] * cost
            book(j, p, k, px, "rotation")
        to_sell = set()
        for j, p in list(held.items()):                            # tartott papirok (stop?)
            stop = p["stop"]
            if stop is not None and o[k, j] <= stop:
                px, why = o[k, j], "gap_stop"
            elif stop is not None and lo[k, j] <= stop:
                px, why = stop, "stop"
            else:
                px = None
            if px is not None:
                r += p["w"] * (px / c[k - 1, j] - 1) - p["w"] * cost
                book(j, held.pop(j), k, px, why)
            elif not np.isnan(c[k, j]):
                r += p["w"] * (c[k, j] / c[k - 1, j] - 1)
        # uj papir: toke / top_n, de legfeljebb a szabad keszpenz (tokeattetel nincs - mint a paper fiokban)
        cash = max(0.0, 1.0 - sum(p["w"] for p in held.values()))
        weight = min(1.0 / params.top_n, cash / len(to_buy)) if to_buy else 0.0
        for j in to_buy:                                           # nyitaskor: vetelek
            if np.isnan(o[k, j]) or o[k, j] <= 0:
                continue
            r += weight * (c[k, j] / o[k, j] - 1) - weight * cost
            stop = o[k, j] * (1 - params.stop_pct / 100) if params.stop_pct > 0 else None
            held[j] = {"entry": o[k, j], "stop": stop, "w": weight, "k": k, "signal": k - 1}
            if stop is not None and lo[k, j] <= stop:              # a belepes napjan is
                r += weight * (stop / c[k, j] - 1)
                book(j, held.pop(j), k, stop, "stop")
        to_buy = []
        rets.append(r)
        for j, p in held.items():                                  # sulyok sodrodasa
            if not np.isnan(c[k, j]) and c[k - 1, j] > 0:
                p["w"] *= (c[k, j] / (o[k, j] if p["k"] == k else c[k - 1, j])) / (1 + r)
        # egyenlo sulyu alap (a jogosult papirok, havi rebalansz a kovetkezo nyitason - itt a zaron kozelitve)
        br = sum(w * (c[k, j] / c[k - 1, j] - 1) for j, w in base_w.items() if not np.isnan(c[k, j]))
        base_rets.append(br)
        if base_w:
            tot = 1 + br
            base_w = {j: w * (c[k, j] / c[k - 1, j]) / tot for j, w in base_w.items() if not np.isnan(c[k, j])}
        if idx[k] in reb:
            row = scores[k]
            ranked = momentum.ranking({names[j]: row[j] for j in range(len(names)) if not np.isnan(row[j])})
            pos = {s: i for i, s in enumerate(names)}
            d = momentum.decide(ranked, [names[j] for j in held], params)
            to_sell = {pos[s] for s in d.sell}
            to_buy = [pos[s] for s in d.buy]
            live = [pos[s] for s in ranked]
            base_w = {j: 1 / len(live) for j in live} if live else {}
    open_trades = [{"symbol": names[j], "entry_date": idx[p["k"]], "entry_price": p["entry"]} for j, p in held.items()]
    return {"returns": pd.Series(rets, index=idx[1:]), "baseline": pd.Series(base_rets, index=idx[1:]),
            "trades": trades, "open": open_trades}


# ---------------------------------------------------------------------------
# Statisztika
# ---------------------------------------------------------------------------
def period_stats(r: pd.Series, base: pd.Series, trades: list[dict], a: pd.Timestamp, b: pd.Timestamp) -> dict:
    mask = (r.index >= a) & (r.index < b)
    r, base = r[mask], base[mask]
    if len(r) < 20:
        return {"days": int(len(r))}
    eq = (1 + r).cumprod()
    years = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / years) - 1
    dd = float((eq / eq.cummax() - 1).min())
    vol = r.std() * math.sqrt(252)
    monthly = (1 + r).resample("ME").prod() - 1
    beq = (1 + base).cumprod()
    bcagr = beq.iloc[-1] ** (1 / years) - 1
    closed = [t for t in trades if a <= pd.Timestamp(t["exit_date"]) < b]
    wins = sum(1 for t in closed if t["net_pct"] > 0)
    lo, hi = wilson(wins, len(closed))
    yearly = {str(y): round(float((1 + g).prod() - 1) * 100, 1) for y, g in r.groupby(r.index.year)}
    return {
        "days": int(len(r)), "cagr": cagr * 100, "vol": vol * 100,
        "sharpe": float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else None,
        "max_dd": dd * 100, "calmar": (cagr / -dd) if dd < 0 else None,
        "month_win": float((monthly > 0).mean()), "baseline_cagr": bcagr * 100,
        "excess": float((r - base).mean() * 252 * 100),
        "trades": len(closed), "trade_win": wins / len(closed) if closed else None, "trade_win_lo": lo,
        "trade_win_hi": hi, "trade_avg": float(np.mean([t["net_pct"] for t in closed])) if closed else None,
        "trades_per_year": len(closed) / years, "yearly": yearly,
    }


def split(result: dict, holdout: bool, end: pd.Timestamp) -> dict:
    r, base, trades = result["returns"], result["baseline"], result["trades"]
    out = {"train": period_stats(r, base, trades, START, SPLIT_DATE),
           "test": period_stats(r, base, trades, SPLIT_DATE, HOLDOUT_START),
           "visible": period_stats(r, base, trades, START, HOLDOUT_START)}
    if holdout:
        out["holdout"] = period_stats(r, base, trades, HOLDOUT_START, end + pd.Timedelta(days=1))
    return out


def gate(main: dict, costly: dict, surv: dict | None) -> dict:
    checks = []
    for name in ("train", "test"):
        s = main[name]
        checks.append({"name": f"Tobblet az egyenlo sulyu univerzum felett ({name})",
                       "ok": (s.get("excess") or -1) > GATE["excess"], "value": s.get("excess")})
        checks.append({"name": f"Sharpe >= {GATE['sharpe']} ({name})",
                       "ok": (s.get("sharpe") or 0) >= GATE["sharpe"], "value": s.get("sharpe")})
        checks.append({"name": f"2x koltseggel is tobblet ({name})",
                       "ok": (costly[name].get("excess") or -1) > GATE["excess_2x_cost"],
                       "value": costly[name].get("excess")})
        if surv is not None:
            checks.append({"name": f"Survivorship-lista: tobblet ({name})",
                           "ok": (surv[name].get("excess") or -1) > GATE["survivorship_excess"],
                           "value": surv[name].get("excess")})
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


def run(symbols: list[str], settings: Settings | None = None, params: MomentumParams | None = None,
        holdout: bool = False, survivorship_symbols: list[str] | None = None, progress=print) -> dict:
    settings = settings or load_settings()
    params = params or settings.strategies.momentum.params
    end = None if holdout else HOLDOUT_START - pd.Timedelta(days=1)      # a holdout adatot be sem toltjuk
    progress(f"Adat betoltese: {len(symbols)} papir...")
    panel = load_panel(symbols, settings, end)
    if not panel:
        raise ValueError("Nincs adat - futtasd: python cli.py fetch-universe (vagy az indexszkennelest)")
    last = panel["close"].index[-1]
    progress(f"Szimulacio: {panel['close'].shape[1]} papir, {panel['close'].index[0]:%Y-%m-%d} - {last:%Y-%m-%d}")
    main = simulate(panel, params)
    costly = simulate(panel, params.model_copy(update={"cost_bps": params.cost_bps * 2}))
    report = {"params": params.model_dump(), "symbols": int(panel["close"].shape[1]), "holdout": holdout,
              "main": split(main, holdout, last), "cost_2x": split(costly, False, last),
              "open_positions": main["open"]}
    surv = None
    if survivorship_symbols:
        progress(f"Survivorship-ellenorzes: {len(survivorship_symbols)} papir")
        other = load_panel(survivorship_symbols, settings, end)
        if other:
            surv = split(simulate(other, params), False, last)
            report["survivorship"] = {"symbols": int(other["close"].shape[1]), **surv}
    report["gate"] = gate(report["main"], report["cost_2x"], surv)
    if holdout:
        log_holdout_opening(["momentum: " + json.dumps(params.model_dump())], report["symbols"], study="momentum")
    save(report, main["trades"])
    return report


# ---------------------------------------------------------------------------
# Kimenet (lemezplafonnal)
# ---------------------------------------------------------------------------
def _f(v, d: int = 1, unit: str = "%") -> str:
    return "-" if v is None else f"{v:+.{d}f}{unit}" if unit == "%" else f"{v:.{d}f}{unit}"


def report_markdown(r: dict) -> str:
    p = r["params"]
    lines = ["# Momentum (12-1) - validacio", "",
             f"Papirok: {r['symbols']} | top {p['top_n']}, puffer {p['buffer']}, ablak {p['lookback']} nap "
             f"(utolso {p['skip']} nelkul), stop {p['stop_pct'] or 'nincs'}, koltseg {p['cost_bps']} bps/oldal", "",
             "| Idoszak | CAGR | Alap (EW) | Tobblet/ev | Sharpe | Max DD | Honap+ | Kotes | Nyero | Atlag/kotes |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    names = {"train": "Tanito", "test": "Validacio", "visible": "Lathato osszesen", "holdout": "HOLDOUT"}
    for key in ("train", "test", "visible", "holdout"):
        s = r["main"].get(key)
        if not s or "cagr" not in s:
            continue
        lines.append(f"| {names[key]} | {_f(s['cagr'])} | {_f(s['baseline_cagr'])} | {_f(s['excess'])} | "
                     f"{_f(s['sharpe'], 2, '')} | {_f(s['max_dd'])} | {s['month_win'] * 100:.0f}% | {s['trades']} | "
                     f"{(s['trade_win'] or 0) * 100:.0f}% | {_f(s['trade_avg'], 2)} |")
    if r.get("survivorship"):
        sv = r["survivorship"]
        lines += ["", f"Survivorship-lista ({sv['symbols']} papir): tanito tobblet {_f(sv['train'].get('excess'))}, "
                      f"validacio {_f(sv['test'].get('excess'))}"]
    lines += ["", f"2x koltseggel: tanito tobblet {_f(r['cost_2x']['train'].get('excess'))}, "
                  f"validacio {_f(r['cost_2x']['test'].get('excess'))}", "",
              f"## Elfogadasi feltetel: {'TELJESUL' if r['gate']['passed'] else 'NEM teljesul'}", ""]
    lines += [f"- {'OK ' if c['ok'] else 'NEM'} {c['name']}: {c['value'] if c['value'] is None else round(c['value'], 2)}"
              for c in r["gate"]["checks"]]
    vis = r["main"]["visible"].get("yearly") or {}
    if vis:
        lines += ["", "## Evenkent (%)", "", " | ".join(f"{y}: {v:+.1f}" for y, v in vis.items())]
    lines += ["", "Csak a mostani indextagok - a tulelesi torzitas felfele hat. Nem befektetesi tanacs."]
    return "\n".join(lines) + "\n"


def _default(o):
    if isinstance(o, pd.Timestamp):
        return f"{o:%Y-%m-%d}"
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(o) else None
    return str(o)


def save(report: dict, trades: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clean = json.loads(json.dumps(report, default=_default))
    clean["computed_at"] = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    (OUT_DIR / "summary.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "report.md").write_text(report_markdown(clean), encoding="utf-8")
    buffer = io.StringIO()
    pd.DataFrame(trades).to_csv(buffer, index=False)
    data = gzip.compress(buffer.getvalue().encode())
    if len(data) <= LIMIT_MB * 1024 * 1024:                       # lemezplafon
        (OUT_DIR / "trades.csv.gz").write_bytes(data)


def load_summary() -> dict | None:
    path = OUT_DIR / "summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None
