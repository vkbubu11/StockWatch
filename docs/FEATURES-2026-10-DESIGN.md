# Öt új funkció – terv (2026. október)

Állapot: **jóváhagyva (2026-10-17), a döntések a 9. fejezetben.** Kész: közös alapok, **(A) Reggeli összefoglaló**
**(B) Éjszakai kutatóügynök** (1–3. fázis; a 4., a szabálynyelv, később), **(C) Eseménytanulmányok** és
**(D) Gyakorló mód** (2026-10-18). Következik: (E), jóváhagyás után.

Eltérések a tervtől a megvalósítás során:
- A labor-munkasor (`labqueue`) a (B)-vel együtt készül, mert az az első, ami használja.
- A `heartbeat.trading_day` a `sessions` modulba került (a `heartbeat` továbbra is ugyanezen a néven éri el), így az új
  kód a brokert elérő `heartbeat` importja nélkül tudja, mikor van kereskedési nap.
- A 8-K sablonokban a bejelentés típusa („8-K”) argumentum, nem szöveg: a sablonokban egyáltalán nincs számjegy.
- Ha a trigger-közelség sora már említi a közeledő gyorsjelentést, a külön gyorsjelentés-sor elmarad.

Eltérések a (D)-nél:
- **Kitörés-összevetés:** nem újraszimulálás, hanem a labor ÉLŐ kísérletének mentett kötése az adott napon. A pontszám
  kalibrációja a teljes univerzum kitörés-cache-éből jön, egy papírból nem számolható ugyanígy. Ha nincs mentett kötés,
  a felület azt írja ki, hogy „a szűrők kizárták”, vagy hogy „a labor még nem értékelte”.
- **Véletlen alapvonal:** a felhasználó céltávolságát (ATR-ben) is átveszi, nem csak a stopot és az időkorlátot, így
  ugyanazzal a kilépési szabállyal fut. Kihagyásnál a referencia-értékekkel (2 ATR, 10 nap) számol.
- **Kihagyott jó helyzet:** ugyanazon a ponton a szabály, vagy egy referencia-kötés legalább +1 R lett volna. A
  referencia-kötés: következő nyitás, 2 ATR stop, 10 nap. Az alapvonal több véletlen nap átlaga, nem az adott pont
  kimenetele, ezért erre nem alkalmas.
- **A belépés napján a cél** csak nyitóáras belépésnél számít: napközbeni limit-teljesülésnél nem tudni, hogy a csúcs
  előtte volt-e (pesszimista kiegészítés a 9. fejezet 3. pontjához).
- **R:** oldalanként a stratégia költségével (`total_cost_bps`) csökkentve.
- **Egy papír egy körben** lehetőleg egyszer szerepel; kis készletnél ismétlődhet, de más napon.
- **Plafonok:** „legfeljebb 100 kör” = 100 mentett munkamenet, egy munkamenet legfeljebb 50 helyzet. A készletben
  kategóriánként 4000 / 2000 / 4000 pont van; papíronként 8 stratégia-pont, 6 „semmi” pont és 8 alakzat-próba.
- **Megosztási kód:** a seed és a készlet-ujjlenyomat mellett a helyzetek számát is tartalmazza, csak betűkkel (szám
  nincs benne, így évszám sem lehet benne).
- **A helyi ranglista** (opcionális) nem készült el.
- Mellékjavítások: a `locks.write_atomic` Windowson rövid ideig újrapróbálja a cserét, ha a célfájlt épp más olvassa. A
  webes POST a korai (401 / 403) elutasítás előtt kiolvassa a kérés törzsét: Windowson ennek hiánya kapcsolat-
  megszakadást okozott a kliensnél, és ettől bukott alkalmanként a `test_http_security`.

Eltérések a (B)-nél:
- **Szomszédság:** a döntős saját, egylépéses szomszédsága a keresési térben (mezőnként a szomszédos értékek). A
  `meanrev_research.neighbourhood` egy fix rács az alapbeállítás körül, egy távoli döntősnél nem a szomszédait mérné.
- **Döntősök:** a tanító rangsor első 3 helyezettje. A validáció nem választ közöttük (az már kiválasztás lenne a
  validáción), csak megmutatja az eredményüket.
- **Indoklás:** mezőnként a döntőst összeveti önmagával úgy, hogy az adott mező az alapértéken van. Ebből jön a „+4 pp nyerési
  arány” típusú mondat. A hatás nélküli mezők egy sorba vonva jelennek meg.
- **A `meanrev_research` bővítése** (visszafelé kompatibilis):
  - `load_universe(end=, frames=)` és `run(end=)`: a holdout előtti vágás és a paritásteszt miatt;
  - `equity_curve()`: a fix súlyú görbe közös képlete; a `stats` és a jelentés is ezt használja.
- **A `lab.evaluate` bővítése** (visszafelé kompatibilis): `until` (az adat vágása) és `guard` (holdout-őr).
- **Kitörés-motor:** a max. visszaesés %-ban az R-ben mért visszaesés × kötésenkénti kockázat (`risk_per_trade_pct`).
  Az elfogadási feltétel egyszerűbb, mint a Visszahúzásé: várható érték és alsó határa, profit-faktor, kétszeres költség.
- **Memória:** egyszerre csak egy jellemző-nézet van a memóriában; a teljes indexlistán (565 papír) a csúcs kb. 370 MB
  (ebből kb. 140 MB maga a Python a könyvtárakkal), a labor 450 MB-os plafonja alatt.
- **A tesztek elkülönítése:** a `conftest` minden tesztben ideiglenes helyre irányítja a `data/users` és a
  `data/queue` mappát.

Eltérések a (C)-nél:
- **S&P 500 változások forrása:** a „Selected changes” táblázat 2026 augusztusában átkerült a Wikipedia „Historical
  components of the S&P 500” oldalára (`id="changes"`); a parser ezt olvassa. A hatályos dátum előre ismert, ezért
  ezek az események nem „csak dátum” jelölésűek, a 0. nap a hatályos nap.
- **Két CAR:** a teljes ablak mellett mindig a „+1. naptól” mért CAR is látszik. Így a 0. napi adatból képzett besorolás
  (gap-irány) külön szabály nélkül sem torzít; a gap szerinti bontásnál ez a mérvadó.
- **A holdout:** az eseménytanulmány nem választ és nem hangol, de a holdout itt is alapból kimarad. Kapcsolóval
  megnyitható, naplózva (`study=event-study`), mint a Backteszt oldalon.
- **Gyorsjelentés-időpont:** a `HistoricalEarnings` cache új `times` mezőt kap. A régi cache-ben ez nincs meg, ott a
  30 naponkénti frissítésig minden gyorsjelentés „csak dátum” (a következő kereskedési naptól számít).
- **Form 4:** a parser a SEC dokumentált formátumára és fixtúrákra épül. Valódi csomagot nem tudtam letölteni:
  a SEC kapcsolattartási adat nélküli User-Agentet 403-mal elutasít, a `.env`-ben pedig nincs `SEC_USER_AGENT`. Az első
  valódi letöltésnél érdemes ellenőrizni az oszlopneveket.
- **Szektor:** az `indexes.parse_sectors` olvassa az S&P 500 táblázat GICS-oszlopát; a Nasdaq-100-only papíroknál
  „nem ismert”. Sorrend: (A) Reggeli összefoglaló → (B) Éjszakai kutatóügynök →
(C) Eseménytanulmányok → (D) Gyakorló mód → (E) Megosztható stratégiaoldalak. Minden funkció külön jóváhagyás
után indul, és a végén `pytest` zöld.

A szolgáltatás kutatási és tanulási eszköz. **Nem kereskedik, nem ad befektetési tanácsot, és nem használ
LLM-et.** Minden szöveg determinisztikus sablonból készül, és minden benne lévő szám számításból jön.

---

## 0. Összefoglaló

| | Hol fut | Nehéz számítás | Tárolás (plafon) | Új modul |
|---|---|---|---|---|
| **A** Reggeli összefoglaló | monitor, 06:30 Budapest (kb. 1–2 perc) | nincs | `digest/` 90 nap (≤ 10 MB) | `digest/` |
| **B** Kutatóügynök | labor-szolgáltatás, éjjel; kérésre azonnal | igen (keretből) | `agent/` 30 futás (≤ 50 MB) | `research_agent/` |
| **C** Eseménytanulmányok | kis univerzum: web-háttérszál; nagy: labor | közepes | tanulmányok ≤ 30 MB; Form 4 ≤ 50 MB | `events/` |
| **D** Gyakorló mód | webes folyamat (egy papír, gyors) | nincs (a feladatkészlet a laborban épül) | ≤ 10 MB | `practice/` |
| **E** Stratégiaoldalak | publikálás: web + labor; napi frissítés: labor | közepes | ≤ 20 MB | `publish/` |

Közös alapok (1. fejezet) minden funkció előtt: munkaterület-paraméter, felhasználónkénti mérés, labor-munkasor,
biztonsági őr-tesztek.

---

## 1. Közös alapok

### 1.1 Munkaterület és mérés (SaaS-előkészítés)

- `src/stockwatch/workspace.py`: minden új nyilvános függvény `workspace: str = "admin"` paramétert kap.
  - `workspace.user_dir(ws, feature)` → `data/users/<ws>/<feature>/`. Csak a felhasználóhoz tartozó új adat
    kerül ide (briefek, ügynök-futások, összefoglalók, gyakorló körök, publikált oldalak). A közös piaci adat
    (Form 4, indexváltozások, árfolyamok) a közös `data/` alatt marad. Az elérési út csak ebből a függvényből
    jön, így a SaaS-átállásnál egy helyen cserélhető.
  - `workspace.watchlist(ws)` és `workspace.holdings(ws)`: az admin munkaterületen a mai globális watchlistet és a
    `holdings.json`-t adják vissza. Más munkaterületen `NotImplementedError`, amíg a SaaS 1. fázisa el nem készül.
- `src/stockwatch/usage.py`: felhasználónként és havonta mér: CPU-másodperc (`time.process_time()` különbsége),
  futásszám fajtánként (`agent`, `event_study`, `practice`, `publish_refresh`, `digest`).
  - Tárolás: `data/users/<ws>/usage.json`, legfeljebb 24 hónap.
  - Ma csak mér, nem tilt. A SAAS-DESIGN 9. fejezetének kvótái egy `usage.allow(ws, kind)` hívással épülnek rá.

### 1.2 Labor-munkasor

A homokozó ma egyetlen `request.json` / `status.json` párral kér futást a labortól. A sok új nehéz feladat miatt
ebből általános, **fájlalapú munkasor** lesz: `src/stockwatch/labqueue.py`.

- Egy feladat egy fájl: `data/queue/<ws>/<job_id>.json`, benne a fajta, a paraméterek és az állapot.
- Az írás zárral és atomikusan történik (`locks.file_lock`, `write_atomic`).
- A labor `run_forever` köre a homokozó-kérés után a munkasort dolgozza fel, utána jön a `run_once`.
- Sorrend: elsőbbség szerint (publikálás frissítése > eseménytanulmány > ügynök), azon belül munkaterületenként
  felváltva. Ez a SAAS-DESIGN 4.1 3. pontjának méltányos sorrendje.
- Plafon: munkaterületenként legfeljebb 10 várakozó feladat. A kész feladatok 7 nap után törlődnek.
- A meglévő homokozó-kérés útvonala változatlan marad.

### 1.3 Biztonsági őrök (tesztek, minden funkcióra)

| Őr | Megvalósítás |
|---|---|
| **Nincs broker** | `tests/test_guards.py`: AST-bejárás az új csomagokból (`digest`, `research_agent`, `events`, `practice`, `publish`, `workspace`, `usage`, `labqueue`). A bejárás tranzitív, és a függvényen belüli importokat is követi. Ha eléri a `stockwatch.broker` modult, a teszt elbukik. Tiltott még: `monitor`, `heartbeat`, `positions`, `replay`, `revalidate`, mert ezek elérik a brokert. |
| **Nincs ajánlás** | Tiltólista (hu / en / de / es) az új sablonok összes katalógussorára és a kimenetekre. Pl. „vedd meg”, „vegyél”, „vételi limit”, „vételi jelzés”, „ajánlott”, „javasolt vétel”, „buy”, „sell now”, „should buy”, „kaufen”, „comprar”. |
| **Szám-ellenőrzés** | A renderelő mondatonként visszaadja a sablon kulcsát és az argumentumokat is. A teszt minden számot (regex) kigyűjt a kimenetből, és megnézi, hogy az argumentumok formázott értékei között van-e, illetve azok a forrásadatból jönnek-e. |
| **Holdout** | `HoldoutGuard`: a kutatási útvonalakon átadott adatkeret utolsó napja `< HOLDOUT_START`. A teszt azt is ellenőrzi, hogy a `holdout_log.jsonl` bájtra azonos a futás előtt és után. |

**Előfeltétel-javítás (egysoros).** A `sentiment/sources/alpaca.py` ma függvényen belül a `broker` modulból
importálja az `alpaca_credentials` függvényt. Ugyanez a függvény a `config`-ban is megvan, és a broker is onnan
veszi. Emiatt a `meanrev` → `sentiment.service` → `alpaca` → `broker` lánc tranzitívan eléri a brokert, és az
AST-őr minden új modult megbuktatna. Javítás: import a `config`-ból. A viselkedés nem változik.

### 1.4 Szövegsablonok

- Minden új mondat a `locale/catalog.jsonl`-ben van. A kulcs a magyar szöveg pozíciós helyőrzőkkel
  (`{0}`, `{1:n}`), a meglévő `i18n.translate` illesztése szerint.
- A sablonok kódban egy helyen vannak felsorolva (`*/templates.py`). Egy teszt ellenőrzi, hogy mindegyik
  szerepel a katalógusban, mind a négy nyelven.
- A dokumentumban a példák a könnyebb olvashatóságért névvel írják a helyőrzőt (`{sym}`); a kódban pozíciósak lesznek.

### 1.5 Napi idővonal (a Budapest és New York közti eltolódást a triggerek saját időzónája kezeli)

| New York | Budapest | Mi fut |
|---|---|---|
| 16:15 | 22:15 | monitor zárás utáni köre (változatlan) |
| a zárás utáni kör vége után | | munkasorba: a publikált oldalak frissítése (E) |
| 20:00 (a `BUSY_WINDOW` vége) | 02:00 | labor: munkasor (ügynök-briefek, eseménytanulmányok), utána `run_once` |
| 00:00 | **06:00** | az ügynök **kemény határideje**: ha addig nem végzett, a részeredményből jelentés készül |
| 00:30 | **06:30** | monitor: reggeli összefoglaló (A), kész kb. 06:35-re (határidő: 07:00) |

A márciusi és novemberi napokon, amikor csak az egyik ország állt át nyári időre, az eltolódás 5 óra. Ezért az
összefoglaló és a határidő triggere `Europe/Budapest` időzónájú, nem New York-i.

---

## 2. (A) Reggeli összefoglaló

### Adatfolyam
```
watchlist + Saját pozíciók ─┐
tárolt, LEZÁRT gyertyák ────┼─► detect.snapshot() ─► mai pillanatkép ─┐
earnings-cache, EDGAR 8-K,  │                                         ├─► detect.diff() ─► események ─► score ─► render ─► store / web / Discord
hír-jellemzők, jelzésnapló, │       tegnapi pillanatkép (store) ──────┘
momentum-rangsor, rezsim ───┘
```

**Pillanatkép papíronként.** Csak tárolt adatból áll, a gyertyáknál csak lezártból (`sessions.completed_bars`):

| Elem | Honnan |
|---|---|
| formációk és trigger-szintek | `forecast.analyse` (`matches`, formálódó / kitört) |
| ATR, záró | `indicators` |
| legközelebbi support / rezisztencia | `levels` |
| következő gyorsjelentés | `market.EarningsCalendar`, csak cache-ből |
| 8-K tételek a legutóbbi összefoglaló óta | `sentiment.store`; reggel EDGAR-frissítés papíronként 1 kéréssel |
| hír-jellemzők | `sentiment.features.daily`, a döntési nap határával |
| stratégiai jelölt-tagság | a **jelzésnapló** (`ledger`) az utolsó kereskedési napra, stratégiánként, és a `momentum_state.json` rangsora |
| rezsim | QQQ, `^GSPC`, a papír saját SMA200-a |

Nincs újraszámolt jelzés, tehát egy igazságforrás van. A piaci szintű rezsimváltás az összefoglaló fejlécébe kerül.

**Változásérzékelés (`detect.diff`).** Ha nincs előző pillanatkép (első futás), csak állapotot ír, változást nem.

| Esemény | Feltétel |
|---|---|
| új / megszűnt formáció | a minta kulcsa (`PatternMatch` azonosító) megjelent vagy eltűnt |
| trigger-közelség | a trigger távolsága ATR-ben ≤ `near_trigger_atr` (alap 1,0), és tegnap még nem volt az, vagy legalább 0,5 ATR-t közeledett |
| áttört / elveszett szint | a záró tegnap a rezisztencia alatt volt, ma fölötte van (vagy a support fordítva) |
| közeledő gyorsjelentés | a hátralévő napok száma átlép egy küszöböt: 7, 3 vagy 1 nap |
| új 8-K | új bejelentés tételkóddal; a lényeges tételek (pl. 4.02, 1.03, 3.01) nagyobb súlyt kapnak |
| hírhangulat-fordulat | a `sent_mean_3d` előjele megfordul (holtsávval), vagy új `material_neg_3d` |
| jelölt be / ki | stratégiánként változik a döntés a jelzésnaplóban, vagy a papír belép a momentum top N-be / kiesik onnan |
| rezsimváltás | QQQ / S&P 500 / saját trend: a záró átlépi az SMA200-at |

**Pontozás (`score.py`).** Szabályalapú: `súly[típus] × módosítók`.

- A módosítók: Saját pozíció ×1,5; trigger-közelségnél `1 / max(ATR-távolság, 0,25)`; lényeges 8-K ×2.
- A súlyok és a plafonok a `settings.yaml` `digest:` szakaszában vannak, pydantic-validációval.
- Papíronként legfeljebb 3 sor, összesen legfeljebb `max_items` (alap 15).
- A változás nélküli papírok egy sorba vonva: „Változás nélkül: AAPL, MSFT, …”.
- Rendezés: pontszám, azonos pontszámnál ticker (determinisztikus).

**Render (`render.py`).** Mondatsablonok a katalógusból, például:
- „Az {sym} {atr} ATR-re került a(z) {pattern} triggerétől; gyorsjelentés {days} nap múlva.”
- „Az {sym} záróára a(z) {level} rezisztencia fölé került.”
- „A Nasdaq-100 (QQQ) a 200 napos átlag alá zárt.”

Ha van friss ügynök-jelentés (B), külön sor hivatkozik rá: a brief neve, a döntősök száma, link.
Az összefoglaló alján mindig: „Nem befektetési tanács.”

### Modulok, CLI, API
- `src/stockwatch/digest/`: `detect.py`, `score.py`, `render.py`, `store.py`, `templates.py`.
- CLI: `python cli.py digest [--print] [--date YYYY-MM-DD] [--no-send]`.
- API:
  - `GET /api/digest` (legutóbbi + archívum-lista);
  - `GET /api/digest/<YYYY-MM-DD>`;
  - `POST /api/digest/run` (kézi futtatás, háttérfeladat).
- Web: „Reggeli összefoglaló” nézet az Áttekintés fül tetején, és archívum-oldal (`#/digest`).
- Discord: opcionálisan (`digest.discord: false` alapból), a meglévő `Dispatcher`-en, `event="digest"`.
  - Hosszú összefoglalónál levág a Discord-korlátra, és a webes nézetre mutat.
  - Csendes időszak (`quiet_hours`) esetén csak naplóz, mint a többi értesítés.

### Tárolás és plafon
- `data/users/<ws>/digest/<YYYY-MM-DD>.json` (pillanatkép + tételek, ~20–60 KB).
- 90 nap marad meg, a régebbi a `housekeeping`-ben törlődik; a `data-usage`-ben külön sor.

### Ütemezés
- A monitor új jobja: 06:30 `Europe/Budapest`, csak olyan napon, amikor New Yorkban aznap kereskedési nap van.
- A hétfői összefoglaló a pénteki zárást és a hétvégi 8-K-kat foglalja össze.
- Ha a monitor 06:30-kor nem futott, induláskor pótolja (07:00-ig), a `heartbeat.missed_close` mintájára.

### Tesztek (a feladatban felsoroltakon túl)
- Szintetikus pillanatkép-párok minden eseménytípusra.
- Első futás: nincs hamis „új” esemény.
- Determinisztikus sorrend azonos pontszámnál.
- A 06:30-as trigger téli és nyári időben is helyes.

### Kockázatok
- A `forecast.analyse` papíronként ~0,5–1 s: 60 + 50 papírnál 1–2 perc, ez belefér.
- A formáció-kulcs stabilitása: ha egy minta geometriája naponta kicsit módosul, ne jelenjen meg minden nap
  „új formációként”. Kezelés: kulcs a mintanév + a kezdőpivot dátuma, és 1 napos türelmi idő a megszűnésre.

---

## 3. (B) Éjszakai kutatóügynök

### Brief (`brief.py`, pydantic, `extra="forbid"`)
```yaml
name: swing-long-indexek
engine: pullback          # pullback | deep | etf  (kiinduló alapbeállítás; 3. fázis: breakout)
universe: indexes         # indexes | nasdaq100 | sp500 | watchlist | etf
objective:
  trades_per_week: [0.5, 2.0]
  max_drawdown_pct: 10
  max_hold_days: 15
  cost_bps: 15            # oldalanként; a futás teljes idejére rögzítve
  min_win_lo: null        # opcionális FELTÉTEL: a nyerési arány Wilson-alsó határa (pl. 0.65)
  min_pf: 1.3
search:
  seed: 42
  method: halving         # grid | random | halving
  max_variants: 300
  max_cpu_minutes: 120
  fixed: {regime_filter: true}       # opcionális: rögzített mezők
```

### Kereshető tér (`space.py`), Visszahúzás-család
| Mező | Értékek | Megjegyzés |
|---|---|---|
| `rsi2_max` | 1, 2, 3, 5, 7, 10 | |
| `limit_pct` | 0, 1, 2, 3, 4, 5, 6, 7 | |
| `stop_atr` | 2, 3, 4, 5, 6, 8 | |
| `exit_crsi` | 40, 50, 60, 70, 80 | |
| `exit_sma` | 0, 5, 10 | jellemző-változó |
| `time_stop` | 3 … `max_hold_days` | a briefből vágva |
| `trend_sma` | 100, 150, 200 | jellemző-változó |
| `regime_filter` | igen / nem | |
| `earnings_blackout_days` | 0, 3, 5 | |
| `max_open` | 0, 5, 10 | a visszaesést korlátozza |
| rögzített | `atr_period`, `min_price`, `min_dollar_volume`, `cost_bps` = a brief költsége | |

Minden változat a `PullbackParams` validátorán megy át. Az érvénytelen változatot a validátor elutasítja: ez
bekerül a naplóba „elutasítva: <ok>” sorral, és a keresés folytatódik.

### Keresés (`strategy.py`, `search.py`)
1. **Adat:** `meanrev_research.load_universe`. Két nézet készül, mindkettő `HoldoutGuard`-dal:
   - *tanító* nézet: `< SPLIT_DATE`, csak ezen történik a kiválasztás;
   - *látható* nézet: `< HOLDOUT_START`, csak a döntősöknek.
   A jellemzők a jellemző-változók (`trend_sma`, `atr_period`, `exit_sma`) szerint csoportosítva készülnek el.
   Egyszerre csak egy csoport van a memóriában, mert a labor memóriaplafonja 450 MB.
2. **Értékelés:** `meanrev_research.evaluate_variant(view, params, holdout=False)`. Nincs saját backteszt-logika.
3. **Célfüggvény:**
   - kemény feltételek: kötés / hét a sávban, max. visszaesés (a `stats` saját fix súlyú görbéjéből), tartás, PF,
     és ha meg van adva, `min_win_lo`;
   - teljesíthető változatnál pontszám = `ev_lo` (az átlagos nettó hozam havi blokkos bootstrap alsó határa) a
     tanító időszakon; a nyerési arány nem célmutató (9. fejezet, 2. pont);
   - nem teljesíthetőnél negatív büntetés a sávtól mért távolsággal, hogy a keresés a teljesíthető felé mozduljon;
   - egyenlőségnél a döntés determinisztikus (`win`, `pf`, majd a paraméterek kanonikus sorrendje).
4. **Successive halving** (alap): `n0` változat véletlenül, a briefben megadott seeddel.
   - 1. fok: a papírok 25%-a (seedelt, hash szerinti részhalmaz); a legjobb harmad továbbmegy.
   - 2. fok: a papírok 50%-a; ismét a legjobb harmad megy tovább.
   - 3. fok: minden papír.
   - Végül **koordináta-finomítás** a legjobb körül: mezőnként ±1 lépés. Ebből jön a „melyik irány javított” napló.
5. **Döntősök** (3–5): **egyszer** megnézi őket a látható nézeten (validáció), majd robusztussági lépések jönnek:
   - `meanrev_research.neighbourhood` a tanító időszakon: hány szomszéd tartja a szintet;
   - kétszeres költség;
   - évenkénti bontás;
   - a `gate()` feltételei.
6. **A legjobb 3** a jelentésbe kerül, a kipróbált változatok számával (érvényes, elutasított, fokonként).

**Költségkeret.** Változatszám és CPU-perc (`usage` mérés), valamint a 06:00-s kemény határidő.
- A keret 20%-a a döntősök ellenőrzésére van fenntartva.
- Ha a keret elfogy, a keresés leáll, és az addigi legjobbakból részjelentés készül. A jelentés kiírja, mely
  lépések maradtak ki.

**Determinizmus.** A seed, a `code_version`, a brief hash-e és az adat ujjlenyomata bekerül a jelentésbe.
Azonos bemenetre bitre azonos JSON készül.

**Megjegyzés.** A korrigált árak osztaléknál visszamenőleg is változnak. Ezért ugyanaz a brief hónapokkal
később kissé eltérhet. Az ujjlenyomat ezt láthatóvá teszi.

### Indoklás és napló (`explain.py`)
- Indoklás: a döntős mezőit összeveti a kiinduló alapbeállítással. Minden eltérő mezőhöz a koordináta-lépés
  mért hatása kerül a sablonba, például: „a tágabb stop ({0:n} ATR) a tanító időszakban {1:n} pp nyerési arányt
  adott, a szomszédos beállítások {2:n}%-a is {3:n}% felett maradt”.
- Napló: `log.jsonl`, egy sor egy lépés: fok, változat-azonosító, módosított mező, régi → új érték, pontszám
  előtte és utána, ítélet (javított / rontott / elutasítva). Legfeljebb 5 000 sor.
- A felületen idővonalként jelenik meg.

### Jelentés (`report.py`)
- JSON + Markdown. Tartalma:
  - döntősönként: paraméterek, tanító és validációs mutatók, a `gate()` ellenőrzései, szomszédság-stabilitás,
    kétszeres költség, évek, tőkegörbe;
  - a futás adatai: kipróbált változatok száma, keret-felhasználás, figyelmeztetések (kis minta, csak mai
    indextagok, sok próbálkozás).
- A tőkegörbe ugyanabból a fix súlyú képletből készül, mint a `meanrev_research.stats` visszaesése. Ehhez a
  képletet egy kis közös függvénybe emelem ki (`equity_points`), így a két helyen biztosan ugyanaz.
- **Paritás:** a `meanrev_research.run` kap egy opcionális `end` paramétert (alapból `None`, a mai működés
  változatlan). A paritásteszt `end = HOLDOUT_START − 1 nap` mellett hasonlítja össze a mutatókat.

### Homokozó-átvétel
Új kis modul: `src/stockwatch/sandbox_adopt.py`, brokermentes. A (B), az (E) „Fork” gombja és később a Backteszt
oldal is ezt használja.

Mit csinál:
- `strategies.sandbox`: `engine=pullback`, `params=<döntős>`, `alerts=false`, `enabled=true`;
- naplózás: `ledger.log_config_change`, majd `config.save_strategies`;
- utána `meanrev_research.request_sandbox`.

A paper-kötés változatlan (a homokozó nem is köthető hozzá). A régi homokozó-paraméterek egy 10 elemes
előzménylistába kerülnek, a felület előtte megerősítést kér. Az élő virtuális követést a monitor meglévő
`pullback_cycle`-ja végzi.

### Ütemezés és tárolás
- Labor-munkasor (1.2). Éjjel a függő briefek futnak; „Futtatás most” gombbal azonnal is indíthatók.
- `data/users/<ws>/agent/`:
  - `briefs/<id>.yaml` (legfeljebb 20);
  - `runs/<run_id>/{status.json, log.jsonl, report.json, report.md}` (legfeljebb 30 futás, 50 MB, a
    legrégebbi törlődik).
- A futások száma briefenként és motoronként látszik. A validációs időszak minden megnézése számít:
  sok futás után már ez sem független.

### Fázisok
1. Brief + tér + keresés a Visszahúzás-családon. CLI: `python cli.py agent run --brief briefs/x.yaml`, Markdown-jelentés.
2. Web:
   - brief-űrlap, futás-állapot, jelentés-oldal tőkegörbével, döntősök összevetése, Homokozó-átvétel;
   - API: `GET/POST /api/agent/briefs`, `POST /api/agent/run`, `GET /api/agent/run/<id>`, `POST /api/agent/adopt`.
3. Kitörés-motor a `lab.EDITABLE` téren.
   - A `lab.evaluate` kap egy opcionális `until` paramétert, hogy az adat a `HOLDOUT_START` előtt vágódjon.
   - A változatok fokonként egy papíronkénti körben futnak. Lassú motor: egy éjszaka kb. 30–60 változat.
4. (Később) szabálynyelv (`backtest_page/rules.py`, JSON-műveletfa) a BACKTEST-PAGE-DESIGN 4. fázisa szerint.

### Kockázatok
- **Túlillesztés (sok próbálkozás).** Kezelés: kiválasztás csak a tanítón, a validációt egyszer nézi meg, a
  szomszédság és a kétszeres költség kötelező, a kipróbált változatok száma mindig látszik. A holdoutot az ügynök
  soha nem nyitja meg.
- **Memória.** 520 papír jellemzői kb. 100 MB csoportonként. Csoportonként sorban halad, a részhalmazos fokok
  kevesebbet töltenek be.
- **A labor `run_once` köre hosszú lehet.** Éjjel ezért a munkasor fut előbb; a napi `run_once` kör az első
  teljes futás után percekig tart.

---

## 4. (C) Eseménytanulmányok

### Eseménytípusok (`catalog.py`)
Minden típus megadja: mikor vált **nyilvánossá** az esemény, ebből a 0. napot („az első kereskedési nap, amikor
már ismert”), és a kiválasztáshoz szükséges adatokat. A kiválasztás csak a 0. nap előtti adatot láthatja.

| Típus | Forrás | 0. nap |
|---|---|---|
| gyorsjelentés (+ gap iránya: fel / le / semleges) | `HistoricalEarnings` | nyitás előtti jelentésnél aznap, zárás utánnál és ismeretlen időpontnál a következő kereskedési nap |
| 8-K tételkód | `sentiment` EDGAR-tár | az első kereskedési nap a beadás (`acceptanceDateTime`) után |
| 52 hetes csúcs / mélypont | árfolyam | a záró napja; a kiválasztás `t`-ig ismert adat |
| nagy gap (≥ X% vagy ≥ k ATR) | árfolyam | a gap napja |
| saját stratégia jelzése | `meanrev.setup_mask`, a labor kitörés-cache-e | a jelzés napja |
| insider-vétel / -eladás, cluster buy | **új:** Form 4 | az első kereskedési nap a beadás után |
| S&P 500 bekerülés / kikerülés | **új:** változás-tábla | a hatályos dátum (a bejelentési hatás nincs benne) |

- **Gap szerinti bontásnál** a besorolás a 0. nap reakcióján múlik. Ilyenkor a kumulált abnormális hozam az
  1. naptól számít (a reakció után), különben a besorolás eleve benne lenne az eredményben. A felület kiírja.
- **Form 4 (`sources/form4.py`):**
  - XML-feldolgozás: kibocsátó, bennfentes, szerep, tranzakciókód (P / S), darab, ár, érték, 10b5-1 jelölés;
  - a beadási idő az EDGAR submissions JSON-ból jön;
  - **cluster buy:** legalább 3 különböző bennfentes P-kódú vétele 10 kereskedési napon belül (paraméterezhető),
    a 10b5-1 tervek nélkül;
  - a múlt a SEC negyedéves csomagjaiból, az élő frissítés papíronkénti XML-lel (9. fejezet, 5. pont).
- **S&P 500 változások (`sources/index_changes.py`):** a Wikipedia „Selected changes” táblája, ugyanazzal a
  letöltő mintával, mint az `indexes.py`. Hatályos dátum, felvett / törölt ticker, ok.

**EDGAR-korlát.** Az `edgar._get` ma folyamaton belül tart ~8 kérés/mp-et. A monitor és a labor viszont
párhuzamosan is kérhet, és együtt túlléphetik a 10/s-ot. Javítás: folyamatok közti korlát egy közös, zárolt
időbélyeg-fájllal, így összesen is legfeljebb 8/s megy ki. A User-Agent kötelező (`SEC_USER_AGENT`).

### Módszertan (`study.py`)
- **Hozam:** korrigált záróból számolt napi hozam.
- **Benchmark:** alapból a papír indexe (`MarketContext.bench_for`), választható a SPY.
- **Piaci modell:** OLS a becslési ablakon (alap: [−260, −21] nap).
  - Legalább 120 megfigyelés kell; ha nincs meg, piac-korrigált hozam (R − R_m), és az esemény jelölve van.
- **AR és CAR:** napi abnormális hozam (AR) és kumulált abnormális hozam (CAR) az ablakban (alap: −10 … +60 nap).
  - A 0. nap előtti szakasz leíró jellegű, a felületen „nem kereskedhető” jelöléssel.
- **Összesítés:**
  - átlagos és medián pálya;
  - **dátum szerint csoportosított bootstrap** sáv (sok gyorsjelentés esik ugyanarra a hétre), 2000 minta, rögzített seed;
  - találati arány (CAR > 0) Wilson-intervallummal.
- **Bontás:** szektor (az S&P-tábla GICS-oszlopából; az `indexes.py` elmenti), rezsim (benchmark > SMA200 a
  0. nap előtt), év.
- **Átfedés:** ugyanannak a papírnak az ablakon belül eső későbbi eseménye kimarad. Mindig látszik: összes
  esemény / felhasznált / átfedés miatt kiesett / adat nélküli.
- **Torzítások, amiket a felület kiír:** a mai indextagság túlélési torzítása, a hiányos régi earnings-naptár, és
  hogy a kivezetett papírok árfolyama nem tölthető le.

### Kód, API, CLI
- `src/stockwatch/events/`: `sources/form4.py`, `sources/index_changes.py`, `catalog.py`, `study.py`, `store.py`.
- API:
  - `GET /api/events/types`;
  - `POST /api/events/study` (egy papírnál vagy watchlistnél háttérszál, 60 s korláttal; nagyobb
    univerzumnál labor-munkasor);
  - `GET /api/events/study/<id>`, `GET /api/events/study/<id>.csv`;
  - `GET /api/events/study/<id>/event/<n>`.
- Web: új „Események” fül.
  - Űrlap: típus, univerzum, időszak, ablak, bontás.
  - Eredmény: átlagos pálya sávval, hisztogram, eseménylista; egy eseményre kattintva a papír chartja az
    esemény körül; CSV-export.
- CLI: `python cli.py event-study --type earnings --universe sp500 --start 2017-01-01 --window -10 60 [--split gap] [--csv out.csv]`.

### Tárolás és plafon
- Tanulmányok: `data/users/<ws>/events/studies/<id>.json.gz`, legfeljebb 30 db, 30 MB.
- Közös források: `data/events/form4/<év>.csv.gz` (csak a kivonat, legfeljebb 50 MB) és
  `data/events/index_changes.json` (< 1 MB). Mindkettő bekerül a `housekeeping`-be és a `data-usage`-be.

### Tesztek
- Szintetikus adat ismert CAR-ral: β · piac + +5% ugrás az 1. napon → CAR ≈ 5%.
- Point-in-time: ha a 0. nap utáni árakat felülírjuk, az α, a β és az eseménylista nem változik.
- Form 4 parser fixtúrákon: P, S, több tranzakció, 10b5-1, módosított beadás (4/A).
- EDGAR-korlát: két „folyamat” mellett is ≥ 0,12 s a kérések között, és van User-Agent.
- Átfedés-kezelés, bootstrap-determinizmus.

### Kockázatok
- Történeti Form 4: a negyedéves csomagok formátuma változhat; a parser fixtúrákon tesztelt.
- A Wikipedia-tábla szerkezete változhat: a parser fixtúrákon tesztelt; ha hibázik, a forrás „nem elérhető”
  lesz, a többi eseménytípus fut tovább.

---

## 5. (D) Gyakorló mód

### Adatfolyam
```
feladatkészlet (labor, adat-ujjlenyomatonként egyszer) ─► sampler(seed) ─► 20 helyzet
   ─► API: rejtett, normalizált chart ─► döntés ─► szimuláció (meanrev.simulate_trade) ─► pörgetés + értékelés ─► felfedés
```

**Feladatkészlet (`sampler.py`).**
- Forrás a `backtest_page` adattára és az élő univerzum-cache, a tár elsőbbséggel.
- Jelölt döntési pont `t`, ha:
  - legalább 250 bemelegítő gyertya van;
  - a `t + 30` nap (a leghosszabb időkorlát) **előbb van, mint a `HOLDOUT_START`**.
- Kategóriák:
  - stratégia-jelzés: `meanrev.setup_mask` a Visszahúzás-családra, a labor kitörés-cache-e a Kitörésre;
  - formálódó minta;
  - „semmi sincs”.
- Súlyozott, seedelt mintavétel (alap: 40 / 20 / 40%).
- A megosztási kód a seedet és a készlet ujjlenyomatát tartalmazza. Más adatnál a felület figyelmeztet, hogy
  ugyanaz a kód nem ugyanazt a 20 helyzetet adja.

**Rejtés (`session.py`).**
- Az API csak egy véletlen, a papírtól független feladat-azonosítót ad ki. A papírt és a dátumot csak a
  szerveroldali munkamenet tárolja.
- A chart adatai:
  - **index-tengely** (−119 … 0), dátum nélkül;
  - OHLC úgy normalizálva, hogy az utolsó látható záró = 100;
  - a volumen a 20 napos átlagához viszonyítva;
  - az EMA-k a normalizált árból.
- A chart a meglévő `drawChart` stílusában rajzolódik, indextengellyel.

**Döntés:** kihagy, vagy belép:
- belépés: következő nyitás vagy limit, normalizált áron;
- stop (kötelező, a belépő alatt);
- cél (opcionális);
- időkorlát: 1–30 nap.

A döntés után nem módosítható.

**Szimuláció (`scoring.py`).** `meanrev.simulate_trade`, ugyanaz a pesszimista motor:
- `levels = (limit, stop)`; a következő nyitásos belépés = végtelen limit, ami a nyitón teljesül;
- `params`: `time_stop`, `cost_bps`;
- a cél: `target` + `target_intraday=True` (csak a gyakorló mód kapcsolja be).

Eredmény R-ben: (kilépő − belépő) / (belépő − stop).

**Összevetés ugyanazon a ponton:**
- a Visszahúzás / Mély visszahúzás: ha `t`-n jelzett, a saját paramétereivel szimulálva, különben „nem jelzett”;
- a Kitörés: ha a labor-cache-ben van esemény `t`-n, az `entry_research` szimulációjával;
- véletlen alapvonal: ugyanazon a papíron, seedelt K = 20 véletlen napon, a felhasználó stop-távolságával
  (ATR-ben) és időkorlátjával, az átlagos R.

**Felfedés.** A döntés rögzítése után: a pörgetés (animálva vagy egy kattintással), az eredmény, majd a papír és
a dátum.

**Kör-statisztika:**
- nyerési arány, átlag R;
- fegyelem: stop-távolság eloszlása ATR-ben, kihagyott jó helyzetek (ahol a stratégia vagy az alapvonal ≥ +1R
  lett volna), a túl szoros stopok aránya (< 1 ATR).

A ranglista a SaaS-fázisig csak helyi, és opcionális.

### Kód, API, tárolás
- `src/stockwatch/practice/`: `sampler.py`, `session.py`, `scoring.py`, `store.py`.
- API:
  - `POST /api/practice/session` `{seed?, rounds, mix}`;
  - `GET /api/practice/session/<id>/task/<k>`;
  - `POST .../task/<k>/decision`;
  - `GET /api/practice/session/<id>/summary`.
- Web: „Gyakorlás” fül.
- Tárolás:
  - `data/users/<ws>/practice/sessions/<id>.json`, legfeljebb 100 kör;
  - a feladatkészlet: `data/practice/pool.json.gz`, legfeljebb 5 MB, ujjlenyomatonként felülíródik.

### Tesztek
- **Szivárgás:** a teljes API-válasz (minden végpont, a felfedés előtt) nem tartalmaz:
  - tickert (a teljes ismert tickerlistával ellenőrizve);
  - dátum-mintát (`\d{4}-\d{2}-\d{2}`, illetve évszámot 1980 és 2030 között);
  - a nyers árak egyikét sem (két tizedesre kerekítve);
  - a normalizálás nélküli volument.
  A legnagyobb árszint a 100 közelében van.
- **Szimuláció:** ugyanarra a bemenetre a `scoring` eredménye megegyezik a közvetlen `meanrev.simulate_trade` hívással.
- **Holdout:** egyetlen feladat jövőbeli ablaka sem éri el a `HOLDOUT_START`-ot.
- **Seed:** ugyanaz a seed és készlet ugyanazt a 20 helyzetet adja.

### Kockázatok
- Nagyon ismert helyzetek (pl. 2020 márciusa) a normalizálás ellenére is felismerhetők. Ez elfogadható;
  igény esetén a sampler kihagyhat jelölt időszakokat.
- A felfedés előtti chart nem tartalmazhat split-ugrást: a korrigált adat ezt kezeli, de a
  `data.quality` ugrás-jelzésével rendelkező szakaszok kimaradnak.

---

## 6. (E) Megosztható stratégiaoldalak

### Mit lehet publikálni
| Forrás | Tartalom |
|---|---|
| Homokozó (visszahúzás-motor) | paraméterek, tanító / validációs összefoglaló, élő virtuális követés a publikálás napjától |
| ügynök-jelentés döntőse | ugyanez |
| mentett backteszt-futtatás | egy papír, egy stratégia; a papír neve látszik, nyers ár nem |
| gyakorló kör | csak a kör statisztikája (R, nyerési arány); a felfedett papírok és dátumok nem |

A Kitörés-motoros oldal később jön, a labor „élő” eredményére építve.

### Pillanatkép (`snapshot.py`)
- A pillanatkép egy **engedélylistás** pydantic-séma (`PublicSnapshot`, `extra="forbid"`). Csak a felsorolt
  mezők kerülhetnek bele, minden más eleve kimarad, nem utólag szűrve.
- A paraméterek **befagyasztva**: a homokozó későbbi módosítása nem változtatja meg az oldalt.
- A papírlista is befagyasztva a publikálás napján, így az élő követés túlélési torzítás nélküli.
- **Backteszt-összefoglaló:** tanító és validációs időszak a `meanrev_research.evaluate_variant`-tal,
  `end = HOLDOUT_START − 1 nap` mellett. Holdout nincs rajta.
- **Élő követés:** naponta, zárás után a labor ugyanazzal a függvénnyel lefuttatja a befagyasztott paramétereket
  a befagyasztott listán, és csak a `signal_date ≥ publikálás napja` kötéseket tartja meg. Ezek közül csak a
  **lezártak** kerülnek fel, és csak ha `exit_date ≤ ma − késleltetés` (alap: 5 kereskedési nap).
- **A tőkegörbe** normalizált (%), és szintén a késleltetési határnál ér véget. Különben a görbe elárulná a
  friss kötéseket.
- A kötéslistában csak ticker, jelzés- és kilépési dátum, nettó %, R és kilépési ok van. Belépő- és kilépőár
  nincs, nyers árfolyam-chart sincs.
- Ha a stratégia univerzuma a watchlist, a ticker maszkolva jelenik meg.

### Megjelenítés (`render.py`)
- **`/s/<slug>`:** szerveroldali HTML, JavaScript nélkül; a tőkegörbe inline SVG.
  - A figyelmeztetés a lap **tetején és alján**, a felület nyelvén. Alapból magyarul, `?lang=en` angolul.
  - A szöveg a katalógusból jön, és nem kapcsolható ki.
- **`/embed/<slug>`:** kompakt widget. Az angol figyelmeztetés látható szövegként szerepel (nem lábjegyzetként),
  és a widget legalább ekkora méretben jelenik meg.
- **`/og/<slug>.png`:** plotly-ábra, kaleidóval renderelve, 1200×630.
  - Az angol figyelmeztetés az ábra szerves része (nagy betűs annotáció), így a kép kivágás nélkül nem terjedhet
    felirat nélkül: „Not financial advice. Past / simulated performance does not guarantee future results.”
  - Ha a renderelés nem sikerül, egy előre elkészített, a figyelmeztetést tartalmazó általános kép szolgálódik ki.

A HTML és a PNG a publikáláskor és a napi frissítéskor készül el, fájlba. A nyilvános útvonal **csak fájlt
olvas**, számolni nem számol.

### Biztonság
- A nyilvános útvonalak (`/s/`, `/embed/`, `/og/`) egy külön, hitelesítés nélküli ágon futnak a `do_GET`
  elején, csak GET-tel.
  - Nem olvasnak sütit, nem állítanak be sütit, és csak a `data/publish/` alatti fájlokat nyitják meg.
  - A slug formátumát regex ellenőrzi, a slug egy publikációs térképben van (`slugs.json`). Útvonal nem épül
    felhasználói bemenetből.
- **Rate limit:** IP-címenként 60 kérés / perc (token-vödör, memóriában, legfeljebb 10 000 IP), túllépéskor 429.
- **Cache:** `Cache-Control: public, max-age=300`.
- **Fejlécek:**
  - a `/s/` és az `/og/` CSP-je `frame-ancestors 'none'`;
  - csak az `/embed/` engedi a beágyazást (`frame-ancestors *`);
  - a script mindenhol tiltva.
- **Főkapcsoló:** `publish.enabled` (alapból **false**) → minden nyilvános útvonal 404, és publikálni sem lehet.
  Csak engedélyezett adatforrás mellett kapcsolható be (9. fejezet, 9. pont).
- **Publikálás:** `POST /api/publish` megerősítéssel (`confirm: true` + a felületen előnézet).
  - Visszavonás: `DELETE /api/publish/<slug>`. A fájlok és a térkép-bejegyzés törlődnek, utána 404.
- **Fork:** `POST /api/publish/<slug>/fork`, csak belépve. A `sandbox_adopt` meghívása, csak paraméter-másolás,
  megerősítéssel.
- **Rövid URL:** 10 karakteres véletlen base32 slug (`/s/k3m9x2qa7d`).

### Tárolás és plafon
- `data/publish/slugs.json` (slug → munkaterület).
- `data/users/<ws>/publish/<slug>/`: `meta.json`, `snapshot.json` (≤ 200 KB), `page_hu.html`, `page_en.html`,
  `embed.html`, `og.png`.
- Munkaterületenként legfeljebb 10 oldal, összesen 20 MB; bekerül a `housekeeping`-be és a `data-usage`-be.

### Tesztek
- A szerkesztett beállításokba, a `.env`-be és a watchlistbe „titok”-jelölőket ültetve egyik sem jelenik meg
  egyik nyilvános válaszban sem.
- Nincs nyitott pozíció, nincs késleltetésen belüli kötés, nincs nyers ár (a válasz számai között nincs a papír
  egyetlen záróára sem).
- Az `/og/` render bemenetén (a plotly-ábra annotációiban) szerepel az angol felirat. Minden `/s/` és `/embed/`
  válasz tartalmazza a figyelmeztetést.
- A visszavont oldal 404-et ad. Rate limit → 429.
- A nyilvános válasz nem állít be sütit, és a CSP a fenti.

### Kockázatok
- **Adatlicenc:** az oldalak yfinance-adatból számolt statisztikát mutatnak nyilvánosan (SAAS-DESIGN 3.1).
  Döntés: a funkció `publish.enabled: false` mögött készül el, és csak engedélyezett adatforrással kapcsolható be;
  addig a tesztek szintetikus adattal futnak.
- A kaleidónak Chromium kell. ARM-on ez a monitor meglévő chart-képeinek útvonala. A labor 450 MB-os
  memóriaplafonja szűk lehet, ezért az OG-kép a webes folyamat háttérszálában készül, nem a laborban.
- Helyben (Windows, `127.0.0.1`) a nyilvános oldal csak a saját gépről érhető el. Valódi megosztáshoz a
  szerveres telepítés és a domain kell.

---

## 7. Lemez-plafonok összesítve

| Mappa | Plafon | Takarítás |
|---|---|---|
| `data/users/<ws>/digest/` | 90 nap (~5 MB) | housekeeping |
| `data/users/<ws>/agent/` | 30 futás, 50 MB | saját `prune` + housekeeping |
| `data/users/<ws>/events/` | 30 tanulmány, 30 MB | saját `prune` |
| `data/events/` (Form 4, indexváltozások) | 50 MB | évenkénti fájlok, a legrégebbi törlődik |
| `data/users/<ws>/practice/` + `data/practice/` | 100 kör + 5 MB | saját `prune` |
| `data/publish/` + `data/users/<ws>/publish/` | 10 oldal / munkaterület, 20 MB | visszavonáskor törlés |
| `data/queue/` | 10 várakozó / munkaterület, a kész 7 nap után törlődik | `labqueue.prune` |
| `data/users/<ws>/usage.json` | 24 hónap | felülírás |

Összesen legfeljebb ~170 MB, a `data-usage` minden sort külön mutat.

---

## 8. Összesített kockázatok

1. **Ajánlásnak tűnő szöveg.** Az új sablonokat tiltólista-teszt védi. A meglévő értesítésekben viszont ma is
   van „VÉTELI LIMIT” szöveg (9. fejezet, 12. pont: külön lépés, W).
2. **Szűk szerver.** 1 mag, a labor 450 MB-os plafonnal. Az ügynök és a nagy eseménytanulmány keretből fut, és
   a reggeli határidő miatt kemény időkorlátja van. A napközbeni webes kérés soha nem indít nehéz számítást.
3. **Reprodukálhatóság.** A korrigált múltbeli árak osztaléknál változnak. Minden jelentés tartalmazza az adat
   ujjlenyomatát és a kódverziót.
4. **Holdout-erózió.** A holdoutot semmi nem nyitja meg automatikusan. A validációs időszak megnézéseit is
   számoljuk (B), mert sok futás után az sem független.
5. **SaaS-előkészítés félúton.** A munkaterület-paraméter megvan, de a watchlist és a Saját pozíciók még
   globálisak. Az admin munkaterület működik, a többi `NotImplementedError` a SaaS 1. fázisáig.

---

## 9. Döntések (2026-10-17)

A felhasználó válaszai. Ahol nem volt válasz, a terv javaslata érvényes; ezek „(javaslat szerint)” jelölést kaptak.

1. **Munkaterület-könyvtár.**
   - A **felhasználóhoz tartozó új adat** a `data/users/admin/…` alá kerül: briefek, ügynök-futások és
     -jelentések, reggeli összefoglalók, gyakorló körök, publikált oldalak.
   - A **közös piaci adat** a közös `data/` alatt marad: Form 4, indexváltozások, eseménykatalógus, árfolyamok.
   - A meglévő modulok most **nem** költöznek (ez a SaaS 1. fázisa).
   - Az elérési utat egyetlen segédfüggvény adja (`workspace.user_dir`), hogy később egy helyen lehessen cserélni.
2. **Az ügynök célmutatója:** az átlagos nettó hozam bootstrap alsó határa (`ev_lo`).
   - A nyerési arány Wilson-alsó határa a briefben **opcionális feltétel** (`min_win_lo`, pl. 0,65), nem célmutató.
   - Ok: a nyerési arány önmagában tág stoppal és kis céllal felfelé tornázható, miközben a várható érték
     romlik (lásd REVIEW-2026-10).
   - A jelentésben mindkettő látszik. A briefből a `rank_by` mező kimarad.
3. **Napközbeni cél.** A `meanrev.simulate_trade` opcionális `target` és `target_intraday=False` paramétert kap;
   alapból a mai viselkedés bitre azonos, és erre regressziós teszt van. Bekapcsolt módban:
   - ha ugyanazon a gyertyán a stop és a cél is elérhető, a stop teljesül;
   - ha a nyitó a cél fölött van, a nyitóáron teljesül.
   A stratégiák backtesztje és a kutatás az alapértelmezést használja, csak a gyakorló mód kapcsolja be.
4. **A gyakorló mód adatforrása:** a `backtest_page` tára és az élő univerzum-cache is, a tár elsőbbséggel
   (javaslat szerint).
5. **Múltbeli Form 4:**
   - a múlt a SEC negyedéves csomagjaiból jön, papíronkénti XML csak az élő frissítéshez;
   - ahol csak a beadás dátuma ismert, az esemény a **következő kereskedési nap nyitásától** számít ismertnek;
   - az élő frissítésnél az elfogadás időpontja az `edgar.accepted_utc` szerint számít;
   - a tanulmányban látszik, melyik eseménynél volt csak dátum;
   - kéréskorlát: legfeljebb 10/s (a gyakorlatban 8/s, folyamatok között közösen), User-Agent.
6. **Gyorsjelentés időpontja:** az időpont is a cache-be kerül (új mező, visszafelé kompatibilis). Ahol nem
   ismert, a 0. nap a jelentés utáni kereskedési nap (javaslat szerint).
7. **S&P 500 változások:** a hatályos dátum a Wikipedia-táblából, a felület kiírja, hogy a bejelentési hatás
   nincs benne (javaslat szerint).
8. **Szektor-adat:** az `indexes.py` elmenti a GICS-szektort is (javaslat szerint).
9. **Nyilvános oldalak yfinance-adatból: NEM.**
   - A funkció elkészül és tesztelt, de `publish.enabled: false` kapcsoló mögött, ami alapból ki van kapcsolva.
   - Akkor kapcsoljuk be, ha lesz engedélyezett adatforrás (IEX HIST írásos engedéllyel, vagy fizetős licenc).
   - Addig a tesztek szintetikus adattal futnak.
10. **Egy homokozó:** az átvétel és a Fork felülírja, megerősítéssel, és a régi paraméterek 10 elemes
    előzménylistába kerülnek (javaslat szerint).
11. **Ticker a nyilvános oldalon:** watchlist-univerzumnál maszkolva (javaslat szerint).
12. **A meglévő értesítések szövege: IGEN**, külön lépésben (W), **még az első külső felhasználó előtt**:
    - „VÉTELI LIMIT” helyett: „Szabály szerinti belépési szint (a backteszt feltételezése)”;
    - „ne vegyél utána” helyett: „A szabály e szint fölött nem számol belépést”;
    - minden felszólító mondat leíró formára, pl. „a szabály szerint itt a pozíció fele zárul”, „a szabály
      szerint a stop a belépőre kerül”;
    - minden értesítés végén: „Nem befektetési tanács. Szimulált / szabályalapú jelzés.”
13. **Reggeli összefoglaló Discordon:** alapból kikapcsolva, a felületen egy kapcsolóval (javaslat szerint).

---

## 10. Megvalósítási sorrend

| Lépés | Tartalom | Kész, ha |
|---|---|---|
| 0 | Közös alapok: `workspace`, `usage`, `labqueue`, őr-tesztek, az `alpaca.py` import-javítása | az őr-tesztek zöldek a mai kódon — **kész** (a `labqueue` a B-vel) |
| A | Reggeli összefoglaló + README-szakasz | `digest --print` valós adaton, tesztek zöldek — **kész** |
| B1 | Ügynök: brief, tér, keresés, CLI, Markdown-jelentés | paritás, determinizmus, keret, holdout tesztek zöldek — **kész** |
| B2 | Ügynök: web és Homokozó-átvétel | átvétel: `alerts=false`, a paper-kötés nem változik — **kész** |
| B3 | Ügynök: Kitörés-motor | `lab.evaluate(until=…)` tesztelve — **kész** |
| C | Eseménytanulmányok (először a meglévő adatból, utána Form 4 és indexváltozások) | ismert CAR, point-in-time, parser, EDGAR-korlát — **kész** |
| D | Gyakorló mód | szivárgás- és szimuláció-egyezés teszt — **kész** |
| E | Megosztható oldalak (kikapcsolt főkapcsolóval) | a nyilvános válasz-tesztek szintetikus adaton, figyelmeztetés minden felületen, 404 visszavonás után |
| W | A meglévő értesítések leíró szövegezése (9. fejezet, 12. pont) | tiltólista-teszt a meglévő értesítésekre is; még az első külső felhasználó előtt |

Minden lépés végén: `pytest` zöld, README-szakasz, és rövid összefoglaló arról, mi készült el és mi maradt.

*Nem befektetési tanács.*
