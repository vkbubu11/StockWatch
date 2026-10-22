"""Konfiguracio betoltese: config/*.yaml + .env.

A titkok fajlja alapbol a projekt gyokereben levo .env. Szerveren a
STOCKWATCH_ENV_FILE kornyezeti valtozo egy kodon kivuli, csak a szolgaltatas
felhasznalojanak olvashato fajlra iranyitja (pl. /etc/stockwatch/stockwatch.env).

A beallitasok pydantic modellekkel validaltak, igy egy elgepelt kulcs vagy
rossz tipus mar indulaskor kiderul, nem fel ora futas utan.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LANGUAGES = ("hu", "en", "de", "es")          # a felulet es az ertesitesek nyelvei (i18n.py)
LOG_DIR = PROJECT_ROOT / "logs"
ENV_PATH = Path(os.getenv("STOCKWATCH_ENV_FILE") or PROJECT_ROOT / ".env")


class DataSettings(BaseModel):
    provider: str = "yahoo"
    interval: str = "1d"
    history_years: int = 10
    refresh_minutes: int = 15


class StorageSettings(BaseModel):
    ohlcv_retention_years: int = 10
    signals_max_mb: float = 5
    signals_backups: int = 3
    log_max_mb: float = 5
    log_backups: int = 3
    state_retention_days: int = 90
    max_data_mb: float = 100


class StructureSettings(BaseModel):
    zigzag_atr_mult: float = 1.5
    fractal_left: int = 3
    fractal_right: int = 3
    atr_period: int = 14


class LevelSettings(BaseModel):
    lookback_bars: int = 500
    cluster_atr_mult: float = 0.5
    cluster_min_pct: float = 0.003
    volume_profile_bins: int = 50
    recency_halflife_days: int = 90
    max_levels: int = 12
    round_number_enabled: bool = True


class PatternSettings(BaseModel):
    min_pattern_bars: int = 10
    max_pattern_bars: int = 180
    min_quality: float = 0.62
    tolerance_atr_mult: float = 0.7
    trendline_max_error_ratio: float = 0.12


class ForecastWeights(BaseModel):
    pattern: float = 0.35
    trend: float = 0.20
    momentum: float = 0.15
    levels: float = 0.20
    volume: float = 0.10

    def normalised(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("A forecast sulyok osszege nem lehet nulla.")
        return {k: v / total for k, v in raw.items()}


class ForecastSettings(BaseModel):
    weights: ForecastWeights = Field(default_factory=ForecastWeights)
    min_samples_for_probability: int = 30
    min_risk_reward: float = 1.5


class BacktestSettings(BaseModel):
    years: int = 10
    max_horizon_mult: float = 2.0
    max_horizon_bars: int = 120
    quality_buckets: list[float] = Field(default_factory=lambda: [0.55, 0.7, 0.85])


class EntrySettings(BaseModel):
    """A swing LONG belepesi motor szabalyai (entry.py).

    Minden kemeny szuro kikapcsolhato vagy hangolhato - a `entry-backtest`
    parancs megmutatja, melyik mennyit er a historiaban.
    """

    enabled: bool = True
    min_score: float = 50.0                 # 0-100; e alatt nincs riasztas
    alert_setups: bool = True               # "elokeszulet" (meg ki nem tort) jelzes is menjen-e
    cooldown_days: int = 10                 # ugyanarra a mintara ennyi napig nincs ismetles

    # --- kemeny szurok ---
    require_uptrend: bool = True            # zaro > SMA200 es EMA50 > EMA200
    rsi_min: float = 45.0
    rsi_max: float = 72.0
    min_breakout_volume: float = 0.0        # kitoresi volumen / 20 napos atlag; 0 = csak a pontszamban
    max_extension_atr: float = 1.5          # ennel messzebb a trigger folott mar ne uldozzuk
    min_risk_reward: float = 2.0
    min_stop_atr: float = 2.0               # a stop legalabb ennyi ATR-re legyen a belepestol
    max_stop_pct: float = 12.0              # ennel tagabb stop mar nem swing trade
    # --- momentum / erosseg szurok (kutatas: entry-research --study) - 0 / None = kikapcsolva ---
    min_high52_ratio: float = 0.0           # zaro / 52 hetes csucs legalabb ennyi (George-Hwang 2004)
    min_momentum_pct: float | None = None   # 12-1 honapos hozam legalabb ennyi % (Jegadeesh-Titman 1993)
    min_rel_strength: float | None = None   # 60 napos hozam - benchmark hozama, szazalekpont
    max_contraction: float = 0.0            # kitores elotti 10 nap atl. sav / elozo 100 nape (VCP), max.

    # --- belepes modja ---
    # Megerosites: a kitores napja utani zaro is a trigger felett legyen.
    # Kevesebb hamis kitores, cserebe kicsit rosszabb ar (entry-research).
    confirm_breakout: bool = True
    alert_retests: bool = False             # visszateszt-belepes kulon jelzese

    # --- kilepes (poziciokovetes es backtest) ---
    exit_mode: str = "partial"              # target | partial | trail | partial_trail
    partial_r: float = 1.0                  # a pozicio fele ennyi R nyeresegnel
    trail_atr: float = 3.0                  # kovetett stop tavolsaga (trail modoknal)
    track_positions: bool = True            # a jelzett trade-ek kovetese es ertesites

    # --- visszateszt ---
    retest_max_bars: int = 10               # kitores utan ennyi napig figyeljuk a visszatesztet
    retest_zone_atr: float = 0.6            # a trigger szint felett ennyi ATR-en belul kell erinteni

    # --- elokeszulet ---
    setup_max_distance_atr: float = 1.0     # formalodo mintanal ennyi ATR-re a triggertol

    # --- piaci kornyezet ---
    # "auto": az atnezett index ETF-je (Settings._resolve_benchmark), vagy egy konkret ticker
    benchmark: str = "auto"
    regime_filter: bool = True              # benchmark zaro > SMA200
    earnings_blackout_days: int = 7         # jelentes elott ennyi naptari napig nincs belepes
    earnings_unknown_blocks: bool = False   # ha nem tudjuk a datumot: tiltson-e

    # --- pozicio meretezes (csak a javaslathoz, nem kereskedik) ---
    account_size: float = 10000.0           # USD
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 25.0
    # eros jelzes nagyobb merettel: pontszam >= boost_score -> kockazat x boost_risk_mult (0 = ki)
    boost_score: float = 0.0
    boost_risk_mult: float = 1.5

    def risk_mult(self, score: float | None) -> float:
        """A kockazat szorzoja egy adott pontszamu jelzesre."""
        if self.boost_score > 0 and score is not None and score >= self.boost_score:
            return max(self.boost_risk_mult, 0.0)
        return 1.0

    # --- vegrehajtas modellezese a backtestben ---
    entry_max_gap_pct: float = 1.0          # masnapi nyito ennyivel lehet a zaro folott
    slippage_bps: float = 5.0               # oldalankent
    commission_bps: float = 0.0             # kotesi dij oldalankent (az oktatasi kiadasban nincs)

    @property
    def total_cost_bps(self) -> float:
        return self.slippage_bps + self.commission_bps


    @field_validator("exit_mode")
    @classmethod
    def _exit_mode(cls, value: str) -> str:
        allowed = ("target", "partial", "trail", "partial_trail")
        if value not in allowed:
            raise ValueError(f"exit_mode: {value} - lehetseges: {', '.join(allowed)}")
        return value


class ScanSettings(BaseModel):
    """A watchlisten tul atnezett indexek (indexes.py). Zaras utan egyszer futnak."""

    nasdaq100: bool = False         # a QQQ altal kovetett Nasdaq-100 osszes papirja
    sp500: bool = False             # az S&P 500 osszes papirja
    index_setups: bool = False      # elokeszulet-jelzes az indexpapirokra is (500 papiron sok zaj)
    refresh_members_days: int = 7   # az indexosszetetel ujraletoltese ennyi naponta
    # A "Legjobb belepo most" gomb listaja: watchlist | nasdaq100 | sp500 | indexes | all
    best_list: str = "watchlist"

    @field_validator("best_list")
    @classmethod
    def _best_list(cls, value: str) -> str:
        if value not in ("watchlist", "nasdaq100", "sp500", "indexes", "all"):
            raise ValueError(f"scan.best_list: {value} - lehetseges: watchlist, nasdaq100, sp500, indexes, all")
        return value


class PullbackParams(BaseModel):
    """A "Visszahuzas (RSI2)" motor szabalyai (meanrev.py) - a kutatas es az eles futas is ezt hasznalja."""

    rsi2_max: float = 2.0                   # jelzes: RSI(2) ez alatt
    trend_sma: int = 200                    # a papir zaroja e mozgoatlag felett (emelkedo trend)
    regime_filter: bool = True              # a papir indexenek (QQQ / S&P 500) vagy sajat trendjenek rezsimje
    limit_pct: float = 5.0                  # masnapi veteli limit ennyi %-kal a zaro alatt (egynapos)
    stop_atr: float = 3.0                   # stop: belepo - ennyi ATR
    atr_period: int = 10
    exit_crsi: float = 50.0                 # kilepes: ha a zaron ConnorsRSI(3,2,100) e felett -> masnap nyitaskor
    exit_sma: int = 0                       # kilepes AKKOR IS, ha a zaro e mozgoatlag felett van (0 = ki; ETF: 5)
    time_stop: int = 10                     # legfeljebb ennyi kereskedesi nap tartas
    min_price: float = 5.0
    min_dollar_volume: float = 20_000_000   # 20 napos atlagos dollarforgalom
    earnings_blackout_days: int = 5         # nincs jelzes, ha ennyi napon belul gyorsjelentes jon
    # 0 = korlat nelkul (a validalas szerint a napi limit rontja az eredmenyt: a -5%-os limitek tobbsege
    # nem teljesul, es a rangsor elejen a legmelyebben zuhano, legrosszabb jeloltek maradnak)
    max_signals_per_day: int = 0            # naponta legfeljebb ennyi jelzes (rangsor: legalacsonyabb RSI2)
    max_open: int = 0                       # egyszerre legfeljebb ennyi nyitott / fuggo kotes (0 = korlat nelkul)
    cost_bps: float = 5.0                   # oldalankent (csuszas + spread) - a netto eredmenyhez
    commission_bps: float = 0.0             # kotesi dij oldalankent (az oktatasi kiadasban nincs)

    @property
    def total_cost_bps(self) -> float:
        return self.cost_bps + self.commission_bps

    @model_validator(mode="after")
    def _check(self) -> "PullbackParams":
        if not 0 < self.rsi2_max <= 50:
            raise ValueError("pullback.rsi2_max: 0 es 50 kozott")
        if not 0 <= self.limit_pct < 30:
            raise ValueError("pullback.limit_pct: 0 es 30 kozott")
        if not 0.5 <= self.stop_atr <= 10:
            raise ValueError("pullback.stop_atr: 0,5 es 10 kozott")
        if not 0 <= self.exit_sma <= 50:
            raise ValueError("pullback.exit_sma: 0 (ki) es 50 kozott")
        if not 1 <= self.time_stop <= 60 or not 0 <= self.max_signals_per_day <= 100 or not 0 <= self.max_open <= 500:
            raise ValueError("pullback: ervenytelen idokorlat / napi limit / max. nyitott")
        return self

    @property
    def daily_cap(self) -> int:
        return self.max_signals_per_day or 1_000_000

    @property
    def open_cap(self) -> int:
        return self.max_open or 1_000_000


class StrategySwitch(BaseModel):
    enabled: bool = True


class PullbackStrategy(StrategySwitch):
    params: PullbackParams = Field(default_factory=PullbackParams)


# Likvid reszveny-ETF-ek: szeles index, meret, szektor, nemzetkozi. Nincs gyorsjelentes, nincs egyedi
# csodkockazat, es a nagy ETF-ek nem szunnek meg (a backtestben nincs tulelesi torzitas).
ETF_UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "MDY", "RSP", "XLK", "XLF", "XLV", "XLI", "XLY", "XLP", "XLE", "XLU",
                "XLB", "XLRE", "XLC", "SMH", "IBB", "KRE", "ITB", "EFA", "EEM", "EWJ"]


def _etf_params() -> "PullbackParams":
    """Az ETF visszahuzas szabalyai - a futtatas ELOTT rogzitve, a szakirodalom alapjan (Connors-Alvarez:
    High Probability ETF Trading; az RSI(2) visszahuzas ETF-eken), nem a sajat adatunkra hangolva."""
    return PullbackParams(
        rsi2_max=10.0,                  # ETF-en ritkabb a melyrepules: RSI(2) < 10 mar eros tuladottsag
        limit_pct=1.0,                  # az ETF napi mozgasa ~1-1,5%: a -5%-os limit szinte soha nem teljesulne
        stop_atr=5.0,                   # tag, katasztrofa-stop: index-ETF-en a szoros stop rontja a talalati aranyt
        exit_crsi=50.0, exit_sma=5,     # kilepes az elso eros visszapattanaskor (zaro > SMA5 vagy CRSI > 50)
        time_stop=10,
        min_dollar_volume=50_000_000,
        earnings_blackout_days=0,       # az ETF-nek nincs gyorsjelentese
        max_signals_per_day=2,          # az ETF-ek egyutt mozognak: egy esesnapon ne vegyunk 10 hasonlot
        max_open=4,
        cost_bps=3.0,                   # likvid ETF: szuk spread
    )


class EtfStrategy(PullbackStrategy):
    """ETF visszahuzas: a Visszahuzas motorja likvid reszveny-ETF-eken, ETF-re szabott szabalyokkal."""

    params: PullbackParams = Field(default_factory=_etf_params)
    symbols: list[str] = Field(default_factory=lambda: list(ETF_UNIVERSE))

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        out = list(dict.fromkeys(str(v).strip().upper() for v in value if str(v).strip()))
        if not out or len(out) > 100:
            raise ValueError("etf.symbols: 1-100 ticker")
        return out


def _deep_params() -> "PullbackParams":
    """A Mely visszahuzas szabalyai: a Visszahuzas (RSI2) bemenete valtozatlan, de a stop TAG (5 ATR).

    A 2026-10-i validacio (514 indextag, `meanrev-research --deep`) szerint a 3 ATR-es stop a
    visszahuzas-kotesek egy reszet a melyponton zarja ki, amelyek utana visszapattannanak: 5 ATR-rel
    tanito 72,8% -> 76,2%, validacio 75,0% (valtozatlan), a profit-faktor 2,5 -> 3,6, es a varhato
    ertek bootstrap also hatara mindket idoszakban pozitiv lett. A valasztas CSAK a tanito idoszakon
    tortent; a holdout (2025.04-) meg zarolt. Connors-Alvarez is azt talalta, hogy a szoros stop
    a visszahuzas-strategiak talalati aranyat rontja."""
    return PullbackParams(stop_atr=5.0)


class DeepPullbackStrategy(PullbackStrategy):
    """Mely visszahuzas: RSI(2) < 2, limit -5%, TAG (5 ATR) stop - a Nasdaq-100 / S&P 500 tagjain.
    Alapbol fut (virtualis kovetes), de nem ertesit: a jelzesei a Visszahuzaseival egyeznek."""

    alerts: bool = False
    params: PullbackParams = Field(default_factory=_deep_params)


class SandboxStrategy(StrategySwitch):
    """Szabad kutatoi strategia: SOHA nincs a paper fiokhoz kotve."""

    alerts: bool = False
    engine: str = "pullback"                                     # pullback | breakout
    params: PullbackParams = Field(default_factory=PullbackParams)
    entry: dict = Field(default_factory=dict)                    # breakout motornal: EntrySettings-felulirasok

    @field_validator("engine")
    @classmethod
    def _engine(cls, value: str) -> str:
        if value not in ("pullback", "breakout"):
            raise ValueError(f"sandbox.engine: {value} - lehetseges: pullback, breakout")
        return value

    @field_validator("entry")
    @classmethod
    def _entry(cls, value: dict) -> dict:
        unknown = set(value) - set(EntrySettings.model_fields)
        if unknown:
            raise ValueError(f"sandbox.entry: ismeretlen kulcs: {', '.join(sorted(unknown))}")
        EntrySettings(**value)                                   # tipus- es ertekellenorzes
        return value


class MomentumParams(BaseModel):
    """A "Momentum (12-1)" rotacio szabalyai (momentum.py) - a kutatas es az eles futas is ezt hasznalja.

    Forras: Kakushadze-Serur: 151 Trading Strategies (2018), 3.1 ar-momentum (12 honapos hozam a
    legutobbi honap nelkul, havi rebalansz, a legjobb papirok long). A 2026-10-i validacio
    (docs/MOMENTUM-STRATEGY-2026-10.md) a TANITO idoszakon valasztotta a 10 papirt, a 20-as puffert
    es a stop nelkuli valtozatot; a rezsimszuros "dual momentum" (4.1.2) es a stopok rontottak."""

    lookback: int = 252                     # a momentum ablaka (kereskedesi nap, ~12 honap)
    skip: int = 21                          # a legutobbi ~1 honap kimarad (rovid tavu visszafordulas)
    top_n: int = 10                         # ennyi papir a portfolioban, egyenlo sullyal
    buffer: int = 20                        # a tartott papir addig marad, amig a rangja ezen belul van
    stop_pct: float = 0.0                   # katasztrofa-stop a belepo alatt %-ban (0 = nincs; a validacio szerint ront)
    r_unit_pct: float = 20.0                # stop nelkul az R-statisztika egysege: 1R = ennyi % (csak kimutatas)
    min_price: float = 5.0
    min_dollar_volume: float = 20_000_000   # 20 napos atlagos dollarforgalom
    cost_bps: float = 5.0                   # oldalankent (csuszas + spread)
    commission_bps: float = 0.0             # kotesi dij oldalankent (az oktatasi kiadasban nincs)

    @property
    def total_cost_bps(self) -> float:
        return self.cost_bps + self.commission_bps

    @model_validator(mode="after")
    def _check(self) -> "MomentumParams":
        if not 60 <= self.lookback <= 504 or not 0 <= self.skip < self.lookback - 20:
            raise ValueError("momentum.lookback: 60-504 nap, skip: 0 es lookback-20 kozott")
        if not 1 <= self.top_n <= 50 or not self.top_n <= self.buffer <= 200:
            raise ValueError("momentum.top_n: 1-50, buffer: top_n es 200 kozott")
        if not 0 <= self.stop_pct < 90 or not 1 <= self.r_unit_pct <= 90:
            raise ValueError("momentum.stop_pct: 0-90 (0 = ki), r_unit_pct: 1-90")
        return self


class MomentumStrategy(StrategySwitch):
    """Momentum (12-1): havi rotacio a Nasdaq-100 + S&P 500 tagjai (es a watchlist) kozott."""

    params: MomentumParams = Field(default_factory=MomentumParams)


STRATEGY_IDS = ("breakout", "pullback", "deep", "etf", "momentum", "sandbox")
PAPER_STRATEGIES = ("breakout", "pullback", "deep", "etf", "momentum")   # a homokozo soha nem kotheto a paper fiokhoz
STRATEGY_LABELS = {"breakout": "Kitores", "pullback": "Visszahuzas (RSI2)", "deep": "Mely visszahuzas",
                   "etf": "ETF visszahuzas", "momentum": "Momentum (12-1)", "sandbox": "Homokozo"}


class StrategiesSettings(BaseModel):
    """Tobb strategia egy rendszerben. A beallitas a config/strategies.yaml-ban (a felulet irja)."""

    paper: str = "breakout"          # melyik strategia van a paper fiokhoz kotve (broker.auto_trade a fokapcsolo)
    breakout: StrategySwitch = Field(default_factory=StrategySwitch)
    pullback: PullbackStrategy = Field(default_factory=PullbackStrategy)
    deep: DeepPullbackStrategy = Field(default_factory=DeepPullbackStrategy)
    etf: EtfStrategy = Field(default_factory=EtfStrategy)
    momentum: MomentumStrategy = Field(default_factory=MomentumStrategy)
    sandbox: SandboxStrategy = Field(default_factory=SandboxStrategy)

    @field_validator("paper")
    @classmethod
    def _paper(cls, value: str) -> str:
        if value not in PAPER_STRATEGIES:
            raise ValueError(f"strategies.paper: {value} - lehetseges: {', '.join(PAPER_STRATEGIES)} "
                             "(a homokozo nem kotheto a paper fiokhoz)")
        return value

    def get(self, strategy: str) -> StrategySwitch:
        return getattr(self, strategy)


BENCHMARK_NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq-100", "^GSPC": "S&P 500"}


def benchmark_for(scan: "ScanSettings") -> str:
    """Az atnezett index ETF-je. Mindket indexnel az S&P 500 a szelesebb piac."""
    return "SPY" if scan.sp500 else "QQQ"


class PracticeSettings(BaseModel):
    """Gyakorlo mod (practice/): a short kotes kolcsonzesi dija es a dontes-itelet kuszobei."""

    borrow_bps_day: float = 0.0         # short: kolcsonzesi dij naponta, bps a belepo erteken (0 = nincs)
    good_r: float = 1.0                 # egy kihagyott helyzet "jo" volt, ha valamelyik alternativa legalabb ennyi R
    tight_stop_atr: float = 1.0         # ennel szorosabb stop: "tul szoros" jelzes
    min_rr: float = 1.0                 # ennel kisebb cel/kockazat arany: jelzes
    better_r: float = 0.5               # a "jobb stop" osszevetes csak legalabb ennyi R javulasnal jelenik meg
    # a feladatok forrasa: synthetic (practice/synthetic.py, adatlicenc nelkul) | real (letoltott arfolyam -
    # nyilvanos szolgaltatasban csak licencelt forrasbol)
    source: str = "synthetic"
    synthetic_symbols: int = 300        # ennyi szintetikus papir a keszletben
    # pontok a dontes minosege szerint (#34) - nem a profit szerint; a pontnak nincs penzbeli erteke
    points: dict[str, int] = Field(default_factory=lambda: {"good": 10, "unlucky": 7, "lucky": 3, "bad": 0,
                                                            "tight": -2, "rr": -1})

    @model_validator(mode="after")
    def _check(self) -> "PracticeSettings":
        if self.source not in ("synthetic", "real"):
            raise ValueError("practice.source: synthetic vagy real")
        if set(self.points) != {"good", "unlucky", "lucky", "bad", "tight", "rr"}:
            raise ValueError("practice.points: good, unlucky, lucky, bad, tight, rr")
        if not self.points["good"] >= self.points["unlucky"] >= self.points["lucky"] >= self.points["bad"] >= 0:
            raise ValueError("practice.points: good >= unlucky >= lucky >= bad >= 0 (a szerencse kevesebbet er)")
        if not 10 <= self.synthetic_symbols <= 2000:
            raise ValueError("practice.synthetic_symbols: 10 es 2000 kozott")
        if not 0 <= self.borrow_bps_day <= 100:
            raise ValueError("practice.borrow_bps_day: 0 es 100 kozott")
        if not 0 < self.good_r <= 10 or not 0 < self.tight_stop_atr <= 5:
            raise ValueError("practice: good_r 0-10, tight_stop_atr 0-5")
        if not 0 < self.min_rr <= 10 or not 0 < self.better_r <= 10:
            raise ValueError("practice: min_rr 0-10, better_r 0-10")
        return self


class Settings(BaseModel):
    data: DataSettings = Field(default_factory=DataSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    structure: StructureSettings = Field(default_factory=StructureSettings)
    levels: LevelSettings = Field(default_factory=LevelSettings)
    patterns: PatternSettings = Field(default_factory=PatternSettings)
    forecast: ForecastSettings = Field(default_factory=ForecastSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    entry: EntrySettings = Field(default_factory=EntrySettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    strategies: StrategiesSettings = Field(default_factory=StrategiesSettings)
    practice: PracticeSettings = Field(default_factory=PracticeSettings)

    @model_validator(mode="after")
    def _resolve_benchmark(self) -> "Settings":
        """`entry.benchmark: auto` -> az atnezett index ETF-je: S&P 500 -> SPY, kulonben QQQ.
        A rezsim-szuro, a relativ ero es a felulet kijelzese is ezt hasznalja."""
        if self.entry.benchmark.strip().lower() == "auto":
            self.entry.benchmark = benchmark_for(self.scan)
        else:
            self.entry.benchmark = self.entry.benchmark.strip().upper()
        return self

    def sandbox_breakout(self) -> "Settings | None":
        """A homokozo kitores-motorjanak beallitasa (az elo `entry` + a homokozo felulirasai), vagy None,
        ha a homokozo nem kitoressel fut."""
        sandbox = self.strategies.sandbox
        if not (sandbox.enabled and sandbox.engine == "breakout"):
            return None
        return self.model_copy(update={"entry": EntrySettings(**(self.entry.model_dump() | sandbox.entry))})

    # --- szarmaztatott utvonalak ---
    @property
    def entry_state_path(self) -> Path:
        return DATA_DIR / "entry_state.json"

    @property
    def index_members_path(self) -> Path:
        return DATA_DIR / "index_members.json"

    @property
    def index_scan_path(self) -> Path:
        return DATA_DIR / "index_scan.json"

    @property
    def earnings_path(self) -> Path:
        return DATA_DIR / "earnings.json"

    @property
    def ohlcv_dir(self) -> Path:
        return DATA_DIR / "ohlcv"

    @property
    def universe_dir(self) -> Path:
        return DATA_DIR / "universe"

    @property
    def signals_path(self) -> Path:
        return DATA_DIR / "signals.jsonl"

    @property
    def state_path(self) -> Path:
        return DATA_DIR / "state.json"

    @property
    def calibration_path(self) -> Path:
        return DATA_DIR / "calibration.json"

    @property
    def log_path(self) -> Path:
        return LOG_DIR / "app.log"


class Watchlist(BaseModel):
    symbols: list[str]

    @field_validator("symbols")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        cleaned = [s.strip().upper() for s in value if s and s.strip()]
        if not cleaned:
            raise ValueError("A watchlist ures.")
        # sorrendtarto duplikatum-szures
        return list(dict.fromkeys(cleaned))


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


STRATEGIES_PATH = CONFIG_DIR / "strategies.yaml"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    load_dotenv(ENV_PATH)
    raw = _read_yaml(CONFIG_DIR / "settings.yaml")
    # a strategiak beallitasat a felulet irja kulon fajlba (a settings.yaml kommentjei igy megmaradnak)
    if STRATEGIES_PATH.exists():
        raw["strategies"] = _read_yaml(STRATEGIES_PATH)
    return Settings(**raw)


def yaml_tickers(values) -> list[str]:
    """Tickerlista YAML-bol. A YAML 1.1 az idezojel nelkuli ON / YES / NO / Y szavakat
    logikai erteknek olvassa - ezeket nem talalgatjuk, hanem jelezzuk (idezojel kell)."""
    import logging

    out = []
    for value in values or []:
        if isinstance(value, str) and value.strip():
            out.append(value.strip().upper())
        elif isinstance(value, bool):
            logging.getLogger(__name__).warning(
                "A tickerlistaban egy idezojel nelkuli ON/YES/NO/Y szo logikai ertekke valt - "
                "tedd idezojelbe (pl. \"ON\").")
    return list(dict.fromkeys(out))


@lru_cache(maxsize=1)
def load_watchlist() -> Watchlist:
    raw = _read_yaml(CONFIG_DIR / "watchlist.yaml")
    return Watchlist(**raw) if raw else Watchlist(symbols=["AAPL"])


def env_value(key: str) -> str | None:
    """Egy titok mindig frissen a titokfajlbol (futas kozben is valtozhat), kulonben a kornyezetbol."""
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    if key in values:                  # a fajl az irany: a feluletrol torolt (ures) titok ne essen vissza
        return values[key] or None     # a folyamat inditaskor betoltott, regi kornyezeti ertekere
    return os.getenv(key) or None


def ensure_dirs() -> None:
    for path in (DATA_DIR, DATA_DIR / "ohlcv", DATA_DIR / "universe", LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
