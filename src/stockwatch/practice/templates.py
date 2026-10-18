"""A gyakorlo kor osszegzesenek mondatsablonjai (i18n katalogus kulcsai; szamjegy a sablonban nincs, minden szam
argumentum a szamitasbol). Tenyt es szimulacios eredmenyt kozol, nem ajanl."""
from __future__ import annotations

TEMPLATES = {
    "rounds": "{0:n} helyzet, ebből {1:n} belépési döntés és {2:n} kihagyás.",
    "unfilled": "{0:n} limitáras belépés nem teljesült.",
    "result": "A teljesült belépések: {0:n} kötés, nyerési arány {1:n}%, átlag {2:n} R, összesen {3:n} R.",
    "no_entries": "Nem volt teljesült belépés.",
    "baseline": "Véletlen alapvonal ugyanazokon a papírokon (azonos stop-távolság és időkorlát, véletlen napokon): átlag {0:n} R.",
    "reference": "Referencia-kötés minden helyzetben (következő nyitás, {0:n} ATR stop, {1:n} nap): átlag {2:n} R.",
    "strategy": "{0}: {1:n} helyzetben jelzett; a szabály szerinti lezárt kötések ({2:n}) átlaga {3:n} R.",
    "strategy_none": "{0}: {1:n} helyzetben jelzett, de lezárt szabály szerinti kötés nem volt (nem teljesült limit, szűrő, vagy a labor még nem értékelte).",
    "missed": "{0:n} kihagyott helyzetben a szabály vagy a referencia-kötés legalább +{1:n} R lett volna.",
    "stop_median": "A stop-távolság mediánja {0:n} ATR; a stopok {1:n}%-a {2:n} ATR-nél szorosabb.",
    "few": "Kevés kötés ({0:n}): az eredmény főleg zaj.",
    "disclaimer": "Nem befektetési tanács. Gyakorlás múltbeli adaton, szimulált kötésekkel.",
}

STRATEGY_LABELS = {"pullback": "Visszahúzás (RSI2)", "deep": "Mély visszahúzás", "breakout": "Kitörés"}
