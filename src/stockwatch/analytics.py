"""Meres (#48): termekesemenyek a sajat adatbazisban, adatvedelmet tisztelo modon.

  * esemeny: nev, idopont, alany, nehany tulajdonsag. Az alany a fiok azonositoja ('u<id>'); IP-cim, e-mail-cim,
    jelszo es token SOHA nem kerul bele (a FORBIDDEN kulcsokat eldobjuk). Fiok torlesekor az alany veletlen
    azonositora csereldik ('x<...>'): a statisztika megmarad, a szemelyhez nem kotheto.
  * oldalmegtekintes: suti es azonosito nelkul, a szerveren - az oldal, a hivatkozo oldal domainje (pl.
    reddit.com), az eszkoz tipusa. Robotok (felugyelet, kereso) nem szamitanak. Harmadik felnek semmi nem megy.
  * hibaszamlalo: bongeszos JavaScript-hiba es szerver 5xx - csak a darabszam (es az utvonal mintaja)
  * megorzes: RETENTION (13 honap); a napi karbantartas (cli.py db backup) torli a regebbit
  * a meres soha nem akaszthatja meg a felhasznalo muveletet: hiba eseten csak naplozunk
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from . import db

log = logging.getLogger(__name__)

RETENTION = timedelta(days=396)                 # ~13 honap
FORBIDDEN = {"ip", "email", "password", "token", "user_agent", "nickname"}
ACTIVITY = ("login", "decision", "daily_start", "lesson_start")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|monitor|uptime|curl|wget|python|httpx|headless|preview", re.I)
COHORT_WEEKS = 8


def subject(user_id: int | None) -> str | None:
    return f"u{int(user_id)}" if user_id is not None else None


def _insert(conn, name: str, subj: str | None, props: dict) -> None:
    conn.execute("INSERT INTO events (name, subject, props) VALUES (%s, %s, %s)", (name, subj, Jsonb(props)))


def track(name: str, user_id: int | None = None, conn=None, **props) -> None:
    """Egy esemeny. `conn`: a hivo tranzakciojaban (mentesi ponttal - a hibaja a hivot nem rontja el)."""
    props = {k: v for k, v in props.items() if k not in FORBIDDEN and v is not None}
    try:
        if conn is not None:
            with conn.transaction():
                _insert(conn, name, subject(user_id), props)
        else:
            with db.connection() as own:
                _insert(own, name, subject(user_id), props)
    except Exception as exc:                                         # a meres nem lehet a muvelet akadalya
        log.warning("Az esemeny (%s) nem rogzult: %s", name, type(exc).__name__)


def track_later(name: str, **props) -> None:
    """Nevtelen esemeny hatterszalon (oldalmegtekintes, hiba): a valaszt nem lassitja."""
    threading.Thread(target=track, args=(name,), kwargs=props, daemon=True).start()


def anonymize(conn, user_id: int) -> str:
    """A fiok esemenyei veletlen alanyra (fioktorleskor); visszaadja az uj alanyt."""
    anon = "x" + secrets.token_hex(8)
    conn.execute("UPDATE events SET subject = %s WHERE subject = %s", (anon, subject(user_id)))
    return anon


def prune() -> int:
    with db.connection() as conn:
        return conn.execute("DELETE FROM events WHERE at < now() - %s", (RETENTION,)).rowcount


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT_RE.search(user_agent))


def device(user_agent: str) -> str:
    if re.search(r"iPad|Tablet", user_agent or ""):
        return "tablet"
    return "mobile" if re.search(r"Mobi|Android|iPhone", user_agent or "") else "desktop"


def ref_host(referer: str | None, own_host: str | None) -> str | None:
    """A hivatkozo oldal domainje (a sajat oldalunk nem; teljes URL, utvonal es parameter soha)."""
    host = (urlparse(referer or "").hostname or "").lower().removeprefix("www.")
    own = (own_host or "").split(":")[0].lower().removeprefix("www.")
    return host[:80] if host and host != own else None


# ---------------------------------------------------------------------------
# Mutatok (admin attekintes, #58)
# ---------------------------------------------------------------------------
def overview(days: int = 30, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    p = {"since": since, "now": now, "act": list(ACTIVITY)}
    with db.connection() as conn:
        funnel = conn.execute(
            "WITH reg AS (SELECT subject FROM events WHERE name = 'register' AND at >= %(since)s AND at < %(now)s "
            "GROUP BY subject) SELECT "
            "(SELECT count(*) FROM events WHERE name = 'pageview' AND props->>'path' = '/' AND at >= %(since)s "
            " AND at < %(now)s), "
            "(SELECT count(*) FROM reg), "
            "(SELECT count(DISTINCT e.subject) FROM events e JOIN reg USING (subject) WHERE e.name = 'verify'), "
            "(SELECT count(DISTINCT e.subject) FROM events e JOIN reg USING (subject) WHERE e.name = 'decision'), "
            "(SELECT count(DISTINCT e.subject) FROM events e JOIN reg USING (subject) "
            " WHERE e.name = 'lesson_pass' AND e.props->>'level' = '1')", p).fetchone()
        active = conn.execute(
            "SELECT count(DISTINCT subject) FILTER (WHERE at >= %(now)s - interval '1 day'), "
            "count(DISTINCT subject) FILTER (WHERE at >= %(now)s - interval '7 days'), "
            "count(DISTINCT subject) FROM events WHERE name = ANY(%(act)s) AND subject IS NOT NULL "
            "AND at >= %(now)s - interval '30 days' AND at < %(now)s", p).fetchone()
        weeks = ", ".join(f"count(DISTINCT a.subject) FILTER (WHERE a.wk = r.wk + interval '{w} week')"
                          for w in range(1, COHORT_WEEKS + 1))
        cohorts = conn.execute(
            "WITH r AS (SELECT subject, date_trunc('week', min(at)) AS wk FROM events WHERE name = 'register' "
            "GROUP BY subject), a AS (SELECT DISTINCT subject, date_trunc('week', at) AS wk FROM events "
            "WHERE name = ANY(%(act)s) AND subject IS NOT NULL AND at < %(now)s) "
            f"SELECT r.wk, count(DISTINCT r.subject), {weeks} FROM r LEFT JOIN a USING (subject) "
            "WHERE r.wk >= date_trunc('week', %(now)s) - interval '" + str(COHORT_WEEKS) + " weeks' "
            "AND r.wk <= %(now)s GROUP BY r.wk ORDER BY r.wk", p).fetchall()
        levels = conn.execute(
            "SELECT (props->>'level')::int, count(DISTINCT subject) FILTER (WHERE name = 'lesson_start'), "
            "count(DISTINCT subject) FILTER (WHERE name = 'lesson_pass') FROM events "
            "WHERE name IN ('lesson_start', 'lesson_pass') AND at < %(now)s GROUP BY 1 ORDER BY 1", p).fetchall()
        daily = conn.execute(
            "SELECT (at AT TIME ZONE 'Europe/Budapest')::date, count(DISTINCT subject) FROM events "
            "WHERE name = 'daily_start' AND at >= %(now)s - interval '14 days' AND at < %(now)s GROUP BY 1 ORDER BY 1",
            p).fetchall()
        decisions = conn.execute(
            "SELECT count(*), count(DISTINCT subject) FROM events WHERE name = 'decision' AND at >= %(since)s "
            "AND at < %(now)s", p).fetchone()
        errors = dict(conn.execute(
            "SELECT name, count(*) FROM events WHERE name IN ('js_error', 'server_error') "
            "AND at >= %(now)s - interval '7 days' AND at < %(now)s GROUP BY name", p).fetchall())
        views = {}
        for key, expr in (("source", "coalesce(props->>'ref', 'közvetlen')"), ("device", "props->>'device'"),
                          ("path", "props->>'path'")):
            views[key] = conn.execute(
                f"SELECT {expr}, count(*) FROM events WHERE name = 'pageview' AND at >= %(since)s AND at < %(now)s "
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p).fetchall()
    landing, registered, verified, decided, level1 = funnel
    return {
        "days": days, "since": since.isoformat(timespec="seconds"), "now": now.isoformat(timespec="seconds"),
        "funnel": [{"step": "landing", "n": landing}, {"step": "register", "n": registered},
                   {"step": "verify", "n": verified}, {"step": "first_decision", "n": decided},
                   {"step": "level1", "n": level1}],
        "active": {"day": active[0], "week": active[1], "month": active[2]},
        "cohorts": [{"week": f"{row[0]:%Y-%m-%d}", "size": row[1],
                     "retention": [round(n / row[1], 3) if row[1] else None for n in row[2:]]} for row in cohorts],
        "levels": [{"level": lv, "started": s, "passed": d} for lv, s, d in levels],
        "daily": [{"day": f"{d:%Y-%m-%d}", "players": n} for d, n in daily],
        "decisions": {"total": decisions[0], "users": decisions[1],
                      "per_user": round(decisions[0] / decisions[1], 1) if decisions[1] else None},
        "errors": {"js": errors.get("js_error", 0), "server": errors.get("server_error", 0)},
        "pageviews": {k: [{"key": a, "n": n} for a, n in rows] for k, rows in views.items()},
    }
