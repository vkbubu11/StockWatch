"""Piaci kornyezet: benchmark (rezsim, relativ ero) es earnings-naptar.

A swing long belepes nem csak a papir chartjan mulik:

  * REZSIM - ha a papir piaca a 200 napos atlaga alatt van, a bikas kitoresek
    sokkal gyakrabban buknak el. Ilyenkor nem veszunk. Papironkent (MarketContext):
    Nasdaq-100 tag -> QQQ, csak S&P 500 tag -> az S&P 500 index (^GSPC),
    egyik sem (csak a watchlisten) -> a papir sajat trendje (zaro > SMA200).
  * RELATIV ERO - a piacnal erosebb papir kitorese tobbet er.
  * EARNINGS - egy gyorsjelentes barmilyen chart-alakzatot felulir
    (lasd a MOD julius vegi -26 szazalekat). Jelentes elott nem lepunk be.

Mindharom hibaturo: ha az adat nem erheto el, a motor figyelmeztet, de nem
all le - a jelzes szovege megmondja, mit nem tudtunk ellenorizni.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from . import sessions, store
from .config import Settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
def load_benchmark(settings: Settings, refresh: bool = True) -> pd.DataFrame:
    """A benchmark gyertyai a universe cache-bol, szukseg eseten frissitve.

    A benchmark szandekosan NEM a figyelt papirok kozott tarolodik, igy nem
    jelenik meg a watchlistben es nem general formaciojelzest.
    """
    return load_index(settings.entry.benchmark, settings, refresh)


def load_index(symbol: str, settings: Settings, refresh: bool = True) -> pd.DataFrame:
    """Egy index / ETF gyertyai a universe cache-bol, ha elavult, frissitve."""
    symbol = symbol.upper()
    cached = store.load(symbol, settings, universe=True)

    if refresh:
        # zaras utan (16:05 NY) a mai gyertya is kell - a 16:15-os vegleges futas rezsimje a mai zaron alljon
        now = sessions.now_ny()
        today = now.normalize().tz_localize(None)
        closed_today = today.weekday() < 5 and (now.hour, now.minute) >= sessions.CLOSE_GRACE
        due = today if closed_today else today - pd.offsets.BDay(1)
        last = None if cached.empty else cached.index.max()
        stale = last is None or last < due
        if stale:
            try:
                from . import feed

                fresh = feed.update([symbol], settings, universe=True).get(symbol)
                if fresh is not None and not fresh.empty:
                    return fresh
            except Exception:
                log.exception("A benchmark (%s) frissitese nem sikerult", symbol)

    if cached.empty:
        log.warning("Nincs benchmark adat (%s) - a rezsim es relativ ero szuro kimarad.", symbol)
    return cached


@dataclass
class Regime:
    ok: bool | None          # None = nem tudjuk
    close: float | None
    sma200: float | None
    text: str
    name: str = ""           # a szuro neve a naploban, pl. "Piac (QQQ)" / "Sajat trend (SMA200)"


def market_regime(bench: pd.DataFrame, as_of: pd.Timestamp, subject: str = "benchmark") -> Regime:
    """Bikas-e a piac (vagy a papir sajat trendje) az adott napon (zaro > SMA200)."""
    if bench is None or bench.empty:
        return Regime(None, None, None, f"{subject} adat nem elerheto")

    series = bench["close"].loc[:as_of]
    if len(series) < 200:
        return Regime(None, None, None, f"tul rovid {subject} historia")

    close = float(series.iloc[-1])
    sma200 = float(series.iloc[-200:].mean())
    ok = close > sma200
    diff = (close / sma200 - 1) * 100
    word = "felett" if ok else "alatt"
    return Regime(ok, close, sma200, f"{subject} {diff:+.1f}%-kal az SMA200 {word}")


# ---------------------------------------------------------------------------
# Papironkenti rezsim: melyik index (vagy a papir sajat trendje)
# ---------------------------------------------------------------------------
NASDAQ_INDEX = "QQQ"            # Nasdaq-100 (a tagsag elsobbseget kap: AAPL -> QQQ)
SP500_INDEX = "^GSPC"           # S&P 500 index (SPX)
REGIME_INDEXES = (NASDAQ_INDEX, SP500_INDEX)
INDEX_LABELS = {NASDAQ_INDEX: "Nasdaq-100", SP500_INDEX: "S&P 500", "SPY": "S&P 500"}
SELF = "self"                   # a papir sajat trendje (egyik indexnek sem tagja)


@dataclass
class MarketContext:
    """A rezsim es a relativ ero papironkent: Nasdaq-100 tag -> QQQ, csak S&P 500 tag -> ^GSPC,
    egyik sem (csak a watchlisten) -> a rezsim a papir sajat trendje, a relativ ero az S&P 500-hoz."""

    benches: dict
    nasdaq: frozenset = frozenset()
    sp500: frozenset = frozenset()

    def index_of(self, symbol: str) -> str | None:
        symbol = symbol.upper()
        if symbol in self.nasdaq:
            return NASDAQ_INDEX
        if symbol in self.sp500:
            return SP500_INDEX
        return None

    def regime_source(self, symbol: str) -> str:
        return self.index_of(symbol) or SELF

    def bench_for(self, symbol: str) -> pd.DataFrame | None:
        """A relativ ero (es a naplo pillanatkepe) indexe."""
        frame = self.benches.get(self.index_of(symbol) or SP500_INDEX)
        return frame if frame is not None and not frame.empty else None

    def regime_frame(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame | None:
        """A rezsim gyertyai: az index, vagy (indexen kivuli papirnal) maga a papir."""
        index = self.index_of(symbol)
        return df if index is None else self.benches.get(index)

    def regime(self, symbol: str, df: pd.DataFrame, as_of: pd.Timestamp) -> Regime:
        index = self.index_of(symbol)
        if index is None:
            out = market_regime(df, as_of, subject="a papir")
            out.name = "Sajat trend (SMA200)"
        else:
            out = market_regime(self.benches.get(index), as_of, subject=INDEX_LABELS[index])
            out.name = f"Piac ({'QQQ' if index == NASDAQ_INDEX else INDEX_LABELS[index]})"
        return out

    @property
    def available(self) -> bool:
        return all(self.benches.get(i) is not None and not self.benches[i].empty for i in REGIME_INDEXES)


def load_context(settings: Settings, refresh: bool = True, allow_network: bool | None = None) -> MarketContext:
    """A ket index (QQQ, ^GSPC) gyertyai es az indextagsag. A tagsagot mindket indexre ismerni kell
    (akkor is, ha az indexszkenneles ki van kapcsolva), kulonben egy Nasdaq-100 tag S&P 500-asnak latszana."""
    from . import indexes

    allow_network = refresh if allow_network is None else allow_network
    members = indexes.IndexMembers(settings)
    nasdaq = frozenset(members.members("nasdaq100", allow_network=allow_network))
    sp500 = frozenset(members.members("sp500", allow_network=allow_network))
    benches = {symbol: load_index(symbol, settings, refresh) for symbol in REGIME_INDEXES}
    return MarketContext(benches, nasdaq, sp500)


def relative_strength(
    df: pd.DataFrame, bench: pd.DataFrame, as_of: pd.Timestamp, lookback: int = 60
) -> float | None:
    """A papir hozama minusz a benchmark hozama `lookback` napra, szazalekpontban."""
    if bench is None or bench.empty:
        return None
    own = df["close"].loc[:as_of]
    ref = bench["close"].loc[:as_of]
    if len(own) <= lookback or len(ref) <= lookback:
        return None
    own_ret = own.iloc[-1] / own.iloc[-1 - lookback] - 1
    ref_ret = ref.iloc[-1] / ref.iloc[-1 - lookback] - 1
    return float((own_ret - ref_ret) * 100)


# ---------------------------------------------------------------------------
# Earnings-naptar
# ---------------------------------------------------------------------------
class EarningsCalendar:
    """Kovetkezo gyorsjelentes datuma tickerenkent, napi cache-sel.

    A yfinance earnings vegpontja gyakran valtozik es neha ures - ezert:
      * naponta legfeljebb egyszer kerdezunk le papironkent,
      * hiba eseten a regi cache-t hasznaljuk,
      * ha semmi nincs, `None` - a motor ezt figyelmeztetesben jelzi.
    """

    def __init__(
        self, settings: Settings, allow_network: bool = True, read_only: bool = False
    ) -> None:
        self.path = settings.earnings_path
        self.allow_network = allow_network
        # Dry-run alatt a lekert datumokat hasznaljuk, de nem irjuk vissza.
        self.read_only = read_only
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Serult earnings cache - ujraepitjuk.")

    def next_date(self, symbol: str, as_of: date | None = None) -> date | None:
        as_of = as_of or date.today()
        entry = self.data.get(symbol.upper())

        fresh = entry is not None and entry.get("fetched") == date.today().isoformat()
        if not fresh and self.allow_network:
            dates = self._fetch(symbol)
            if dates is not None:
                entry = {"fetched": date.today().isoformat(), "dates": dates}
                self.data[symbol.upper()] = entry
                self._save()

        if not entry:
            return None
        upcoming = sorted(
            d for d in (date.fromisoformat(x) for x in entry.get("dates", [])) if d >= as_of
        )
        return upcoming[0] if upcoming else None

    def _fetch(self, symbol: str) -> list[str] | None:
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            dates: set[str] = set()

            try:
                frame = ticker.get_earnings_dates(limit=8)
                if frame is not None and not frame.empty:
                    dates.update(pd.DatetimeIndex(frame.index).tz_localize(None).strftime("%Y-%m-%d"))
            except Exception:
                log.debug("get_earnings_dates nem mukodott: %s", symbol)

            if not dates:
                calendar = ticker.calendar or {}
                raw = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
                for value in raw or []:
                    dates.add(pd.Timestamp(value).strftime("%Y-%m-%d"))

            return sorted(dates) if dates else None
        except Exception as exc:
            log.warning("Earnings datum lekerese sikertelen (%s): %s", symbol, exc)
            return None

    def _save(self) -> None:
        if self.read_only:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        except OSError:
            log.exception("Az earnings cache nem mentheto")


class HistoricalEarnings:
    """Multbeli gyorsjelentesi datumok - hogy az earnings-tilalom VISSZATESZTELHETO legyen.

    data/research/earnings_history.json, papironkent ~100 datum (~1 KB), 30 naponta
    frissul. Ahol nincs adat, ott a szuro "nem ismert" (nem blokkol), es a kutatas
    kiirja, hany esemenynel volt ismert a datum.
    """

    REFRESH_DAYS = 30

    def __init__(self, settings: Settings, allow_network: bool = True) -> None:
        from .config import DATA_DIR

        self.path = DATA_DIR / "research" / "earnings_history.json"
        self.allow_network = allow_network
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Serult earnings_history.json - ujratoltjuk.")

    def dates(self, symbol: str) -> list[date]:
        symbol = symbol.upper()
        entry = self.data.get(symbol)
        stale = entry is None or entry.get("fetched", "") < (date.today() - timedelta(days=self.REFRESH_DAYS)).isoformat()
        if stale and self.allow_network:
            fetched = self._fetch(symbol)
            if fetched is not None:
                dates, times = fetched
                entry = {"fetched": date.today().isoformat(), "dates": dates, "times": times}
                self.data[symbol] = entry
        return [date.fromisoformat(d) for d in (entry or {}).get("dates", [])]

    def times(self, symbol: str) -> dict[str, str]:
        """Datum -> a bejelentes idopontja (New York, "HH:MM"), ahol a forras megadta. Regi cache-ben nincs:
        ott az idopont nem ismert (az esemenytanulmany konzervativan a kovetkezo kereskedesi napot veszi)."""
        self.dates(symbol)
        return dict((self.data.get(symbol.upper()) or {}).get("times") or {})

    def _fetch(self, symbol: str) -> tuple[list[str], dict[str, str]] | None:
        try:
            import yfinance as yf

            frame = yf.Ticker(symbol).get_earnings_dates(limit=100)
            if frame is None or frame.empty:
                return None
            index = pd.DatetimeIndex(frame.index)
            local = index.tz_convert("America/New_York") if index.tz is not None else index
            dates = sorted(set(local.tz_localize(None).strftime("%Y-%m-%d")))
            times = {}
            if index.tz is not None:
                for ts in local:
                    if (ts.hour, ts.minute) != (0, 0):           # az ejfel = csak datum (idopont nelkul)
                        times[f"{ts:%Y-%m-%d}"] = f"{ts:%H:%M}"
            return dates, times
        except Exception as exc:
            log.warning("Historikus earnings datumok nem tolthetok le (%s): %s", symbol, exc)
            return None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=0), encoding="utf-8")


def next_earnings_days(dates: list[date], today: date) -> int | None:
    """Hany nap van a `today` utani (vagy aznapi) kovetkezo jelentesig - a historikus listabol."""
    upcoming = [d for d in dates if d >= today]
    return (min(upcoming) - today).days if upcoming else None


def days_until(target: date | None, today: date | None = None) -> int | None:
    if target is None:
        return None
    today = today or datetime.now().date()
    return (target - today).days
