"""Bejelentkezes a webes feluletre - szerveren (proxy mogott / halozati cimen) kotelezo.

    python cli.py web-password       ->  jelszo beallitasa / csereje

JELSZO
------
  * csak a scrypt hash-e kerul a titokfajlba (STOCKWATCH_WEB_PASSWORD_HASH), maga a
    jelszo sehol nincs tarolva; minimum 12 karakter
  * a hash-t minden keresnel frissen olvassuk: jelszocsere utan azonnal ervenyes,
    es minden korabbi munkamenetet kileptet

MUNKAMENET
----------
  * 256 bites veletlen token HttpOnly + SameSite=Strict sutiben (HTTPS mogott Secure is)
  * csak a szerver memoriajaban el (ujrainditas utan ujra be kell lepni), 7 napig
    ervenyes, legfeljebb MAX_SESSIONS darab - a legregebbi esik ki

TALALGATAS ELLEN
----------------
  * IP-nkent 5 hibas probalkozas utan 15 perc zarolas, minden hiba 1 mp kesleltetes
  * egyszerre csak egy jelszo-ellenorzes fut (a scrypt 32 MB memoriat ker) - sok
    parhuzamos keres sem tudja a gep memoriajat elfogyasztani
  * a kliens IP-jet csak akkor vesszuk az X-Forwarded-For fejlecbol, ha a kapcsolat a
    gepen beluli proxytol jon, es a felulet --behind-proxy modban fut
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import OrderedDict
from http.cookies import CookieError, SimpleCookie

from . import config

log = logging.getLogger(__name__)

HASH_KEY = "STOCKWATCH_WEB_PASSWORD_HASH"
COOKIE = "sw_session"
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
SESSION_TTL = 7 * 24 * 3600
MAX_SESSIONS = 50
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
FAIL_DELAY = 1.0
MAX_TRACKED_IPS = 10_000
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 64 * 1024 * 1024


class LoginLocked(Exception):
    """Az adott IP-rol tul sok hibas probalkozas jott."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """'scrypt:n:r:p:salt:hash' - csak [A-Za-z0-9_-:], igy a .env-ben idezojel nelkul is biztonsagos."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"A jelszo legalabb {MIN_PASSWORD} karakter legyen.")
    if len(password) > MAX_PASSWORD:
        raise ValueError("Tul hosszu jelszo.")
    salt = secrets.token_bytes(16)
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
        got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        log.error("A %s erteke serult - allitsd be ujra: python cli.py web-password", HASH_KEY)
        return False
    return hmac.compare_digest(got, expected)


class Auth:
    """A webes felulet beleptetese. Ha nincs jelszo beallitva es nem kotelezo, minden szabad
    (helyi, 127.0.0.1-es hasznalat); ha kotelezo es nincs jelszo, semmi sem szabad."""

    def __init__(self, password_hash=None, trust_proxy: bool = False, required: bool = False) -> None:
        self._hash = password_hash or (lambda: config.env_value(HASH_KEY))
        self.trust_proxy = trust_proxy
        self.required = required
        self._lock = threading.Lock()
        self._verify = threading.Lock()
        self._sessions: OrderedDict[str, tuple[float, str]] = OrderedDict()   # token -> (lejarat, hash)
        self._fails: dict[str, list[float]] = {}

    @property
    def has_password(self) -> bool:
        return bool(self._hash())

    @property
    def enabled(self) -> bool:
        return self.required or self.has_password

    # -- munkamenet -------------------------------------------------------
    def check(self, token: str | None) -> bool:
        current = self._hash()
        if not current:
            return not self.required
        if not token:
            return False
        with self._lock:
            item = self._sessions.get(token)
            if item is None:
                return False
            expires, issued_for = item
            if expires < time.time() or not hmac.compare_digest(issued_for, current):
                del self._sessions[token]
                return False
            return True

    def login(self, ip: str, password: str) -> str | None:
        """Sikeres belepeskor uj munkamenet-token, kulonben None. Zarolt IP-nel LoginLocked."""
        current = self._hash()
        if not current:
            return None
        with self._lock:
            if len(self._recent_fails(ip)) >= MAX_FAILS:
                raise LoginLocked
        with self._verify:
            ok = verify_password(password, current)
        if not ok:
            with self._lock:
                self._recent_fails(ip).append(time.time())
            log.warning("Sikertelen bejelentkezes: %s", ip)
            time.sleep(FAIL_DELAY)
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._fails.pop(ip, None)
            self._sessions[token] = (time.time() + SESSION_TTL, current)
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        log.info("Bejelentkezes: %s", ip)
        return token

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def _recent_fails(self, ip: str) -> list[float]:
        """Az IP utolso LOCK_SECONDS-on beluli hibai (a tarolt IP-k szama korlatos)."""
        now = time.time()
        times = [t for t in self._fails.get(ip, []) if now - t < LOCK_SECONDS]
        self._fails[ip] = times
        if len(self._fails) > MAX_TRACKED_IPS:
            self._fails = {k: v for k, v in self._fails.items()
                           if v and now - v[-1] < LOCK_SECONDS or k == ip}
            if len(self._fails) > MAX_TRACKED_IPS:
                self._fails = {ip: times}
        return times

    # -- keres adatai -----------------------------------------------------
    def _via_proxy(self, handler) -> bool:
        return self.trust_proxy and handler.client_address[0] in LOOPBACK

    def client_ip(self, handler) -> str:
        if self._via_proxy(handler):
            forwarded = handler.headers.get("X-Forwarded-For", "")
            last = forwarded.split(",")[-1].strip()
            if last:
                return last[:64]
        return handler.client_address[0]

    def is_https(self, handler) -> bool:
        return self._via_proxy(handler) and handler.headers.get("X-Forwarded-Proto", "") == "https"


def session_token(handler) -> str | None:
    try:
        cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    except CookieError:
        return None
    morsel = cookie.get(COOKIE)
    return morsel.value if morsel else None


def session_cookie(token: str, secure: bool, max_age: int = SESSION_TTL) -> str:
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
