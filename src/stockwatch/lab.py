"""Strategia-labor: belepesi strategiak folyamatos tesztelese multbeli adatokon.

    python cli.py lab            hatterfolyamat (szerveren: stockwatch-lab szolgaltatas)
    python cli.py lab --once     egy teljes kor, utana kilep
    python cli.py lab status     allapot

A webes felulet "Labor" fulen uj kiserlet veheto fel; a hatterfolyamat korrol korre:

  1. ADAT        a hianyzo / elavult papirok letoltese (data/universe) - a monitor zaras
                 utani indexszkennelesenek idoablakaban nem nyul hozza
  2. KITORESEK   papironkent EGYSZER kigyujti a bikas kitoreseket, utana naponta csak az uj
                 gyertyakat (data/lab/events). Ha egy osztalek atirja a multbeli arakat
                 (korrigalt arak), az adott papirt ujraszamolja.
  3. ERTEKELES   minden friss adatra vagy uj kiserletre vegigjatssza a kiserleteket
                 (entry_research szimulacioja: masnapi nyitas, gap-limit, csuszas, pesszimista
                 kilepes), papironkent - egyszerre csak egy papir arfolyama van a memoriaban
  4. GEPI TANULAS  lasd lab_ml.py

BECSULETESSEG
-------------
  * tanito (<2022) es validacios (2022 - 2025-03) idoszak latszik
  * a ZAROLT holdout (2025-04 -> a kiserlet letrehozasa) rejtve marad; kiserletenkent
    kifejezett keresre nyithato meg, naplozva (data/research/holdout_log.jsonl)
  * "elo": a kiserlet LETREHOZASA UTANI uj adatokon mert eredmeny - a strategia kitalalasakor
    meg nem letezett, ez valodi mintan kivuli teszt
  * a valaha lefuttatott kiserletek szama latszik: sok kiserlet kozul a legjobb reszben szerencse

ERONFORRAS
----------
A szolgaltatas a legalacsonyabb prioritassal fut (nice 19, idle IO, memoriaplafon): a monitor
es a felulet mindig elsobbseget kap. Lemez: MAX_EXPERIMENTS kiserlet, kiserletenkent
legfeljebb MAX_TRADES trade; a mar nem hasznalt papirok cache-e 30 nap utan torlodik.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import pickle
import socket
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import backtest, feed, indicators, market, sessions, store
from . import entry_research as er
from .config import CONFIG_DIR, DATA_DIR, EntrySettings, Settings, load_settings, load_watchlist, yaml_tickers
from .locks import file_lock, write_atomic

log = logging.getLogger(__name__)

LAB_DIR = DATA_DIR / "lab"
CACHE_VERSION = 1
MAX_EXPERIMENTS = 60
MAX_TRADES = 20_000
IDLE_SLEEP = 600                 # mp: ha nincs teendo
STATUS_EVERY = 5.0               # mp: allapotfajl irasa legfeljebb ilyen surun
WORKER_STALE = 45 * 60           # mp: ennyi ota nem jelentkezett -> "nem fut"
PRUNE_DAYS = 30
ANCHOR_TOLERANCE = 1e-4          # relativ: ennel nagyobb elteres = atirt multbeli ar
MARKET_TZ = "America/New_York"
BUSY_WINDOW = ((15, 30), (20, 0))   # NY: a monitor zaras utani indexszkennelese - adatletoltes nincs
_download_attempts: dict[str, float] = {}     # papir -> utolso letoltesi probalkozas (folyamaton belul)

UNIVERSES = {
    "indexes": "Nasdaq-100 + S&P 500",
    "research": "Bovitett kutatasi minta (config/universe.yaml)",
    "watchlist": "Watchlist",
    "all": "Mind",
}
# a feluletrol allithato belepesi beallitasok: mezo -> (tipus, min, max)
EDITABLE = {
    "confirm_breakout": (bool, None, None),
    "alert_retests": (bool, None, None),
    "min_stop_atr": (float, 0.5, 5.0),
    "exit_mode": (str, None, None),
    "partial_r": (float, 0.3, 3.0),
    "trail_atr": (float, 1.0, 6.0),
    "min_risk_reward": (float, 0.5, 5.0),
    "rsi_min": (float, 0.0, 100.0),
    "rsi_max": (float, 0.0, 100.0),
    "max_extension_atr": (float, 0.1, 10.0),
    "max_stop_pct": (float, 1.0, 50.0),
    "min_score": (float, 0.0, 100.0),
    "min_breakout_volume": (float, 0.0, 5.0),
    "regime_filter": (bool, None, None),
    "earnings_blackout_days": (int, 0, 30),
    "min_high52_ratio": (float, 0.0, 1.0),
    "min_momentum_pct": (float, -100.0, 500.0),
    "min_rel_strength": (float, -100.0, 100.0),
    "max_contraction": (float, 0.0, 3.0),
    "boost_score": (float, 0.0, 100.0),
    "boost_risk_mult": (float, 1.0, 3.0),
}


# ---------------------------------------------------------------------------
# Utvonalak (a tesztek a LAB_DIR-t atallitjak)
# ---------------------------------------------------------------------------
def _p(*parts: str):
    return LAB_DIR.joinpath(*parts)


def experiments_path():
    return _p("experiments.json")


def status_path():
    return _p("status.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Univerzumok es adat
# ---------------------------------------------------------------------------
def universe_symbols(name: str, settings: Settings) -> list[str]:
    from . import indexes

    if name == "watchlist":
        return list(load_watchlist().symbols)
    if name == "research":
        import yaml

        path = CONFIG_DIR / "universe.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        return yaml_tickers((raw or {}).get("symbols", []))
    if name == "indexes":
        members = indexes.IndexMembers(settings)
        return list(dict.fromkeys(members.members("nasdaq100", allow_network=False)
                                  + members.members("sp500", allow_network=False)))
    if name == "all":
        out: list[str] = []
        for other in ("indexes", "research", "watchlist"):
            out += universe_symbols(other, settings)
        return list(dict.fromkeys(out))
    raise ValueError(f"Ismeretlen univerzum: {name}")


def _raw(symbol: str, settings: Settings) -> pd.DataFrame:
    df = store.load(symbol, settings, universe=True)
    return df if not df.empty else store.load(symbol, settings)


def load_frame(symbol: str, settings: Settings) -> pd.DataFrame | None:
    # csak LEZART gyertya: a napkozben irt, meg alakulo mai gyertya kesobb atirodik, es a
    # kitoreseket is a vegleges zaron kell keresni
    df = sessions.completed_bars(_raw(symbol, settings))
    if df.empty or len(df) < 320:
        return None
    return indicators.enrich(df, settings.structure.atr_period)


def _busy_window(now: pd.Timestamp | None = None) -> bool:
    now = now or pd.Timestamp.now(tz=MARKET_TZ)
    (h0, m0), (h1, m1) = BUSY_WINDOW
    return now.weekday() < 5 and (h0, m0) <= (now.hour, now.minute) < (h1, m1)


_last_dates: dict[tuple, tuple] = {}     # (papir, univerzum?) -> (fajl mtime, utolso nap)


def data_last_date(symbol: str, settings: Settings) -> pd.Timestamp | None:
    """Az adat utolso napja - a fajl modositasi idejevel memoizalva (a pihenokor ne olvasson be
    minden korben ~600 tomoritett fajlt)."""
    for universe in (True, False):
        path = store._path_for(symbol, settings, universe)
        if not path.exists():
            continue
        mtime = path.stat().st_mtime
        memo = _last_dates.get((symbol, universe))
        if not memo or memo[0] != mtime:
            df = store.load(symbol, settings, universe=universe)
            tail = [pd.Timestamp(d) for d in df.index[-2:]]
            memo = (mtime, tail)
            _last_dates[(symbol, universe)] = memo
        tail = memo[1]
        # a meg alakulo mai gyertya nem szamit (lasd load_frame)
        if tail and not sessions.session_closed(tail[-1]):
            tail = tail[:-1]
        return tail[-1] if tail else None
    return None


def stale_data(symbols: list[str], settings: Settings, days: int = 4) -> list[str]:
    """Hianyzo vagy `days` naptari napnal regebbi adat (amit a monitor nem frissit)."""
    limit = pd.Timestamp.now().normalize() - pd.Timedelta(days=days)
    return [s for s in symbols if (last := data_last_date(s, settings)) is None or last < limit]


# ---------------------------------------------------------------------------
# Kitoresek: novekmenyes cache papironkent
# ---------------------------------------------------------------------------
def settings_key(settings: Settings) -> str:
    raw = (f"{CACHE_VERSION}|{settings.patterns.model_dump_json()}|{settings.structure.model_dump_json()}|"
           f"{settings.backtest.model_dump_json()}")
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cache_path(symbol: str):
    return _p("events", f"{symbol.upper()}.pkl")


def load_cache(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        return pickle.loads(path.read_bytes())
    except Exception:
        return None


def collect_symbol(symbol: str, settings_raw: dict, lab_dir: str | None = None) -> tuple[str, str, int]:
    """Egy papir kitoreseinek gyujtese (folytatva, ha lehet). (papir, mi tortent, kitoresek szama).

    Modul-szintu fuggveny, hogy tobb processzmagon is futhasson (helyi gepen --workers); a
    gyerekfolyamat a `lab_dir`-bol tudja, hova irjon (a modul-szintu beallitast nem orokli)."""
    global LAB_DIR
    if lab_dir:
        from pathlib import Path

        LAB_DIR = Path(lab_dir)
    settings = Settings(**settings_raw)
    try:
        return _collect(symbol, settings)
    except Exception as exc:
        if isinstance(exc, ValueError):          # vart eset (pl. tul rovid historia) - nem kell traceback
            log.warning("Labor: %s kimarad: %s", symbol, exc)
        else:
            log.exception("Labor: %s kitoreseinek gyujtese hibara futott - kimarad", symbol)
        # jelolo cache: csak uj adat erkezesekor probalja ujra (ne porogjon minden korben)
        last = data_last_date(symbol, settings)
        if last is not None:
            _write_cache(symbol, {"key": settings_key(settings), "last_date": pd.Timestamp(last),
                                  "anchor": float("nan"), "seen": [], "events": [], "outcomes": {},
                                  "error": f"{type(exc).__name__}: {exc}"[:200]})
        return symbol, "hiba", 0


def _write_cache(symbol: str, payload: dict) -> None:
    path = _cache_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(pickle.dumps(payload))
    os.replace(tmp, path)


def _same(value: float, ref: float) -> bool:
    return ref != 0 and abs(value / ref - 1) < ANCHOR_TOLERANCE


def history_intact(cache: dict, df: pd.DataFrame, last: pd.Timestamp) -> bool:
    """Valtozatlan-e a historia a `last` nap ELOTT - azaz csak az utolso gyertya irodott at
    (pl. napkozben mentett, meg alakulo gyertya), nem osztalek-korrekcio (ami minden multbeli
    arat atir). Ellenorzes: a regebbi horgony (`anchor_prev`), vagy a korabbi kitoresek
    pivot-arai a mostani sorozatban (min. 3 egyezo pont, egy elteres sem)."""
    prev = cache.get("anchor_prev")
    if prev:
        day, close = pd.Timestamp(prev[0]), prev[1]
        return day in df.index and _same(float(df["close"].loc[day]), close)
    checked = 0
    for _, _, match in cache.get("events", [])[-5:]:
        for day, price in match.key_points:
            day = pd.Timestamp(day)
            if day >= last or day not in df.index:
                continue
            row = df.loc[day]
            if not any(_same(float(row[k]), price) for k in ("open", "high", "low", "close")):
                return False
            checked += 1
    return checked >= 3


def _collect(symbol: str, settings: Settings) -> tuple[str, str, int]:
    df = load_frame(symbol, settings)
    if df is None:
        raise ValueError("nincs eleg adat (legalabb 320 gyertya kell)")
    cache = load_cache(symbol)
    key = settings_key(settings)
    begin, seen, kept = None, set(), []
    status = "uj"
    if cache and cache.get("key") == key:
        last = pd.Timestamp(cache["last_date"])
        if last in df.index and _same(float(df["close"].loc[last]), cache["anchor"]):
            if df.index[-1] <= last:
                return symbol, "valtozatlan", len(cache["events"])
            begin = int(df.index.get_loc(last)) + 1
            seen = set(cache["seen"])
            kept = cache["events"]
            status = "frissitve"
        elif last in df.index and history_intact(cache, df, last):
            # csak az utolso gyertya valtozott: azt a napot ujra, a ra eso kitoresek nelkul
            # (nem kell a teljes 10 evet ujraszamolni)
            begin = int(df.index.get_loc(last))
            kept = [e for e in cache["events"] if pd.Timestamp(e[0]) < last]
            dropped = {e[2].fingerprint() for e in cache["events"] if pd.Timestamp(e[0]) >= last}
            seen = set(cache["seen"]) - dropped
            status = "utolso nap ujra"

    new = [(e.match.trigger_date, e.horizon, e.match)
           for e in backtest.iter_triggers(symbol, df, settings, begin=begin, seen=seen)
           if e.match.direction == "bullish"]
    events = kept + new
    outcomes = er.outcome_rows(to_events(symbol, events, df), df)
    back = df.index[max(len(df) - 21, 0)]          # regebbi horgony: a kovetkezo javitashoz (history_intact)
    _write_cache(symbol, {"key": key, "last_date": df.index[-1], "anchor": float(df["close"].iloc[-1]),
                          "anchor_prev": [back, float(df["close"].loc[back])],
                          "seen": sorted(seen), "events": events, "outcomes": outcomes})
    return symbol, status, len(events)


def to_events(symbol: str, stored: list, df: pd.DataFrame) -> list[er.Event]:
    """(kitoresi nap, horizont, minta) -> er.Event a MOSTANI sorozat pozicioival (a retention miatt
    az eleje levagodhat, ezert datum szerint tarolunk)."""
    out = []
    for trigger_date, horizon, match in stored:
        if trigger_date in df.index:
            out.append(er.Event(symbol, int(df.index.get_loc(trigger_date)), horizon, match))
    return out


def needs_collection(symbol: str, settings: Settings, key: str) -> bool:
    cache = load_cache(symbol)
    if cache is None or cache.get("key") != key:
        return True
    last = data_last_date(symbol, settings)
    return last is not None and last > pd.Timestamp(cache["last_date"])


# ---------------------------------------------------------------------------
# Kiserletek
# ---------------------------------------------------------------------------
def _empty_store() -> dict:
    return {"experiments": [], "created_total": 0, "ml": {"enabled": True, "pending": True}}


def load_store() -> dict:
    path = experiments_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**_empty_store(), **data}
    except json.JSONDecodeError:
        log.warning("Serult lab/experiments.json - uresen indulunk.")
        return _empty_store()


def save_store(data: dict) -> None:
    write_atomic(experiments_path(), json.dumps(data, indent=1, ensure_ascii=False, default=str))


def validate_entry(values: dict, base: EntrySettings) -> EntrySettings:
    """A feluletrol erkezo beallitasok ellenorzese: csak az EDITABLE mezok, tartomanyon belul."""
    update = {}
    for key, raw in (values or {}).items():
        if key not in EDITABLE:
            raise ValueError(f"Nem allithato mezo: {key}")
        kind, lo, hi = EDITABLE[key]
        if raw is None or raw == "":
            if key in ("min_momentum_pct", "min_rel_strength"):
                update[key] = None
                continue
            raise ValueError(f"Hianyzo ertek: {key}")
        value = (str(raw).lower() in ("1", "true", "on", "yes")) if kind is bool and not isinstance(raw, bool) \
            else kind(raw)
        if lo is not None and not lo <= value <= hi:
            raise ValueError(f"{key}: {value} - megengedett {lo} - {hi}")
        update[key] = value
    cfg = base.model_copy(update=update)
    EntrySettings(**cfg.model_dump())          # a pydantic validatorok (pl. exit_mode)
    if cfg.rsi_min >= cfg.rsi_max:
        raise ValueError("Az RSI minimum legyen kisebb a maximumnal.")
    return cfg


def add_experiment(name: str, universe: str, entry: EntrySettings, live: bool = False,
                   data: dict | None = None) -> dict:
    """Uj kiserlet a sorba. `data`: mar zarolt, betoltott tar (ilyenkor a hivo ment)."""
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Adj nevet a kiserletnek.")
    if universe not in UNIVERSES:
        raise ValueError(f"Ismeretlen univerzum: {universe}")
    own = data is None
    if own:
        lock = file_lock(experiments_path())
        lock.__enter__()
        data = load_store()
    try:
        if len(data["experiments"]) >= MAX_EXPERIMENTS:
            raise ValueError(f"Legfeljebb {MAX_EXPERIMENTS} kiserlet tarolhato - torolj egy regebbit.")
        exp = {"id": uuid.uuid4().hex[:10], "name": name, "created": _now(), "universe": universe,
               "entry": entry.model_dump(), "live": live, "status": "queued", "error": None,
               "updated": None, "evaluated": None, "holdout_opened": None, "summary": None}
        data["experiments"].append(exp)
        data["created_total"] = int(data.get("created_total", 0)) + 1
        if own:
            save_store(data)
        return exp
    finally:
        if own:
            lock.__exit__(None, None, None)


def update_store(mutate) -> dict:
    """Zarolt olvas-modosit-ir: `mutate(data)` helyben modosit."""
    with file_lock(experiments_path()):
        data = load_store()
        mutate(data)
        save_store(data)
        return data


def seed(settings: Settings) -> None:
    """Elso indulaskor: az ELO beallitas + az elore rogzitett vizsgalat (study_grid) az indexeken."""
    def mutate(data: dict) -> None:
        if data["experiments"]:
            return
        add_experiment("ELO beallitas", "indexes", settings.entry, live=True, data=data)
        for variant in er.study_grid(settings.entry):
            add_experiment(variant.name, "indexes", settings.entry.model_copy(update=variant.overrides),
                           data=data)
    update_store(mutate)


def experiment_entry(exp: dict, settings: Settings) -> EntrySettings:
    """Az ELO kiserlet mindig a mostani beallitast hasznalja; a tobbi a letrehozaskori pillanatkepet."""
    return settings.entry if exp.get("live") else EntrySettings(**exp["entry"])


def results_path(exp_id: str):
    return _p("results", f"{exp_id}.json.gz")


def save_results(exp_id: str, results: list[er.Result]) -> None:
    rows = [[f"{x.date:%Y-%m-%d}", x.symbol, x.pattern, x.kind, round(float(x.r), 4), x.reason, int(x.held)]
            for x in sorted(results, key=lambda x: x.date)[-MAX_TRADES:]]
    path = results_path(exp_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(rows).encode("utf-8")))
    os.replace(tmp, path)


def load_results(exp_id: str) -> list[er.Result]:
    path = results_path(exp_id)
    if not path.exists():
        return []
    rows = json.loads(gzip.decompress(path.read_bytes()))
    return [er.Result(symbol, pattern, pd.Timestamp(d), kind, r, reason, held)
            for d, symbol, pattern, kind, r, reason, held in rows]


def summarize(results: list[er.Result], created: str) -> dict:
    """A latszo idoszakok + az "elo" (letrehozas utani) + a holdout (rejtve tarolva) statisztikaja."""
    created_ts = pd.Timestamp(created).tz_localize(None).normalize()
    visible = er.split_stats(results)
    by_pattern: dict[str, list[float]] = {}
    for x in results:
        if x.date < er.HOLDOUT_START:
            by_pattern.setdefault(x.pattern, []).append(x.r)
    return {
        **visible,
        "forward": er.stats([x for x in results if x.date >= created_ts]),
        "holdout": er.stats([x for x in results if er.HOLDOUT_START <= x.date < created_ts]),
        "patterns": {k: {"n": len(v), "win": float(np.mean(np.array(v) > 0)), "ev": float(np.mean(v))}
                     for k, v in sorted(by_pattern.items(), key=lambda kv: -len(kv[1]))},
    }


# ---------------------------------------------------------------------------
# Ertekeles - papironkent (memoriakimelo)
# ---------------------------------------------------------------------------
class LabContext:
    """Az er.ResearchContext megfeleloje: a kalibracio az OSSZES papir elore mentett kimeneteleibol,
    a szintek papironkent (a kiserletek kozott megosztva)."""

    def __init__(self, calibration: er.PointInTimeCalibration, settings: Settings) -> None:
        self.calibration = calibration
        self.settings = settings
        self._levels: dict[tuple, tuple] = {}

    def levels(self, symbol: str, history: pd.DataFrame) -> tuple:
        key = (symbol, len(history))
        if key not in self._levels:
            from . import levels as levels_mod

            found = levels_mod.find_levels(history, self.settings.levels, self.settings.structure.atr_period)
            self._levels[key] = (levels_mod.nearest_support(found), levels_mod.nearest_resistance(found))
        return self._levels[key]

    def next_symbol(self) -> None:
        self._levels.clear()


def calibration_from_caches(symbols: list[str]) -> er.PointInTimeCalibration:
    rows: dict[str, list[tuple]] = {}
    for symbol in symbols:
        cache = load_cache(symbol)
        for key, items in ((cache or {}).get("outcomes") or {}).items():
            rows.setdefault(key, []).extend(items)
    return er.PointInTimeCalibration.from_rows(rows)


def evaluate(experiments: list[dict], symbols: list[str], settings: Settings, progress=None,
             symbol_hook=None) -> dict[str, list[er.Result]]:
    """Az osszes megadott kiserlet egyetlen papironkenti korben. `symbol_hook(symbol, events, frame)`:
    ugyanabban a korben tovabbi feldolgozas (gepi tanulas adatsorai)."""
    universes = {exp["id"]: set(universe_symbols(exp["universe"], settings)) for exp in experiments}
    variants = {}
    for exp in experiments:
        cfg = experiment_entry(exp, settings)
        variants[exp["id"]] = (er.Variant.from_settings(cfg, name=exp["name"]), cfg)
    context = LabContext(calibration_from_caches(symbols), settings)
    bench = market.load_context(settings, refresh=False, allow_network=False)     # papironkenti rezsim
    hist = market.HistoricalEarnings(settings) if any(cfg.earnings_blackout_days > 0
                                                      for _, cfg in variants.values()) else None

    acc: dict[str, list[er.Result]] = {exp["id"]: [] for exp in experiments}
    for n, symbol in enumerate(symbols, 1):
        if progress:
            progress(n, len(symbols), symbol)
        try:
            cache = load_cache(symbol)
            frame = load_frame(symbol, settings) if cache else None
            if frame is None:
                continue
            events = to_events(symbol, cache["events"], frame)
            context.next_symbol()
            dates = {symbol: hist.dates(symbol)} if hist is not None else {}
            for exp in experiments:
                if symbol not in universes[exp["id"]]:
                    continue
                variant, cfg = variants[exp["id"]]
                local = settings.model_copy(update={"entry": cfg})
                acc[exp["id"]] += er.run_variant(variant, {symbol: events}, {symbol: frame}, local, bench,
                                                 earnings=dates, context=context)
            if symbol_hook:
                symbol_hook(symbol, events, frame, bench.bench_for(symbol))
        except Exception:
            log.exception("Labor: %s ertekelese hibara futott - kimarad", symbol)
    if hist is not None:
        hist.save()
    return acc


# ---------------------------------------------------------------------------
# Allapot
# ---------------------------------------------------------------------------
class Status:
    def __init__(self) -> None:
        self.data = {"pid": os.getpid(), "host": socket.gethostname(), "started": _now(),
                     "phase": "indul", "detail": "", "progress": None, "last_pass": None,
                     "coverage": None, "heartbeat": _now()}
        self._written = 0.0
        if status_path().exists():
            try:
                old = json.loads(status_path().read_text(encoding="utf-8"))
                self.data["last_pass"] = old.get("last_pass")
                self.data["coverage"] = old.get("coverage")
            except (json.JSONDecodeError, OSError):
                pass

    def set(self, phase: str, detail: str = "", progress: tuple | None = None, force: bool = False) -> None:
        self.data.update(phase=phase, detail=detail, progress=list(progress) if progress else None,
                         heartbeat=_now())
        if force or time.monotonic() - self._written >= STATUS_EVERY:
            self.write()

    def write(self) -> None:
        write_atomic(status_path(), json.dumps(self.data, ensure_ascii=False, default=str))
        self._written = time.monotonic()


def read_status() -> dict:
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "phase": "nem fut", "detail": "A labor meg nem futott."}
    beat = pd.Timestamp(data.get("heartbeat"))
    age = (pd.Timestamp.now(tz="UTC") - beat).total_seconds() if beat is not pd.NaT else 1e9
    data["running"] = age < WORKER_STALE
    data["age_s"] = age
    return data


# ---------------------------------------------------------------------------
# Egy kor
# ---------------------------------------------------------------------------
def _events_version(symbols: list[str]) -> str:
    """Valtozik, ha barmelyik papir kitores-cache-e frissult (uj nap / ujraszamolas)."""
    digest = hashlib.sha1()
    for symbol in symbols:
        cache = load_cache(symbol)
        if cache:
            digest.update(f"{symbol}|{cache['last_date']}|{len(cache['events'])}".encode())
    return digest.hexdigest()[:16]


def run_once(settings: Settings | None = None, workers: int = 1, status: Status | None = None) -> bool:
    """Egy teljes kor. Igaz, ha volt teendo."""
    from . import lab_ml

    settings = settings or load_settings()
    status = status or Status()
    seed(settings)
    data = load_store()
    experiments = data["experiments"]
    symbols = list(dict.fromkeys(s for name in {e["universe"] for e in experiments} | {lab_ml.UNIVERSE}
                                 for s in universe_symbols(name, settings)))
    did = False

    # 1. adat: csak ami hianyzik vagy regota nem frissult (a monitor indexszkennelese mellett ritka);
    #    papironkent legfeljebb naponta egy probalkozas (a kivezetett papirokat ne kerje 10 percenkent)
    now = time.time()
    missing = [s for s in stale_data(symbols, settings) if now - _download_attempts.get(s, 0) > 86400]
    if missing and not _busy_window():
        for start in range(0, len(missing), 25):
            if _busy_window():
                break
            batch = missing[start:start + 25]
            _download_attempts.update({s: now for s in batch})
            status.set("adat", f"letoltes: {', '.join(batch[:3])}...", (start, len(missing)), force=True)
            try:
                got = feed.update(batch, settings, universe=True)
                did = did or any(not df.empty for df in got.values())
            except Exception:
                log.exception("Labor: letoltes hibara futott")

    # 2. kitoresek (novekmenyesen)
    key = settings_key(settings)
    todo = [s for s in symbols if needs_collection(s, settings, key)]
    if todo:
        raw = settings.model_dump()
        done = 0
        if workers > 1:
            pool = ProcessPoolExecutor(max_workers=workers)
            outcomes = pool.map(collect_symbol, todo, [raw] * len(todo), [str(LAB_DIR)] * len(todo))
        else:
            pool, outcomes = None, (collect_symbol(s, raw) for s in todo)
        for symbol, what, n in outcomes:
            done += 1
            did = did or what in ("uj", "frissitve", "utolso nap ujra")
            status.set("kitoresek", f"{symbol}: {what} ({n})", (done, len(todo)))
        if pool is not None:
            pool.shutdown()
    covered = sum(1 for s in symbols if load_cache(s))
    status.data["coverage"] = {"symbols": len(symbols), "with_events": covered,
                               "with_data": len(symbols) - len(stale_data(symbols, settings, days=30))}

    # 3. ertekeles: az uj / sorban allo kiserletek, es mindegyik, ha friss adat jott
    version = _events_version(symbols)
    live_entry = settings.entry.model_dump()
    due = [e for e in experiments
           if e["status"] in ("queued", "error") or e.get("evaluated") != version
           or (e.get("live") and e.get("entry") != live_entry)]
    ml_due = lab_ml.due(data, version)
    if due or ml_due:
        did = True
        ids = {e["id"] for e in due}
        update_store(lambda d: [x.update(status="running") for x in d["experiments"] if x["id"] in ids])
        collector = lab_ml.RowCollector(settings) if ml_due else None
        eval_symbols = list(dict.fromkeys(
            [s for e in due for s in universe_symbols(e["universe"], settings)]
            + (universe_symbols(lab_ml.UNIVERSE, settings) if ml_due else [])))
        started = time.monotonic()
        acc = evaluate(due, eval_symbols, settings,
                       progress=lambda n, total, sym: status.set("ertekeles", f"{len(due)} kiserlet - {sym}",
                                                                 (n, total)),
                       symbol_hook=collector.add if collector else None)

        def store_results(d: dict) -> None:
            for exp in d["experiments"]:
                if exp["id"] not in acc:
                    continue
                results = acc[exp["id"]]
                save_results(exp["id"], results)
                exp.update(status="done", error=None, updated=_now(), evaluated=version,
                           summary=summarize(results, exp["created"]))
                if exp.get("live"):
                    exp["entry"] = live_entry
        update_store(store_results)

        # 4. gepi tanulas (ugyanannak a kornek az adatsoraival)
        if collector is not None:
            status.set("gepi tanulas", f"{len(collector.rows)} minta, evenkenti tanitas", force=True)
            live = next((e for e in load_store()["experiments"] if e.get("live")), None)
            live_keys = {(x.symbol, x.date, x.pattern) for x in load_results(live["id"])} if live else set()
            try:
                lab_ml.train_and_report(collector.rows, live_keys, version, settings.entry.model_dump())
            except Exception:
                log.exception("Labor: a gepi tanulas hibara futott")
            update_store(lambda d: d["ml"].update(pending=False, version=version, trained=_now()))
        status.data["last_pass"] = {"finished": _now(), "seconds": round(time.monotonic() - started),
                                    "experiments": len(due), "symbols": len(eval_symbols)}
    # 5. arnyek-uzem: a naplozott jelzesek kimenetele (ha mar lefutottak)
    try:
        from . import shadow

        if shadow.resolve(settings):
            did = True
    except Exception:
        log.exception("Labor: az arnyek-jelzesek kiertekelese hibara futott")
    prune()
    status.set("var", "nincs teendo - a kovetkezo ellenorzes 10 perc mulva" if not did else "kor kesz",
               force=True)
    return did


def run_forever(workers: int = 1) -> None:
    status = Status()
    status.set("indul", force=True)
    log.info("Strategia-labor elindult (pid %s).", os.getpid())
    while True:
        try:
            from . import config

            config.load_settings.cache_clear()
            config.load_watchlist.cache_clear()
            from . import meanrev_research

            # a homokozo visszahuzas-backtestje elsobbseget kap (a felulet kerte, valaszt var)
            did = meanrev_research.run_sandbox_request(load_settings())
            did = run_once(load_settings(), workers=workers, status=status) or did
        except Exception:
            log.exception("Strategia-labor: a kor hibara futott")
            status.set("hiba", "lasd a naplot", force=True)
            did = False
        if not did:
            # varakozas kozben is jelentkezik, hogy a felulet lassa: fut
            for _ in range(IDLE_SLEEP // 60):
                time.sleep(60)
                status.set("var", "nincs teendo", force=True)
                from . import meanrev_research

                store = load_store()
                if (store["ml"].get("pending") or any(e["status"] == "queued" for e in store["experiments"])
                        or meanrev_research.SANDBOX_REQUEST.exists()):
                    break


# ---------------------------------------------------------------------------
# Takaritas (lemezplafon)
# ---------------------------------------------------------------------------
def prune(settings: Settings | None = None) -> dict:
    """A torolt kiserletek eredmenyei, es a 30 napja nem hasznalt papirok cache-e."""
    settings = settings or load_settings()
    data = load_store()
    ids = {e["id"] for e in data["experiments"]}
    removed = 0
    for path in (_p("results").glob("*.json.gz") if _p("results").exists() else []):
        if path.name.split(".")[0] not in ids:
            path.unlink(missing_ok=True)
            removed += 1
    try:
        wanted = set()
        for name in {e["universe"] for e in data["experiments"]} | {"all"}:
            wanted |= set(universe_symbols(name, settings))
    except Exception:
        return {"results": removed, "events": 0}
    limit = time.time() - PRUNE_DAYS * 86400
    dropped = 0
    for path in (_p("events").glob("*.pkl") if _p("events").exists() else []):
        if path.stem.upper() not in wanted and path.stat().st_mtime < limit:
            path.unlink(missing_ok=True)
            dropped += 1
    return {"results": removed, "events": dropped}


def usage_bytes() -> int:
    if not LAB_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in LAB_DIR.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# A feluletnek
# ---------------------------------------------------------------------------
def public_summary(exp: dict) -> dict:
    """A holdout statisztika csak megnyitas utan latszik."""
    summary = dict(exp.get("summary") or {})
    if summary and not exp.get("holdout_opened"):
        summary.pop("holdout", None)
    out = {k: v for k, v in exp.items() if k not in ("summary",)}
    out["summary"] = summary or None
    return out


def open_holdout(exp_id: str) -> dict:
    def mutate(data: dict) -> None:
        exp = next((e for e in data["experiments"] if e["id"] == exp_id), None)
        if exp is None:
            raise ValueError("Nincs ilyen kiserlet.")
        if not exp.get("holdout_opened"):
            exp["holdout_opened"] = _now()
            er.log_holdout_opening([f"lab:{exp['name']}"], len(universe_symbols(exp["universe"], load_settings())))
    data = update_store(mutate)
    return next(e for e in data["experiments"] if e["id"] == exp_id)
