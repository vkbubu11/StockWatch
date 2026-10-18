"""A kutatougynok tara: data/users/<munkaterulet>/agent/

    briefs/<brief-id>.yaml                      legfeljebb MAX_BRIEFS
    runs/<run-id>/status.json                   allapot (sorban / fut / kesz / hiba), haladas
    runs/<run-id>/log.jsonl                     a keresesi lepesek (legfeljebb LOG_MAX_LINES sor)
    runs/<run-id>/report.json, report.md        a jelentes

Plafon: legfeljebb MAX_RUNS futas es LIMIT_MB; a legregebbi (befejezett) futas torlodik eloszor.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone

from .. import workspace
from ..locks import write_atomic
from .brief import Brief

log = logging.getLogger(__name__)

MAX_BRIEFS = 20
MAX_RUNS = 30
LIMIT_MB = 50
LOG_MAX_LINES = 5000
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9a-f]{8}$")
BRIEF_ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root(ws: str = workspace.DEFAULT):
    return workspace.user_dir(ws, "agent")


# ---------------------------------------------------------------------------
# Briefek
# ---------------------------------------------------------------------------
def _brief_path(brief_id: str, ws: str):
    if not BRIEF_ID_RE.match(brief_id or ""):
        raise ValueError("Ervenytelen brief-azonosito.")
    return root(ws) / "briefs" / f"{brief_id}.yaml"


def save_brief(brief: Brief, ws: str = workspace.DEFAULT) -> str:
    path = _brief_path(brief.id, ws)
    if not path.exists() and len(list_briefs(ws)) >= MAX_BRIEFS:
        raise ValueError(f"Legfeljebb {MAX_BRIEFS} brief tarolhato - torolj egy regebbit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, brief.to_yaml())
    return brief.id


def load_brief(brief_id: str, ws: str = workspace.DEFAULT) -> Brief:
    path = _brief_path(brief_id, ws)
    if not path.exists():
        raise ValueError("Nincs ilyen brief.")
    return Brief.from_yaml(path.read_text(encoding="utf-8"))


def list_briefs(ws: str = workspace.DEFAULT) -> list[dict]:
    folder = root(ws) / "briefs"
    out = []
    for path in sorted(folder.glob("*.yaml")) if folder.exists() else []:
        try:
            brief = Brief.from_yaml(path.read_text(encoding="utf-8"))
            out.append({"id": path.stem, "brief": brief.model_dump()})
        except Exception as exc:
            log.warning("Serult brief: %s (%s)", path, exc)
    return out


def delete_brief(brief_id: str, ws: str = workspace.DEFAULT) -> None:
    _brief_path(brief_id, ws).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Futasok
# ---------------------------------------------------------------------------
def run_dir(run_id: str, ws: str = workspace.DEFAULT):
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("Ervenytelen futas-azonosito.")
    return root(ws) / "runs" / run_id


def new_run(brief: Brief, ws: str = workspace.DEFAULT, state: str = "queued", **extra) -> str:
    run_id = f"{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"
    folder = run_dir(run_id, ws)
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic(folder / "brief.yaml", brief.to_yaml())
    set_status(run_id, ws, state=state, message="sorban all" if state == "queued" else "indul",
               brief_id=brief.id, brief_name=brief.name, engine=brief.engine, created=_now(), **extra)
    prune(ws)
    return run_id


def status(run_id: str, ws: str = workspace.DEFAULT) -> dict | None:
    path = run_dir(run_id, ws) / "status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def set_status(run_id: str, ws: str = workspace.DEFAULT, **fields) -> dict:
    current = status(run_id, ws) or {"run_id": run_id}
    current.update(fields, updated=_now())
    write_atomic(run_dir(run_id, ws) / "status.json", json.dumps(current, ensure_ascii=False, indent=1))
    return current


def run_brief(run_id: str, ws: str = workspace.DEFAULT) -> Brief:
    return Brief.from_yaml((run_dir(run_id, ws) / "brief.yaml").read_text(encoding="utf-8"))


class LogWriter:
    """A lepesnaplo irasa (sor / lepes), plafonnal: a LOG_MAX_LINES utani lepesek csak szamlalodnak."""

    def __init__(self, run_id: str, ws: str = workspace.DEFAULT) -> None:
        self.path = run_dir(run_id, ws) / "log.jsonl"
        self.lines = 0
        self.dropped = 0
        self.path.write_text("", encoding="utf-8")

    def __call__(self, rec: dict) -> None:
        if self.lines >= LOG_MAX_LINES:
            self.dropped += 1
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.lines += 1


def read_log(run_id: str, ws: str = workspace.DEFAULT, limit: int = LOG_MAX_LINES) -> list[dict]:
    path = run_dir(run_id, ws) / "log.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[:limit]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_report(run_id: str, report: dict, markdown: str, ws: str = workspace.DEFAULT) -> None:
    folder = run_dir(run_id, ws)
    write_atomic(folder / "report.json", json.dumps(report, ensure_ascii=False, indent=1, default=str))
    write_atomic(folder / "report.md", markdown)
    prune(ws)


def load_report(run_id: str, ws: str = workspace.DEFAULT) -> dict | None:
    path = run_dir(run_id, ws) / "report.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def runs(ws: str = workspace.DEFAULT) -> list[dict]:
    folder = root(ws) / "runs"
    out = []
    for path in folder.iterdir() if folder.exists() else []:
        if path.is_dir() and RUN_ID_RE.match(path.name):
            st = status(path.name, ws)
            if st:
                out.append(st)
    return sorted(out, key=lambda s: (s.get("created") or "", s["run_id"]), reverse=True)


def validation_looks(engine: str, ws: str = workspace.DEFAULT) -> int:
    """Hany befejezett futas nezte meg eddig a validacios idoszakot ennel a motornal (a mostanit is)."""
    return sum(1 for s in runs(ws) if s.get("engine") == engine and s.get("validated"))


def _size(path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def prune(ws: str = workspace.DEFAULT) -> int:
    """MAX_RUNS / LIMIT_MB: a legregebbi befejezett futasok torlodnek (a sorban allo / futo nem)."""
    folder = root(ws) / "runs"
    if not folder.exists():
        return 0
    items = [s for s in runs(ws)]
    finished = [s for s in reversed(items) if s.get("state") in ("done", "error", "canceled")]
    total = sum(_size(run_dir(s["run_id"], ws)) for s in items)
    removed = 0
    while finished and (len(items) - removed > MAX_RUNS or total > LIMIT_MB * 1024 * 1024):
        victim = finished.pop(0)
        path = run_dir(victim["run_id"], ws)
        total -= _size(path)
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


def usage_bytes() -> int:
    base = workspace.USERS_DIR
    return sum(_size(p) for p in base.glob("*/agent") if p.is_dir()) if base.exists() else 0


def latest_done(ws: str = workspace.DEFAULT, since: str | None = None) -> dict | None:
    """A legutobbi kesz futas allapota (`since` ISO idopont utan befejezve) - a reggeli osszefoglalonak."""
    for st in runs(ws):
        if st.get("state") == "done" and (since is None or (st.get("finished") or "") > since):
            return st
    return None
