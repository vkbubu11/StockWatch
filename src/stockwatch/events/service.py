"""Esemenytanulmany futtatasa.

  * web: egy papir vagy a watchlist a webes folyamat hatterszalaban (gyors); nagyobb univerzum a labor-
    munkasoron (labqueue, "event_study"), alacsony prioritassal
  * CLI: python cli.py event-study --type earnings --universe sp500 --start 2017-01-01 --window -10 60

A zarolt holdout alapbol kimarad: az arfolyam a HOLDOUT_START elotti napon vagodik, es az ablakba be nem fero
esemeny kimarad. Kapcsoloval (include_holdout) megnyithato - ez naplozodik (holdout_log, study=event-study).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import labqueue, ledger, market, usage, workspace
from .. import meanrev_research as mr
from ..config import Settings, load_settings
from ..entry_research import HOLDOUT_START, log_holdout_opening
from . import catalog, store, study
from .sources import form4, index_changes
from .templates import TEMPLATES

log = logging.getLogger(__name__)

VISIBLE_END = HOLDOUT_START - pd.Timedelta(days=1)
INLINE_UNIVERSES = ("symbol", "watchlist")
MAX_EVENTS_STORED = 20_000
FEW_EVENTS = 30
CI_LEVEL = 95
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")
UNIVERSE_LABELS = {"symbol": "Egy papír", "watchlist": "Watchlist", "nasdaq100": "Nasdaq-100", "sp500": "S&P 500",
                   "indexes": "Nasdaq-100 + S&P 500"}


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["earnings", "filing_8k", "high52", "low52", "gap", "strategy", "insider_buy", "insider_sell",
                  "insider_cluster_buy", "sp500_add", "sp500_remove"]
    params: dict = Field(default_factory=dict)
    universe: Literal["symbol", "watchlist", "nasdaq100", "sp500", "indexes"] = "indexes"
    symbol: str | None = None
    sector: str | None = None
    start: date = date(2017, 1, 1)
    end: date | None = None
    window: tuple[int, int] = (-10, 60)
    estimation: tuple[int, int] = (-260, -21)
    benchmark: Literal["index", "SPY"] = "index"
    include_holdout: bool = False

    @model_validator(mode="after")
    def _check(self) -> "StudySpec":
        a, b = self.window
        e0, e1 = self.estimation
        if not -60 <= a <= 0 < b <= 250:
            raise ValueError("window: -60 <= kezdet <= 0 < veg <= 250")
        if not -1000 <= e0 < e1 < a:
            raise ValueError("estimation: a becslesi ablak az esemenyablak elott erjen veget (veg < ablak kezdete)")
        if self.universe == "symbol":
            self.symbol = (self.symbol or "").strip().upper()
            if not SYMBOL_RE.match(self.symbol):
                raise ValueError("symbol: ervenytelen ticker")
        if self.end is not None and self.end < self.start:
            raise ValueError("end: a kezdet utan legyen")
        if self.type == "strategy" and self.params.get("strategy", "pullback") not in catalog.STRATEGIES:
            raise ValueError(f"params.strategy: {', '.join(catalog.STRATEGIES)}")
        unknown = set(self.params) - set(catalog.TYPES[self.type]["params"])
        if unknown:
            raise ValueError(f"params: ismeretlen mezo: {', '.join(sorted(unknown))}")
        return self


@dataclass
class Line:
    key: str
    args: list[str] = field(default_factory=list)
    numeric: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return re.sub(r"\{(\d+)(?::n)?\}", lambda m: self.args[int(m.group(1))], TEMPLATES[self.key])

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.args, "numeric": self.numeric, "text": self.text}


# ---------------------------------------------------------------------------
# Adat
# ---------------------------------------------------------------------------
def universe(spec: StudySpec, settings: Settings, ws: str = workspace.DEFAULT) -> list[str]:
    from .. import indexes

    if spec.universe == "symbol":
        return [spec.symbol]
    if spec.universe == "watchlist":
        return sorted(workspace.watchlist(ws))
    members = indexes.IndexMembers(settings)
    names = ("nasdaq100", "sp500") if spec.universe == "indexes" else (spec.universe,)
    return sorted({s for n in names for s in members.members(n, allow_network=False)})


def prices(symbol: str, settings: Settings, end: pd.Timestamp) -> pd.DataFrame | None:
    """A papir gyertyai a vizsgalt idoszak vegeig: az elo / univerzum-cache, vagy a Backteszt oldal tara, ha az
    hosszabb tortenetet tartalmaz."""
    from ..backtest_page import data as bt_data

    df = mr.load_raw(symbol, settings)
    try:
        longer = bt_data.load(symbol)
    except (OSError, ValueError):
        longer = None
    if longer is not None and not longer.empty and (df.empty or longer.index[0] < df.index[0]):
        df = longer
    df = df.loc[:end]
    return None if df.empty else df[["open", "high", "low", "close", "volume"]].astype(float)


def _bench_frames(settings: Settings, end: pd.Timestamp) -> dict[str, pd.Series]:
    out = {}
    for symbol in ("QQQ", "^GSPC", "SPY"):
        df = market.load_index(symbol, settings, refresh=False)
        if df is not None and not df.empty:
            out[symbol] = df["close"].astype(float).loc[:end]
    return out


def context(spec: StudySpec, settings: Settings, symbols: list[str], end: pd.Timestamp) -> dict:
    ctx: dict = {"settings": settings, "sectors": index_changes.load_sectors()}
    if spec.type in ("earnings", "strategy"):
        hist = market.HistoricalEarnings(settings, allow_network=False)
        ctx["earnings_dates"] = {s: hist.dates(s) for s in symbols}
        ctx["earnings_times"] = {s: hist.times(s) for s in symbols}
    if spec.type == "filing_8k":
        from ..sentiment import store as news_store

        ctx["articles"] = {s: news_store.articles(s, rescore=False) for s in symbols}
    if spec.type.startswith("insider"):
        ctx["form4"] = form4.load(symbols=symbols, start=f"{spec.start:%Y-%m-%d}", end=f"{end:%Y-%m-%d}")
    if spec.type.startswith("sp500"):
        ctx["index_changes"] = index_changes.load_changes()
    if spec.type == "strategy":
        mctx = market.load_context(settings, refresh=False, allow_network=False)
        benches = {k: (v.loc[:end] if v is not None and not v.empty else v) for k, v in mctx.benches.items()}
        ctx["market"] = market.MarketContext(benches, mctx.nasdaq, mctx.sp500)
    return ctx


def _bench_for(symbol: str, spec: StudySpec, benches: dict, nasdaq: frozenset) -> pd.Series | None:
    if spec.benchmark == "SPY":
        return benches.get("SPY")
    if symbol in nasdaq and benches.get("QQQ") is not None:
        return benches["QQQ"]
    return benches["^GSPC"] if benches.get("^GSPC") is not None else benches.get("SPY")


# ---------------------------------------------------------------------------
# Futtatas
# ---------------------------------------------------------------------------
def _pct(v) -> str:
    return "–" if v is None else f"{v:.2f}"


def _lines(spec: StudySpec, res: dict, counts: dict, end: pd.Timestamp) -> list[dict]:
    a, b = spec.window
    out = [Line("counts", [str(counts["events"]), str(counts["symbols_with_events"]), str(res["n"]),
                           str(counts["overlap"]), str(counts["incomplete"])], [0, 1, 2, 3, 4])]
    if not res["n"]:
        out.append(Line("no_events"))
    else:
        s, p = res["summary"], res["post"]
        out.append(Line("car", [str(a), str(b), _pct(s["mean"]), _pct(s["ci_lo"]), _pct(s["ci_hi"]), _pct(s["median"]),
                                f"{(s['hit'] or 0) * 100:.1f}", str(CI_LEVEL)], [0, 1, 2, 3, 4, 5, 6, 7]))
        if b >= 1:
            out.append(Line("post", ["1", str(b), _pct(p["mean"]), _pct(p["ci_lo"]), _pct(p["ci_hi"]), _pct(p["median"]),
                                     f"{(p['hit'] or 0) * 100:.1f}", str(CI_LEVEL)], [0, 1, 2, 3, 4, 5, 6, 7]))
        if res["n"] < FEW_EVENTS:
            out.append(Line("few_events", [str(res["n"])], [0]))
    if counts["date_only"]:
        out.append(Line("date_only", [str(counts["date_only"])], [0]))
    if counts["adjusted"]:
        out.append(Line("adjusted", [str(counts["adjusted"])], [0]))
    if a < 0:
        out.append(Line("pre_event"))
    if spec.universe in ("nasdaq100", "sp500", "indexes"):
        out.append(Line("survivor"))
    if spec.type.startswith("sp500"):
        out.append(Line("sp500_effective", ["S&P 500"]))
        if not counts.get("changes"):
            out.append(Line("no_changes", ["S&P 500", "python cli.py event-sources --index-changes"]))
    if spec.type.startswith("insider") and not counts.get("form4_rows"):
        out.append(Line("no_form4", ["Form 4", f"python cli.py event-sources --form4-since {spec.start.year}"]))
    out.append(Line("holdout_open") if spec.include_holdout else Line("holdout_hidden", [f"{HOLDOUT_START:%Y-%m-%d}"]))
    out.append(Line("disclaimer"))
    return [ln.to_dict() for ln in out]


def run(spec: StudySpec, settings: Settings | None = None, ws: str = workspace.DEFAULT, progress=None,
        refresh: bool = False) -> dict:
    settings = settings or load_settings()
    say = progress or (lambda msg: None)
    with usage.meter(ws, "event_study"):
        symbols = universe(spec, settings, ws)
        if not symbols:
            raise ValueError("Az univerzum ures (nincs letoltott indexosszetetel / watchlist).")
        if spec.include_holdout:
            end = pd.Timestamp(spec.end or date.today())
            log_holdout_opening([f"event-study:{spec.type}"], len(symbols), study="event-study")
        else:
            end = min(pd.Timestamp(spec.end), VISIBLE_END) if spec.end else VISIBLE_END
        if refresh and spec.type == "filing_8k":
            from ..sentiment import service as news

            for n, symbol in enumerate(symbols, 1):
                if n % 25 == 1:
                    say(f"8-K bejelentesek: {n}/{len(symbols)}")
                news.refresh(symbol, settings, since=pd.Timestamp(spec.start) - pd.Timedelta(days=30),
                             sources=["edgar"])
        ctx = context(spec, settings, symbols, end)
        sectors = ctx["sectors"]
        benches = _bench_frames(settings, end)
        from .. import indexes

        nasdaq = frozenset(indexes.IndexMembers(settings).members("nasdaq100", allow_network=False))
        outcomes, index_of = [], {}
        counts = {"events": 0, "incomplete": 0, "no_data": 0, "date_only": 0, "adjusted": 0,
                  "symbols_with_events": 0, "changes": len(ctx.get("index_changes") or []),
                  "form4_rows": len(ctx["form4"]) if "form4" in ctx else None}
        lo = pd.Timestamp(spec.start)
        for n, symbol in enumerate(symbols, 1):
            if n % 50 == 1:
                say(f"esemenyek: {n}/{len(symbols)} ({symbol})")
            if spec.sector and sectors.get(symbol) != spec.sector:
                continue
            df = prices(symbol, settings, end)
            bench = _bench_for(symbol, spec, benches, nasdaq)
            if df is None or bench is None:
                counts["no_data"] += 1
                continue
            bench = bench.reindex(df.index, method="ffill")
            events = [e for e in catalog.detect(spec.type, symbol, df, ctx, spec.params) if lo <= e.day <= end]
            if events:
                counts["symbols_with_events"] += 1
            counts["events"] += len(events)
            for e in events:
                got = study.abnormal(df["close"], bench, e.day, spec.window, spec.estimation)
                if got is None:
                    counts["incomplete"] += 1
                    continue
                ar, model, est_n, alpha, beta = got
                outcomes.append(study.Outcome(symbol, e.day, ar, model, est_n, alpha, beta, e.attrs, e.exact,
                                              {"direction": e.attrs.get("direction"),
                                               "sector": sectors.get(symbol, "unknown"),
                                               "regime": study.regime_on(bench, e.day), "year": int(e.day.year)}))
            index_of[symbol] = df.index
        kept, counts["overlap"] = study.drop_overlaps(outcomes, index_of, spec.window)
        counts["date_only"] = sum(1 for o in kept if not o.exact)
        counts["adjusted"] = sum(1 for o in kept if o.model == "adjusted")
        say(f"osszesites: {len(kept)} esemeny")
        res = study.aggregate(kept, spec.window)
        post_from = max(0, 1 - spec.window[0])
        events = [{"symbol": o.symbol, "day": f"{o.day:%Y-%m-%d}", "attrs": o.attrs, "exact": o.exact,
                   "car": round(float(o.ar.sum()) * 100, 4), "post": round(float(o.ar[post_from:].sum()) * 100, 4),
                   "model": o.model, "est_n": o.est_n, "groups": o.groups}
                  for o in sorted(kept, key=lambda o: (o.day, o.symbol))][:MAX_EVENTS_STORED]
        result = dict(res, counts=counts, symbols=len(symbols))
        record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "spec": spec.model_dump(mode="json"), "end": f"{end:%Y-%m-%d}", "workspace": ws,
                  "code_version": ledger.code_version(), "result": result, "events": events,
                  "lines": _lines(spec, res, counts, end), "type_label": catalog.TYPES[spec.type]["label"],
                  "universe_label": UNIVERSE_LABELS[spec.universe]}
        store.save(record, ws)
    return record


# ---------------------------------------------------------------------------
# Egy esemeny reszletei, CSV
# ---------------------------------------------------------------------------
def event_detail(record: dict, n: int, settings: Settings | None = None) -> dict:
    """Egy esemeny a kozeleben: a papir es a benchmark zaroara (100 = az esemeny elotti nap), az AR es a CAR."""
    settings = settings or load_settings()
    if not 0 <= n < len(record["events"]):
        raise ValueError("Nincs ilyen esemeny.")
    ev = record["events"][n]
    spec = StudySpec(**record["spec"])
    end = pd.Timestamp(record["end"])
    df = prices(ev["symbol"], settings, end)
    if df is None:
        raise ValueError("Nincs arfolyamadat.")
    from .. import indexes

    nasdaq = frozenset(indexes.IndexMembers(settings).members("nasdaq100", allow_network=False))
    bench = _bench_for(ev["symbol"], spec, _bench_frames(settings, end), nasdaq).reindex(df.index, method="ffill")
    day = pd.Timestamp(ev["day"])
    i = df.index.get_loc(day)
    a, b = spec.window
    sl = slice(max(i + a - 1, 0), min(i + b + 1, len(df)))
    close, ref = df["close"].iloc[sl], bench.iloc[sl]
    base = df["close"].iloc[i - 1] if i > 0 else df["close"].iloc[i]
    got = study.abnormal(df["close"], bench, day, spec.window, spec.estimation)
    ar = got[0] if got else None
    return {"symbol": ev["symbol"], "day": ev["day"], "attrs": ev["attrs"], "car": ev["car"], "post": ev["post"],
            "rel": [int(k - i) for k in range(sl.start, sl.stop)], "dates": [f"{d:%Y-%m-%d}" for d in close.index],
            "close": (close / base * 100).round(3).tolist(),
            "bench": (ref / (bench.iloc[i - 1] if i > 0 else bench.iloc[i]) * 100).round(3).tolist(),
            "car_path": (pd.Series(ar).cumsum() * 100).round(4).tolist() if ar is not None else None}


def to_csv(record: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["symbol", "day", "car_pct", "post_pct", "model", "est_n", "exact", "direction", "sector",
                     "regime", "year", "attrs"])
    for e in record["events"]:
        g = e.get("groups") or {}
        writer.writerow([e["symbol"], e["day"], e["car"], e["post"], e["model"], e["est_n"], e["exact"],
                         g.get("direction") or "", g.get("sector") or "", g.get("regime") or "", g.get("year") or "",
                         " ".join(f"{k}={v}" for k, v in sorted(e["attrs"].items()))])
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Munkasor
# ---------------------------------------------------------------------------
def submit(spec: StudySpec, ws: str = workspace.DEFAULT) -> dict:
    if not usage.allow(ws, "event_study"):
        raise ValueError("Elfogyott a havi kutatasi keret.")
    return labqueue.submit(ws, "event_study", {"spec": spec.model_dump(mode="json")})


def run_job(job: dict, settings: Settings) -> dict:
    record = run(StudySpec(**job["payload"]["spec"]), settings, job["workspace"], refresh=True)
    return {"message": f"kesz: {record['result']['n']} esemeny", "study_id": record["id"]}
