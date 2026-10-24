"""Pontozas, XP, napi sorozat es jelvenyek (#34) - a dontes MINOSEGE szerint, nem a profit szerint.

  * pont az itelet szerint (practice.points): jo > balszerencses (a szabaly szerint indokolt volt) > szerencses >
    rossz; levonas a vegrehajtasi jelzesekert (tul szoros stop, alacsony cel / kockazat). Determinisztikus, es a
    dontes utan latszik, miert annyi. A pontnak nincs penzbeli erteke.
  * XP = a pontok osszege; szint = 1 + XP // LEVEL_XP
  * napi sorozat (Europe/Budapest naptari nap): egy kihagyott nap "befagyaszthato"; egy het folyamatos
    aktivitas visszaadja a fagyasztot
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import db
from .config import Settings

TZ = ZoneInfo("Europe/Budapest")
LEVEL_XP = 100
FREEZE_EVERY = 7                                  # ennyi egymast koveto aktiv nap visszaadja a fagyasztot
LABELS = {"good": "Jó döntés", "unlucky": "Balszerencsés (a szabály szerint indokolt)", "lucky": "Szerencsés",
          "bad": "Rossz döntés", "tight": "Túl szoros stop", "rr": "Alacsony cél / kockázat"}
BADGES = {
    "first_short": "Első short",
    "good_skips_10": "10 jó kihagyás egymás után",
    "wide_stops_20": "20 belépés legalább 1 ATR-es stoppal egymás után",
}


def today(now: datetime | None = None) -> date:
    return (now or datetime.now(TZ)).astimezone(TZ).date()


def score(verdict: dict | None, settings: Settings) -> dict:
    """{"points", "parts": [{"key", "label", "value"}]} - ugyanarra az iteletre mindig ugyanannyi."""
    weights = settings.practice.points
    if not verdict:
        return {"points": 0, "parts": []}
    parts = [{"key": verdict["category"], "label": LABELS[verdict["category"]], "value": weights[verdict["category"]]}]
    for flag in verdict.get("flags") or []:
        if flag["key"] in ("tight", "rr"):
            parts.append({"key": flag["key"], "label": LABELS[flag["key"]], "value": weights[flag["key"]]})
    return {"points": max(0, sum(p["value"] for p in parts)), "parts": parts}


def next_streak(last_active: date | None, streak: int, freeze: bool, day: date) -> tuple[int, bool]:
    """(uj sorozat, van-e fagyaszto) egy aktiv nap utan."""
    if last_active == day:
        return streak, freeze
    gap = (day - last_active).days if last_active else None
    if gap == 1:
        streak += 1
    elif gap == 2 and freeze:
        streak, freeze = streak + 1, False                 # a kihagyott napot a fagyaszto athidalja
    else:
        streak = 1
    if streak % FREEZE_EVERY == 0:
        freeze = True
    return streak, freeze


def level(xp: int) -> dict:
    return {"level": 1 + xp // LEVEL_XP, "xp": xp, "level_xp": xp % LEVEL_XP, "next": LEVEL_XP}


def record(conn, user_id: int, points: int, day: date) -> dict:
    """A dontes pontja az XP-be, a nap a sorozatba, es az uj jelvenyek - a dontes mentesevel egy tranzakcioban
    (a practice_tasks sor mar a frissitett ponttal / stop-tavolsaggal szerepel)."""
    row = conn.execute("SELECT xp, streak, best_streak, last_active, freeze_available FROM progress "
                       "WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
    xp, streak, best, last, freeze = row or (0, 0, 0, None, True)
    streak, freeze = next_streak(last, streak, freeze, day)
    xp, best = xp + points, max(best, streak)
    conn.execute("INSERT INTO progress (user_id, xp, streak, best_streak, last_active, freeze_available) "
                 "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET xp = EXCLUDED.xp, "
                 "streak = EXCLUDED.streak, best_streak = EXCLUDED.best_streak, last_active = EXCLUDED.last_active, "
                 "freeze_available = EXCLUDED.freeze_available", (user_id, xp, streak, best, day, freeze))
    new = [b for b in _earned(conn, user_id) if conn.execute(
        "INSERT INTO user_badges (user_id, badge) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, b)).rowcount]
    return level(xp) | {"streak": streak, "best_streak": best, "freeze": freeze,
                        "new_badges": [{"key": b, "label": BADGES[b]} for b in new]}


def _earned(conn, user_id: int) -> list[str]:
    recent = conn.execute(
        "SELECT t.decision->>'action', t.verdict, t.stop_atr FROM practice_tasks t JOIN practice_sessions s "
        "ON s.id = t.session_id WHERE s.user_id = %s AND t.decision IS NOT NULL AND t.kind = 'decision' "
        "ORDER BY t.decided_at DESC LIMIT 40",          # a jelvenyek a dontesi feladatokbol (a tobbinek nincs iranya)
        (user_id,)).fetchall()
    out = []
    if any(action == "short" for action, _, _ in recent):
        out.append("first_short")
    last10 = recent[:10]
    if len(last10) == 10 and all(action == "skip" and verdict == "good" for action, verdict, _ in last10):
        out.append("good_skips_10")
    entered = [stop for action, _, stop in recent if action != "skip"][:20]
    if len(entered) == 20 and all(stop is not None and stop >= 1.0 for stop in entered):
        out.append("wide_stops_20")
    return out


def summary(user_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute("SELECT xp, streak, best_streak, last_active, freeze_available FROM progress "
                           "WHERE user_id = %s", (user_id,)).fetchone()
        earned = dict(conn.execute("SELECT badge, earned_at FROM user_badges WHERE user_id = %s", (user_id,)).fetchall())
    xp, streak, best, last, freeze = row or (0, 0, 0, None, True)
    # a sorozat csak akkor el, ha ma vagy tegnap (fagyasztoval tegnapelott) volt aktivitas
    alive = last is not None and (today() - last).days <= (2 if freeze else 1)
    return level(xp) | {"streak": streak if alive else 0, "best_streak": best, "freeze": freeze,
                        "badges": [{"key": k, "label": v, "earned_at": earned.get(k)} for k, v in BADGES.items()]}
