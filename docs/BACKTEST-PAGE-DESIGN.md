# „Backteszt” oldal – egy papír vagy ETF teljes története, bármilyen stratégiával

Állapot: **terv**, még nincs implementálva. Ticket: [#21](https://github.com/vkbubu11/StockWatch/issues/21).

## 1. Cél

A felhasználó kiválaszt egy papírt vagy ETF-et (pl. `SPY`, `NVDA`) és egy kezdődátumot (pl. `2000-01-01`). A rendszer
**összegyűjti az összes szükséges adatot visszamenőleg** eddig a dátumig, és ezen **bármelyik stratégiára** backtestet
futtat:

- a meglévőkre (Kitörés, Visszahúzás, Mély visszahúzás, ETF visszahúzás, Homokozó), akár módosított paraméterekkel;
- egy saját, a felületen összerakott szabályra;
- mindig összevetve a „vettem és tartottam” eredménnyel.

Miben más, mint a meglévő eszközök:

| | `meanrev-research` / `entry-research` | Labor | **Backteszt oldal (új)** |
|---|---|---|---|
| Minta | sok papír, fix 10 év | univerzum, folyamatos | **egy papír, tetszőleges kezdődátum** (akár 30 év) |
| Stratégia | a saját motorja | Kitörés-változatok | **bármelyik motor + saját szabály** |
| Kimenet | aggregált statisztika | kísérlet-összefoglaló | **kötéslista, tőkegörbe, chart a kötésekkel, összevetés** |
| Cél | validálás | kutatás | **megértés és feltárás** („hogyan viselkedett ez a stratégia ezen a papíron 2008-ban?”) |

## 2. Alapelvek (a rendszer meglévő szabályaiból)

1. **Egyetlen igazságforrás.** A backtest ugyanazokat a függvényeket hívja, mint az élő futás és a kutatás:
   `meanrev.features / setup_mask / exit_flags / simulate_trade`, a Kitörésnél `entry_research` szimulációja
   (másnapi nyitás, gap-limit, csúszás, pesszimista kilépés). **Nincs külön „oldal-backtest” logika.**
2. **Nincs jövőbe látás.** Csak a jelzés napjának záróig ismert adat. A rezsim, a gyorsjelentés-tilalom és a hírek is point-in-time.
3. **A holdout védett.** Alapból a vizsgált időszak vége `HOLDOUT_START − 1 nap` (2025-03-31). A teljes időszak egy kapcsolóval nyitható meg. Ilyenkor a megnyitás bekerül a `data/research/holdout_log.jsonl` naplóba (`study="backtest-page"`), és az eredményen jól látható jelzés figyelmeztet, hogy a holdoutot is tartalmazza.
4. **Őszinte statisztika.** Egy papír kis minta: a nyerési arány mellett mindig ott a Wilson-intervallum, 30 kötés alatt figyelmeztetés jelenik meg („kevés kötés, az eredmény főleg zaj”). A sok paraméter-próbálgatásra is figyelmeztet a felület: a futtatások száma papíronként látszik.

## 3. Adatgyűjtés („Adatok összegyűjtése” gomb)

Egy papírra és kezdődátumra a rendszer ezeket tölti le és tárolja:

| Adat | Forrás | Miért kell | Megjegyzés |
|---|---|---|---|
| Napi OHLCV (korrigált és nyers) | yfinance (`start=`, `auto_adjust` külön mindkettő) | jelzés (korrigált), végrehajtási ár és osztalék nélküli hozam (nyers) | az élő `store` 10 éves plafonja **nem** vonatkozik rá: külön tár |
| Osztalékok, splitek | yfinance `actions` | teljes hozam, a split-napi ugrások felismerése | |
| Rezsim-index ugyanarra az időszakra | `^GSPC` (1927-től), `QQQ` (1999-től), a papír saját trendje | `market.MarketContext` rezsimje | ha az index rövidebb, a hiányzó napokon a rezsim „nem ismert”, és ez naplózva van |
| Benchmark | `SPY` (1993-tól) vagy `^GSPC` | összevetés, alfa / béta | |
| Gyorsjelentési dátumok | `market.HistoricalEarnings` (~100 dátum / papír) | earnings-tilalom | régebbi időszakra hiányos lehet → a kimaradó tilalom a riportban jelölve |
| VIX (opcionális) | `^VIX` (1990-től) | rezsimszűrők, ld. ticket #11 | |
| Hírek (később) | ld. #20 (sentiment) | hírszűrő | csak 2015-től |

**Tárolás:**
- `data/backtest/<SYMBOL>/ohlcv.csv.gz`, `actions.csv.gz`, `meta.json` (forrás, letöltés ideje, első és utolsó nap, adatminőség);
- plafon: összesen 300 MB, a legrégebben használt papír törlődik először (a `housekeeping`-be kötve).

**Adatminőség-jelentés** (a letöltés után mindig látszik):
- az első elérhető nap (ha későbbi a kértnél: „az NVDA 1999-01-22-től elérhető”);
- hiányzó kereskedési napok a tőzsdei naptárhoz képest (`pandas_market_calendars`);
- nulla volumenű napok;
- split nélküli, 30%-nál nagyobb ugrások;
- a korrigált és a nyers ár aránya (osztalék-korrekció).

Probléma esetén a futtatás nem tiltott, de az eredmény fölött figyelmeztetés jelenik meg.

**Frissítés:** ha az adat már megvan, csak a hiányzó elejét vagy végét tölti le. Ha a múltbeli korrigált árak megváltoztak (osztalék-korrekció), az egészet újratölti. Ez ugyanaz az elv, mint a `lab.history_intact`.

## 4. Stratégia kiválasztása és beállítása

**A) Meglévő stratégia**
- Legördülő lista: Kitörés, Visszahúzás (RSI2), Mély visszahúzás, ETF visszahúzás, Homokozó.
- Az aktuális paraméterei betöltődnek, és a felületen módosíthatók. A Visszahúzás-családnál ez a `PullbackParams`, a Kitörésnél az `EntrySettings` szerkeszthető része (`lab.EDITABLE`), ugyanazzal a validációval (`PullbackParams`, `lab.validate_entry`).
- A módosítás **nem** írja át a stratégia éles beállítását. Ehhez külön gomb van: „Mentés a Homokozóba”.

**B) Saját szabály (szabályszerkesztő)**

Feltételsorok ÉS / VAGY kapcsolattal, engedélyezett elemekből:

```
Belépés (záron):   RSI(2) < 5   ÉS   Záró > SMA(200)   ÉS   Záró < Bollinger alsó(20, 2)
Megbízás:          limit a záró −2%-án, egynapos   |   másnapi nyitás   |   záró (MOC)
Stop:              3 × ATR(10)   |   −8%   |   nincs
Kilépés (záron):   RSI(2) > 70   VAGY   Záró > SMA(5)      → másnap nyitáskor
Időkorlát:         10 nap
```

- **Elemek:**
  - indikátorok az `indicators.py`-ból: SMA, EMA, RSI, ConnorsRSI, ATR / NATR, Bollinger, MACD, ADX, OBV;
  - árak: nyitó, záró, minimum, maximum;
  - n-napos csúcs és mélypont, egymást követő eső és emelkedő napok, volumen-arány, a rezsim igaz/hamis értéke.
- **Biztonság:** a szabály JSON-ként tárolt **adatszerkezet** (műveletfa), nem kód. A szerver egy fix műveleti táblából értékeli ki, nincs `eval`, és a paraméterek tartománya ellenőrzött.
- **Végrehajtás:** a saját szabály is a `meanrev.simulate_trade` motoron fut. A belépési és a kilépési maszk a szabályból jön, így ugyanaz a pesszimista végrehajtás érvényes (stop a jel előtt, kilépés a következő nyitáson).
- A jól működő saját szabály egy gombnyomással Homokozó-stratégia lehet, hogy élőben is mérhető legyen. Ehhez a Homokozónak kell egy `engine: "rule"` mód.

**C) Közös beállítások:**
- időszak (kezdet, vég, alapból a holdout előtt);
- kezdőtőke, kockázat kötésenként vagy fix összeg / fix %;
- költség bps / oldal (alap: a stratégiáé), csúszás;
- osztalék beszámítása igen / nem;
- benchmark.

## 5. Eredmény-oldal

Vázlat – **a számok csak illusztrációk, nem mért eredmények**:

```
┌ SPY · Mély visszahúzás (stop 5 ATR) · 2000-01-03 → 2025-03-31 · költség 5 bps ──────── [CSV] [Mentés] [Összevetés] ┐
│ ⚠ 38 kötés – kevés minta.  Adat: rendben.  Holdout: rejtve.                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Kötés 38 · Nyerési arány 76% (61–87%) · Átlag +1,9% · PF 3,1 · CAGR +2,4% · Max DD −6,1% · Piacon töltött idő 4%    │
│ Vettem és tartottam: CAGR +7,8% · Max DD −55% · Sharpe 0,48            Stratégia: Sharpe 0,71                    │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Ár-chart: gyertyák + belépés ▲ / kilépés ▼ jelölők, stop- és limitvonal a kiválasztott kötésnél; nagyítható]    │
│ [Tőkegörbe: stratégia vs. vettem és tartottam (log skála) · alatta visszaesés-sáv]                               │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Időszakok: Tanító (<2022) │ Validáció (2022–2025.03) │ (Holdout – rejtve)      Évenként: hőtérkép havi hozammal  │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Kötések: jelzés napja és ideje │ belépés │ ár │ kilépés │ ár │ ok (jel/stop/idő) │ nap │ nettó % │ R │ MAE │ MFE    │
│  → sorra kattintva a chart a kötésre ugrik; a kiinduló jelzés szűrőinek értékei is látszanak                   │
├────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Érzékenység (opcionális): a fő paraméterek ±1 lépéses szomszédsága egy rácsban (nyerési arány / átlag)          │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Mutatók:** ugyanaz a modul számolja, mint a teljesítmény-analitikát (#7): CAGR, Sharpe / Sortino / Calmar, max. visszaesés és annak hossza, kitettség, nyerési arány Wilson-intervallummal, profit-faktor, átlagos nyerő és vesztes, a kilépési okok megoszlása, MAE / MFE (#14).
- **Mentett futtatások és összevetés:** legfeljebb 50 futtatás (`data/backtest/runs/<id>.json.gz`), kettő-négy egymás mellé tehető (tőkegörbe és mutatók), és mindegyik permalinkkel újranyitható (`#/backtest/run/<id>`).
- **Export:** a kötéslista és a tőkegörbe CSV-ben.

## 6. Technikai felépítés

**Új modulok:**
```
src/stockwatch/backtest_page/
  data.py        letöltés, tár, adatminőség, frissítés (feed / providers újrahasznosítva, külön könyvtárba)
  rules.py       saját szabály: séma (pydantic), kiértékelés maszkká, validáció
  runner.py      egy futtatás: adat → jellemzők → jelzések → simulate → mutatók → riport (a meglévő motorokkal)
  runs.py        mentett futtatások, lemezplafon, összevetés
```

**API** (`webapp.py`, a meglévő hitelesítéssel):
| Végpont | Leírás |
|---|---|
| `POST /api/backtest/data` `{symbol, start}` | adatgyűjtés indítása (háttérfeladat, a meglévő `renderJob` / `pollJob` mechanizmussal) |
| `GET /api/backtest/data/<SYMBOL>` | elérhető időszak, adatminőség |
| `POST /api/backtest/run` `{symbol, start, end, strategy, params, rule, costs, sizing, include_holdout}` | futtatás (háttérfeladat) |
| `GET /api/backtest/run/<id>` | eredmény |
| `GET /api/backtest/runs`, `DELETE /api/backtest/run/<id>` | lista, törlés |

**Futtatás helye:**
- Egy papír napi adata 30 évre is csak ~7 500 gyertya. A Visszahúzás-motor ezt másodpercek alatt lefuttatja, ezért a webes folyamat egy háttérszálban futtatja, papíronkénti zárral, időkorláttal (60 s) és egyidejűségi korláttal (1 futás).
- A Kitörés-motor (alakzatfelismerés teljes történetre) lassabb. Ezt a meglévő labor-szolgáltatás futtatja, a homokozó kérésének mintájára (`request.json` / `status.json`), alacsony prioritással.

**CLI** (ugyanaz a `runner`):
```bash
python cli.py backtest-symbol SPY --start 2000-01-01 --strategy deep --set stop_atr=4 --cost-bps 5
python cli.py backtest-symbol NVDA --start 2005-01-01 --rule rules/my_rule.json --include-holdout
```

**Felület:**
- új fül: „Backteszt” (`#/backtest`) a Kutatás és a Labor között;
- a papír-oldalon gomb: „Backteszt erre a papírra”;
- a chart a meglévő `charting` színeit és jelölőit használja.

## 7. Tesztek
- Ugyanarra az adatra és paraméterre a Backteszt oldal kötései **pontosan egyeznek** a `meanrev_research.candidates()` kötéseivel (egyetlen igazságforrás – regressziós teszt).
- A szabályszerkesztő: ismeretlen művelet vagy tartományon kívüli paraméter → elutasítva; egy RSI2-szabály a beépített Visszahúzással azonos maszkot ad.
- A holdout kapcsoló nélkül nincs 2025-04-01 utáni kötés az eredményben; kapcsolóval van, és a megnyitás naplózva van.
- Adatminőség: a szintetikus hiányzó napot, split-ugrást és rövidebb rezsim-indexet felismeri.
- A lemezplafon és a futtatásszám-korlát működik.

## 8. Ütemezés

| Fázis | Tartalom |
|---|---|
| **1** | `data.py` + adatminőség + `POST/GET /api/backtest/data` + CLI letöltés |
| **2** | `runner.py` a Visszahúzás-családdal (pullback, deep, etf, sandbox-pullback), paraméter-felülírással; eredmény-oldal: mutatók, kötéslista, tőkegörbe, ár-chart jelölőkkel |
| **3** | Kitörés-motor a labor-szolgáltatáson keresztül; mentett futtatások, összevetés, CSV export |
| **4** | Saját szabályszerkesztő (`rules.py`) + „Mentés Homokozóba” (`engine: "rule"`) |
| **5** | Érzékenységi rács, VIX / hír-szűrők bekötése (#11, #20), teljesítmény-mutatók közös modulja (#7) |

## 9. Korlátok, amiket a felület kiír
- A yfinance-adat nem hivatalos, a régi időszakokra hiányos lehet (ld. #15).
- Egy papír nem bizonyít stratégiát: a validáláshoz továbbra is a több papíros kutatás (`meanrev-research`, Labor) a mérvadó.
- A ma létező papír múltja **túlélési torzítást** hordoz: amelyik csődbe ment, azt nem lehet kiválasztani.
- A régi időszakok költségei (szélesebb spread, nagyobb jutalék) a mai `cost_bps`-nél nagyobbak voltak. A 2005 előtti időszakra a felület magasabb alapköltséget javasol.

*Nem befektetési tanács.*
