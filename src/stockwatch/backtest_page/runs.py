"""Mentett Backteszt-futtatasok: data/backtest/runs/<id>.json.gz, legfeljebb MAX_RUNS darab."""
from __future__ import annotations

import gzip
import json
import math
import re
import uuid

import numpy as np
import pandas as pd

from .data import BT_DIR

RUNS_DIR = BT_DIR / "runs"
MAX_RUNS = 50
RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _default(o):
    if isinstance(o, pd.Timestamp):
        return f"{o:%Y-%m-%d}"
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


def _clean(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def save(result: dict) -> str:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    result = _clean(json.loads(json.dumps(result, default=_default)))
    result["id"] = run_id
    (RUNS_DIR / f"{run_id}.json.gz").write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False).encode()))
    _prune()
    return run_id


def load(run_id: str) -> dict | None:
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("Ervenytelen futtatas-azonosito")
    path = RUNS_DIR / f"{run_id}.json.gz"
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None


def delete(run_id: str) -> bool:
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("Ervenytelen futtatas-azonosito")
    path = RUNS_DIR / f"{run_id}.json.gz"
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def _files():
    return sorted(RUNS_DIR.glob("*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True) if RUNS_DIR.exists() else []


def listing() -> list[dict]:
    out = []
    for path in _files():
        try:
            r = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError):
            continue
        a = (r.get("periods") or {}).get("all") or {}
        out.append({"id": r.get("id"), "created_at": r.get("created_at"), "symbol": r.get("symbol"),
                    "strategy": r.get("strategy"), "strategy_label": r.get("strategy_label"),
                    "start": r.get("start"), "end": r.get("end"), "holdout": r.get("holdout"),
                    "overrides": r.get("overrides"), "n": a.get("n"), "win": a.get("win"), "ev": a.get("ev"),
                    "cagr": (r.get("strategy_metrics") or {}).get("cagr"),
                    "max_dd": (r.get("strategy_metrics") or {}).get("max_dd"),
                    "bh_cagr": (r.get("buy_hold_metrics") or {}).get("cagr")})
    return out


def _prune() -> None:
    for path in _files()[MAX_RUNS:]:
        path.unlink(missing_ok=True)
