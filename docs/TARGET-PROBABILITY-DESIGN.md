# Célár-valószínűség – terv és előre rögzített mérés (2026-11)

**Cél:** a főképernyő „Papír kiértékelése” ablakában a felhasználó megad egy irányt (long / short), egy célárat, egy
időtávot (kereskedési nap) és opcionálisan egy stopot. A rendszer kiírja, a múltban **milyen gyakran** érte el az ár
a célt ennyi idő alatt hasonló helyzetekben – és ha stop is van, milyen gyakran a stop előtt.

A „nincs kitalált valószínűség” elv marad: a szám csak mért, kalibrált gyakoriság lehet, intervallummal és a
hasonló helyzetek számával. Ha a mérés szerint a feltételek nem javítanak az alapesélyen, csak az alapesély látszik.

## 1. Az esemény

- `t` nap zárása után kérdezünk; a célár távolsága ATR-ben: `d = irány × (cél − záró_t) / ATR14_t`.
- **Érinti:** a `t+1 … t+N` napok bármelyikén a csúcs (shortnál a mélypont) eléri a célt.
- **Stop előtt:** a cél korábban teljesül, mint a stop; ha egy napon mindkettő elérhető, a stop számít (pesszimista,
  ugyanaz a szabály, mint a backtestben).
- Tartomány: `0 < d ≤ 8`, `1 ≤ N ≤ 60`. Ezen kívül nincs szám („a múltbeli minta ezt nem fedi le”).

## 2. Tanító adat

- Papírok: a kutatási univerzum (106) + az S&P 500 és a Nasdaq-100 tagjai, ahol van árfolyam.
- Napok: papíronként véletlen (seedelt) napok 2010-től a zárolt holdout előtti napig (2025-03-31), a `t+N` napnak
  is a holdout előtt kell lennie.
- Minden naphoz és irányhoz 6 véletlen (seedelt) kérdés: `d` log-egyenletesen 0,3–8 ATR, `N` egyenletesen 1–60 nap,
  a felénél stop is (log-egyenletesen 0,5–5 ATR). *Módosítás az első futás után, a csoport-kiválasztás előtt:*
  az eredeti rács (`d` 7, `N` 5, stop 4 értéke) lépcsős modellt adott – a köztes stopnál a becslés a szomszédos
  rácspontra ugrott. Az elfogadási szabály nem változott.

## 3. Jellemzőcsoportok (point-in-time, mindegyik csak a `t` napig ismert adatból)

| csoport | jellemzők |
|---|---|
| **A – alap** | `d`, `N`, irány, ATR%, 20 napos realizált volatilitás |
| **B – piac** | a papír benchmarkja (QQQ / ^GSPC) az SMA200 felett, a benchmark 20 és 60 napos hozama |
| **C – szektor** | a szektor-ETF az SMA200 felett, 1 hónapos (21 nap) hozama, a papír hozama a szektorhoz képest |
| **D – momentum** | 12-1 havi, 3 havi, 1 havi hozam, relatív erő 60 napra, távolság a 52 hetes csúcstól |
| **E – technika** | RSI14, ADX, EMA20/50/200 sorrend, távolság az SMA200-tól ATR-ben, záró a 20 napos sávban |
| **F – fundamentum** | év/év EPS- és árbevétel-növekedés, EPS-gyorsulás (SEC XBRL, a beadás utáni naptól, #72) |
| **J – gyorsjelentés** | van-e jelentés a `t+1 … t+N` ablakban, hány kereskedési nap múlva jön, a papír szokásos jelentési mozgása ATR-ben (az utolsó 8 jelentés kétnapos elmozdulásának mediánja, csak a `t` előttiekből) |
| **V – volatilitás** | realizált volatilitás 5 és 60 napra, Parkinson- és Garman–Klass-volatilitás (20 nap), EWMA-volatilitás (λ = 0,94, a GARCH egyszerű változata), ^VIX és a ^VIX az egyéves mediánjához képest |
| **K – karakter** | a papír saját múltbeli találati aránya ugyanarra a távolságra (az 5 / 20 / 60 napos legközelebbi időtávra, az utolsó 2 évből, csak a `t`-ig lezárult kimenetekből), a nyitási rések gyakorisága (> 0,5 ATR), 250 napos csúcsosság (kurtózis), 250 napos béta |
| **G – hírek** | csak ha a #20 validációja átment (`news-research`); különben a kártyán külön, a számba nem |

## 4. Modell és mérés

- Gradient boosting osztályozó (scikit-learn `HistGradientBoostingClassifier`), monotonitási kényszerrel:
  `d` nő → az esély nem nő, `N` nő → nem csökken. Kalibráció: izotonikus, külön kalibrációs időszakon.
- Időrendi felosztás: tanító `< 2020`, kalibráció `2020–2021`, **validáció `2022 – 2025-03`**. Holdout zárva.
- **Alapesély-modell:** csak az A csoport.
- **Csoport-kiválasztás (előre rögzítve):** a B–F, majd (2026-11-06-i bővítés, a mérés előtt rögzítve) a J, V, K
  csoportok egyenként kerülnek az addigi modell mellé, sorrendben; egy csoport
  akkor marad, ha a validációs Brier-pontszámot legalább **0,5%-kal** javítja az addigi modellhez képest, és a
  kalibrációs hiba (ECE) nem nő 0,01-nél többel. A G csak akkor jelölt, ha a #20 kapuja átment.
- **Elfogadás (a végső modell):** a validáción
  1. Brier-javulás az alapesély-modellhez képest ≥ 1%;
  2. ECE ≤ 0,03 (10 sávban);
  3. évenként (2022, 2023, 2024) a Brier nem rosszabb az alapesélyénél.
  Ha nem megy át: a felület csak az alapesélyt mutatja, a feltételek hatását „nem javít mérhetően” felirattal.

- **Megbízhatóság:** az elfogadott modell és az alapesély Brier-különbségére napokra blokkolt bootstrap 95%-os
  intervallum (a validáción); csak akkor számít javulásnak, ha az intervallum alsó határa is 0 fölött van.
- **Korlát:** az árfolyamtár 10 éves (2016-10-től), ezért a tanító időszak valójában 2017 vége – 2019.
- **J – ismert-e a jelentés dátuma `t`-kor?** A múltbeli tényleges dátumot használjuk; a cégek a dátumot jellemzően
  4–6 héttel előre bejelentik, a negyedéves ritmus pedig ismert, ezért ez csak enyhe előretekintés. Élesben a
  meglévő jelentési naptár adja.

## 5. Megjelenítés

- A szám: a kalibrált esély, mellette a **validációs gyakoriság** a ±5 százalékpontos sávban (Wilson-intervallum) és
  a sávba eső múltbeli helyzetek száma; kevés (< 200) helyzetnél „nincs elég adat”.
- Az alapesély (csak volatilitásból) külön sorban – hogy látszódjon, mennyit tesznek hozzá a feltételek.
- Hozzájárulás csoportonként: az esély a csoporttal és nélküle (százalékpont), leíró szöveggel.
- Leíró megfogalmazás, nem ajánlás: „a múltban hasonló helyzetekben X%-ban érte el”.

## 6. Ami nincs benne (szándékosan)

- Nyelvi modell (a projekt elve).
- Opciós implikált volatilitás (nincs megbízható ingyenes, point-in-time forrás).
- A zárolt holdout: csak egyszer, a végső ellenőrzéshez, naplózva.

## 7. Eredmények

### 2026-11-06 – a J, V, K csoportokkal (532 papír, ~500 ezer kérdés, validáció 2022 – 2025/03)

| csoport | sorrendi Brier | egyedül az alap mellett | marad |
|---|---|---|---|
| A – alap | 0,1660 (ECE 0,0065) | – | igen |
| B – piac | 0,1694 | −2,07% | nem |
| C – szektor | 0,1678 | −1,10% | nem |
| D – momentum | 0,1662 | −0,12% | nem |
| E – technika | 0,1664 | −0,22% | nem |
| F – fundamentum | 0,1666 | −0,34% | nem |
| J – gyorsjelentés | 0,1661 | −0,04% | nem |
| V – volatilitás-előrejelzés | 0,1770 | −6,62% | nem |
| K – a papír karaktere | 0,1669 | −0,51% | nem |

Egyik csoport sem javított; a végső modell = az alapesély (javulás 95%-os intervalluma: 0 .. 0).

**Diagnózis:** a tanító időszak az árfolyamtár 10 éves ablaka miatt csak 2017 vége – 2019, egyetlen nyugodt rezsim
(^VIX > 25 a napok 3%-án; a validációban 17%-án, a kalibrációban 35%-án). A fa-alapú modell a tanítóban nem látott
volatilitás-szinteket nem tudja kezelni – ezért ront leginkább a szint-típusú V csoport (^VIX, realizált
volatilitás). A javítás iránya: hosszabb (2000-től, 2008-at és 2020-at is tartalmazó) tanító történet, és
rezsimfüggetlen (arány-típusú) jellemzők – egy új, a mérés előtt rögzített futásban.
