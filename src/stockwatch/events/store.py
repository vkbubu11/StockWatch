"""Mentett esemenytanulmanyok: data/users/<munkaterulet>/events/studies/<azonosito>.json.gz.

Legfeljebb MAX_STUDIES tanulmany es LIMIT_MB; a legregebbi torlodik eloszor. A kozos forrasadat (Form 4,
indexvaltozasok, szektorok) a kozos data/events/ alatt van, a sajat moduljaiban.
"""
from __future__ import annotations

import gzip
import json
import re
import uuid
from datetime import datetime, timezone

from .. import workspace

MAX_STUDIES = 30
LIMIT_MB = 30
STUDY_ID_RE = re.compile(r"^[0-9]{8}-[0-9a-f]{8}$")


def directory(ws: str = workspace.DEFAULT):
    return workspace.user_dir(ws, "events") / "studies"


def _path(study_id: str, ws: str):
    if not STUDY_ID_RE.match(study_id or ""):
        raise ValueError("Ervenytelen tanulmany-azonosito.")
    return directory(ws) / f"{study_id}.json.gz"


def new_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"


def save(study: dict, ws: str = workspace.DEFAULT) -> str:
    study_id = study.get("id") or new_id()
    study["id"] = study_id
    path = _path(study_id, ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(study, ensure_ascii=False, default=str).encode("utf-8")))
    tmp.replace(path)
    prune(ws)
    return study_id


def load(study_id: str, ws: str = workspace.DEFAULT) -> dict | None:
    path = _path(study_id, ws)
    try:
        return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None
    except (OSError, ValueError):
        return None


def delete(study_id: str, ws: str = workspace.DEFAULT) -> bool:
    path = _path(study_id, ws)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def listing(ws: str = workspace.DEFAULT) -> list[dict]:
    folder = directory(ws)
    out = []
    for path in sorted(folder.glob("*.json.gz"), reverse=True) if folder.exists() else []:
        study = load(path.name[:-8], ws)
        if study:
            s = study.get("result", {}).get("summary") or {}
            out.append({"id": study["id"], "created": study.get("created"), "spec": study.get("spec"),
                        "n": study.get("result", {}).get("n"), "mean": s.get("mean"), "hit": s.get("hit")})
    return out


def prune(ws: str = workspace.DEFAULT) -> int:
    folder = directory(ws)
    if not folder.exists():
        return 0
    files = sorted(folder.glob("*.json.gz"), key=lambda p: p.stat().st_mtime)
    removed = 0
    while files and (len(files) > MAX_STUDIES or sum(p.stat().st_size for p in files) > LIMIT_MB * 1024 * 1024):
        files.pop(0).unlink(missing_ok=True)
        removed += 1
    return removed


def usage_bytes() -> int:
    base = workspace.USERS_DIR
    return sum(p.stat().st_size for p in base.glob("*/events/studies/*.json.gz")) if base.exists() else 0
