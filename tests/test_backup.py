"""Napi adatbazis-mentes (backup.py): mentes -> uritett adatbazis -> visszaallitas pontosan ugyanazt adja; a
visszaallitas csak ures adatbazisba es azonos migracios allapotra megy; megorzes 14 nap + lemezplafon; sikertelen
mentes nem torol regit."""
from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta

import pytest

from stockwatch import accounts, backup, db, groups

from .conftest import make_user

DAY = date(2026, 11, 1)


def _snapshot() -> dict[str, list]:
    with db.connection() as conn:
        return {t: sorted(conn.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr) for t in backup._tables(conn)}


def _wipe() -> None:
    with db.connection() as conn:
        tables = ", ".join(f'"{t}"' for t in backup._tables(conn))
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


@pytest.fixture
def filled(neon):
    anna = make_user()
    bela = make_user("bela@example.com", verified=False)
    accounts.login("anna@example.com", "helyes-lo-elem-tuzfal", "127.0.0.1")            # munkamenet-sor
    group = groups.create(anna, "Kurzus")
    with db.connection() as conn:
        # nehez szoveg: tabulator, sortores, visszaper, ekezet - a COPY-formatum escape-eli
        conn.execute("UPDATE users SET nickname = %s WHERE id = %s", ("Anna\tő\\x", anna))
        conn.execute("UPDATE learning_groups SET name = %s WHERE id = %s", ("Kurzus\n2. félév", group["id"]))
        conn.execute("INSERT INTO lesson_progress (user_id, level, best_share, completed_at) VALUES (%s, 1, 0.8, now())", (anna,))
    return {"anna": anna, "bela": bela}


def test_backup_then_restore_gives_back_the_same_data(filled, tmp_path):
    before = _snapshot()
    result = backup.backup(tmp_path, DAY)
    assert result["path"] == tmp_path / "learn-2026-11-01.zip" and result["rows"]["users"] == 2
    assert not list(tmp_path.glob("*.tmp"))
    with zipfile.ZipFile(result["path"]) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    names = [t["name"] for t in manifest["tables"]]
    assert names.index("users") < names.index("sessions") < len(names)            # a hivatkozott tabla elobb
    assert "schema_migrations" not in names and manifest["migrations"]

    _wipe()
    loaded = backup.restore(result["path"])
    assert loaded["users"] == 2 and _snapshot() == before
    # az azonosito-szamlalo a visszaallitott legnagyobb utan folytatodik
    assert make_user("cili@example.com") == max(filled.values()) + 1


def test_restore_only_into_an_empty_database(filled, tmp_path):
    path = backup.backup(tmp_path, DAY)["path"]
    before = _snapshot()
    with pytest.raises(ValueError, match="ures adatbazis"):
        backup.restore(path)
    assert _snapshot() == before


def test_restore_refuses_another_migration_state(filled, tmp_path):
    path = backup.backup(tmp_path, DAY)["path"]
    other = tmp_path / "masik.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(other, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["migrations"] = manifest["migrations"][:-1]
                data = json.dumps(manifest).encode()
            dst.writestr(item, data)
    _wipe()
    with pytest.raises(ValueError, match="migracios allapota"):
        backup.restore(other)
    assert not any(_snapshot().values())


def _touch(directory, day: date, size: int = 10):
    path = directory / f"learn-{day.isoformat()}.zip"
    path.write_bytes(b"x" * size)
    return path


def test_prune_keeps_two_weeks_and_respects_the_size_limit(tmp_path, monkeypatch):
    days = [DAY - timedelta(days=n) for n in range(0, 40)]
    for day in days:
        _touch(tmp_path, day)
    other = tmp_path / "jegyzet.txt"
    other.write_text("marad")
    removed = backup.prune(tmp_path, DAY)
    left = sorted(p.name for p in tmp_path.glob("learn-*.zip"))
    assert len(left) == backup.KEEP_DAYS and left[0] == f"learn-{(DAY - timedelta(days=backup.KEEP_DAYS - 1)).isoformat()}.zip"
    assert len(removed) == 40 - backup.KEEP_DAYS and other.exists()
    assert backup.KEEP_DAYS < 30                                                       # az adatkezelesi tajekoztato igerete

    monkeypatch.setattr(backup, "LIMIT_MB", 25 / (1024 * 1024))                       # 25 bajt: 2 fajl fer el
    backup.prune(tmp_path, DAY)
    assert sorted(p.name for p in tmp_path.glob("learn-*.zip")) == [f"learn-{(DAY - timedelta(days=1)).isoformat()}.zip",
                                                                      f"learn-{DAY.isoformat()}.zip"]
    monkeypatch.setattr(backup, "LIMIT_MB", 0)                                         # a legujabb mindig megmarad
    backup.prune(tmp_path, DAY)
    assert [p.name for p in tmp_path.glob("learn-*.zip")] == [f"learn-{DAY.isoformat()}.zip"]


def test_a_failed_backup_deletes_nothing(tmp_path, monkeypatch):
    old = _touch(tmp_path, DAY - timedelta(days=40))                                  # a takaritas csak siker utan jon

    def down():
        raise db.DatabaseUnavailable("Az adatbazis nem erheto el.")

    monkeypatch.setattr(backup.db, "connection", down)
    with pytest.raises(db.DatabaseUnavailable):
        backup.backup(tmp_path, DAY)
    assert old.exists() and not list(tmp_path.glob("*.tmp")) and not (tmp_path / f"learn-{DAY}.zip").exists()
