"""A kereses: valtozatok ertekelese a MEGLEVO motorokkal, dontosok, robusztussagi lepesek, koltsegkeret.

EGYETLEN IGAZSAGFORRAS
  Visszahuzas-csalad: meanrev_research.load_universe + evaluate_variant (meanrev.simulate_trade, portfolio,
  stats, split, gate) - ugyanaz, mint a `meanrev-research` es az eles futas.
  Kitores: lab.evaluate (entry_research szimulacio, a labor kitores-cache-evel) es entry_research.stats.

BECSULETESSEG (HOLDOUT-OR)
  * minden adat a HOLDOUT_START elott VAGODIK (VISIBLE_END), es ezt minden ertekeles elott ellenorizzuk
  * a KIVALASZTAS csak a tanito idoszakon (az adat a SPLIT_DATE elott vagodik, TRAIN_END)
  * a validacios idoszakot (SPLIT_DATE -> HOLDOUT_START) csak a dontosokre nezzuk meg, egyszer
  * a holdout-naplot (entry_research.log_holdout_opening) ez a modul soha nem hivja

KOLTSEGKERET
  valtozatszam (a kereses uj valtozatai) es CPU-perc. A CPU-keret SEARCH_SHARE resze jut a keresesre, a
  maradek a dontosok ellenorzesere. Ha elfogy (vagy elmulik a hatarido), a kereses leall, es az addigi
  legjobbakbol keszul jelentes; a kimaradt lepesek a jelentesben latszanak.
"""
from __future__ import annotations

import gc
import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import market, workspace
from .. import meanrev_research as mr
from ..config import Settings
from ..entry_research import HOLDOUT_START, SPLIT_DATE
from . import strategy
from .brief import Brief
from .space import Space

log = logging.getLogger(__name__)

VISIBLE_END = HOLDOUT_START - pd.Timedelta(days=1)
TRAIN_END = SPLIT_DATE - pd.Timedelta(days=1)
FINALISTS = 3
SEARCH_SHARE = 0.8
BATCH = 8                            # ennyi valtozat utan ellenorzi a keretet
PF_CAP = 99.0                        # vesztes nelkuli profit-faktor (vegtelen) helyett - JSON-ban is ertelmes
VIOLATIONS = ("few_trades", "too_rare", "too_frequent", "drawdown", "pf", "win_lo", "hold")


class HoldoutLeak(AssertionError):
    """A kutatougynok holdout-idoszaki adatot kapott volna - ez soha nem tortenhet meg."""


def guard(last_dates) -> pd.Timestamp | None:
    """Az atadott adat utolso napja < HOLDOUT_START; kulonben HoldoutLeak."""
    last = max((pd.Timestamp(d) for d in last_dates if d is not None), default=None)
    if last is not None and last >= HOLDOUT_START:
        raise HoldoutLeak(f"A kutatougynok holdout-adatot kapott volna ({last:%Y-%m-%d}).")
    return last


# ---------------------------------------------------------------------------
# Keret
# ---------------------------------------------------------------------------
@dataclass
class Budget:
    max_variants: int
    cpu_minutes: float
    deadline: datetime | None = None
    clock: object = time.process_time
    now: object = field(default=lambda: datetime.now(timezone.utc))
    started: float = 0.0

    def __post_init__(self) -> None:
        self.started = self.clock()

    @property
    def cpu_seconds(self) -> float:
        return self.clock() - self.started

    def _late(self) -> bool:
        return self.deadline is not None and self.now() >= self.deadline

    def search_stop(self, tried: int) -> str | None:
        if tried >= self.max_variants:
            return "variants"
        if self.cpu_seconds >= self.cpu_minutes * 60 * SEARCH_SHARE:
            return "cpu"
        return "deadline" if self._late() else None

    def final_stop(self) -> str | None:
        if self.cpu_seconds >= self.cpu_minutes * 60:
            return "cpu"
        return "deadline" if self._late() else None


# ---------------------------------------------------------------------------
# Celfuggveny
# ---------------------------------------------------------------------------
def assess(m: dict | None, brief: Brief, fraction: float = 1.0) -> dict:
    """Feltetelek (brief.objective) es pontszam: ev_lo, az atlagos netto hozam (Kitoresnel R) bootstrap also
    hatara. A rangsor: kevesebb nem teljesult feltetel elol, azon belul a nagyobb ev_lo."""
    obj = brief.objective
    if not m or not m.get("n"):
        return {"violations": ["few_trades"], "score": None}
    v = []
    if m["n"] < obj.min_trades * fraction:
        v.append("few_trades")
    lo, hi = obj.trades_per_week
    if m.get("per_week") is not None and m["per_week"] < lo:
        v.append("too_rare")
    if m.get("per_week") is not None and m["per_week"] > hi:
        v.append("too_frequent")
    if (m.get("max_dd") or 0) > obj.max_drawdown_pct:
        v.append("drawdown")
    if obj.min_pf is not None and (m.get("pf") or 0) < obj.min_pf:
        v.append("pf")
    if obj.min_win_lo is not None and (m.get("win_lo") or 0) < obj.min_win_lo:
        v.append("win_lo")
    if (m.get("held") or 0) > obj.max_hold_days:
        v.append("hold")
    return {"violations": v, "score": m.get("ev_lo")}


def rank_key(item: dict) -> tuple:
    a = item["assess"]
    score = a["score"] if a["score"] is not None else -math.inf
    return (len(a["violations"]), -score, item["key"])


def _num(v, digits: int = 4):
    if v is None:
        return None
    v = float(v)
    if math.isinf(v):
        return PF_CAP
    return None if math.isnan(v) else round(v, digits)


# ---------------------------------------------------------------------------
# Motorok
# ---------------------------------------------------------------------------
def universe_symbols(brief: Brief, settings: Settings, ws: str = workspace.DEFAULT) -> list[str]:
    from .. import indexes, lab

    if brief.universe == "watchlist":
        return sorted(workspace.watchlist(ws))
    if brief.universe == "etf":
        return sorted(settings.strategies.etf.symbols)
    if brief.universe in ("nasdaq100", "sp500"):
        return sorted(indexes.IndexMembers(settings).members(brief.universe, allow_network=False))
    return sorted(lab.universe_symbols(brief.universe, settings))


def fingerprint(frames: dict[str, pd.DataFrame]) -> str:
    digest = hashlib.sha1()
    for symbol in sorted(frames):
        df = frames[symbol]
        if df is None or df.empty:
            continue
        digest.update(f"{symbol}|{len(df)}|{df.index[0]:%Y-%m-%d}|{df.index[-1]:%Y-%m-%d}|"
                      f"{float(df['close'].iloc[-1]):.4f}|{float(df['close'].sum()):.2f}".encode())
    return digest.hexdigest()[:16]


class PullbackEngine:
    """A Visszahuzas-csalad: meanrev_research.load_universe + evaluate_variant."""

    unit = "%"

    def __init__(self, space: Space, brief: Brief, settings: Settings, symbols: list[str], progress=None) -> None:
        self.space, self.brief, self.settings, self.progress = space, brief, settings, progress
        raw = {}
        for n, symbol in enumerate(symbols, 1):
            if progress and n % 50 == 1:
                progress(f"adat: {n}/{len(symbols)}")
            df = mr.load_raw(symbol, settings)
            if not df.empty:
                raw[symbol] = df.loc[:VISIBLE_END, ["open", "high", "low", "close", "volume"]].copy()
        guard(df.index[-1] for df in raw.values() if not df.empty)
        self.raw = raw
        self.symbols = sorted(raw)
        ctx = market.load_context(settings, refresh=False, allow_network=False)
        benches = {k: (v.loc[:VISIBLE_END] if v is not None and not v.empty else v) for k, v in ctx.benches.items()}
        guard(v.index[-1] for v in benches.values() if v is not None and not v.empty)
        self.ctx = market.MarketContext(benches, ctx.nasdaq, ctx.sp500)
        self.earnings = market.HistoricalEarnings(settings, allow_network=False)
        self._views: dict = {}

    @property
    def data_fingerprint(self) -> str:
        return fingerprint(self.raw)

    def _view(self, fkey: tuple, params, fraction: float, end: pd.Timestamp) -> dict:
        key = (fkey, fraction, end)
        if key not in self._views:
            self._views.clear()                       # memoriakimelo: egyszerre egy nezet (a labor plafonja 450 MB)
            gc.collect()
            names = strategy.subset(self.symbols, fraction, self.brief.search.seed)
            data = mr.load_universe(names, self.settings, params, self.ctx, self.earnings, end=end, frames=self.raw)
            guard(d.feat.index[-1] for d in data.values())
            self._views[key] = data
        return self._views[key]

    def metrics(self, s: dict, fraction: float = 1.0) -> dict:
        per_week = s.get("fills_per_week")
        return {"n": int(s.get("n") or 0), "per_week": _num(per_week / fraction if per_week is not None else None),
                "win": _num(s.get("win")), "win_lo": _num(s.get("win_lo")), "ev": _num(s.get("ev")),
                "ev_lo": _num(s.get("ev_lo")), "pf": _num(s.get("pf")), "max_dd": _num(s.get("max_dd_pct")),
                "held": _num(s.get("held")), "total": _num(s.get("total_pct"))}

    def evaluate_many(self, items: list[tuple[str, dict]], fraction: float, period: str = "train",
                      cost_mult: float = 1.0) -> dict[str, dict]:
        """items: (kulcs, valtozat). period: train (a tanito adaton) | visible (a holdout elotti adaton)."""
        end = TRAIN_END if period == "train" else VISIBLE_END
        out = {}
        data = None
        for key, assignment in sorted(items, key=lambda kv: (str(self.space.feature_key(kv[1])), kv[0])):
            params = self.space.params(assignment)
            if cost_mult != 1.0:
                params = params.model_copy(update={"cost_bps": params.cost_bps * cost_mult})
            data = None                                # a regi nezet ne eljen, amig az uj epul
            data = self._view(self.space.feature_key(assignment), params, fraction, end)
            if not data:
                out[key] = None
                continue
            res = mr.evaluate_variant(data, params, holdout=False)
            split = res["split"]
            if period == "train":
                out[key] = {"train": self.metrics(split["train"], fraction)}
            else:
                closed = sorted((t for t in res["orders"] if t.status == "closed" and t.signal_date < HOLDOUT_START),
                                key=lambda t: t.exit_date)
                curve = mr.equity_curve([t.net_pct for t in closed], params)
                out[key] = {"train": self.metrics(split["train"]), "test": self.metrics(split["test"]),
                            "visible": self.metrics(split["visible"]), "years": split["years"],
                            "equity": [[f"{t.exit_date:%Y-%m-%d}", round((v - 1) * 100, 3)]
                                       for t, v in zip(closed, curve)],
                            "_split": split}
        return out

    def gate(self, visible: dict, costly: dict) -> dict:
        g = mr.gate(visible["_split"], costly["_split"])
        return {"passed": g["passed"], "checks": [[name, bool(ok)] for name, ok in g["checks"]]}


class BreakoutEngine:
    """A Kitores-motor a laboron keresztul: lab.evaluate (entry_research szimulacio, kitores-cache)."""

    unit = "R"

    def __init__(self, space: Space, brief: Brief, settings: Settings, symbols: list[str], progress=None) -> None:
        from .. import lab

        self.space, self.brief, self.settings, self.progress = space, brief, settings, progress
        self.symbols = sorted(s for s in symbols if lab.load_cache(s))
        firsts, lasts = [], []
        for s in self.symbols:
            df = lab._raw(s, settings)
            if not df.empty:
                firsts.append(df.index[0])
                lasts.append(min(df.index[-1], VISIBLE_END))
        self.start = (min(firsts) + pd.offsets.BDay(320)) if firsts else SPLIT_DATE
        self._fp = hashlib.sha1("|".join(f"{s}" for s in self.symbols).encode()).hexdigest()[:16]
        self.risk_pct = settings.entry.risk_per_trade_pct

    @property
    def data_fingerprint(self) -> str:
        return self._fp

    def _weeks(self, lo: pd.Timestamp, hi: pd.Timestamp) -> float:
        return max(len(pd.bdate_range(lo, hi)) / 5, 1e-9)

    def metrics(self, results: list, lo, hi, fraction: float = 1.0) -> dict:
        from .. import entry_research as er

        s = er.stats(results)
        weeks = self._weeks(lo, hi)
        return {"n": s["n"], "per_week": _num(s["n"] / weeks / fraction), "win": _num(s["win"]),
                "win_lo": _num(s["win_lo"]), "ev": _num(s["ev"]), "ev_lo": _num(s["ev_lo"]), "pf": _num(s["pf"]),
                "max_dd": _num((s["dd"] or 0) * self.risk_pct), "held": _num(np.mean([x.held for x in results]))
                if results else None, "total": _num(s["total"])}

    def evaluate_many(self, items: list[tuple[str, dict]], fraction: float, period: str = "train",
                      cost_mult: float = 1.0) -> dict[str, dict]:
        from .. import entry_research as er
        from .. import lab

        end = TRAIN_END if period == "train" else VISIBLE_END
        names = strategy.subset(self.symbols, fraction, self.brief.search.seed)
        exps = []
        for key, assignment in items:
            cfg = self.space.params(assignment)
            if cost_mult != 1.0:
                cfg = cfg.model_copy(update={"slippage_bps": cfg.slippage_bps * cost_mult})
            exps.append({"id": key, "name": key, "universe": "all", "entry": cfg.model_dump(), "live": False})
        acc = lab.evaluate(exps, names, self.settings, until=end, guard=guard) if names else {}
        out = {}
        for key, _ in items:
            results = sorted((x for x in acc.get(key, []) if x.date <= end), key=lambda x: (x.date, x.symbol))
            if period == "train":
                out[key] = {"train": self.metrics(results, self.start, TRAIN_END, fraction)}
                continue
            train = [x for x in results if x.date < SPLIT_DATE]
            test = [x for x in results if x.date >= SPLIT_DATE]
            r = np.array([x.r for x in sorted(results, key=lambda x: x.date)], dtype=float)
            curve = np.cumprod(1 + r * self.risk_pct / 100) if r.size else np.array([])
            ordered = sorted(results, key=lambda x: x.date)
            out[key] = {"train": self.metrics(train, self.start, TRAIN_END),
                        "test": self.metrics(test, SPLIT_DATE, VISIBLE_END),
                        "visible": self.metrics(results, self.start, VISIBLE_END),
                        "years": {str(y): {"n": v["n"], "win": None, "ev": v["ev"]}
                                  for y, v in er.yearly(results)["by_year"].items()},
                        "equity": [[f"{x.date:%Y-%m-%d}", round((v - 1) * 100, 3)] for x, v in zip(ordered, curve)]}
        return out

    def gate(self, visible: dict, costly: dict) -> dict:
        checks = []
        for name in ("train", "test"):
            m = visible[name]
            checks.append([f"{name}: varhato ertek > 0 (bootstrap also hatar is)", (m.get("ev_lo") or -1) > 0])
            checks.append([f"{name}: profit-faktor >= 1.3", (m.get("pf") or 0) >= 1.3])
        checks.append(["2x koltseggel is pozitiv varhato ertek", (costly["visible"].get("ev") or -1) > 0])
        return {"passed": all(ok for _, ok in checks), "checks": checks}


def make_engine(space: Space, brief: Brief, settings: Settings, symbols: list[str], progress=None):
    cls = BreakoutEngine if brief.engine == "breakout" else PullbackEngine
    return cls(space, brief, settings, symbols, progress)


# ---------------------------------------------------------------------------
# A kereses
# ---------------------------------------------------------------------------
class Search:
    def __init__(self, brief: Brief, settings: Settings, engine, space: Space, budget: Budget,
                 log_step=None, progress=None) -> None:
        self.brief, self.settings, self.engine, self.space, self.budget = brief, settings, engine, space, budget
        self.log_step = log_step or (lambda rec: None)
        self.progress = progress or (lambda msg: None)
        self.items: dict[str, dict] = {}          # kulcs -> {key, assignment, changes, rejected, results}
        self.cache: dict[tuple, dict | None] = {}
        self.tried = 0                            # a kereses uj (ervenyes vagy elutasitott) valtozatai
        self.rejected = 0
        self.extra_evals = 0                      # robusztussagi lepesek ertekelesei (nem szamit a keretbe)
        self.stopped: str | None = None
        self.skipped: list[str] = []
        self.seq = 0

    # -- naplo -------------------------------------------------------------
    def _log(self, **rec) -> None:
        self.seq += 1
        self.log_step({"i": self.seq, **rec})

    # -- valtozatok --------------------------------------------------------
    def _register(self, assignment: dict, phase: str, count: bool = True) -> dict | None:
        key = self.space.key(assignment)
        if key in self.items:
            return self.items[key]
        item = {"key": key, "assignment": assignment, "changes": self.space.changes(assignment), "rejected": None}
        try:
            self.space.params(assignment)
        except (ValueError, TypeError) as exc:
            item["rejected"] = str(exc).splitlines()[0][:200]
            if count:
                self.rejected += 1
        if count:
            self.tried += 1
        self.items[key] = item
        if item["rejected"]:
            self._log(phase=phase, variant=key, changes=item["changes"], verdict="rejected", reason=item["rejected"])
        return item

    def evaluate(self, items: list[dict], fraction: float, period: str = "train", cost_mult: float = 1.0,
                 extra: bool = False) -> dict[str, dict | None]:
        todo = [(i["key"], i["assignment"]) for i in items if not i["rejected"]
                and (i["key"], fraction, period, cost_mult) not in self.cache]
        if todo:
            got = self.engine.evaluate_many(todo, fraction, period, cost_mult)
            for key, _ in todo:
                self.cache[(key, fraction, period, cost_mult)] = got.get(key)
            if extra:
                self.extra_evals += len(todo)
        return {i["key"]: self.cache.get((i["key"], fraction, period, cost_mult)) for i in items if not i["rejected"]}

    def _scored(self, items: list[dict], fraction: float) -> list[dict]:
        out = []
        for item in items:
            if item["rejected"]:
                continue
            res = self.cache.get((item["key"], fraction, "train", 1.0))
            m = res["train"] if res else None
            out.append({"key": item["key"], "item": item, "metrics": m, "assess": assess(m, self.brief, fraction)})
        return sorted(out, key=rank_key)

    def _evaluate_budgeted(self, items: list[dict], fraction: float, phase: str, rung: int) -> list[dict]:
        done = []
        for start in range(0, len(items), BATCH):
            reason = self.budget.search_stop(0)          # itt csak a CPU / hatarido szamit (a valtozatszam mar)
            if reason in ("cpu", "deadline"):
                self.stopped = self.stopped or reason
                break
            chunk = items[start:start + BATCH]
            self.evaluate(chunk, fraction)
            done += chunk
            self.progress(f"{phase} {rung + 1}: {len(done)}/{len(items)} valtozat")
        return done

    # -- futas --------------------------------------------------------------
    def run(self) -> dict:
        b = self.brief
        plan = strategy.plan(self.space, b.search.method, b.search.max_variants, b.search.seed)
        current = []
        for a in plan.initial:
            if self.budget.search_stop(self.tried) == "variants":
                self.stopped = "variants"
                break
            item = self._register(a, "initial")
            if not item["rejected"]:
                current.append(item)
        ranked: list[dict] = []
        for rung, fraction in enumerate(plan.fractions):
            evaluated = self._evaluate_budgeted(current, fraction, "rung", rung)
            ranked = self._scored(evaluated, fraction)
            last = rung == len(plan.fractions) - 1 or self.stopped in ("cpu", "deadline")
            keep = len(ranked) if last else strategy.keep_count(len(ranked))
            for pos, r in enumerate(ranked):
                self._log(phase="rung", rung=rung + 1, fraction=fraction, variant=r["key"], changes=r["item"]["changes"],
                          score=r["assess"]["score"], violations=r["assess"]["violations"],
                          verdict="kept" if pos < keep else "dropped")
            self._log(phase="rung_summary", rung=rung + 1, fraction=fraction, evaluated=len(ranked),
                      kept=min(keep, len(ranked)), symbols=len(strategy.subset(self.engine.symbols, fraction,
                                                                                 b.search.seed)))
            if last:
                break
            current = [r["item"] for r in ranked[:keep]]
        full = [r for r in ranked]                           # a teljes papirlistan ertekelt valtozatok
        self._refine(full, plan.refine_budget)
        return self._finish()

    def _full_ranked(self) -> list[dict]:
        items = [i for i in self.items.values() if (i["key"], 1.0, "train", 1.0) in self.cache]
        return self._scored(items, 1.0)

    def _refine(self, ranked: list[dict], budget: int) -> None:
        """Koordinata-finomitas a legjobb korul: mezonkent egy lepes; a legjobb javito lepest megtartja."""
        if not ranked or budget <= 0 or self.stopped in ("cpu", "deadline"):
            return
        best = self._full_ranked()[0]
        left = budget
        while left > 0:
            reason = self.budget.search_stop(self.tried)
            if reason:
                self.stopped = reason
                return
            moves = []
            for name, value, a in self.space.neighbours(best["item"]["assignment"]):
                if self.space.key(a) in self.items and (self.space.key(a), 1.0, "train", 1.0) in self.cache:
                    continue
                if left <= 0:
                    break
                if self.space.key(a) not in self.items:
                    left -= 1
                moves.append((name, value, self._register(a, "refine")))
            valid = [m for m in moves if not m[2]["rejected"]]
            if not valid:
                return
            self.evaluate([m[2] for m in valid], 1.0)
            scored = {r["key"]: r for r in self._scored([m[2] for m in valid], 1.0)}
            for name, value, item in valid:
                r = scored[item["key"]]
                better = rank_key(r) < rank_key(best)
                self._log(phase="refine", variant=item["key"], move=[name, best["item"]["assignment"][name], value],
                          score_before=best["assess"]["score"], score=r["assess"]["score"],
                          violations=r["assess"]["violations"], verdict="better" if better else "worse")
            top = min(scored.values(), key=rank_key)
            if rank_key(top) < rank_key(best):
                self._log(phase="step", variant=top["key"], changes=top["item"]["changes"], score=top["assess"]["score"])
                best = top
            else:
                return

    def _finish(self) -> dict:
        ranked = self._full_ranked()
        finalists = ranked[:FINALISTS]
        preset_item = self._register(self.space.preset_assignment(), "preset", count=False)
        out = []
        for pos, f in enumerate(finalists, 1):
            reason = self.budget.final_stop()
            if reason:
                self.stopped = self.stopped or reason
                self.skipped.append(f"finalist_{pos}")
                out.append({"rank": pos, "key": f["key"], "item": f["item"], "train": f["metrics"],
                            "assess": f["assess"], "checked": False})
                continue
            out.append(self._check_finalist(pos, f))
        preset = None
        if not preset_item["rejected"]:
            if (preset_item["key"], 1.0, "train", 1.0) not in self.cache and not self.budget.final_stop():
                self.evaluate([preset_item], 1.0, extra=True)
            res = self.cache.get((preset_item["key"], 1.0, "train", 1.0))
            preset = {"key": preset_item["key"], "train": res["train"] if res else None}
        return {"finalists": out, "preset": preset}

    def _check_finalist(self, pos: int, f: dict) -> dict:
        item = f["item"]
        visible = self.evaluate([item], 1.0, "visible", extra=True)[item["key"]]   # a validacio: EGYSZER
        self._log(phase="final", rank=pos, variant=item["key"], verdict="validated")
        out = {"rank": pos, "key": item["key"], "item": item, "train_search": f["metrics"], "assess": f["assess"],
               "checked": True, "visible": visible}
        steps = (("cost_2x", self._cost2x), ("neighbourhood", self._neighbourhood), ("ablation", self._ablation))
        for name, fn in steps:
            if self.budget.final_stop():
                self.skipped.append(f"{name}_{pos}")
                out[name] = None
                continue
            out[name] = fn(item, f)
        if visible and out.get("cost_2x"):
            out["gate"] = self.engine.gate(visible, out["cost_2x"])
        return out

    def _cost2x(self, item: dict, f: dict) -> dict | None:
        return self.evaluate([item], 1.0, "visible", cost_mult=2.0, extra=True)[item["key"]]

    def _neighbourhood(self, item: dict, f: dict) -> dict:
        neigh = [self._register(a, "neighbourhood", count=False) for _, _, a in self.space.neighbours(item["assignment"])]
        valid = [n for n in neigh if not n["rejected"]]
        self.evaluate(valid, 1.0, extra=True)
        scored = self._scored(valid, 1.0)
        wins = [r["metrics"]["win"] for r in scored if r["metrics"] and r["metrics"].get("win") is not None]
        evs = [r["metrics"]["ev"] for r in scored if r["metrics"] and r["metrics"].get("ev") is not None]
        return {"n": len(scored), "feasible": sum(1 for r in scored if not r["assess"]["violations"]),
                "positive_ev_lo": sum(1 for r in scored if (r["assess"]["score"] or -1) > 0),
                "win_min": min(wins) if wins else None, "win_max": max(wins) if wins else None,
                "ev_min": min(evs) if evs else None, "ev_max": max(evs) if evs else None}

    def _ablation(self, item: dict, f: dict) -> list[dict]:
        """Mezonkent: a dontos, de az adott mezo a preset erteken - mennyit adott a valtoztatas (tanito)."""
        base = f["metrics"] or {}
        out = []
        preset = self.space.preset_assignment()
        for name, (old, new) in item["changes"].items():
            reverted = self._register(dict(item["assignment"]) | {name: old}, "ablation", count=False)
            if reverted["rejected"]:
                continue
            res = self.evaluate([reverted], 1.0, extra=True)[reverted["key"]]
            m = res["train"] if res else {}
            out.append({"field": name, "value": new, "preset": preset.get(name),
                        "d_win_pp": _num(((base.get("win") or 0) - (m.get("win") or 0)) * 100, 2)
                        if base.get("win") is not None and m.get("win") is not None else None,
                        "d_ev": _num((base.get("ev") or 0) - (m.get("ev") or 0), 3)
                        if base.get("ev") is not None and m.get("ev") is not None else None})
        return out
