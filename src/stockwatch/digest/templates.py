"""A reggeli osszefoglalo mondatsablonjai.

Minden sablon a forditasi katalogus kulcsa (locale/catalog.jsonl): a helyorzo pozicios ({0}, a szamoke
{1:n}), a sablonban SZAMJEGY NINCS - minden szam argumentum, es a szamitasbol jon. Tenyt kozol, nem ajanl
(a tiltolista-teszt ellenorzi mind a negy nyelven).
"""
from __future__ import annotations

TEMPLATES = {
    "header": "Reggeli összefoglaló – {0} (adat: a(z) {1}-i zárásig)",
    "regime_up": "{0}: a záróár ({1:n}) a(z) {2:n} napos átlag ({3:n}) fölé került.",
    "regime_down": "{0}: a záróár ({1:n}) a(z) {2:n} napos átlag ({3:n}) alá került.",
    "own_trend_up": "{0}: a záróár ({1:n}) a saját {2:n} napos átlaga ({3:n}) fölé került.",
    "own_trend_down": "{0}: a záróár ({1:n}) a saját {2:n} napos átlaga ({3:n}) alá került.",
    "pattern_triggered": "{0}: a(z) {1} formáció triggerszintje ({2:n}) átlépve.",
    "pattern_new": "{0}: új formáció: {1} ({2}).",
    "pattern_gone": "{0}: a(z) {1} formáció már nem látszik.",
    "near_trigger": "{0}: {1:n} ATR-re került a(z) {2} triggerétől ({3:n}).",
    "near_trigger_earnings": "{0}: {1:n} ATR-re került a(z) {2} triggerétől ({3:n}); gyorsjelentés {4:n} nap múlva.",
    "level_break": "{0}: a záróár ({1:n}) a(z) {2:n} körüli rezisztencia fölé került.",
    "level_lost": "{0}: a záróár ({1:n}) a(z) {2:n} körüli support alá került.",
    "earnings_soon": "{0}: gyorsjelentés {1:n} nap múlva ({2}).",
    "earnings_today": "{0}: gyorsjelentés ma ({1}).",
    "filing_8k": "{0}: új {1} bejelentés, tételek: {2}.",
    "filing_8k_material": "{0}: új {1} bejelentés lényeges tétellel: {2}.",
    "filing_8k_plain": "{0}: új {1} bejelentés.",
    "sentiment_neg": "{0}: a hírhangulat negatívra fordult ({1:n} napos átlag: {2:n}).",
    "sentiment_pos": "{0}: a hírhangulat pozitívra fordult ({1:n} napos átlag: {2:n}).",
    "material_news": "{0}: lényeges negatív hír: {1}.",
    "candidate_in": "{0}: bekerült a(z) {1} stratégia jelöltjei közé.",
    "candidate_out": "{0}: kikerült a(z) {1} stratégia jelöltjei közül.",
    "agent_report": "Az éjszakai kutatás elkészült: {0} – {1:n} jelölt, {2:n} kipróbált változat.",
    "holding": "{0} (saját pozíció)",
    "unchanged": "Változás nélkül: {0}.",
    "more": "További {0:n} tétel a webes felületen.",
    "first_run": "Első összefoglaló: a változásokat a következő kereskedési naptól jelzi.",
    "quiet": "Az előző összefoglaló óta nincs jelentős változás.",
    "no_symbols": "Nincs figyelt papír és Saját pozíció.",
    "disclaimer": "Nem befektetési tanács. Tényközlés és múltbeli / szimulált statisztika.",
}

# a felulet (es a katalogus) ekezetes strategianevei - a config.STRATEGY_LABELS ekezet nelkuli
STRATEGY_NAMES = {"breakout": "Kitörés", "pullback": "Visszahúzás (RSI2)", "deep": "Mély visszahúzás",
                  "etf": "ETF visszahúzás", "momentum": "Momentum (12-1)", "sandbox": "Homokozó"}
DIRECTIONS = {"bullish": "bikás", "bearish": "medvés"}
