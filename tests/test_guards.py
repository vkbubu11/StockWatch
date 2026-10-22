"""Ores-tesztek (docs/FEATURES-2026-10-DESIGN.md, 1.3) es az oktatasi kiadas hatarai (#29).

  * a webes alkalmazas importzarlataban nincs broker, monitor, ertesites, labor - es ezek a modulok nincsenek is
  * nincs Alpaca-, Discord- vagy Telegram-beallitas, es nincs ilyen vegpont
  * a segedek maguk is mukodnek (ne legyen "zold, mert semmit nem ellenoriz")
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from stockwatch.config import Settings

from .guards import (BROKER_REACHING, _module_file, advice_hits, import_closure, numbers, package_modules,
                     unexplained_numbers)

ROOT = Path(__file__).resolve().parents[1]
NEW_PACKAGES = ["stockwatch.practice", "stockwatch.accounts", "stockwatch.db", "stockwatch.mailer", "stockwatch.learning"]
# a teljes StockWatch kereskedesi / ertesitesi / kutatasi reszei - az oktatasi agon nem lehetnek
REMOVED = BROKER_REACHING + ("stockwatch.alerts", "stockwatch.lab", "stockwatch.labqueue", "stockwatch.research_agent",
                             "stockwatch.digest", "stockwatch.events", "stockwatch.sentiment", "stockwatch.pnl_calendar",
                             "stockwatch.holdings", "stockwatch.backtest_page.runner")


def _new_modules() -> list[str]:
    out = []
    for package in NEW_PACKAGES:
        out += package_modules(package)
    return out


def test_new_modules_exist():
    mods = _new_modules()
    assert "stockwatch.practice.session" in mods and "stockwatch.practice.sampler" in mods


@pytest.mark.parametrize("module", _new_modules())
def test_new_module_never_reaches_the_broker(module):
    closure = import_closure(module)
    bad = sorted(closure & set(BROKER_REACHING))
    assert not bad, f"{module} tranzitivan eleri: {bad}"


def test_closure_follows_lazy_imports():
    # a webapp a gyakorlo munkamenetet fuggvenyen belul (lustan) importalja - ezt is latni kell
    assert "stockwatch.practice.session" in import_closure("stockwatch.webapp")


def test_web_app_reaches_no_trading_alerting_or_lab_module():
    closure = import_closure("stockwatch.webapp", *package_modules("stockwatch.practice"), "stockwatch.housekeeping")
    assert not closure & set(REMOVED)
    assert [m for m in REMOVED if _module_file(m) is not None] == []


def test_no_broker_or_messenger_settings_or_endpoints():
    assert not {"alerts", "broker", "lab", "sentiment", "digest"} & set(Settings.model_fields)
    files = ["src/stockwatch/webapp.py", "src/stockwatch/config.py", "cli.py", "config/settings.yaml", ".env.example",
             "app/web/index.html", "app/web/app.js", "app/web/auth.html"]
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"alpaca|discord|telegram|webhook", text, re.IGNORECASE), name
        assert not re.search(r"/api/(broker|holdings|positions|lab|agent|digest|events|calendar|backtest)", text), name


def test_advice_blocklist_catches_recommendations_but_not_facts():
    assert advice_hits("VETELI LIMIT holnapra")
    assert advice_hits("Vegyél belőle, érdemes megvenni")
    assert advice_hits("You should buy this") and advice_hits("Kauflimit für morgen") and advice_hits("comprar ahora")
    assert not advice_hits("AAPL: 0.8 ATR-re került a(z) Dupla alj triggerétől.")
    assert not advice_hits("Not financial advice. Facts and past / simulated statistics.")


def test_number_helpers():
    assert numbers("a 200 napos átlag (123.45) fölé, +0.8 ATR") == ["200", "123.45", "0.8"]
    assert numbers("RSI2 és Nasdaq-100") == ["100"]
    assert unexplained_numbers("Nasdaq-100 (QQQ): 412.10", ["Nasdaq-100 (QQQ)"], {"412.10"}) == []
    assert unexplained_numbers("AAPL: 7 nap", ["AAPL"], {"3"}) == ["7"]
