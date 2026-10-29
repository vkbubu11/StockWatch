"""Hibak ismetlese (#36): a rossz es a szerencses dontes (vagy a tul szoros stop) helyzettipusa (kategoria +
forras) ismetlesi sorba kerul; masnap esedekes, es a rendszer hasonlo - ugyanabbol a tipusbol, de masik - helyzetet ad.
A jo dontes duplazza az idokozt (1 -> 2 -> 4 -> 8 -> 16 nap), utana a tipus kikerul a sorbol.

  * a napi kihivas dontesei nem szamitanak (a beadas elott a sor valtozasa elarulna az iteletet)
  * „Gyengesegeim”: helyzettipusonkent a dontesek, a rossz / szerencses itelet es a szoros stop aranya
"""
from __future__ import annotations

from datetime import date, timedelta

from . import analytics, db, progress
from .practice import scoring

MISS = ("bad", "lucky")
MAX_INTERVAL = 16
ROUNDS = 5


def update(conn, user_id: int, category: str, source: str, verdict: str | None, flags: list[dict], day: date) -> None:
    """A dontes mentesevel egy tranzakcioban."""
    if verdict is None:
        return
    if verdict in MISS or any(f.get("key") == "tight" for f in flags or []):
        conn.execute("INSERT INTO review_queue (user_id, category, source, interval_days, due, misses) "
                     "VALUES (%s, %s, %s, 1, %s, 1) ON CONFLICT (user_id, category, source) DO UPDATE SET "
                     "interval_days = 1, due = EXCLUDED.due, misses = review_queue.misses + 1",
                     (user_id, category, source, day + timedelta(days=1)))
        return
    conn.execute("UPDATE review_queue SET interval_days = interval_days * 2, due = %s + interval_days * 2 "
                 "WHERE user_id = %s AND category = %s AND source = %s", (day, user_id, category, source))
    conn.execute("DELETE FROM review_queue WHERE user_id = %s AND category = %s AND source = %s AND interval_days > %s",
                 (user_id, category, source, MAX_INTERVAL))


def queue(user_id: int) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT category, source, interval_days, due, misses FROM review_queue WHERE user_id = %s "
                            "ORDER BY due, category, source", (user_id,)).fetchall()
    today = progress.today()
    return [{"category": c, "source": s, "interval": i, "due": f"{d:%Y-%m-%d}", "is_due": d <= today, "misses": m}
            for c, s, i, d, m in rows]


def start(user_id: int, settings=None) -> dict:
    """Ismetlo kor az esedekes tipusokbol: hasonlo, de uj helyzetek (a keszlet szurese kategoria + forras szerint)."""
    from .practice import session, store

    due = [q for q in queue(user_id) if q["is_due"]]
    if not due:
        raise ValueError("Nincs esedékes ismétlés.")
    only: dict[str, list[str]] = {}
    for q in due:
        only.setdefault(q["category"], []).append(q["source"])
    pool = store.load_pool() or {"entries": {}}
    available = sum(1 for c, srcs in only.items() for e in pool["entries"].get(c) or [] if e[2] in srcs)
    if not available:
        raise ValueError("A feladatkészletben nincs ilyen típusú helyzet.")
    created = session.create(user_id, settings, rounds=min(ROUNDS, available), only=only)
    analytics.track("review_start", user_id, types=len(due))
    return created


def weaknesses(user_id: int, limit: int = 300) -> list[dict]:
    from .practice.session import HIDDEN_SQL

    with db.connection() as conn:
        rows = conn.execute(
            "WITH recent AS (SELECT t.category, t.verdict, t.stop_atr FROM practice_tasks t JOIN practice_sessions s "
            "ON s.id = t.session_id WHERE s.user_id = %s AND t.kind = 'decision' AND t.verdict IS NOT NULL "
            f"AND NOT {HIDDEN_SQL} "
            "ORDER BY t.decided_at DESC LIMIT %s) "
            "SELECT category, count(*), count(*) FILTER (WHERE verdict = ANY(%s)), "
            "count(*) FILTER (WHERE stop_atr IS NOT NULL AND stop_atr < %s) FROM recent GROUP BY category",
            (user_id, limit, list(MISS), scoring.TIGHT_ATR)).fetchall()
    out = [{"category": c, "n": n, "misses": m, "tight": t, "miss_rate": round(m / n, 3)} for c, n, m, t in rows]
    return sorted(out, key=lambda r: (-r["miss_rate"], -r["n"]))
