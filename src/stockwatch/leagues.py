"""Heti ligak (#56): Bronz -> Ezust -> Arany -> Platina -> Gyemant; hetfotol vasarnapig (Europe/Budapest) a heti pont
szamit (a dontes minosege szerint, #34), kb. GROUP_SIZE fos csoportban.

  * csoportba csak az kerul, aki az adott heten dontott (a het elso dontesekor), es nem lepett ki
  * heti pont: a het donteseinek pontja, de egy helyzetet (papir + nap + feladattipus) csak az elso dontes szamit -
    ugyanannak a feladatnak az ujrajatszasa (pl. megosztasi koddal) nem; a napi kihivas es a leckek is szamitanak
  * a het zarasa lustan: a kovetkezo het elso liga-muvelete zarja a korabbi heteket (determinisztikus: csak az adott
    het dontesein mulik); a be nem adott napi kihivas pontja a heti allasban csak a beadas utan latszik
  * a zaraskor a legjobb ~UP feljut, a leggyengebb ~DOWN kiesik (kisebb csoportban aranyosan; a feljutashoz pont kell)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from . import db, progress

TIERS = ("Bronz", "Ezüst", "Arany", "Platina", "Gyémánt")
GROUP_SIZE = 30
UP, DOWN = 7, 5                                   # egy GROUP_SIZE fos csoportban
LOCK = 560056                                     # advisory lock: csoportba osztas es zaras egyszerre egy tranzakcioban


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def quotas(n: int, tier: int) -> tuple[int, int]:
    """(feljuto, kieso) egy n fos csoportban: a legfelso ligabol nincs feljutas, a legalsobol nincs kieses."""
    up = 0 if tier == len(TIERS) - 1 else -(-n * UP // GROUP_SIZE)
    down = 0 if tier == 0 else n * DOWN // GROUP_SIZE
    return up, down


def standings(rows: list[tuple], tier: int) -> list[tuple]:
    """rows: (user_id, pont, mikor erte el) -> [(user_id, pont, helyezes, up | stay | down)]. Holtversenyben az
    elobb, aki elobb erte el a pontjat (az utolso pontot hozo dontese korabbi), vegul a kisebb azonosito."""
    far = datetime.max.replace(tzinfo=timezone.utc)
    ranked = sorted(rows, key=lambda r: (-r[1], r[2] or far, r[0]))
    up, down = quotas(len(ranked), tier)
    return [(uid, xp, i + 1, "up" if i < up and xp > 0 else "down" if i >= len(ranked) - down else "stay")
            for i, (uid, xp, _) in enumerate(ranked)]


def _points(conn, user_ids: list[int], week: date, live: bool) -> dict[int, tuple[int, datetime | None]]:
    """A het pontja felhasznalonkent. live: a be nem adott napi kihivas nelkul (zaraskor minden dontes szamit)."""
    from .practice.session import HIDDEN_SQL

    # shortcut: a torolt kor dontese nem latszik, igy torles utan ugyanaz a helyzet ujra szamit; ha visszaelnek vele,
    # kulon tabla kell a mar szamolt helyzetekrol
    rows = conn.execute(
        "WITH firsts AS (SELECT DISTINCT ON (s.user_id, t.symbol, t.day, t.kind) s.user_id, t.points, t.decided_at, "
        f"{HIDDEN_SQL} AS hidden "
        "FROM practice_tasks t JOIN practice_sessions s ON s.id = t.session_id "
        "WHERE s.user_id = ANY(%(ids)s) AND t.decided_at IS NOT NULL ORDER BY s.user_id, t.symbol, t.day, t.kind, t.decided_at) "
        "SELECT user_id, coalesce(sum(points), 0), max(decided_at) FILTER (WHERE points > 0) FROM firsts "
        "WHERE (decided_at AT TIME ZONE 'Europe/Budapest')::date BETWEEN %(lo)s AND %(hi)s AND NOT (%(live)s AND hidden) "
        "GROUP BY user_id",
        {"ids": user_ids, "lo": week, "hi": week + timedelta(days=6), "live": live}).fetchall()
    return {uid: (int(xp), reached) for uid, xp, reached in rows}


def _settle_due(conn, week: date) -> None:
    """A `week` elotti, meg le nem zart hetek zarasa (hetenkent sorban, igy a liga-szint lepesrol lepesre valtozik)."""
    if not conn.execute("SELECT 1 FROM league_entries WHERE week < %s AND outcome IS NULL LIMIT 1", (week,)).fetchone():
        return
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
    groups = conn.execute("SELECT DISTINCT week, tier, grp FROM league_entries WHERE week < %s AND outcome IS NULL "
                          "ORDER BY week, tier, grp", (week,)).fetchall()
    for w, tier, grp in groups:
        ids = [r[0] for r in conn.execute("SELECT user_id FROM league_entries WHERE week = %s AND tier = %s AND grp = %s",
                                          (w, tier, grp)).fetchall()]
        pts = _points(conn, ids, w, live=False)
        for uid, xp, rank, outcome in standings([(u, *pts.get(u, (0, None))) for u in ids], tier):
            conn.execute("UPDATE league_entries SET xp = %s, rank = %s, outcome = %s WHERE week = %s AND user_id = %s",
                         (xp, rank, outcome, w, uid))
            conn.execute("UPDATE league_members SET tier = %s WHERE user_id = %s",
                         (tier + {"up": 1, "down": -1}.get(outcome, 0), uid))


def join(conn, user_id: int, day: date) -> None:
    """A dontes mentesevel egy tranzakcioban: a het elso dontesekor csoportba kerul (ha nem lepett ki)."""
    week = week_of(day)
    if conn.execute("SELECT 1 FROM league_entries WHERE week = %s AND user_id = %s", (week, user_id)).fetchone():
        return
    member = conn.execute("SELECT opted_out_at FROM league_members WHERE user_id = %s", (user_id,)).fetchone()
    if member and member[0]:
        return
    _settle_due(conn, week)                       # a multheti eredmeny donti el, melyik ligaba kerul
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
    tier = conn.execute("INSERT INTO league_members (user_id) VALUES (%s) ON CONFLICT (user_id) DO UPDATE "
                        "SET tier = league_members.tier RETURNING tier", (user_id,)).fetchone()[0]
    n = conn.execute("SELECT count(*) FROM league_entries WHERE week = %s AND tier = %s", (week, tier)).fetchone()[0]
    # shortcut: erkezesi sorrendben toltjuk a csoportokat (a korai kezdok egyutt), nem aktivitas szerint
    conn.execute("INSERT INTO league_entries (week, user_id, tier, grp) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                 (week, user_id, tier, n // GROUP_SIZE))


def view(user_id: int, day: date | None = None) -> dict:
    week = week_of(day or progress.today())
    with db.connection() as conn:
        _settle_due(conn, week)
        tier, out = conn.execute("SELECT tier, opted_out_at FROM league_members WHERE user_id = %s",
                                 (user_id,)).fetchone() or (0, None)
        entry = conn.execute("SELECT tier, grp FROM league_entries WHERE week = %s AND user_id = %s",
                             (week, user_id)).fetchone()
        group = None
        if entry:
            members = conn.execute("SELECT u.id, u.nickname, substr(md5(u.id::text), 1, 5) FROM league_entries e "
                                   "JOIN users u ON u.id = e.user_id WHERE e.week = %s AND e.tier = %s AND e.grp = %s",
                                   (week, *entry)).fetchall()
            names = {uid: (nick, tag) for uid, nick, tag in members}
            pts = _points(conn, list(names), week, live=True)
            group = [{"rank": rank, "name": names[uid][0], "tag": names[uid][1], "xp": xp, "zone": zone, "me": uid == user_id}
                     for uid, xp, rank, zone in standings([(u, *pts.get(u, (0, None))) for u in names], entry[0])]
        last = conn.execute("SELECT week, tier, xp, rank, outcome, seen_at IS NULL FROM league_entries "
                            "WHERE user_id = %s AND outcome IS NOT NULL ORDER BY week DESC LIMIT 1", (user_id,)).fetchone()
    return {"tier": tier, "opted_out": out is not None, "week": f"{week:%Y-%m-%d}",
            "ends": datetime.combine(week + timedelta(days=7), time(), progress.TZ).isoformat(),
            "quotas": dict(zip(("up", "down"), quotas(len(group), tier))) if group else None, "group": group,
            "last": dict(zip(("week", "tier", "xp", "rank", "outcome", "new"), last)) if last else None}


def seen(user_id: int) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE league_entries SET seen_at = now() WHERE user_id = %s AND outcome IS NOT NULL "
                     "AND seen_at IS NULL", (user_id,))


def set_opt_in(user_id: int, opt_in: bool, day: date | None = None) -> None:
    """Kilepes: azonnal kikerul a heti csoportbol (es a kovetkezokbe sem kerul); visszalepes: a kovetkezo dontessel."""
    with db.connection() as conn:
        conn.execute("INSERT INTO league_members (user_id, opted_out_at) VALUES (%s, CASE WHEN %s THEN NULL ELSE now() END) "
                     "ON CONFLICT (user_id) DO UPDATE SET opted_out_at = EXCLUDED.opted_out_at", (user_id, opt_in))
        if not opt_in:
            conn.execute("DELETE FROM league_entries WHERE user_id = %s AND week = %s AND outcome IS NULL",
                         (user_id, week_of(day or progress.today())))
