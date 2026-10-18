"""Napi eletjel: minden zaras utani futas vegen rovid osszefoglalo a kulso csatornakra
(Discord / Telegram / webhook).

Ha egy hetkoznap este nem jon meg, a monitor nem fut (vagy a szerver all) - igy
nem kell a csendbol talalgatni, hogy "nincs jelzes" vagy "nincs rendszer".

  * a zaras utani futas lepesei kulon-kulon vedettek: ha egy elhasal, a tobbi lefut,
    es az eletjel akkor is kimegy - "FIGYELEM" allapottal es a hiba megnevezesevel
  * a jelzesnaploba (signals.jsonl) NEM kerul, a csendes idoszak nem tartja vissza
  * a monitor indulasakor is szol (ujrainditas, szerver-ujraindulas), de legfeljebb
    START_NOTICE_EVERY-nkent egyszer, hogy egy ujrainditasi hurok ne arassza el a csatornat
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import broker, housekeeping, positions, sessions
from .alerts import Alert
from .config import DATA_DIR, Settings

log = logging.getLogger(__name__)

MARKET_TZ = "America/New_York"
START_NOTICE_EVERY = pd.Timedelta(minutes=30)
STATE_PATH = DATA_DIR / "heartbeat.json"          # 1 kis JSON, fix meret

EVENT_GROUPS = (("entry_", "belepes"), ("pos_", "kovetett"), ("broker_", "paper"),
                ("hold_", "sajat poz."))


class ErrorCounter(logging.Handler):
    """A naplo hibainak / figyelmezteteseinek szamlaloja a legutobbi eletjel ota."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.errors = 0
        self.warnings = 0
        self.last_error = ""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.errors += 1
            self.last_error = f"{record.name.rsplit('.', 1)[-1]}: {record.getMessage()}"[:160]
        else:
            self.warnings += 1

    def reset(self) -> None:
        self.errors = self.warnings = 0
        self.last_error = ""


@dataclass
class DayReport:
    """A zaras utani futas eredmenye - az eletjel ebbol kesz."""

    day: pd.Timestamp
    watchlist_total: int = 0
    watchlist_analysed: int | None = None
    last_bar: pd.Timestamp | None = None
    index: dict | None = None
    broker_events: int = 0
    failures: list[str] = field(default_factory=list)
    late: bool = False              # potlolagos futas (a monitor nem futott a zaraskor)


trading_day = sessions.trading_day        # a tesztek ezen a neven csereljek


def _events_line(sent: Counter) -> str:
    total = sum(sent.values())
    if not total:
        return "Mai ertesitesek: 0"
    groups = Counter()
    for event, n in sent.items():
        label = next((name for prefix, name in EVENT_GROUPS if event.startswith(prefix)), "alakzat")
        groups[label] += n
    return f"Mai ertesitesek: {total} (" + ", ".join(f"{k} {v}" for k, v in groups.most_common()) + ")"


def _broker_lines(settings: Settings) -> list[str]:
    client = broker.AlpacaClient.from_env()
    if client is None:
        return ["Paper szamla: nincs beallitva"]
    book = broker.BrokerBook(settings, read_only=True)
    stats = book.stats()
    closed = (f" | lezart {stats['n']}, talalati arany {stats['win']:.0%}, P/L {stats['pnl']:+,.0f} $"
              if stats["n"] else "")
    line = f"Paper trade-ek: {len(book.active())} nyitott{closed}"
    gate = broker.readiness_lines(book, settings)
    try:
        account = client.account()
        equity, last = float(account.get("equity") or 0), float(account.get("last_equity") or 0)
        day = f" (ma {(equity / last - 1) * 100:+.2f}%)" if last else ""
        return [f"Paper szamla: {equity:,.0f} ${day}".replace(",", " "), line] + gate
    except Exception as exc:                      # a kulcs soha nincs a kivetel szovegeben
        return [f"Paper szamla: nem elerheto ({type(exc).__name__})", line] + gate


def build(settings: Settings, report: DayReport, sent: Counter | None, errors: ErrorCounter | None,
          started: datetime | None, next_run: datetime | None = None) -> Alert:
    """Az eletjel uzenet. 'heartbeat' (rendben) vagy 'heartbeat_warn' (valami nem stimmel).
    A None-kent atadott reszek (kezi jelentes: nincs napi szamlalo, nem a monitor fut) kimaradnak."""
    warnings: list[str] = [f"hibara futott: {name}" for name in report.failures]
    trading = trading_day(report.day)
    day_text = {True: "kereskedesi nap", False: "unnepnap - a tozsde zarva volt", None: "naptar nem elerheto"}

    lines = [f"Nap: {report.day:%Y-%m-%d} ({day_text[trading]})"]
    if report.late:
        lines.append("Potlolagos futas: a monitor nem futott a zaraskor - a jelzesek kesve mentek ki")
    if report.watchlist_analysed is not None:
        bar = f", utolso gyertya: {report.last_bar:%Y-%m-%d}" if report.last_bar is not None else ""
        lines.append(f"Watchlist: {report.watchlist_analysed}/{report.watchlist_total} papir elemezve{bar}")
        if report.watchlist_analysed < report.watchlist_total:
            warnings.append(f"{report.watchlist_total - report.watchlist_analysed} papirhoz nincs adat")
        day = report.day.tz_localize(None) if report.day.tzinfo else report.day
        if (trading and report.last_bar is not None and report.last_bar.date() < day.date()
                and sessions.session_closed(day)):        # zaras elott a mai gyertya meg nem jar
            warnings.append("az arfolyamadat nem friss (nincs mai gyertya)")
    if report.index:
        lines.append(f"Indexek: {report.index.get('analysed', 0)}/{report.index.get('symbols', 0)} papir elemezve, "
                     f"{len(report.index.get('signals', []))} belepesi helyzet")
    if sent is not None:
        lines.append(_events_line(sent))

    tracker = positions.PositionTracker(settings, read_only=True)
    stats = tracker.closed_stats()
    closed = (f" | lezart {stats['n']}, talalati arany {stats['win']:.0%}, osszesen {stats['total']:+.1f}R"
              if stats["n"] else "")
    lines.append(f"Kovetett trade-ek: {len(tracker.active())} nyitott{closed}")
    try:
        lines += _broker_lines(settings)
    except Exception:
        log.exception("Az eletjel paper-szamla resze nem keszult el")
        lines.append("Paper szamla: hiba (lasd a naplot)")

    if errors is not None:
        lines.append(f"Hibak a nap folyaman: {errors.errors}, figyelmeztetesek: {errors.warnings}")
        if errors.last_error:
            lines.append(f"  utolso hiba: {errors.last_error}")
        if errors.errors:
            warnings.append(f"{errors.errors} hiba a naploban")
    usage = housekeeping.total_usage_mb(settings)
    lines.append(f"Lemez: {usage:.0f} MB / {settings.storage.max_data_mb:.0f} MB")
    if usage > settings.storage.max_data_mb:
        warnings.append("a lemezhasznalat a kuszob felett")

    if started is not None:
        uptime = datetime.now(timezone.utc) - started
        lines.append(f"Monitor fut: {uptime.days} napja {uptime.seconds // 3600} oraja")
    if next_run is not None:
        lines.append(f"Kovetkezo zaras utani futas: {pd.Timestamp(next_run).tz_convert(MARKET_TZ):%Y-%m-%d %H:%M} NY")

    status = "rendben" if not warnings else "FIGYELEM - " + "; ".join(warnings)
    return Alert(symbol="STOCKWATCH", title=f"Eletjel - {status}"[:200], body="\n".join(lines),
                 forecast=None, event="heartbeat_warn" if warnings else "heartbeat")


def _state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(**fields) -> None:
    try:
        STATE_PATH.write_text(json.dumps({**_state(), **fields}), encoding="utf-8")
    except OSError:
        log.warning("Az eletjel allapota nem irhato: %s", STATE_PATH)


def start_notice_due(now: datetime | None = None) -> bool:
    """Kell-e indulasi uzenet (legfeljebb START_NOTICE_EVERY-nkent egyszer)."""
    now = now or datetime.now(timezone.utc)
    try:
        last = pd.Timestamp(_state()["start_notice"])
        if pd.Timestamp(now) - last < START_NOTICE_EVERY:
            return False
    except (ValueError, KeyError, TypeError):
        pass
    _save_state(start_notice=now.isoformat())
    return True


# ---------------------------------------------------------------------------
# Kimaradt zaras utani futas potlasa
# ---------------------------------------------------------------------------
CLOSE_RUN = (16, 15)        # a monitor zaras utani futasa, NY ido (monitor.schedule_triggers)


def last_session(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """A legutobbi kereskedesi nap (NY datum, idozona nelkul), amelynek a zaras utani
    futasa mar esedekes volt."""
    now = pd.Timestamp(now).tz_convert(MARKET_TZ) if now is not None else sessions.now_ny()
    day = now.tz_localize(None).normalize()
    if (now.hour, now.minute) < CLOSE_RUN:
        day -= pd.Timedelta(days=1)
    for _ in range(10):                     # hetvege, unnepnap visszafele
        if trading_day(day) is not False:
            return day
        day -= pd.Timedelta(days=1)
    return day


def mark_close_done(day: pd.Timestamp) -> None:
    """A zaras utani futas lefutott erre a kereskedesi napra."""
    _save_state(close_done=f"{pd.Timestamp(day):%Y-%m-%d}")


def missed_close(now: pd.Timestamp | None = None) -> pd.Timestamp | None:
    """A legutobbi kereskedesi nap, ha a zaras utani futasa kimaradt (a monitor nem futott
    16:15-kor), kulonben None. Csak a legutobbi napot potoljuk - a regebbi jelzesek belepoje
    mar elavult. Friss telepitesnel (meg nincs feljegyzes) nem potolunk: ismeretlen
    elozmenyre nem kuldunk kesei megbizast."""
    done = _state().get("close_done")
    if not done:
        return None
    day = last_session(now)
    try:
        return day if pd.Timestamp(done) < day else None
    except (ValueError, TypeError):
        return None


def start_alert(watchlist: int, next_run: datetime | None) -> Alert:
    when = f"\nKovetkezo zaras utani futas: {pd.Timestamp(next_run).tz_convert(MARKET_TZ):%Y-%m-%d %H:%M} NY" \
        if next_run is not None else ""
    return Alert(symbol="STOCKWATCH", title="Monitor elindult",
                 body=f"A monitor (ujra)indult, {watchlist} papirt figyel.{when}\n"
                      "Ha ez nem a te ujrainditasod volt: a szerver ujraindult, vagy a monitor leallt es "
                      "a systemd ujrainditotta (journalctl -u stockwatch-monitor).",
                 forecast=None, event="heartbeat_start")
