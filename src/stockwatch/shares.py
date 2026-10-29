"""Megosztható eredménykártya (#57): a napi kihívás eredménye vagy egy teljesített szint - kártya (PNG) és
megosztható oldal linkelőnézettel.

  * csak a felhasználó kifejezett megosztására jön létre; az adat a megosztás pillanatában rögzül (becenév vagy
    „Játékos #...”, eredmény, jelölések); e-mail-cím soha; visszavonható (utána a link és a kép 404)
  * a kártyán nincs chart és nincs árfolyam (adatlicenc), nincs profit / hozam; a figyelmeztetés a képbe rajzolva
  * a napi kihívásnál helyzetenként csak egy jelölés (✓ ≈ ↺ ✗) - a helyzetek és a válaszok nem, hogy a címzett is
    kitölthesse; a megosztott oldal „Próbáld ki a mai kihívást” gombja a regisztrációra visz (forrás: share)
"""
from __future__ import annotations

import io
from pathlib import Path

from psycopg.types.json import Jsonb

from . import analytics, db, i18n
from .practice import store

MARK = {"good": "✓", "unlucky": "↺", "lucky": "≈", "bad": "✗"}
GOOD = ("good", "unlucky")                          # a szabaly szerint indokolt dontes (mint a tanulasi utnal)
DISCLAIMER = "Oktatási célú gyakorlás, nem befektetési tanács."
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf", "C:/Windows/Fonts/segoeui{bw}.ttf",
         "/Library/Fonts/Arial{sp}.ttf")
SYMBOL_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/seguisym.ttf")   # ✓ ↺ ≈ ✗
MARK_COLOR = {"✓": "#8fe3c0", "↺": "#cfe9df", "≈": "#f2d48f", "✗": "#ff9a8a"}


def _player(conn, user_id: int) -> str:
    nick, tag = conn.execute("SELECT nickname, substr(md5(id::text), 1, 5) FROM users WHERE id = %s", (user_id,)).fetchone()
    return nick or f"Játékos #{tag}"


def create(user_id: int, kind: str, ref: str, lang: str, group: int | None = None) -> dict:
    """Uj (vagy a meglevo, vissza nem vont) megosztas a felhasznalo sajat eredmenyerol."""
    from . import learning
    from .practice import session

    if kind == "daily":
        sid = store.daily_session(user_id, ref)
        if sid is None:
            raise LookupError("Ezen a napon nem játszottál napi kihívást.")
        summary = session.summary_payload(user_id, sid)
        if summary.get("hidden"):
            raise ValueError("A napi kihívás eredménye a beadás után osztható meg.")
        verdicts = [t.get("verdict") or (None if t.get("quality") is None else "good" if t["quality"] >= 0.6 else "bad")
                    for t in summary["tasks"]]
        data = {"good": sum(v in GOOD for v in verdicts), "total": len(verdicts),
                "points": sum(t.get("points") or 0 for t in summary["tasks"]), "marks": [MARK.get(v, "·") for v in verdicts]}
    elif kind == "level":
        level = int(ref)
        state = learning.progress(user_id).get(level)
        if not state or not state["completed"]:
            raise ValueError("Csak teljesített szint osztható meg.")
        data = {"level": level, "title": learning.lesson(level)["title"], "best": state["best_share"]}
    else:
        raise ValueError("Ismeretlen megosztás.")
    with db.connection() as conn:
        data["player"] = _player(conn, user_id)
        if group:
            row = conn.execute("SELECT g.name, g.code FROM learning_groups g JOIN group_members m ON m.group_id = g.id "
                               "WHERE g.id = %s AND m.user_id = %s", (group, user_id)).fetchone()
            if row is None:
                raise LookupError("Nincs ilyen csoport.")
            data["group"] = {"name": row[0], "code": row[1]}
        old = conn.execute("SELECT id, revoked_at FROM shares WHERE user_id = %s AND kind = %s AND ref = %s",
                           (user_id, kind, ref)).fetchone()
        if old and old[1] is None:
            conn.execute("UPDATE shares SET data = %s, lang = %s WHERE id = %s", (Jsonb(data), lang, old[0]))
            return {"id": old[0]}
        if old:                                                    # visszavont: uj azonosito, a regi link halott marad
            conn.execute("DELETE FROM shares WHERE id = %s", (old[0],))
        sid = store.new_id()
        conn.execute("INSERT INTO shares (id, user_id, kind, ref, data, lang) VALUES (%s, %s, %s, %s, %s, %s)",
                     (sid, user_id, kind, ref, Jsonb(data), lang))
        analytics.track("share_create", user_id, conn, kind=kind)
    return {"id": sid}


def get(share_id: str) -> dict | None:
    if not store.SESSION_ID_RE.match(share_id or ""):
        return None
    with db.connection() as conn:
        row = conn.execute("SELECT id, kind, ref, data, lang, created_at FROM shares WHERE id = %s AND revoked_at IS NULL",
                           (share_id,)).fetchone()
    return dict(zip(("id", "kind", "ref", "data", "lang", "created_at"), row)) if row else None


def revoke(user_id: int, share_id: str) -> bool:
    with db.connection() as conn:
        return conn.execute("UPDATE shares SET revoked_at = now() WHERE id = %s AND user_id = %s AND revoked_at IS NULL",
                            (share_id, user_id)).rowcount > 0


def mine(user_id: int) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("SELECT id, kind, ref, created_at FROM shares WHERE user_id = %s AND revoked_at IS NULL "
                            "ORDER BY created_at DESC LIMIT 50", (user_id,)).fetchall()
    return [dict(zip(("id", "kind", "ref", "created_at"), r)) for r in rows]


def lines(share: dict, lang: str) -> dict:
    """A kartya szovegei a megadott nyelven (a kep es a megosztott oldal ugyanebbol)."""
    d = share["data"]
    tr = lambda s: i18n.translate(s, lang) if lang != "hu" else s           # noqa: E731
    if share["kind"] == "daily":
        title = f"{tr('Napi kihívás')} · {share['ref']}"
        headline = tr(f"{d['good']} / {d['total']} jó döntés")
        sub = tr(f"+{d['points']} pont")
        marks = " ".join(d["marks"])
    elif share["kind"] == "cert":                                          # vizsga-tanusitvany (#37)
        title = tr("Záróvizsga: teljesítve")
        headline = tr(f"{round(d['score'] * 100)}% jó döntés")
        sub = tr(f"{d['levels']} / {d['levels_total']} szint · {d['date']}")
        marks = ""
    else:
        title = tr(f"{d['level']}. szint: teljesítve")
        headline = tr(d["title"])
        sub = tr(f"legjobb: {round((d['best'] or 0) * 100)}%")
        marks = ""
    return {"brand": tr("StockWatch Gyakorlás"), "player": d["player"] if not d["player"].startswith("Játékos #")
            else tr(d["player"]), "title": title, "headline": headline, "sub": sub, "marks": marks,
            "code": tr(f"Ellenőrző kód: {share['ref']}") if share["kind"] == "cert" else None,
            "disclaimer": tr(d.get("disclaimer") or DISCLAIMER)}


def _font(size: int, bold: bool = False, paths: tuple = FONTS):
    from PIL import ImageFont

    for pattern in paths:
        path = pattern.format(b="-Bold" if bold else "", bw="b" if bold else "", sp=" Bold" if bold else "")
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def png(share: dict, lang: str) -> bytes:
    """1200 x 630-as kartya (a linkelonezetek szabvanyos merete): csak szoveg es jelolesek, chart es arfolyam nelkul."""
    from PIL import Image, ImageDraw

    t = lines(share, lang)
    img = Image.new("RGB", (1200, 630), "#0d2620")
    draw = ImageDraw.Draw(img)
    for y in range(630):                                            # finom atmenet a hatterben
        shade = int(13 + 10 * y / 630)
        draw.line([(0, y), (1200, y)], fill=(shade, 38 + shade // 2, 32))
    draw.rounded_rectangle([60, 60, 120, 120], 14, fill="#1f6f5c")
    draw.line([(72, 104), (87, 87), (99, 97), (112, 74)], fill="#ffffff", width=6, joint="curve")
    draw.text((140, 70), t["brand"], font=_font(34, True), fill="#8fe3c0")
    draw.text((60, 170), t["player"], font=_font(40), fill="#ffffff")
    draw.text((60, 230), t["title"], font=_font(34), fill="#cfe9df")
    size = 76
    while size > 40 and draw.textlength(t["headline"], font=_font(size, True)) > 1080:   # hosszu leckecim
        size -= 6
    draw.text((60, 300 + (76 - size) // 2), t["headline"], font=_font(size, True), fill="#ffffff")
    x, sym = 60, _font(54, True, SYMBOL_FONTS)
    for mark in t["marks"].split():
        draw.text((x, 408), mark, font=sym, fill=MARK_COLOR.get(mark, "#cfe9df"))
        x += 70
    draw.text((x + 30 if x > 60 else 60, 418), t["sub"], font=_font(38), fill="#cfe9df")
    if t["code"]:
        draw.text((60, 490), t["code"], font=_font(30), fill="#cfe9df")
    draw.text((60, 560), t["disclaimer"], font=_font(26), fill="#a9c7bb")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
