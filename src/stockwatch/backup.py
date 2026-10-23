"""Napi adatbazis-mentes (#41): a Neon-adatbazis minden tablaja egy zip-fajlba, a Postgres sajat COPY-formatumaban
(minden oszloptipus pontosan visszaallithato), egyetlen konzisztens pillanatkepbol.

  * data/backup/learn-<ev-ho-nap>.zip: manifest.json (formatum, migraciok, tablak, oszlopok, sorszamok) +
    tablankent egy <tabla>.copy tag; 0600-as jogosultsag (e-mail-cimek, jelszo-lenyomatok)
  * megorzes: KEEP_DAYS nap - az adatkezelesi tajekoztato szerint a torolt fiok a mentesekbol is legkesobb 30 napon
    belul eltunik; lemezplafon: LIMIT_MB (folotte a legregebbi torlodik, a legujabb mindig megmarad)
  * a regi mentesek csak egy SIKERES mentes utan torlodnek
  * visszaallitas (restore): csak ures adatbazisba, azonos migracios allapotra - elotte `db migrate`

Szerveren a stockwatch-learn-backup.timer futtatja naponta (deploy/install.sh); kezzel:
    sudo stockwatch-learn-cli db backup
    sudo stockwatch-learn-cli db restore /opt/stockwatch-learn/data/backup/learn-2026-11-01.zip
pg_dump nem kell hozza (a gepre nem kerul adatbazis-szoftver, es a kliens verziojanak a szerverehez kellene igazodnia).
"""
from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from graphlib import TopologicalSorter
from pathlib import Path

from psycopg import sql

from . import db
from .config import DATA_DIR

log = logging.getLogger(__name__)

BACKUP_DIR = DATA_DIR / "backup"
KEEP_DAYS = 14                     # < 30 nap: az adatkezelesi tajekoztato igerete
LIMIT_MB = 50                      # lemezplafon a mentesek mappajara (a legujabb mindig megmarad)
FORMAT = 1
NAME = re.compile(r"^learn-(\d{4}-\d{2}-\d{2})\.zip$")
CHUNK = 1 << 16


def _tables(conn) -> list[str]:
    """Az aktualis sema tablai idegen kulcs szerinti sorrendben (a hivatkozott elobb); a schema_migrations nelkul
    (azt a visszaallitas elotti migracio tolti)."""
    names = [r[0] for r in conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname <> 'schema_migrations' ORDER BY c.relname")]
    graph: dict[str, set[str]] = {name: set() for name in names}
    for child, parent in conn.execute(
            "SELECT cl.relname, ref.relname FROM pg_constraint co "
            "JOIN pg_class cl ON cl.oid = co.conrelid JOIN pg_class ref ON ref.oid = co.confrelid "
            "JOIN pg_namespace n ON n.oid = cl.relnamespace WHERE co.contype = 'f' AND n.nspname = current_schema()"):
        if child in graph and parent in graph and child != parent:
            graph[child].add(parent)
    return list(TopologicalSorter(graph).static_order())


def _columns(conn, table: str) -> list[str]:
    """A tabla irhato oszlopai (a generalt oszlopot a COPY FROM nem irhatja)."""
    return [r[0] for r in conn.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
        "AND attgenerated = '' ORDER BY attnum", (sql.Identifier(table).as_string(conn),))]


def _migrations(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def _copy_sql(table: str, columns: list[str], direction: str) -> sql.Composed:
    return sql.SQL("COPY {} ({}) " + direction).format(sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)))


def backup(directory: Path | None = None, today: date | None = None) -> dict:
    """Egy mentes (a nap fajlja felulirodik), utana a regiek takaritasa. Visszaad: utvonal, meret, sorok, torolt."""
    directory = directory or BACKUP_DIR
    today = today or datetime.now(timezone.utc).date()
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob(".learn-*.tmp"):     # egy megszakadt mentes maradeka
        stale.unlink(missing_ok=True)
    target = directory / f"learn-{today.isoformat()}.zip"
    tmp = directory / f".{target.name}.tmp"
    manifest: dict = {"format": FORMAT, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": []}
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf, db.connection() as conn:
            # minden tabla ugyanabbol a pillanatbol (egy irasvedett, ismetelheto olvasasu tranzakcioban)
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            manifest["migrations"] = _migrations(conn)
            with conn.cursor() as cur:
                for table in _tables(conn):
                    columns = _columns(conn, table)
                    rows = 0
                    with zf.open(f"{table}.copy", "w") as member, cur.copy(_copy_sql(table, columns, "TO STDOUT")) as copy:
                        for chunk in copy:
                            member.write(chunk)
                            rows += bytes(chunk).count(b"\n")     # szoveges COPY: soronkent egy sor (a sortores escape-elt)
                    manifest["tables"].append({"name": table, "columns": columns, "rows": rows})
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    removed = prune(directory, today)
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    log.info("Adatbazis-mentes kesz: %s (%.1f KB, %d sor, %d regi torolve)", target.name, target.stat().st_size / 1024,
             sum(rows.values()), len(removed))
    return {"path": target, "bytes": target.stat().st_size, "rows": rows, "removed": removed}


def prune(directory: Path | None = None, today: date | None = None) -> list[Path]:
    """A KEEP_DAYS napnal regebbi mentesek torlese, utana a lemezplafon (a legregebbi elobb; a legujabb marad)."""
    directory = directory or BACKUP_DIR
    today = today or datetime.now(timezone.utc).date()
    if not directory.exists():
        return []
    dated = sorted((date.fromisoformat(m.group(1)), path) for path in directory.iterdir() if (m := NAME.match(path.name)))
    removed = []
    for day, path in dated:
        if day <= today - timedelta(days=KEEP_DAYS):
            path.unlink()
            removed.append(path)
    kept = [path for _, path in dated if path not in removed]
    total = sum(path.stat().st_size for path in kept)
    while len(kept) > 1 and total > LIMIT_MB * 1024 * 1024:
        oldest = kept.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()
        removed.append(oldest)
        log.warning("A mentesek elertek a %d MB-os plafont - torolve: %s (emeld a LIMIT_MB-t, ha kevesebb nap maradt)",
                    LIMIT_MB, oldest.name)
    return removed


def usage_bytes(directory: Path | None = None) -> int:
    directory = directory or BACKUP_DIR
    return sum(p.stat().st_size for p in directory.glob("learn-*.zip")) if directory.exists() else 0


def restore(path: Path) -> dict[str, int]:
    """Visszaallitas egy mentesbol: elotte a migraciok; csak ha az adatbazis ures, es a migracios allapot azonos.
    Egyetlen tranzakcio: hibanal semmi nem marad bent. Visszaad: tablankent a betoltott sorok."""
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise ValueError(f"Ismeretlen mentesformatum: {manifest.get('format')}")
        db.migrate()
        with db.connection() as conn, conn.cursor() as cur:
            current = _migrations(conn)
            if current != manifest["migrations"]:
                raise ValueError(f"A mentes migracios allapota ({manifest['migrations'][-1]}) elter az adatbazisetol "
                                 f"({current[-1]}) - a mentes idejebeli kodverzioval allitsd vissza.")
            busy = [t["name"] for t in manifest["tables"]
                    if conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(t["name"]))).fetchone()[0]]
            if busy:
                raise ValueError(f"Csak ures adatbazisba allithato vissza (nem ures: {', '.join(busy)}).")
            loaded = {}
            for table in manifest["tables"]:
                with zf.open(f"{table['name']}.copy") as member, cur.copy(_copy_sql(table["name"], table["columns"], "FROM STDIN")) as copy:
                    while chunk := member.read(CHUNK):
                        copy.write(chunk)
                loaded[table["name"]] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table["name"]))).fetchone()[0]
                if loaded[table["name"]] != table["rows"]:
                    raise ValueError(f"{table['name']}: {loaded[table['name']]} sor toltodott be, a mentesben {table['rows']}.")
                # az identity-szamlalo a betoltott legnagyobb azonosito utan folytassa
                qualified = sql.Identifier(table["name"]).as_string(conn)
                for (column,) in conn.execute("SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attidentity <> ''",
                                              (qualified,)).fetchall():
                    conn.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE(max({}), 0) + 1, false) FROM {}")
                                 .format(sql.Identifier(column), sql.Identifier(table["name"])), (qualified, column))
    log.info("Visszaallitva: %s (%d sor)", Path(path).name, sum(loaded.values()))
    return loaded
