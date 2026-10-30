"""Admin felulet (#58): felhasznalok, tartalom, kozosseg, mutatok es rendszerallapot - bongeszobol, biztonsagosan.

BIZTONSAG
  * szerepkor a felhasznalon (users.role = 'admin'; kiosztas: python cli.py admin grant <e-mail>)
  * kotelezo ketlepcsos azonositas: TOTP (RFC 6238, 30 mp, 6 jegy) - a titok az elso belepeskor keszul, a beallitas
    az elso helyes koddal erosodik meg; egy kod csak egyszer hasznalhato (totp_last_step), kerelemkorlattal
  * az admin munkamenet a TOTP-ellenorzes idejetol ADMIN_TTL-ig el (minden admin keres meghosszabbitja)
  * nem admin felhasznalonak minden /admin es /api/admin utvonal 404 (a webapp gondoskodik rola)
  * minden admin muvelet az audit-naplóba kerul (admin_audit: csak hozzafuzheto - adatbazis-trigger)
  * jelszo-hash, munkamenet-token, Google-azonosito soha nem kerul a valaszba
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from psycopg.types.json import Jsonb

from . import accounts, analytics, db, webauth
from .config import DATA_DIR, PROJECT_ROOT, load_settings

ADMIN_TTL = timedelta(minutes=30)
TOTP_STEP = 30
TOTP_LIMIT = webauth.RateLimit("admin-totp", 5, 15 * 60)       # felhasznalonkent 5 kod / 15 perc
ISSUER = "StockWatch Practice"
_POOL_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# TOTP (RFC 6238)
# ---------------------------------------------------------------------------
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset: offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def otpauth_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/{quote(ISSUER)}:{quote(email)}?secret={secret}&issuer={quote(ISSUER)}&digits=6&period=30"


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "admin"


def verified(user: dict | None) -> bool:
    at = (user or {}).get("admin_at")
    return is_admin(user) and at is not None and datetime.now(timezone.utc) - at < ADMIN_TTL


def state(user: dict) -> dict:
    """A belepesi allapot az admin oldalnak. Be nem allitott TOTP-nal a titok (es az otpauth URI) is - csak
    addig, amig az elso helyes koddal meg nem erosodik."""
    with db.connection() as conn:
        secret, last = conn.execute("SELECT totp_secret, totp_last_step FROM users WHERE id = %s", (user["id"],)).fetchone()
        if secret is None:
            secret = new_secret()
            conn.execute("UPDATE users SET totp_secret = %s WHERE id = %s", (secret, user["id"]))
    out = {"verified": verified(user), "enrolled": last is not None, "email": user["email"]}
    if last is None:
        out |= {"secret": secret, "uri": otpauth_uri(secret, user["email"])}
    return out


def verify(user: dict, token: str, code: str, now: float | None = None) -> bool:
    """A TOTP-kod ellenorzese (±1 lepes eltolodas); siker: az admin munkamenet elindul."""
    if not TOTP_LIMIT.hit(str(user["id"])):
        raise accounts.RateLimited("Túl sok próbálkozás. Próbáld újra 15 perc múlva.")
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    now_step = int((now if now is not None else time.time()) // TOTP_STEP)
    with db.connection() as conn:
        secret, last = conn.execute("SELECT totp_secret, totp_last_step FROM users WHERE id = %s FOR UPDATE",
                                    (user["id"],)).fetchone()
        if not secret or len(code) != 6:
            return False
        step = next((s for s in (now_step - 1, now_step, now_step + 1)
                     if hmac.compare_digest(totp(secret, s), code) and (last is None or s > last)), None)
        if step is None:
            return False
        conn.execute("UPDATE users SET totp_last_step = %s WHERE id = %s", (step, user["id"]))
        conn.execute("UPDATE sessions SET admin_at = now() WHERE token_hash = %s", (accounts._h(token),))
        audit(conn, user["id"], "admin_login")
    TOTP_LIMIT.clear(str(user["id"]))
    return True


def touch(token: str) -> None:
    """Az admin munkamenet meghosszabbitasa (minden admin keres)."""
    with db.connection() as conn:
        conn.execute("UPDATE sessions SET admin_at = now() WHERE token_hash = %s AND admin_at IS NOT NULL",
                     (accounts._h(token),))


def audit(conn, admin_id: int, action: str, target: int | None = None, **details) -> None:
    conn.execute("INSERT INTO admin_audit (admin_id, action, target_user, details) VALUES (%s, %s, %s, %s)",
                 (admin_id, action, target, Jsonb(details)))


def audit_log(limit: int = 200) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT a.at, a.admin_id, u.email, a.action, a.target_user, a.details FROM admin_audit a "
                            "LEFT JOIN users u ON u.id = a.admin_id ORDER BY a.id DESC LIMIT %s", (limit,)).fetchall()
    return [dict(zip(("at", "admin_id", "admin", "action", "target_user", "details"), r)) for r in rows]


def grant(email: str, role: str | None = "admin") -> bool:
    """CLI: szerepkor kiosztasa / visszavonasa (visszavonaskor a TOTP is torlodik)."""
    reset = "" if role else ", totp_secret = NULL, totp_last_step = NULL"
    with db.connection() as conn:
        row = conn.execute(f"UPDATE users SET role = %s{reset} WHERE lower(email) = %s RETURNING id",
                           (role, email.strip().lower())).fetchone()
    return row is not None


def reset_totp(email: str) -> bool:
    """CLI: elveszett hitelesito alkalmazas - a kovetkezo admin belepeskor uj titok keszul."""
    with db.connection() as conn:
        return conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = NULL WHERE lower(email) = %s",
                            (email.strip().lower(),)).rowcount > 0


# ---------------------------------------------------------------------------
# Felhasznalok
# ---------------------------------------------------------------------------
USER_COLS = ("u.id, u.email, u.nickname, u.created_at, u.email_verified_at, u.suspended_at, u.role, "
             "u.password_hash IS NOT NULL, EXISTS (SELECT 1 FROM user_identities i WHERE i.user_id = u.id), "
             "(SELECT max(last_used_at) FROM sessions s WHERE s.user_id = u.id), "
             "coalesce((SELECT xp FROM progress p WHERE p.user_id = u.id), 0), u.stats_excluded")
USER_KEYS = ("id", "email", "nickname", "created_at", "verified_at", "suspended_at", "role", "password", "google",
             "last_active", "xp", "stats_excluded")


def users(query: str = "", limit: int = 50) -> list[dict]:
    q = f"%{query.strip().lower()}%"
    with db.connection() as conn:
        rows = conn.execute(f"SELECT {USER_COLS} FROM users u WHERE lower(u.email) LIKE %s OR lower(coalesce(u.nickname, '')) "
                            "LIKE %s ORDER BY u.created_at DESC LIMIT %s", (q, q, limit)).fetchall()
    return [dict(zip(USER_KEYS, r)) for r in rows]


def user_detail(user_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute(f"SELECT {USER_COLS} FROM users u WHERE u.id = %s", (user_id,)).fetchone()
        if row is None:
            raise LookupError("Nincs ilyen felhasználó.")
        lessons = conn.execute("SELECT level, best_share, completed_at FROM lesson_progress WHERE user_id = %s "
                               "ORDER BY level", (user_id,)).fetchall()
        groups = conn.execute("SELECT g.id, g.name, g.owner_id = %s FROM group_members m JOIN learning_groups g "
                              "ON g.id = m.group_id WHERE m.user_id = %s", (user_id, user_id)).fetchall()
        counts = conn.execute("SELECT count(*), count(*) FILTER (WHERE daily IS NOT NULL) FROM practice_sessions "
                              "WHERE user_id = %s", (user_id,)).fetchone()
        decisions = conn.execute("SELECT count(*) FROM practice_tasks t JOIN practice_sessions s ON s.id = t.session_id "
                                 "WHERE s.user_id = %s AND t.decision IS NOT NULL", (user_id,)).fetchone()[0]
        sessions = conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s AND expires_at > now()",
                                (user_id,)).fetchone()[0]
    return dict(zip(USER_KEYS, row)) | {
        "lessons": [{"level": lv, "best_share": share, "completed_at": done} for lv, share, done in lessons],
        "groups": [{"id": g, "name": n, "owner": o} for g, n, o in groups],
        "rounds": counts[0], "daily_rounds": counts[1], "decisions": decisions, "active_sessions": sessions}


def user_action(admin: dict, user_id: int, action: str, base_url: str) -> dict:
    """resend | suspend | unsuspend | reset_nickname | delete | export | exclude | include - mind naplozva."""
    if action in ("suspend", "delete") and user_id == admin["id"]:
        raise ValueError("Saját fiókodat innen nem függesztheted fel és nem törölheted.")
    detail = user_detail(user_id)
    out: dict = {"ok": True}
    with db.connection() as conn:
        if action == "resend":
            if detail["verified_at"]:
                raise ValueError("Ez a fiók már megerősített.")
            token = accounts._new_token(conn, user_id, "verify", accounts.VERIFY_TTL)
        elif action == "suspend":
            conn.execute("UPDATE users SET suspended_at = now() WHERE id = %s", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        elif action == "unsuspend":
            conn.execute("UPDATE users SET suspended_at = NULL WHERE id = %s", (user_id,))
        elif action == "reset_nickname":
            conn.execute("UPDATE users SET nickname = NULL WHERE id = %s", (user_id,))
        elif action in ("exclude", "include"):                  # az admin osszegzesbol (analytics.overview)
            conn.execute("UPDATE users SET stats_excluded = %s WHERE id = %s", (action == "exclude", user_id))
        elif action == "export":
            out = {"export": accounts.export(user_id)}
        elif action != "delete":
            raise ValueError("Ismeretlen művelet.")
        audit(conn, admin["id"], f"user_{action}", user_id)
    if action == "resend":
        accounts._verify_mail(detail["email"], token, base_url)
    if action == "delete":
        accounts.erase(user_id)
    return out


# ---------------------------------------------------------------------------
# Tartalom
# ---------------------------------------------------------------------------
def lessons_state() -> dict:
    from . import learning

    with db.connection() as conn:
        versions = conn.execute("SELECT v.id, v.created_at, v.note, u.email FROM lesson_versions v LEFT JOIN users u "
                                "ON u.id = v.admin_id WHERE v.lang = 'hu' ORDER BY v.id DESC LIMIT 50").fetchall()
    return {"data": list(learning.lessons()), "source": "db" if versions else "file",
            "versions": [dict(zip(("id", "created_at", "note", "admin"), v)) for v in versions]}


def save_lessons(admin: dict, data, note: str = "") -> dict:
    from . import learning

    learning.validate(data)                                         # ervenytelen leckét nem ment
    with db.connection() as conn:
        vid = conn.execute("INSERT INTO lesson_versions (lang, data, note, admin_id) VALUES ('hu', %s, %s, %s) RETURNING id",
                           (Jsonb(data), (note or "")[:200], admin["id"])).fetchone()[0]
        audit(conn, admin["id"], "lessons_save", version=vid, note=(note or "")[:200])
    learning.lessons.cache_clear()
    learning.figure.cache_clear()
    return {"version": vid}


def restore_lessons(admin: dict, version_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute("SELECT data FROM lesson_versions WHERE id = %s AND lang = 'hu'", (version_id,)).fetchone()
    if row is None:
        raise LookupError("Nincs ilyen verzió.")
    return save_lessons(admin, row[0], f"visszaállítás: #{version_id}")


def daily_preview(days: int = 7) -> list[dict]:
    """A kovetkezo napok helyzetsora (kategoria es forras), a csere jelolesevel."""
    from . import daily, progress
    from .practice import sampler, store

    pool = store.load_pool()
    if not pool:
        return []
    today = progress.today()
    with db.connection() as conn:
        overrides = {d for (d,) in conn.execute("SELECT day FROM daily_overrides WHERE day >= %s", (today,)).fetchall()}
    out = []
    for n in range(days):
        day = today + timedelta(days=n)
        seed = daily.day_seed(day, pool["fingerprint"])
        picks = sampler.sample(pool, seed, daily.practice.DAILY_ROUNDS)
        out.append({"day": f"{day:%Y-%m-%d}", "replaced": day in overrides,
                    "tasks": [{"category": p["category"], "source": p["source"], "symbol": p["symbol"]} for p in picks]})
    return out


def replace_daily(admin: dict, day: str) -> dict:
    """Egy nap helyzetsoranak csereje (pl. hibas feladat): uj veletlen seed. Aki mar elkezdte, a sajatjat jatssza."""
    from .practice import sampler

    when = date.fromisoformat(day)
    seed = secrets.randbelow(sampler.SEED_MAX)
    with db.connection() as conn:
        conn.execute("INSERT INTO daily_overrides (day, seed, admin_id) VALUES (%s, %s, %s) ON CONFLICT (day) DO UPDATE "
                     "SET seed = EXCLUDED.seed, admin_id = EXCLUDED.admin_id, created_at = now()", (when, seed, admin["id"]))
        audit(conn, admin["id"], "daily_replace", day=day)
    return {"ok": True}


def rebuild_pool(admin: dict) -> dict:
    """A feladatkeszlet ujraepitese hatterszalon (perceket vesz igenybe); egyszerre egy."""
    from .practice import sampler

    if not _POOL_LOCK.acquire(blocking=False):
        raise ValueError("Az újraépítés már fut.")
    with db.connection() as conn:
        audit(conn, admin["id"], "pool_rebuild")

    def run():
        try:
            sampler.build(load_settings(), force=True)
        finally:
            _POOL_LOCK.release()

    threading.Thread(target=run, daemon=True, name="pool-rebuild").start()
    return {"ok": True, "message": "Az újraépítés elindult (néhány perc)."}


# ---------------------------------------------------------------------------
# Kozosseg
# ---------------------------------------------------------------------------
def groups_list() -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT g.id, g.name, g.code, g.created_at, u.email, u.nickname, "
                            "(SELECT count(*) FROM group_members m WHERE m.group_id = g.id) FROM learning_groups g "
                            "LEFT JOIN users u ON u.id = g.owner_id ORDER BY g.created_at DESC LIMIT 200").fetchall()
    return [dict(zip(("id", "name", "code", "created_at", "owner_email", "owner_nickname", "members"), r)) for r in rows]


def group_action(admin: dict, group_id: int, action: str) -> dict:
    from . import groups

    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM learning_groups WHERE id = %s", (group_id,)).fetchone():
            raise LookupError("Nincs ilyen csoport.")
        if action == "revoke_code":
            conn.execute("UPDATE learning_groups SET code = %s WHERE id = %s", (groups._code(), group_id))
        elif action == "delete":
            conn.execute("DELETE FROM learning_groups WHERE id = %s", (group_id,))
        else:
            raise ValueError("Ismeretlen művelet.")
        audit(conn, admin["id"], f"group_{action}", group=group_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Rendszer
# ---------------------------------------------------------------------------
def system() -> dict:
    from . import backup, housekeeping, mailer
    from .practice import sampler, store

    start = time.perf_counter()
    with db.connection() as conn:
        conn.execute("SELECT 1")
        latency = round((time.perf_counter() - start) * 1000, 1)
        users_n, verified_n = conn.execute("SELECT count(*), count(email_verified_at) FROM users").fetchone()
        migration = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    backups = sorted(backup.BACKUP_DIR.glob("learn-*.zip")) if backup.BACKUP_DIR.exists() else []
    last = backups[-1] if backups else None
    deployed = datetime.fromtimestamp((PROJECT_ROOT / "cli.py").stat().st_mtime, timezone.utc)
    return {"db_ms": latency, "migration": migration, "users": users_n, "verified": verified_n,
            "mail": mailer.configured(), "backup": {"name": last.name, "bytes": last.stat().st_size,
                                                    "at": datetime.fromtimestamp(last.stat().st_mtime, timezone.utc)}
            if last else None, "backups": len(backups), "disk_mb": round(housekeeping.total_usage_mb(), 1),
            "data_dir": str(DATA_DIR), "deployed": deployed, "pool": sampler.pool_info(store.load_pool()),
            "pool_rebuilding": _POOL_LOCK.locked(), "errors": analytics.overview(7)["errors"]}
