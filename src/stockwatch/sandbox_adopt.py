"""Parameterek atvetele a Homokozoba (kutatougynok-jelolt, kesobb a publikalt oldal "Fork" gombja).

A Homokozo SOHA nincs a paper fiokhoz kotve (config.PAPER_STRATEGIES), az ertesitese itt mindig ki van
kapcsolva (alerts=false): a monitor jelez es virtualisan kovet, de semmi nem megy ki es semmit nem kot.
Az atvetel elott a regi homokozo-beallitas egy elozmenylistaba kerul (legfeljebb HISTORY_MAX), a valtozas
a beallitas-valtozasnaploba (ledger.log_config_change).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import config, ledger, workspace
from .config import EntrySettings, PullbackParams, load_settings
from .locks import write_atomic

log = logging.getLogger(__name__)

HISTORY_MAX = 10


def history_path(ws: str = workspace.DEFAULT):
    return workspace.user_dir(ws, "sandbox") / "history.json"


def history(ws: str = workspace.DEFAULT) -> list[dict]:
    path = history_path(ws)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def _remember(previous: dict, source: str, ws: str) -> None:
    items = [{"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "replaced_by": source,
              "sandbox": previous}] + history(ws)
    path = history_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(items[:HISTORY_MAX], ensure_ascii=False, indent=1))


def adopt(engine: str, params: dict, source: str, ws: str = workspace.DEFAULT, request_backtest: bool = True) -> dict:
    """`engine`: pullback (PullbackParams mezoi) | breakout (lab.EDITABLE mezok az elo `entry` felett).
    Visszaadja az uj homokozo-beallitast. Ervenytelen parameter -> ValueError, es semmi nem valtozik."""
    workspace._require_admin(ws)
    before = load_settings()
    data = before.strategies.model_dump()
    previous = dict(data["sandbox"])
    if engine == "pullback":
        checked = PullbackParams(**(before.strategies.sandbox.params.model_dump() | dict(params)))
        data["sandbox"].update(engine="pullback", params=checked.model_dump(), entry={})
    elif engine == "breakout":
        from . import lab

        values = lab.validate_entry(dict(params), before.entry)
        data["sandbox"].update(engine="breakout", entry={k: getattr(values, k) for k in params})
        EntrySettings(**(before.entry.model_dump() | data["sandbox"]["entry"]))
    else:
        raise ValueError(f"Ismeretlen motor: {engine}")
    data["sandbox"].update(enabled=True, alerts=False)
    strategies = config.StrategiesSettings(**data)                # validalas MENTES ELOTT
    if strategies.paper == "sandbox":                             # a validator ezt amugy sem engedne
        raise ValueError("A homokozo nem kotheto a paper fiokhoz.")
    after = before.model_copy(update={"strategies": strategies})
    if ledger.log_config_change(before, after, source=source) is not None:
        _remember(previous, source, ws)
        config.save_strategies(strategies)
    if request_backtest and engine == "pullback":
        from . import meanrev_research

        meanrev_research.request_sandbox(strategies.sandbox.params)
    log.info("Homokozo atveve (%s): %s", source, engine)
    return strategies.sandbox.model_dump()
