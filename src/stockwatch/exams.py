"""Vizsga es tanusitvany (#37).

  * vizsga: ROUNDS dontesi helyzet, session.EXAM_MINUTES perc; a feladatok az inditaskor rogzulnek, a felfedes csak
    a beadas (vagy a lejart ido) utan, mint a napi kihivasnal; elore ismert kuszob: PASS_SHARE jo vagy
    balszerencses (a szabaly szerint indokolt) dontes - a ki nem toltott helyzet nem jo
  * COOLDOWN_HOURS-onkent egy inditas; a futo vizsga folytathato; a vizsga kore nem torolheto
  * sikeres vizsga: tanusitvany a /c/<kod> oldalon (becenev, teljesitett szintek, datum, eredmeny, ellenorzo kod);
    a kod alapjan barki visszaellenorizheti. shortcut: PDF helyett nyomtathato oldal; ha intezmeny keri, kell PDF
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import analytics, db, learning, progress, shares
from .practice import session, store

ROUNDS = 10
PASS_SHARE = 0.7
COOLDOWN_HOURS = 24
DISCLAIMER = "Oktatási célú gyakorlás múltbeli adaton, nem befektetési képesítés."
COLS = ("id", "session_id", "created_at", "finished_at", "score", "passed", "player", "levels", "levels_total")


def _settle(conn, user_id: int, row: tuple) -> dict:
    """A beadott (vagy lejart ideju) vizsga eredmenye egyszer rogzul."""
    exam = dict(zip(COLS, row))
    if exam["finished_at"]:
        return exam
    s = store._load(conn, exam["session_id"], user_id)
    if session.hidden(s):
        return exam
    verdicts = [((t.get("result") or {}).get("review") or {}).get("verdict", {}).get("category") for t in s["tasks"]]
    score = round(sum(v in learning.GOOD for v in verdicts) / len(verdicts), 3)
    levels = conn.execute("SELECT count(*) FROM lesson_progress WHERE user_id = %s AND completed_at IS NOT NULL",
                          (user_id,)).fetchone()[0]
    exam |= {"finished_at": datetime.now(timezone.utc), "score": score, "passed": score >= PASS_SHARE,
             "player": shares._player(conn, user_id), "levels": levels, "levels_total": len(learning.lessons())}
    conn.execute("UPDATE exams SET finished_at = %(finished_at)s, score = %(score)s, passed = %(passed)s, "
                 "player = %(player)s, levels = %(levels)s, levels_total = %(levels_total)s "
                 "WHERE id = %(id)s AND finished_at IS NULL", exam)
    analytics.track("exam_finish", user_id, conn, passed=exam["passed"])
    return exam


def mine(user_id: int) -> dict:
    with db.connection() as conn:
        rows = conn.execute(f"SELECT {', '.join(COLS)} FROM exams WHERE user_id = %s ORDER BY created_at DESC LIMIT 20",
                            (user_id,)).fetchall()
        exams = [_settle(conn, user_id, r) for r in rows]
    running = next((e["session_id"] for e in exams if not e["finished_at"]), None)
    nxt = exams[0]["created_at"] + timedelta(hours=COOLDOWN_HOURS) if exams else None
    return {"rounds": ROUNDS, "minutes": session.EXAM_MINUTES, "pass_share": PASS_SHARE, "running": running,
            "next_start": nxt if nxt and nxt > datetime.now(timezone.utc) and not running else None,
            "exams": [{"code": e["id"] if e["passed"] else None, "session": e["session_id"], "created": e["created_at"],
                       "finished": e["finished_at"], "score": e["score"], "passed": e["passed"]} for e in exams]}


def start(user_id: int, settings=None) -> dict:
    state = mine(user_id)
    if state["running"]:
        return session.public(store.load_session(state["running"], user_id))
    if state["next_start"]:
        raise ValueError(f"Újabb vizsgát {COOLDOWN_HOURS} óránként indíthatsz.")
    created = session.create(user_id, settings, rounds=ROUNDS, exam=True)
    with db.connection() as conn:
        conn.execute("INSERT INTO exams (id, user_id, session_id) VALUES (%s, %s, %s)", (store.new_id(), user_id, created["id"]))
        analytics.track("exam_start", user_id, conn)
    return created


def certificate(code: str) -> dict | None:
    """Nyilvanos visszaellenorzes: csak sikeres vizsga; e-mail-cim soha. A megosztasi kartya (shares.lines / png)
    formajaban."""
    if not store.SESSION_ID_RE.match(code or ""):
        return None
    with db.connection() as conn:
        row = conn.execute("SELECT finished_at, score, player, levels, levels_total FROM exams WHERE id = %s AND passed",
                           (code,)).fetchone()
    if row is None:
        return None
    finished, score, player, levels, total = row
    return {"id": code, "kind": "cert", "ref": code, "lang": None,
            "data": {"player": player, "score": score, "levels": levels, "levels_total": total,
                     "date": f"{progress.today(finished):%Y-%m-%d}", "disclaimer": DISCLAIMER}}
