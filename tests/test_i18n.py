"""Tobbnyelvuseg: a katalogus epsege, a soronkenti forditas, az ertesitesek nyelve, a felulet vegpontja."""
from __future__ import annotations

import json
import re

import pytest

from stockwatch import i18n, webapp
from stockwatch.alerts import Alert, AlertChannel, Dispatcher
from stockwatch.config import LANGUAGES, Settings

PH = re.compile(r"\{(\d+)(?::n)?\}")          # a kulcsban {1:n} = csak szamra illeszkedo helyorzo


def test_catalog_rows_are_complete_and_consistent():
    keys = set()
    for n, line in enumerate(i18n.CATALOG_PATH.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        assert set(row) == set(LANGUAGES), f"{n}. sor: {row}"
        key = " ".join(row["hu"].split())
        assert key not in keys, f"ismetlodo kulcs: {row['hu']!r}"
        keys.add(key)
        for lang in ("en", "de", "es"):
            assert isinstance(row[lang], str)
            if row[lang]:                                   # ures forditas: a toredek eltunik (pl. "választható")
                assert sorted(PH.findall(row["hu"])) == sorted(PH.findall(row[lang])), (lang, row)
    assert len(keys) > 1500


@pytest.mark.parametrize("lang, expected", [
    ("en", "[Pullback (RSI2)] AAPL: PULLBACK - buy limit for tomorrow"),
    ("de", "[Rücksetzer (RSI2)] AAPL: RÜCKSETZER - Kauflimit für morgen"),
    ("es", "[Retroceso (RSI2)] AAPL: RETROCESO: límite de compra para mañana"),
])
def test_structured_title_translates_each_part(lang, expected):
    assert i18n.translate("[Visszahuzas (RSI2)] AAPL: VISSZAHUZAS - veteli limit holnapra", lang) == expected


def test_body_translates_line_by_line_and_keeps_unknown_text():
    body = "Jelzes napja: 2026-10-16  |  zaro 231.50 $\n\n  - Trend: +0.40 (teljes bullish EMA-sorrend)\nvalami ismeretlen sor"
    out = i18n.translate(body, "en").split("\n")
    assert out[0] == "Signal date: 2026-10-16 | close 231.50 $"
    assert out[1] == ""
    assert out[2] == "  - Trend: +0.40 (full bullish EMA order)"         # a behuzas megmarad, a zarojeles resz fordul
    assert out[3] == "valami ismeretlen sor"
    assert i18n.translate(body, "hu") == body
    assert i18n.translate(None, "en") is None


class Recorder(AlertChannel):
    def __init__(self, name):
        self.name, self.got = name, []

    def send(self, alert):
        self.got.append(alert)
        return True


def test_dispatcher_localizes_outgoing_but_keeps_the_log_hungarian():
    settings = Settings(alerts={"language": "de", "console": False, "jsonl": False, "telegram": False,
                                "discord": False, "webhook": False})
    d = Dispatcher(settings)
    log, chat = Recorder("jsonl"), Recorder("discord")
    d.channels = [log, chat]
    d.dispatch(Alert(symbol="AAPL", title="AAPL: Bull zaszlo - KITORES", body="Nem befektetesi tanacs.",
                     forecast=None, event="triggered"))
    assert log.got[0].title == "AAPL: Bull zaszlo - KITORES" and log.got[0].body == "Nem befektetesi tanacs."
    assert chat.got[0].title == "AAPL: Bullenflagge - AUSBRUCH" and chat.got[0].body == "Keine Anlageberatung."


def test_language_setting_is_validated_and_editable():
    assert Settings(alerts={"language": "EN"}).alerts.language == "en"
    with pytest.raises(ValueError):
        Settings(alerts={"language": "fr"})
    assert webapp.EDITABLE["language"] == ("alerts", str)


def test_ui_catalog_endpoint_payload():
    en = i18n.entries("en")
    assert en["Beállítások"] == "Settings" and en["Utolsó gyertya: {0}"] == "Last bar: {0}"
    assert i18n.entries("hu") == {} and i18n.entries("xx") == {}
