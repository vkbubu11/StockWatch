"""Admin felulet (#58): nem admin felhasznalo semmilyen /admin vegpontot nem er el (404); TOTP nelkul admin sem;
minden admin muvelet az audit-naploba kerul (csak hozzafuzheto); a leckeszerkeszto ervenytelent nem ment, a korabbi
verzio visszaallithato; jelszo, munkamenet-token es Google-azonosito a valaszokban nincs."""
from __future__ import annotations

import copy
import json
import time

import psycopg
import pytest

from stockwatch import accounts, admin, db, learning, webauth

from .conftest import make_user
from .test_webapp import _req, port  # noqa: F401  (pytest fixture)

PW = "helyes-lo-elem-tuzfal"
GET_PATHS = ["/admin", "/admin/", "/admin/admin.js", "/admin/index.html", "/api/admin/state", "/api/admin/overview",
             "/api/admin/users", "/api/admin/users/1", "/api/admin/content", "/api/admin/groups", "/api/admin/system",
             "/api/admin/audit", "/api/admin/nincs"]
POST_PATHS = ["/api/admin/verify", "/api/admin/logout", "/api/admin/users/1/suspend", "/api/admin/users/1/delete",
              "/api/admin/users/1/export", "/api/admin/lessons", "/api/admin/lessons/restore", "/api/admin/daily/replace",
              "/api/admin/pool/rebuild", "/api/admin/groups/1/delete", "/api/admin/nincs"]


def _cookie(email: str) -> dict:
    return {"Cookie": f"{webauth.COOKIE}={accounts.login(email, PW, '127.0.0.1')}"}


def _post(port, path, body, headers):  # noqa: F811
    return _req(port, "POST", path, json.dumps(body), {"X-StockWatch": "1", "Content-Type": "application/json", **headers})


def _admin(port, email="root@example.com", verify=True) -> tuple[int, dict]:  # noqa: F811
    uid = make_user(email)
    admin.grant(email)
    headers = _cookie(email)
    state = json.loads(_req(port, "GET", "/api/admin/state", headers=headers).text)
    if verify:
        code = admin.totp(state["secret"], int(time.time() // admin.TOTP_STEP))
        assert _post(port, "/api/admin/verify", {"code": code}, headers).status == 200
    return uid, headers


@pytest.mark.parametrize("who", ["anonymous", "user"])
def test_non_admins_get_404_on_every_admin_endpoint(neon, port, who):  # noqa: F811
    headers = {}
    if who == "user":
        make_user()
        headers = _cookie("anna@example.com")
    for path in GET_PATHS:
        assert _req(port, "GET", path, headers=headers).status == 404, path
    for path in POST_PATHS:
        assert _post(port, path, {"code": "123456", "id": 1, "day": "2026-01-01", "data": []}, headers).status == 404, path
    assert _req(port, "GET", "/admin.js", headers=headers).status in (303, 404)        # nincs kerulout


def test_admin_needs_a_one_time_totp_code(neon, port):  # noqa: F811
    _, headers = _admin(port, verify=False)
    state = json.loads(_req(port, "GET", "/api/admin/state", headers=headers).text)
    assert state["enrolled"] is False and state["uri"].startswith("otpauth://totp/") and not state["verified"]
    assert _req(port, "GET", "/api/admin/overview", headers=headers).status == 403
    assert _post(port, "/api/admin/users/1/suspend", {}, headers).status == 403
    assert _post(port, "/api/admin/verify", {"code": "000000"}, headers).status == 400
    code = admin.totp(state["secret"], int(time.time() // admin.TOTP_STEP))
    assert _post(port, "/api/admin/verify", {"code": code}, headers).status == 200
    assert _req(port, "GET", "/api/admin/overview", headers=headers).status == 200
    again = json.loads(_req(port, "GET", "/api/admin/state", headers=headers).text)
    assert again["enrolled"] and "secret" not in again                     # a titok tobbe nem latszik
    assert _post(port, "/api/admin/verify", {"code": code}, headers).status == 400       # egy kod csak egyszer
    assert _post(port, "/api/admin/logout", {}, headers).status == 200
    assert _req(port, "GET", "/api/admin/overview", headers=headers).status == 403


def test_totp_matches_the_rfc_test_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                       # "12345678901234567890" (RFC 6238, SHA1)
    assert admin.totp(secret, 59 // 30) == "287082" and admin.totp(secret, 1111111109 // 30) == "081804"


def _audit_actions() -> list[str]:
    return [r["action"] for r in admin.audit_log()]


def test_admin_actions_are_audited_and_the_log_is_append_only(neon, port, outbox):  # noqa: F811
    root, headers = _admin(port)
    other = make_user("bela@example.com", verified=False)
    target = make_user("cili@example.com")
    with db.connection() as conn:
        conn.execute("UPDATE users SET nickname = 'csunya nev' WHERE id = %s", (target,))
    for action in ("resend", "suspend"):
        assert _post(port, f"/api/admin/users/{other if action == 'resend' else target}/{action}", {}, headers).status == 200
    assert "/verify?token=" in outbox[-1][2] and outbox[-1][0] == "bela@example.com"
    with pytest.raises(accounts.AccountError):                          # felfuggesztve: nem lep be
        accounts.login("cili@example.com", PW, "127.0.0.1")
    for action in ("unsuspend", "reset_nickname", "exclude", "export"):
        assert _post(port, f"/api/admin/users/{target}/{action}", {}, headers).status == 200
    detail = json.loads(_req(port, "GET", f"/api/admin/users/{target}", headers=headers).text)
    assert detail["nickname"] is None and detail["suspended_at"] is None and detail["stats_excluded"]
    assert _post(port, f"/api/admin/users/{root}/delete", {}, headers).status == 400     # sajat magat nem
    assert _post(port, "/api/admin/daily/replace", {"day": "2026-11-02"}, headers).status == 200
    assert _post(port, f"/api/admin/users/{other}/delete", {}, headers).status == 200
    assert json.loads(_req(port, "GET", f"/api/admin/users/{other}", headers=headers).text)["error"]
    assert _audit_actions() == ["user_delete", "daily_replace", "user_export", "user_exclude", "user_reset_nickname", "user_unsuspend",
                                "user_suspend", "user_resend", "admin_login"]
    with db.connection() as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE admin_audit SET action = 'x'")
    with db.connection() as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM admin_audit")


def test_lesson_editor_rejects_invalid_lessons_and_restores_versions(neon, port):  # noqa: F811
    _, headers = _admin(port)
    try:
        original = json.loads(_req(port, "GET", "/api/admin/content", headers=headers).text)["lessons"]["data"]
        broken = copy.deepcopy(original)
        del broken[2]                                                    # hianyzo szint
        assert _post(port, "/api/admin/lessons", {"data": broken}, headers).status == 400
        bad_filter = copy.deepcopy(original)
        bad_filter[0]["tasks"]["only"] = {"nincs": None}
        assert _post(port, "/api/admin/lessons", {"data": bad_filter}, headers).status == 400
        assert _post(port, "/api/admin/lessons", {"data": original, "note": "kiindulas"}, headers).status == 200
        changed = copy.deepcopy(original)
        changed[0]["title"] = "Gyertyák, trend és lendület"
        assert _post(port, "/api/admin/lessons", {"data": changed}, headers).status == 200
        assert learning.lessons()[0]["title"] == "Gyertyák, trend és lendület"
        versions = json.loads(_req(port, "GET", "/api/admin/content", headers=headers).text)["lessons"]["versions"]
        first = versions[-1]["id"]
        assert _post(port, "/api/admin/lessons/restore", {"id": first}, headers).status == 200
        assert learning.lessons()[0]["title"] == original[0]["title"]
        assert _audit_actions()[:3] == ["lessons_save", "lessons_save", "lessons_save"]
    finally:
        learning.lessons.cache_clear()


def test_admin_responses_contain_no_secrets(neon, port):  # noqa: F811
    _, headers = _admin(port)
    uid = make_user("dora@example.com")
    with db.connection() as conn:
        conn.execute("INSERT INTO user_identities (provider, subject, user_id, email) VALUES ('google', 'g-sub-123456', %s, "
                     "'dora@example.com')", (uid,))
        pw_hash = conn.execute("SELECT password_hash FROM users WHERE id = %s", (uid,)).fetchone()[0]
    token = accounts.login("dora@example.com", PW, "127.0.0.1")
    texts = [_req(port, "GET", p, headers=headers).text for p in
             ("/api/admin/users?q=dora", f"/api/admin/users/{uid}", "/api/admin/audit", "/api/admin/system")]
    texts.append(_post(port, f"/api/admin/users/{uid}/export", {}, headers).text)
    for text in texts:
        assert "g-sub-123456" not in text and pw_hash not in text and token not in text
        assert accounts._h(token).hex() not in text
