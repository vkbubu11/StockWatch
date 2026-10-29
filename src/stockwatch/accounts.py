"""Felhasznaloi fiokok (#31): regisztracio e-maillel es jelszoval, kotelezo e-mail-ellenorzes, belepes,
munkamenet, jelszo-visszaallitas; a fiok torlese es az adatexport (#40).

  * az adatbazisban a jelszonak csak a scrypt hash-e, a munkamenet- es e-mail-tokennek csak a sha256 hash-e van
  * a regisztracio, a belepes es a visszaallitas valasza nem arulja el, letezik-e a fiok (nincs fiok-felsorolas)
  * ellenorizetlen e-maillel nincs belepes (es igy gyakorlas sem); a visszaallito link az e-mailt is igazolja
  * jelszocserenel minden munkamenet zarul
  * a lejart munkamenetek es tokenek a kovetkezo belepeskor / token-kereskor torlodnek (nincs korlatlan noves)
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta

from . import analytics, db, i18n, mailer, webauth

TERMS_VERSION = "2026-10-23"                    # az ASZF es az adatkezelesi tajekoztato verzioja (#40)
SESSION_TTL = timedelta(days=30)
VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)
UNVERIFIED_TTL = timedelta(days=30)             # ennyi ido utan a meg nem erositett regisztracio torlodik
MAX_SESSIONS_PER_USER = 20
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,}$")
# gyakori, kiszivargott jelszavak (a 10 karakteres minimumon felul) - rovid tiltolista, kisbetuvel
LEAKED = frozenset({
    "1234567890", "0123456789", "12345678910", "123456789a", "1234567890a", "qwertyuiop", "qwertyuiop1",
    "1q2w3e4r5t", "1q2w3e4r5t6y", "password12", "password123", "password1234", "passw0rd123", "qwerty1234",
    "qwerty123456", "iloveyou123", "abcdefghij", "1111111111", "0000000000", "asdfghjkl1", "zxcvbnm123",
    "jelszo1234", "jelszo12345", "jelszavam123", "aaaaaaaaaa", "123123123123", "abc1234567", "admin12345",
})

IP_LOGIN = webauth.RateLimit("ip-login", 20, 15 * 60)       # IP-nkent 20 belepesi kiserlet / 15 perc
ACCOUNT_LOGIN = webauth.RateLimit("account-login", 5, 15 * 60)   # fiokonkent 5 kiserlet / 15 perc (sikeres belepes torli)
IP_REGISTER = webauth.RateLimit("ip-register", 5, 60 * 60)   # IP-nkent 5 regisztracio / ora
IP_RESET = webauth.RateLimit("ip-reset", 5, 60 * 60)         # IP-nkent 5 visszaallitasi keres / ora
ACCOUNT_RESET = webauth.RateLimit("account-reset", 3, 60 * 60)   # cimenkent 3 visszaallito level / ora
IP_RESEND = webauth.RateLimit("ip-resend", 5, 60 * 60)       # IP-nkent 5 uj megerosito link / ora (#44)
ACCOUNT_RESEND = webauth.RateLimit("account-resend", 3, 60 * 60)   # cimenkent 3 uj megerosito link / ora

_DUMMY_HASH: list[str] = []                     # ismeretlen e-mailnel is fusson egy scrypt (idozites)


class AccountError(ValueError):
    """A felhasznalonak szolo hiba (magyar szoveg)."""


class RateLimited(AccountError):
    pass


def _h(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AccountError("Érvénytelen e-mail-cím.")
    return email


def check_password(password: str) -> None:
    if len(password or "") < webauth.MIN_PASSWORD:
        raise AccountError(f"A jelszó legalább {webauth.MIN_PASSWORD} karakter legyen.")
    if len(password) > webauth.MAX_PASSWORD:
        raise AccountError("Túl hosszú jelszó.")
    if password.lower() in LEAKED:
        raise AccountError("Ez a jelszó szerepel a kiszivárgott jelszavak listáján – válassz másikat.")


def _new_token(conn, user_id: int, purpose: str, ttl: timedelta) -> str:
    conn.execute("DELETE FROM email_tokens WHERE expires_at < now() OR used_at IS NOT NULL")
    conn.execute("DELETE FROM email_tokens WHERE user_id = %s AND purpose = %s", (user_id, purpose))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, expires_at) VALUES (%s, %s, %s, now() + %s)",
                 (_h(token), user_id, purpose, ttl))
    return token


def _send(email: str, subject: str, body: str, lang: str) -> None:
    """A level a felhasznalo feluleti nyelven (a katalogusbol, soronkent; a link sor valtozatlan)."""
    mailer.send(email, i18n.translate(subject, lang), i18n.translate(body, lang))


def _verify_mail(email: str, token: str, base_url: str, lang: str = "hu") -> None:
    _send(email, "StockWatch – erősítsd meg az e-mail-címedet",
          f"A regisztráció befejezéséhez nyisd meg ezt a linket (24 óráig érvényes, egyszer használható):\n\n"
          f"{base_url}/verify?token={token}\n\nHa nem te regisztráltál, hagyd figyelmen kívül ezt a levelet.\n", lang)


# ---------------------------------------------------------------------------
# Regisztracio, ellenorzes
# ---------------------------------------------------------------------------
def register(email: str, password: str, accepted_terms: bool, age_ok: bool, ip: str, base_url: str,
             lang: str = "hu", source: str | None = None) -> None:
    """Uj fiok + ellenorzo level. Letezo, ellenorizetlen cimre uj ellenorzo levelet kuld, letezo ellenorzottre
    semmit - a valasz minden esetben ugyanaz."""
    if not IP_REGISTER.hit(ip):
        raise RateLimited("Túl sok regisztráció erről a címről. Próbáld újra később.")
    email = normalize_email(email)
    check_password(password)
    if not accepted_terms:
        raise AccountError("A regisztrációhoz el kell fogadnod az ÁSZF-et és az adatkezelési tájékoztatót.")
    if not age_ok:
        raise AccountError("A szolgáltatás 16 éven felülieknek szól.")
    password_hash = webauth.hash_password(password)
    with db.connection() as conn:
        # adattakarekossag (#40): a 30 napja meg nem erositett regisztraciok torlodnek
        conn.execute("DELETE FROM users WHERE email_verified_at IS NULL AND created_at < now() - %s", (UNVERIFIED_TTL,))
        row = conn.execute(
            "INSERT INTO users (email, password_hash, terms_version, terms_accepted_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT ((lower(email))) DO NOTHING RETURNING id", (email, password_hash, TERMS_VERSION)).fetchone()
        if row is not None:
            analytics.track("register", row[0], conn, method="password", source=source)
        if row is None:
            row = conn.execute("SELECT id FROM users WHERE lower(email) = %s AND email_verified_at IS NULL",
                               (email,)).fetchone()
        token = _new_token(conn, row[0], "verify", VERIFY_TTL) if row else None
    if token:
        _verify_mail(email, token, base_url, lang)


def resend_verification(email: str, ip: str, base_url: str, lang: str = "hu") -> None:
    """Uj megerosito link (#44) - csak letezo, meg nem megerositett fiokra; a regi link ervenytelen lesz. A valasz
    minden esetben ugyanaz (nincs fiok-felsorolas)."""
    email = normalize_email(email)
    if not IP_RESEND.hit(ip) or not ACCOUNT_RESEND.hit(email):
        raise RateLimited("Túl sok kérés. Próbáld újra később.")
    with db.connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(email) = %s AND email_verified_at IS NULL", (email,)).fetchone()
        token = _new_token(conn, row[0], "verify", VERIFY_TTL) if row else None
    if token:
        _verify_mail(email, token, base_url, lang)


def verify_email(token: str) -> bool:
    with db.connection() as conn:
        row = conn.execute("UPDATE email_tokens SET used_at = now() WHERE token_hash = %s AND purpose = 'verify' "
                           "AND used_at IS NULL AND expires_at > now() RETURNING user_id", (_h(token or ""),)).fetchone()
        if row:
            conn.execute("UPDATE users SET email_verified_at = coalesce(email_verified_at, now()) WHERE id = %s",
                         (row[0],))
            analytics.track("verify", row[0], conn)
    return row is not None


# ---------------------------------------------------------------------------
# Belepes, munkamenet
# ---------------------------------------------------------------------------
def login(email: str, password: str, ip: str, user_agent: str = "") -> str | None:
    """Uj munkamenet-token, vagy None (rossz e-mail / jelszo). Ellenorizetlen fiok: AccountError."""
    key = (email or "").strip().lower()
    if not IP_LOGIN.hit(ip) or not ACCOUNT_LOGIN.hit(key):
        raise RateLimited("Túl sok próbálkozás. Próbáld újra 15 perc múlva.")
    with db.connection() as conn:
        row = conn.execute("SELECT id, password_hash, email_verified_at FROM users WHERE lower(email) = %s",
                           (key,)).fetchone()
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(webauth.hash_password(secrets.token_urlsafe(16)))
    ok = webauth.verify_password(password, (row[1] if row else None) or _DUMMY_HASH[0])
    if not (row and row[1] and ok):                     # csak Google-os fiok (nincs jelszo): nincs jelszavas belepes
        return None
    if row[2] is None:
        raise AccountError("Előbb erősítsd meg az e-mail-címedet a regisztrációkor küldött linkkel. Nem kaptad meg? "
                           "Nézd meg a spam mappát, vagy kérj újat lent: „Nem kaptad meg a megerősítő levelet?”")
    ACCOUNT_LOGIN.clear(key)
    with db.connection() as conn:
        token = _new_session(conn, row[0], user_agent)
        analytics.track("login", row[0], conn, method="password")
        return token


def _new_session(conn, user_id: int, user_agent: str) -> str:
    if conn.execute("SELECT suspended_at IS NOT NULL FROM users WHERE id = %s", (user_id,)).fetchone()[0]:
        raise AccountError("A fiókod fel van függesztve. Kérdés esetén írj a kapcsolati címre.")      # admin (#58)
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at, user_agent) VALUES (%s, %s, now() + %s, %s)",
                 (_h(token), user_id, SESSION_TTL, (user_agent or "")[:200]))
    conn.execute("DELETE FROM sessions WHERE user_id = %s AND token_hash NOT IN (SELECT token_hash FROM sessions "
                 "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s)", (user_id, user_id, MAX_SESSIONS_PER_USER))
    return token


def session_user(token: str | None) -> dict | None:
    """A munkamenet felhasznaloja ({"id", "email", "onboarded"}), vagy None (nincs / lejart munkamenet)."""
    if not token:
        return None
    with db.connection() as conn:
        row = conn.execute("UPDATE sessions s SET last_used_at = now() FROM users u WHERE s.token_hash = %s "
                           "AND s.expires_at > now() AND u.id = s.user_id AND u.suspended_at IS NULL "
                           "RETURNING u.id, u.email, u.onboarded_at IS NOT NULL, u.nickname, u.password_hash IS NOT NULL, "
                           "EXISTS (SELECT 1 FROM user_identities i WHERE i.user_id = u.id AND i.provider = 'google'), "
                           "u.role, s.admin_at", (_h(token),)).fetchone()
    return {"id": row[0], "email": row[1], "onboarded": row[2], "nickname": row[3], "has_password": row[4],
            "google": row[5], "role": row[6], "admin_at": row[7]} if row else None


def mark_onboarded(user_id: int) -> None:
    """Az elso belepeskori bevezeto (#39) vege - tobbe nem jelenik meg."""
    with db.connection() as conn:
        conn.execute("UPDATE users SET onboarded_at = coalesce(onboarded_at, now()) WHERE id = %s", (user_id,))


def logout(token: str | None) -> None:
    if token:
        with db.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_h(token),))


def logout_everywhere(user_id: int) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


# ---------------------------------------------------------------------------
# Jelszo-visszaallitas
# ---------------------------------------------------------------------------
def request_reset(email: str, ip: str, base_url: str, lang: str = "hu") -> None:
    """Visszaallito level, ha van ilyen fiok - a valasz minden esetben ugyanaz."""
    email = normalize_email(email)
    if not IP_RESET.hit(ip) or not ACCOUNT_RESET.hit(email):
        raise RateLimited("Túl sok kérés. Próbáld újra később.")
    with db.connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (email,)).fetchone()
        token = _new_token(conn, row[0], "reset", RESET_TTL) if row else None
    if token:
        _send(email, "StockWatch – új jelszó beállítása",
              f"Új jelszót ezen a linken állíthatsz be (1 óráig érvényes, egyszer használható):\n\n"
              f"{base_url}/reset?token={token}\n\nHa nem te kérted, hagyd figyelmen kívül ezt a levelet – "
              f"a jelszavad nem változik.\n", lang)


def reset_password(token: str, password: str) -> bool:
    """Uj jelszo a visszaallito tokennel; az e-mailt is igazolja, es minden munkamenetet zar."""
    check_password(password)
    password_hash = webauth.hash_password(password)
    with db.connection() as conn:
        row = conn.execute("UPDATE email_tokens SET used_at = now() WHERE token_hash = %s AND purpose = 'reset' "
                           "AND used_at IS NULL AND expires_at > now() RETURNING user_id", (_h(token or ""),)).fetchone()
        if row:
            conn.execute("UPDATE users SET password_hash = %s, email_verified_at = coalesce(email_verified_at, now()) "
                         "WHERE id = %s", (password_hash, row[0]))
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (row[0],))
    return row is not None


# ---------------------------------------------------------------------------
# Fiok torlese, adatexport (#40)
# ---------------------------------------------------------------------------
def delete_account(user_id: int, password: str) -> bool:
    """A fiok vegleges torlese a jelszo (csak Google-os fioknal: az e-mail-cim) megerositesevel. A kaszkad viszi a
    munkameneteket, a tokeneket es a gyakorlo koroket; a torles utan az adatok nem kerdezhetok le. Rossz: False."""
    with db.connection() as conn:
        row = conn.execute("SELECT password_hash, email FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return False
    if row[0] is None:
        if (password or "").strip().lower() != row[1].lower():
            return False
    elif not webauth.verify_password(password, row[0]):
        return False
    erase(user_id)
    return True


def erase(user_id: int) -> None:
    """A fiok es minden adata (kaszkad); az esemenyek anonimizalva maradnak (#48)."""
    with db.connection() as conn:
        anon = analytics.anonymize(conn, user_id)
        analytics._insert(conn, "account_delete", anon, {})
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


def export(user_id: int) -> dict:
    """A felhasznalo minden tarolt adata gepi feldolgozasra alkalmas formaban (GDPR 20. cikk) - jelszo-hash es
    token-hash nelkul."""
    from .practice import store

    with db.connection() as conn:
        row = conn.execute("SELECT email, created_at, email_verified_at, terms_version, terms_accepted_at, onboarded_at "
                           "FROM users WHERE id = %s", (user_id,)).fetchone()
        sessions = conn.execute("SELECT created_at, last_used_at, expires_at, user_agent FROM sessions "
                                "WHERE user_id = %s ORDER BY created_at", (user_id,)).fetchall()
        identities = conn.execute("SELECT provider, email, created_at FROM user_identities WHERE user_id = %s",
                                  (user_id,)).fetchall()
        groups = conn.execute("SELECT g.name, g.owner_id = %s, m.joined_at FROM group_members m JOIN learning_groups g "
                              "ON g.id = m.group_id WHERE m.user_id = %s", (user_id, user_id)).fetchall()
        shares = conn.execute("SELECT id, kind, ref, data, created_at, revoked_at FROM shares WHERE user_id = %s "
                              "ORDER BY created_at", (user_id,)).fetchall()
        duels = conn.execute("SELECT id, rounds, created_at, expires_at, revoked_at, challenger_id = %s, winner_id = %s "
                             "FROM duels WHERE challenger_id = %s OR opponent_id = %s ORDER BY created_at",
                             (user_id, user_id, user_id, user_id)).fetchall()
        league = conn.execute("SELECT tier, opted_out_at FROM league_members WHERE user_id = %s", (user_id,)).fetchone()
        weeks = conn.execute("SELECT week, tier, xp, rank, outcome FROM league_entries WHERE user_id = %s ORDER BY week",
                             (user_id,)).fetchall()
    keys =("email", "created_at", "email_verified_at", "terms_version", "terms_accepted_at", "onboarded_at")
    return {"account": dict(zip(keys, row)) if row else None,
            "sessions": [dict(zip(("created_at", "last_used_at", "expires_at", "user_agent"), s)) for s in sessions],
            "identities": [dict(zip(("provider", "email", "created_at"), i)) for i in identities],
            "groups": [dict(zip(("name", "owner", "joined_at"), g)) for g in groups],
            "shares": [dict(zip(("id", "kind", "ref", "data", "created_at", "revoked_at"), s)) for s in shares],
            "duels": [dict(zip(("id", "rounds", "created_at", "expires_at", "revoked_at", "challenger", "won"), d))
                      for d in duels],
            "league": {"tier": league[0], "opted_out_at": league[1]} if league else None,
            "league_weeks": [dict(zip(("week", "tier", "xp", "rank", "outcome"), w)) for w in weeks],
            "practice": [store.load_session(s["id"], user_id) for s in store.sessions(user_id, limit=store.MAX_SESSIONS)]}


# ---------------------------------------------------------------------------
# Google-fiok (#43) - a token-ellenorzes a google_auth.py-ban
# ---------------------------------------------------------------------------
def google_login(subject: str, email: str, link_user: int | None, terms: bool, user_agent: str = "") -> tuple[str, str | None]:
    """A Google-azonosito (sub) feldolgozasa. Visszaad: ("session", token) | ("linked", None) | (hibakod, None):
      * ismert sub: belepes abba a fiokba (osszekapcsolasnal: masik fiokhoz kotott -> "taken")
      * osszekapcsolas (a felhasznalo jelszoval mar belepett): a sub az o fiokjahoz kerul
      * ismeretlen sub, de letezo e-mail: "link_required" - nincs automatikus fiokatvetel
      * uj fiok: csak az ASZF + 16+ elfogadasaval ("terms_required"); a Google mar igazolta az e-mailt"""
    email = (email or "").strip().lower()
    with db.connection() as conn:
        row = conn.execute("SELECT user_id FROM user_identities WHERE provider = 'google' AND subject = %s",
                           (subject,)).fetchone()
        if row:
            if link_user is not None:
                return ("linked", None) if row[0] == link_user else ("taken", None)
            token = _new_session(conn, row[0], user_agent)
            analytics.track("login", row[0], conn, method="google")
            return "session", token
        if link_user is not None:
            conn.execute("INSERT INTO user_identities (provider, subject, user_id, email) VALUES ('google', %s, %s, %s)",
                         (subject, link_user, email))
            return "linked", None
        if conn.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,)).fetchone():
            return "link_required", None
        if not terms:
            return "terms_required", None
        user_id = conn.execute(
            "INSERT INTO users (email, password_hash, email_verified_at, terms_version, terms_accepted_at) "
            "VALUES (%s, NULL, now(), %s, now()) RETURNING id", (email, TERMS_VERSION)).fetchone()[0]
        conn.execute("INSERT INTO user_identities (provider, subject, user_id, email) VALUES ('google', %s, %s, %s)",
                     (subject, user_id, email))
        for name in ("register", "verify", "login"):
            analytics.track(name, user_id, conn, method="google")
        return "session", _new_session(conn, user_id, user_agent)


def unlink_google(user_id: int) -> None:
    """A Google-kapcsolat bontasa - csak ha van jelszo (kulonben a fiok elerhetetlenne valna)."""
    with db.connection() as conn:
        if conn.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,)).fetchone()[0] is None:
            raise AccountError("Előbb állíts be jelszót (Elfelejtett jelszó), különben nem tudnál belépni.")
        conn.execute("DELETE FROM user_identities WHERE user_id = %s AND provider = 'google'", (user_id,))
