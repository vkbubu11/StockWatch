"""A gyakorlo mod tarolasa.

  * feladatkeszlet: data/practice/pool.json.gz (kozos piaci / szintetikus adatbol), legfeljebb POOL_LIMIT_MB; az uj
    ujjlenyomatu keszlet felulirja a regit
  * korok (#32): az adatbazisban (practice_sessions, practice_tasks), felhasznalonkent legfeljebb MAX_SESSIONS
    (a legregebbi torlodik eloszor). Minden lekerdezes a felhasznalo azonositojaval szur: mas felhasznalo kore
    "nincs ilyen".
"""
from __future__ import annotations

import gzip
import json
import re
import secrets
from contextlib import contextmanager
from datetime import timezone

from psycopg.types.json import Jsonb

from .. import db
from ..config import DATA_DIR

POOL_DIR = DATA_DIR / "practice"
POOL_LIMIT_MB = 5
MAX_SESSIONS = 30                                   # felhasznalonkent (egy kor ~50-150 KB tomoritve)
ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz"            # betuk: az azonositoban ne legyen szam (pl. evszam)
SESSION_ID_RE = re.compile(r"^[a-z]{12}$")


def new_id(length: int = 12) -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
# Feladatkeszlet
# ---------------------------------------------------------------------------
def pool_path():
    return POOL_DIR / "pool.json.gz"


def save_pool(pool: dict) -> int:
    raw = gzip.compress(json.dumps(pool, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(raw) > POOL_LIMIT_MB * 1024 * 1024:
        raise ValueError(f"A feladatkeszlet {len(raw) / 1e6:.1f} MB - a plafon {POOL_LIMIT_MB} MB.")
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    path = pool_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    return len(raw)


def load_pool() -> dict | None:
    path = pool_path()
    try:
        return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Korok (adatbazis)
# ---------------------------------------------------------------------------
SESSION_COLS = "id, created_at, seed, rounds, mix, pool_fingerprint, code, warning, summary, lesson, daily"
TASK_COLS = "k, task_id, symbol, day, category, source, breakout, decision, decided_at, points, kind, params, quality"


def _check(session_id: str) -> None:
    if not SESSION_ID_RE.match(session_id or ""):
        raise ValueError("Ervenytelen munkamenet-azonosito.")


def _iso(value) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


def _session(row, tasks: list[dict]) -> dict:
    sid, created, seed, rounds, mix, fingerprint, code, warning, summary, lesson, daily = row
    out = {"id": sid, "created": _iso(created), "seed": seed, "rounds": rounds, "mix": mix,
           "pool_fingerprint": fingerprint, "code": code, "warning": warning, "lesson": lesson,
           "daily": f"{daily:%Y-%m-%d}" if daily else None, "tasks": tasks}
    return out | ({"summary": summary} if summary is not None else {})


def _task(row, result: bytes | None) -> dict:
    k, task_id, symbol, day, category, source, breakout, decision, decided_at, points, kind, params, quality = row
    task = {"task_id": task_id, "symbol": symbol, "date": f"{day:%Y-%m-%d}", "category": category, "source": source,
            "breakout": breakout, "decision": decision, "points": points, "kind": kind, "params": params,
            "quality": quality,
            "result": json.loads(gzip.decompress(result)) if result is not None else None}
    return task | ({"decided_at": _iso(decided_at)} if decided_at else {})


def _load(conn, session_id: str, user_id: int, lock: bool = False) -> dict | None:
    _check(session_id)
    row = conn.execute(f"SELECT {SESSION_COLS} FROM practice_sessions WHERE id = %s AND user_id = %s"
                       + (" FOR UPDATE" if lock else ""), (session_id, user_id)).fetchone()
    if row is None:
        return None
    tasks = [_task(t[:-1], t[-1]) for t in conn.execute(
        f"SELECT {TASK_COLS}, result FROM practice_tasks WHERE session_id = %s ORDER BY k", (session_id,))]
    return _session(row, tasks)


def load_session(session_id: str, user_id: int) -> dict | None:
    with db.connection() as conn:
        return _load(conn, session_id, user_id)


def _save(conn, session: dict, user_id: int, only: list[int] | None = None) -> None:
    conn.execute(
        "INSERT INTO practice_sessions (id, user_id, seed, rounds, mix, pool_fingerprint, code, warning, summary, lesson, "
        "daily) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary "
        "WHERE practice_sessions.user_id = EXCLUDED.user_id",
        (session["id"], user_id, session["seed"], session["rounds"], Jsonb(session["mix"]), session["pool_fingerprint"],
         session["code"], session.get("warning"), Jsonb(session["summary"]) if session.get("summary") else None,
         session.get("lesson"), session.get("daily")))
    rows = []
    for k, t in enumerate(session["tasks"]):
        if only is not None and k not in only:
            continue
        res = t.get("result")
        rows.append((session["id"], k, t["task_id"], t["symbol"], t["date"], t["category"], t["source"],
                     Jsonb(t["breakout"]) if t.get("breakout") else None,
                     Jsonb(t["decision"]) if t.get("decision") else None,
                     ((res or {}).get("review") or {}).get("verdict", {}).get("category") if res else None,
                     ((res or {}).get("user") or {}).get("r") if res else None, t.get("decided_at"),
                     gzip.compress(json.dumps(res, ensure_ascii=False).encode("utf-8")) if res is not None else None,
                     t.get("points"), ((res or {}).get("user") or {}).get("stop_atr") if res else None,
                     t.get("kind") or "decision", Jsonb(t["params"]) if t.get("params") else None, t.get("quality")))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO practice_tasks (session_id, k, task_id, symbol, day, category, source, breakout, decision, "
            "verdict, r, decided_at, result, points, stop_atr, kind, params, quality) VALUES (%s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (session_id, k) DO UPDATE SET decision = EXCLUDED.decision, "
            "verdict = EXCLUDED.verdict, r = EXCLUDED.r, decided_at = EXCLUDED.decided_at, result = EXCLUDED.result, "
            "points = EXCLUDED.points, stop_atr = EXCLUDED.stop_atr, quality = EXCLUDED.quality", rows)


def create_session(session: dict, user_id: int) -> None:
    """Uj kor mentese; a felhasznalo MAX_SESSIONS-nel regebbi (nem napi) korei torlodnek. A napi kihivas korei
    maradnak - a ranglista ezekbol szamol. shortcut: napi ~50 KB / felhasznalo orokre; ha a Neon merete szamit,
    a regi napi korok felfedes-blobja (result) torolheto, a pont oszlop marad."""
    with db.connection() as conn:
        _save(conn, session, user_id)
        conn.execute("DELETE FROM practice_sessions WHERE user_id = %s AND daily IS NULL AND id NOT IN (SELECT id "
                     "FROM practice_sessions WHERE user_id = %s AND daily IS NULL ORDER BY created_at DESC, id LIMIT %s)",
                     (user_id, user_id, MAX_SESSIONS))


@contextmanager
def editing(session_id: str, user_id: int):
    """A kor zarolva (FOR UPDATE) a blokk vegeig; `save(k)` a k. helyzetet (es az osszegzest) irja vissza; a
    kapcsolat ugyanabban a tranzakcioban tovabbi irasra (pl. XP) is hasznalhato."""
    with db.connection() as conn:
        session = _load(conn, session_id, user_id, lock=True)
        if session is None:
            raise LookupError("Nincs ilyen munkamenet.")
        yield session, lambda k: _save(conn, session, user_id, only=[k]), conn


def daily_session(user_id: int, day: str) -> str | None:
    with db.connection() as conn:
        row = conn.execute("SELECT id FROM practice_sessions WHERE user_id = %s AND daily = %s", (user_id, day)).fetchone()
    return row[0] if row else None


def delete_session(session_id: str, user_id: int) -> bool:
    _check(session_id)
    with db.connection() as conn:
        return conn.execute("DELETE FROM practice_sessions WHERE id = %s AND user_id = %s",
                            (session_id, user_id)).rowcount > 0


def sessions(user_id: int, limit: int = 30) -> list[dict]:
    """A felhasznalo legutobbi korei (a helyzetek dontesevel, felfedes nelkul), a legujabb elol."""
    with db.connection() as conn:
        rows = conn.execute(f"SELECT {SESSION_COLS} FROM practice_sessions WHERE user_id = %s "
                            "ORDER BY created_at DESC, id LIMIT %s", (user_id, limit)).fetchall()
        tasks: dict[str, list] = {r[0]: [] for r in rows}
        if rows:
            for t in conn.execute(f"SELECT session_id, {TASK_COLS} FROM practice_tasks WHERE session_id = ANY(%s) "
                                  "ORDER BY session_id, k", (list(tasks),)):
                tasks[t[0]].append(_task(t[1:], None))
    return [_session(r, tasks[r[0]]) for r in rows]


def pool_bytes() -> int:
    path = pool_path()
    return path.stat().st_size if path.exists() else 0
