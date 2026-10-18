"""A kutatougynok futtatasa.

  * CLI: azonnal, a sajat folyamatban (`python cli.py agent run --brief briefs/x.yaml`)
  * web: a labor-munkasoron at (labqueue) - "most" vagy "ma ejjel". Az ejszakai futas a monitor zaras utani
    kore utan indul (NIGHT_START, New York-i ido), es kemeny hatarideje van (DEADLINE, budapesti ido), hogy a
    jelentes a reggeli osszefoglalo elott kesz legyen. A homokozo-atvetel kulon, megerositett lepes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from .. import labqueue, sessions, usage, workspace
from ..config import Settings, load_settings
from . import report as report_mod
from . import store
from .brief import Brief
from . import search as search_mod
from .space import Space

log = logging.getLogger(__name__)

NIGHT_START = (20, 0)                 # New York: a monitor zaras utani indexszkennelesenek vege (lab.BUSY_WINDOW)
DEADLINE = (6, 0)                     # Europe/Budapest: a jelentes eddig kesz (a reggeli osszefoglalo 06:30)
BUDAPEST_TZ = "Europe/Budapest"


def _at(day, hour: int, minute: int, tz: str) -> pd.Timestamp:
    """Egy nap adott (fali) idopontja az adott idozonaban - nyari / teli idovaltaskor is helyesen."""
    return pd.Timestamp(f"{pd.Timestamp(day):%Y-%m-%d} {hour:02d}:{minute:02d}").tz_localize(tz)


def next_deadline(t: pd.Timestamp) -> pd.Timestamp:
    """Az elso budapesti DEADLINE (06:00) a `t` idopont UTAN."""
    local = t.tz_convert(BUDAPEST_TZ)
    d = _at(local, *DEADLINE, BUDAPEST_TZ)
    return d if d > local else _at(local + pd.Timedelta(days=1), *DEADLINE, BUDAPEST_TZ)


def night_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """(legkorabbi indulas, hatarido): ha most ejszakai ablak van (20:00 NY utan, a reggeli hatarido elott),
    azonnal indulhat; kulonben ma 20:00-kor (NY). A hatarido az indulas utani elso 06:00 (Budapest)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    now = now if now.tzinfo else now.tz_localize("UTC")
    ny = now.tz_convert(sessions.MARKET_TZ)
    tonight = _at(ny, *NIGHT_START, sessions.MARKET_TZ)
    last_night = _at(ny - pd.Timedelta(days=1), *NIGHT_START, sessions.MARKET_TZ)
    if now >= tonight:
        start, deadline = now, next_deadline(tonight)
    elif now < next_deadline(last_night):
        start, deadline = now, next_deadline(last_night)
    else:
        start, deadline = tonight, next_deadline(tonight)
    return start.tz_convert("UTC").to_pydatetime(), deadline.tz_convert("UTC").to_pydatetime()


def run(brief: Brief, settings: Settings | None = None, ws: str = workspace.DEFAULT, run_id: str | None = None,
        deadline: datetime | None = None, progress=None, clock=None) -> dict:
    """Egy teljes futas: kereses -> dontosok -> jelentes (JSON + Markdown). Visszaadja a jelentest."""
    settings = settings or load_settings()
    run_id = run_id or store.new_run(brief, ws, state="running")
    started = datetime.now(timezone.utc)
    store.set_status(run_id, ws, state="running", message="indul", started=started.isoformat(timespec="seconds"))
    say = progress or (lambda msg: None)

    def progress_fn(msg: str) -> None:
        store.set_status(run_id, ws, message=str(msg)[:200])
        say(msg)

    try:
        with usage.meter(ws, "agent"):
            space = Space.from_brief(brief, settings)
            symbols = search_mod.universe_symbols(brief, settings, ws)
            if not symbols:
                raise ValueError("Az univerzum ures (nincs letoltott indexosszetetel / watchlist).")
            engine = search_mod.make_engine(space, brief, settings, symbols, progress_fn)
            budget = search_mod.Budget(brief.search.max_variants, brief.search.max_cpu_minutes, deadline,
                            **({"clock": clock} if clock else {}))
            writer = store.LogWriter(run_id, ws)
            search = search_mod.Search(brief, settings, engine, space, budget, writer, progress_fn)
            result = search.run()
            looks = store.validation_looks(brief.engine, ws) + (1 if any(f.get("checked") for f in result["finalists"]) else 0)
            runtime = {"run_id": run_id, "cpu_seconds": round(budget.cpu_seconds, 1),
                       "started": started.isoformat(timespec="seconds"),
                       "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "deadline": deadline.isoformat(timespec="seconds") if deadline else None,
                       "log_dropped": writer.dropped}
            report = report_mod.build(brief, space, engine, search, result, symbols=len(engine.symbols),
                                      looks=looks, runtime=runtime)
            store.save_report(run_id, report, report_mod.markdown(report), ws)
        best = report["finalists"][0] if report["finalists"] else None
        store.set_status(run_id, ws, state="done", message="kesz", finished=runtime["finished"],
                         validated=any(f["checked"] for f in report["finalists"]),
                         tried=report["tried"]["total"], finalists=len(report["finalists"]),
                         best_ev_lo=(best or {}).get("assess", {}).get("score"), stopped=report["stopped"])
        return report
    except Exception as exc:
        log.exception("Kutatougynok: a futas hibara futott (%s)", run_id)
        store.set_status(run_id, ws, state="error", message=f"{type(exc).__name__}: {exc}"[:300],
                         finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        raise


def submit(brief_id: str, ws: str = workspace.DEFAULT, when: str = "now", now: datetime | None = None) -> dict:
    """A brief futtatasa a labor-munkasoron: when = now (amint a labor sorra veszi) | night (ma ejjel)."""
    if when not in ("now", "night"):
        raise ValueError("when: now | night")
    if not usage.allow(ws, "agent"):
        raise ValueError("Elfogyott a havi kutatasi keret.")
    brief = store.load_brief(brief_id, ws)
    not_before, deadline = night_window(now) if when == "night" else (None, None)
    run_id = store.new_run(brief, ws, state="queued", when=when,
                           not_before=not_before.isoformat(timespec="seconds") if not_before else None)
    job = labqueue.submit(ws, "agent", {"run_id": run_id, "brief_id": brief_id,
                                        "deadline": deadline.isoformat(timespec="seconds") if deadline else None},
                          not_before=not_before)
    store.set_status(run_id, ws, job_id=job["id"])
    return {"run_id": run_id, "job": job}


def run_job(job: dict, settings: Settings) -> dict:
    """A labor-munkasor kezeloje."""
    ws, payload = job["workspace"], job["payload"]
    run_id = payload["run_id"]
    deadline = datetime.fromisoformat(payload["deadline"]) if payload.get("deadline") else None
    report = run(store.run_brief(run_id, ws), settings, ws, run_id=run_id, deadline=deadline)
    return {"message": f"kesz: {len(report['finalists'])} jelolt, {report['tried']['total']} valtozat",
            "run_id": run_id}


def adopt(run_id: str, rank: int, ws: str = workspace.DEFAULT) -> dict:
    """Egy jelolt atvetele a Homokozoba (alerts=false, paper-kotes nelkul) - a felulet elotte megerositest ker."""
    from .. import sandbox_adopt

    report = store.load_report(run_id, ws)
    if report is None:
        raise ValueError("Nincs ilyen jelentes.")
    finalist = next((f for f in report["finalists"] if f["rank"] == int(rank)), None)
    if finalist is None:
        raise ValueError("Nincs ilyen jelolt.")
    adopt = finalist["adopt"]
    return sandbox_adopt.adopt(adopt["engine"], adopt["params"], source=f"agent:{run_id}#{rank}", ws=ws)


def cancel(run_id: str, ws: str = workspace.DEFAULT) -> dict:
    st = store.status(run_id, ws)
    if not st or not st.get("job_id"):
        raise ValueError("Nincs ilyen sorban allo futas.")
    labqueue.cancel(ws, st["job_id"])
    return store.set_status(run_id, ws, state="canceled", message="visszavonva",
                            finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def deadline_in(minutes: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
