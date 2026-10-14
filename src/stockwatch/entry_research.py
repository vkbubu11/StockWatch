"""Belepesi szabalyvaltozatok osszehasonlitasa - gyorsan es becsuletesen.

MIERT KULON MODUL?
------------------
A kitoresek megkeresese a lassu resz (gyertyankent ujra-detektalas, papironkent
~1 perc). A szabalyok (stop, kilepes, szurok) kiprobalasa viszont olcso. Ezert:

  1. `collect_events` - a kitoreseket EGYSZER kigyujtjuk, es cache-eljuk
     (data/research/<TICKER>.pkl). Tobb processzmagon parhuzamosan fut.
  2. `run_variant` - minden valtozatot ugyanazokon az esemenyeken jatszunk le,
     masodpercek alatt.

IDOSZAKOK (walk-forward + zarolt vegso teszt)
--------------------------------------------
  TANITO      2022 elott
  VALIDACIO   2022-01-01 -> HOLDOUT_START   (ezen valasztunk a valtozatok kozul)
  HOLDOUT     HOLDOUT_START-tol             ZAROLT: a normal kutatas nem latja.

A 2022 utani idoszakot a korabbi kutatas mar 192 valtozat kozotti valasztasra
hasznalta - igy az mar nem fuggetlen teszt. Ezert a legutolso ~18 honap zarolt
vegso teszt: csak `--holdout` kapcsoloval nyithato meg, es minden megnyitas
naplozodik (data/research/holdout_log.jsonl). Ha tobbszor nezzuk meg, es a
latottak alapjan valtoztatunk, a holdout is "elhasznalodik".

Egy valtozat csak akkor szamit javulasnak, ha mindket lathato idoszakban megall,
es a varhato ertek 95%-os konfidencia-intervallumanak ALSO hatara sem rosszabb a
jelenlegi beallitasenal (kevés trade-bol a pontbecsles felrevezeto). Az evenkenti
bontas mutatja, hany evben volt pozitiv - egy-ket kiugro ev nem eleg.

A "tanitobol valasztott alakzatok" szuro a mintalistat KIZAROLAG a tanito
idoszakbol hatarozza meg.

KILEPESI MODOK
--------------
  target        teljes pozicio a measured-move celig (vagy stop / idokorlat)
  partial       fele +1R-nel eladva, a stop a belepore huzva, a masik fele a celig
  trail         nincs fix cel: a stop a legmagasabb ar alatt `trail_atr` ATR-rel kovet
  partial_trail fele +1R-nel, a stop a belepore, a maradek kovetett stoppal

A szimulacio pesszimista: ha egy gyertyan a stop es a cel is teljesulhetett,
a stopot konyveljuk; gap eseten a rosszabb nyitoaron toltunk.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import math
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from . import backtest, entry, indicators, market, store
from .config import DATA_DIR, EntrySettings, Settings, load_settings
from .forecast import Calibration
from .patterns.base import PatternMatch
from .trade_mgmt import PositionState, max_hold

log = logging.getLogger(__name__)

RESEARCH_DIR = DATA_DIR / "research"
SPLIT_DATE = pd.Timestamp("2022-01-01")
HOLDOUT_START = pd.Timestamp("2025-04-01")      # innentol zarolt vegso teszt (ne mozgasd!)
HOLDOUT_LOG = RESEARCH_DIR / "holdout_log.jsonl"
CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# 1. Esemenyek kigyujtese
# ---------------------------------------------------------------------------
@dataclass
class Event:
    symbol: str
    index: int            # a kitoresi gyertya pozicioja
    horizon: int
    match: PatternMatch


def _cache_key(df: pd.DataFrame, settings: Settings) -> str:
    raw = (
        f"{CACHE_VERSION}|{len(df)}|{df.index[-1]}|{df['close'].iloc[-1]:.4f}|"
        f"{settings.patterns.model_dump_json()}|{settings.structure.model_dump_json()}|"
        f"{settings.backtest.model_dump_json()}"
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _collect_one(args: tuple[str, bool, dict]) -> tuple[str, list[Event]]:
    symbol, universe, settings_raw = args
    settings = Settings(**settings_raw)
    df = store.load(symbol, settings, universe=universe)
    if df.empty or len(df) < 320:
        return symbol, []

    path = RESEARCH_DIR / f"{symbol.upper()}.pkl"
    key = _cache_key(df, settings)
    if path.exists():
        try:
            cached = pickle.loads(path.read_bytes())
            if cached.get("key") == key:
                return symbol, cached["events"]
        except Exception:
            pass

    enriched = indicators.enrich(df, settings.structure.atr_period)
    events = [
        Event(symbol, e.trigger_index, e.horizon, e.match)
        for e in backtest.iter_triggers(symbol, enriched, settings)
        if e.match.direction == "bullish"
    ]
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps({"key": key, "events": events}))
    return symbol, events


def collect_events(
    symbols: list[str], settings: Settings, universe: bool = False, workers: int = 2
) -> dict[str, list[Event]]:
    """Minden bikas kitores minden papiron (cache-elve, parhuzamosan)."""
    jobs = [(s, universe, settings.model_dump()) for s in symbols]
    out: dict[str, list[Event]] = {}
    if workers <= 1:
        results = map(_collect_one, jobs)
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        results = pool.map(_collect_one, jobs)
    for done, (symbol, events) in enumerate(results, 1):
        out[symbol] = events
        log.info("[%d/%d] %s: %d bikas kitores", done, len(jobs), symbol, len(events))
    if workers > 1:
        pool.shutdown()
    return out


# ---------------------------------------------------------------------------
# 1b. Idoponthelyes kalibracio + a pontszam tobbi resze (mint elesben)
# ---------------------------------------------------------------------------
def outcome_rows(symbol_events: list[Event], df: pd.DataFrame) -> dict[str, list[tuple]]:
    """Egy papir alakzat-kimenetelei a kalibraciohoz: {minta|irany: [(lezarult, R, talalt, lezart)]}."""
    rows: dict[str, list[tuple]] = {}
    for e in symbol_events:
        t = backtest._track(df, e.match, e.index, e.horizon)
        if t is None:
            continue
        risk = abs(t.trigger_price - t.invalidation)
        if risk <= 0:
            continue
        reward = abs(t.target - t.trigger_price) / risk
        r = reward if t.is_hit else (-1.0 if t.outcome == "invalidated" else 0.0)
        resolved = df.index[min(e.index + t.bars_to_outcome, len(df) - 1)]
        rows.setdefault(f"{t.name}|{t.direction}", []).append(
            (resolved, r, t.is_hit, t.outcome != "timeout"))
    return rows


class PointInTimeCalibration:
    """A pontszam "Historia" resze jovobe latas nelkul.

    Elesben a kalibracio (backtest) a teljes multbol keszul. A kutatasban ez
    jovobe latas lenne: egy 2018-as jelzes pontszama nem fugghet a 2023-as
    kimenetelektol. Ezert minden esemenyhez csak azokat az alakzat-kimeneteleket
    szamoljuk, amelyek a jelzes napja ELOTT mar lezarultak (cel / ervenytelenites /
    idokorlat) - ugyanazzal a kovetessel, mint a backtest (`backtest._track`).
    """

    def __init__(self, events: dict, frames: dict) -> None:
        rows: dict[str, list[tuple]] = {}
        for symbol, symbol_events in events.items():
            df = frames.get(symbol)
            if df is None:
                continue
            for key, items in outcome_rows(symbol_events, df).items():
                rows.setdefault(key, []).extend(items)
        self._build(rows)

    @classmethod
    def from_rows(cls, rows: dict[str, list[tuple]]) -> "PointInTimeCalibration":
        """Papironkent elore kiszamolt `outcome_rows` egyesitesebol (a labor igy dolgozik:
        nem kell minden papir arfolyama egyszerre a memoriaban)."""
        self = cls.__new__(cls)
        self._build(rows)
        return self

    def _build(self, rows: dict[str, list[tuple]]) -> None:
        self.tables = {}
        for key, items in rows.items():
            items.sort(key=lambda x: x[0])
            self.tables[key] = (
                np.array([x[0] for x in items], dtype="datetime64[ns]"),
                np.cumsum([x[1] for x in items]),
                np.cumsum([1 if x[2] else 0 for x in items]),
                np.cumsum([1 if x[3] else 0 for x in items]),
            )

    def calibration_for(self, as_of: pd.Timestamp, key: str) -> Calibration:
        table = self.tables.get(key)
        if table is None:
            return Calibration()
        dates, cum_r, cum_hit, cum_resolved = table
        n = int(np.searchsorted(dates, np.datetime64(as_of), side="left"))   # SZIGORUAN a jelzes elott
        if n == 0:
            return Calibration({"patterns": {key: {"samples": 0}}})
        resolved = int(cum_resolved[n - 1])
        return Calibration({"patterns": {key: {
            "samples": n,
            "hit_rate": (int(cum_hit[n - 1]) / resolved) if resolved else 0.0,
            "expectancy_r": float(cum_r[n - 1] / n),
        }}})


class ResearchContext:
    """A pontszam elo-szeru reszei: idoponthelyes kalibracio, relativ ero, szintek (cache-elve)."""

    def __init__(self, events: dict, frames: dict, settings: Settings) -> None:
        self.calibration = PointInTimeCalibration(events, frames)
        self.settings = settings
        self._levels: dict[tuple, tuple] = {}

    def levels(self, symbol: str, history: pd.DataFrame) -> tuple:
        key = (symbol, len(history))
        if key not in self._levels:
            from . import levels as levels_mod

            found = levels_mod.find_levels(history, self.settings.levels, self.settings.structure.atr_period)
            self._levels[key] = (levels_mod.nearest_support(found), levels_mod.nearest_resistance(found))
        return self._levels[key]


# ---------------------------------------------------------------------------
# 2. Valtozatok
# ---------------------------------------------------------------------------
@dataclass
class Variant:
    name: str
    entry_mode: str = "breakout"      # breakout | confirm | retest | both (=breakout+retest) | confirm+retest
    stop_atr: float = 1.5
    exit_mode: str = "target"         # target | partial | trail | partial_trail
    partial_r: float = 1.0
    trail_atr: float = 3.0
    regime: bool = False
    train_patterns: bool = False      # csak a tanito idoszakban pozitiv alakzatok
    earnings: bool = False            # earnings-tilalom a historikus jelentesi datumokkal
    overrides: dict = field(default_factory=dict)   # tovabbi EntrySettings mezok

    @classmethod
    def from_settings(cls, cfg: EntrySettings, name: str = "jelenlegi beallitas") -> "Variant":
        """Az ELO beallitasnak megfelelo valtozat (az entry-backtest ezt futtatja)."""
        mode = "confirm" if cfg.confirm_breakout else "breakout"
        if cfg.alert_retests:
            mode += "+retest"
        return cls(name, mode, cfg.min_stop_atr, cfg.exit_mode, cfg.partial_r, cfg.trail_atr,
                   regime=cfg.regime_filter, earnings=cfg.earnings_blackout_days > 0)

    def entry_settings(self, base: EntrySettings) -> EntrySettings:
        cfg = base.model_copy(deep=True)
        cfg.min_stop_atr = self.stop_atr
        cfg.regime_filter = self.regime
        # earnings-tilalom: csak ha a valtozat keri (a historikus datumokkal tesztelheto)
        cfg.earnings_blackout_days = base.earnings_blackout_days if self.earnings else 0
        for key, value in self.overrides.items():
            setattr(cfg, key, value)
        return cfg


@dataclass
class Result:
    symbol: str
    pattern: str
    date: pd.Timestamp
    kind: str
    r: float                          # a meretezessel sulyozva (alap kockazat = 1R, lasd boost_score)
    reason: str
    held: int
    score: float | None = None


# ---------------------------------------------------------------------------
# Kilepes szimulacioja
# ---------------------------------------------------------------------------
def simulate_exit(
    df: pd.DataFrame,
    entry_bar: int,
    entry_price: float,
    stop: float,
    target: float,
    horizon: int,
    atr: float,
    mode: str,
    partial_r: float = 1.0,
    trail_atr: float = 3.0,
) -> tuple[float, str, int]:
    """Visszaad: (R-ben mert eredmeny, kilepes oka, tartott gyertyak) - csuszas nelkul.

    A gyertyankenti logika a trade_mgmt.PositionState-ben van - ugyanaz, amit
    az elo pozicio-kovetes hasznal.
    """
    if entry_price - stop <= 0:
        return 0.0, "invalid", 0

    state = PositionState(entry_price, stop, stop, target, atr, mode, partial_r, trail_atr)
    last_bar = min(entry_bar + max_hold(horizon, mode) - 1, len(df) - 1)
    opens, highs, lows = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()

    for j in range(entry_bar, last_bar + 1):
        state.step(opens[j], highs[j], lows[j], first=j == entry_bar)
        if state.closed:
            break
    if not state.closed:
        state.timeout(float(df["close"].iloc[last_bar]))
    return float(state.realised_r), state.reason, state.bars_held


# ---------------------------------------------------------------------------
def _find_retest(match: PatternMatch, df: pd.DataFrame, i: int, cfg: EntrySettings) -> int | None:
    """Az elso visszateszt-nap a kitores (i) utan, vagy None."""
    level = match.trigger_level
    for k in range(i + 2, min(i + cfg.retest_max_bars, len(df) - 2) + 1):
        if df["close"].iloc[k] < level:       # hamis kitores - nincs visszateszt
            return None
        if entry._is_retest(match, df.iloc[: k + 1], cfg):
            return k
    return None


def _trade(
    event: Event,
    enriched: pd.DataFrame,
    signal_bar: int,
    kind: str,
    variant: Variant,
    cfg: EntrySettings,
    settings: Settings,
    bench: pd.DataFrame | None,
    earnings_dates: list | None = None,
    context: "ResearchContext | None" = None,
    regime_bench: pd.DataFrame | None = None,
) -> Result | None:
    """Egy jelzes a `signal_bar` zarasakor: szurok, terv, masnapi belepes, kilepes."""
    if signal_bar + 1 >= len(enriched):
        return None
    history = enriched.iloc[: signal_bar + 1]
    match = event.match
    check_kind = "breakout" if kind == "confirm" else kind

    plan = entry.build_plan(match, check_kind, history, cfg)
    if not plan.target > plan.entry > plan.stop:
        return None

    as_of = history.index[-1]
    # a papir sajat rezsimje (MarketContext.regime_frame: az indexe, vagy indexen kivul maga a papir)
    regime = (
        market.market_regime(regime_bench if regime_bench is not None else bench, as_of) if cfg.regime_filter
        else market.Regime(True, None, None, "")
    )
    earnings_days = (market.next_earnings_days(earnings_dates, as_of.date())
                     if cfg.earnings_blackout_days > 0 and earnings_dates else None)
    rel_strength = (market.relative_strength(history, bench, as_of)
                    if cfg.min_rel_strength is not None and bench is not None else None)
    checks = entry.run_checks(match, check_kind, history, plan, cfg, regime, earnings_days=earnings_days,
                              rel_strength=rel_strength)
    if any(c.passed is False for c in checks):
        return None
    if cfg.regime_filter and regime.ok is None:
        return None                      # benchmark nelkul a rezsim-valtozat nem ertelmes

    points = None
    if cfg.min_score > 0 or cfg.boost_score > 0:
        # ugyanugy, mint elesben: kalibracio (idoponthelyesen), relativ ero, szintek
        if context is not None:
            calibration = context.calibration.calibration_for(as_of, f"{match.name}|{match.direction}")
            rel = market.relative_strength(history, bench, as_of) if bench is not None else None
            support, resistance = context.levels(event.symbol, history)
        else:
            calibration, rel, support, resistance = Calibration(), None, None, None
        parts, _ = entry.score(match, check_kind, history, plan, settings, calibration,
                               rel, support, resistance)
        points = sum(p.points for p in parts)
        if points < cfg.min_score:
            return None

    # Belepes masnap nyitaskor, gap-limittel
    entry_bar = signal_bar + 1
    close = float(history["close"].iloc[-1])
    open_ = float(enriched["open"].iloc[entry_bar])
    if open_ > close * (1 + cfg.entry_max_gap_pct / 100) or open_ <= plan.stop:
        return None

    slip = cfg.slippage_bps / 10_000
    entry_price = open_ * (1 + slip)
    r, reason, held = simulate_exit(
        enriched, entry_bar, entry_price, plan.stop, plan.target, event.horizon,
        plan.atr, variant.exit_mode, variant.partial_r, variant.trail_atr,
    )
    # kilepesi csuszas R-ben
    r -= (entry_price * slip) / (entry_price - plan.stop)
    # eros jelzes nagyobb merettel: az eredmeny az ALAP kockazat egysegeben (1R = risk_per_trade_pct)
    r *= cfg.risk_mult(points)
    return Result(event.symbol, match.name, as_of, kind, r, reason, held, points)


def run_variant(
    variant: Variant,
    events: dict[str, list[Event]],
    frames: dict[str, pd.DataFrame],
    settings: Settings,
    bench: pd.DataFrame | None,
    whitelist: set[str] | None = None,
    earnings: dict[str, list] | None = None,
    context: "ResearchContext | None" = None,
) -> list[Result]:
    cfg = variant.entry_settings(settings.entry)
    results: list[Result] = []

    for symbol, symbol_events in events.items():
        df = frames.get(symbol)
        if df is None:
            continue
        # papironkenti rezsim (mint elesben), ha MarketContext jon; regi hivoknak egyetlen benchmark
        if isinstance(bench, market.MarketContext):
            sym_bench, regime_bench = bench.bench_for(symbol), bench.regime_frame(symbol, df)
        else:
            sym_bench = regime_bench = bench
        dates = (earnings or {}).get(symbol) if variant.earnings else None
        for event in symbol_events:
            if variant.train_patterns and whitelist is not None and event.match.name not in whitelist:
                continue
            i = event.index
            mode = variant.entry_mode
            primary = "confirm" if "confirm" in mode else (
                "breakout" if mode in ("breakout", "both") or "breakout" in mode else None)
            with_retest = mode == "both" or "retest" in mode

            trade = None
            if primary == "breakout":
                trade = _trade(event, df, i, "breakout", variant, cfg, settings, sym_bench, dates, context,
                               regime_bench)
            elif primary == "confirm" and i + 1 < len(df):
                if df["close"].iloc[i + 1] > event.match.trigger_level:
                    trade = _trade(event, df, i + 1, "confirm", variant, cfg, settings, sym_bench, dates, context,
                                   regime_bench)
            if trade is None and with_retest:
                k = _find_retest(event.match, df, i, cfg)
                if k is not None:
                    trade = _trade(event, df, k, "retest", variant, cfg, settings, sym_bench, dates, context,
                                   regime_bench)
            if trade is not None:
                results.append(trade)
    return results


# ---------------------------------------------------------------------------
# Statisztika
# ---------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """A nyeresi arany 95%-os konfidencia-intervalluma (Wilson) - kis mintan is jol viselkedik."""
    if n <= 0:
        return None, None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_ci(values, n_boot: int = 2000, seed: int = 7) -> tuple[float | None, float | None]:
    """Az atlag (varhato ertek, R) 95%-os bootstrap konfidencia-intervalluma. Determinisztikus."""
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return None, None
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, arr.size, size=(n_boot, arr.size))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def stats(results: list[Result]) -> dict:
    r = np.array([x.r for x in results], dtype=float)
    if r.size == 0:
        return {"n": 0, "win": None, "ev": None, "pf": None, "streak": 0, "dd": 0.0, "total": 0.0,
                "win_lo": None, "win_hi": None, "ev_lo": None, "ev_hi": None}
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    ordered = sorted(results, key=lambda x: x.date)
    equity = np.cumsum([x.r for x in ordered])
    drawdown = float((np.maximum.accumulate(np.r_[0, equity]) - np.r_[0, equity]).max())
    streak = worst = 0
    for x in ordered:
        streak = streak + 1 if x.r <= 0 else 0
        worst = max(worst, streak)
    win_lo, win_hi = wilson(int((r > 0).sum()), int(r.size))
    ev_lo, ev_hi = bootstrap_ci(r)
    return {
        "n": int(r.size),
        "win": float((r > 0).mean()),
        "win_lo": win_lo, "win_hi": win_hi,
        "ev": float(r.mean()),
        "ev_lo": ev_lo, "ev_hi": ev_hi,
        "pf": float(gains / losses) if losses > 0 else math.inf,
        "streak": worst,
        "dd": drawdown,
        "total": float(r.sum()),
    }


def split_stats(results: list[Result], include_holdout: bool = False) -> dict:
    """A lathato idoszakok statisztikaja. A holdout CSAK kifejezett keresre kerul bele."""
    visible = [x for x in results if x.date < HOLDOUT_START]
    out = {
        "all": stats(visible),
        "train": stats([x for x in visible if x.date < SPLIT_DATE]),
        "test": stats([x for x in visible if x.date >= SPLIT_DATE]),
        "years": yearly(visible),
    }
    if include_holdout:
        out["holdout"] = stats([x for x in results if x.date >= HOLDOUT_START])
    return out


def yearly(results: list[Result]) -> dict:
    """Gordulo (evenkenti) bontas: hany evben volt pozitiv a varhato ertek, melyik a legrosszabb ev."""
    by_year: dict[int, list[float]] = {}
    for x in results:
        by_year.setdefault(int(x.date.year), []).append(x.r)
    table = {y: {"n": len(v), "ev": float(np.mean(v))} for y, v in sorted(by_year.items())}
    if not table:
        return {"years": 0, "positive": 0, "worst": None, "worst_year": None, "by_year": {}}
    worst_year = min(table, key=lambda y: table[y]["ev"])
    return {"years": len(table), "positive": sum(v["ev"] > 0 for v in table.values()),
            "worst": table[worst_year]["ev"], "worst_year": worst_year, "by_year": table}


def log_holdout_opening(variants: list[str], symbols: int, study: str = "entry") -> None:
    """A zarolt vegso teszt megnyitasa nyomot hagy - ha sokszor nyitjuk meg, mar nem fuggetlen."""
    import json
    from datetime import datetime, timezone

    from .ledger import code_version

    HOLDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "holdout_start": f"{HOLDOUT_START:%Y-%m-%d}", "variants": variants[:20],
              "n_variants": len(variants), "symbols": symbols, "code": code_version(), "study": study}
    with HOLDOUT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def holdout_openings() -> int:
    return len(HOLDOUT_LOG.read_text(encoding="utf-8").splitlines()) if HOLDOUT_LOG.exists() else 0


def train_whitelist(results: list[Result], min_samples: int = 8) -> set[str]:
    """Azok az alakzatok, amelyek a TANITO idoszakban pozitiv varhato erteket hoztak."""
    by_pattern: dict[str, list[float]] = {}
    for x in results:
        if x.date < SPLIT_DATE:
            by_pattern.setdefault(x.pattern, []).append(x.r)
    return {p for p, rs in by_pattern.items() if len(rs) >= min_samples and np.mean(rs) > 0}


# ---------------------------------------------------------------------------
# Racs
# ---------------------------------------------------------------------------
def default_grid(with_regime: bool) -> list[Variant]:
    grid: list[Variant] = []
    for entry_mode in ("breakout", "both", "retest", "confirm"):
        for stop_atr in (1.5, 2.0, 2.5):
            for exit_mode in ("target", "partial", "trail", "partial_trail"):
                name = f"{entry_mode}/stop{stop_atr}/{exit_mode}"
                grid.append(Variant(name, entry_mode, stop_atr, exit_mode))
    # szurok a legigeretesebb alapokon (a riport ezeket kulon mutatja)
    extras: list[Variant] = []
    for base in list(grid):
        extras.append(replace(base, name=base.name + "/+alakzat", train_patterns=True))
        if with_regime:
            extras.append(replace(base, name=base.name + "/+QQQ", regime=True))
            extras.append(replace(base, name=base.name + "/+QQQ+alakzat",
                                  regime=True, train_patterns=True))
    return grid + extras


def study_grid(cfg: EntrySettings) -> list[Variant]:
    """Elore rogzitett, szakirodalmi alapu vizsgalat az ELO beallitashoz kepest (`--study`).

    Szandekosan rovid lista (a sok valtozat kozotti valasztas maga is tulillesztes):
    minden sor EGY szuro hozzaadasa / lazitasa az elo beallitashoz, plusz ket kombinacio.
      52 hetes csucs     George & Hwang (2004): a momentum-hozam nagy reszet ez magyarazza
      12-1 momentum      Jegadeesh & Titman (1993): 3-12 honapos folytatas
      relativ ero        a benchmarkot vero papirok (60 nap)
      osszehuzodas       VCP / squeeze - gyakorlati szabaly, tudomanyos bizonyitek nelkul
      volumen            Bulkowski: kicsi, vegyes hatas - ujramerve
      RSI-plafon         a tulvettseg kevesse elorejelzo - lazitva
      pontszam-kuszob    elorejelzo-e a pontszam?
    """
    live = Variant.from_settings(cfg)

    def v(name: str, **overrides) -> Variant:
        return replace(live, name=name, overrides=overrides)

    return [
        v("+52 hetes csucs >= 85%", min_high52_ratio=0.85),
        v("+52 hetes csucs >= 90%", min_high52_ratio=0.90),
        v("+52 hetes csucs >= 95%", min_high52_ratio=0.95),
        v("+momentum 12-1 > 0%", min_momentum_pct=0.0),
        v("+momentum 12-1 >= 20%", min_momentum_pct=20.0),
        v("+relativ ero > 0 (60n)", min_rel_strength=0.0),
        v("+osszehuzodas <= 0.80", max_contraction=0.80),
        v("+kitoresi volumen >= 1.5x", min_breakout_volume=1.5),
        v("RSI-plafon 80", rsi_max=80.0),
        v("pontszam >= 60", min_score=60.0),
        v("pontszam >= 70", min_score=70.0),
        v("+52h >= 90% +momentum > 0", min_high52_ratio=0.90, min_momentum_pct=0.0),
        v("+52h >= 90% +relativ ero > 0", min_high52_ratio=0.90, min_rel_strength=0.0),
    ]


def run(
    symbols: list[str],
    settings: Settings | None = None,
    universe: bool = False,
    workers: int = 2,
    grid: list[Variant] | None = None,
    holdout: bool = False,
) -> dict:
    settings = settings or load_settings()
    events = collect_events(symbols, settings, universe=universe, workers=workers)

    frames = {}
    for symbol in events:
        df = store.load(symbol, settings, universe=universe)
        if not df.empty:
            frames[symbol] = indicators.enrich(df, settings.structure.atr_period)

    # papironkenti rezsim, mint elesben (Nasdaq-100 -> QQQ, csak S&P 500 -> ^GSPC, egyik sem -> sajat trend)
    bench = market.load_context(settings, refresh=False, allow_network=False)
    if grid is None:
        # Az osszehasonlitas alapja (grid[0]) a TENYLEGESEN ELO beallitas - nem a racs elso eleme.
        live = Variant.from_settings(settings.entry, name="ELO beallitas")
        grid = [live] + default_grid(with_regime=bench.available)

    # historikus jelentesi datumok - csak ha valamelyik valtozat earnings-tilalommal fut
    earnings: dict[str, list] = {}
    if any(v.earnings for v in grid):
        hist = market.HistoricalEarnings(settings)
        earnings = {s: hist.dates(s) for s in frames}
        hist.save()

    # A baseline = grid[0] (alapbol az elo beallitas)
    context = ResearchContext(events, frames, settings)
    base_results = run_variant(grid[0], events, frames, settings, bench, earnings=earnings, context=context)
    whitelist = train_whitelist(
        run_variant(Variant("minden", "breakout", 1.5, "target"), events, frames, settings, bench)
    )

    rows = []
    for variant in grid:
        res = base_results if variant is grid[0] else run_variant(
            variant, events, frames, settings, bench, whitelist, earnings, context
        )
        rows.append({"variant": variant, "stats": split_stats(res, include_holdout=holdout), "results": res})
    if holdout:
        log_holdout_opening([r["variant"].name for r in rows], len(frames))

    return {
        "rows": rows,
        "symbols": len(frames),
        "events": sum(len(v) for v in events.values()),
        "whitelist": sorted(whitelist),
        "benchmark": bench.available,
        "holdout": holdout,
        "holdout_openings": holdout_openings(),
        "earnings_coverage": {"symbols": sum(1 for v in earnings.values() if v), "of": len(frames)}
        if earnings else None,
    }


def worst_period_ev(stats: dict) -> float | None:
    train, test = stats["train"]["ev"], stats["test"]["ev"]
    if train is None or test is None:
        return None
    return min(train, test)


def robust_winners(rows: list[dict], baseline: dict, min_period_n: int = 15) -> list[dict]:
    """Robusztus javulasok, nyeresi arany szerint csokkenoen.

    Feltetel: mindket idoszakban eleg trade, a ROSSZABBIK idoszak varhato
    erteke legalabb akkora, mint a jelenlegi beallitase a rosszabbik idoszakban,
    ES a varhato ertek 95%-os konfidencia-intervallumanak also hatara sem
    rosszabb a jelenlegi beallitasenal (kis mintan a pontbecsles felrevezeto).
    (A "mindket idoszakban verje" feltetel tul szigoru, ha a baseline egyik
    idoszaka nehany kiugro trade-bol el - ez itt pontosan igy van.)
    """
    floor = worst_period_ev(baseline)
    out = []
    for row in rows:
        s = row["stats"]
        if s["train"]["n"] < min_period_n or s["test"]["n"] < min_period_n:
            continue
        worst = worst_period_ev(s)
        if worst is None or floor is None or worst < floor:
            continue
        lo, base_lo = s["all"].get("ev_lo"), baseline["all"].get("ev_lo")
        if lo is not None and base_lo is not None and lo < base_lo:
            continue
        out.append(row)
    return sorted(out, key=lambda r: (r["stats"]["all"]["win"], worst_period_ev(r["stats"])), reverse=True)
