"""Egy naplozott belepesi dontes visszajatszasa es osszevetese az eredetivel.

A naplo (ledger.py) minden jelzesnel eltarolja a beallitas- es kalibracio-verziot,
a kikuldott jelzeseknel a teljes gyertyasort is. Ebbol:

  * PILLANATKEPBOL: bitre ugyanazt az adatot, beallitast es kalibraciot kapja a
    motor - az eredmenynek pontosan egyeznie kell. Ha nem egyezik, a kod
    viselkedese valtozott (a naplo a kodverziot is mutatja).
  * MOSTANI ADATBOL (nincs pillanatkep): a cache-elt gyertyakbol a jelzes napjaig.
    A yfinance osztalek / split utan visszamenoleg korrigal - ezt felismerjuk
    (az arak egyenletes skalazodasa), es kulon jelezzuk.
"""
from __future__ import annotations

import math

import pandas as pd

from . import entry, indicators, ledger, market, store
from .config import Settings, load_settings
from .forecast import Calibration, analyse

PRICE_FIELDS = ("entry_limit", "stop", "target")


def trace(signal_id: str, settings: Settings | None = None) -> dict:
    """Egy jelzes teljes eletutja a kozos azonosito alapjan:
    naplo-dontes(ek) -> kikuldott ertesitesek -> virtualis pozicio -> paper trade -> utolagos ertekeles."""
    import json

    from . import broker, housekeeping, positions, signal_review

    settings = settings or load_settings()
    records = ledger.find(signal_id)
    # a regi, azonosito nelkul irt ertesitesek / trade-ek a (papir, jelzesnap, alakzat) harmas alapjan
    first = records[0] if records else None
    key = ((first["symbol"], first["as_of"], (first.get("pattern") or {}).get("name"))
           if first and first.get("kind") != "setup" else None)
    alerts = []
    for path in [settings.signals_path] + [housekeeping._archive_path(settings.signals_path, i)
                                           for i in range(1, settings.storage.signals_backups + 1)]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if signal_id not in line and not (key and key[0] in line and key[1] in line):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = (raw.get("entry") or {}).get("signal_id")
            if sid == signal_id or (not sid and key and ledger.alert_key(raw) == key):
                alerts.append(raw)
    alerts.sort(key=lambda a: a.get("timestamp") or "")

    legacy_id = "|".join(key) if key else None
    tracker = positions.PositionTracker(settings, read_only=True)
    position = next((p for p in tracker.positions.values() if p.signal_id == signal_id), None)
    if position is None and legacy_id in tracker.positions and not tracker.positions[legacy_id].signal_id:
        position = tracker.positions[legacy_id]
    book = broker.BrokerBook(settings, read_only=True)
    trade = next((t for t in book.trades.values() if t.signal_id == signal_id), None)
    if trade is None and legacy_id in book.trades and not book.trades[legacy_id].signal_id:
        trade = book.trades[legacy_id]

    rec = next((r for r in records if r.get("decision") in ("alerted", "manual")),
               records[0] if records else None)
    review = None
    entry_alert = next((a for a in alerts if str(a.get("event", "")).startswith("entry_")), None)
    if entry_alert is None and rec is not None:
        # a naplobejegyzesbol is ertekelheto (pl. kihagyott vagy elutasitott jelolt: "mi lett volna?")
        entry_alert = {"event": f"entry_{rec['kind']}", "symbol": rec["symbol"], "price": rec["price"],
                       "as_of": rec["as_of"], "entry": {"as_of": rec["as_of"], "price": rec["price"],
                                                        "plan": rec.get("plan"), "score": rec.get("score")},
                       "pattern": rec.get("pattern")}
    if entry_alert is not None:
        df = store.load(entry_alert["symbol"], settings)
        if not df.empty:
            df = indicators.enrich(df, settings.structure.atr_period)
        review = signal_review.review(entry_alert, df, settings)

    from dataclasses import asdict
    return {
        "signal_id": signal_id,
        "records": records,
        "alerts": [{k: a.get(k) for k in ("timestamp", "event", "title", "price")} for a in alerts],
        "position": asdict(position) if position else None,
        "trade": asdict(trade) if trade else None,
        "review": review,
    }


def _replay_context(symbol: str, source: str | None, bench) -> "market.MarketContext | None":
    """A naplozott dontes rezsim-forrasa (QQQ / ^GSPC / sajat trend). A regi bejegyzeseknel (forras
    nelkul) None: azok egyetlen benchmarkot neztek, a pillanatkep azt tartalmazza."""
    if not source:
        return None
    key = market.SP500_INDEX if source == market.SELF else source
    return market.MarketContext({key: bench}, frozenset({symbol}) if source == market.NASDAQ_INDEX else frozenset(),
                                frozenset({symbol}) if source == market.SP500_INDEX else frozenset())


def replay(signal_id: str, settings: Settings | None = None, use_snapshot: bool = True) -> dict:
    """`use_snapshot=False`: a mostani (pl. frissen veglegesitett) adatbol szamol akkor is,
    ha van pillanatkep - a nyitas elotti ujraellenorzes igy nezi meg, megall-e a jelzes."""
    records = ledger.find(signal_id)
    if not records:
        raise ValueError(f"Nincs ilyen jelzes a naploban: {signal_id}")
    rec = next((r for r in records if r.get("decision") in ("alerted", "manual")), records[0])
    current = settings or load_settings()
    notes: list[str] = []

    versions = rec.get("versions") or {}
    cfg = ledger.load_config(versions.get("config", "")) if versions.get("config") else None
    if cfg is None:
        cfg = current
        notes.append("a naplozott beallitas-verzio nem talalhato - a mostani beallitassal")
    if rec.get("strategy") == "sandbox" and rec.get("kind") != "pullback":
        cfg = cfg.sandbox_breakout() or cfg          # a homokozo kitores-motorja a sajat szuroivel dontott
    cal_key = versions.get("calibration")
    if cal_key == "nincs":
        calibration = Calibration()          # a dontesnel sem volt kalibracio
    else:
        calibration = ledger.load_calibration(cal_key) if cal_key else None
        if calibration is None:
            calibration = Calibration.load(cfg.calibration_path)
            notes.append("a naplozott kalibracio nem talalhato - a mostanival")

    raw, bench = ledger.load_snapshot(signal_id) if (rec.get("snapshot") and use_snapshot) else (None, None)
    source = "pillanatkep"
    if raw is None:
        source = "mostani adat"
        as_of = pd.Timestamp(rec["as_of"])
        raw = store.load(rec["symbol"], cfg)
        raw = raw[raw.index <= as_of]
        src = rec.get("regime")
        bench = (market.load_index(market.SP500_INDEX if src == market.SELF else src, cfg, refresh=False)
                 if src else market.load_benchmark(cfg, refresh=False))
        bench = bench[bench.index <= as_of] if bench is not None and not bench.empty else None
    if raw is None or raw.empty:
        raise ValueError(f"Nincs adat a visszajatszashoz: {rec['symbol']}")

    data = rec.get("data") or {}
    same_data = ledger.window_hash(raw) == data.get("window_hash")
    enriched = indicators.enrich(raw, cfg.structure.atr_period)
    earnings = pd.Timestamp(rec["earnings_date"]).date() if rec.get("earnings_date") else None
    ctx = _replay_context(rec["symbol"], rec.get("regime"), bench)
    regime = ctx.regime(rec["symbol"], enriched, enriched.index[-1]) if ctx else None
    if rec.get("kind") == "momentum":
        raise ValueError("A momentum-jelzes keresztmetszeti rangsorbol szuletik (az osszes indextag) - "
                         "egy papirbol nem jatszhato vissza. A dontes a jelzesnaploban es a momentum_state-ben latszik.")
    pullback = rec.get("kind") == "pullback"
    if pullback:
        # Visszahuzas: a naplozott beallitas-verzio strategia-parameterevel (meanrev.py)
        from . import meanrev

        strategy = rec.get("strategy") or "pullback"
        sig = meanrev.evaluate(rec["symbol"], raw, cfg, cfg.strategies.get(strategy).params, regime,
                               earnings, strategy)
        signals = [sig] if sig is not None else []
    else:
        forecast = analyse(rec["symbol"], enriched, cfg, calibration)
        candidates = [m for m in forecast.matches if entry.classify(m, enriched, cfg.entry)]
        signals = entry.evaluate(rec["symbol"], enriched, candidates, cfg, calibration=calibration,
                                 benchmark=bench, earnings_date=earnings,
                                 support=forecast.support, resistance=forecast.resistance,
                                 regime=regime) if candidates else []

    def ok(s) -> bool:
        return s.passed if pullback else s.qualifies(cfg.entry)

    pattern = rec.get("pattern") or {}
    match = next((s for s in signals if s.signal_id == signal_id), None)
    if match is None:   # arkorrekcio utan az ujjlenyomat (trigger-szint) valtozhat
        match = next((s for s in signals if s.pattern.name == pattern.get("name") and s.kind == rec.get("kind")), None)

    out = {
        "signal_id": signal_id, "symbol": rec["symbol"], "as_of": rec["as_of"], "source": source,
        "same_data": same_data, "code_then": versions.get("code"), "code_now": ledger.code_version(),
        "notes": notes, "found": match is not None, "diffs": [], "scale": None, "verdict": "",
    }
    if match is None:
        out["verdict"] = "NEM REPRODUKALHATO - a motor ma nem talalja ezt a jelzest"
        return out

    # arskala: osztalek / split utani visszamenoleges korrekcio felismerese
    recorded_close = (data.get("last_bar") or {}).get("close") or rec.get("price")
    scale = float(raw["close"].iloc[-1]) / recorded_close if recorded_close else 1.0
    scaled = abs(scale - 1) > 1e-6
    out["scale"] = scale

    diffs = []

    def compare(name, then, now, tol=1e-6, price=False):
        if then is None or now is None:
            return
        expected = then * scale if price else then
        if isinstance(then, (int, float)) and isinstance(now, (int, float)):
            if math.isclose(expected, now, rel_tol=max(tol, 1e-6), abs_tol=0.011 if price else 0.051):
                return
        elif then == now:
            return
        diffs.append({"field": name, "then": then, "now": now})

    plan = rec.get("plan") or {}
    now_plan = match.plan.to_dict()
    compare("helyzet", rec.get("kind"), match.kind)
    compare("megfelel", rec.get("qualifies") if not pullback else not any(
        c.get("passed") is False for c in rec.get("checks") or []), ok(match))
    compare("pontszam", rec.get("score"), match.score, tol=0.02 if scaled else 1e-6)
    for field in PRICE_FIELDS:
        compare(f"terv.{field}", plan.get(field), now_plan.get(field), tol=0.002, price=True)
    compare("terv.darab", plan.get("shares"), now_plan.get("shares"), tol=0.05 if scaled else 1e-6)
    then_checks = {c["name"]: c["passed"] for c in rec.get("checks") or []}
    for c in match.checks:
        if c.name in then_checks and then_checks[c.name] != c.passed:
            diffs.append({"field": f"szuro.{c.name}", "then": then_checks[c.name], "now": c.passed})
    out["diffs"] = diffs

    if not diffs and same_data:
        out["verdict"] = "PONTOSAN EGYEZIK - ugyanabbol az adatbol ugyanaz a dontes"
    elif not diffs:
        out["verdict"] = ("EGYEZIK" + (f" - az arak {scale:.4f}-szeresre korrigalva (osztalek / split),"
                                       " a dontes valtozatlan" if scaled else " (eltero adatablak, azonos dontes)"))
    else:
        reason = ("a kod megvaltozott" if out["code_then"] != out["code_now"]
                  else "arkorrekcio" if scaled else "eltero adat")
        out["verdict"] = f"ELTER ({len(diffs)} mezo) - valoszinu ok: {reason}"
    out["replayed"] = {"kind": match.kind, "score": match.score, "qualifies": ok(match), "plan": now_plan}
    return out
