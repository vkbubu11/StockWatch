"""Napi kihivas es ranglista (#35).

  * minden nap (Europe/Budapest) ugyanaz a DAILY_ROUNDS helyzet mindenkinek: a seed a napbol es a feladatkeszlet
    ujjlenyomatabol jon (a megosztasi kod mechanizmusa); naponta egyszer jatszhato, az inditastol DAILY_MINUTES
    percig; a felfedes csak a beadas utan (practice/session.hidden)
  * ranglista: napi, heti (hetfotol), havi, osszesitett - a napi kihivasok pontjai szerint (#34), igy a vegtelen
    gyakorlassal nem lehet "farmolni"; becenev latszik, e-mail soha; csak megerositett fiok
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

import psycopg

from . import db, progress
from .config import Settings
from .practice import sampler, store
from .practice import session as practice

PERIODS = ("day", "week", "month", "all")
TOP = 50
NICK_RE = re.compile(r"^[\w .\-]{3,20}$")


def seed_for(day: date, fingerprint: str) -> int:
    return int(hashlib.sha1(f"daily|{day:%Y-%m-%d}|{fingerprint}".encode()).hexdigest()[:8], 16) % sampler.SEED_MAX


def status(user_id: int, day: date | None = None) -> dict:
    day = day or progress.today()
    sid = store.daily_session(user_id, f"{day:%Y-%m-%d}")
    session = store.load_session(sid, user_id) if sid else None
    return {"day": f"{day:%Y-%m-%d}", "rounds": practice.DAILY_ROUNDS, "minutes": practice.DAILY_MINUTES,
            "session": practice.public(session) if session else None,
            "hidden": practice.hidden(session) if session else None}


def start(user_id: int, settings: Settings | None = None, day: date | None = None) -> dict:
    """A mai kihivas (ha mar elkezdte, ugyanaz - egyszer jatszhato)."""
    day = day or progress.today()
    key = f"{day:%Y-%m-%d}"
    sid = store.daily_session(user_id, key)
    if sid:
        return practice.public(store.load_session(sid, user_id))
    pool = store.load_pool()
    if not pool:
        raise ValueError("Meg nincs feladatkeszlet.")
    try:
        return practice.create(user_id, settings, seed=seed_for(day, pool["fingerprint"]), rounds=practice.DAILY_ROUNDS,
                               daily=key)
    except psycopg.errors.UniqueViolation:            # egyszerre ket indulas: a masik mar letrehozta
        return practice.public(store.load_session(store.daily_session(user_id, key), user_id))


def _range(period: str, day: date) -> tuple[date | None, date | None]:
    if period == "day":
        return day, day
    if period == "week":
        return day - timedelta(days=day.weekday()), day
    if period == "month":
        return day.replace(day=1), day
    return None, None


def leaderboard(period: str, user_id: int, day: date | None = None) -> dict:
    if period not in PERIODS:
        raise ValueError(f"period: {', '.join(PERIODS)}")
    day = day or progress.today()
    lo, hi = _range(period, day)
    with db.connection() as conn:
        rows = conn.execute(
            "WITH scores AS (SELECT u.id, coalesce(u.nickname, 'Játékos #' || substr(md5(u.id::text), 1, 5)) AS name, "
            "sum(t.points) AS points, count(DISTINCT s.id) AS days FROM practice_sessions s "
            "JOIN practice_tasks t ON t.session_id = s.id JOIN users u ON u.id = s.user_id "
            "WHERE s.daily IS NOT NULL AND (%(lo)s::date IS NULL OR s.daily BETWEEN %(lo)s AND %(hi)s) "
            "AND u.email_verified_at IS NOT NULL AND t.points IS NOT NULL GROUP BY u.id), "
            "ranked AS (SELECT *, rank() OVER (ORDER BY points DESC) AS rank FROM scores) "
            "SELECT rank, name, points, days, id = %(me)s FROM ranked WHERE rank <= %(top)s OR id = %(me)s "
            "ORDER BY rank, name", {"lo": lo, "hi": hi, "me": user_id, "top": TOP}).fetchall()
    out = [{"rank": r, "name": n, "points": p, "days": d, "me": me} for r, n, p, d, me in rows]
    return {"period": period, "from": f"{lo:%Y-%m-%d}" if lo else None, "to": f"{hi:%Y-%m-%d}" if hi else None,
            "rows": [r for r in out if r["rank"] <= TOP], "me": next((r for r in out if r["me"]), None)}


def set_nickname(user_id: int, nickname: str) -> str:
    nickname = " ".join((nickname or "").split())
    if not NICK_RE.match(nickname) or "@" in nickname:
        raise ValueError("A becenév 3–20 karakter: betű, szám, szóköz, pont, kötőjel (e-mail-cím nem lehet).")
    try:
        with db.connection() as conn:
            conn.execute("UPDATE users SET nickname = %s WHERE id = %s", (nickname, user_id))
    except psycopg.errors.UniqueViolation:
        raise ValueError("Ez a becenév már foglalt.") from None
    return nickname
