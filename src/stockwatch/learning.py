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

import psycopg

from . import analytics, db, walkthrough
from .config import Settings
from .practice import session as practice
from .practice import boss as practice_boss, synthetic

PASS_SHARE = 0.6
CHAPTERS = ((1, 2, 3), (4, 5, 6), (7, 8, 9))      # fejezetek; a vegukon fejezetzaro feladatsor (#79 b)
HINTS = ("show", "toggle", "hidden")             # az itelet-szempontok a dontes elott (#59): latszik / osszecsukva / nem
GOOD = ("good", "unlucky")                       # a szabaly szerint indokolt dontes
LESSONS_PATH = Path(__file__).with_name("locale") / "lessons.hu.json"


def validate(data) -> list[dict]:
    """A leckek szerkezete (a leckeszerkeszto, #58, csak ervenyeset ment): szintek 1-tol folyamatosan, szoveg,
    abra (ismert alakzat vagy trend), feladatsor (helyzetszam, kategoria-szuro, feladattipusok)."""
    from .practice import kinds, sampler

    if not isinstance(data, list) or not data:
        raise ValueError("A leckek listaja ures vagy nem lista.")
    if [x.get("level") if isinstance(x, dict) else None for x in data] != list(range(1, len(data) + 1)):
        raise ValueError("A szintek 1-tol folyamatosan szamozodjanak (level).")
    for x in data:
        n = x["level"]
        if not isinstance(x.get("title"), str) or not x["title"].strip():
            raise ValueError(f"{n}. szint: hianyzo cim (title).")
        for key in ("paragraphs", "points"):
            if not isinstance(x.get(key), list) or not all(isinstance(s, str) and s.strip() for s in x[key]) or not x[key]:
                raise ValueError(f"{n}. szint: {key} - nem ures szovegek listaja kell.")
        if not isinstance(x.get("caption"), str):
            raise ValueError(f"{n}. szint: hianyzo abrafelirat (caption).")
        fig = x.get("figure")
        if not isinstance(fig, dict) or not (fig.get("kind") == "trend" or fig.get("pattern") in synthetic.TEMPLATES):
            raise ValueError(f"{n}. szint: az abra (figure) {{'kind': 'trend'}} vagy ismert alakzat (pattern) legyen.")
        if x.get("walkthrough") is not None and x["walkthrough"] not in walkthrough.BUILDERS:
            raise ValueError(f"{n}. szint: walkthrough: {', '.join(walkthrough.BUILDERS)} (vagy hianyzik).")
        tasks = x.get("tasks")
        if not isinstance(tasks, dict) or not isinstance(tasks.get("rounds"), int) \
                or not 1 <= tasks["rounds"] <= sampler.MAX_ROUNDS:
            raise ValueError(f"{n}. szint: a feladatsor helyzetszama (tasks.rounds) 1 es {sampler.MAX_ROUNDS} kozott.")
        only = tasks.get("only")
        if not isinstance(only, dict) or not only or set(only) - set(sampler.CATEGORIES) \
                or not all(v is None or (isinstance(v, list) and all(isinstance(s, str) for s in v)) for v in only.values()):
            raise ValueError(f"{n}. szint: rossz feladat-szuro (tasks.only): kategoria -> null vagy forrasok listaja; "
                             f"kategoriak: {', '.join(sampler.CATEGORIES)}.")
        if not isinstance(tasks.get("manage", False), bool):
            raise ValueError(f"{n}. szint: tasks.manage: true / false (gyertyankenti visszajatszas kezelessel).")
        if tasks.get("hints", "hidden") not in HINTS:
            raise ValueError(f"{n}. szint: tasks.hints: {', '.join(HINTS)}.")
        if "kinds" in tasks:
            try:
                kinds.normalize_kinds(tasks["kinds"])
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{n}. szint: rossz feladattipus-sulyok (tasks.kinds).") from None
    return data


def stored(lang: str = "hu") -> tuple[int, list] | None:
    """A leckeszerkeszto legutobbi verzioja (id, adat), vagy None (adatbazis nelkul is: None)."""
    try:
        with db.connection() as conn:
            row = conn.execute("SELECT id, data FROM lesson_versions WHERE lang = %s ORDER BY id DESC LIMIT 1",
                               (lang,)).fetchone()
    except (db.DatabaseUnavailable, psycopg.Error):                     # pl. a migracio elott: a fajl ervenyes
        return None
    return (row[0], row[1]) if row else None


@lru_cache(maxsize=1)
def lessons() -> tuple[dict, ...]:
    """A leckek: a szerkesztett legujabb verzio (#58), kulonben a fajl. A mentes a gyorsitotarat uriti."""
    version = stored()
    data = version[1] if version else json.loads(LESSONS_PATH.read_text(encoding="utf-8"))
    return tuple(validate(data))


def hints(session: dict) -> str:
    """Az itelet-szempontok a dontes elott (#59): a lecke beallitasa szerint; napi kihivasban soha; szabad
    gyakorlasban osszecsukva (a felhasznalo nyithatja ki)."""
    if session.get("daily") or session.get("exam"):
        return "hidden"
    if session.get("lesson"):
        try:
            return lesson(int(session["lesson"]))["tasks"].get("hints", "hidden")
        except LookupError:
            return "hidden"
    return "toggle"


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


def is_admin(user_id: int) -> bool:
    """Az admin minden szintet es a vizsgat varakozas nelkul kiprobalhatja (a tartalom ellenorzesehez)."""
    with db.connection() as conn:
        row = conn.execute("SELECT role = 'admin' FROM users WHERE id = %s", (user_id,)).fetchone()
    return bool(row and row[0])


def chapter_of(level: int) -> int | None:
    """A fejezet sorszama, ha a szint egy fejezet utolso szintje (utana jon a fejezetzaro)."""
    return next((n for n, levels in enumerate(CHAPTERS, 1) if levels[-1] == level), None)


def boss_progress(user_id: int) -> dict[int, dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT chapter, best_share, completed_at IS NOT NULL FROM chapter_bosses WHERE user_id = %s",
                            (user_id,)).fetchall()
    return {c: {"best_share": share, "completed": done} for c, share, done in rows}


def overview(user_id: int) -> list[dict]:
    """A szintek; a fejezet utolso szintjen a fejezetzaro allapota is. A kovetkezo fejezet elso szintje csak a
    fejezetzaro teljesitese utan nyilik - kiveve, ha a felhasznalo mar korabban elkezdte (ne zarjuk vissza)."""
    done = progress(user_id)
    bosses = boss_progress(user_id)
    admin = is_admin(user_id)
    out, unlocked = [], True
    for item in lessons():
        state = done.get(item["level"], {})
        row = {"level": item["level"], "title": item["title"], "unlocked": unlocked or admin or bool(state),
               "completed": bool(state.get("completed")), "best_share": state.get("best_share")}
        unlocked = bool(state.get("completed"))
        chapter = chapter_of(item["level"])
        if chapter and chapter in practice_boss.REGIMES:
            b = bosses.get(chapter, {})
            row["boss"] = {"chapter": chapter, "unlocked": unlocked or admin, "completed": bool(b.get("completed")),
                           "best_share": b.get("best_share"), "source": practice_boss.SOURCES[practice_boss.REGIMES[chapter]]}
            unlocked = unlocked and bool(b.get("completed"))
        out.append(row)
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
        "levels": len(lessons()), "walkthrough": steps(level)}


def steps(level: int) -> dict | None:
    """A lecke levezetese a sajat pelda-chartjan (walkthrough.py); None, ha a leckehez nincs."""
    kind = lesson(level).get("walkthrough")
    return walkthrough.BUILDERS[kind](figure(level)) if kind else None


def start(user_id: int, level: int, settings: Settings | None = None) -> dict:
    """A szint celzott feladatsora (egy uj gyakorlo kor)."""
    _require_unlocked(user_id, level)
    tasks = lesson(level)["tasks"]
    created = practice.create(user_id, settings, rounds=tasks["rounds"], only=tasks["only"], lesson=level,
                              kinds_mix=tasks.get("kinds"), manage=tasks.get("manage", False))
    analytics.track("lesson_start", user_id, level=level)
    return created


def start_boss(user_id: int, chapter: int, settings: Settings | None = None) -> dict:
    """A fejezetzaro feladatsor (#79 b): a fejezet utolso szintjenek teljesitese utan."""
    item = next((x for x in overview(user_id) if (x.get("boss") or {}).get("chapter") == chapter), None)
    if item is None:
        raise LookupError("Nincs ilyen fejezet.")
    if not item["boss"]["unlocked"]:
        raise ValueError("A fejezetzáró a fejezet utolsó szintjének teljesítése után nyílik.")
    created = practice.create(user_id, settings, rounds=practice_boss.ROUNDS, boss=chapter)
    analytics.track("boss_start", user_id, chapter=chapter)
    return created


def record_boss(user_id: int, session: dict) -> dict | None:
    """Egy befejezett fejezetzaro eredmenye: ugyanaz a mertek, mint a szinteknel (jo + balszerencses >= 60%)."""
    chapter = session.get("boss")
    if not chapter or not all(t.get("decision") for t in session["tasks"]):
        return None
    verdicts = [((t.get("result") or {}).get("review") or {}).get("verdict", {}).get("category") for t in session["tasks"]]
    share = round(sum(v in GOOD for v in verdicts) / len(verdicts), 3)
    passed = share >= PASS_SHARE
    with db.connection() as conn:
        conn.execute("INSERT INTO chapter_bosses (user_id, chapter, best_share, completed_at) "
                     "VALUES (%s, %s, %s, CASE WHEN %s THEN now() END) ON CONFLICT (user_id, chapter) DO UPDATE SET "
                     "best_share = greatest(chapter_bosses.best_share, EXCLUDED.best_share), "
                     "completed_at = coalesce(chapter_bosses.completed_at, EXCLUDED.completed_at)",
                     (user_id, chapter, share, passed))
    nxt = CHAPTERS[chapter][0] if chapter < len(CHAPTERS) else len(lessons())
    return {"chapter": chapter, "share": share, "passed": passed, "needed": PASS_SHARE, "next": nxt if passed else None}


def record(user_id: int, session: dict) -> dict | None:
    """Egy befejezett leckekor eredmenye: a jo + balszerencses arany, es teljesult-e a szint."""
    level = session.get("lesson")
    if not level or not all(t.get("decision") for t in session["tasks"]):
        return None
    verdicts = [((t.get("result") or {}).get("review") or {}).get("verdict", {}).get("category") for t in session["tasks"]]
    share = round(sum(v in GOOD for v in verdicts) / len(verdicts), 3)
    passed = share >= PASS_SHARE
    with db.connection() as conn:
        before = conn.execute("SELECT completed_at IS NOT NULL FROM lesson_progress WHERE user_id = %s AND level = %s",
                              (user_id, level)).fetchone()
        if passed and not (before and before[0]):                      # az elso teljesites (#48)
            analytics.track("lesson_pass", user_id, conn, level=level)
        conn.execute("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) "
                     "VALUES (%s, %s, %s, CASE WHEN %s THEN now() END) ON CONFLICT (user_id, level) DO UPDATE SET "
                     "best_share = greatest(lesson_progress.best_share, EXCLUDED.best_share), "
                     "completed_at = coalesce(lesson_progress.completed_at, EXCLUDED.completed_at)",
                     (user_id, level, share, passed))
    chapter = chapter_of(level)
    boss = chapter if passed and chapter and not boss_progress(user_id).get(chapter, {}).get("completed") else None
    return {"level": level, "share": share, "passed": passed, "needed": PASS_SHARE, "boss": boss,   # elobb a fejezetzaro
            "next": level + 1 if passed and level < len(lessons()) and not boss else None}
