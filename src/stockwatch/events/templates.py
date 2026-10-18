"""Az esemenytanulmany mondatsablonjai (i18n katalogus kulcsai; szamjegy a sablonban nincs, minden szam
argumentum a szamitasbol). Tenyt es multbeli statisztikat kozol, nem ajanl."""
from __future__ import annotations

TEMPLATES = {
    "counts": "{0:n} esemény {1:n} papíron; felhasznált {2:n}, átfedés miatt kimaradt {3:n}, nem teljes ablak {4:n}.",
    "car": "Kumulált abnormális hozam ({0:n}. – {1:n}. nap): átlag {2:n}% ({7:n}%-os sáv: {3:n}% – {4:n}%), medián {5:n}%; az esetek {6:n}%-ában pozitív.",
    "post": "Az esemény napjának zárója után ({0:n}. – {1:n}. nap): átlag {2:n}% ({7:n}%-os sáv: {3:n}% – {4:n}%), medián {5:n}%; az esetek {6:n}%-ában pozitív.",
    "no_events": "Nem volt kiértékelhető esemény.",
    "few_events": "Kevés esemény ({0:n}): az eredmény főleg zaj.",
    "date_only": "{0:n} eseménynél csak a dátum ismert: ezek a következő kereskedési naptól számítanak.",
    "adjusted": "{0:n} eseménynél kevés volt a becslési adat: piac-korrigált hozam (piaci modell nélkül).",
    "pre_event": "Az esemény napja előtti szakasz leíró jellegű: az esemény akkor még nem volt ismert.",
    "survivor": "Csak a mai indextagok szerepelnek: a csődbe ment és kiesett papírok hiányoznak, ez torzíthat.",
    "sp500_effective": "A(z) {0} változásoknál a hatályos dátum számít; a bejelentési hatás (jellemzően néhány nappal korábban) nincs benne.",
    "holdout_hidden": "A zárolt holdout ({0}-tól) kimaradt: az adat előtte véget ér.",
    "holdout_open": "A tanulmány a zárolt holdoutot is tartalmazza (naplózva).",
    "no_form4": "Nincs {0} adat a vizsgált időszakra – letöltés: {1}",
    "no_changes": "Nincs {0} változás-tábla – letöltés: {1}",
    "disclaimer": "Nem befektetési tanács. Múltbeli statisztika, a jövőbeli eredményt nem garantálja.",
}

GROUP_LABELS = {"direction": "Irány", "sector": "Szektor", "regime": "Piaci rezsim", "year": "Év"}
VALUE_LABELS = {"up": "felfelé", "down": "lefelé", "flat": "semleges", "bull": "emelkedő piac", "bear": "eső piac",
                "unknown": "nem ismert"}
