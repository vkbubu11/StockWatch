"""Nyitas elotti ujraellenorzes: megall-e a tegnap esti jelzes a VEGLEGESITETT adaton?

A zaras utani elemzes (16:15 NY) a napi gyertyat meg frissen kapja; a yfinance
kesobb meg korrigalhatja a zaroarat es a volument. Nyitas elott (09:05 NY) a monitor
ujratolti az adatot, es minden elozo esti KIKULDOTT jelzest ujraszamol ugyanazzal a
beallitassal es kalibracioval (replay.py, a mostani adatbol):

  megerositve  - ugyanaz a jelzes, minden szuro megall  -> csak a naplo rogziti
  visszavonva  - a jelzes mar nem all meg (vagy nem is talalhato)
                 -> "VISSZAVONVA" ertesites, a fuggo paper megbizas torlese,
                    a virtualis pozicio kimarad

Igy nem marad a levegoben egy olyan belepo, amelyik csak egy meg nem vegleges
gyertya miatt szuletett.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import broker, feed, ledger, positions, replay, sessions
from .config import Settings

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 7


def pending(today: date | None = None) -> list[dict]:
    """Az elmult napok kikuldott jelzesei, amiket meg nem ellenoriztunk ujra."""
    today = today or sessions.now_ny().date()
    oldest = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    alerted: dict[str, dict] = {}
    done: set[str] = set()
    for rec in ledger.iter_records():
        if (rec.get("as_of") or "") < oldest:
            continue
        sid = rec.get("signal_id")
        if rec.get("decision") in ("confirmed", "revoked"):
            done.add(sid)
        elif rec.get("decision") == "alerted" and sid not in alerted:
            alerted[sid] = rec
    return [r for sid, r in alerted.items() if sid not in done and r["as_of"] < today.isoformat()]


def revalidate(settings: Settings, dispatcher=None, refresh: bool = True,
               today: date | None = None, client=None) -> list[dict]:
    """Minden fuggo jelzes ujraellenorzese. Visszaadja az eredmenyeket."""
    results = []
    for rec in pending(today):
        symbol, sid = rec["symbol"], rec["signal_id"]
        if refresh:
            try:
                feed.update([symbol], settings, universe=rec.get("mode") == "index")
            except Exception:
                log.exception("Az adat frissitese nem sikerult (%s) - a cache-sel ellenorzunk", symbol)
        try:
            res = replay.replay(sid, settings, use_snapshot=False)
        except ValueError as exc:
            res = {"found": False, "diffs": [], "verdict": str(exc), "scale": None}

        still = res.get("found") and res.get("replayed", {}).get("qualifies", False) \
            and res.get("replayed", {}).get("kind") == rec.get("kind")
        if still:
            decision = "confirmed"
            reason = "a veglegesitett adaton is megall" + (
                f" (elteres: {', '.join(d['field'] for d in res['diffs'])})" if res.get("diffs") else "")
        else:
            decision = "revoked"
            reason = ("a veglegesitett adaton mar nem all meg: "
                      + (", ".join(f"{d['field']} {d['then']} -> {d['now']}" for d in res.get("diffs", []))
                         or res.get("verdict", "nem talalhato")))
        ledger.append_followup(rec, decision, reason, {"verdict": res.get("verdict"), "diffs": res.get("diffs")})
        actions = _revoke(settings, rec, reason, dispatcher, client) if decision == "revoked" else []
        log.info("Ujraellenorzes %s %s: %s - %s", symbol, sid, decision, reason)
        results.append({"signal_id": sid, "symbol": symbol, "decision": decision, "reason": reason,
                        "actions": actions})
    return results


def _revoke(settings: Settings, rec: dict, reason: str, dispatcher, client) -> list[str]:
    sid, actions = rec["signal_id"], []
    # virtualis pozicio: ha meg belepesre var, kimarad
    tracker = positions.PositionTracker(settings)
    for pos in tracker.positions.values():
        if pos.signal_id == sid and pos.status == "pending":
            tracker._close_skipped(pos, pd.Timestamp(sessions.now_ny().date()), "a jelzes visszavonva (nyitas elotti ellenorzes)")
            actions.append("virtualis pozicio kimarad")
    tracker.save()
    # paper megbizas: ha meg nem teljesult, visszavonjuk
    book = broker.BrokerBook(settings)
    trade = next((t for t in book.trades.values() if t.signal_id == sid and t.status == "submitted"), None)
    if trade is not None:
        client = client or broker.AlpacaClient.from_env()
        if client is not None:
            try:
                broker.close_trade(trade, client)
                trade.note("visszavonva: a nyitas elotti ellenorzesen a jelzes nem allt meg")
                actions.append("paper megbizas visszavonva")
            except broker.BrokerError as exc:
                actions.append(f"paper megbizas visszavonasa sikertelen: {exc}")
        book.save()
    if dispatcher is not None:
        from .alerts import Alert

        pattern = (rec.get("pattern") or {}).get("name", "")
        body = "\n".join([
            f"Jelzes napja: {rec['as_of']}  |  azonosito: {sid}",
            f"Ok: {reason}",
            "",
            "NE vegyel ra: a jelzes a veglegesitett napi adaton mar nem all meg.",
            *([f"Teendo elvegezve: {', '.join(actions)}"] if actions else []),
            "",
            "Nem befektetesi tanacs.",
        ])
        dispatcher.dispatch(Alert(symbol=rec["symbol"], title=f"{rec['symbol']}: {pattern} - JELZES VISSZAVONVA",
                                  body=body, forecast=None, event="entry_revoked",
                                  extra={"signal_id": sid, "reason": reason},
                                  strategy=rec.get("strategy") or "breakout"))
    return actions
