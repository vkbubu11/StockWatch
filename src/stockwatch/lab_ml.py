"""Strategia-labor: gepi tanulas a kitoresek kimenetelere - evenkent elore halado (walk-forward) teszttel.

KERDES: meg tudja-e mondani egy modell a kitores napjan ismert adatokbol, hogy melyik kitores
lesz jo trade - JOBBAN, mint a mostani kemeny szurok?

ADATSOR: minden bikas kitores (minden papiron), a mostani belepesi mod szerint (megerosites utan),
az ELO kilepesi tervvel (stop, fele +1R-nel, cel, idokorlat) lejatszva - SZURES NELKUL. A cimke a
trade eredmenye R-ben. A jellemzok csak a jelzes napjaig ismert adatbol keszulnek.

TESZT: minden Y evre a modell CSAK az Y elott LEZARULT trade-ekbol tanul (a meg nyitott trade
kimenetele a tanitaskor nem ismert - "purge"), es Y-ban azokat a trade-eket valasztja, amelyekre a
becsult eredmeny a tanito halmaz felso VALASZTOTT_ARANY-aban van. Osszehasonlitas ugyanazokon az
eveken: minden kitores / a mostani szurok / a modell / a modell + a mostani szurok.

A holdout (2025-04 utan) kimarad - ugyanugy zarolt, mint a kutatasban. Elesben a modell semmirol
nem dont. Ha mintan kivul bizonyit (verdict "better"), a shadow.py arnyek-uzemben a jelzesek
melle irja a becslest, es naplozza - igy valodi uj adaton merheto, mielott barmirol dontene.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import entry, market
from . import entry_research as er
from .config import Settings
from .locks import write_atomic
from .trade_mgmt import max_hold

log = logging.getLogger(__name__)

UNIVERSE = "all"
SELECT_FRACTION = 0.4            # a tanito halmazon becsult legjobb 40% kerul kivalasztasra
MIN_TRAIN = 300
MIN_TEST = 20
FIRST_TEST_YEAR = 2018
LABEL_CLIP = (-1.5, 5.0)         # a kiugro (pl. +12R) trade-ek ne uraljak a tanitast
RETRAIN_EVERY_DAYS = 7

NUMERIC = ["quality", "horizon", "rsi", "adx", "vol_ratio", "vol_ratio_breakout", "extension_atr", "stop_pct",
           "risk_reward", "atr_pct", "close_ema20", "close_sma200", "ema50_slope", "high52", "momentum",
           "rel_strength", "contraction", "regime", "days_since_breakout"]
LABELS = {
    "quality": "mintaminoseg", "horizon": "minta hossza", "rsi": "RSI", "adx": "ADX",
    "vol_ratio": "volumen (jelzes napja)", "vol_ratio_breakout": "kitoresi volumen",
    "extension_atr": "tavolsag a triggertol (ATR)", "stop_pct": "stop %", "risk_reward": "hozam/kockazat",
    "atr_pct": "volatilitas (ATR%)", "close_ema20": "zaro / EMA20", "close_sma200": "zaro / SMA200",
    "ema50_slope": "EMA50 meredekseg", "high52": "52 hetes csucs %", "momentum": "momentum 12-1",
    "rel_strength": "relativ ero (60n)", "contraction": "osszehuzodas", "regime": "QQQ / SMA200",
    "days_since_breakout": "napok a kitores ota", "pattern": "alakzat",
}


def report_path():
    from . import lab

    return lab._p("ml", "report.json")


def due(data: dict, version: str) -> bool:
    """Kell-e tanitani: kerte valaki, vagy uj adat jott es a legutobbi tanitas regebbi egy hetnel."""
    ml = data.get("ml") or {}
    if not ml.get("enabled", True):
        return False
    if ml.get("pending"):
        return True
    trained = ml.get("trained")
    if not trained:
        return True
    old = datetime.now(timezone.utc) - pd.Timestamp(trained).to_pydatetime() > pd.Timedelta(days=RETRAIN_EVERY_DAYS)
    return old and ml.get("version") != version


# ---------------------------------------------------------------------------
# Adatsor
# ---------------------------------------------------------------------------
class RowCollector:
    """A labor ertekelo koreben papironkent hivodik (lab.evaluate symbol_hook)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rows: list[dict] = []
        self._bench_sma: pd.Series | None = None

    def add(self, symbol: str, events: list[er.Event], frame: pd.DataFrame, bench: pd.DataFrame | None) -> None:
        if bench is not None and self._bench_sma is None:
            self._bench_sma = bench_ratio(bench)
        for event in events:
            row = sample(event, frame, self.settings, bench, self._bench_sma)
            if row is not None:
                self.rows.append(row)


def _num(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def sample(event: er.Event, frame: pd.DataFrame, settings: Settings, bench: pd.DataFrame | None,
           bench_sma: pd.Series | None) -> dict | None:
    """Egy kitores -> (jellemzok, eredmeny R-ben), a mostani belepesi mod es kilepesi terv szerint."""
    cfg = settings.entry
    match, i = event.match, event.index
    signal_bar = i + 1 if cfg.confirm_breakout else i
    if signal_bar + 1 >= len(frame):
        return None
    if cfg.confirm_breakout and not frame["close"].iloc[signal_bar] > match.trigger_level:
        return None                                # a megerosites elmaradt: elesben sincs jelzes
    history = frame.iloc[: signal_bar + 1]
    plan = entry.build_plan(match, "breakout", history, cfg)
    if not plan.target > plan.entry > plan.stop:
        return None
    entry_bar = signal_bar + 1
    close = float(history["close"].iloc[-1])
    open_ = float(frame["open"].iloc[entry_bar])
    if open_ > close * (1 + cfg.entry_max_gap_pct / 100) or open_ <= plan.stop:
        return None                                # elesben sem lett volna belepes
    if entry_bar + max_hold(event.horizon, cfg.exit_mode) > len(frame) - 1:
        return None                                # a trade meg nem futott le - a kimenete nem ismert
    slip = cfg.total_cost_bps / 10_000
    price = open_ * (1 + slip)
    r, reason, held = er.simulate_exit(frame, entry_bar, price, plan.stop, plan.target, event.horizon,
                                       plan.atr, cfg.exit_mode, cfg.partial_r, cfg.trail_atr)
    r -= (price * slip) / (price - plan.stop)

    return {"symbol": event.symbol, "pattern": match.name, "date": history.index[-1],
            "exit_date": frame.index[min(entry_bar + max(held, 1) - 1, len(frame) - 1)],
            "r": float(r), "reason": reason,
            **features(match, i, history, plan, event.horizon, bench, bench_sma)}


def features(match, breakout_index: int, history: pd.DataFrame, plan, horizon: int,
             bench: pd.DataFrame | None, bench_sma: pd.Series | None) -> dict:
    """A modell bemenetei a jelzes napjan (`history` utolso sora). A tanitas (sample) es az eles
    arnyek-becsles (shadow.py) is EZT hivja - igy a ketto nem csuszhat szet."""
    last = history.iloc[-1]
    as_of = history.index[-1]
    close = float(last["close"])
    sma200 = history["close"].iloc[-200:].mean() if len(history) >= 200 else math.nan
    if bench_sma is not None:
        upto = bench_sma.loc[:as_of]
        regime = _num(upto.iloc[-1]) if len(upto) else math.nan
    else:
        regime = math.nan
    return {
        "quality": _num(match.quality), "horizon": float(horizon),
        "rsi": _num(last.get("rsi")), "adx": _num(last.get("adx")), "vol_ratio": _num(last.get("vol_ratio")),
        "vol_ratio_breakout": _num(history["vol_ratio"].iloc[breakout_index])
        if "vol_ratio" in history and 0 <= breakout_index < len(history) else math.nan,
        "extension_atr": _num((close - match.trigger_level) / plan.atr) if plan.atr else math.nan,
        "stop_pct": _num(plan.stop_pct), "risk_reward": _num(plan.risk_reward), "atr_pct": _num(last.get("atr_pct")),
        "close_ema20": _num(close / last.get("ema20") - 1) if last.get("ema20") else math.nan,
        "close_sma200": _num(close / sma200 - 1),
        "ema50_slope": _num(last.get("ema50_slope")),
        "high52": _num(entry.high52_ratio(history)), "momentum": _num(entry.momentum_12_1(history)),
        "rel_strength": _num(market.relative_strength(history, bench, as_of)) if bench is not None else math.nan,
        "contraction": _num(entry.contraction_ratio(history)),
        "regime": regime,
        "days_since_breakout": float(len(history) - 1 - breakout_index),
    }


def bench_ratio(bench: pd.DataFrame | None) -> pd.Series | None:
    """QQQ / SMA200 - 1 (a "regime" jellemzohoz)."""
    if bench is None or bench.empty:
        return None
    return bench["close"] / bench["close"].rolling(200, min_periods=200).mean() - 1


# ---------------------------------------------------------------------------
# Tanitas es walk-forward ertekeles
# ---------------------------------------------------------------------------
def _matrix(df: pd.DataFrame, patterns: list[str]) -> np.ndarray:
    onehot = np.stack([(df["pattern"] == p).to_numpy(dtype=float) for p in patterns], axis=1) if patterns \
        else np.empty((len(df), 0))
    return np.hstack([df[NUMERIC].to_numpy(dtype=float), onehot])


def _model():
    from sklearn.ensemble import HistGradientBoostingRegressor

    # eros regularizacio: zajos cimke, nehany ezer minta
    return HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=60,
                                         l2_regularization=1.0, random_state=7)


def _group(r: np.ndarray) -> dict:
    if r.size == 0:
        return {"n": 0, "win": None, "ev": None, "ev_lo": None, "ev_hi": None, "win_lo": None, "win_hi": None}
    lo, hi = er.bootstrap_ci(r)
    wlo, whi = er.wilson(int((r > 0).sum()), int(r.size))
    return {"n": int(r.size), "win": float((r > 0).mean()), "ev": float(r.mean()), "ev_lo": lo, "ev_hi": hi,
            "win_lo": wlo, "win_hi": whi}


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 10:
        return None
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def walk_forward(rows: list[dict], live_keys: set) -> dict:
    """Evenkenti, csak a multbol tanulo ertekeles. A holdout (2025-04 utan) kimarad."""
    df = pd.DataFrame(rows)
    if df.empty:
        return {"rows": 0, "years": [], "overall": None, "note": "Meg nincs adat."}
    df = df[df["date"] < er.HOLDOUT_START].reset_index(drop=True)
    df["live"] = [(s, d, p) in live_keys for s, d, p in zip(df["symbol"], df["date"], df["pattern"])]
    patterns = sorted(df["pattern"].unique())
    y = df["r"].clip(*LABEL_CLIP).to_numpy()
    X = _matrix(df, patterns)

    years, picks, preds = [], np.zeros(len(df), dtype=bool), np.full(len(df), np.nan)
    tested = np.zeros(len(df), dtype=bool)
    last_model = None
    for year in range(FIRST_TEST_YEAR, int(df["date"].max().year) + 1):
        start = pd.Timestamp(year=year, month=1, day=1)
        train = (df["exit_date"] < start).to_numpy()            # csak a mar LEZARULT trade-ek
        test = (df["date"].dt.year == year).to_numpy()
        if train.sum() < MIN_TRAIN or test.sum() < MIN_TEST:
            continue
        model = _model().fit(X[train], y[train])
        threshold = float(np.quantile(model.predict(X[train]), 1 - SELECT_FRACTION))
        p = model.predict(X[test])
        preds[test], picks[test], tested[test] = p, p >= threshold, True
        r = df["r"].to_numpy()[test]
        live = df["live"].to_numpy()[test]
        sel = p >= threshold
        years.append({"year": year, "train": int(train.sum()), "all": _group(r), "live": _group(r[live]),
                      "ml": _group(r[sel]), "ml_live": _group(r[sel & live]), "ic": _spearman(p, r)})
        last_model = (model, X[test], y[test])

    r_all = df["r"].to_numpy()
    live = df["live"].to_numpy()
    overall = {
        "all": _group(r_all[tested]), "live": _group(r_all[tested & live]),
        "ml": _group(r_all[tested & picks]), "ml_live": _group(r_all[tested & picks & live]),
        "ic": _spearman(preds[tested], r_all[tested]),
    } if tested.any() else None

    features = []
    if last_model is not None:
        from sklearn.inspection import permutation_importance

        model, Xt, yt = last_model
        imp = permutation_importance(model, Xt, yt, n_repeats=5, random_state=7, scoring="r2")
        names = NUMERIC + [f"pattern={p}" for p in patterns]
        order = np.argsort(-imp.importances_mean)[:10]
        features = [{"name": LABELS.get(names[k], names[k]), "importance": float(imp.importances_mean[k])}
                    for k in order]
    return {"rows": int(len(df)), "patterns": len(patterns), "years": years, "overall": overall,
            "features": features, "select_fraction": SELECT_FRACTION}


def train_and_report(rows: list[dict], live_keys: set, version: str, entry_cfg: dict | None = None) -> dict:
    """Walk-forward jelentes + (ha van eleg adat) az eles arnyek-becsleshez kesz vegso modell."""
    report = walk_forward(rows, live_keys)
    level, text = verdict(report)
    report.update(trained=datetime.now(timezone.utc).isoformat(timespec="seconds"), version=version,
                  verdict=text, level=level)
    write_atomic(report_path(), json.dumps(report, ensure_ascii=False, default=str))
    try:
        save_final_model(rows, report, entry_cfg)
    except Exception:
        log.exception("A vegso modell mentese nem sikerult")
    log.info("Gepi tanulas kesz: %s", text)
    return report


def model_path():
    from . import lab

    return lab._p("ml", "model.pkl")


def save_final_model(rows: list[dict], report: dict, entry_cfg: dict | None) -> bool:
    """A vegso modell MINDEN lezart trade-bol (az eles, elore nezo becsleshez ez a helyes: a jovo
    meg ismeretlen, igy a holdout-idoszak is tanitoadat lehet). Egyetlen fajl, felulirva."""
    import pickle

    import sklearn

    df = pd.DataFrame(rows)
    if len(df) < MIN_TRAIN:
        model_path().unlink(missing_ok=True)
        return False
    patterns = sorted(df["pattern"].unique())
    X, y = _matrix(df, patterns), df["r"].clip(*LABEL_CLIP).to_numpy()
    model = _model().fit(X, y)
    payload = {
        "model": model, "patterns": patterns, "numeric": list(NUMERIC),
        "threshold": float(np.quantile(model.predict(X), 1 - SELECT_FRACTION)),
        "trained": report["trained"], "rows": int(len(df)), "level": report["level"],
        "verdict": report["verdict"], "overall": report.get("overall"), "entry": entry_cfg,
        "sklearn": sklearn.__version__,
    }
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(pickle.dumps(payload))
    tmp.replace(path)
    return True


def predict(payload: dict, feats: dict, pattern: str) -> float:
    """Egy jelzes becsult eredmenye R-ben a mentett modellel."""
    row = pd.DataFrame([{**{k: feats.get(k, math.nan) for k in payload["numeric"]}, "pattern": pattern}])
    return float(payload["model"].predict(_matrix(row, payload["patterns"]))[0])


def verdict(report: dict) -> tuple[str, str]:
    """(szint, egy mondatos ovatos osszegzes). Szint: better | slightly | no | nodata.
    Az arnyek-uzem "auto" modban csak "better" szintu modellel indul."""
    o = report.get("overall")
    if not o or not o["ml"]["n"] or not o["live"]["n"]:
        return "nodata", "Meg nincs eleg adat az ertekeleshez."
    ml, live = o["ml"], o["live"]
    if ml["ev_lo"] is not None and live["ev"] is not None and ml["ev_lo"] > live["ev"]:
        return "better", (f"A modell mintan kivul jobb a mostani szuroknel ({ml['ev']:+.2f}R vs {live['ev']:+.2f}R, "
                          "az also 95%-os hatar is felette) - az arnyek-uzem elindul.")
    if ml["ev"] is not None and live["ev"] is not None and ml["ev"] > live["ev"]:
        return "slightly", (f"A modell kisse jobb ({ml['ev']:+.2f}R vs {live['ev']:+.2f}R), de a kulonbseg a "
                            "bizonytalansagon belul van - meg nem bizonyitott.")
    return "no", (f"A modell nem jobb a mostani szuroknel ({ml['ev']:+.2f}R vs {live['ev']:+.2f}R) - "
                  "a kemeny szurok maradnak.")


def load_report() -> dict | None:
    try:
        return json.loads(report_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
