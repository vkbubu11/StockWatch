"""Magabiztossag es kalibracio (#54): a dontes melle megadott 50-95% („ennyire vagyok biztos benne, hogy ez a jo
dontes”) a kimenet (az itelet: jo vagy balszerencses = a szabaly szerint indokolt) szerint.

  * kalibracios pont: Brier-pontszambol, 10 * (1 - 4 * (p - o)^2) - 50%-nal 0, biztos talalatnal ~10, tulzott
    magabiztossaggal teves dontesnel akar -26: a tul nagy magabiztossag tobbe kerul, mint a bizonytalansag
  * a magabiztossag az iteletet es a #34 pontjait nem befolyasolja (kulon szamolodik)
  * a gorbe savonkent: a megadott atlag vs. a tenyleges talalati arany; MIN_N dontes alatt a felulet jelzi,
    hogy meg foleg zaj
"""
from __future__ import annotations

from . import db

LO, HI = 50, 95
BUCKETS = ((50, 59), (60, 69), (70, 79), (80, 89), (90, 95))
MIN_N = 30
HIT = ("good", "unlucky")
TOLERANCE = 0.05                               # ennyi elteres az atlagban meg „jol kalibralt”


def points(confidence: int, verdict: str | None) -> dict:
    p, o = confidence / 100, 1.0 if verdict in HIT else 0.0
    brier = (p - o) ** 2
    return {"confidence": confidence, "hit": bool(o), "brier": round(brier, 4), "points": round(10 * (1 - 4 * brier))}


def curve(pairs: list[tuple[int, bool]]) -> dict:
    """[(magabiztossag, talalt-e)] -> savok, atlagok, Brier, osszegzes (None: keves adat)."""
    n = len(pairs)
    buckets = []
    for lo, hi in BUCKETS:
        part = [(c, h) for c, h in pairs if lo <= c <= hi]
        if part:
            buckets.append({"lo": lo, "hi": hi, "n": len(part), "stated": round(sum(c for c, _ in part) / len(part) / 100, 3),
                            "actual": round(sum(h for _, h in part) / len(part), 3)})
    if not n:
        return {"n": 0, "min": MIN_N, "noisy": True, "buckets": [], "stated": None, "actual": None, "brier": None,
                "points": 0, "summary": None}
    stated = sum(c for c, _ in pairs) / n / 100
    actual = sum(h for _, h in pairs) / n
    gap = stated - actual
    return {"n": n, "min": MIN_N, "noisy": n < MIN_N, "buckets": buckets, "stated": round(stated, 3),
            "actual": round(actual, 3), "brier": round(sum((c / 100 - h) ** 2 for c, h in pairs) / n, 4),
            "points": sum(points(c, "good" if h else "bad")["points"] for c, h in pairs),
            "summary": None if n < MIN_N else "over" if gap > TOLERANCE else "under" if gap < -TOLERANCE else "ok"}


def for_user(user_id: int, limit: int = 500) -> dict:
    from .practice.session import DAILY_MINUTES

    with db.connection() as conn:
        # a be nem adott napi kihivas nem szamit: a gorbe valtozasa elarulna az iteletet
        rows = conn.execute(
            "SELECT (t.decision->>'confidence')::int, t.verdict FROM practice_tasks t JOIN practice_sessions s "
            "ON s.id = t.session_id WHERE s.user_id = %s AND t.kind = 'decision' AND t.verdict IS NOT NULL "
            "AND t.decision->>'confidence' IS NOT NULL AND (s.daily IS NULL OR s.summary IS NOT NULL "
            "OR s.created_at < now() - %s * interval '1 minute') ORDER BY t.decided_at DESC LIMIT %s",
            (user_id, DAILY_MINUTES, limit)).fetchall()
    return curve([(c, v in HIT) for c, v in rows])
