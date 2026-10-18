"""A kutatougynok jelentesenek mondatsablonjai (i18n katalogus kulcsai).

Pozicios helyorzok ({0}, a szamoke {1:n}); a sablonban szamjegy nincs, minden szam argumentum a
szamitasbol. Tenyt es multbeli / szimulalt statisztikat kozol, nem ajanl.
"""
from __future__ import annotations

TEMPLATES = {
    "summary": "{0:n} változatot próbált ki ({1:n} érvénytelen); a kiválasztás csak a tanító időszakon történt ({2} előtt).",
    "validation_once": "A validációs időszakot ({0} – {1}) csak a(z) {2:n} döntősnél nézte meg, egyszer.",
    "holdout": "A zárolt holdout ({0}-tól) érintetlen: az adat előtte véget ér, és a holdout-napló nem változott.",
    "stopped_variants": "A keresés a változat-keretnél ({0:n}) állt meg.",
    "stopped_cpu": "A keresés a CPU-keretnél ({0:n} perc) állt meg; a jelentés az addigi legjobb eredményből készült.",
    "stopped_deadline": "A keresés a reggeli határidőnél állt meg; a jelentés az addigi legjobb eredményből készült.",
    "skipped": "A keret miatt kimaradt lépések: {0}.",
    "no_feasible": "Egyik változat sem teljesítette a brief összes feltételét; a jelöltek a nem teljesült feltételekkel együtt látszanak.",
    "no_result": "Nem volt értékelhető változat (nincs adat vagy kötés).",
    "warn_many": "Sok változatot próbált ki: a legjobb eredmény részben szerencse lehet (többszörös tesztelés).",
    "warn_survivor": "Csak a mai indextagok szerepelnek: a csődbe ment és kiesett papírok hiányoznak, ez felfelé torzít.",
    "warn_looks": "Ez a motorcsalád eddig {0:n} futásban nézte meg a validációs időszakot: sok megnézés után az sem független.",
    "change_pct": "{0}: {1} (alap: {2}) – a tanító időszakban {3:n} százalékpont nyerési arányt és {4:n} százalékpont átlagos nettó hozamot adott a változtatás nélküli beállításhoz képest.",
    "change_r": "{0}: {1} (alap: {2}) – a tanító időszakban {3:n} százalékpont nyerési arányt és {4:n} R átlagos eredményt adott a változtatás nélküli beállításhoz képest.",
    "change_plain": "{0}: {1} (alap: {2}).",
    "change_none": "A tanító időszak eredményén nem változtatott: {0}.",
    "neighbours": "A(z) {0:n} szomszédos beállításból {1:n} teljesíti a feltételeket; a nyerési arány {2:n}% és {3:n}% között maradt.",
    "neighbours_none": "Szomszédos beállítás nem volt kiértékelhető.",
    "cost2x_pct": "Kétszeres költséggel a holdout előtti időszakban: nyerési arány {0:n}%, átlagos nettó hozam {1:n}%.",
    "cost2x_r": "Kétszeres költséggel a holdout előtti időszakban: nyerési arány {0:n}%, átlagos eredmény {1:n} R.",
    "years": "{0:n} évből {1:n} évben volt pozitív az átlagos eredmény.",
    "gate": "Az előre rögzített elfogadási feltételből {0:n} / {1:n} teljesül.",
    "violations": "Nem teljesül: {0}.",
    "preset_same": "Ez maga a kiinduló beállítás, változtatás nélkül.",
    "log_rung": "{0:n}. fok: {1:n} változat a papírok {2:n}%-án ({3:n} papír); {4:n} ment tovább.",
    "log_better": "Finomítás: {0} {1} → {2}: a pontszám {3} → {4} (javított).",
    "log_worse": "Finomítás: {0} {1} → {2}: a pontszám {3} → {4} (nem javított).",
    "log_step": "Új legjobb változat: {0}.",
    "log_rejected": "Elutasítva ({0}): {1}.",
    "log_validated": "{0:n}. döntős: a validációs időszak megnézve.",
    "disclaimer": "Nem befektetési tanács. Múltbeli / szimulált eredmény, a jövőbelit nem garantálja.",
}

VIOLATION_LABELS = {
    "few_trades": "kevés kötés", "too_rare": "ritkább a kért sávnál", "too_frequent": "sűrűbb a kért sávnál",
    "drawdown": "nagyobb visszaesés a megengedettnél", "pf": "kisebb profit-faktor a megengedettnél",
    "win_lo": "a nyerési arány alsó határa a kért alatt", "hold": "hosszabb tartás a megengedettnél",
}
WORDS = {True: "igen", False: "nem", None: "nincs"}
