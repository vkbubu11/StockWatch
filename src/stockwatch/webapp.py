"""Az oktatasi szolgaltatas webes felulete - sima HTML/CSS/JS, a Python standard konyvtaranak
webszerverevel. Csak a Gyakorlas es a tanulas: nincs broker, monitor, ertesites, labor (#29).

    python cli.py web            ->  http://127.0.0.1:8765      (DATABASE_URL kell, lasd db.py)

BIZTONSAG
---------
  * felhasznaloi fiokok (accounts.py): minden API-keres a bejelentkezett felhasznalo neveben fut, es minden
    azonosito csak a sajat adatai kozott keresodik (mas felhasznaloe: "nincs ilyen")
  * a modosito API-keresek (POST) egyedi fejlecet varnak (X-StockWatch): idegen weboldal a bongeszobol ilyet
    nem tud kuldeni (CORS preflight nelkul nem mehet at); az urlapok (belepes, regisztracio...) Origin-ellenorzest
  * a feladatkeszletet a webes keres soha nem epiti (python cli.py practice-pool)
"""
from __future__ import annotations

import html
import json
import logging
import math
import posixpath
import re
import threading
from datetime import date, datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from . import accounts, config, db, google_auth, webauth
from .config import PROJECT_ROOT, load_settings
from .locks import LockTimeout

log = logging.getLogger(__name__)

WEB_DIR = PROJECT_ROOT / "app" / "web"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
PUBLIC_FILES = ("/style.css", "/i18n.js")             # bejelentkezes nelkul is (+ /i18n/<nyelv>.json, urlapok)
AUTH_PAGES = ("/login", "/register", "/forgot", "/reset", "/resend")
LEGAL_PAGES = {"/aszf": "aszf.html", "/adatkezeles": "adatkezeles.html", "/sutik": "sutik.html"}   # nyilvanos (#40)
# AI-felfedezes (GEO): nyilvanos, gepi olvasasra; a {URL} es a {CONTACT} a szerver beallitasaibol
AI_FILES = {"/llms.txt": "llms.txt", "/ai.json": "ai.json", "/.well-known/ai.json": "ai.json", "/ecp.json": "ecp.json",
            "/.well-known/ecp.json": "ecp.json"}
SITEMAP = ("/", "/about", "/register", "/login", "/aszf", "/adatkezeles", "/sutik")
LESSON_RE = re.compile(r"/api/lessons/(\d{1,2})(/start)?")
# admin felulet (#58): nem adminnak minden /admin es /api/admin utvonal 404
ADMIN_FILES = {"/admin": "index.html", "/admin/": "index.html", "/admin/admin.js": "admin.js"}
ADMIN_GET = re.compile(r"/api/admin/(state|overview|users|users/(\d{1,12})|content|groups|system|audit)")
ADMIN_POST = re.compile(r"/api/admin/(verify|logout|users/(\d{1,12})/(resend|suspend|unsuspend|reset_nickname|delete|export)"
                        r"|lessons|lessons/restore|daily/replace|pool/rebuild|groups/(\d{1,12})/(revoke_code|delete))")
CLIENT_ERRORS = webauth.RateLimit("ip-client-error", 20, 60 * 60)   # a bongeszos hibaszamlalo (#48) IP-nkent
PRACTICE_SESSION_RE = re.compile(r"/api/practice/session/([a-z]{12})/(task/(\d{1,2})(/decision)?|summary)")

# az urlapok visszajelzesei (?e=kod): (fajta, szoveg)
NOTICES = {
    "sent": ("info", "Ha ez a cím még nincs megerősítve, küldtünk rá egy ellenőrző linket. Nézd meg a leveleidet "
                     "(a spam mappát is)."),
    "resent": ("info", "Ha ez a cím egy még nem megerősített fiókhoz tartozik, küldtünk rá egy új linket (a régi már "
                       "nem érvényes). Nézd meg a spam mappát is."),
    "verified": ("info", "Az e-mail-címed megerősítve – most már beléphetsz."),
    "badlink": ("error", "A link lejárt vagy már felhasználták. Kérj újat."),
    "resetsent": ("info", "Ha van ilyen fiók, küldtünk egy linket az új jelszó beállításához."),
    "reset": ("info", "Az új jelszavad beállítva – lépj be vele. A többi eszközön kiléptettünk."),
    "bad": ("error", "Hibás e-mail-cím vagy jelszó."),
    "origin": ("error", "A kérés nem erről az oldalról jött."),
    "loggedout": ("info", "Kiléptettünk minden eszközön."),
    "googlelink": ("error", "Ehhez az e-mail-címhez már van jelszavas fiók. Lépj be jelszóval, és a Profil oldalon "
                            "kapcsold össze a Google-fiókoddal."),
    "googleterms": ("error", "Új fiókhoz fogadd el az ÁSZF-et és az adatkezelési tájékoztatót, majd folytasd Google-lel."),
    "googlefail": ("error", "A Google-belépés nem sikerült – próbáld újra."),
    "googletaken": ("error", "Ez a Google-fiók már egy másik fiókhoz kapcsolódik."),
}


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def _clean(value):
    """pandas / numpy ertekek JSON-baratta alakitasa (NaN -> null)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d") if value == value.normalize() else value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else v
    return value


def _dumps(payload) -> bytes:
    return json.dumps(_clean(payload), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Gyakorlas
# ---------------------------------------------------------------------------
def practice_payload(user_id: int) -> dict:
    """A feladatkeszlet allapota, a felhasznalo korei (papir es datum nelkul), az alapertekek."""
    from .practice import sampler
    from .practice import session as practice
    from .practice import store as practice_store

    return {"pool": sampler.pool_info(practice_store.load_pool()), "sessions": practice.listing(user_id),
            "defaults": {"rounds": 20, "mix": sampler.DEFAULT_MIX, "max_days": sampler.MAX_DAYS,
                         "max_rounds": sampler.MAX_ROUNDS}}


def practice_action(action: str, body: dict, user_id: int) -> dict:
    from .practice import session as practice

    if action == "session":
        seed = body.get("seed")
        created = practice.create(user_id, load_settings(), seed=int(seed) if seed not in (None, "") else None,
                                  rounds=int(body.get("rounds") or 20), mix=body.get("mix") or None,
                                  code=str(body.get("code") or "").strip() or None, kinds_mix=body.get("kinds") or None)
        return {"session": created}
    if action == "delete":
        if not practice.delete(user_id, str(body.get("id") or "")):
            raise LookupError("Nincs ilyen munkamenet.")
        return practice_payload(user_id) | {"message": "Torolve."}
    raise ValueError("Ismeretlen muvelet")


# ---------------------------------------------------------------------------
# Urlapok (szerveroldalon, JavaScript nelkul is mukodnek)
# ---------------------------------------------------------------------------
def _field(name: str, label: str, kind: str, autocomplete: str, value: str = "", extra: str = "") -> str:
    return (f'<label for="{name}">{label}</label><input id="{name}" name="{name}" type="{kind}" '
            f'autocomplete="{autocomplete}" value="{html.escape(value)}" required {extra}>')


def auth_form(page: str, email: str = "", token: str = "", google: bool = False) -> tuple[str, str]:
    """(cim, urlap) a belepes / regisztracio / elfelejtett jelszo / uj jelszo oldalhoz; `google`: a Google-gomb is."""
    pw_min = f'minlength="{webauth.MIN_PASSWORD}" maxlength="{webauth.MAX_PASSWORD}"'
    links = {"login": '<a href="/register">Regisztráció</a><a href="/forgot">Elfelejtett jelszó</a>'
                      '<a href="/resend">Nem kaptad meg a megerősítő levelet?</a>',
             "resend": '<a href="/login">Vissza a belépéshez</a>',
             "register": '<a href="/login">Van már fiókom – belépés</a>',
             "forgot": '<a href="/login">Vissza a belépéshez</a>', "reset": '<a href="/login">Vissza a belépéshez</a>'}
    if page == "login":
        title, body = "Bejelentkezés", (_field("email", "E-mail-cím", "email", "username", email, "autofocus")
                                  + _field("password", "Jelszó", "password", "current-password")
                                  + '<button class="btn primary" type="submit">Bejelentkezés</button>')
    elif page == "register":
        title, body = "Regisztráció", (
            _field("email", "E-mail-cím", "email", "username", email, "autofocus")
            + _field("password", f"Jelszó (legalább {webauth.MIN_PASSWORD} karakter)", "password", "new-password",
                     extra=pw_min)
            + '<label class="check"><input type="checkbox" name="terms" value="1" required> <span>Elfogadom az '
              '<a href="/aszf" target="_blank">ÁSZF-et</a> és az <a href="/adatkezeles" target="_blank">adatkezelési '
              'tájékoztatót</a>.</span></label>'
            + '<label class="check"><input type="checkbox" name="age" value="1" required> <span>Elmúltam 16 éves.</span>'
              '</label><button class="btn primary" type="submit">Regisztráció</button>')
    elif page == "resend":
        title, body = "Új megerősítő link", (_field("email", "E-mail-cím", "email", "username", email, "autofocus")
                                             + '<button class="btn primary" type="submit">Új link küldése</button>')
    elif page == "forgot":
        title, body = "Elfelejtett jelszó", (_field("email", "E-mail-cím", "email", "username", email, "autofocus")
                                             + '<button class="btn primary" type="submit">Link küldése</button>')
    else:
        title, body = "Új jelszó", (f'<input type="hidden" name="token" value="{html.escape(token)}">'
                                    + _field("password", f"Új jelszó (legalább {webauth.MIN_PASSWORD} karakter)",
                                             "password", "new-password", extra=pw_min + " autofocus")
                                    + '<button class="btn primary" type="submit">Jelszó beállítása</button>')
    top = ""                                          # a Google-lehetoseg felul (gyors regisztracio / belepes)
    if google and page == "login":
        top = ('<a class="btn primary auth-google" href="/auth/google/start">Folytatás Google-lel</a>'
               '<p class="auth-or">vagy e-mail-címmel</p>')
    elif google and page == "register":
        top = ('<form method="get" action="/auth/google/start" class="field">'
               '<input type="hidden" name="terms" value="1">'
               '<label class="check"><input type="checkbox" name="age" value="1" required> <span>Elfogadom az '
               '<a href="/aszf" target="_blank">ÁSZF-et</a> és az <a href="/adatkezeles" target="_blank">adatkezelési '
               'tájékoztatót</a>, és elmúltam 16 éves.</span></label>'
               '<button class="btn primary auth-google" type="submit">Gyors regisztráció Google-fiókkal</button></form>'
               '<p class="auth-or">vagy e-mail-címmel</p>')
    return title, (f'{top}<form method="post" action="/{page}" class="field">{body}</form>'
                   f'<p class="auth-links">{links[page]}</p>')


def auth_page(page: str, message: tuple[str, str] | None = None, email: str = "", token: str = "") -> bytes:
    title, form = auth_form(page, email, token, google_auth.configured())
    notice = (f'<p class="login-{"error" if message[0] == "error" else "info"}" role="alert">{html.escape(message[1])}</p>'
              if message else "")
    text = (WEB_DIR / "auth.html").read_text(encoding="utf-8")
    return text.replace("<!--TITLE-->", title).replace("<!--MESSAGE-->", notice).replace("<!--FORM-->", form).encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def make_handler(trust_proxy: bool = False):

    class Handler(BaseHTTPRequestHandler):
        server_version = "StockWatch"

        def log_message(self, fmt, *args):  # a konzolt ne szemetelje tele
            log.debug("web: " + fmt, *args)

        # -- valaszok --------------------------------------------------
        def _https(self) -> bool:
            return webauth.is_https(self, trust_proxy)

        def _headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if self._https():
                self.send_header("Strict-Transport-Security", "max-age=31536000")

        def _send(self, status: int, body: bytes, ctype: str, cookie: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._headers()
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; form-action 'self'",
            )
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, cookie: str | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self._headers()
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _json(self, payload, status: int = 200, cookie: str | None = None) -> None:
            self._send(status, _dumps(payload), "application/json; charset=utf-8", cookie)

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _page(self, page: str, status: int = 200, message=None, **fields) -> None:
            self._send(status, auth_page(page, message, **fields), STATIC_TYPES[".html"])

        def _cookie(self, token: str, max_age: int | None = None) -> str:
            ttl = int(accounts.SESSION_TTL.total_seconds()) if max_age is None else max_age
            return webauth.session_cookie(token, self._https(), ttl)

        def _base_url(self) -> str:
            # a levelek linkje: soha nem a keres Host fejlecebol (azt a tamado allithatja)
            return (config.env_value("PUBLIC_URL") or f"http://127.0.0.1:{self.server.server_address[1]}").rstrip("/")

        # -- bejelentkezes ---------------------------------------------
        def _user(self) -> dict | None:
            return accounts.session_user(webauth.session_token(self))

        def _form(self) -> dict | None:
            """Az urlap mezoi; idegen oldalrol jott (Origin) vagy tul nagy keres: None."""
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 4096:
                return None
            raw = self.rfile.read(length).decode("utf-8", "replace")
            origin = self.headers.get("Origin")
            if origin and origin != "null" and urlparse(origin).netloc != self.headers.get("Host"):
                return None
            return {k: v[0] for k, v in parse_qs(raw).items()}

        def _lang(self) -> str:
            """A feluleti nyelv (az i18n.js sutije) - a levelek nyelvehez."""
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get("sw_lang")
            except CookieError:
                morsel = None
            return morsel.value if morsel and morsel.value in config.LANGUAGES else "en"

        def _post_form(self, path: str) -> None:
            form = self._form()
            page = path.lstrip("/")
            if form is None:
                return self._page(page, 400, NOTICES["origin"])
            ip = webauth.client_ip(self, trust_proxy)
            email = form.get("email", "")
            try:
                if path == "/login":
                    token = accounts.login(email, form.get("password", ""), ip, self.headers.get("User-Agent", ""))
                    if not token:
                        return self._page("login", 400, NOTICES["bad"], email=email)
                    return self._redirect("/", self._cookie(token))
                if path == "/register":
                    accounts.register(email, form.get("password", ""), form.get("terms") == "1", form.get("age") == "1",
                                      ip, self._base_url(), self._lang())
                    return self._redirect("/login?e=sent")
                if path == "/forgot":
                    accounts.request_reset(email, ip, self._base_url(), self._lang())
                    return self._redirect("/login?e=resetsent")
                if path == "/resend":
                    accounts.resend_verification(email, ip, self._base_url(), self._lang())
                    return self._redirect("/login?e=resent")
                token = form.get("token", "")
                if not accounts.reset_password(token, form.get("password", "")):
                    return self._redirect("/forgot?e=badlink")
                return self._redirect("/login?e=reset")
            except accounts.RateLimited as exc:
                return self._page(page, 429, ("error", str(exc)), email=email)
            except accounts.AccountError as exc:
                return self._page(page, 400, ("error", str(exc)), email=email, token=form.get("token", ""))

        def _google(self, path: str, query: dict) -> None:
            """/auth/google/start es /callback (#43)."""
            if not google_auth.configured():
                return self._error(404, "Nem talalhato")
            secure = "; Secure" if self._https() else ""
            if path == "/auth/google/start":
                link_user = None
                if query.get("link", [""])[0] == "1":
                    user = self._user()
                    if user is None:
                        return self._redirect("/login")
                    link_user = user["id"]
                terms = query.get("terms", [""])[0] == "1" and query.get("age", [""])[0] == "1"
                target, state = google_auth.begin(self._base_url(), link_user, terms)
                # Lax: a Google-rol visszatero (oldalak kozotti) GET is elviszi; a state a bongeszohoz kotve
                return self._redirect(target, f"{google_auth.COOKIE}={state}; Path=/auth/google; HttpOnly; "
                                              f"SameSite=Lax; Max-Age={google_auth.STATE_TTL}{secure}")
            clear = f"{google_auth.COOKIE}=; Path=/auth/google; HttpOnly; SameSite=Lax; Max-Age=0{secure}"
            if "error" in query:
                return self._redirect("/login?e=googlefail", clear)
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", "")).get(google_auth.COOKIE)
            except CookieError:
                cookie = None
            try:
                pending, claims = google_auth.finish(query.get("state", [""])[0], cookie.value if cookie else None,
                                                     query.get("code", [""])[0], self._base_url())
            except google_auth.GoogleError:
                return self._redirect("/login?e=googlefail", clear)
            outcome, token = accounts.google_login(claims["sub"], claims["email"], pending["link_user"], pending["terms"],
                                                   self.headers.get("User-Agent", ""))
            if outcome == "session":
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self._headers()
                self.send_header("Set-Cookie", clear)
                self.send_header("Set-Cookie", self._cookie(token))
                self.end_headers()
                return None
            target = {"linked": "/#/profile", "link_required": "/login?e=googlelink", "terms_required": "/register?e=googleterms",
                      "taken": "/login?e=googletaken"}[outcome]
            return self._redirect(target, clear)

        def _logout(self, everywhere: bool, user: dict) -> None:
            if everywhere:
                accounts.logout_everywhere(user["id"])
            else:
                accounts.logout(webauth.session_token(self))
            self._json({"ok": True}, cookie=self._cookie("", 0))

        # -- GET -------------------------------------------------------
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            try:
                if path == "/admin" or path.startswith(("/admin/", "/api/admin/")):
                    return self._admin_get(path, query)
                if path in AUTH_PAGES:
                    self._pageview(path)
                    return self._page(path.lstrip("/"), message=NOTICES.get(query.get("e", [""])[0]),
                                      token=query.get("token", [""])[0])
                if path in ("/auth/google/start", "/auth/google/callback"):
                    return self._google(path, query)
                if path == "/verify":
                    ok = accounts.verify_email(query.get("token", [""])[0])
                    return self._redirect("/login?e=verified" if ok else "/login?e=badlink")
                m = re.fullmatch(r"/i18n/([a-z]{2})\.json", path)
                if m:
                    from . import i18n

                    if m.group(1) not in config.LANGUAGES:
                        return self._error(404, "Nem talalhato")
                    return self._json(i18n.entries(m.group(1)))
                if path in PUBLIC_FILES or posixpath.normpath(path).startswith("/img/"):   # a kezdooldal kepei
                    return self._static(path)
                if path in AI_FILES or path in ("/robots.txt", "/sitemap.xml"):
                    return self._discovery(path)
                if path == "/about":                                   # nyilvanos: rolunk es kapcsolat
                    self._pageview(path)
                    return self._localized("about.html")
                if path in LEGAL_PAGES:
                    text = (WEB_DIR / "legal" / LEGAL_PAGES[path]).read_text(encoding="utf-8")
                    contact = config.env_value("CONTACT_EMAIL")       # egy forras: az Informacio oldal is ezt mutatja
                    if contact:
                        text = text.replace("[KAPCSOLATI E-MAIL]", html.escape(contact))
                    # az adatkezelo neve a szerver titokfajljabol (a repo nyilvanos); nelkule semleges megnevezes
                    text = text.replace("[ADATKEZELŐ NEVE]", html.escape(config.env_value("OPERATOR_NAME")
                                                                         or "a szolgáltatás üzemeltetője"))
                    self._pageview(path)
                    return self._send(200, text.encode("utf-8"), STATIC_TYPES[".html"])
                user = self._user()
                if user is None:
                    if path.startswith("/api/"):
                        return self._error(401, "Bejelentkezes szukseges")
                    if path == "/":                                    # nyilvanos kezdooldal (#39)
                        self._pageview("/")
                        return self._localized("landing.html")
                    return self._redirect("/login")
                if path == "/api/session":
                    return self._json({k: user[k] for k in ("email", "onboarded", "nickname", "has_password", "google")}
                                      | {"admin": user.get("role") == "admin", "google_available": google_auth.configured(),
                                         "contact": config.env_value("CONTACT_EMAIL")})
                if path == "/api/daily":
                    from . import daily

                    return self._json(daily.status(user["id"]))
                if path == "/api/leaderboard":
                    from . import daily

                    group = query.get("group", [""])[0]
                    try:
                        return self._json(daily.leaderboard(query.get("period", ["week"])[0], user["id"],
                                                            group=int(group) if group else None))
                    except LookupError as exc:
                        return self._error(404, str(exc))
                    except ValueError as exc:
                        return self._error(400, str(exc))
                if path == "/api/groups":
                    from . import groups

                    return self._json(groups.mine(user["id"]))
                if path == "/api/practice":
                    return self._json(practice_payload(user["id"]))
                if path == "/api/progress":
                    from . import progress

                    return self._json(progress.summary(user["id"]))
                if path == "/api/lessons":
                    from . import learning

                    return self._json(learning.overview(user["id"]))
                m = LESSON_RE.fullmatch(path)
                if m and not m.group(2):
                    from . import learning

                    try:
                        return self._json(learning.page(user["id"], int(m.group(1))))
                    except LookupError as exc:
                        return self._error(404, str(exc))
                    except ValueError as exc:
                        return self._error(403, str(exc))
                if path == "/api/account/export":
                    body = _dumps(accounts.export(user["id"]))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="stockwatch-adataim.json"')
                    self.send_header("Content-Length", str(len(body)))
                    self._headers()
                    self.end_headers()
                    self.wfile.write(body)
                    return
                m = PRACTICE_SESSION_RE.fullmatch(path)
                if m and not m.group(4):
                    from .practice import session as practice

                    try:
                        if m.group(2) == "summary":
                            return self._json(practice.summary_payload(user["id"], m.group(1)))
                        return self._json(practice.task_payload(user["id"], m.group(1), int(m.group(3))))
                    except LookupError as exc:
                        return self._error(404, str(exc))
                    except ValueError as exc:
                        return self._error(400, str(exc))
                if path in ("", "/"):
                    self._pageview("/app")
                return self._static(path)
            except db.DatabaseUnavailable:
                return self._error(503, "Az adatbazis most nem erheto el - probald ujra par masodperc mulva.")
            except Exception:
                log.exception("Hiba a keres kiszolgalasa kozben: %s", path)
                self._count_error(path)
                return self._error(500, "Szerverhiba")

        def _localized(self, name: str) -> None:
            """Nyilvanos oldal a latogato nyelven, a szerveren forditva (a JavaScriptet nem futtato robotok is igy
            latjak; valasztas nelkul angolul)."""
            from . import i18n

            text = (WEB_DIR / name).read_text(encoding="utf-8")
            text = text.replace("[KAPCSOLATI E-MAIL]", html.escape(config.env_value("CONTACT_EMAIL") or ""))
            self._send(200, i18n.translate_html(text, self._lang()).encode("utf-8"), STATIC_TYPES[".html"])

        def _discovery(self, path: str) -> None:
            """llms.txt, ai.json, ecp.json (gyoker es /.well-known), robots.txt, sitemap.xml."""
            base = self._base_url()
            if path == "/robots.txt":
                body = f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\n\nSitemap: {base}/sitemap.xml\n"
                return self._send(200, body.encode("utf-8"), "text/plain; charset=utf-8")
            if path == "/sitemap.xml":
                urls = "".join(f"<url><loc>{html.escape(base + p)}</loc></url>" for p in SITEMAP)
                body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
                return self._send(200, body.encode("utf-8"), "application/xml; charset=utf-8")
            name = AI_FILES[path]
            contact = config.env_value("CONTACT_EMAIL") or ""
            if name.endswith(".json"):
                contact = json.dumps(contact)[1:-1]
            text = (WEB_DIR / "ai" / name).read_text(encoding="utf-8").replace("{URL}", base).replace("{CONTACT}", contact)
            ctype = "application/json; charset=utf-8" if name.endswith(".json") else "text/plain; charset=utf-8"
            return self._send(200, text.encode("utf-8"), ctype)

        def _pageview(self, path: str) -> None:
            """Sutimentes oldalmegtekintes (#48): az oldal, a hivatkozo domain, az eszkoz - robot nem szamit."""
            from . import analytics

            agent = self.headers.get("User-Agent", "")
            if not analytics.is_bot(agent):
                analytics.track_later("pageview", path=path, device=analytics.device(agent),
                                      ref=analytics.ref_host(self.headers.get("Referer"), self.headers.get("Host")))

        def _count_error(self, path: str) -> None:
            from . import analytics

            analytics.track_later("server_error", path=re.sub(r"[a-z]{12}", ":id", path)[:80])

        def _not_found(self, api: bool) -> None:
            if api:
                return self._error(404, "Nem talalhato")
            self._send(404, "Nem található.".encode("utf-8"), "text/plain; charset=utf-8")

        # -- admin (#58) -----------------------------------------------
        def _admin_get(self, path: str, query: dict) -> None:
            from . import admin, analytics

            user = self._user()
            if not admin.is_admin(user):
                return self._not_found(path.startswith("/api/"))
            if path in ADMIN_FILES:
                target = WEB_DIR / "admin" / ADMIN_FILES[path]
                return self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])
            m = ADMIN_GET.fullmatch(path)
            if m is None:
                return self._not_found(True)
            if m.group(1) == "state":
                return self._json(admin.state(user))
            if not admin.verified(user):
                return self._error(403, "Kétlépcsős azonosítás szükséges.")
            admin.touch(webauth.session_token(self))
            what = m.group(1)
            if what == "overview":
                days = int(query.get("days", ["30"])[0] or 30)
                return self._json(analytics.overview(max(1, min(days, 400))))
            if what == "users":
                return self._json(admin.users(query.get("q", [""])[0]))
            if m.group(2):
                return self._json(admin.user_detail(int(m.group(2))))
            if what == "content":
                from .practice import sampler
                from .practice import store as practice_store

                return self._json({"lessons": admin.lessons_state(), "daily": admin.daily_preview(),
                                   "pool": sampler.pool_info(practice_store.load_pool()),
                                   "rebuilding": admin._POOL_LOCK.locked()})
            if what == "groups":
                return self._json(admin.groups_list())
            if what == "system":
                return self._json(admin.system())
            return self._json(admin.audit_log())

        def _admin_post(self, path: str) -> None:
            from . import admin

            user = self._user()
            if not admin.is_admin(user):
                self._discard_body()
                return self._not_found(True)
            m = ADMIN_POST.fullmatch(path)
            length = int(self.headers.get("Content-Length", "0") or 0)
            if m is None or length > 1_000_000:
                self._discard_body()
                return self._not_found(True) if m is None else self._error(413, "Tul nagy keres")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                return self._error(400, "Ervenytelen keres")
            token = webauth.session_token(self)
            what = m.group(1)
            if what == "verify":
                if not admin.verify(user, token, str(body.get("code") or "")):
                    return self._error(400, "Hibás vagy már felhasznált kód.")
                return self._json({"ok": True})
            if not admin.verified(user):
                return self._error(403, "Kétlépcsős azonosítás szükséges.")
            if what == "logout":
                with db.connection() as conn:
                    conn.execute("UPDATE sessions SET admin_at = NULL WHERE token_hash = %s", (accounts._h(token),))
                return self._json({"ok": True})
            admin.touch(token)
            if m.group(2):
                return self._json(admin.user_action(user, int(m.group(2)), m.group(3), self._base_url()))
            if m.group(4):
                return self._json(admin.group_action(user, int(m.group(4)), m.group(5)))
            if what == "lessons":
                return self._json(admin.save_lessons(user, body.get("data"), str(body.get("note") or "")))
            if what == "lessons/restore":
                return self._json(admin.restore_lessons(user, int(body.get("id") or 0)))
            if what == "daily/replace":
                return self._json(admin.replace_daily(user, str(body.get("day") or "")))
            return self._json(admin.rebuild_pool(user))

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB_DIR / name).resolve()
            # utvonal-bejaras ellen: csak az app/web mappabol szolgalunk ki; az urlap-sablon nem fajl
            if (WEB_DIR.resolve() not in target.parents or not target.is_file() or target.name == "auth.html"
                    or (WEB_DIR / "admin").resolve() in target.parents):
                return self._error(404, "Nem talalhato")
            ctype = STATIC_TYPES.get(target.suffix, "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)

        # -- POST ------------------------------------------------------
        def _discard_body(self) -> None:
            """Elutasitas elott a (kis) keres-torzs kiolvasasa: Windowson az olvasatlan adattal lezart kapcsolat
            RST-t kuld, es a kliens a valasz helyett kapcsolat-megszakadast lat."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if 0 < length <= 64_000:
                self.rfile.read(length)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in AUTH_PAGES:
                    return self._post_form(path)
                if self.headers.get("X-StockWatch") != "1":
                    self._discard_body()
                    return self._error(403, "Hianyzo X-StockWatch fejlec")
                if path.startswith("/api/admin/"):
                    return self._admin_post(path)
                if path == "/api/client-error":                         # bongeszos JS-hiba szamlalo (#48), nevtelen
                    from . import analytics

                    self._discard_body()
                    if CLIENT_ERRORS.hit(webauth.client_ip(self, trust_proxy)):
                        analytics.track_later("js_error")
                    return self._json({"ok": True})
                user = self._user()
                if user is None:
                    self._discard_body()
                    return self._error(401, "Bejelentkezes szukseges")
                length = int(self.headers.get("Content-Length", "0"))
                if length > 64_000:
                    return self._error(413, "Tul nagy keres")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    return self._error(400, "Ervenytelen keres")
                if path in ("/logout", "/api/logout-all"):
                    return self._logout(path == "/api/logout-all", user)
                if path == "/api/account/delete":
                    if not accounts.delete_account(user["id"], str(body.get("password") or "")):
                        return self._error(400, "Hibás jelszó.")
                    return self._json({"ok": True}, cookie=self._cookie("", 0))
                if path == "/api/daily/start":
                    from . import daily

                    return self._json({"session": daily.start(user["id"], load_settings())})
                if path.startswith("/api/groups/"):
                    from . import groups

                    action = path.rsplit("/", 1)[-1]
                    if action == "create":
                        return self._json(groups.create(user["id"], str(body.get("name") or "")))
                    if action == "join":
                        return self._json(groups.join(user["id"], str(body.get("code") or "")))
                    if action in ("leave", "delete"):
                        getattr(groups, action)(user["id"], int(body.get("id") or 0))
                        return self._json({"ok": True})
                    return self._error(404, "Nem talalhato")
                if path == "/api/profile/nickname":
                    from . import daily

                    return self._json({"nickname": daily.set_nickname(user["id"], str(body.get("nickname") or ""))})
                if path == "/api/account/google/unlink":
                    accounts.unlink_google(user["id"])
                    return self._json({"ok": True})
                if path == "/api/onboarded":
                    accounts.mark_onboarded(user["id"])
                    return self._json({"ok": True})
                m = PRACTICE_SESSION_RE.fullmatch(path)
                if m and m.group(4):
                    from .practice import session as practice

                    return self._json(practice.decide(user["id"], m.group(1), int(m.group(3)), body))
                m = LESSON_RE.fullmatch(path)
                if m and m.group(2):
                    from . import learning

                    return self._json({"session": learning.start(user["id"], int(m.group(1)), load_settings())})
                if path.startswith("/api/practice/"):
                    return self._json(practice_action(path.rsplit("/", 1)[-1], body, user["id"]))
                return self._error(404, "Nem talalhato")
            except LookupError as exc:
                return self._error(404, str(exc))
            except accounts.AccountError as exc:
                return self._error(400, str(exc))
            except (ValueError, TypeError, LockTimeout) as exc:
                return self._error(400, str(exc))
            except db.DatabaseUnavailable:
                return self._error(503, "Az adatbazis most nem erheto el - probald ujra par masodperc mulva.")
            except Exception:
                log.exception("POST hiba: %s", path)
                self._count_error(path)
                return self._error(500, "Szerverhiba")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          behind_proxy: bool = False) -> None:
    if not db.url():
        raise SystemExit("Nincs beallitva adatbazis (DATABASE_URL a titokfajlban) - lasd README, Adatbazis.\n"
                         "Utana: python cli.py db migrate")
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("A felulet %s cimen figyel - a halozatrol is elerheto! Titkositatlan HTTP: "
                    "a jelszo es a sutik lehallgathatok. Javasolt: 127.0.0.1 + HTTPS proxy.", host)
    if behind_proxy and not config.env_value("PUBLIC_URL"):
        log.warning("Nincs PUBLIC_URL: az ellenorzo es visszaallito levelek linkje 127.0.0.1-re mutat.")
    httpd = ThreadingHTTPServer((host, port), make_handler(trust_proxy=behind_proxy))
    url = f"http://{'127.0.0.1' if host in ('127.0.0.1', 'localhost') else host}:{port}"
    print(f"StockWatch oktatasi felulet: {url}   (leallitas: Ctrl+C)")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nLeallitva.")
    finally:
        httpd.server_close()
        db.close()
