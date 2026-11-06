"""Celar-valoszinuseg (docs/TARGET-PROBABILITY-DESIGN.md): milyen gyakran erte el a multban az ar a celt `N`
kereskedesi nap alatt hasonlo helyzetekben - es ha stop is van, milyen gyakran a stop ELOTT.

  * esemeny: a `t` nap zarasa utan; a cel tavolsaga ATR-ben `d = irany x (cel - zaro) / ATR14`; a `t+1 ... t+N`
    napok csucsa (shortnal melypontja) eleri a celt; stoppal: elobb a cel, mint a stop (egy napon mindketto: stop)
  * modell: HistGradientBoostingClassifier monotonitasi kenyszerrel (d no -> nem no; N no -> nem csokken; tagabb
    stop -> nem csokken), izotonikus kalibracio kulon idoszakon; idorendi felosztas, a holdout zarva
  * jellemzocsoportok elore rogzitve (A alap, B piac, C szektor, D momentum, E technika, F fundamentum); egy csoport
    csak akkor marad, ha a validacios Brier-pontszamot legalabb MIN_GAIN-nel javitja (es az ECE nem romlik)
A szam leiro gyakorisag (nem ajanlas); ha a modell nem megy at az elfogadason, csak az alapesely latszik.
"""
from __future__ import annotations

import logging
import math
import pickle
from datetime import date

import numpy as np
import pandas as pd

from . import indicators, store
from .config import DATA_DIR, Settings
from .entry_research import HOLDOUT_START, wilson

log = logging.getLogger(__name__)

OUT_DIR = DATA_DIR / "research" / "target_prob"
MODEL_PATH = OUT_DIR / "model.pkl"
NO_STOP = 99.0                                  # "nincs stop" a stop-tavolsag jellemzoben
MAX_D, MAX_N = 8.0, 60
DAYS_PER_SYMBOL = 80
COMBOS_PER_DAY = 6                              # (d, N, stop) parok egy naphoz, iranyonkent
START = pd.Timestamp("2010-01-01")
CALIB_START, VALID_START = pd.Timestamp("2020-01-01"), pd.Timestamp("2022-01-01")
MIN_GAIN = 0.005                                # egy csoport legalabb ennyivel (relativ) javitsa a Brier-t
ACCEPT_GAIN = 0.01
ACCEPT_ECE = 0.03
MIN_BAND = 200                                  # ennyi multbeli helyzet kell a ±5 pp savban
GROUPS = {
    "A": ["d", "n", "stop_d", "side", "atr_pct", "vol20"],
    "B": ["bench_above200", "bench_r20", "bench_r60"],
    "C": ["sector_above200", "sector_r21", "rel_sector21"],
    "D": ["mom_12_1", "r63", "r21", "rs60", "dist_high52"],
    "E": ["rsi", "adx", "ema_order", "dist_sma200_atr", "pos20"],
    "F": ["eps_yoy", "revenue_yoy", "eps_acc"],
}
GROUP_LABELS = {"A": "alap (volatilitas, tavolsag, ido)", "B": "piac", "C": "szektor", "D": "momentum",
                "E": "technika", "F": "fundamentum"}


# ---------------------------------------------------------------------------
# Jellemzok (point-in-time: minden ertek a `t` napig ismert adatbol)
# ---------------------------------------------------------------------------
def _ret(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def _aligned(other: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.Series | None:
    return None if other is None or other.empty else other["close"].reindex(index, method="ffill")


def daily_features(df: pd.DataFrame, bench: pd.DataFrame | None, sector: pd.DataFrame | None,
                   fund: dict | None = None) -> pd.DataFrame:
    """Naponkenti jellemzok (a `d`, `n`, `stop_d`, `side` nelkul) egy papirra."""
    e = indicators.enrich(df)
    c = e["close"]
    out = pd.DataFrame(index=e.index)
    out["atr"] = e["atr"]
    out["atr_pct"] = e["atr"] / c * 100
    out["vol20"] = np.log(c).diff().rolling(20).std() * math.sqrt(252) * 100
    b = _aligned(bench, e.index)
    if b is not None:
        out["bench_above200"] = (b > b.rolling(200).mean()).astype(float).where(b.rolling(200).count() == 200)
        out["bench_r20"], out["bench_r60"] = _ret(b, 20) * 100, _ret(b, 60) * 100
        out["rs60"] = (_ret(c, 60) - _ret(b, 60)) * 100
    s = _aligned(sector, e.index)
    if s is not None:
        out["sector_above200"] = (s > s.rolling(200).mean()).astype(float).where(s.rolling(200).count() == 200)
        out["sector_r21"] = _ret(s, 21) * 100
        out["rel_sector21"] = (_ret(c, 21) - _ret(s, 21)) * 100
    out["mom_12_1"] = (c.shift(21) / c.shift(252) - 1) * 100
    out["r63"], out["r21"] = _ret(c, 63) * 100, _ret(c, 21) * 100
    out["dist_high52"] = (c / c.rolling(252, min_periods=200).max() - 1) * 100
    out["rsi"] = e["rsi"]
    out["adx"] = indicators.adx(e)["adx"] if "adx" not in e else e["adx"]
    out["ema_order"] = np.where((e["ema20"] > e["ema50"]) & (e["ema50"] > e["ema200"]), 1.0,
                                np.where((e["ema20"] < e["ema50"]) & (e["ema50"] < e["ema200"]), -1.0, 0.0))
    out["dist_sma200_atr"] = (c - c.rolling(200).mean()) / e["atr"]
    lo, hi = e["low"].rolling(20).min(), e["high"].rolling(20).max()
    out["pos20"] = (c - lo) / (hi - lo).replace(0, np.nan)
    if fund:
        out[["eps_yoy", "revenue_yoy", "eps_acc"]] = fundamental_series(fund, e.index)
    # hianyzo benchmark / szektor / SEC-adat: az oszlop ures (NaN) - a modell ezt "nem ismert"-kent kezeli
    return out.reindex(columns=["atr"] + [c for g in "ABCDEF" for c in GROUPS[g] if c not in ("d", "n", "stop_d", "side")])


def fundamental_series(fund: dict, index: pd.DatetimeIndex) -> pd.DataFrame:
    """A SEC-kivonat napi sorra: az ertek a beadas UTANI naptol el (fundamentals.as_of a valtozasi napokon)."""
    from . import fundamentals

    filed = sorted({pd.Timestamp(r[3]) + pd.Timedelta(days=1) for key in ("eps", "revenue") for r in fund.get(key, [])})
    rows = {}
    for day in filed:
        f = fundamentals.as_of("", day, fund)
        rows[day] = (f["eps_yoy"], f["revenue_yoy"], float(f["eps_accelerating"])) if f else (np.nan, np.nan, np.nan)
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=["eps_yoy", "revenue_yoy", "eps_acc"], dtype=float)
    if frame.empty:
        return pd.DataFrame(np.nan, index=index, columns=frame.columns if len(frame.columns) else ["eps_yoy", "revenue_yoy", "eps_acc"])
    out = frame.reindex(frame.index.union(index)).sort_index().ffill().reindex(index)
    # elavult (pl. megszunt jelentes): STALE_DAYS utan mar nem ismert
    last = pd.Series(frame.index, index=frame.index).reindex(frame.index.union(index)).ffill().reindex(index)
    stale = (index.to_series() - last).dt.days > fundamentals.STALE_DAYS
    return out.mask(np.repeat(stale.to_numpy()[:, None], out.shape[1], axis=1))


# ---------------------------------------------------------------------------
# Cimkek: a cel / a stop elso elerese (a jovobeli ablakon, csak a tanitashoz)
# ---------------------------------------------------------------------------
def first_touch(high: np.ndarray, low: np.ndarray, i: int, close: float, atr: float, side: int,
                d: np.ndarray, stop_d: np.ndarray, horizon: int = MAX_N) -> tuple[np.ndarray, np.ndarray]:
    """A `t+1 ...` napok kozul hanyadikon eri el eloszor a cel (d ATR) es a stop (stop_d ATR) - nincs: horizon."""
    hi, lo = high[i + 1: i + 1 + horizon], low[i + 1: i + 1 + horizon]
    fav = np.maximum.accumulate(hi if side > 0 else -lo)
    adv = np.maximum.accumulate(-lo if side > 0 else hi)
    tgt = (close + side * d * atr) * side
    stp = (close - side * stop_d * atr) * (-side)
    return np.searchsorted(fav, tgt, side="left"), np.searchsorted(adv, stp, side="left")


def samples(symbol: str, feats: pd.DataFrame, df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Veletlen napok (a holdout elott, hogy a t+N is elotte legyen) x (d, N, stop, irany) - cimkevel es jellemzokkel."""
    idx = feats.index
    ok = np.flatnonzero((idx >= START) & (idx < HOLDOUT_START) & feats["atr"].notna().to_numpy()
                        & feats["vol20"].notna().to_numpy())
    ok = ok[ok + MAX_N < np.searchsorted(idx, HOLDOUT_START)]
    if not len(ok):
        return pd.DataFrame()
    days = rng.choice(ok, size=min(DAYS_PER_SYMBOL, len(ok)), replace=False)
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    rows = []
    for i in sorted(days):
        atr = float(feats["atr"].iloc[i])
        for side in (1, -1):
            # folytonos huzas (a felhasznalo barmilyen arat megadhat - racson a modell lepcsos lenne); a `d` es a
            # stop log-egyenletesen, hogy a kozeli es a tavoli cel is eleg mintat kapjon
            d = np.exp(rng.uniform(np.log(0.3), np.log(MAX_D), COMBOS_PER_DAY))
            n = rng.integers(1, MAX_N + 1, COMBOS_PER_DAY)
            stop = np.where(rng.random(COMBOS_PER_DAY) < 0.5, NO_STOP,
                            np.exp(rng.uniform(np.log(0.5), np.log(5.0), COMBOS_PER_DAY)))
            t_hit, s_hit = first_touch(high, low, i, close[i], atr, side, d, np.minimum(stop, 1e6))
            hit = (t_hit < n) & ((stop >= NO_STOP) | (t_hit < s_hit))
            for k in range(COMBOS_PER_DAY):
                rows.append((symbol, idx[i], side, d[k], n[k], stop[k], bool(hit[k])))
    out = pd.DataFrame(rows, columns=["symbol", "day", "side", "d", "n", "stop_d", "hit"])
    return out.join(feats.drop(columns="atr"), on="day")


# ---------------------------------------------------------------------------
# Tanitas es meres
# ---------------------------------------------------------------------------
def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Kalibracios hiba: a savonkenti |atlagos becsles - gyakorisag| a sav sulyaval."""
    edges = np.linspace(0, 1, bins + 1)
    k = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return float(sum(abs(p[k == b].mean() - y[k == b].mean()) * (k == b).mean() for b in range(bins) if (k == b).any()))


def _fit(train: pd.DataFrame, calib: pd.DataFrame, cols: list[str], seed: int = 7):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression

    cols = [c for c in cols if train[c].notna().any()]            # teljesen ures oszlop (pl. nincs benchmark): kimarad
    mono = [{"d": -1, "n": 1, "stop_d": 1}.get(c, 0) for c in cols]
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=200,
                                         l2_regularization=1.0, monotonic_cst=mono, random_state=seed)
    clf.fit(train[cols], train["hit"])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(clf.predict_proba(calib[cols])[:, 1], calib["hit"])
    return {"cols": cols, "clf": clf, "iso": iso}


def predict(model: dict, x: pd.DataFrame) -> np.ndarray:
    return model["iso"].predict(model["clf"].predict_proba(x[model["cols"]])[:, 1])


def train(data: pd.DataFrame) -> dict:
    """Az elore rogzitett csoport-kivalasztas es az elfogadas (a holdout zarva)."""
    data = data[data["day"] < HOLDOUT_START]
    tr, ca, va = (data[data["day"] < CALIB_START], data[(data["day"] >= CALIB_START) & (data["day"] < VALID_START)],
                  data[data["day"] >= VALID_START])
    y = va["hit"].to_numpy(dtype=float)
    steps, cols = [], list(GROUPS["A"])
    base = _fit(tr, ca, cols)
    p = predict(base, va)
    best = {"brier": brier(p, y), "ece": ece(p, y)}
    steps.append({"group": "A", "kept": True} | best)
    models = {"A": base}
    for g in "BCDEF":
        cand = _fit(tr, ca, cols + GROUPS[g])
        pc = predict(cand, va)
        score = {"brier": brier(pc, y), "ece": ece(pc, y)}
        kept = score["brier"] <= best["brier"] * (1 - MIN_GAIN) and score["ece"] <= best["ece"] + 0.01
        steps.append({"group": g, "kept": kept} | score)
        if kept:
            cols, best, p = cols + GROUPS[g], score, pc
            models[g] = cand
    base_brier = steps[0]["brier"]
    years = {}
    pa = predict(base, va)
    for year in (2022, 2023, 2024):
        m = (va["day"].dt.year == year).to_numpy()
        if m.any():
            years[year] = {"model": brier(p[m], y[m]), "base": brier(pa[m], y[m])}
    accepted = (best["brier"] <= base_brier * (1 - ACCEPT_GAIN) and best["ece"] <= ACCEPT_ECE
                and all(v["model"] <= v["base"] for v in years.values()))
    final = models[list(models)[-1]]
    return {"steps": steps, "years": years, "accepted": accepted, "groups": list(models), "models": models,
            "final": final if accepted else base, "valid": {"p": (p if accepted else pa).round(4).tolist(),
                                                           "y": y.astype(int).tolist()},
            "rows": {"train": len(tr), "calib": len(ca), "valid": len(va)},
            "trained": date.today().isoformat()}


def build(symbols: list[str], settings: Settings, seed: int = 26) -> pd.DataFrame:
    """A tanito adat a kutatasi univerzumbol (helyi arfolyamtar; a SEC-kivonat, ha van)."""
    from . import fundamentals, market, sectors

    ctx = market.load_context(settings, refresh=False, allow_network=False)
    smap = sectors.sector_map(symbols, allow_network=False)
    etfs = {name: store.load(etf, settings, universe=True) for name, etf in sectors.SECTOR_ETFS.items()}
    rng = np.random.default_rng(seed)
    parts = []
    for n, symbol in enumerate(symbols, 1):
        df = store.load(symbol, settings, universe=True)
        if df.empty or len(df) < 400:
            continue
        feats = daily_features(df, ctx.bench_for(symbol), etfs.get(smap.get(symbol)), fundamentals.load(symbol))
        parts.append(samples(symbol, feats, df, rng))
        if n % 50 == 0:
            log.info("Celar-valoszinuseg: %d / %d papir", n, len(symbols))
    return pd.concat([p for p in parts if not p.empty], ignore_index=True)


def save(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(result))
    tmp.replace(MODEL_PATH)


def load() -> dict | None:
    try:
        return pickle.loads(MODEL_PATH.read_bytes()) if MODEL_PATH.exists() else None
    except (OSError, pickle.UnpicklingError, AttributeError, ModuleNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Lekerdezes (a Papir kiertekelese ablak)
# ---------------------------------------------------------------------------
def estimate(symbol: str, side: str, target: float, days: int, stop: float | None, settings: Settings) -> dict:
    """A felulet kerese: a papir utolso LEZART napja utan (a modell is zaras utani dontesre tanult)."""
    from . import best, fundamentals, market, sectors, sessions
    from .sentiment import service as news

    if side not in ("long", "short"):
        raise ValueError("Az irany long vagy short legyen.")
    for name, v in (("celar", target), ("stop", stop)):
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0):
            raise ValueError(f"Ervenytelen {name}.")
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError("Az idotav egesz szam (kereskedesi nap) legyen.")
    result = load()
    if result is None:
        raise ValueError("Meg nincs tanitott modell (python cli.py target-prob).")
    df = sessions.completed_bars(best._frame(symbol, settings))
    if len(df) < 260:
        raise ValueError(f"{symbol}: tul rovid historia a becsleshez.")
    ctx = market.load_context(settings, refresh=False, allow_network=False)
    sector = sectors.sector_map([symbol], allow_network=False).get(symbol)
    etf = sectors.SECTOR_ETFS.get(sector)
    feats = daily_features(df, ctx.bench_for(symbol), store.load(etf, settings, universe=True) if etf else None,
                           fundamentals.load(symbol))
    out = query(result, feats.iloc[-1], float(df["close"].iloc[-1]), side, float(target), days,
                None if stop is None else float(stop))
    try:                                         # a hirek csak tajekoztatasul (a #20 validacioja szerint)
        out["news"] = news.summary(news.features_for(symbol, df.index[-1]))
    except Exception:
        out["news"] = None
    return out | {"symbol": symbol, "as_of": f"{df.index[-1]:%Y-%m-%d}", "close": round(float(df["close"].iloc[-1]), 2),
                  "sector": sector, "side": side, "target": target, "days": days, "stop": stop}


def query(result: dict, feats_today: pd.Series, close: float, side: str, target: float, days: int,
          stop: float | None = None) -> dict:
    """A mai helyzet becslese. `feats_today`: daily_features utolso sora."""
    sign = 1 if side == "long" else -1
    atr = float(feats_today["atr"])
    d = sign * (target - close) / atr if atr > 0 else math.nan
    stop_d = NO_STOP if stop is None else sign * (close - stop) / atr
    if not (0 < d <= MAX_D) or not (1 <= days <= MAX_N) or (stop is not None and not 0 < stop_d < NO_STOP):
        return {"ok": False, "reason": "range", "d": None if math.isnan(d) else round(d, 2)}
    x = pd.DataFrame([feats_today.drop(labels="atr", errors="ignore").to_dict()
                      | {"d": d, "n": days, "stop_d": stop_d, "side": sign}])
    base = float(predict(result["models"]["A"], x)[0])
    prob = float(predict(result["final"], x)[0])
    vp, vy = np.array(result["valid"]["p"]), np.array(result["valid"]["y"])
    band = np.abs(vp - prob) <= 0.05
    n = int(band.sum())
    lo, hi = wilson(int(vy[band].sum()), n) if n else (None, None)
    contrib, prev = [], base
    if result["accepted"]:
        for g in result["groups"][1:]:
            pg = float(predict(result["models"][g], x)[0])
            contrib.append({"group": g, "label": GROUP_LABELS[g], "pp": round((pg - prev) * 100, 1)})
            prev = pg
    return {"ok": n >= MIN_BAND, "reason": None if n >= MIN_BAND else "few", "probability": round(prob, 3),
            "base": round(base, 3), "accepted": result["accepted"], "d": round(d, 2),
            "stop_d": None if stop is None else round(stop_d, 2), "band": {"n": n, "observed": round(float(vy[band].mean()), 3) if n else None,
                                                                         "lo": lo, "hi": hi},
            "contributions": contrib, "trained": result["trained"]}
