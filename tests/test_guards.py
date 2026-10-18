"""Az uj funkciok kozos ores-tesztjei (docs/FEATURES-2026-10-DESIGN.md, 1.3).

  * az uj modulok (es amit tranzitivan importalnak) nem erik el a broker modult
  * a segedek maguk is mukodnek (ne legyen "zold, mert semmit nem ellenoriz")
"""
from __future__ import annotations

import pytest

from .guards import (BROKER_REACHING, advice_hits, import_closure, numbers, package_modules,
                    unexplained_numbers)

# Az uj csomagok; amelyik meg nem letezik, kimarad (a kesobbi fazisok bovitik a listat).
NEW_PACKAGES = ["stockwatch.workspace", "stockwatch.usage", "stockwatch.digest", "stockwatch.research_agent",
                "stockwatch.labqueue", "stockwatch.sandbox_adopt", "stockwatch.events", "stockwatch.practice"]


def _new_modules() -> list[str]:
    out = []
    for package in NEW_PACKAGES:
        out += package_modules(package)
    return out


def test_new_modules_exist():
    mods = _new_modules()
    assert "stockwatch.digest.detect" in mods and "stockwatch.digest.service" in mods


@pytest.mark.parametrize("module", _new_modules())
def test_new_module_never_reaches_the_broker(module):
    closure = import_closure(module)
    bad = sorted(closure & set(BROKER_REACHING))
    assert not bad, f"{module} tranzitivan eleri: {bad}"


def test_closure_follows_lazy_imports():
    # a monitor a brokert kozvetlenul, a replay fuggvenyen belul (lustan) importalja - mindkettot latni kell
    assert "stockwatch.broker" in import_closure("stockwatch.monitor")
    assert "stockwatch.broker" in import_closure("stockwatch.replay")


def test_meanrev_no_longer_reaches_the_broker_through_news():
    # korabban: meanrev -> sentiment.service -> sources.alpaca -> broker (lusta import)
    assert "stockwatch.sentiment.sources.alpaca" in import_closure("stockwatch.meanrev")
    assert "stockwatch.broker" not in import_closure("stockwatch.meanrev")


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
