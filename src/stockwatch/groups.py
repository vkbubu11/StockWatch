"""Privat csoportok a ranglistahoz (#47) - pl. egy egyetemi kurzus vagy egy barati kor.

  * letrehozas nevvel -> meghivokod (CODE_LEN betu); csatlakozas koddal; kilepes; a letrehozo torolheti a csoportot
  * a csoport-ranglistat (daily.leaderboard group=...) csak tag lathatja; mas felhasznalonak a csoport "nincs ilyen"
  * korlatok: MAX_OWNED sajat csoport, MAX_MEMBERSHIPS tagsag, MAX_MEMBERS tag csoportonkent; a kodtalalgatas ellen
    JOIN_LIMIT (felhasznalonkent, adatbazisban)
"""
from __future__ import annotations

import re
import secrets

import psycopg

from . import db, webauth

CODE_LEN = 8
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz"       # szam es konnyen osszetevesztheto betu (i, l, o) nelkul
MAX_OWNED = 5
MAX_MEMBERSHIPS = 20
MAX_MEMBERS = 500
NAME_RE = re.compile(r"^[\w .,()\-]{3,40}$")
JOIN_LIMIT = webauth.RateLimit("group-join", 10, 60 * 60)   # felhasznalonkent 10 csatlakozasi kiserlet / ora


def _code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def mine(user_id: int) -> list[dict]:
    """A felhasznalo csoportjai (a koddal - a tagok tovabbadhatjak), a legujabb tagsag elol."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.code, g.owner_id = %s, (SELECT count(*) FROM group_members x WHERE x.group_id = g.id) "
            "FROM group_members m JOIN learning_groups g ON g.id = m.group_id WHERE m.user_id = %s "
            "ORDER BY m.joined_at DESC", (user_id, user_id)).fetchall()
    return [{"id": i, "name": n, "code": c, "owner": o, "members": k} for i, n, c, o, k in rows]


def create(user_id: int, name: str) -> dict:
    name = " ".join((name or "").split())
    if not NAME_RE.match(name):
        raise ValueError("A csoport neve 3–40 karakter: betű, szám, szóköz, pont, vessző, kötőjel, zárójel.")
    with db.connection() as conn:
        if conn.execute("SELECT count(*) FROM learning_groups WHERE owner_id = %s", (user_id,)).fetchone()[0] >= MAX_OWNED:
            raise ValueError(f"Legfeljebb {MAX_OWNED} saját csoportod lehet.")
        if conn.execute("SELECT count(*) FROM group_members WHERE user_id = %s", (user_id,)).fetchone()[0] >= MAX_MEMBERSHIPS:
            raise ValueError(f"Legfeljebb {MAX_MEMBERSHIPS} csoportnak lehetsz tagja.")
        for _ in range(5):                             # utkozo kod (gyakorlatilag soha): uj kod
            try:
                with conn.transaction():
                    gid = conn.execute("INSERT INTO learning_groups (name, code, owner_id) VALUES (%s, %s, %s) RETURNING id",
                                       (name, _code(), user_id)).fetchone()[0]
                break
            except psycopg.errors.UniqueViolation:
                continue
        conn.execute("INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (gid, user_id))
    return next(g for g in mine(user_id) if g["id"] == gid)


def join(user_id: int, code: str) -> dict:
    if not JOIN_LIMIT.hit(str(user_id)):
        raise ValueError("Túl sok csatlakozási kísérlet. Próbáld újra később.")
    code = (code or "").strip().lower()
    with db.connection() as conn:
        row = conn.execute("SELECT id FROM learning_groups WHERE code = %s FOR UPDATE", (code,)).fetchone()
        if row is None:
            raise LookupError("Nincs ilyen meghívókód.")
        gid = row[0]
        if conn.execute("SELECT 1 FROM group_members WHERE group_id = %s AND user_id = %s", (gid, user_id)).fetchone():
            return next(g for g in mine(user_id) if g["id"] == gid)
        if conn.execute("SELECT count(*) FROM group_members WHERE user_id = %s", (user_id,)).fetchone()[0] >= MAX_MEMBERSHIPS:
            raise ValueError(f"Legfeljebb {MAX_MEMBERSHIPS} csoportnak lehetsz tagja.")
        if conn.execute("SELECT count(*) FROM group_members WHERE group_id = %s", (gid,)).fetchone()[0] >= MAX_MEMBERS:
            raise ValueError("Ez a csoport megtelt.")
        conn.execute("INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (gid, user_id))
    return next(g for g in mine(user_id) if g["id"] == gid)


def leave(user_id: int, group_id: int) -> None:
    with db.connection() as conn:
        row = conn.execute("SELECT owner_id FROM learning_groups WHERE id = %s", (group_id,)).fetchone()
        if row and row[0] == user_id:
            raise ValueError("A létrehozó nem léphet ki – törölheti a csoportot.")
        if not conn.execute("DELETE FROM group_members WHERE group_id = %s AND user_id = %s", (group_id, user_id)).rowcount:
            raise LookupError("Nincs ilyen csoport.")


def delete(user_id: int, group_id: int) -> None:
    with db.connection() as conn:
        if not conn.execute("DELETE FROM learning_groups WHERE id = %s AND owner_id = %s", (group_id, user_id)).rowcount:
            raise LookupError("Nincs ilyen csoport.")


def is_member(user_id: int, group_id: int) -> bool:
    with db.connection() as conn:
        return conn.execute("SELECT 1 FROM group_members WHERE group_id = %s AND user_id = %s",
                            (group_id, user_id)).fetchone() is not None
