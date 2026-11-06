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

from . import barrier, indicators, store
from .config import DATA_DIR, Settings
from .entry_research import HOLDOUT_START, wilson

log = logging.getLogger(__name__)

OUT_DIR = DATA_DIR / "research" / "target_prob"
MODEL_PATH = OUT_DIR / "model.pkl"
NO_STOP = 99.0                                  # "nincs stop" a stop-tavolsag jellemzoben
MAX_D, MAX_N = 8.0, 60
DAYS_PER_SYMBOL = 160
HIST_DIR = DATA_DIR / "research" / "history"     # a hosszu (2000-tol) kutatasi arfolyamtar
COMBOS_PER_DAY = 6                              # (d, N, stop) parok egy naphoz, iranyonkent
START = pd.Timestamp("2001-06-01")
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
    "J": ["earn_days", "earn_in", "earn_jump_atr"],
    "V": ["rv5", "rv60", "park20", "gk20", "ewma_vol", "vix", "vix_rel"],
    "K": ["own_hit", "gap_freq", "kurt250", "beta250"],
}
GROUP_LABELS = {"A": "alap (volatilitas, tavolsag, ido)", "B": "piac", "C": "szektor", "D": "momentum",
                "E": "technika", "F": "fundamentum", "J": "gyorsjelentes", "V": "volatilitas-elorejelzes",
                "K": "a papir karaktere"}
ROW_FEATURES = ("d", "n", "stop_d", "side", "earn_in", "own_hit")   # a kerdestol (d, N, irany) is fuggnek
EXC_N = (5, 20, 60)                             # a sajat talalati arany idotavjai
OWN_LOOKBACK, OWN_MIN = 504, 100                # 2 ev; ennel kevesebb lezart kimenet: nem ismert
BOOT = 500                                      # napokra blokkolt bootstrap a Brier-kulonbsegre


# ---------------------------------------------------------------------------
# Jellemzok (point-in-time: minden ertek a `t` napig ismert adatbol)
# ---------------------------------------------------------------------------
def _ret(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def _aligned(other: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.Series | None:
    return None if other is None or other.empty else other["close"].reindex(index, method="ffill")


def daily_features(df: pd.DataFrame, bench: pd.DataFrame | None, sector: pd.DataFrame | None,
                   fund: dict | None = None, earnings: list | None = None, vix: pd.DataFrame | None = None) -> pd.DataFrame:
    """Naponkenti jellemzok (a kerdestol fuggok - ROW_FEATURES - nelkul) egy papirra."""
    e = indicators.enrich(df)
    c = e["close"]
    out = pd.DataFrame(index=e.index)
    out["atr"] = e["atr"]
    out["atr_pct"] = e["atr"] / c * 100
    lr = np.log(c).diff()
    ann = math.sqrt(252) * 100
    out["vol20"] = lr.rolling(20).std() * ann
    # V: tobb idotavu realizalt, sav-alapu (Parkinson, Garman-Klass) es EWMA-volatilitas, ^VIX
    out["rv5"], out["rv60"] = lr.rolling(5).std() * ann, lr.rolling(60).std() * ann
    hl, co = np.log(e["high"] / e["low"]), np.log(e["close"] / e["open"])
    out["park20"] = np.sqrt((hl ** 2 / (4 * math.log(2))).rolling(20).mean()) * ann
    out["gk20"] = np.sqrt((0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2).clip(lower=0).rolling(20).mean()) * ann
    out["ewma_vol"] = np.sqrt((lr ** 2).ewm(alpha=0.06, adjust=False).mean()) * ann
    v = _aligned(vix, e.index)
    if v is not None:
        out["vix"], out["vix_rel"] = v, v / v.rolling(252, min_periods=120).median()
    # K: nyitasi resek gyakorisaga, csucsossag, beta
    gap = (e["open"] / c.shift(1) - 1).abs() * 100
    out["gap_freq"] = (gap > 0.5 * out["atr_pct"].shift(1)).astype(float).rolling(250, min_periods=200).mean()
    out["kurt250"] = lr.rolling(250, min_periods=200).kurt()
    b = _aligned(bench, e.index)
    if b is not None:
        rb = np.log(b).diff()
        out["beta250"] = lr.rolling(250, min_periods=200).cov(rb) / rb.rolling(250, min_periods=200).var()
    if earnings:
        out[["earn_days", "earn_jump_atr"]] = earnings_features(e, earnings)
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
    # v2: HAR-regresszorok (a `t`-ig) es a HAR illesztesehez a JOVOBELI celok (_frv*, jellemzokent soha)
    var = barrier.daily_variance(e)
    out = out.join(barrier.har_regressors(var)).join(barrier.har_targets(var))
    out["close"] = c
    # hianyzo benchmark / szektor / SEC-adat: az oszlop ures (NaN) - a modell ezt "nem ismert"-kent kezeli
    return out.reindex(columns=["atr", "close", "har_d", "har_w", "har_m", "_frv5", "_frv20", "_frv60"]
                       + [c for g in GROUPS for c in GROUPS[g] if c not in ROW_FEATURES])


def earnings_features(e: pd.DataFrame, dates: list) -> pd.DataFrame:
    """J: a kovetkezo jelentesig hatralevo kereskedesi napok (a mai is: 0), es a papir szokasos jelentesi mozgasa
    ATR-ben: az utolso 8 MAR LEZAJLOTT jelentes ketnapos (D-1 zaro -> D+1 zaro) elmozdulasanak mediánja."""
    idx = e.index
    days = np.array(sorted({np.datetime64(pd.Timestamp(d).date()) for d in dates}), dtype="datetime64[D]")
    today = idx.values.astype("datetime64[D]")
    k = np.searchsorted(days, today, side="left")                  # az elso jelentes >= ma
    nxt = np.where(k < len(days), days[np.minimum(k, len(days) - 1)], np.datetime64("NaT"))
    ahead = np.where(k < len(days), np.busday_count(today, np.where(k < len(days), nxt, today)), np.nan).astype(float)
    ahead[ahead > 120] = np.nan                                     # tul messze: nem ismert / nem relevans
    c, atr_pct = e["close"].to_numpy(), (e["atr"] / e["close"]).to_numpy()
    moves, known = [], []                                           # (ismert ettol a naptol, mozgas ATR-ben)
    for d in days:
        p = int(np.searchsorted(today, d))
        if 1 <= p and p + 1 < len(c) and atr_pct[p - 1] > 0:
            moves.append(abs(c[p + 1] / c[p - 1] - 1) / atr_pct[p - 1])
            known.append(p + 2)                                     # a D+1 zaras utani naptol ismert
    jump = np.full(len(idx), np.nan)
    for j in range(len(moves)):
        lo = known[j]
        hi = known[j + 1] if j + 1 < len(known) else len(idx)
        if lo < len(idx):
            jump[lo:hi] = np.median(moves[max(0, j - 7): j + 1])
    return pd.DataFrame({"earn_days": ahead, "earn_jump_atr": jump}, index=idx)


def excursions(df: pd.DataFrame, atr: pd.Series) -> dict[tuple[int, int], np.ndarray]:
    """(N, irany) -> a `s` naptol a kovetkezo N nap legkedvezobb elmozdulasa ATR(s)-ben. JOVOBELI ertek: csak a
    `s + N <= t` napok szamithatnak a `t` napi jellemzobe (own_hit)."""
    out = {}
    for n in EXC_N:
        up = (df["high"].rolling(n).max().shift(-n) - df["close"]) / atr
        dn = (df["close"] - df["low"].rolling(n).min().shift(-n)) / atr
        out[(n, 1)], out[(n, -1)] = up.to_numpy(), dn.to_numpy()
    return out


def own_hit(exc: dict, t: int, d: float, n: int, side: int) -> float:
    """K: a papir sajat multbeli talalati aranya ugyanerre a tavolsagra (a legkozelebbi idotavon), az utolso 2 evbol,
    csak a `t`-ig lezarult kimenetekbol."""
    nb = min(EXC_N, key=lambda x: abs(x - n))
    vals = exc[(nb, side)][max(0, t - OWN_LOOKBACK): max(0, t - nb + 1)]
    vals = vals[~np.isnan(vals)]
    return float(np.mean(vals >= d)) if len(vals) >= OWN_MIN else math.nan


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


def samples(symbol: str, feats: pd.DataFrame, df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:  # noqa: C901
    """Veletlen napok (a holdout elott, hogy a t+N is elotte legyen) x (d, N, stop, irany) - cimkevel es jellemzokkel."""
    idx = feats.index
    ok = np.flatnonzero((idx >= START) & (idx < HOLDOUT_START) & feats["atr"].notna().to_numpy()
                        & feats["vol20"].notna().to_numpy() & feats["har_m"].notna().to_numpy())
    ok = ok[ok + MAX_N < np.searchsorted(idx, HOLDOUT_START)]
    if not len(ok):
        return pd.DataFrame()
    days = rng.choice(ok, size=min(DAYS_PER_SYMBOL, len(ok)), replace=False)
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    exc = excursions(df, feats["atr"])
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
                rows.append((symbol, idx[i], side, d[k], n[k], stop[k], own_hit(exc, int(i), d[k], int(n[k]), side),
                             bool(hit[k])))
    out = pd.DataFrame(rows, columns=["symbol", "day", "side", "d", "n", "stop_d", "own_hit", "hit"])
    out = out.join(feats, on="day")
    out["earn_in"] = (out["earn_days"] <= out["n"]).astype(float).where(out["earn_days"].notna())
    return out


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
    for g in "BCDEFJVK":
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
    ci = boot_gain(pa, p, y, va["day"].to_numpy())
    accepted = (best["brier"] <= base_brier * (1 - ACCEPT_GAIN) and best["ece"] <= ACCEPT_ECE
                and all(v["model"] <= v["base"] for v in years.values()) and ci[0] > 0)
    final = models[list(models)[-1]]
    return {"steps": steps, "years": years, "accepted": accepted, "groups": list(models), "models": models,
            "gain_ci": ci, "single": single_groups(tr, ca, va, y, steps[0]["brier"]),
            "final": final if accepted else base, "valid": {"p": (p if accepted else pa).round(4).tolist(),
                                                           "y": y.astype(int).tolist()},
            "rows": {"train": len(tr), "calib": len(ca), "valid": len(va)},
            "trained": date.today().isoformat()}


def boot_gain(p_base: np.ndarray, p_model: np.ndarray, y: np.ndarray, days: np.ndarray, seed: int = 7) -> tuple:
    """A Brier-javulas (alapesely - modell, relativ) 95%-os intervalluma napokra blokkolt bootstrappal (az egy napon
    belüli kerdesek nem fuggetlenek)."""
    if np.array_equal(p_base, p_model):
        return (0.0, 0.0)
    codes, uniq = pd.factorize(days)
    eb, em = np.bincount(codes, (p_base - y) ** 2), np.bincount(codes, (p_model - y) ** 2)
    rng = np.random.default_rng(seed)
    gains = []
    for _ in range(BOOT):
        w = np.bincount(rng.integers(0, len(uniq), len(uniq)), minlength=len(uniq))
        gains.append(1 - (w @ em) / (w @ eb))
    return (round(float(np.percentile(gains, 2.5)), 4), round(float(np.percentile(gains, 97.5)), 4))


def single_groups(tr, ca, va, y, base_brier: float) -> dict:
    """Tajekoztatas: minden csoport EGYEDUL az alap mellett (a kivalasztast nem befolyasolja)."""
    out = {}
    for g in "BCDEFJVK":
        pg = predict(_fit(tr, ca, GROUPS["A"] + GROUPS[g]), va)
        out[g] = {"brier": brier(pg, y), "gain": round(1 - brier(pg, y) / base_brier, 4)}
    return out


def load_history(symbol: str, settings: Settings) -> pd.DataFrame:
    """A hosszu kutatasi tar (2000-tol), ha van; kulonben a 10 eves arfolyamtar."""
    path = HIST_DIR / f"{symbol}.csv.gz"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df.loc[:, ["open", "high", "low", "close", "volume"]].dropna()
    return store.load(symbol, settings, universe=True)


# ---------------------------------------------------------------------------
# v2: HAR-RV + elso athaladas + volatilitasra normalizalt jellemzok, walk-forward (docs/... 8. fejezet)
# ---------------------------------------------------------------------------
FAMILIES = {
    "M0": GROUPS["A"],
    "M1": ["p_an"],
    "M2": ["p_an", "z_b", "z_c", "side", "n", "atr_sig", "rv_ratio", "vix_rel", "earn_in", "earn_jump_sig"],
}
FAMILIES["M3"] = FAMILIES["M2"] + [c for g in "BCDEFK" for c in GROUPS[g]]
FAMILY_LABELS = {"M0": "alapesely (ATR, tavolsag, ido)", "M1": "HAR-RV + elso athaladas (analitikus)",
                 "M2": "normalizalt gradient boosting", "M3": "normalizalt + piaci / ceges kornyezet"}
TEST_YEARS = list(range(2008, 2026))             # 2025: csak a holdout elotti negyedev
PURGE = pd.offsets.BDay(MAX_N)
TRAIN_CAP = 400_000                              # tanito sorok foldenkent legfeljebb (seedelt mintavetel)
MIN_TRAIN = 20_000                               # ennel kevesebb tanito sorral az ev kimarad
Z_NO_STOP = 10.0


def prepare(rows: pd.DataFrame, har: dict) -> pd.DataFrame:
    """A kerdes (d ATR, N, stop, irany) a HAR-szigmaval: log-tavolsag / (sigma sqrt N), analitikus esely, aranyok."""
    x = rows.copy()
    sig = barrier.har_sigma(x, x["n"].to_numpy(dtype=float), har)
    spread = sig * np.sqrt(x["n"].to_numpy(dtype=float))
    side, close, atr = x["side"].to_numpy(dtype=float), x["close"].to_numpy(), x["atr"].to_numpy()
    target = close + side * x["d"].to_numpy() * atr
    b = side * np.log(np.clip(target, 1e-9, None) / close)
    has_stop = x["stop_d"].to_numpy() < NO_STOP
    stop = close - side * np.where(has_stop, x["stop_d"].to_numpy(), 0.0) * atr
    c = np.where(has_stop, -side * np.log(np.clip(stop, 1e-9, None) / close), np.inf)
    x["z_b"] = b / spread
    x["z_c"] = np.where(has_stop, c / spread, Z_NO_STOP)
    x["p_an"] = np.where(has_stop, barrier.target_before_stop(x["z_b"].to_numpy(), x["z_c"].to_numpy()),
                         barrier.touch_prob(x["z_b"].to_numpy()))
    x["atr_sig"] = (atr / close) / sig
    x["rv_ratio"] = x["rv5"] / x["rv60"]
    x["earn_jump_sig"] = x["earn_jump_atr"] * (atr / close) / spread
    return x


def _finite(x: pd.DataFrame) -> pd.DataFrame:
    """A hibas arsor (pl. 0 vagy hianyzo ar) napjai kimaradnak: az analitikus esely nem szamolhato."""
    return x[np.isfinite(x["p_an"].to_numpy())]


def _fit_family(fam: str, tr: pd.DataFrame, ca: pd.DataFrame, seed: int = 7) -> dict:
    from sklearn.isotonic import IsotonicRegression

    if fam == "M1":                              # csak kalibracio: az analitikus esely izotonikusan
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(ca["p_an"], ca["hit"])
        return {"family": fam, "cols": ["p_an"], "clf": None, "iso": iso}
    return _fit(tr, ca, FAMILIES[fam], seed) | {"family": fam}


def predict_any(model: dict, x: pd.DataFrame) -> np.ndarray:
    if model["clf"] is None:
        return model["iso"].predict(x["p_an"].to_numpy())
    return predict(model, x)


def walk_forward(data: pd.DataFrame, families=("M0", "M1", "M2", "M3"), seed: int = 7) -> dict:
    """Evenkent: tanitas a Y-2 elott (PURGE-dzsel), kalibracio [Y-2, Y), teszt Y. A holdout soha."""
    rng = np.random.default_rng(seed)
    data = data[data["day"] < HOLDOUT_START]
    preds = {f: [] for f in families}
    ys, days = [], []
    for year in TEST_YEARS:
        y0, cal0 = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year - 2}-01-01")
        tr = data[data["day"] < cal0 - PURGE]
        ca = data[(data["day"] >= cal0) & (data["day"] < y0 - PURGE)]
        te = data[(data["day"] >= y0) & (data["day"] < min(pd.Timestamp(f"{year + 1}-01-01"), HOLDOUT_START))]
        if len(tr) < MIN_TRAIN or ca.empty or te.empty:
            continue
        har = barrier.fit_har(tr[tr["day"] < cal0 - PURGE])
        tr_x = _finite(prepare(tr.sample(n=min(TRAIN_CAP, len(tr)), random_state=int(rng.integers(1 << 31))), har))
        ca_x, te_x = _finite(prepare(ca, har)), _finite(prepare(te, har))
        for f in families:
            preds[f].append(predict_any(_fit_family(f, tr_x, ca_x), te_x))
        ys.append(te_x["hit"].to_numpy(dtype=float))
        days.append(te_x["day"].to_numpy())
        log.info("Walk-forward %d: tanito %d, kalibracio %d, teszt %d", year, len(tr), len(ca), len(te))
    y, d = np.concatenate(ys), np.concatenate(days)
    p = {f: np.concatenate(v) for f, v in preds.items()}
    years = pd.DatetimeIndex(d).year.to_numpy()
    out = {"families": {}, "y": y, "days": d, "p": p}
    for f in families:
        per_year = {int(yr): {"brier": brier(p[f][years == yr], y[years == yr]), "ece": ece(p[f][years == yr], y[years == yr])}
                    for yr in np.unique(years)}
        out["families"][f] = {"brier": brier(p[f], y), "ece": ece(p[f], y), "years": per_year,
                              "gain_vs_m0": boot_gain(p["M0"], p[f], y, d) if f != "M0" and "M0" in p else (0.0, 0.0)}
    return out


def decide(wf: dict) -> str | None:
    """Az elore rogzitett dontes: a legkisebb Brier, ha kalibralt (osszesen ECE <= 0,03, evente <= 0,05) es (ha nem
    az M0) az M0-hoz kepesti javulas intervallumanak also hatara > 0."""
    for f in sorted(wf["families"], key=lambda k: wf["families"][k]["brier"]):
        r = wf["families"][f]
        calibrated = r["ece"] <= ACCEPT_ECE and all(v["ece"] <= 0.05 for v in r["years"].values())
        if calibrated and (f == "M0" or r["gain_vs_m0"][0] > 0):
            return f
    return None


def fit_live(data: pd.DataFrame, family: str, wf: dict, seed: int = 7) -> dict:
    """Az eles modell: tanitas 2023 elott (PURGE), kalibracio 2023 - a holdout elotti napig."""
    cal0 = pd.Timestamp("2023-01-01")
    data = data[data["day"] < HOLDOUT_START]
    tr, ca = data[data["day"] < cal0 - PURGE], data[data["day"] >= cal0]
    har = barrier.fit_har(tr)
    tr_x = _finite(prepare(tr.sample(n=min(TRAIN_CAP, len(tr)), random_state=seed), har))
    ca_x = _finite(prepare(ca, har))
    model, an = _fit_family(family, tr_x, ca_x, seed), _fit_family("M1", tr_x, ca_x, seed)
    p = wf["p"][family]
    return {"version": 2, "family": family, "model": model, "analytic": an, "har": har,
            "wf": {f: {k: v for k, v in r.items()} for f, r in wf["families"].items()},
            "valid": {"p": np.round(p, 4).tolist(), "y": wf["y"].astype(int).tolist()},
            "accepted": True, "trained": date.today().isoformat()}


def build(symbols: list[str], settings: Settings, seed: int = 26) -> pd.DataFrame:
    """A tanito adat a kutatasi univerzumbol (helyi arfolyamtar; a SEC-kivonat, ha van)."""
    from . import fundamentals, market, sectors

    ctx = market.load_context(settings, refresh=False, allow_network=False)
    smap = sectors.sector_map(symbols, allow_network=False)
    etfs = {name: load_history(etf, settings) for name, etf in sectors.SECTOR_ETFS.items()}
    benches = {b: load_history(b, settings) for b in market.REGIME_INDEXES}
    earnings = market.HistoricalEarnings(settings, allow_network=False)
    vix = load_history("^VIX", settings)
    rng = np.random.default_rng(seed)
    parts = []
    for n, symbol in enumerate(symbols, 1):
        df = load_history(symbol, settings)
        if df.empty or len(df) < 400:
            continue
        feats = daily_features(df, benches[ctx.index_of(symbol) or market.SP500_INDEX], etfs.get(smap.get(symbol)),
                               fundamentals.load(symbol),
                               earnings.dates(symbol), vix)
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
    from . import best, feed, fundamentals, market, sectors, sessions
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
    sector = sectors.sector_map([symbol], allow_network=True).get(symbol)
    etf = sectors.SECTOR_ETFS.get(sector)
    frames = {}
    for name in [x for x in (etf, "^VIX") if x]:   # a modell bemenete ne legyen csendben ures: elavultnal frissit
        frame = store.load(name, settings, universe=True)
        if frame.empty or frame.index[-1] < df.index[-1] - pd.Timedelta(days=7):
            try:
                feed.update([name], settings, universe=True)
                frame = store.load(name, settings, universe=True)
            except Exception as exc:
                log.warning("Celar-valoszinuseg: %s nem frissitheto: %s", name, exc)
        frames[name] = frame
    try:
        fund = fundamentals.fetch(symbol)
        hist = market.HistoricalEarnings(settings, allow_network=True)
        dates = hist.dates(symbol)
        hist.save()
        upcoming = market.EarningsCalendar(settings, allow_network=True).next_date(symbol)   # a jovobeli: a naptarbol
    except Exception as exc:                     # halozati hiba: a tarolt adattal
        log.warning("Celar-valoszinuseg: %s kiegeszito adata nem frissult: %s", symbol, exc)
        fund, dates, upcoming = fundamentals.load(symbol), [], None
    feats = daily_features(df, ctx.bench_for(symbol), frames.get(etf), fund, dates + ([upcoming] if upcoming else []),
                           frames.get("^VIX"))
    out = query(result, feats.iloc[-1], float(df["close"].iloc[-1]), side, float(target), days,
                None if stop is None else float(stop), excursions(df, feats["atr"]), len(df) - 1)
    try:                                         # a hirek csak tajekoztatasul (a #20 validacioja szerint)
        out["news"] = news.summary(news.features_for(symbol, df.index[-1]))
    except Exception:
        out["news"] = None
    return out | {"symbol": symbol, "as_of": f"{df.index[-1]:%Y-%m-%d}", "close": round(float(df["close"].iloc[-1]), 2),
                  "sector": sector, "side": side, "target": target, "days": days, "stop": stop}


def query(result: dict, feats_today: pd.Series, close: float, side: str, target: float, days: int,
          stop: float | None = None, exc: dict | None = None, t: int | None = None) -> dict:
    """A mai helyzet becslese. `feats_today`: daily_features utolso sora."""
    sign = 1 if side == "long" else -1
    atr = float(feats_today["atr"])
    d = sign * (target - close) / atr if atr > 0 else math.nan
    stop_d = NO_STOP if stop is None else sign * (close - stop) / atr
    if not (0 < d <= MAX_D) or not (1 <= days <= MAX_N) or (stop is not None and not 0 < stop_d < NO_STOP):
        return {"ok": False, "reason": "range", "d": None if math.isnan(d) else round(d, 2)}
    earn = feats_today.get("earn_days", math.nan)
    x = pd.DataFrame([feats_today.to_dict()
                      | {"d": d, "n": days, "stop_d": stop_d, "side": sign,
                         "earn_in": math.nan if pd.isna(earn) else float(earn <= days),
                         "own_hit": own_hit(exc, t, d, days, sign) if exc is not None and t is not None else math.nan}])
    if result.get("version") == 2:               # HAR-RV + elso athaladas (+ a valasztott csalad)
        x = prepare(x, result["har"])
        if not np.isfinite(x["p_an"].iloc[0]):
            return {"ok": False, "reason": "data", "d": round(d, 2)}
        base, prob = float(predict_any(result["analytic"], x)[0]), float(predict_any(result["model"], x)[0])
    else:
        base, prob = float(predict(result["models"]["A"], x)[0]), float(predict(result["final"], x)[0])
    vp, vy = np.array(result["valid"]["p"]), np.array(result["valid"]["y"])
    band = np.abs(vp - prob) <= 0.05
    n = int(band.sum())
    lo, hi = wilson(int(vy[band].sum()), n) if n else (None, None)
    contrib, prev = [], base
    if result["accepted"] and result.get("version") != 2:
        for g in result["groups"][1:]:
            pg = float(predict(result["models"][g], x)[0])
            contrib.append({"group": g, "label": GROUP_LABELS[g], "pp": round((pg - prev) * 100, 1)})
            prev = pg
    return {"ok": n >= MIN_BAND, "reason": None if n >= MIN_BAND else "few", "probability": round(prob, 3),
            "base": round(base, 3), "accepted": result["accepted"], "d": round(d, 2),
            "stop_d": None if stop is None else round(stop_d, 2), "band": {"n": n, "observed": round(float(vy[band].mean()), 3) if n else None,
                                                                         "lo": lo, "hi": hi},
            "contributions": contrib, "trained": result["trained"],
            "method": FAMILY_LABELS.get(result.get("family"), "alapesely") if result.get("version") == 2 else None}
