"""Gepi tanulas arnyek-uzemben: a modell becslese a jelzesek melle kerul, de SEMMIROL nem dont.

    settings.yaml -> lab.shadow: auto | on | off

  auto  csak akkor indul, ha a labor legutobbi walk-forward tesztje szerint a modell mintan kivul
        jobb a mostani szuroknel (lab_ml.verdict == "better") - igy "kesz all", es magatol bekapcsol
  on    mindig, ha van mentett modell (pl. kiprobalasra)
  off   soha

MI TORTENIK
-----------
  1. A monitor minden kiertekelt kitoreses belepesi jelzeshez (a szuron elbukottakhoz is) kiszamolja
     a modell becsleset (R) - PONTOSAN ugyanazokkal a jellemzokkel, mint a tanitasban
     (lab_ml.features). Az ertesites szovegeben megjelenik: "a modell valasztana / nem valasztana".
  2. Zaras utan a jelzeseket naplozza (data/lab/ml/shadow.jsonl, legfeljebb SHADOW_MAX sor).
  3. A labor minden koreben kiszamolja a mar lefutott jelzesek kimenetelet - ugyanazzal a
     szimulacioval (masnapi nyitas, gap-limit, csuszas, pesszimista kilepes), amivel a modell tanult.
  4. A Labor ful osszeveti: a modell altal valasztott vs. elvetett jelzesek eredmenye - ez a valodi,
     uj adaton mert teszt, mielott a modell barmirol dontene.
"""
from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import entry_research as er
from . import lab, lab_ml, store
from .config import Settings
from .locks import file_lock, write_atomic
from .trade_mgmt import max_hold

log = logging.getLogger(__name__)

SHADOW_MAX = 20_000            # naplosor (kb. 10 MB); felette a legregebbiek torlodnek
RESOLVE_AFTER_DAYS = 3         # ennyi naptari nap utan kezdjuk keresni a kimenetelt

_model_cache: dict = {"mtime": None, "payload": None}
_bench_cache: dict = {"id": None, "ratio": None}
_logged_ids: set[str] | None = None


def shadow_path():
    return lab._p("ml", "shadow.jsonl")


def load_model() -> dict | None:
    path = lab_ml.model_path()
    if not path.exists():
        _model_cache.update(mtime=None, payload=None)
        return None
    mtime = path.stat().st_mtime
    if _model_cache["mtime"] != mtime:
        try:
            _model_cache.update(mtime=mtime, payload=pickle.loads(path.read_bytes()))
        except Exception:
            log.exception("A mentett modell nem toltheto be")
            _model_cache.update(mtime=mtime, payload=None)
    return _model_cache["payload"]


def active_model(settings: Settings) -> dict | None:
    """A hasznalhato modell, ha az arnyek-uzem be van kapcsolva (auto: csak "better" szintu)."""
    mode = settings.lab.shadow
    if mode == "off":
        return None
    payload = load_model()
    if payload is None or (mode == "auto" and payload.get("level") != "better"):
        return None
    return payload


def _bench_ratio(bench: pd.DataFrame | None) -> pd.Series | None:
    if bench is None:
        return None
    if _bench_cache["id"] != id(bench):
        _bench_cache.update(id=id(bench), ratio=lab_ml.bench_ratio(bench))
    return _bench_cache["ratio"]


def score_signals(signals: list, df: pd.DataFrame, settings: Settings, bench: pd.DataFrame | None) -> None:
    """A kitoreses jelzesekhez (signal.ml) a modell becslese. Hiba eseten a jelzes valtozatlan."""
    payload = active_model(settings)
    if payload is None:
        return
    ratio = _bench_ratio(bench)
    for signal in signals:
        if signal.kind != "breakout":          # a modell kitoresekbol tanult
            continue
        try:
            trigger = signal.pattern.trigger_date
            i = int(df.index.get_loc(trigger)) if trigger is not None and trigger in df.index else len(df) - 1
            feats = lab_ml.features(signal.pattern, i, df, signal.plan, signal.plan.horizon or 40, bench, ratio)
            pred = lab_ml.predict(payload, feats, signal.pattern.name)
            signal.ml = {"pred_r": round(pred, 3), "threshold": round(payload["threshold"], 3),
                         "pick": bool(pred >= payload["threshold"]), "model": payload["trained"],
                         "level": payload["level"]}
        except Exception:
            log.exception("Arnyek-becsles hibara futott: %s", signal.symbol)


# ---------------------------------------------------------------------------
# Naplo
# ---------------------------------------------------------------------------
def _read() -> list[dict]:
    path = shadow_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write(records: list[dict]) -> None:
    write_atomic(shadow_path(), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records[-SHADOW_MAX:]))


def log_signals(signals: list, settings: Settings) -> int:
    """Zaras utan: a becslessel ellatott jelzesek naplozasa (jelazonositonkent egyszer)."""
    global _logged_ids
    fresh = [s for s in signals if getattr(s, "ml", None)]
    if not fresh:
        return 0
    cfg = settings.entry
    with file_lock(shadow_path()):
        if _logged_ids is None:
            _logged_ids = {r.get("signal_id") for r in _read()}
        new = []
        for s in fresh:
            if s.signal_id in _logged_ids:
                continue
            plan = s.plan
            new.append({
                "signal_id": s.signal_id, "symbol": s.symbol, "date": f"{s.as_of:%Y-%m-%d}",
                "pattern": s.pattern.name, "kind": s.kind, "score": s.score, "qualifies": s.qualifies(cfg),
                **s.ml, "close": round(s.price, 4),
                "plan": {"stop": plan.stop, "target": plan.target, "atr": plan.atr, "horizon": plan.horizon or 40},
                "exit": {"mode": cfg.exit_mode, "partial_r": cfg.partial_r, "trail_atr": cfg.trail_atr,
                         "gap_pct": cfg.entry_max_gap_pct, "slippage_bps": cfg.slippage_bps},
                "logged": datetime.now(timezone.utc).isoformat(timespec="seconds"), "outcome": None,
            })
            _logged_ids.add(s.signal_id)
        if new:
            path = shadow_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in new)
            if len(_logged_ids) > SHADOW_MAX + 1000:          # plafon: a legregebbiek torlodnek
                records = _read()
                _write(records)
                _logged_ids = {r.get("signal_id") for r in records[-SHADOW_MAX:]}
    return len(new)


def outcome(record: dict, frame: pd.DataFrame) -> dict | None:
    """A jelzes kimenetele a tanitassal azonos szimulacioval; None, ha a trade meg nem futott le."""
    day = pd.Timestamp(record["date"])
    if day not in frame.index:
        return None
    idx = int(frame.index.get_loc(day))
    entry_bar = idx + 1
    if entry_bar >= len(frame):
        return None
    plan, ex = record["plan"], record["exit"]
    close = float(frame["close"].iloc[idx])
    open_ = float(frame["open"].iloc[entry_bar])
    if open_ > close * (1 + ex["gap_pct"] / 100) or open_ <= plan["stop"]:
        return {"traded": False, "reason": "nem volt belepes (gap / stop alatti nyito)"}
    slip = ex["slippage_bps"] / 10_000
    price = open_ * (1 + slip)
    if price <= plan["stop"]:
        return {"traded": False, "reason": "nem volt belepes"}
    r, reason, held = er.simulate_exit(frame, entry_bar, price, plan["stop"], plan["target"], plan["horizon"],
                                       plan["atr"], ex["mode"], ex["partial_r"], ex["trail_atr"])
    finished = reason != "timeout" or entry_bar + max_hold(plan["horizon"], ex["mode"]) - 1 <= len(frame) - 1
    if not finished:
        return None
    r -= (price * slip) / (price - plan["stop"])
    return {"traded": True, "r": round(float(r), 4), "reason": reason, "held": int(held)}


def resolve(settings: Settings) -> int:
    """A labor minden koreben: a mar lefutott jelzesek kimenetele. A megoldottak szamat adja."""
    path = shadow_path()
    if not path.exists():
        return 0
    limit = pd.Timestamp.now().normalize() - pd.Timedelta(days=RESOLVE_AFTER_DAYS)
    with file_lock(path):
        records = _read()
        pending = [r for r in records if r.get("outcome") is None and pd.Timestamp(r["date"]) <= limit]
        frames: dict[str, pd.DataFrame] = {}
        done = 0
        for record in pending:
            symbol = record["symbol"]
            if symbol not in frames:
                df = store.load(symbol, settings, universe=True)
                frames[symbol] = df if not df.empty else store.load(symbol, settings)
            if frames[symbol].empty:
                continue
            result = outcome(record, frames[symbol])
            if result is not None:
                record["outcome"] = result
                done += 1
        if done:
            _write(records)
    return done


def report(settings: Settings) -> dict:
    """A Labor fulnek: allapot + a modell altal valasztott vs. elvetett jelzesek eredmenye."""
    payload = load_model()
    active = active_model(settings) is not None
    records = _read()
    traded = [r for r in records if (r.get("outcome") or {}).get("traded")]

    def group(rows: list[dict]) -> dict:
        return lab_ml._group(np.array([r["outcome"]["r"] for r in rows], dtype=float))

    return {
        "mode": settings.lab.shadow,
        "active": active,
        "model": None if payload is None else {
            k: payload.get(k) for k in ("trained", "rows", "level", "verdict", "threshold", "sklearn")},
        "predictions": len(records),
        "resolved": sum(1 for r in records if r.get("outcome") is not None),
        "pending": sum(1 for r in records if r.get("outcome") is None),
        "since": records[0]["date"] if records else None,
        "groups": {
            "all": group(traded),
            "pick": group([r for r in traded if r.get("pick")]),
            "skip": group([r for r in traded if not r.get("pick")]),
            "qualified_pick": group([r for r in traded if r.get("qualifies") and r.get("pick")]),
            "qualified_skip": group([r for r in traded if r.get("qualifies") and not r.get("pick")]),
        },
    }
