"""Adatbazis: Neon Postgres (#30). Kapcsolat a DATABASE_URL titokbol (.env, szerveren STOCKWATCH_ENV_FILE),
semamigraciok a migrations/ sorszamozott SQL-fajljaibol. Nincs ORM.

    python cli.py db migrate

  * a kapcsolati adat (URL, jelszo, host, felhasznalo, adatbazisnev) soha nem kerul naploba, hibauzenetbe,
    API-valaszba: a sajat hibank altalanos szoveg, a psycopg naplosorait a _Redact szuro kitakarja
  * Neon: a pooled vegpont (-pooler host); sslmode=require, ha az URL mast nem mond
  * hideginditas: a Neon szamitasa tetlenseg utan leall - az elso kapcsolat lassu lehet, a pool POOL_TIMEOUT
    masodpercig probalkozik; a tetlenseg alatt lezart kapcsolatot kiadas elott ellenorzi es csereli
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool, PoolTimeout

from . import config
from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

URL_KEY = "DATABASE_URL"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
POOL_TIMEOUT = 30.0                 # mp: a kapcsolat kiadasara (Neon hidegindulassal egyutt)
CONNECT_TIMEOUT = 15                # mp: egy kapcsolodasi kiserlet
PSYCOPG_LOGGERS = ("psycopg", "psycopg.pool")

_POOL: ConnectionPool | None = None
_FILTER: logging.Filter | None = None
_LOCK = threading.Lock()


class DatabaseUnavailable(RuntimeError):
    """Nincs beallitva vagy nem erheto el az adatbazis. A szoveg soha nem tartalmaz kapcsolati adatot."""


class _Redact(logging.Filter):
    """A psycopg naplosoraibol kitakarja a kapcsolati adatokat (pl. a pool kapcsolodasi hibaja a hostot irja)."""

    def __init__(self, values: list[str]) -> None:
        super().__init__()
        self.values = sorted({v for v in values if v and len(v) >= 3}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self.values:
            message = message.replace(value, "***")
        record.msg, record.args = message, None
        return True


def url() -> str | None:
    """A kapcsolati szoveg (a tesztek az ideiglenes Neon-semara iranyitjak at)."""
    return config.env_value(URL_KEY)


def pool() -> ConnectionPool:
    global _POOL, _FILTER
    with _LOCK:
        if _POOL is not None:
            return _POOL
        url_ = url()
        if not url_:
            raise DatabaseUnavailable(f"Nincs beallitva adatbazis ({URL_KEY} a titokfajlban).")
        try:
            params = conninfo_to_dict(url_)
        except PsycopgError:
            raise DatabaseUnavailable(f"Ervenytelen {URL_KEY}.") from None
        _FILTER = _Redact([url_, *(str(params.get(k) or "") for k in ("password", "host", "user", "dbname"))])
        for name in PSYCOPG_LOGGERS:
            logging.getLogger(name).addFilter(_FILTER)
        # prepare_threshold=None: a Neon pooler (PgBouncer) mogott a szerveroldali prepared statement nem biztos
        kwargs = {"prepare_threshold": None}
        if "sslmode" not in params:
            kwargs["sslmode"] = "require"
        if "connect_timeout" not in params:
            kwargs["connect_timeout"] = CONNECT_TIMEOUT
        _POOL = ConnectionPool(url_, min_size=1, max_size=5, kwargs=kwargs, check=ConnectionPool.check_connection,
                               name="stockwatch", open=True)
        return _POOL


@contextmanager
def connection():
    """Kapcsolat a poolbol; a blokk vegen commit, hibanal rollback."""
    try:
        with pool().connection(timeout=POOL_TIMEOUT) as conn:
            yield conn
    except PoolTimeout:
        log.warning("Az adatbazis nem erheto el %.0f mp alatt.", POOL_TIMEOUT)
        raise DatabaseUnavailable("Az adatbazis nem erheto el.") from None


def close() -> None:
    global _POOL, _FILTER
    with _LOCK:
        if _POOL is not None:
            _POOL.close()
            _POOL = None
        if _FILTER is not None:
            for name in PSYCOPG_LOGGERS:
                logging.getLogger(name).removeFilter(_FILTER)
            _FILTER = None


def migrate() -> list[str]:
    """A meg nem futott migraciok sorszam szerint, egyetlen tranzakcioban (hibanal egyik sem marad bent).
    Visszaadja a most lefutottakat; masodszor futtatva ures."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        new = [path for path in files if path.stem not in done]
        for path in new:
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
    return [path.stem for path in new]
