"""Kiprobalhato helyzet a kezdooldalon, regisztracio nelkul.

  * mindig ugyanaz az egy helyzet a feladatkeszletbol (rogzitett seed); a dontes ugyanazzal a kiertekelessel kap
    iteletet es levezetest, mint a gyakorlasban - de semmi nem mentodik (nincs felhasznalo, nincs kor)
  * meres nevtelenul (analytics): megnyitas (demo_view), dontes (demo_decide), es a regisztraciok forrasa (demo)
"""
from __future__ import annotations

from .config import Settings
from .practice import sampler, session, store

SEED = 20261031
ONLY = {"strategy": None, "pattern": None}       # olyan helyzet, ahol van mit merlegelni (jelzes vagy alakzat)


def _task() -> dict:
    pool = store.load_pool()
    if not pool:
        raise LookupError("Meg nincs feladatkeszlet.")
    pick = sampler.sample(pool, SEED, 1, None, ONLY)[0]
    return pick | {"kind": "decision", "task_id": "demo", "decision": None, "result": None}


def payload(settings: Settings) -> dict:
    df, i = session._frame(_task(), settings)
    return {"chart": session.hidden_chart(df, i), "max_days": sampler.MAX_DAYS}


def decide(body: dict, settings: Settings) -> dict:
    task = _task()
    fake = {"id": "demo", "seed": SEED, "created": session._now(), "tasks": [task]}     # mentes nelkul
    answer, result, points = session._evaluate(fake, task, 0, body, settings)
    return session.reveal(task | {"decision": answer, "result": result | {"points": points}})
