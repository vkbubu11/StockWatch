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


class AlertSettings(BaseModel):
    console: bool = True
    jsonl: bool = True
    telegram: bool = True
    discord: bool = True          # csak akkor aktiv, ha DISCORD_WEBHOOK_URL be van allitva
    webhook: bool = True          # csak akkor aktiv, ha ALERT_WEBHOOK_URL be van allitva
    send_chart: bool = True
    heartbeat: bool = True        # napi eletjel zaras utan + uzenet a monitor indulasakor
    quiet_hours: list[str] = Field(default_factory=list)
    min_seconds_between: float = 5
    # A regi, minden formacioallapotra kuldott riasztas (bearish is).
    # A swing long belepesi jelzesektol fuggetlen.
    pattern_events: bool = True
    # Milyen iranyu jelzesek menjenek ki:
    #   long  - csak a swing long belepesi jelzesek (alap)
    #   short - csak a medves alakzatok lefele torese (nincs short belepesi motor)
    #   both  - mindketto
    # A poziciokovetes, a sajat poziciok es a paper trading ertesitesei mindig mennek.
    direction: str = "long"

    @field_validator("direction")
    @classmethod
    def _direction(cls, value: str) -> str:
        if value not in ("long", "short", "both"):
            raise ValueError(f"direction: {value} - lehetseges: long, short, both")
        return value

    @property
    def want_long(self) -> bool:
        return self.direction in ("long", "both")

    @property
    def want_short(self) -> bool:
        return self.direction in ("short", "both")


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


    @field_validator("exit_mode")
    @classmethod
    def _exit_mode(cls, value: str) -> str:
        allowed = ("target", "partial", "trail", "partial_trail")
        if value not in allowed:
            raise ValueError(f"exit_mode: {value} - lehetseges: {', '.join(allowed)}")
        return value


class LabSettings(BaseModel):
    """Strategia-labor (lab.py) es a gepi tanulas arnyek-uzeme (shadow.py)."""

    # auto: csak ha a legutobbi walk-forward jelentes szerint a modell mintan kivul jobb a mostani
    #       szuroknel ("better") | on: ha van mentett modell | off: soha
    # Az arnyek-uzemben a modell becslese a jelzesek melle kerul es naplozodik - de SEMMIROL nem dont.
    shadow: str = "auto"

    @field_validator("shadow")
    @classmethod
    def _shadow(cls, value: str) -> str:
        if value not in ("auto", "on", "off"):
            raise ValueError(f"lab.shadow: {value} - lehetseges: auto, on, off")
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


class BrokerSettings(BaseModel):
    """Alpaca PAPER kereskedes (broker.py). A kulcsok a .env-ben vannak, nem itt."""

    auto_trade: bool = False        # a monitor maga kuldi-e be a belepesi jelzeseket
    size_from_account: bool = True  # darabszam a paper szamla toke-jebol (kulonben entry.account_size)
    max_open_trades: int = 5        # egyszerre ennyi nyitott / fuggo broker-trade (Visszahuzas: csak a teljesult)
    # Kitores: a belepo csak a NYITASKOR teljesulhet (mint a backtestben). Ha a nyitas utan ennyi perccel
    # sem teljesult (a nyito a limit folott volt), visszavonjuk. 0 = ki (a limit egesz nap el).
    entry_window_min: int = 5
    # Eles kereskedes elotti kapu (stratégiankent, a lezart paper trade-ekbol)
    live_gate_min_trades: int = 50  # ennyi lezart paper trade kell a donteshez
    live_gate_win: float = 0.70     # nyeresi arany legalabb
    live_gate_pf: float = 1.3       # profit-faktor legalabb

    @model_validator(mode="after")
    def _check(self) -> "BrokerSettings":
        if not 0 <= self.entry_window_min <= 390:
            raise ValueError("broker.entry_window_min: 0 es 390 perc kozott")
        if not 0 < self.live_gate_win < 1 or self.live_gate_min_trades < 1 or self.live_gate_pf <= 0:
            raise ValueError("broker.live_gate_*: ervenytelen kuszob")
        return self


class PullbackParams(BaseModel):
    """A "Visszahuzas (RSI2)" motor szabalyai (meanrev.py) - a kutatas es az eles futas is ezt hasznalja."""

    rsi2_max: float = 2.0                   # jelzes: RSI(2) ez alatt
    trend_sma: int = 200                    # a papir zaroja e mozgoatlag felett (emelkedo trend)
    regime_filter: bool = True              # a papir indexenek (QQQ / S&P 500) vagy sajat trendjenek rezsimje
    limit_pct: float = 5.0                  # masnapi veteli limit ennyi %-kal a zaro alatt (egynapos)
    stop_atr: float = 3.0                   # stop: belepo - ennyi ATR
    atr_period: int = 10
    exit_crsi: float = 50.0                 # kilepes: ha a zaron ConnorsRSI(3,2,100) e felett -> masnap nyitaskor
    time_stop: int = 10                     # legfeljebb ennyi kereskedesi nap tartas
    min_price: float = 5.0
    min_dollar_volume: float = 20_000_000   # 20 napos atlagos dollarforgalom
    earnings_blackout_days: int = 5         # nincs jelzes, ha ennyi napon belul gyorsjelentes jon
    # 0 = korlat nelkul (a validalas szerint a napi limit rontja az eredmenyt: a -5%-os limitek tobbsege
    # nem teljesul, es a rangsor elejen a legmelyebben zuhano, legrosszabb jeloltek maradnak)
    max_signals_per_day: int = 0            # naponta legfeljebb ennyi jelzes (rangsor: legalacsonyabb RSI2)
    max_open: int = 0                       # egyszerre legfeljebb ennyi nyitott / fuggo kotes (0 = korlat nelkul)
    cost_bps: float = 5.0                   # oldalankent (jutalek + csuszas) - a netto eredmenyhez

    @model_validator(mode="after")
    def _check(self) -> "PullbackParams":
        if not 0 < self.rsi2_max <= 50:
            raise ValueError("pullback.rsi2_max: 0 es 50 kozott")
        if not 0 <= self.limit_pct < 30:
            raise ValueError("pullback.limit_pct: 0 es 30 kozott")
        if not 0.5 <= self.stop_atr <= 10:
            raise ValueError("pullback.stop_atr: 0,5 es 10 kozott")
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
    alerts: bool = True             # Discord / Telegram ertesites (a naploba mindig bekerul)


class PullbackStrategy(StrategySwitch):
    params: PullbackParams = Field(default_factory=PullbackParams)


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


STRATEGY_IDS = ("breakout", "pullback", "sandbox")
PAPER_STRATEGIES = ("breakout", "pullback")          # a homokozo soha nem kotheto a paper fiokhoz
STRATEGY_LABELS = {"breakout": "Kitores", "pullback": "Visszahuzas (RSI2)", "sandbox": "Homokozo"}


class StrategiesSettings(BaseModel):
    """Tobb strategia egy rendszerben. A beallitas a config/strategies.yaml-ban (a felulet irja)."""

    paper: str = "breakout"          # melyik strategia van a paper fiokhoz kotve (broker.auto_trade a fokapcsolo)
    breakout: StrategySwitch = Field(default_factory=StrategySwitch)
    pullback: PullbackStrategy = Field(default_factory=PullbackStrategy)
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


class Settings(BaseModel):
    data: DataSettings = Field(default_factory=DataSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    structure: StructureSettings = Field(default_factory=StructureSettings)
    levels: LevelSettings = Field(default_factory=LevelSettings)
    patterns: PatternSettings = Field(default_factory=PatternSettings)
    forecast: ForecastSettings = Field(default_factory=ForecastSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    entry: EntrySettings = Field(default_factory=EntrySettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    lab: LabSettings = Field(default_factory=LabSettings)
    strategies: StrategiesSettings = Field(default_factory=StrategiesSettings)

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
    def positions_path(self) -> Path:
        return DATA_DIR / "positions.json"

    @property
    def index_members_path(self) -> Path:
        return DATA_DIR / "index_members.json"

    @property
    def index_scan_path(self) -> Path:
        return DATA_DIR / "index_scan.json"

    @property
    def broker_path(self) -> Path:
        return DATA_DIR / "broker.json"

    @property
    def holdings_path(self) -> Path:
        return DATA_DIR / "holdings.json"

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


def save_strategies(strategies: StrategiesSettings) -> None:
    """A strategiak beallitasanak mentese (validalt modellbol, atomikusan)."""
    STRATEGIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STRATEGIES_PATH.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("# A feluletrol irt strategia-beallitasok (Strategiak ful). Kezzel is szerkesztheto.\n")
        yaml.safe_dump(strategies.model_dump(), fh, allow_unicode=True, sort_keys=False)
    tmp.replace(STRATEGIES_PATH)
    load_settings.cache_clear()


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


def save_watchlist(symbols: list[str]) -> None:
    """A dashboard oldalsavja ezen keresztul irja vissza a listat."""
    watchlist = Watchlist(symbols=symbols)
    path = CONFIG_DIR / "watchlist.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"symbols": watchlist.symbols}, fh, allow_unicode=True)
    load_watchlist.cache_clear()


def telegram_credentials() -> tuple[str | None, str | None]:
    """A titkok kizarolag kornyezeti valtozobol jonnek, soha nem a YAML-bol."""
    load_dotenv(ENV_PATH)
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")


def webhook_url() -> str | None:
    """Google Chat / Slack / Discord incoming webhook - kizarolag a .env-bol."""
    load_dotenv(ENV_PATH)
    return os.getenv("ALERT_WEBHOOK_URL") or None


def discord_webhook_url() -> str | None:
    """Discord webhook - kizarolag a titokfajlbol. A regi ALERT_WEBHOOK_URL-ben megadott
    Discord cimet is ez a csatorna viszi (kartyaval, keppel)."""
    url = env_value("DISCORD_WEBHOOK_URL")
    if url:
        return url
    legacy = webhook_url() or ""
    return legacy if "/api/webhooks/" in legacy and "discord" in legacy else None


ALPACA_KEYS = ("ALPACA_ENDPOINT", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY")


def alpaca_credentials() -> tuple[str | None, str | None, str | None]:
    """(endpoint, key, secret) - mindig frissen a .env-bol, mert a feluletrol
    futas kozben is valtozhat (a kornyezeti valtozo ilyenkor elavult lenne)."""
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return tuple((values.get(k) or os.getenv(k) or None) for k in ALPACA_KEYS)  # type: ignore[return-value]


def save_env(updates: dict[str, str]) -> None:
    """Kulcsok beirasa / csereje a .env-ben; a tobbi sor (es komment) erintetlen."""
    for key, value in updates.items():
        if not key.isidentifier() or any(c in str(value) for c in "\r\n"):
            raise ValueError(f"Ervenytelen .env ertek: {key}")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    # atomi csere; a titokfajl csak a tulajdonosanak olvashato (0600)
    tmp = ENV_PATH.with_name(ENV_PATH.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_PATH)


def env_value(key: str) -> str | None:
    """Egy titok mindig frissen a titokfajlbol (futas kozben is valtozhat), kulonben a kornyezetbol."""
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return values.get(key) or os.getenv(key) or None


def ensure_dirs() -> None:
    for path in (DATA_DIR, DATA_DIR / "ohlcv", DATA_DIR / "universe", LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
