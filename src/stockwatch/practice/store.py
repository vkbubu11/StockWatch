"""A gyakorlo mod tarolasa.

  * munkamenetek: data/users/<munkaterulet>/practice/sessions/<azonosito>.json, legfeljebb MAX_SESSIONS kor
    (a legregebbi torlodik eloszor)
  * feladatkeszlet: data/practice/pool.json.gz (kozos piaci adatbol), legfeljebb POOL_LIMIT_MB; az uj
    ujjlenyomatu keszlet felulirja a regit
"""
from __future__ import annotations

import gzip
import json
import re
import secrets

from .. import workspace
from ..config import DATA_DIR
from ..locks import write_atomic

POOL_DIR = DATA_DIR / "practice"
POOL_LIMIT_MB = 5
MAX_SESSIONS = 100
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
# Munkamenetek
# ---------------------------------------------------------------------------
def directory(ws: str = workspace.DEFAULT):
    return workspace.user_dir(ws, "practice") / "sessions"


def session_path(session_id: str, ws: str = workspace.DEFAULT):
    if not SESSION_ID_RE.match(session_id or ""):
        raise ValueError("Ervenytelen munkamenet-azonosito.")
    return directory(ws) / f"{session_id}.json"


def save_session(session: dict, ws: str = workspace.DEFAULT) -> None:
    path = session_path(session["id"], ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(session, ensure_ascii=False))
    prune(ws)


def load_session(session_id: str, ws: str = workspace.DEFAULT) -> dict | None:
    path = session_path(session_id, ws)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def delete_session(session_id: str, ws: str = workspace.DEFAULT) -> bool:
    path = session_path(session_id, ws)
    existed = path.exists()
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".lock").unlink(missing_ok=True)
    return existed


def sessions(ws: str = workspace.DEFAULT) -> list[dict]:
    folder = directory(ws)
    out = []
    for path in folder.glob("*.json") if folder.exists() else []:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda s: s.get("created") or "", reverse=True)


def prune(ws: str = workspace.DEFAULT) -> int:
    folder = directory(ws)
    if not folder.exists():
        return 0
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    removed = 0
    while len(files) > MAX_SESSIONS:
        victim = files.pop(0)
        victim.unlink(missing_ok=True)
        victim.with_name(victim.name + ".lock").unlink(missing_ok=True)
        removed += 1
    return removed


def usage_bytes() -> int:
    base = workspace.USERS_DIR
    own = sum(p.stat().st_size for p in base.glob("*/practice/sessions/*.json")) if base.exists() else 0
    path = pool_path()
    return own + (path.stat().st_size if path.exists() else 0)
