"""Bejelentkezesi segedek: jelszo-hash (scrypt), munkamenet-suti, kliens-IP, keresgyakorisag-korlat (adatbazisban).
A fiokok es a munkamenetek az adatbazisban vannak (accounts.py).

JELSZO
------
  * csak a scrypt hash-e tarolodik (sajat sav, n=2^15, r=8, p=1), maga a jelszo sehol
  * egyszerre legfeljebb SCRYPT_SLOTS ellenorzes fut (egy scrypt 32 MB memoriat ker) - sok
    parhuzamos keres sem tudja a gep memoriajat elfogyasztani

MUNKAMENET-SUTI
---------------
  * 256 bites veletlen token, HttpOnly + SameSite=Strict (HTTPS mogott Secure is)

KLIENS-IP
---------
  * az X-Forwarded-For fejlecbol csak akkor, ha a kapcsolat a gepen beluli proxytol jon, es a felulet
    --behind-proxy modban fut
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
from datetime import timedelta
from http.cookies import CookieError, SimpleCookie

from . import db

log = logging.getLogger(__name__)

COOKIE = "sw_session"
MIN_PASSWORD = 10
MAX_PASSWORD = 1024
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 64 * 1024 * 1024
SCRYPT_SLOTS = threading.BoundedSemaphore(2)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """'scrypt:n:r:p:salt:hash' - csak [A-Za-z0-9_-:]."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"A jelszo legalabb {MIN_PASSWORD} karakter legyen.")
    if len(password) > MAX_PASSWORD:
        raise ValueError("Tul hosszu jelszo.")
    salt = secrets.token_bytes(16)
    with SCRYPT_SLOTS:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=_MAXMEM, dklen=32, **_SCRYPT)
    return f"scrypt:{_SCRYPT['n']}:{_SCRYPT['r']}:{_SCRYPT['p']}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    if not password or len(password) > MAX_PASSWORD:
        return False
    try:
        algo, n, r, p, salt, digest = stored.split(":")
        if algo != "scrypt":
            return False
        expected = _unb64(digest)
        with SCRYPT_SLOTS:
            got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                 maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        log.error("Serult jelszo-hash az adatbazisban")
        return False
    return hmac.compare_digest(got, expected)


class RateLimit:
    """Legfeljebb `limit` esemeny kulcsonkent (IP, e-mail) a csuszo `window` mp-es ablakban - az adatbazisban
    (rate_hits), igy tobb webes folyamat / gep kozott is kozos. A `hit` egy tranzakcioban, kulcsonkenti advisory
    lock alatt ellenoriz es konyvel: az egyszerre erkezo keresek sem jutnak at tobben."""

    def __init__(self, name: str, limit: int, window: float) -> None:
        self.name, self.limit, self.window = name, limit, window

    def _key(self, key: str) -> str:
        return f"{self.name}:{key}"[:300]

    def hit(self, key: str) -> bool:
        key = self._key(key)
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            conn.execute("DELETE FROM rate_hits WHERE at < now() - interval '1 day'")   # a tabla nem no korlatlanul
            conn.execute("DELETE FROM rate_hits WHERE key = %s AND at < now() - %s", (key, timedelta(seconds=self.window)))
            if conn.execute("SELECT count(*) FROM rate_hits WHERE key = %s", (key,)).fetchone()[0] >= self.limit:
                return False
            conn.execute("INSERT INTO rate_hits (key) VALUES (%s)", (key,))
            return True

    def clear(self, key: str) -> None:
        with db.connection() as conn:
            conn.execute("DELETE FROM rate_hits WHERE key = %s", (self._key(key),))


def session_token(handler) -> str | None:
    try:
        cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    except CookieError:
        return None
    morsel = cookie.get(COOKIE)
    return morsel.value if morsel else None


def session_cookie(token: str, secure: bool, max_age: int) -> str:
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def via_proxy(handler, trust_proxy: bool) -> bool:
    return trust_proxy and handler.client_address[0] in LOOPBACK


def client_ip(handler, trust_proxy: bool) -> str:
    if via_proxy(handler, trust_proxy):
        last = handler.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        if last:
            return last[:64]
    return handler.client_address[0]


def is_https(handler, trust_proxy: bool) -> bool:
    return via_proxy(handler, trust_proxy) and handler.headers.get("X-Forwarded-Proto", "") == "https"
