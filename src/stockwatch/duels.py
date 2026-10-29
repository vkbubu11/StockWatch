"""Parbaj (#55): ket felhasznalo ugyanazon a helyzetsoron (ugyanaz a seed, a feladatkeszlet es a tipusok, tehat
ugyanazok a helyzetek ugyanabban a sorrendben).

  * a meghivo link az elso elfogadohoz kotodik; a kihivo az elfogadasig visszavonhatja; HOURS ora utan lejar
  * a masik fel eredmenye es dontesei csak a sajat kor befejezese (vagy a hatarido) utan kerhetok le
  * a hatarido utani dontes nem szamit: aki addig nem fejezte be, veszit (ha egyik sem: nincs gyoztes)
  * a parbaj korenek megosztasi kodja nem latszik (session.public), hogy a helyzetsor elore ne legyen kiprobalhato
  * csak becenev (vagy „Jatekos #...”) latszik, e-mail soha
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import analytics, db, progress
from .practice import sampler, store

HOURS = 72
ROUNDS = (5, 10)
WINS_BADGE = 5


def _player(conn, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = conn.execute("SELECT nickname, substr(md5(id::text), 1, 5) FROM users WHERE id = %s", (user_id,)).fetchone()
    return (row[0] or f"Játékos #{row[1]}") if row else None


def _row(conn, duel_id: str, lock: bool = False) -> dict:
    if not store.SESSION_ID_RE.match(duel_id or ""):
        raise LookupError("Nincs ilyen párbaj.")
    cols = ("id", "challenger_id", "opponent_id", "seed", "rounds", "kinds", "pool_fingerprint", "created_at",
            "expires_at", "revoked_at", "settled_at", "winner_id", "rematch_of")
    row = conn.execute(f"SELECT {', '.join(cols)} FROM duels WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                       (duel_id,)).fetchone()
    if row is None:
        raise LookupError("Nincs ilyen párbaj.")
    return dict(zip(cols, row))


def _state(conn, duel: dict, user_id: int | None) -> dict | None:
    """A felhasznalo parbaj-kore: dontesek, pont, es hogy a hataridoig befejezte-e."""
    if user_id is None:
        return None
    row = conn.execute(
        "SELECT s.id, count(t.k), count(t.decision), coalesce(sum(t.points) FILTER (WHERE t.decided_at <= %s), 0), "
        "max(t.decided_at) FROM practice_sessions s JOIN practice_tasks t ON t.session_id = s.id "
        "WHERE s.duel = %s AND s.user_id = %s GROUP BY s.id", (duel["expires_at"], duel["id"], user_id)).fetchone()
    if row is None:
        return None
    sid, total, decided, points, last = row
    return {"session": sid, "decided": decided, "total": total, "points": int(points),
            "finished": decided == total and last is not None and last <= duel["expires_at"]}


def create(user_id: int, settings=None, rounds: int = 5, kinds_mix: dict | None = None,
           opponent: int | None = None, rematch_of: str | None = None) -> dict:
    from .practice import kinds, session

    if rounds not in ROUNDS:
        raise ValueError("A párbaj 5 vagy 10 helyzetes.")
    kinds.normalize_kinds(kinds_mix)                                   # ervenytelen tipus: ValueError mar itt
    pool = store.load_pool()
    if not pool:
        raise ValueError("Még nincs feladatkészlet.")
    duel_id, seed = store.new_id(), secrets.randbelow(sampler.SEED_MAX)
    with db.connection() as conn:
        conn.execute("INSERT INTO duels (id, challenger_id, opponent_id, seed, rounds, kinds, pool_fingerprint, expires_at, "
                     "rematch_of) VALUES (%s, %s, %s, %s, %s, %s, %s, now() + %s, %s)",
                     (duel_id, user_id, opponent, seed, rounds, Jsonb(kinds_mix) if kinds_mix else None,
                      pool["fingerprint"], timedelta(hours=HOURS), rematch_of))
        analytics.track("duel_create", user_id, conn, rounds=rounds, rematch=bool(rematch_of))
    created = session.create(user_id, settings, seed=seed, rounds=rounds, kinds_mix=kinds_mix, duel=duel_id)
    return {"id": duel_id, "session": created}


def accept(user_id: int, duel_id: str, settings=None) -> dict:
    """A meghivo elfogadasa: az elso elfogado a parbaj masik fele (visszavagonal a kijelolt ellenfel)."""
    from .practice import session

    with db.connection() as conn:
        duel = _row(conn, duel_id, lock=True)
        mine = _state(conn, duel, user_id)
        if mine:                                                        # mar elfogadta: ugyanaz a kor
            return {"id": duel_id, "session": {"id": mine["session"]}}
        if user_id == duel["challenger_id"]:
            raise ValueError("A saját párbajodat nem fogadhatod el.")
        if duel["revoked_at"] or (duel["opponent_id"] not in (None, user_id)):
            raise LookupError("Ez a meghívó már nem érvényes.")
        if datetime.now(timezone.utc) >= duel["expires_at"]:
            raise ValueError("A meghívó lejárt.")
        pool = store.load_pool()
        if not pool or pool["fingerprint"] != duel["pool_fingerprint"]:
            raise ValueError("A feladatkészlet közben megváltozott – kérj új meghívót.")
        conn.execute("UPDATE duels SET opponent_id = %s WHERE id = %s", (user_id, duel_id))
        analytics.track("duel_accept", user_id, conn)
    # az UPDATE mar rogzult: egy masodik elfogado az opponent_id miatt nem fer be
    created = session.create(user_id, settings, seed=duel["seed"], rounds=duel["rounds"], kinds_mix=duel["kinds"],
                             duel=duel_id)
    return {"id": duel_id, "session": created}


def revoke(user_id: int, duel_id: str) -> None:
    with db.connection() as conn:
        duel = _row(conn, duel_id, lock=True)
        if duel["challenger_id"] != user_id:
            raise LookupError("Nincs ilyen párbaj.")
        if _state(conn, duel, duel["opponent_id"]):
            raise ValueError("Az ellenfél már elfogadta – a párbaj nem vonható vissza.")
        conn.execute("UPDATE duels SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (duel_id,))


def _settle(conn, duel: dict, user_id: int, me: dict | None, other: dict | None, now: datetime) -> str | None:
    """Az eredmeny, ha mar eldolt (mindketten befejeztek, vagy lejart); az elso alkalommal rogzitve, jelvenyekkel."""
    both = bool(me and other and me["finished"] and other["finished"])
    if duel["opponent_id"] is None or not (both or now >= duel["expires_at"]):
        return None
    a, b = (_state(conn, duel, duel["challenger_id"]), _state(conn, duel, duel["opponent_id"]))
    score = lambda s: (bool(s and s["finished"]), s["points"] if s and s["finished"] else -1)   # noqa: E731
    winner = (duel["challenger_id"] if score(a) > score(b) else duel["opponent_id"] if score(b) > score(a) else None)
    if duel["settled_at"] is None and conn.execute(
            "UPDATE duels SET settled_at = now(), winner_id = %s WHERE id = %s AND settled_at IS NULL",
            (winner, duel["id"])).rowcount:
        duel["winner_id"] = winner
        for uid in (duel["challenger_id"], duel["opponent_id"]):
            progress.award(conn, uid, "first_duel")
        if winner and conn.execute("SELECT count(*) FROM duels WHERE winner_id = %s", (winner,)).fetchone()[0] >= WINS_BADGE:
            progress.award(conn, winner, "duel_wins_5")
    return "draw" if duel["winner_id"] is None else "win" if duel["winner_id"] == user_id else "loss"


def view(user_id: int, duel_id: str) -> dict:
    """A parbaj a felhasznalo szemszogebol. Nem resztvevonek csak az ervenyes meghivo (ki hivta, hany helyzet)."""
    from .practice import session

    now = datetime.now(timezone.utc)
    with db.connection() as conn:
        duel = _row(conn, duel_id)
        side = "challenger" if user_id == duel["challenger_id"] else "opponent" if user_id == duel["opponent_id"] else None
        base = {"id": duel_id, "rounds": duel["rounds"], "expires": duel["expires_at"].isoformat(timespec="seconds"),
                "challenger": _player(conn, duel["challenger_id"])}
        if side is None:
            if duel["revoked_at"] or duel["opponent_id"] is not None or now >= duel["expires_at"]:
                raise LookupError("Ez a meghívó már nem érvényes.")
            return base | {"invite": True}
        other_id = duel["opponent_id"] if side == "challenger" else duel["challenger_id"]
        me, other = _state(conn, duel, user_id), _state(conn, duel, other_id)
        result = _settle(conn, duel, user_id, me, other, now)
        out = base | {"side": side, "opponent": _player(conn, other_id), "revoked": bool(duel["revoked_at"]),
                      "me": me, "other": {"accepted": other is not None, "finished": bool(other and other["finished"])},
                      "result": result, "over": result is not None}
    # a masik fel pontja es dontesei csak a sajat kor befejezese utan (vagy ha lejart)
    if (me and me["finished"]) or result is not None:
        out["other"] |= {"points": other["points"] if other else 0}
        mine = session.summary_payload(user_id, me["session"])["tasks"] if me else []
        theirs = session.summary_payload(other_id, other["session"])["tasks"] if other else []
        out["compare"] = [{"k": k, "me": _brief(mine[k] if k < len(mine) else None),
                           "other": _brief(theirs[k] if k < len(theirs) else None)} for k in range(duel["rounds"])]
    return out


def _brief(task: dict | None) -> dict | None:
    if not task or not task.get("decided"):
        return None
    return {"kind": task.get("kind"), "action": task.get("action"), "verdict": task.get("verdict"),
            "quality": task.get("quality"), "points": task.get("points"), "symbol": task.get("symbol"),
            "date": task.get("date")}


def mine(user_id: int) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, challenger_id, opponent_id, rounds, created_at, expires_at, revoked_at, winner_id, settled_at "
            "FROM duels WHERE challenger_id = %s OR opponent_id = %s ORDER BY created_at DESC LIMIT 30",
            (user_id, user_id)).fetchall()
        out = []
        for did, ch, op, rounds, created, expires, revoked, winner, settled in rows:
            other = op if ch == user_id else ch
            out.append({"id": did, "rounds": rounds, "created": created.isoformat(timespec="seconds"),
                        "expires": expires.isoformat(timespec="seconds"), "revoked": bool(revoked),
                        "opponent": _player(conn, other), "mine": ch == user_id,
                        "result": None if not settled else "draw" if winner is None else "win" if winner == user_id else "loss"})
    return out


def rematch(user_id: int, duel_id: str, settings=None) -> dict:
    """Visszavago egy kattintassal: uj parbaj ugyanannyi helyzettel es tipussal, az ellenfel kijelolve."""
    with db.connection() as conn:
        duel = _row(conn, duel_id)
    if user_id not in (duel["challenger_id"], duel["opponent_id"]) or duel["opponent_id"] is None:
        raise LookupError("Nincs ilyen párbaj.")
    if duel["settled_at"] is None:
        raise ValueError("A visszavágó a párbaj eredménye után indítható.")
    other = duel["opponent_id"] if user_id == duel["challenger_id"] else duel["challenger_id"]
    return create(user_id, settings, duel["rounds"], duel["kinds"], opponent=other, rematch_of=duel_id)
