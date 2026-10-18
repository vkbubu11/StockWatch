"""Labor-munkasor: a nehez, felhasznalonkenti feladatok (kutatougynok, kesobb esemenytanulmany, publikalt
oldalak frissitese) fajlalapu sora. A webes felulet / CLI berak, a labor-szolgaltatas (lab.run_forever)
sorban, alacsony prioritassal futtatja.

  * egy feladat egy fajl: data/queue/<munkaterulet>/<azonosito>.json (zarral, atomikusan irva)
  * sorrend: prioritas (kisebb elobb), azon belul az a munkaterulet, amelyik az utobbi 24 oraban a
    legkevesebbet futtatott (SAAS-DESIGN 4.1: meltanyos sorrend), vegul a regebbi
  * `not_before`: korabban nem indul (pl. az ejszakai ablak)
  * plafon: munkateruletenkent legfeljebb MAX_PENDING varakozo feladat; a kesz feladat 7 nap utan torlodik

A feladatot vegrehajto fuggveny a fajtahoz rendelt kezelo (HANDLERS), amely a labor folyamataban fut.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR
from .locks import file_lock, write_atomic

log = logging.getLogger(__name__)

QUEUE_DIR = DATA_DIR / "queue"
MAX_PENDING = 10
KEEP_DAYS = 7
PRIORITY = {"publish_refresh": 10, "event_study": 20, "practice_pool": 25, "agent": 30}
STALE_RUNNING_HOURS = 12          # ennyi ideje "fut" -> a labor ujraindult kozben: hibakent lezarjuk


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _lock():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    return file_lock(QUEUE_DIR / "queue")


def _path(ws: str, job_id: str):
    from . import workspace

    return QUEUE_DIR / workspace.check(ws) / f"{job_id}.json"


def _all() -> list[dict]:
    out = []
    if not QUEUE_DIR.exists():
        return out
    for path in QUEUE_DIR.glob("*/*.json"):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            log.warning("Serult munkasor-fajl: %s", path)
    return out


def _write(job: dict) -> None:
    path = _path(job["workspace"], job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(job, ensure_ascii=False, indent=1))


def submit(ws: str, kind: str, payload: dict, not_before: datetime | None = None) -> dict:
    if kind not in PRIORITY:
        raise ValueError(f"Ismeretlen feladatfajta: {kind}")
    with _lock():
        pending = [j for j in _all() if j["workspace"] == ws and j["status"] in ("queued", "running")]
        if len(pending) >= MAX_PENDING:
            raise ValueError(f"Legfeljebb {MAX_PENDING} varakozo feladat lehet - varj, amig a korabbiak lefutnak.")
        job = {"id": uuid.uuid4().hex[:12], "workspace": ws, "kind": kind, "payload": payload,
               "priority": PRIORITY[kind], "status": "queued", "message": "sorban all",
               "created": _iso(_now()), "not_before": _iso(not_before) if not_before else None,
               "started": None, "finished": None, "result": None}
        _write(job)
    return job


def get(ws: str, job_id: str) -> dict | None:
    path = _path(ws, job_id)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def jobs(ws: str, kind: str | None = None) -> list[dict]:
    return sorted((j for j in _all() if j["workspace"] == ws and (kind is None or j["kind"] == kind)),
                  key=lambda j: j["created"], reverse=True)


def update(job: dict, **fields) -> dict:
    with _lock():
        current = get(job["workspace"], job["id"]) or job
        current.update(fields)
        _write(current)
    job.update(fields)
    return current


def cancel(ws: str, job_id: str) -> dict:
    with _lock():
        job = get(ws, job_id)
        if job is None:
            raise ValueError("Nincs ilyen feladat.")
        if job["status"] != "queued":
            raise ValueError("Csak a meg el nem indult feladat vonhato vissza.")
        job.update(status="canceled", finished=_iso(_now()), message="visszavonva")
        _write(job)
    return job


def due(now: datetime | None = None) -> list[dict]:
    """A most indithato feladatok, futtatasi sorrendben."""
    now = now or _now()
    items = _all()
    recent: dict[str, int] = {}
    limit = _iso(now - timedelta(hours=24))
    for j in items:
        if j.get("started") and j["started"] >= limit:
            recent[j["workspace"]] = recent.get(j["workspace"], 0) + 1
    ready = [j for j in items if j["status"] == "queued" and (not j.get("not_before") or j["not_before"] <= _iso(now))]
    return sorted(ready, key=lambda j: (j["priority"], recent.get(j["workspace"], 0), j["created"], j["id"]))


def claim(now: datetime | None = None) -> dict | None:
    """A kovetkezo feladat atvetele (zarolva: ket labor-folyamat se vegye at ugyanazt)."""
    with _lock():
        for job in due(now):
            current = get(job["workspace"], job["id"])
            if current and current["status"] == "queued":
                current.update(status="running", started=_iso(now or _now()), message="fut")
                _write(current)
                return current
    return None


HANDLERS: dict = {}        # fajta -> fn(job, settings) -> dict (a feladat eredmenye)


def _handlers() -> dict:
    if not HANDLERS:
        from .events import service as events
        from .practice import sampler as practice
        from .research_agent import service as agent

        HANDLERS["agent"] = agent.run_job
        HANDLERS["event_study"] = events.run_job
        HANDLERS["practice_pool"] = practice.run_job
    return HANDLERS


def run_next(settings, now: datetime | None = None) -> bool:
    """A labor hivja: egy esedekes feladat vegrehajtasa. Igaz, ha futott valami."""
    recover()
    job = claim(now)
    if job is None:
        return False
    handler = _handlers().get(job["kind"])
    try:
        if handler is None:
            raise ValueError(f"Nincs kezelo: {job['kind']}")
        result = handler(job, settings) or {}
        update(job, status="done", finished=_iso(_now()), message=result.get("message", "kesz"), result=result)
    except Exception as exc:
        log.exception("Munkasor: a(z) %s feladat hibara futott", job["id"])
        update(job, status="error", finished=_iso(_now()), message=f"{type(exc).__name__}: {exc}"[:300])
    return True


def recover(now: datetime | None = None, all_running: bool = False) -> int:
    """A labor ujraindulasa elott "futo"-kent maradt feladatok lezarasa (kulonben orokke futnanak).
    `all_running`: a labor indulasakor - egyetlen labor-folyamat van, tehat ami "fut", az mar nem fut."""
    now = now or _now()
    limit = _iso(now + timedelta(seconds=1)) if all_running else _iso(now - timedelta(hours=STALE_RUNNING_HOURS))
    fixed = 0
    if not QUEUE_DIR.exists():
        return 0
    with _lock():
        for job in _all():
            if job["status"] == "running" and (job.get("started") or "") < limit:
                job.update(status="error", finished=_iso(now), message="megszakadt (a labor ujraindult)")
                _write(job)
                fixed += 1
    return fixed


def prune(now: datetime | None = None) -> int:
    limit = _iso((now or _now()) - timedelta(days=KEEP_DAYS))
    removed = 0
    if not QUEUE_DIR.exists():
        return 0
    with _lock():
        for job in _all():
            if job["status"] in ("done", "error", "canceled") and (job.get("finished") or "") < limit:
                _path(job["workspace"], job["id"]).unlink(missing_ok=True)
                removed += 1
    return removed


def has_due(now: datetime | None = None) -> bool:
    return bool(due(now))
