"""Meres (#48): az esemenynaploban nincs IP- es e-mail-cim; torolt fioknal anonimizalva; a tolcser es a kohorsz
napra pontosan (szintetikus esemenyekbol); suti nelkuli oldalmegtekintes; 13 honapos megorzes."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from stockwatch import accounts, analytics, db

from .conftest import make_user
from .test_webapp import _req, port  # noqa: F401  (pytest fixture)

PW = "helyes-lo-elem-tuzfal"
NOW = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)                  # szerda; a het hetfon kezdodik


def _rows() -> list[tuple]:
    with db.connection() as conn:
        return conn.execute("SELECT name, subject, props::text FROM events ORDER BY id").fetchall()


def _put(name: str, subject: str | None, at: datetime, **props) -> None:
    with db.connection() as conn:
        conn.execute("INSERT INTO events (name, subject, props, at) VALUES (%s, %s, %s, %s)", (name, subject, Jsonb(props), at))


def test_events_never_store_ip_or_email(neon, outbox):
    accounts.register("anna@example.com", PW, True, True, "198.51.100.7", "https://x.example")
    analytics.track("register", 99, ip="198.51.100.7", email="anna@example.com", user_agent="Mozilla", level=1)
    rows = _rows()
    assert [r[0] for r in rows] == ["register", "register"]
    text = repr(rows)
    assert "198.51.100.7" not in text and "anna@example.com" not in text and "Mozilla" not in text
    assert rows[1][2] == '{"level": 1}'


def test_deleting_an_account_anonymizes_its_events(neon):
    uid = make_user()
    accounts.login("anna@example.com", PW, "127.0.0.1")
    analytics.track("decision", uid, kind="stop")
    assert {r[1] for r in _rows()} == {f"u{uid}"}
    assert accounts.delete_account(uid, PW)
    rows = _rows()
    subjects = {r[1] for r in rows}
    assert f"u{uid}" not in subjects and len(subjects) == 1 and next(iter(subjects)).startswith("x")
    assert [r[0] for r in rows] == ["login", "decision", "account_delete"]          # a statisztika megmarad


def test_funnel_and_cohort_retention_are_exact(neon):
    reg_week = NOW - timedelta(days=16)                                  # ket hettel korabbi het hetfoje (marc. 2.)
    for n in (1, 2, 3):
        _put("register", f"u{n}", reg_week)
    _put("verify", "u1", reg_week)
    _put("verify", "u2", reg_week)
    _put("decision", "u1", reg_week + timedelta(hours=1))
    _put("lesson_pass", "u1", reg_week + timedelta(hours=2), level=1)
    _put("decision", "u1", reg_week + timedelta(days=7))                 # 1. het
    _put("login", "u2", reg_week + timedelta(days=14))                   # 2. het
    for _ in range(5):
        _put("pageview", None, NOW - timedelta(days=3), path="/", ref="reddit.com", device="mobile")
    _put("pageview", None, NOW - timedelta(days=40), path="/")           # az idoszakon kivul
    _put("daily_start", "u1", NOW - timedelta(days=1))
    _put("js_error", None, NOW - timedelta(days=2))
    o = analytics.overview(30, now=NOW)
    assert [s["n"] for s in o["funnel"]] == [5, 3, 2, 1, 1]
    cohort = next(c for c in o["cohorts"] if c["week"] == "2026-03-02")
    assert cohort["size"] == 3 and cohort["retention"][:3] == [0.333, 0.667, 0.0]   # u1 napi kihivasa (marc. 17.) is a 2. het
    assert o["active"] == {"day": 1, "week": 2, "month": 2}             # u2 marc. 16., u1 marc. 17.
    assert o["levels"] == [{"level": 1, "started": 0, "passed": 1}]
    assert o["decisions"] == {"total": 2, "users": 1, "per_user": 2.0}
    assert o["errors"] == {"js": 1, "server": 0}
    assert o["pageviews"]["source"] == [{"key": "reddit.com", "n": 5}] and o["daily"][-1]["players"] == 1
    # napra pontosan: egy nappal korabbi "most" mellett a 3 napja tortent megtekintes meg nincs benne
    assert analytics.overview(30, now=NOW - timedelta(days=4))["funnel"][0]["n"] == 0


def test_old_events_are_pruned(neon):
    _put("login", "u1", datetime.now(timezone.utc) - timedelta(days=400))
    _put("login", "u1", datetime.now(timezone.utc) - timedelta(days=10))
    assert analytics.prune() == 1 and len(_rows()) == 1


def _wait_for(name: str, n: int = 1) -> list[tuple]:
    for _ in range(50):
        rows = [r for r in _rows() if r[0] == name]
        if len(rows) >= n:
            return rows
        time.sleep(0.05)
    return [r for r in _rows() if r[0] == name]


def test_pageviews_are_counted_without_cookie_or_identifier(neon, port):  # noqa: F811
    browser = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "Referer": "https://www.reddit.com/r/SideProject/x"}
    res = _req(port, "GET", "/", headers=browser)
    assert res.status == 200 and res.getheader("Set-Cookie") is None
    _req(port, "GET", "/login", headers={"User-Agent": "UptimeRobot/2.0"})            # robot: nem szamit
    rows = _wait_for("pageview")
    assert rows == [("pageview", None, '{"ref": "reddit.com", "path": "/", "device": "mobile"}')]
    res = _req(port, "POST", "/api/client-error", "{}", {"X-StockWatch": "1"})
    assert res.status == 200 and _wait_for("js_error")[0][1:] == (None, "{}")


def test_excluded_users_are_left_out_of_the_overview(neon):
    keep, test = make_user("anna@example.com"), make_user("teszt@example.com")
    with db.connection() as conn:
        conn.execute("UPDATE users SET stats_excluded = true WHERE id = %s", (test,))
    for uid in (keep, test):
        _put("register", f"u{uid}", NOW - timedelta(days=2))
        _put("decision", f"u{uid}", NOW - timedelta(days=1))
        _put("decision", f"u{uid}", NOW - timedelta(days=1))
    _put("pageview", None, NOW - timedelta(days=1), path="/")                    # nevtelen: marad
    o = analytics.overview(30, now=NOW)
    assert [s["n"] for s in o["funnel"]][:4] == [1, 1, 0, 1]
    assert o["decisions"] == {"total": 2, "users": 1, "per_user": 2.0} and o["active"]["week"] == 1
    assert [e["email"] for e in o["excluded"]] == ["teszt@example.com"]
