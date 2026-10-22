"""Tanulasi ut (#33): szintek rovid leckevel, abraval es celzott feladatsorral.

  * a leckek tartalma fajlban van (locale/lessons.<nyelv>.json), nem a kodban
  * az abra a meglevo chartrajzolonak szol: normalizalt gyertyak egy szintetikus peldan (practice/synthetic.py)
  * a feladatsor a kozos feladatkeszletbol jon, a szint temajara szurve (kategoria es forras: sampler.sample `only`)
  * egy szint akkor teljesul, ha a feladatsor dontesei kozul legalabb PASS_SHARE jo vagy balszerencses (a szabaly
    szerint indokolt) - az eredmenytol (szerencse) fuggetlenul; a kovetkezo szint addig zarolt
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import db
from .config import Settings
from .practice import session as practice
from .practice import synthetic

PASS_SHARE = 0.6
GOOD = ("good", "unlucky")                       # a szabaly szerint indokolt dontes
LESSONS_PATH = Path(__file__).with_name("locale") / "lessons.hu.json"


@lru_cache(maxsize=1)
def lessons() -> tuple[dict, ...]:
    data = json.loads(LESSONS_PATH.read_text(encoding="utf-8"))
    if [x["level"] for x in data] != list(range(1, len(data) + 1)):
        raise ValueError(f"{LESSONS_PATH.name}: a szintek 1-tol folyamatosan szamozodnak")
    return tuple(data)


def lesson(level: int) -> dict:
    if not 1 <= level <= len(lessons()):
        raise LookupError("Nincs ilyen szint.")
    return lessons()[level - 1]


@lru_cache(maxsize=16)
def figure(level: int) -> dict:
    """Normalizalt pelda-chart a lecke abrajahoz (szintetikus papiron, determinisztikusan)."""
    spec = lesson(level)["figure"]
    if "pattern" in spec:
        for symbol in synthetic.symbols(200):
            df, embedded = synthetic.generate(symbol)
            hit = next((e for e in embedded if e[0] == spec["pattern"] and e[4]), None)   # egy sikeres peldany
            if hit:
                return practice.hidden_chart(df, min(hit[3] + 25, len(df) - 1))
        raise LookupError(f"Nincs pelda: {spec['pattern']}")
    df, _ = synthetic.generate("SYN0003")
    return practice.hidden_chart(df, 900)


def progress(user_id: int) -> dict[int, dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT level, best_share, completed_at IS NOT NULL FROM lesson_progress WHERE user_id = %s",
                            (user_id,)).fetchall()
    return {level: {"best_share": share, "completed": done} for level, share, done in rows}


def overview(user_id: int) -> list[dict]:
    done = progress(user_id)
    out, unlocked = [], True
    for item in lessons():
        state = done.get(item["level"], {})
        out.append({"level": item["level"], "title": item["title"], "unlocked": unlocked,
                    "completed": bool(state.get("completed")), "best_share": state.get("best_share")})
        unlocked = bool(state.get("completed"))
    return out


def _require_unlocked(user_id: int, level: int) -> dict:
    item = next((x for x in overview(user_id) if x["level"] == level), None)
    if item is None:
        raise LookupError("Nincs ilyen szint.")
    if not item["unlocked"]:
        raise ValueError("Ez a szint meg zarolt - elobb teljesitsd az elozot.")
    return item


def page(user_id: int, level: int) -> dict:
    status = _require_unlocked(user_id, level)
    item = lesson(level)
    return {k: item[k] for k in ("level", "title", "paragraphs", "points", "caption")} | {
        "rounds": item["tasks"]["rounds"], "figure": figure(level), "status": status, "pass_share": PASS_SHARE,
        "levels": len(lessons())}


def start(user_id: int, level: int, settings: Settings | None = None) -> dict:
    """A szint celzott feladatsora (egy uj gyakorlo kor)."""
    _require_unlocked(user_id, level)
    tasks = lesson(level)["tasks"]
    return practice.create(user_id, settings, rounds=tasks["rounds"], only=tasks["only"], lesson=level)


def record(user_id: int, session: dict) -> dict | None:
    """Egy befejezett leckekor eredmenye: a jo + balszerencses arany, es teljesult-e a szint."""
    level = session.get("lesson")
    if not level or not all(t.get("decision") for t in session["tasks"]):
        return None
    verdicts = [((t.get("result") or {}).get("review") or {}).get("verdict", {}).get("category") for t in session["tasks"]]
    share = round(sum(v in GOOD for v in verdicts) / len(verdicts), 3)
    passed = share >= PASS_SHARE
    with db.connection() as conn:
        conn.execute("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) "
                     "VALUES (%s, %s, %s, CASE WHEN %s THEN now() END) ON CONFLICT (user_id, level) DO UPDATE SET "
                     "best_share = greatest(lesson_progress.best_share, EXCLUDED.best_share), "
                     "completed_at = coalesce(lesson_progress.completed_at, EXCLUDED.completed_at)",
                     (user_id, level, share, passed))
    return {"level": level, "share": share, "passed": passed, "needed": PASS_SHARE,
            "next": level + 1 if passed and level < len(lessons()) else None}
