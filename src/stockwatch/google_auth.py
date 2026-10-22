"""Belepes Google-fiokkal (#43): OpenID Connect, authorization code + PKCE, szerveroldalon.

    /auth/google/start  ->  Google  ->  /auth/google/callback       (webapp.py)

  * hatokor csak `openid email profile` - Gmailhez, Drive-hoz semmilyen hozzaferes nem kell es nem kerunk
  * a `state` es a `nonce` egyszer hasznalhato, STATE_TTL mp-ig ervenyes; a `state` a bongeszohoz is kotve (suti)
  * az ID token a Google token-vegpontjarol jon, TLS-en, a kliens-titokkal hitelesitett kerese valaszakent. Az OIDC
    Core 3.1.3.7 / 6. pontja szerint ilyenkor az alairas ellenorzese helyett a TLS-szerver-azonositas is eleg - igy
    nem kell uj fuggoseg (JWKS / RSA); a claimeket (iss, aud, exp, nonce, email_verified) mindig ellenorizzuk
  * a kliens-titok csak a szerver titokfajljaban (GOOGLE_CLIENT_SECRET): naploba, hibauzenetbe, valaszba nem kerul
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_TTL = 600
COOKIE = "sw_oauth"
# shortcut: a fuggo bejelentkezesek egy folyamat memoriajaban (10 percig) - tobb webes folyamatnal adatbazisba kell
_PENDING: dict[str, dict] = {}
_LOCK = threading.Lock()


class GoogleError(Exception):
    """A Google-belepes nem sikerult (a szoveg altalanos, titok nelkul)."""


def configured() -> bool:
    return bool(config.env_value("GOOGLE_CLIENT_ID") and config.env_value("GOOGLE_CLIENT_SECRET"))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def begin(base_url: str, link_user: int | None = None, terms: bool = False) -> tuple[str, str]:
    """(a Google bejelentkezo oldalanak cime, state). `link_user`: osszekapcsolas a bejelentkezett fiokkal;
    `terms`: a regisztracios oldalon elfogadta az ASZF-et es a 16+ feltetelt (uj fiokhoz kell)."""
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    now = time.time()
    with _LOCK:
        for key in [k for k, v in _PENDING.items() if now - v["created"] > STATE_TTL]:
            del _PENDING[key]
        _PENDING[state] = {"nonce": nonce, "verifier": verifier, "created": now, "link_user": link_user, "terms": terms}
    query = {"client_id": config.env_value("GOOGLE_CLIENT_ID"), "redirect_uri": f"{base_url}/auth/google/callback",
             "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
             "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
             "prompt": "select_account"}
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}", state


def _exchange(code: str, verifier: str, redirect_uri: str) -> str:
    """A kod cserje ID tokenre a Google token-vegpontjan."""
    body = urllib.parse.urlencode({"code": code, "client_id": config.env_value("GOOGLE_CLIENT_ID"),
                                   "client_secret": config.env_value("GOOGLE_CLIENT_SECRET"), "redirect_uri": redirect_uri,
                                   "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=15) as res:
            return json.loads(res.read())["id_token"]
    except urllib.error.HTTPError as exc:
        log.warning("Google token-csere elutasitva (HTTP %s)", exc.code)
    except (OSError, ValueError, KeyError) as exc:
        log.warning("Google token-csere sikertelen: %s", type(exc).__name__)
    raise GoogleError("A Google-belépés nem sikerült.")


def claims(id_token: str, nonce: str) -> dict:
    """Az ID token claimjei, ellenorizve: kibocsato, kozonseg (a sajat kliensazonosito), lejarat, nonce, igazolt
    e-mail. Barmelyik hibas: GoogleError."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(id_token.split(".")[1] + "=="))
    except (IndexError, ValueError):
        raise GoogleError("Érvénytelen Google-válasz.") from None
    aud = payload.get("aud")
    checks = (payload.get("iss") in ISSUERS,
              aud == config.env_value("GOOGLE_CLIENT_ID") or (isinstance(aud, list) and config.env_value("GOOGLE_CLIENT_ID") in aud),
              isinstance(payload.get("exp"), (int, float)) and payload["exp"] > time.time(),
              secrets.compare_digest(str(payload.get("nonce") or ""), nonce),
              payload.get("email_verified") is True and bool(payload.get("email")) and bool(payload.get("sub")))
    if not all(checks):
        raise GoogleError("A Google-belépés nem igazolható.")
    return payload


def finish(state: str, cookie_state: str | None, code: str, base_url: str) -> tuple[dict, dict]:
    """(a fuggo bejelentkezes adatai, ellenorzott claimek). A state egyszer hasznalhato, es a bongeszo sutijevel
    egyezik (CSRF / bejelentkezes-csempeszes ellen)."""
    with _LOCK:
        pending = _PENDING.pop(state or "", None)
    if (pending is None or not cookie_state or not secrets.compare_digest(cookie_state, state)
            or time.time() - pending["created"] > STATE_TTL or not code):
        raise GoogleError("A Google-belépés lejárt vagy érvénytelen – próbáld újra.")
    token = _exchange(code, pending["verifier"], f"{base_url}/auth/google/callback")
    return pending, claims(token, pending["nonce"])
