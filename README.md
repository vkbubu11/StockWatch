# StockWatch — NASDAQ chart formáció figyelő

Folyamatosan figyeli a saját watchlistedet (és kérésre a teljes Nasdaq-100-at és S&P 500-at), felismeri a klasszikus chart
formációkat, meghatározza a support/rezisztencia szinteket, és irányra vonatkozó
előrejelzést ad — **valós, backtestelt találati aránnyal**, nem kitalált százalékokkal.

> ⚠️ **Nem befektetési tanács.** Ez egy technikai elemző eszköz. A chart formációk
> nem garantálnak semmit, és a tőzsdén pénzt lehet veszíteni.

---

## Mit tud

| Képesség | Részletek |
|---|---|
| **Formációfelismerés** | Dupla/tripla alj és tető, fej-váll és fordítottja, emelkedő/csökkenő/szimmetrikus háromszög, emelkedő és eső ék, bull/bear zászló, téglalap, csésze-fül |
| **Support/rezisztencia** | 5 független forrásból (swing pivotok, volumen profil, mozgóátlagok, kerek számok, periódus-szélsőértékek), klaszterezve és 0–100 pontszámmal |
| **Előrejelzés** | 5 komponensű kompozit pontszám (formáció, trend, momentum, szintek, volumen), **komponensenkénti indoklással** |
| **Valószínűség** | Kizárólag historikus backtestből. Ha nincs elég minta, azt írja ki, hogy „nincs elég adat" |
| **Swing long belépési jelzés** | Kitörés / visszateszt / előkészület felismerése, 8 kemény szűrő, 0–100 pontszám indoklással, kész kereskedési terv (belépő-limit, stop, cél, darabszám) |
| **Saját pozíciók** | A brókernél meglévő pozíciók kézi felvétele; kiértékelés (Rendben / Figyelj / Kockázatos) okokkal, stop- és cél-javaslat, értesítés stopnál, célnál, romló képnél és jelentés előtt |
| **Piaci környezet** | rezsim papíronként (Nasdaq-100 tag → QQQ, csak S&P 500 tag → S&P 500 index, egyéb → a papír saját trendje), relatív erő, earnings-tilalom |
| **Több stratégia** | Kitörés, Visszahúzás (RSI2), Homokozó – mindegyik jelez és virtuálisan követ; a paper fiókhoz egy köthető, átváltható |
| **Riasztás** | Discord (színes kártya chart képpel), Telegram (chart képpel), Google Chat / Slack webhook, konzol, rotáló JSONL napló |
| **Felület** | Streamlit dashboard: áttekintés, részletes chart, jelzés-előzmények, backtest tábla |

---

## Telepítés

```bash
cd Stock                     # a letöltött / klónozott mappa

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Discord értesítés (ajánlott)

A jelzések színes kártyaként érkeznek egy Discord-csatornába, alattuk a chart képével
(zöld: belépés / cél, piros: stop / bukás, sárga: figyelendő, kék: paper trading esemény).

1. Discordon: **Szerver beállítások → Integrációk → Webhookok → Új webhook**, válaszd ki a csatornát, majd **Webhook URL másolása**
2. A webes felületen: **Beállítások → Discord értesítések – webhook** → illeszd be az URL-t → *Mentés és ellenőrzés*.
   Vagy parancssorból: `python cli.py discord` (szerveren: `sudo stockwatch-cli discord`); a beírt szöveg nem látszik.
3. Mentés előtt a rendszer ellenőrzi a webhookot a Discordnál, elmenti a titokfájlba, és kérésre próbaüzenetet küld.
4. A webhook ugyanígy **cserélhető vagy törölhető**. A futó monitor minden küldés előtt a titokfájlból olvassa a címet,
   ezért a csere a következő értesítéstől érvényes, újraindítás nélkül.

A webhook URL maga a jelszó: aki ismeri, írhat a csatornába. Ezért csak a titokfájlba kerül, a naplóba soha.
Ha kiszivárgott, töröld a webhookot a Discordban, és állíts be újat.

**Napi életjel.** Minden hétköznap zárás után (16:15 New York, kb. 22:15 magyar idő) rövid összefoglaló érkezik:
hány papírt elemzett, friss-e az adat, hány értesítés ment ki, a követett és a paper trade-ek, a paper számla
egyenlege, a nap hibái és a lemezhasználat. Zöld kártya: minden rendben; piros: valami hibára futott vagy nem friss
az adat (a címben, hogy mi). **Ha egy hétköznap este nem jön meg, a monitor vagy a szerver áll.**
A monitor indulásakor is jön egy kék üzenet, így látod, ha a szerver vagy a szolgáltatás újraindult
(félóránként legfeljebb egyszer). **Ha a monitor nem futott a záráskor** (leállt, szerver-újraindítás),
induláskor azonnal pótolja a legutóbbi kereskedési nap zárás utáni futását: belépési jelzés, Discord-értesítés,
automatikus paper beküldés — az életjelen „Pótlólagos futás” sorral. Friss telepítés után az első rendes
16:15-ös futásig nincs pótlás (ismeretlen előzményre nem küld késői megbízást). Kézzel bármikor: `python cli.py heartbeat` (szerveren: `sudo stockwatch-cli heartbeat`).
Kikapcsolás: `alerts.heartbeat: false`.

### Telegram beállítása (opcionális, a `settings.yaml`-ban: `alerts.telegram: true`)

1. Írj a **@BotFather**-nek Telegramon → `/newbot` → kapsz egy tokent
2. Írj egy üzenetet a saját botodnak
3. Nyisd meg: `https://api.telegram.org/bot<TOKEN>/getUpdates` → innen olvasd ki a `chat.id` értéket
4. Hozz létre egy `.env` fájlt a projekt gyökerében:

```
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

Ellenőrzés: `python cli.py test-alert`

A `.env` fájl a `.gitignore`-ban van — a token soha nem kerül verziókezelésbe vagy naplóba.

---

## Első használat

```bash
# 1. Adatok letöltése (10 év napi gyertya, ~1,5 MB)
python cli.py fetch --symbols all

# 2. Kalibráció — ez adja a valós találati arányokat (~6 perc)
python cli.py backtest --symbols all --walk-forward

# 3. Nézd meg, mit talál
python cli.py scan --symbols all
python cli.py entries --all          # mai swing long belépési jelöltek, okokkal

# 3b. Bővített minta és a szabályok történeti tesztje
python cli.py fetch-universe
python cli.py entry-research --universe

# 4. Webes felület (böngészőben nyílik meg)
python cli.py web

# 5. Folyamatos figyelés
python cli.py monitor
```

---

## Parancsok

| Parancs | Mit csinál |
|---|---|
| `fetch [--symbols X,Y] [--full]` | Árfolyam letöltése, inkrementálisan |
| `scan [--symbols X,Y] [--plot]` | Elemzés; `--plot` HTML chartokat ment a `charts/` mappába |
| `levels --symbol AAPL` | Support/rezisztencia szintek részletesen |
| `entries [--all] [--details] [--offline]` | Mai swing long belépési pontok; `--all` az elbukott jelölteket is mutatja okokkal, `--details` a teljes tervet |
| `entry-backtest [--universe] [--csv out.csv] [--holdout]` | A jelenlegi beállítás történeti eredménye a szűretlen kitörésekhez és az earnings-tilalom nélküli változathoz képest, 95%-os intervallumokkal |
| `entry-research [--universe] [--csv r.csv] [--holdout] [--study [--only N]]` | 192 szabályváltozat összehasonlítása, tanító / validáció / zárolt holdout bontásban; `--study`: az előre rögzített momentum- és erősségszűrő-vizsgálat |
| `fetch-universe` | A 106 papíros kutatási minta + QQQ letöltése |
| `positions` | A jelzett trade-ek követése és élő eredménye |
| `holdings [add\|close\|set-stop\|remove]` | Saját pozíciók felvétele és kiértékelése (lásd lent) |
| `broker [status\|sync]` | Alpaca paper számla és a paper trade-ek (lásd lent) |
| `index-scan [--nasdaq100] [--sp500] [--all] [--alerts]` | A teljes Nasdaq-100 / S&P 500 átnézése (lásd lent) |
| `ledger [--decision X] [--symbol Y]` · `trace <jel-id>` · `replay <jel-id>` | Jelzésnapló, egy jelzés életútja, visszajátszás (lásd lent) |
| `web [--port 8765] [--no-browser] [--behind-proxy]` | Webes felület a böngészőben; `--behind-proxy` szerveren, HTTPS proxy mögött (jelszó kötelező) |
| `web-password [--disable]` | A webes felület jelszava (lásd: Futtatás szerveren) |
| `backtest [--walk-forward] [--purge-cache]` | Historikus kalibráció |
| `monitor [--once] [--dry-run]` | Folyamatos figyelés; `--once` egy ciklus, `--dry-run` nem riaszt |
| `data-usage` | Lemezhasználat kimutatás |
| `housekeeping` | Takarítás kézi futtatása |
| `test-alert` | Telegram / Discord kapcsolat ellenőrzése, próbaüzenet chart-képpel |
| `discord [--disable]` | Discord értesítés beállítása (webhook) |
| `lab [status] [--once] [--workers N]` | Stratégia-labor: folyamatos stratégiateszt múltbeli adatokon (lásd lent) |
| `heartbeat [--print]` | Életjel / állapotjelentés most a Discordra (`--print`: csak kiírja) |
| `event-study --type earnings --universe sp500 [--window -10 60] [--csv out.csv]` · `event-sources --index-changes --form4-since 2017` | Eseménytanulmányok és a forrásadatuk (lásd lent) |
| `agent run --brief briefs/x.yaml` · `agent queue --brief … [--night]` · `agent list` · `agent report --run-id …` | Kutatóügynök: stratégiaváltozatok keresése egy kutatási cél szerint (lásd lent) |
| `digest [--print] [--no-refresh] [--date YYYY-MM-DD]` | Reggeli összefoglaló most: a watchlist és a Saját pozíciók változásai (lásd lent) |
| `practice-pool [--queue] [--force]` | A Gyakorló mód feladatkészletének felépítése (lásd lent) |

---

## Swing long belépési jelzések

A formációfelismerő megmondja, *mi* van a charton. A belépési motor (`entry.py`) azt,
hogy **most van-e jó long beszállás** — és ha igen, pontosan hogyan.

> A program **valódi pénzzel semmit nem vásárol**. Értesít; kereskedni csak egy Alpaca **paper**
> számlán tud, ha beállítod (lásd: *Paper trading*).

### Belépési helyzetek

| Helyzet | Mikor | Mit jelent |
|---|---|---|
| 🟢 **MEGERŐSÍTETT KITÖRÉS** | a bikás minta *tegnap* zárt először a trigger fölött, és a *mai* záró is fölötte maradt | belépő: másnap nyitáskor, limit = záró +1% |
| 👀 **ELŐKÉSZÜLET** | a minta még alakul, az ár ≤1 ATR-re a triggertől | *még nem vétel* — figyelmeztetés |
| 🟢 VISSZATESZT *(alapból ki)* | kitörés után visszajött a szinthez, megtartotta, felfelé fordult | `alert_retests: true` |

A megerősítő nap miatt kevesebb a jelzés, de a hamis kitörések nagy része kiesik
(lásd a kutatást lent). `confirm_breakout: false` → már a kitörés napján jelez.

### Kilépés: a pozíció fele +1R-nél

Ez emelte a legtöbbet a nyerési arányon. Minden jelzés kész kilépési tervet ad:

1. **A pozíció fele eladva +1R-nél** (1R = belépő − stop), érdemes rá előre limit megbízást adni
2. **Utána a stop a belépőre kerül**: onnantól a trade veszteség nélkül zárul
3. **A maradék a mintából számolt célig** fut, vagy időkorlátig

A rendszer a jelzett trade-eket **napi zárón virtuálisan követi**, és szól:
📥 belépés megtörtént · ⏭️ nem volt belépés (a nyitó a limit fölött) · 💰 +1R: add el a felét,
húzd a stopot · ⚖️ nullszaldó · 🛑 stop · 🎯 cél · ⏰ időkorlát.
Amíg egy papírban nyitott trade van, arra nem jön újabb belépési jelzés.
`python cli.py positions` mutatja a nyitott trade-eket és az **élő eredménynaplót**.

> **Ha a jelzésből Alpaca paper trade lett, a „Jelzett trade-ek” oldal az Alpaca állapotát mutatja**
> (beküldve / nyitott / visszavonva / nem teljesült / lezárva, a tényleges belépővel és eredménnyel).
> A felület megnyitáskor frissít az Alpacáról (percenként legfeljebb egyszer), a monitor 15 percenként,
> piacon kívül is – így egy az Alpacán kézzel visszavont megbízás sem marad „belépésre vár” állapotban.
> Paper trade nélkül a követés virtuális: a jelzés szerinti belépést feltételezi. A táblában „Alpaca” vagy
> „virtuális” jelzi a forrást. **Minden paper trade itt van**, a Paper trading oldalról kézzel beküldött is.

### Kemény szűrők — ha egy is elbukik, nincs jelzés

1. **Trend** — záró > SMA200 és EMA50 > EMA200 (ellentrendbe nem veszünk)
2. **RSI 45–72** — ne legyen gyenge, de ne legyen már túlvett
3. **Kitörési volumen** — *alapból kikapcsolva kemény szűrőként* (`min_breakout_volume: 0`),
   csak a pontszámban számít; lásd a backtest eredményét lent. Tankönyvi érték: 1,3
4. **Nem túlnyújtott** — max. 1,5 ATR a trigger fölött (nem üldözzük)
5. **Hozam/kockázat ≥ 1:2** — a *tényleges* belépőből számolva
6. **Stop ≤ 12%** — a stop legalább 2 ATR-re van a belépőtől (a napi zaj ne üsse ki)
7. **Piaci rezsim** — a papír piaca > SMA200 (medvepiacon a kitörések többsége elbukik), **papíronként**:
   Nasdaq-100 tag (akkor is, ha S&P 500 tag is) → **QQQ**; csak S&P 500 tag → az **S&P 500 index** (`^GSPC`);
   egyik indexben sincs (csak a watchlisten) → **a papír saját trendje** (saját záró > saját SMA200).
   A relatív erő a papír indexéhez mér (indexen kívül az S&P 500-hoz). A fejléc mindkét indexet mutatja.
   Ugyanez a szabály fut a kutatásban, a laborban és a visszajátszásban is (a napló rögzíti, melyiket nézte).
8. **Earnings-tilalom** — gyorsjelentés előtt 7 napig nincs belépés

Ha egy szűrőt nem lehet ellenőrizni (pl. nem elérhető az earnings dátum), a jelzés
nem akad el, de **FIGYELEM** sorban kiírja, mit nem tudott ellenőrizni.

### Pontszám (0–100) — a szűrőn átjutottak rangsorolása

| Komponens | Max | Mit néz |
|---|---|---|
| Mintaminőség | 20 | a formáció geometriai illeszkedése |
| História | 15 | a kalibráció várható értéke (R) erre a mintára |
| Trenderősség | 15 | ADX, EMA50 meredekség, EMA20 fölött |
| Volumen | 15 | kitörési volumen + volumen-száradás a mintában |
| Relatív erő | 10 | 60 napos hozam a benchmarkhoz (SPY / QQQ) képest |
| RSI | 10 | 50–65 = ideális |
| Hozam/kockázat | 10 | 1:1,5 → 0 pont, 1:3,5 → teljes |
| Szintek | 5 | support a stop fölött, nincs közeli rezisztencia |

Riasztás: minden szűrő ✔ **és** pontszám ≥ `min_score` (50). **A pontszám nem
valószínűség** — a történeti találati arányt a jelzés külön sorban közli.

### Egy jelzés így néz ki

```
🟢 MSFT: Bull zaszlo - MEGEROSITETT KITORES - belepesi jelzes (74/100)
Zaro (2026-10-02): 521.40 $
Trigger szint: 517.78 $
Belepo: masnap nyitaskor, limit 526.61 $ (felette ne ugorj utana)
Stop: 500.20 $ (-4.1%, 2.0 ATR)
Cel: 570.00 $ (+9.3%)
Hozam/kockazat: 1 : 2.29
Meret: 4 db = 2,086 $ (kockazat 85 $ = 1.0% a 10,000 $ szamlabol)
Historia: 53% talalat, varhato ertek +0.53R (219 historikus minta)

Kilepesi terv:
  1. Fele (2 db) eladas +1.0R-nel: 542.60 $ (+4.1%) - adj be ra limit megbizast
  2. Utana a stopot huzd a belepore (521.40 $) - innen nem lehet veszteseg
  3. A maradek a celig: 570.00 $
  Idokorlat: ~40 kereskedesi nap utan zarasra kilepes
  A rendszer koveti a trade-et, es szol, ha lepni kell.
Kovetkezo jelentes: 2026-10-28 (26 nap)

Szurok:
  [OK ] Trend: zaro > SMA200 (468.10), EMA50 > EMA200
  [OK ] RSI: 61 (sav 45-72)
  ...
Nem befektetesi tanacs. A pontszam nem valoszinuseg.
```
*(illusztráció — a számok példák)*

### Kutatás: mi emeli a nyerési arányt? (`entry-research`)

A kitöréseket egyszer kigyűjti, majd **192 szabályváltozatot** játszik le ugyanazokon:
4 belépési mód × 3 stop-távolság × 4 kilépési mód, plusz alakzat- és QQQ-szűrő.
Időszakok:

| Időszak | Mire való |
|---|---|
| **Tanító** – 2022 előtt | a „tanítóból választott alakzatok” szűrő csak ezt látja |
| **Validáció** – 2022-01-01 → 2025-03-31 | ezen választunk a változatok közül |
| **Holdout** – 2025-04-01-től | **zárolt végső teszt**: a normál kutatás nem látja; csak `--holdout`-tal nyitható, minden megnyitás naplózva (`data/research/holdout_log.jsonl`) |

(A 2022 utáni időszakot a korábbi kutatás már 192 változat közötti választásra használta, így az
már nem független teszt – ezért kellett egy új, érintetlen végső teszt-időszak.)

**Robusztus** az a változat, amelyiknek a *rosszabbik* időszaki várható értéke is legalább
akkora, mint a jelenlegi beállításé a rosszabbik időszakban, **és a várható érték 95%-os
konfidencia-intervallumának alsó határa sem rosszabb** – kevés trade-ből a pontbecslés félrevezető.
Minden sor mutatja a nyerési arány (Wilson) és a várható érték (bootstrap) 95%-os intervallumát, és
hogy hány évben volt pozitív (`+ev`). Az **earnings-tilalom** a historikus jelentési dátumokkal
(yfinance, `data/research/earnings_history.json`) tesztelve; az `entry-backtest` egy „tilalom nélkül”
sort is mutat, ami a szűrő hatása.

**Eredmény az új módszerrel, a 105 papíros bővített mintán** (2016 – 2025. március, holdout zárva):

| | Trade | Nyerés (95% CI) | EV, R (95% CI) | Max. visszaesés | Pozitív évek |
|---|---|---|---|---|---|
| Minden bikás kitörés, szűretlen | 1721 | 43% (41–45%) | +0,20 (+0,12…+0,28) | 71R | 5/9 |
| **Jelenlegi beállítás** | 234 | **56% (50–63%)** | **+0,14 (−0,01…+0,29)** | 12,5R | 5/8 |
| Jelenlegi, earnings-tilalom nélkül | 254 | 56% (50–62%) | +0,13 (−0,02…+0,29) | 12,1R | 7/8 |

- A szűrők **statisztikailag kimutathatóan emelik a nyerési arányt** (a két intervallum nem fed át),
  és a hatodára csökkentik a legnagyobb visszaesést.
- **A jelenlegi beállítás várható értékének intervalluma tartalmazza a nullát**: ezen a szélesebb,
  kevésbé túlélési torzítással terhelt mintán az előny **nem bizonyított**. A lenti, kedvezőbb
  számok 10 nagy technológiai papírból (az évtized győzteseiből) jöttek.
- Az earnings-tilalom 20 trade-et szűr ki, mérhető haszon nélkül – sem a bennhagyása, sem a
  kivétele nem igazolt, ezért változatlan.
- Ismert eltérés: a kutatás a pontszámot kalibráció, relatív erő és szintek nélkül számolja (a teljes
  kalibráció jövőbe látást okozna), élesben ezek is benne vannak – így a `min_score` küszöb élesben
  kissé más jelölteket enged át. Javítás: időben haladó kalibráció (folyamatban).

### Kutatás 2026. október: momentum- és erősségszűrők (`entry-research --universe --study`)

**Kérdés:** van-e olyan, szakirodalmilag megalapozott szűrő, amely megbízhatóbbá teszi a belépési jelzéseket?
A vizsgált változatok listája **a futtatás előtt rögzítve** (`entry_research.study_grid`), mindegyik egyetlen
eltérés az élő beállítástól. Minden sor látszik, nem csak a nyertesek. Szakirodalmi háttér:

| Szűrő | Háttér | Bizonyíték |
|---|---|---|
| 52 hetes csúcs közelsége | George & Hwang (2004, *Journal of Finance*) | erős, egyedi részvényeken; indexeken gyenge |
| 12–1 hónapos momentum | Jegadeesh & Titman (1993) | erős; a publikálás után kisebb prémium |
| Relatív erő a QQQ-hoz | momentum-irodalom | közvetett |
| Volatilitás-összehúzódás (VCP, squeeze) | gyakorlati szabály | független vizsgálat nem található |
| Kitörési volumen | Bulkowski | kicsi, vegyes hatás |
| RSI-túlvettség | rövid távú visszafordulás | gyenge, főleg illikvid papírokon |

**Eredmény a látható időszakokon** (105 papír, 2016 – 2025. március):

| Változat | Trade | Nyerés (95%) | EV, R (95%) | Max. DD | + évek |
|---|---|---|---|---|---|
| **Élő beállítás** | 224 | 56% (50–63%) | +0,15 (−0,01…+0,31) | 14,1R | 5/8 |
| + 52 hetes csúcs ≥ 90% | 205 | 58% (51–65%) | +0,19 (+0,03…+0,35) | 12,8R | 6/8 |
| + relatív erő > 0 | 198 | 58% (51–64%) | +0,18 (+0,01…+0,34) | 14,6R | 6/8 |
| + 12–1 momentum > 0% | 205 | 57% (50–63%) | +0,15 (−0,01…+0,31) | 11,8R | 5/8 |
| + összehúzódás ≤ 0,8 | 91 | 57% (47–67%) | +0,19 (−0,05…+0,43) | 8,9R | 5/7 |
| + kitörési volumen ≥ 1,5x | 17 | 76% (53–90%) | +0,25 (−0,19…+0,70) | 1,8R | 3/6 |
| RSI-plafon 80 | 267 | 55% (49–61%) | +0,14 (−0,01…+0,29) | 14,0R | 6/9 |
| pontszám ≥ 60 | 115 | 57% (48–66%) | +0,15 (−0,07…+0,37) | 8,8R | 4/8 |

**A kidobott trade-ek páros vizsgálata:** az 52 hetes szűrő 19 trade-et dobott ki (37% nyerés, −0,29R,
86% eséllyel negatív várható érték); a relatív erő 26-ot, de azok gyakorlatilag átlagosak voltak (−0,07R);
a 60-as pontszám-küszöb 109-et, amelyek ugyanolyan jók voltak, mint a megtartottak (+0,15R) – **a pontszám
50 fölött nem jelez előre**.

**Végső teszt – a zárolt holdout egyszeri megnyitása** (2025. április – 2026. október, előre rögzített
döntési szabály: a szűrő csak akkor kerül élesbe, ha a holdoutban nem rosszabb az élő beállításnál):

| Holdout | Trade | Nyerés | EV |
|---|---|---|---|
| **Élő beállítás** | 43 | 53% | **+0,18R** |
| + 52 hetes csúcs ≥ 90% | 38 | 50% | +0,13R |

**Döntés: az élő beállítás marad.** Az 52 hetes szűrő a holdoutban nem ismételte meg a javulást (az 5 kidobott
trade nyerő volt), ezért kikapcsolva marad – a kódban elérhető (`min_high52_ratio`, `min_momentum_pct`,
`min_rel_strength`, `max_contraction`, alapból mind ki). **Az élő beállítás viszont a független holdoutban is
pozitív maradt** (+0,18R a korábbi +0,15R után) – ez a legfontosabb eredmény.

A holdout ezzel **elhasználódott** (`data/research/holdout_log.jsonl`: 1 megnyitás). Innentől a független
teszt a **paper trading** (Jelzett trade-ek / Paper trading oldal). A 105 papíros mintán ~25–30 jelzés jön évente,
egy 15 papíros watchlisten ennek csak töredéke – egy +0,15R-es előny statisztikai igazolásához viszont néhány száz
trade kell. Ezért a Nasdaq-100 / S&P 500 indexszkennelés bekapcsolása sokszorosára gyorsítja a mérést.

*Az alábbi táblázat a régi (2 időszakos, intervallum nélküli) módszerrel, 10 papíron készült –
történeti referencia.*

Eredmény 10 papíron (2017–2026), régi módszer:

| | Trade | Nyerő | EV | Tanító | Teszt | Max. vesztes sorozat | Max. visszaesés |
|---|---|---|---|---|---|---|---|
| Régi: kitörés napja, 1,5 ATR stop, teljes cél | 82 | 48% | +0,88R | 55% / +1,50R | 40% / +0,22R | 5 | 7,7R |
| **Új: megerősítés + 2 ATR stop + fele +1R-nél** | 49 | **67%** | +0,51R | 62% / +0,43R | **74% / +0,60R** | **3** | **4,1R** |

Mit érdemes tudni róla:
- **A nyerési arány mindkét időszakban nőtt**, a legrosszabb sorozat és a visszaesés is felére csökkent.
- **Az átlag-EV kisebb**, mert a régi beállítás tanító-időszaki +1,50R-je néhány 2020-as óriási
  futásból jött. 2022 óta (teszt) az új beállítás jobb: +0,60R a +0,22R helyett.
- **Kevesebb a trade (49 vs 82).** Tíz év alatt összesen kevesebb R gyűlik össze, cserébe
  sokkal egyenletesebben.
- A **követett stop** és a **tanító időszakból kiválasztott alakzatok** szűrője *nem* javított.
- 10 papír kevés: ezért van a **bővített minta** (lent).

### Bővített minta — 106 papír + QQQ

A `config/universe.yaml` 106 papírt tartalmaz. A nagy győztesek mellett szándékosan
gyenge és oldalazó papírokat is (INTC, PYPL, WBA, BIIB, ZM, PTON, NKE…), hogy csökkenjen
a túlélési torzítás. A QQQ-t is letölti, így a piaci rezsim-szűrő is tesztelhető.

```bash
python cli.py fetch-universe                          # ~2-4 perc
python cli.py entry-research --universe --csv r.csv   # első futás: kb. 106 perc / magok száma
python cli.py entry-backtest --universe               # a jelenlegi beállítás a teljes mintán
```

Az első futás lassú (papíronként ~1 perc/mag), utána a `data/research` cache-ből másodpercek.

### Mikor jön értesítés?

- **Csak piaczárás után** (16:15 New York = 22:15 magyar idő), lezárt napi gyertyán —
  a belépési és a pozíciókövetési értesítések is.
  Napközben a monitor figyel, de belépési riasztást nem küld: egy napközbeni
  „kitörés" zárásig visszaeshet — a hamis kitörések nagy része pont ilyen.
- Ugyanarra a mintára **10 napig nincs ismétlés** (`data/entry_state.json`).
- **Irány** (Beállítások → *Értesítések iránya*, `alerts.direction`):
  - `long` (alap) – csak a swing long belépési jelzések,
  - `short` – csak a medvés alakzatok (fej-váll, dupla tető, bear zászló…) friss, zárón
    megerősített lefelé törése, majd annak kimenete (cél / bukás),
  - `both` – mindkettő.

  A short jelzés mögött **nincs belépési motor** (szűrők, kereskedési terv, paper trading), és a
  backtest szerint a medvés alakzatok gyengébbek (pl. bear zászló: 28% találat, −0,16R).
  A pozíciókövetés, a saját pozíciók és a paper trading értesítései iránytól függetlenül mennek.
- A régi, minden formáció-állapotra szóló riasztás (a választott irány(ok)ban) a
  `settings.yaml`-ban kapcsolható vissza: `alerts.pattern_events: true`.

### Saját számla beállítása

A `settings.yaml` → `entry:` részben állítsd be a `account_size` (USD), a
`risk_per_trade_pct` (egy trade max. vesztesége, alap 1%) és a `max_position_pct`
értékeket — ebből számolja a javasolt darabszámot.

### Webhook (Google Chat / Slack / Discord)

A `.env` fájlba: `ALERT_WEBHOOK_URL=https://...` — a formátumot az URL-ből ismeri fel.
Az URL maga a jelszó: **soha ne a settings.yaml-ba** írd.

A watchlist a `config/watchlist.yaml`-ban van (a dashboard oldalsávjából is szerkeszthető),
minden küszöb és súly a `config/settings.yaml`-ban.

---

## Saját pozíciók

A brókernél **már meglévő** pozícióidat is felveheted (ticker, belépő ár, darab, dátum,
opcionálisan stop és cél). A rendszer ugyanazzal a motorral értékeli ki őket, mint a belépéseket:

| Szempont | Mikor jelez |
|---|---|
| Stop | nincs megadva (javasol egyet) · az ár elérte · nyereséges pozíciónál feljebb húzható |
| Visszaesés | a belépés óta mért csúcstól már 3 ATR-nél többet esett |
| Hosszú / rövid trend | záró az SMA200 alatt (**kockázatos**) · EMA50 < EMA200 · záró az EMA50 alatt |
| RSI | túlvett (≥75) vagy gyenge (≤35) |
| Medvés minta | kitört (**kockázatos**) vagy 1 ATR-en belül van a triggertől |
| Rezisztencia | 1 ATR-en belül van |
| Earnings | a tilalmi időn (7 nap) belül – egy gap átugorhatja a stopot |
| Piac, irányjelzés | QQQ az SMA200 alatt · medvés kompozit kép |

**Minősítés:** egy „kockázatos” szempont → *Kockázatos*, egy figyelmeztetés → *Figyelj*, egyébként *Rendben*.
**Stop-javaslat:** a belépés óta mért csúcs − 3 ATR (követett stop), de legalább 1 ATR-re a zárótól.

A monitor zárás után ellenőrzi őket, és szól: 🛑 stop elérve · 🎯 cél elérve · ⚠️ romlott a minősítés ·
📅 közeleg a jelentés (mindegyik egyszer). Amíg egy papír a tiéd, arra nem jön új vételi jelzés.

```bash
python cli.py holdings add --symbol AAPL --price 180 --shares 10 --date 2026-03-02 --stop 170
python cli.py holdings                       # lista + részletes kiértékelés
python cli.py holdings set-stop --id AAPL-20261012-1a2b3c --stop 185
python cli.py holdings close --id AAPL-20261012-1a2b3c --price 205
```

A webes felületen a **Saját pozíciók** fülön ugyanez űrlappal megy, a papír oldalán a chart
a belépőt és a stopot is mutatja. A saját pozíciók **nem kerülnek** a jelzések élő eredménynaplójába
(`data/holdings.json`, max. 50 nyitott + 200 lezárt). A rendszer nem lát bele a számládba:
ha a brókernél eladtál, zárd le itt is.

---

## Reggeli összefoglaló (Összefoglaló fül)

Minden kereskedési napon **06:30-kor (budapesti idő)** a monitor összefoglalót készít a watchlistről és a Saját
pozíciókról: mi változott az előző összefoglaló óta. **Tényeket közöl, nem ajánl:** nincs benne „vedd meg” vagy
vételi limit, csak az, ami az adatban történt. Terv: [`docs/FEATURES-2026-10-DESIGN.md`](docs/FEATURES-2026-10-DESIGN.md) (A).

| Változás | Mikor szól |
|---|---|
| Formáció | új vagy megszűnt minta, a trigger átlépése |
| Trigger-közelség | a formálódó minta triggerétől `near_trigger_atr` ATR-en belül (alap 1), vagy közben legalább 0,5 ATR-t közeledett |
| Szint | a záró a tegnapi rezisztencia fölé vagy a tegnapi support alá került |
| Gyorsjelentés | 7, 3 és 1 napnál (`earnings_days`), egyszer-egyszer |
| 8-K | új SEC-bejelentés a tételkódokkal; a lényeges tételek (pl. 4.02, 1.03, 3.01) nagyobb súllyal |
| Hírhangulat | a 3 napos hangulat-átlag előjelet vált, vagy új lényeges negatív hír jelenik meg |
| Stratégiai jelölt | a papír bekerül egy stratégia jelöltjei közé vagy kikerül onnan – a **jelzésnaplóból**, nem újraszámolva |
| Rezsim | a QQQ / S&P 500 vagy a papír saját záróárja átlépi a 200 napos átlagot |

- **Fontosság:** szabályalapú pontszám, eseménytípusonként állítható súllyal (`settings.yaml` → `digest.weights`).
  A Saját pozíciók eseményei 1,5-szeres súlyt kapnak. Papíronként legfeljebb 3 sor, összesen legfeljebb 15; a
  változás nélküli papírok egy sorban.
- **Csak tárolt, lezárt adat:** a még alakuló gyertya kimarad, a hírek a döntési nap 16:00-s (NY) határával
  számítanak. Minden szám a számításból jön (teszt ellenőrzi), a mondatok a fordítási katalógus sablonjai, így
  angolul, németül és spanyolul is megjelennek.
- **Hol látszik:** az Áttekintés tetején egy rövid kártya, az **Összefoglaló** fülön a teljes szöveg archívummal
  (90 nap) és „Készítés most” gombbal. Discordra a fülön lévő kapcsolóval küldhető (`digest.send_discord`, alapból
  ki); csendes időszakban (`alerts.quiet_hours`) nem megy ki.
- **Ha a monitor 06:30-kor nem futott,** induláskor pótolja, de csak a nyitás előtt.
- **Tárolás:** `data/users/admin/digest/<nap>.json` (20–60 KB / nap, 90 napig). Ez az első funkció, amely már a
  felhasználónkénti munkaterület-könyvtárba ír (SaaS-előkészítés); a futások CPU-ideje a `data/users/admin/usage.json`-ba kerül.

```bash
python cli.py digest --print                 # elkészíti, menti és kiírja (Discordra nem küldi)
python cli.py digest --print --no-refresh    # a 8-K frissítése nélkül, csak tárolt adatból
python cli.py digest --print --date 2026-10-16   # egy korábbi reggel újragenerálása (csak az akkor lezárt gyertyák)
```

---

## Eseménytanulmányok (Események fül)

**Mi történt az árral egy adott eseménytípus után?** Választható univerzum (egy papír, watchlist, Nasdaq-100,
S&P 500, a kettő együtt, opcionálisan GICS-szektorra szűrve), időszak és ablak (alap: −10 … +60. nap).
Terv: [`docs/FEATURES-2026-10-DESIGN.md`](docs/FEATURES-2026-10-DESIGN.md) (C).

| Esemény | Forrás | 0. nap (mikor vált ismertté) |
|---|---|---|
| Gyorsjelentés (irány: a 0. napi nyitó gap) | a gyorsjelentés-naptár | nyitás előtti jelentésnél aznap, különben és ismeretlen időpontnál a következő kereskedési nap |
| 8-K bejelentés (tételkódra szűrhető, pl. 2.02) | SEC EDGAR (a hírtár) | az első nyitás az elfogadás után |
| Új 52 hetes csúcs / mélypont | árfolyam | a nap zárója (csendes szakasz után az első) |
| Nagy nyitó gap | árfolyam | a gap napja |
| Saját stratégia jelzése (Visszahúzás-család, Kitörés) | ugyanaz a motor, mint élesben | a jelzés napjának zárója |
| Bennfentes vétel / eladás, csoportos vétel (Form 4) | SEC negyedéves csomagok + élő XML | pontos időpontnál az első nyitás utána; csak dátumnál a **következő** nap |
| S&P 500 bekerülés / kikerülés | Wikipedia „Historical components of the S&P 500” | a hatályos nap (előre ismert; a bejelentési hatás nincs benne) |

**Módszertan:**
- abnormális hozam a benchmarkhoz képest, piaci modellel (OLS a becslési ablakon, alap −260 … −21. nap; legalább 120 nap kell, különben piac-korrigált hozam);
- a becslési ablak az eseményablak előtt véget ér, így a 0. nap utáni ár nem hat rá;
- kumulált abnormális hozam (CAR): átlag és medián;
- 95%-os sáv **dátum szerint csoportosított** bootstrappal (az egy napra eső események együtt mozognak);
- találati arány (CAR > 0) Wilson-intervallummal;
- bontás irányra, szektorra, piaci rezsimre és évre.

Két CAR jelenik meg: a teljes ablakra, és az esemény napjának **zárója utánra** (+1. naptól). Ez utóbbiba a 0. napi reakció és a 0. napi adatból
képzett besorolás (pl. a gap iránya) nem keveredik bele. Ugyanannak a papírnak az ablakon belüli későbbi eseménye kimarad;
a felhasznált, az átfedés miatt kimaradt és a nem teljes ablakú események száma mindig látszik.

**A zárolt holdout alapból kimarad** (az adat 2025-03-31-nél véget ér). Kapcsolóval megnyitható, ilyenkor naplózódik
(`study=event-study`). A felület kiírja a túlélési torzítást (csak a mai indextagok) és a csak dátummal ismert események
számát. Az S&P 500 változásoknál azt is, hogy a bejelentési hatás nincs benne.

**Hol fut:**
- egy papír és a watchlist azonnal, a webes folyamatban;
- nagyobb univerzum a labor-munkasoron.

**Az eredmény:** átlagos pálya sávval, eloszlás-hisztogram, bontások, eseménylista. Egy sorra kattintva a papír és a benchmark
látszik az esemény körül. CSV-export is van.

**Forrásadat:**

```bash
python cli.py event-sources --index-changes      # S&P 500 változások (1976-tól) + GICS-szektorok -> data/events/
python cli.py event-sources --form4-since 2017   # SEC negyedéves Form 4 csomagok -> data/events/form4/ (max. 50 MB)
python cli.py event-sources --form4-live         # a watchlist és a Saját pozíciók új Form 4 beadásai (XML, pontos idővel)
```

A SEC azonosító User-Agentet kér (név + e-mail): a `.env`-be írd be: `SEC_USER_AGENT="Neved email@cimed"`. Enélkül a
SEC elutasítja a letöltést. A kérések folyamatok között is közösen legfeljebb 8 / mp-et tesznek ki.

```bash
python cli.py event-study --type earnings --universe symbol --symbol NVDA --window -5 20
python cli.py event-study --type sp500_add --universe sp500 --window -5 40
python cli.py event-study --type filing_8k --params '{"item": "4.02"}' --universe indexes --refresh --csv 402.csv
python cli.py event-study --type strategy --params '{"strategy": "deep"}' --universe indexes
```

Tárolás:
- a tanulmányok: `data/users/admin/events/studies/` (legfeljebb 30 db, 30 MB);
- a közös forrásadat: `data/events/`.

---

## Gyakorló mód (Gyakorlás fül)

**Rejtett, múltbeli chart-helyzetek.** Egy körben (alap: 20 helyzet) mindegyiknél 120 napnyi chartot látsz, papír és
dátum nélkül. Eldöntöd, hogy kihagyod, vagy **long** vagy **short** irányban belépsz (három gomb: Kihagyom / Long / Short):
- belépés a következő nyitáson, vagy egynapos limittel (long: a belépő alatt, short: a belépő fölött teljesül);
- kötelező stop (long: a belépő alatt, short: fölötte), opcionális cél (a másik oldalon);
- 1–30 napos időkorlát.

A döntés után nem módosítható. Utána lepörög, mi történt, és kiderül a papír és a dátum.
Terv: [`docs/FEATURES-2026-10-DESIGN.md`](docs/FEATURES-2026-10-DESIGN.md) (D).

**Rejtés:** a felfedés előtt az API csak véletlen, betűs azonosítót és normalizált adatot ad ki:
- index-tengely (−119 … 0);
- az utolsó látható záró = 100;
- volumen a 20 napos átlagához viszonyítva;
- EMA20/50/200 és ATR a normalizált árból.

Ticker, dátum, nyers ár és nyers volumen nincs benne; ezt teszt ellenőrzi minden végpontra.

**Szimuláció:** ugyanaz a pesszimista motor, mint a stratégiáké (`meanrev.simulate_trade`). A cél napközben teljesül
(csak itt van bekapcsolva):
- ha ugyanazon a gyertyán a stop és a cél is elérhető, a stop számít;
- ha a nyitó a cél fölött van, a nyitóáron teljesül;
- a belépés napján a cél csak nyitóáras belépésnél számít.

Az eredmény R-ben: (kilépő − belépő) / (belépő − stop), oldalanként a stratégia költségével csökkentve.

**Short:** ugyanaz a motor tükrözött árakon (p' = K − p, a csúcs és a mélypont helyet cserél), így a fenti pesszimista
szabályok pontosan tükrözve érvényesek (azonos gyertyán stop és cél → stop; felfelé nyitási rés a stopon túl → nyitóár).
R = (belépő − kilépő − költség − kölcsönzési díj) / (stop − belépő). A kölcsönzési díj (bps / nap a belépő értékén)
a `practice.borrow_bps_day` beállítás, alapból 0. A long viselkedés bitre változatlan (teszt).

**Összevetés ugyanazon a ponton:**
- Visszahúzás és Mély visszahúzás: ha a döntés napján jelzett, a saját paramétereivel szimulálva;
- Kitörés: a labor élő kísérletének mentett kötése, ha a kitörés-cache-ben aznap kitörés van;
- **Short (kísérleti):** a `short.py` motor a döntés napjáig ismert adatból (megerősített pivotok); ha egy megerősített
  letörés minden szűrőn átment, kötés a következő nyitáson a terv stopjával, céljával és időtávjával. Kísérleti
  jelöléssel, mert a short oldal nincs backtesttel igazolva;
- referencia-kötés: következő nyitás, 2 ATR stop, 10 nap;
- véletlen alapvonal: ugyanazon a papíron 20 seedelt véletlen napon, a te stop-távolságoddal (ATR-ben), céloddal és
  időkorlátoddal, az átlagos R.

**Ítélet minden döntés után (#25)** – két külön szempont, mert a jó döntés és a jó kimenet nem ugyanaz:
- *kimenet szerint* (utólag): kihagyás (0 R), long, short azonos feltételekkel (következő nyitás, a te
  stop-távolságod ATR-ben, célod és időkorlátod; kihagyásnál a referencia-beállítás), ugyanazzal a szimulációval.
  Belépésnél a kimenet jó, ha a teljesült kötés nyereséges; kihagyásnál, ha egyik alternatíva sem lett legalább
  `practice.good_r` (1) R;
- *szabály szerint* (a döntés napjáig ismert adatból): long, ha a Visszahúzás / Mély visszahúzás jelzett, vagy a Kitörés
  kötése átment a szűrőkön; short, ha a kísérleti short motor megerősített letörése átment; egyik sem: kihagyás.
- Kategória: **jó döntés** (mindkettő), **szerencsés** (csak a kimenet), **balszerencsés** (csak a szabály – egy jó döntés
  is veszíthet), **rossz döntés** (egyik sem). Mellette jelzés: túl szoros stop (`practice.tight_stop_atr`), alacsony
  cél/kockázat (`practice.min_rr`), nem teljesült limit.

**Levezetés (#26)** – kinyitható panel, lépésenként, sablonos mondatokkal (LLM nélkül, minden szám a számításból;
teszt ellenőrzi), a chart a kiválasztott lépés szintjeit emeli ki:
1. mit lehetett látni a döntéskor (záró, SMA200, EMA50/200, RSI(2), RSI(14), ATR, 20 napos csúcs / mélypont, trendkép,
   piaci rezsim, gyorsjelentés) – csak a döntés napjáig;
2. a szabályok szűrőnként (érték, feltétel, teljesült);
3. a te kötésed: belépés, kockázat (egy R), a kilépés napja és oka (azonos gyertyánál a pesszimista szabály kiírva),
   bruttó − költség − kölcsönzési díj = nettó, R = nettó / kockázat;
4. az alternatívák ugyanígy kiszámolva;
5. mi lett volna a jó döntés és mi különböztette meg, valamint (belépésnél) egy **utólagos** stop-összevetés egy előre
   rögzített kis rácsból (1–3 ATR), csak `practice.better_r` (0,5) R javulástól, utólagosként jelölve.

**A kör összegzése:**
- nyerési arány, átlagos és összes R;
- az alapvonal és a stratégiák átlaga;
- a kihagyott jó helyzetek (ahol a szabály vagy a referencia-kötés legalább +1 R lett volna);
- a stop-távolság eloszlása ATR-ben, és az 1 ATR-nél szorosabb stopok aránya;
- long és short bontás, és az ítéletek megoszlása (jó / szerencsés / balszerencsés / rossz).

**Feladatkészlet:** a Backteszt oldal tárából (elsőbbséggel) és az univerzum-cache-ből készül, egyszer adat-ujjlenyomatonként.
Egy pont akkor kerül bele, ha legalább 250 bemelegítő gyertya van előtte, és a 31. napja is a zárolt holdout előtt van.
Gyanús (split nélküli) ugrás vagy nulla volumen sem lehet az ablakban. Négy kategória van, alapból 35 / 15 / 15 / 35%:
- stratégia-jelzés: a Visszahúzás setup-ja, vagy a labor kitörés-cache-ének kitörése;
- formálódó bikás alakzat: pont-időben, csak az addigi adatból;
- short-gyanús helyzet: záró < SMA200 és EMA50 < EMA200, lehetőleg formálódó vagy épp letört medvés alakzattal;
- semmi különös.

A kategória csak a felfedéskor látszik, és mindegyikben lehet long, short vagy kihagyás a jó válasz, így az irány nem
található ki a feladat típusából. A készlet verziója 2: a régi készletet a „Újraépítés” gomb (vagy
`python cli.py practice-pool --force`) cseréli; a régi körök és döntések (a korábbi „belépés” = long) továbbra is
megnyithatók.

Ugyanaz a seed ugyanazt a helyzetsort adja. A **megosztási kód** (csak betűk) a seedet, a készlet ujjlenyomatát és a
helyzetek számát tartalmazza; más készletnél a felület figyelmeztet.

```bash
python cli.py practice-pool              # a feladatkészlet felépítése (~1–2 perc, 500 papíron)
python cli.py practice-pool --queue      # ugyanez a labor-munkasoron (a felület gombja is ezt teszi)
```

Tárolás:
- a körök: `data/users/admin/practice/sessions/` (legfeljebb 100);
- a feladatkészlet: `data/practice/pool.json.gz` (legfeljebb 5 MB, jellemzően ~40 KB).

Gyakorlás múltbeli adaton, szimulált kötésekkel – nem befektetési tanács.

---

## Kutatóügynök (Ügynök fül)

Megadsz egy **kutatási célt (brief)**, például: „swing long, Nasdaq-100 + S&P 500, heti 0,5–2 kötés, max. 10%
visszaesés, legfeljebb 15 nap tartás, 15 bps / oldal költség”. Az ügynök a labor-szolgáltatásban stratégiaváltozatokat
generál és tesztel, és jelentést ad a legjobb 3 jelöltről: tőkegörbe, mutatók, szabályalapú indoklás, és hogy „hogyan
jutott ide”. **Nincs LLM:** a keresés determinisztikus (rögzített seed), az indoklás sablonos szöveg mért számokkal.
Terv: [`docs/FEATURES-2026-10-DESIGN.md`](docs/FEATURES-2026-10-DESIGN.md) (B).

| Lépés | Mit csinál |
|---|---|
| Tér | A Visszahúzás-családnál (`pullback`, `deep`, `etf`) a `PullbackParams` mezői (RSI(2) küszöb, limit, stop, kilépés, időkorlát, trend-SMA, rezsimszűrő…), a Kitörésnél a Labor szerkeszthető mezői. A kiinduló beállítás a motor mostani paramétere a brief költségével; a `search.fixed` mezői rögzítve. |
| Keresés | Successive halving (alap): sok változat a papírok negyedén, a legjobb harmad tovább a felére, majd az összesre; utána mezőnkénti finomítás a legjobb körül. Választható: rács, véletlen. |
| Célmutató | Az átlagos nettó hozam (Kitörésnél R) **bootstrap alsó határa** a tanító időszakban. A nyerési arány alsó határa csak opcionális **feltétel** (`min_win_lo`), mert önmagában tág stoppal felfelé tornázható, miközben a várható érték romlik. |
| Feltételek | kötés / hét sáv, max. visszaesés, profit-faktor, tartás, min. kötésszám. A rangsor: kevesebb nem teljesült feltétel elöl, azon belül a nagyobb alsó határ. |
| Döntősök (3) | A validációs időszakot **csak ők** látják, egyszer. Utána: szomszédos beállítások stabilitása, kétszeres költség, évenkénti bontás, az előre rögzített elfogadási feltétel, és mezőnként, hogy a változtatás mennyit adott (a döntős az adott mező alapértékével összevetve). |

**Becsületesség:**
- az adat a **holdout előtt véget ér** (2025-03-31), és ezt minden értékelés előtt ellenőrzi (`HoldoutGuard`);
- a keresés adata a 2022 előtti napon vágódik;
- a holdout-napló nem változik;
- a kipróbált változatok száma, és hogy a motorcsalád hányszor nézte meg a validációs időszakot, a jelentésben látszik.

Ugyanazok a függvények számolnak, mint a `meanrev-research`-ben (`evaluate_variant`), illetve a Laborban (`lab.evaluate`): egy teszt ellenőrzi,
hogy a kiválasztott változatot a `meanrev_research.run` újrafuttatva ugyanazok a mutatók jönnek ki.

**Költségkeret briefenként:** max. változatszám és max. CPU-perc. A CPU-keret 80%-a jut a keresésre, a maradék a döntősök ellenőrzésére.
Ha a keret elfogy (vagy éjjel elérkezik a 06:00-s budapesti határidő), a keresés leáll, és az addigi legjobbakból
készül jelentés; a kimaradt lépéseket a jelentés felsorolja.

**Futtatás:**
- a **Ügynök** fülön: „Futtatás most” (amint a labor sorra veszi) vagy „Ma éjjel” (20:00 New York után indul, 06:00-ra kész, a reggeli összefoglaló hivatkozik rá);
- a futás oldalán: tőkegörbék, döntősök összevetése, idővonal;
- **„Átvétel a Homokozóba”** megerősítéssel. A Homokozó értesítés nélkül (`alerts: false`) és paper-kötés nélkül fut, élő virtuális követéssel; a régi homokozó-beállítás egy 10 elemes előzménylistába kerül.

A labor egy fájlalapú munkasort dolgoz fel (`data/queue/`, munkaterületenként legfeljebb 10 várakozó feladat). A futások
CPU-ideje a `data/users/admin/usage.json`-ba kerül. Memória: a teljes indexlistán a csúcs kb. 370 MB (a labor plafonja 450 MB).

```bash
python cli.py agent run --brief briefs/pelda-swing-long.yaml                    # azonnal, ebben a folyamatban
python cli.py agent run --brief briefs/pelda-swing-long.yaml --max-variants 40 --max-cpu 10   # kisebb kerettel
python cli.py agent queue --brief briefs/pelda-swing-long.yaml --night          # a labor ma éjjel futtatja
python cli.py agent list                                                       # briefek és futások
python cli.py agent report --run-id 20261017-5005fba6                         # egy jelentés Markdownban
```

Tárolás: `data/users/admin/agent/` (briefek, legfeljebb 30 futás és 50 MB; futásonként `report.json`, `report.md`, `log.jsonl`).

---

## Stratégiák (Stratégiák és Kutatás fül)

| Stratégia | Mit csinál | Paper fiók |
|---|---|---|
| **Kitörés** | a kitöréses alakzat-motor (fent) | köthető – most ez az aktív |
| **Visszahúzás (RSI2)** | mély, rövid visszahúzódás emelkedő trendben, a Nasdaq-100 + S&P 500 + watchlist teljes listáján | köthető |
| **ETF visszahúzás** | rövid túladottság likvid részvény-ETF-eken (SPY, QQQ, IWM, szektor-ETF-ek…) emelkedő trendben | köthető |
| **Homokozó** | szabad kutatói stratégia, választható motorral (visszahúzás / kitörés) és saját paraméterekkel | **soha** |

- Mindhárom küld jelzést, naplóz és virtuálisan követ; a **paper fiókba csak a hozzá kötött** stratégia jelzései
  mennek (`config/strategies.yaml` → `paper`, a `broker.auto_trade` a főkapcsoló). Váltás a Stratégiák vagy a
  Paper trading fülön, megerősítéssel, változásnaplóval és értesítéssel; a régi stratégia nyitott kötései a
  **saját szabályaikkal** zárulnak.
- Minden értesítés címe a stratégia nevével kezdődik (`[Visszahuzas (RSI2)] AMAT: …`), és az Értesítések, a
  Jelzett trade-ek, a Jelzésnapló és a Paper trading oldalon stratégia-oszlop és -szűrő van.
- A **Homokozó** Discord-értesítése alapból ki van kapcsolva (a felületen és a naplóban mindig látszik). A Kutatás
  fülön a szabályai szerkeszthetők, a backtest a labor-szolgáltatásban fut, és ami beválik, egy gombbal
  átvehető a Visszahúzás stratégiába (a paper fiókhoz kötés külön döntés).

### Visszahúzás (RSI2) – szabályok és validálás

Jelzés a zárón: a papír rezsimje emelkedő, záró > SMA200, **RSI(2) < 2**, ár ≥ 5 $, 20 napos forgalom ≥ 20 M$,
nincs gyorsjelentés 5 napon belül. Belépés: **egynapos vételi limit 5%-kal a záró alatt**. Stop: limit − 3×ATR(10).
Kilépés: ha a zárón **ConnorsRSI(3,2,100) > 50**, másnap **nyitáskor**; legkésőbb 10 nap. Paperben OTO-megbízás
(limitvétel + GTC stop), a kilépés nyitás után piaci áron.

`python cli.py meanrev-research [--neighbourhood] [--survivorship-check]` – 514 papír, 2017–2025.03, költség
(5 bps / oldal) után, a zárolt 2025.04 utáni időszak nélkül:

| Változat | Tanító (2022 előtt) | Validáció (2022–2025.03) | Kötés |
|---|---|---|---|
| napi limit nélkül (alapbeállítás) | 72,8% nyerő, átlag +1,7% (151 kötés) | 75,0% nyerő, átlag +2,5% (40 kötés) | ~0,48 / hét |
| napi 2 megbízás, max. 5 nyitott | 56,8% | 62,5% | ~0,13 / hét |

A napi limit rontja az eredményt: a −5%-os limitek többsége nem teljesül, és a rangsor elején a legmélyebben
zuhanó jelöltek maradnak. Összesen 73,3% (191 kötés), profit-faktor 2,76; kétszeres költséggel 72,3%; a kiesett /
csődbe ment papírokat is tartalmazó 104 papíros listán 70,4% (71 kötés). Korlátok: csak a mostani indextagokról van
adat (felfelé torzít), a kötések válságokban torlódnak, és az előre rögzített elfogadási feltételen
(heti ≥ 0,7 kötés, a bootstrap-intervallum alsó határa > 0) nem ment át – ezért **kutatási stratégiaként** fut,
nincs a paper fiókhoz kötve, amíg az élő eredmények nem igazolják.

### ETF visszahúzás – szabályok és validálás

Ugyanaz a motor, mint a Visszahúzásnál, de **likvid részvény-ETF-eken** (alap: SPY, QQQ, IWM, DIA, MDY, RSP,
11 szektor-ETF, SMH, IBB, KRE, ITB, EFA, EEM, EWJ – a Stratégiák fülön szerkeszthető). Miért ETF-ek:

- **nincs gyorsjelentés és nincs egyedi csődkockázat** – a részvényes visszahúzás nagy veszteségei jellemzően ebből jönnek
  (rossz hír → a „túladott” papír tovább esik);
- egy index-ETF túladottsága a szakirodalom szerint (Connors–Alvarez, *High Probability ETF Trading*) **megbízhatóbban
  fordul vissza**, mint egy egyedi részvényé;
- **nincs túlélési torzítás** a backtestben (a nagy ETF-ek nem szűnnek meg), és szűk a spread.

Szabályok (**a futtatás előtt rögzítve, nem a saját adatunkra hangolva**): záró > SMA200 és saját trend emelkedő,
**RSI(2) < 10**, vételi limit **1%-kal** a záró alatt (egynapos), katasztrófa-stop **5 ATR**, kilépés másnap nyitáskor, ha
a záró **az SMA5 fölé ér vagy a ConnorsRSI > 50**, legkésőbb 10 nap. Az ETF-ek együtt mozognak, ezért **naponta
legfeljebb 2, egyszerre legfeljebb 4** kötés. Költség: 3 bps / oldal.

```bash
python cli.py meanrev-research --etf                   # tanító / validáció, elfogadási feltétellel (az ETF-adatot is letölti)
python cli.py meanrev-research --etf --neighbourhood   # stabilitás a szomszédos beállításokon
python cli.py meanrev-research --etf --holdout         # a zárolt 2025.04 utáni időszak – EGYSZER, előre rögzített szabállyal
```

**Állapot:** új, még nincs validálva saját adaton. Alapból **megfigyel** (jelez, naplóz, virtuálisan követ); a
paper fiókhoz a Stratégiák fülön köthető. Élesítés csak az élesítési kapu után (`python cli.py broker`).

### Mély visszahúzás (tág stop)

A Visszahúzás (RSI2) bemenete változatlan (RSI(2) < 2, záró > SMA200, rezsim, vételi limit 5%-kal a záró alatt,
kilépés ConnorsRSI > 50 → másnap nyitáskor, legkésőbb 10 nap), de a stop **5 ATR** (a 3 helyett). A 2026-10-i
validáció (514 indextag) szerint: tanító **76,2%**, validáció **75,0%** nyerési arány, profit-faktor 3,6 / 4,4,
és a várható érték bootstrap alsó határa mindkét időszakban pozitív. Gyengéje: ritka (heti 0,2–0,7 kötés).
Részletek és összehasonlítás: [`docs/STRATEGY-COMPARISON-2026-10.md`](docs/STRATEGY-COMPARISON-2026-10.md).

```bash
python cli.py meanrev-research --deep                  # tanító / validáció, elfogadási feltétellel
python cli.py meanrev-research --deep --neighbourhood  # stabilitás a szomszédos beállításokon
python cli.py meanrev-research --deep --holdout        # a zárolt 2025.04 utáni időszak – EGYSZER
```

Alapból **fut és virtuálisan követ, de értesítést nem küld** (a jelzései egyeznek a Visszahúzáséval); a paper fiókhoz
a Stratégiák fülön köthető.

### Momentum (12-1) – havi rotáció

Kakushadze–Serur: *151 Trading Strategies* (2018), 3.1 ár-momentum. A hónap utolsó kereskedési napján a Nasdaq-100 +
S&P 500 tagjait (és a watchlistet) a 12 havi hozamuk szerint rangsorolja, a legutóbbi hónap nélkül. A legjobb **10**
papírt tartja egyenlő súllyal; egy papír addig marad, amíg a legjobb **20** között van. Vétel és eladás a rebalanszt
követő nyitáson. Stop nincs, mert a validáció szerint rontott.

A 2026-10-i validáció (518 indextag, 5 bps költség) szerint az évi hozam a tanítón **+30%**, a validáción **+52%**.
Ugyanannak a listának az egyenlő súlyú tartása ugyanekkor +17% és +9%. A max. visszaesés ~38%. A kiesett papírokat
is tartalmazó vegyes listán a többlet évi **+8–10%**, ez a reálisabb várakozás. A könyv többi
illeszkedő stratégiája (ETF-rotáció, dual momentum, több eszközös trend, alacsony volatilitás) a mi adatunkon nem érte
meg. Részletek, kockázatok: [`docs/MOMENTUM-STRATEGY-2026-10.md`](docs/MOMENTUM-STRATEGY-2026-10.md).

```bash
python cli.py momentum-research --survivorship-check   # tanító / validáció, elfogadási feltétellel
python cli.py momentum-research --holdout              # a zárolt 2025.04 utáni időszak – EGYSZER
```

Alapból **fut, értesít és virtuálisan követ**. A rangsort minden zárás után frissíti (Stratégiák fül → „Mostani
rangsor”; a „Papír kiértékelése” ablak is mutatja a papír helyét). A paper fiókhoz a Stratégiák fülön köthető, ilyenkor
egyszerre 10 pozíciót tart.

## Backteszt oldal (Backteszt fül)

Egy kiválasztott papír vagy ETF **teljes története a megadott kezdődátumtól** (akár 1980-tól), a rendszer
stratégiáival. Terv és ütemezés: [`docs/BACKTEST-PAGE-DESIGN.md`](docs/BACKTEST-PAGE-DESIGN.md) (ticket #21).

1. **Adat** (a „Futtatás” gomb automatikusan letölti / frissíti, külön lépés nélkül; a naptár a cég tőzsdére lépésénél korábbi napot nem enged választani): a papír napi gyertyái (split- és osztalékkorrigált), mellette a rezsim-indexek (`^GSPC`, `QQQ`) és a benchmark (`SPY`). Külön tárba kerülnek (`data/backtest/`, 300 MB plafon, a legrégebben használt papír törlődik). Az élő cache 10 éves plafonja erre nem vonatkozik. Utána adatminőség-jelentés jön: hiányzó napok, nulla volumen, split nélküli ugrások, a kértnél későbbi első nap.
2. **Stratégia:** Visszahúzás (RSI2), Mély visszahúzás, ETF visszahúzás, Homokozó (visszahúzás-motorral). A paraméterek erre a futtatásra módosíthatók, az éles beállítás nem változik. A Kitörés-motor és a saját szabályszerkesztő a következő fázisokban jön.
3. **Eredmény:**
   - kötésszám, nyerési arány a 95%-os sávval, átlag, profit-faktor;
   - CAGR, max. visszaesés, Sharpe, mindegyik a „vettem és tartottam” eredménnyel összevetve;
   - tőkegörbe és visszaesés-diagram, ár-chart a belépésekkel és kilépésekkel;
   - bontás tanító / validációs időszak és évek szerint, kötéslista, CSV-export;
   - mentett futtatások (legfeljebb 50).

Ugyanazok a függvények számolnak, mint az éles futásban és a `meanrev-research`-ben: egy teszt ellenőrzi, hogy a kötések
egyeznek. A **zárolt holdout alapból rejtve** marad, mert az adat 2025-03-31-nél véget ér. Kapcsolóval megnyitható,
naplózva. A felület figyelmeztet:
- a kis mintára (30 kötés alatt);
- a rövidebb rezsim-indexre;
- a hiányos gyorsjelentés-naptárra;
- arra, hogy a split-korrigált régi árak a min. ár alatt lehetnek (pl. NVDA 2016 előtt: állítsd a min. árat 0-ra);
- a túlélési torzításra.

```bash
python cli.py backtest-symbol SPY --start 2000-01-01 --strategy etf            # első futáskor az adatot is letölti
python cli.py backtest-symbol NVDA --start 2005-01-01 --strategy deep --set min_price=0 --fetch
python cli.py backtest-symbol QQQ --strategy etf --set rsi2_max=5 --cost-bps 5 --json out.json
```

## Hírek és hangulat (sentiment) – árnyék-üzem

A belépési jelöltek mellé a rendszer kiírja, milyen hírek és bejelentések jelentek meg a papírról az utolsó 3
kereskedési napban. **Csak tájékoztat: a jelzés kimenetelét nem változtatja meg**, amíg a validáció nem mutat mérhető
hatást (terv: [`docs/SENTIMENT-DESIGN.md`](docs/SENTIMENT-DESIGN.md), ticket #20).

**Források:**
- **SEC EDGAR:** 8-K bejelentések a tételkódokkal (pl. 4.02 könyvelési helyesbítés, 3.01 kivezetési értesítés, 1.03 csőd, 4.01 könyvvizsgáló-csere) és kibocsátási prospektusok. Ingyenes, évtizedekre visszamenőleg, ezért visszatesztelhető. A SEC azonosító User-Agentet kér: a `.env`-be írd be: `SEC_USER_AGENT="Neved email@cimed"`.
- **Yahoo:** a legfrissebb ~25 hír, kulcs nélkül. Csak az a hír számít, amelynek címében a ticker vagy a cégnév szerepel.
- **Alpaca News** (Benzinga): a paper kulcsokkal, ha a fiók csomagja tartalmazza.

**Pontozás:** két réteg kell az egyetértéshez:
1. általános hangulat-lexikon (VADER);
2. saját pénzügyi szólista és eseményfelismerés: leminősítés, előrejelzés-csökkentés, kibocsátás, per és vizsgálat, FDA, csőd stb., „lényegesség” 0–3 skálán.

A cégnevet és a tickert pontozás előtt kivesszük a szövegből: a „Super Micro” ne legyen pozitív.

**Point-in-time:** a *t* napi döntés csak a *t* nap 16:00 (New York) előtt megjelent híreket látja. A hír-volumen
z-értéke csak teljes lefedettségnél számolódik; hiányos lefedettségnél a felület „részleges lefedettség”-et ír,
és nem „nincs negatív hír”-t.

**Hol látszik:**
- a jelzés szűrőlistájában „Hírek: …” sorként (nem dönt);
- a Discord / Telegram értesítésben;
- a jelzésnaplóban (`news` mező);
- a belépési jelöltek táblázatában (▼ hír = lényeges negatív hír);
- a papír-oldal „Hírek” kártyáján, frissítés gombbal.

```bash
python cli.py news NVDA --fetch                         # letöltés + pontozott lista + a mai hír-jellemzők
python cli.py news SMCI --fetch --since 2024-01-01 --as-of 2024-10-31   # mit látott volna a döntés azon a napon
```

## Jelzésnapló, életút, visszajátszás

A jelzések megbízhatóságát csak akkor lehet mérni, ha minden döntés visszakereshető és reprodukálható.

- **Jelzésnapló** (`data/ledger/`): minden lezárt gyertyán kiértékelt belépési jelölt bekerül –
  **a kiküldöttek, a kihagyottak és az elutasítottak is**, a döntés okával („bukott szűrő: Trend”,
  „ismétlésvédelem”, „a long irány ki van kapcsolva”…), a szűrők mért értékeivel, a pontszám-bontással,
  a **beállítás-, kód- és kalibráció-verzióval** és az adat ujjlenyomatával. Így mérhető, hogy egy
  szűrő jó vagy rossz trade-eket dob-e ki.
- **Közös jelazonosító** (`sig-…`): a napló, az értesítés, a virtuális pozíció és a paper trade
  ugyanazt hordozza – a **Jelzésnapló** fülön minden jelzés **életútja** egy oldalon látszik
  (döntés → értesítés → pozíció → paper trade → „mi lett belőle”). A kihagyott / elutasított
  jelölteknél azt is mutatja, mi lett volna, ha kimegy.
- **A három oldal szinkronban van**: az Értesítések, a Jelzett trade-ek és a Jelzésnapló ugyanazt a
  jelzést ugyanazzal az azonosítóval mutatja. A Jelzésnapló „Trade” oszlopa ugyanazt az állapotot
  mutatja, mint a Jelzett trade-ek oldal. A **kézi beküldés** (Paper trading oldal) is naplózódik
  („Kézzel beküldve”, adat-pillanatképpel), és „Paper: beküldve” értesítés megy róla. Az azonosító
  bevezetése előtti értesítéseket és trade-eket a rendszer a papír + jelzésnap + alakzat alapján köti össze.
- **Visszajátszás**: a naplózott beállítással, kalibrációval és adattal újraszámolja a döntést, és
  mezőnként összeveti. A kiküldött jelzésekhez teljes adat-pillanatkép készül → bitre pontos; a
  többinél a mostani adatból, és felismeri, ha az eltérés csak osztalék / split miatti
  visszamenőleges árkorrekció.
- **Beállítás-változások**: ki, mikor, mit változtatott (a felületről), és minden beállítás-verzió
  eltárolva.
- **Csak lezárt gyertya**: a végleges elemzés (belépési jelzés, felület, `entries`, indexszkennelés)
  nyitvatartás alatt kihagyja a még alakuló mai gyertyát – különben egy tegnapi megerősített kitörés
  napközben „eltűnne”, a volumen 0,0x-nek látszana. A chart a mai gyertyát is mutatja.
- **Nyitás előtti újraellenőrzés** (hétköznap 9:05 NY = 15:05 magyar idő, a monitor futtatja; kézzel:
  `python cli.py revalidate`): az előző esti kiküldött jelzéseket újraszámolja a **véglegesített**
  napi adaton. Ha megáll → *megerősítve* a naplóban; ha nem → ↩️ **VISSZAVONVA** értesítés, a függő
  paper megbízás törlődik, a virtuális pozíció kimarad.

```bash
python cli.py ledger --decision rejected     # mit dobtak ki a szűrők, és miért
python cli.py trace sig-211f2d5a8912         # egy jelzés életútja
python cli.py replay sig-211f2d5a8912        # reprodukálható-e a döntés
```

Lemezplafon: havi fájlok, a lezárt hónapok tömörítve, 36 hónap után törlődnek; pillanatképek max. 50 MB
(a legrégebbi megy); a változásnapló max. 2000 sor.

---

## Teljes Nasdaq-100 és S&P 500 átnézése

Nem csak a saját watchlistet: a **Beállítások → Átnézett papírok** résznél bekapcsolható a teljes
**Nasdaq-100** (a QQQ által követett ~100 papír) és a teljes **S&P 500** (~500 papír). A monitor ezeket
**zárás után** nézi át (a belépési jelzés úgyis csak lezárt gyertyán megy ki), és rájuk is küld
belépési jelzést, követi a trade-eket, és – ha be van kapcsolva – paper trade-et is nyit.

- **Az összetétel** a Wikipédia naprakész táblázataiból jön, hetente frissül (`data/index_members.json`).
  Ha a letöltés nem megy, a legutóbb mentett listával dolgozik; egy gyanús (pl. 30 papíros) S&P 500
  listát nem fogad el.
- **Mikor fut:** amint bekapcsolsz egy indexet, **azonnal** lefut (ha a piac zárva van, az
  értesítések is kimennek – különben a mai megerősített kitörések elvesznének; nyitvatartás alatt
  csak az eredmény frissül). Utána **minden zárás után**: a `monitor` 16:15-kor, a webes felület
  16:20-kor (New York), ha aznap még nem futott – elég, ha a kettőből az egyik fut, és nem fut le kétszer.
  A kézi *Szkennelés most* gomb nem küld értesítést.
- **Zaj ellen:** az indexpapírokról alapból csak a valódi belépési jelzések mennek ki; az
  „előkészület” csak az *Előkészület-jelzés az indexpapírokra* kapcsolóval (500 papíron sok lenne).
- **Idő és hely:** a két index együtt ~510 papír (az átfedést és a watchlistet nem duplázza);
  az első futás letöltéssel ~1–2 perc, utána ~1 perc. Az adat tömörítve a `data/universe` alá kerül,
  ~30 MB; amit kikapcsolsz vagy ami kiesik az indexből, azt a takarítás 30 nap után törli.
- **Felület:** az Áttekintésen „Index-jelöltek” kártya (*Szkennelés most* gomb), a papír oldala a
  watchlisten kívüli papírokra is mutat chartot és belépési helyzetet, és onnan paper trade is küldhető.

```bash
python cli.py index-scan                    # a bekapcsolt indexek, értesítés nélkül
python cli.py index-scan --sp500 --all      # csak most az S&P 500, az elbukott jelöltekkel együtt
```

---

## Paper trading (Alpaca)

Az ajánlott swing belépéseket egy **Alpaca paper** (nem valódi pénzes) számlán lehet élesben tesztelni.

**Beállítás:** Alpaca → *Paper Trading* → *API Keys* → *Generate*, majd a webes felület
**Beállítások** fülén: Endpoint (`https://paper-api.alpaca.markets`), API Key ID, Secret Key →
*Mentés és kapcsolat-teszt*. A kulcsok csak sikeres teszt után kerülnek a `.env`-be; a felület
utána csak a kulcs utolsó 4 karakterét mutatja. **Csak a paper endpoint fogadható el** — valódi
pénzes számlára a kód nem tud megbízást küldeni.

**Kötési díj:** Beállítások → *Költségek* → *Kötési díj (% / kötés)* (`broker.commission_pct`, alapból 0, mert
az Alpaca jutalékmentes). A kötés értékének ennyi %-a vételkor és eladáskor is. Minden motor a saját csúszás-költsége
(`cost_bps` / `slippage_bps`) felett ezzel számol: élő virtuális követés, Backteszt oldal, kutatás és labor. A paper
trade-ek dollár-eredményéből is levonódik, a beküldéskori díjjal. Kötésenkénti minimumdíjat nem modellez.

**Hogyan kereskedik:** a jelzés tervét bracket megbízásokként küldi be (gtc, limit belépő):

| Láb | Darab | Cél | Stop |
|---|---|---|---|
| A | a pozíció fele | +1R (½ eladás) | a terv stopja |
| B | a másik fele | a minta célja | a terv stopja → **a belépőre húzva**, ha A célja teljesült |

`target` kilépési módnál (vagy 1 darabnál) egyetlen bracket megy a célig. A követett stopos
módok (`trail`, `partial_trail`) még nem támogatottak.

**A kiszállás azonnali, nem nap végi:** a cél és a stop az Alpacán élő megbízás, így napközben, amint az ár
eléri, teljesül. A szinkronizálás nyitvatartás alatt **percenként** fut (zárva 15 percenként és zárás után;
a felületen gombbal): lekéri a teljesüléseket és percen belül értesít róluk (Discord), a ½ eladása után
nullszaldóra húzza a maradék stopját, **visszavonja a belépőt, ha a jelzés utáni napon nem teljesült**, és az
**időkorlát** utolsó napján még a zárás előtti 10 percben piaci áron zár (mint a backtest, a napi záron —
nem a következő nyitáson). Eladás előtt ellenőrzi, hogy a cél vagy stop nem teljesült-e közben
(különben short pozíció keletkezne).

**Beküldés:** kézzel a **Paper trading** fülön vagy a papír oldalán (*Küldés a paper számlára*), vagy
automatikusan: Beállítások → *Automatikus beküldés* (alapból ki). Védelmek: egy jelzés csak egyszer,
papíronként egy trade, max. nyitott trade-szám (alap 5), szabad keret és meglévő Alpaca pozíció ellenőrzése.
**Margin nélkül:** csak a szabad készpénzből vesz (készpénz − függő vételek); az Alpaca paper számla 4x-es
„buying power”-ét nem használja. A Paper oldalon a „Lekötött tőke” és „A számla összetétele most” mutatja,
mennyi van pozícióban — a vétel az equity-görbét nem mozdítja (készpénzből részvény lesz).
A darabszám alapból a paper számla equity-jéből és a `risk_per_trade_pct`-ből számolódik (felülről a
`max_position_pct` korlátozza). Ha a portfólióban nincs hely (elérted a max. trade-számot, vagy nincs elég
szabad készpénz), a rendszer nem vásárol, és erről „nincs szabad hely” értesítést küld — nem hibaként.

```bash
python cli.py broker          # számla és paper trade-ek
python cli.py broker sync     # szinkronizálás kézzel
```

> **Belépés csak a nyitáskor (mint a backtestben).** A kitörés bracket-belépője gtc limit; ha a nyitás után
> `broker.entry_window_min` (alap 5) perccel sem teljesült – a nyitó a limit fölött volt –, a rendszer visszavonja,
> ahogy a backtest is kihagyja. (A napközben kézzel beküldött megbízásra nem vonatkozik; `0` = egész nap él.)
>
> **Visszahúzás:** a kilépési jel utáni reggel 9:05-kor (NY) a monitor az eladást a **nyitóaukcióra** (OPG) küldi,
> így az a backtest szerinti nyitóáron teljesül. A függő −5%-os limitek is helyet foglalnak a `max_open_trades`-ből,
> így esésnapon sem nyílhat a korlátnál több pozíció (a validáció szerint ez a gyengébb változat – tudatos döntés). Ha egy kilépő megbízást az Alpaca elutasít vagy visszavon, a rendszer újraküldi –
> a pozíció nem maradhat stop nélkül.
>
> **Élesítési kapu:** a `python cli.py broker` és a napi életjel stratégiánként mutatja, átment-e: ≥ 50 lezárt paper
> trade, ≥ 70% nyerő, profit-faktor ≥ 1,3, pozitív összesített eredmény (`broker.live_gate_*`), a nyerési arány
> 95%-os intervallumával. Részletek és indoklás: [`docs/REVIEW-2026-10.md`](docs/REVIEW-2026-10.md).

---

## Webes felület

```bash
python cli.py web          # megnyitja: http://127.0.0.1:8765
```

Sima HTML/CSS/JS, a Python beépített webszerverével: **nincs új függőség, nincs CDN**,
internet nélkül is fut (csak a Frissítés gomb tölt le adatot). Világos és sötét téma, mobilon is használható.

| Oldal | Mit mutat |
|---|---|
| **Áttekintés** | piaci rezsim, mai belépési jelzések / figyelendők / követett trade-ek; jelölt-tábla (belépő, stop, ½ eladás, cél, R/R, darab, *miért esett ki*); a figyelt papírok napi állapota |
| **Papír** (kattints egy sorra) | interaktív napi chart: gyertyák, EMA20/50/200, volumen, alakzat-geometria, a kereskedési terv vonalai, support/rezisztencia; a belépési helyzet szűrőkkel és pontszám-bontással; mutatók |
| **Saját pozíciók** | a kézzel felvett pozícióid kiértékelése, felvétel / szerkesztés / lezárás |
| **Paper trading** | Alpaca paper számla: **eddigi eredmény** (a számla a nyitás óta + a rendszer trade-jei realizált / nyitott bontásban), equity-görbe táblázattal, mai beküldhető jelzések, a rendszer paper trade-jei lábanként, Alpaca pozíciók és megbízások |
| **Jelzett trade-ek** | a követett trade-ek állapota (belépésre vár / nyitott / lezárt / kimaradt) és az élő eredménynapló R-ben |
| **Értesítések** | minden, amit a monitor kiküldött, utólag kiértékelve: jelzéskori és mostani ár, irányhelyes változás, profit ($ / R), minősítés (jó / rossz / folyamatban); jelerősség (belépési pont vagy alakzat-minőség, gyenge / közepes / erős) és találati arány erősség szerint; szűrés irány, minősítés és erősség szerint; a típusokra és oszlopokra húzott egér magyarázatot mutat |
| **Gyakorlás** | rejtett, múltbeli chart-helyzetek: döntés (kihagyás / long / short stoppal), felfedés, ítélet és lépésenkénti levezetés, összevetés a stratégiákkal és egy véletlen alapvonallal, kör-statisztika (lásd: Gyakorló mód) |
| **Beállítások** | számlaméret, kockázat, belépési és kilépési szabályok, kötési díj, értesítések nyelve, paper trading és Alpaca kulcsok, Discord webhook, watchlist, a `settings.yaml` kommentjeit megtartva |
| **Információ** (jobb felül) | hibabejelentés: a rendszer GitHub oldalának Issues füle (link: <https://github.com/vkbubu11/StockWatch/issues>) |

A felület **nem küld riasztást**, és kereskedni csak az Alpaca paper számlán tud, kézi gombnyomásra; az értesítéseket továbbra is a
`python cli.py monitor` küldi (a kettő futhat egyszerre). Csak a `127.0.0.1` címen érhető el,
a módosító kérések egyedi fejlécet várnak (más weboldal nem tud a nevedben beállítást átírni),
a `.env` titkait nem adja vissza (az Alpaca kulcsokat beírni lehet, kiolvasni nem). A régi Streamlit dashboard (`streamlit run app/dashboard.py`) továbbra is megvan.

### Nyelvek: magyar, angol, német, spanyol

- **Felület:** a fejléc nyelvválasztója (HU / EN / DE / ES). A böngésző megjegyzi; alapból a böngésző nyelve, ha a négy
  közül való, különben magyar. A számok és dátumok formátuma is a választott nyelvet követi.
- **Értesítések** (Discord, Telegram, webhook, konzol): Beállítások → *Értesítések nyelve* (`alerts.language`: `hu`,
  `en`, `de`, `es`). A jelzésnapló (`data/signals.jsonl`) mindig magyar marad; a felület a saját nyelvén jeleníti meg.
- A parancssor (`cli.py`), a naplófájlok és a kutatási jelentések magyarok maradnak.

**Hogyan működik:** a rendszer magyarul fogalmaz, a fordítás a megjelenítés / kiküldés előtt történik. A felületen a
`app/web/i18n.js` a kirajzolt szöveget és a `title` / `placeholder` / `aria-label` attribútumokat cseréli (adatot,
`data-*` attribútumot, URL-t nem), az értesítéseknél a `src/stockwatch/i18n.py` soronként fordít. Mindkettő ugyanazt a
katalógust használja: `src/stockwatch/locale/catalog.jsonl`, soronként egy `{"hu", "en", "de", "es"}` objektum
(a felület a `/i18n/<nyelv>.json` címen kapja meg). A kulcs a magyar szöveg; a `{0}`, `{1}`… helyőrző bármit illeszt
(a `{1:n}` csak számot), a befogott rész maga is fordul. Az összetett sorokat (`címke: érték`, `a · b`, `a, b`,
`[stratégia] PAPÍR: …`) a fordító a zárójeleken kívüli elválasztóknál tagolja, és darabonként fordítja.
**Ami nincs a katalógusban, magyarul jelenik meg** – új szövegnél egy sort kell felvenni a katalógusba;
a `tests/test_i18n.py` ellenőrzi, hogy minden sorban mind a négy nyelv megvan, és a helyőrzők egyeznek.

---

## Stratégia-labor (Labor fül)

Belépési stratégiák **folyamatos tesztje múltbeli adatokon**, alapból a **Nasdaq-100 + S&P 500 összes
papírján** (~520). Egy külön háttérfolyamat (`python cli.py lab`, szerveren a `stockwatch-lab` szolgáltatás)
körről körre dolgozik, a felületen pedig új kísérlet vehető fel: a mezők a mostani beállítással vannak
kitöltve, csak azt kell átírni, amit tesztelni szeretnél.

| Lépés | Mit csinál |
|---|---|
| **Adat** | a hiányzó / régóta nem frissült papírok letöltése (a monitor zárás utáni indexszkennelése közben nem) |
| **Kitörések** | papíronként **egyszer** kigyűjti a bikás kitöréseket, utána naponta csak az új gyertyákat (`data/lab/events`). Ha egy osztalék átírja a múltbeli (korrigált) árakat, azt a papírt újraszámolja |
| **Értékelés** | új kísérlet vagy friss adat után végigjátssza a kísérleteket – ugyanazzal a szimulációval, mint a kutatás (másnapi nyitás, gap-limit, csúszás, pesszimista kilépés). Papíronként halad: egyszerre csak egy papír árfolyama van a memóriában |
| **Gépi tanulás** | hetente / kérésre (lásd lent) |

**Becsületesség:** tanító (2022 előtt) és validációs (2022 – 2025. március) időszak látszik. A **holdout**
(2025. április → a kísérlet létrehozása) rejtve marad; kísérletenként, megerősítéssel nyitható meg, naplózva.
Az **Élő** oszlop a kísérlet **létrehozása utáni** új adaton mért eredmény – ezt a stratégia kitalálásakor
még senki nem látta, ez a valódi mintán kívüli teszt. A felület mutatja, hány kísérlet futott eddig: sok
változat közül a legjobb részben szerencse. **Robusztus** az a kísérlet, amelyik mindkét időszakban és a
várható érték alsó 95%-os határában sem rosszabb az élő beállításnál.

Első induláskor a labor létrehozza az **élő beállítást** (mindig a mostani `settings.yaml` szerint) és a
**2026. októberi vizsgálat** 13 változatát (`entry-research --study`) – így a kutatás az indexeken is
folyamatosan fut, és minden új nappal bővül az élő mérés.

### Gépi tanulás

Gradient boosting modell (scikit-learn) becsüli egy kitörés várható eredményét a jelzés napján ismert
adatokból: alakzat, minőség, RSI, ADX, volumen, távolság a triggertől, stop %, hozam/kockázat, volatilitás,
EMA-k, 52 hetes csúcs, 12–1 momentum, relatív erő, összehúzódás, piaci rezsim. **Évenként előre haladó
teszt:** minden évre csak az addig *lezárult* trade-ekből tanul, és a tanító halmazon becsült legjobb 40%-ot
választja. Összehasonlítás ugyanazokon az éveken: minden kitörés / a mostani szűrők / a modell / a kettő
együtt, és a becslés rangkorrelációja (IC) a tényleges eredménnyel. A holdout itt is kimarad.
**Élesben a modell semmiről nem dönt**, amíg mintán kívül tartósan jobb nem a mostani szűrőknél.

### Árnyék-üzem (készenlétben)

Minden tanítás után a labor egy **éles használatra kész modellt** is elment (`data/lab/ml/model.pkl`, egyetlen
fájl, minden lezárult trade-ből tanítva). Az árnyék-üzem (`settings.yaml` → `lab.shadow`):

| Mód | Mikor fut |
|---|---|
| `auto` (alap) | csak ha a legutóbbi walk-forward teszt szerint a modell **mintán kívül jobb** a mostani szűrőknél (az EV alsó 95%-os határa is felettük) – addig készenlétben vár, és magától indul |
| `on` | mindig, ha van mentett modell (kipróbálásra) |
| `off` | soha |

Ha fut: a monitor minden kitöréses jelzéshez (a szűrőn elbukottakhoz is) kiszámolja a modell becslését –
**pontosan ugyanazokkal a jellemzőkkel, mint a tanításban** –, és a jelzés mellé írja: az értesítésben
„Gépi tanulás (árnyék-üzem, nem dönt): becsült +0,32R – a modell VÁLASZTANÁ”, a felületen a jelöltek mellett
„ML: +0,32R ✓”. Zárás után naplózza (`data/lab/ml/shadow.jsonl`, legfeljebb 20 000 sor), a labor pedig
kiszámolja a már lefutott jelzések kimenetelét ugyanazzal a szimulációval. A Labor fül összeveti: a modell
által **választott** vs. **elvetett** jelzések, és a szűrőn átmentek közül azok, amelyeket a modell kiszűrne.
**A modell semmiről nem dönt** – szűrőként csak akkor érdemes bekapcsolni, ha ez az összevetés valódi új
adaton tartósan igazolja.

### Erőforrás és lemez

A szolgáltatás `SCHED_IDLE` ütemezéssel fut: csak akkor kap processzoridőt, ha a gépen semmi más nem akar
futni (a monitor és a felület mindig elsőbbséget kap), és memóriaplafonja van (450 MB). Pihenőkörben nem
olvas be minden fájlt (módosítási idő alapján dönt). Lemez: legfeljebb 60 kísérlet, kísérletenként
legfeljebb 20 000 trade; a 30 napja nem használt papírok cache-e törlődik; a `data-usage` külön sorban mutatja
(kb. 20–40 MB az indexeken). Az első teljes kör a gyenge szerveren **1–2 nap** (a kitörések egyszeri
kigyűjtése); utána naponta percek.

```bash
python cli.py lab                 # háttérfolyamat (helyben: --workers 8 a gyorsabb első körhöz)
python cli.py lab --once          # egy kör, utána kilép
python cli.py lab status          # állapot és kísérletek
sudo stockwatch-cli lab status    # szerveren
```

---

## „Legjobb belépő” gomb

A fejlécben, a Frissítés mellett. Lenyomásra **az adott pillanatban** megkeresi a legjobb **long** és
**short** belépőt a Beállításokban választott listán („Átnézett papírok” → *„Legjobb belépő” gomb listája*:
watchlist, Nasdaq-100, S&P 500, a kettő együtt, vagy minden). Letölti a legfrissebb árakat (nyitvatartás
alatt a mai, még alakuló gyertyát is – ezt az ablak jelzi), és egy ablakban mutatja:

- a legjobb belépőt kész tervvel (belépő/limit, stop, cél, hozam/kockázat, darabszám, szűrők, kilépési terv),
  és ha fut, a gépi tanulás árnyék-becslését;
- további belépőket, a még ki nem tört **előkészületeket**, és a **majdnem-jelölteket** azzal, melyik szűrőn buktak el.

A long oldal ugyanaz a belépési motor, mint az értesítéseknél. **A short oldal kísérleti** (`short.py`, a
long motor tükörképe: lefelé tartó trend, RSI 28–55, megerősített letörés, stop a belépő felett, a QQQ az
SMA200 alatt): **nincs backtesttel igazolva**, és a paper trading nem küldi be.

Nagy listán (~520 papír) a teljes elemzés a gyenge szerveren túl lassú lenne: ilyenkor az **esti
indexszkennelés** legjobb long és short jelöltjeit (és a watchlistet) számolja újra friss árral – kb. 1–3
perc. Ha még nem volt indexszkennelés, a teljes listát nézi át. A gomb semmit nem küld ki és nem kereskedik.

---

## Futtatás szerveren (folyamatos paper trading teszt)

A hosszú paper trading teszthez a monitornak éjjel-nappal futnia kell. Ehhez egy kis Linux VPS
kell **Ubuntu 24.04** (vagy Debian 12) rendszerrel: 1 vCPU, 1–2 GB RAM, 10 GB lemez elég.
Ingyenes PHP-tárhelyen (pl. InfinityFree) nem fut: ott nincs Python és nem futhat folyamatos program.

**Telepítés a saját gépedről** (PowerShell, a projekt mappájában):

```powershell
# első alkalommal: kód + config + data + .env (titkok), HTTPS-sel
.\deploy\upload.ps1 -Server root@<szerver-IP> -Domain stockwatch.pelda.hu

# frissítés: csak a kód; a szerveren lévő adat, beállítások és titkok maradnak
.\deploy\upload.ps1 -Server root@<szerver-IP> -Domain stockwatch.pelda.hu -CodeOnly
```

A telepítő ([deploy/install.sh](deploy/install.sh)) újrafuttatható. Az első futáskor bekéri a webes felület jelszavát (min. 12 karakter).
ARM gépen (pl. Oracle Ampere A1) a chart-képekhez a Playwright arm64-es Chromiumát teszi a `/opt/chromium`-ba (~280 MB, fix méret),
mert a Chrome for Testing-nek nincs linux-arm64 változata.
Két szolgáltatást állít be, mindkettő a gép újraindulása után magától indul, és leállás után 30 / 10 mp múlva újraindul:

| Szolgáltatás | Mit csinál |
|---|---|
| `stockwatch-monitor` | `cli.py monitor`: jelzések, értesítések, Alpaca paper trading |
| `stockwatch-web` | `cli.py web --behind-proxy`: csak `127.0.0.1:8765`, kívülről a Caddy (HTTPS) éri el |
| `stockwatch-lab` | `cli.py lab`: stratégia-labor, a legalacsonyabb prioritással |

**Domain:** a domain (vagy aldomain) DNS **A rekordja** mutasson a szerver IP-címére, a 80-as és 443-as port legyen nyitva.
A tanúsítványt a Caddy automatikusan kéri és megújítja (Let's Encrypt). `-Domain` nélkül nincs nyilvános cím, és a felület
SSH-alagúton érhető el: `ssh -L 8765:127.0.0.1:8765 root@<szerver-IP>`, utána böngészőben `http://127.0.0.1:8765`.

**Titkok a szerveren:** a `/etc/stockwatch/stockwatch.env` fájlban vannak, a kódon kívül, `0600` jogosultsággal,
és csak a `stockwatch` rendszerfelhasználó olvashatja. A program a `STOCKWATCH_ENV_FILE` környezeti változóból tudja, hol keresse.
Az Alpaca kulcsokat a felületen is meg lehet adni, ide íródnak. A webes jelszónak csak a scrypt hash-e kerül ide, maga a jelszó sehol.

**Bejelentkezés:** szerveren (`--behind-proxy`) a jelszó kötelező; jelszó nélkül a felület senkit nem enged be.
A munkamenet 7 napig érvényes (HttpOnly + Secure + SameSite=Strict süti), a szolgáltatás újraindításakor vagy jelszócserekor kilép.
IP-címenként 5 hibás próbálkozás után 15 percig zárolva. Helyben, Windowson (`start.bat`) jelszó nélkül fut, mint eddig.

**Kódvédelem:** a kód a `/opt/stockwatch` alatt root tulajdonú és írásvédett; a szolgáltatás csak a `data/`, `config/`, `logs/`
mappát és a titokfájlt írhatja (systemd `ProtectSystem=strict`). A rendszernaplót a telepítő 200 MB-ra korlátozza.

| Teendő | Parancs a szerveren |
|---|---|
| Napló | `journalctl -u stockwatch-monitor -f` |
| Állapot | `systemctl status stockwatch-monitor stockwatch-web` |
| Újraindítás | `sudo systemctl restart stockwatch-monitor stockwatch-web` |
| Jelszócsere | `sudo stockwatch-password` |
| Discord beállítása / cseréje | a felületen: Beállítások → Discord értesítések, vagy `sudo stockwatch-cli discord` |
| Bármely CLI-parancs | `sudo stockwatch-cli <parancs>`, pl. `positions`, `broker`, `test-alert` |
| Titkok szerkesztése | `sudo -u stockwatch nano /etc/stockwatch/stockwatch.env`, utána újraindítás |

---

## Lemezhasználat — nem nő korlátlanul

A monitor hónapokig futhat felügyelet nélkül, ezért **minden írt fájlnak felső korlátja van**:

| Fájl | Korlátozás | Plafon |
|---|---|---|
| `data/ohlcv/*.csv` | 10 éves retention, minden íráskor vágva | ~2,5 MB |
| `data/signals.jsonl` | méretalapú rotáció, max 3 archívum | 20 MB |
| `logs/app.log` | `RotatingFileHandler` | 20 MB |
| `data/state.json` | csak aktív jelzések, 90 nap után pruning | < 100 KB |
| `data/calibration.json` | minden futás felülírja | < 1 MB |
| `data/holdings.json` | max. 50 nyitott + 200 lezárt saját pozíció | < 200 KB |
| `data/broker.json` | max. 50 nyitott + 500 lezárt paper trade | < 1 MB |
| `data/universe/*.csv.gz` | csak a bekapcsolt indexek + kutatási minta; a többi 30 nap után törlődik | ~30 MB (két index) |
| `data/index_members.json`, `data/index_scan.json` | minden futás felülírja | < 200 KB |
| `data/ledger/` | havi fájlok, tömörítve, 36 hónap; pillanatképek max. 50 MB | ~5–10 MB / év + pillanatképek |
| `data/users/<munkaterület>/digest/` | reggeli összefoglalók, 90 nap (`digest.keep_days`) | ~5 MB |
| `data/users/<munkaterület>/agent/` | kutatóügynök: legfeljebb 20 brief, 30 futás, 50 MB; lépésnapló futásonként max. 5000 sor | ≤ 50 MB |
| `data/users/<munkaterület>/events/` | eseménytanulmányok: legfeljebb 30 db, 30 MB | ≤ 30 MB |
| `data/events/form4/` | Form 4 kivonat évenként; a legrégebbi év törlődik | ≤ 50 MB |
| `data/events/index_changes.json`, `sectors.json` | minden letöltés felülírja | < 1 MB |
| `data/users/<munkaterület>/practice/` | gyakorló körök: legfeljebb 100 | ≤ 5 MB |
| `data/practice/pool.json.gz` | a gyakorló mód feladatkészlete, adat-ujjlenyomatonként felülíródik | ≤ 5 MB (jellemzően ~40 KB) |
| `data/queue/` | labor-munkasor: munkaterületenként max. 10 várakozó feladat, a befejezett 7 nap után törlődik | < 100 KB |
| `data/users/<munkaterület>/usage.json` | felhasználónkénti CPU-idő és futásszám, 24 hónap | < 50 KB |

**Állandósult lábnyom: ~35 MB**, és nem nő tovább. A Telegramra küldött chart memóriából
megy ki — ideiglenes PNG nem keletkezik a lemezen.

Ellenőrzés bármikor: `python cli.py data-usage`

---

## Hogyan működik

```
yfinance ──> CSV cache ──> indikátorok ──> swing pivotok
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    ▼                          ▼                          ▼
            support/rezisztencia        formációfelismerés           trend/momentum
                    │                          │                          │
                    └──────────────────────────┼──────────────────────────┘
                                               ▼
                                     kompozit pontszám
                                               │
                              backtest kalibráció ──> valószínűség
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                               dashboard              Telegram
```

Minden a **swing pivotokra** épül, nem a nyers gyertyákra — ettől robusztus a felismerés.

---

## Három dolog, amiben ez a rendszer szigorú magával

### 1. Nincs kitalált valószínűség

A kompozit pontszám irányt és meggyőződést fejez ki — **nem valószínűséget**. A megjelenített
százalék kizárólag a backtestből származik. Ha egy formációra kevesebb mint 30 historikus
minta van, a rendszer azt írja ki: *„nincs elég adat"*.

A watchlist 10 papírján ~440 triggerelt formáció keletkezik 10 év alatt, ami csak a
leggyakoribb mintákhoz (zászlók) ad elég mintát. Több statisztikához bővítsd a watchlistet,
vagy futtasd a backtestet szélesebb univerzumon.

### 2. Look-ahead védelem, három rétegben

A backtestbe beszivárgó jövőbeli információ a leggyakoribb hiba az ilyen rendszerekben —
és irreálisan jó eredményt ad, ami hamis biztonságot szül. Három védelem:

- **A legutolsó pivot soha nem „megerősített"** — a backtest csak megerősített pivotot használ
- **A detektor csak az adott napig terjedő adatszeletet kapja**
- **Csak az aznapi kitörést könyveljük belépésnek** — egy 3 hete kitört minta felismerése nem
  ér, mert akkor még nem tudtuk volna

> A fejlesztés során ez valós hibát fogott meg: a fej-váll kezdetben **100% találati arányt**
> mutatott 30 mintán. A harmadik védelem bevezetése után 47 ügyletre és ~50%-os arányra esett
> vissza. A `backtest.py` ezért automatikusan figyelmeztet, ha bármely formáció 85% fölé megy.

### 3. Zajon hallgatnia kell

A hamis pozitív itt a valódi kockázat: egy rendszer, ami mindenben fej-vállat lát, rosszabb
a semminél. A tesztek véletlen bolyongáson és tiszta trenden is futnak, és tiltják a magas
minőségű találatot. A legerősebb szűrő nem küszöbhangolás, hanem elvi: **a fordulós mintához
megelőző trend kell** (amit megfordít), a **folytatólagoshoz** szintén (amit folytat).

---

## Tesztek

```bash
python -m pytest tests/ -q
```

521 teszt fut, köztük:

- **Pozitív**: matematikailag tökéletes formációkon a detektornak találnia kell, és a
  kulcsszinteknek a generált geometriára kell esniük
- **Negatív**: véletlen bolyongáson és tiszta trenden nem lehet magas minőségű találat
- **Look-ahead**: az utolsó pivot nem lehet megerősített
- **Belépési motor**: minden szűrő külön elbuktatva, pozícióméret-matek, visszateszt vs.
  hamis kitörés, cooldown, és hogy a „jövő” gyertyái nem változtatják a mai döntést
- **Szimuláció**: cél/stop/gap/időkorlát, részleges kilépés + nullszaldós stop, követett stop;
  egy gyertyán cél+stop → stop (pesszimista)
- **Pozíciókövetés**: belépés / kihagyás / +1R / nullszaldó / stop események, újrafuttatás nem duplikál
- **Saját pozíciók**: validálás, kiértékelés (stop, trend, medvés minta, visszaesés, earnings),
  értesítés csak egyszer és csak romlásnál
- **Paper trading** (hamis Alpacával, hálózat nélkül): csak paper endpoint, bracket-lábak, duplikátum- és
  vételierő-védelem, nullszaldós stop, lejárt belépő visszavonása, időkorlát dupla eladás nélkül
- **Lemezhasználat**: 50 000 jelzés beírása után a `data/` a plafon alatt marad, és a
  legfrissebb jelzés megmarad
- **Őr-tesztek az új funkciókhoz** (`tests/test_guards.py`): az új modulok, és amit tranzitívan (függvényen belül is)
  importálnak, nem érik el a broker modult; tiltólista az ajánlás jellegű szóhasználatra négy nyelven
- **Eseménytanulmányok** (`tests/test_events.py`): ismert CAR visszamérése szintetikus adaton, point-in-time (a 0. nap
  utáni ár nem hat a becslésre; a detektorok a későbbi gyertyák nélkül ugyanazt adják), 0. nap-szabályok, Form 4 parser
  fixtúrákon (XML és negyedéves csomag), SEC-kéréskorlát folyamatok között és User-Agent, indexváltozás-parser,
  átfedés, a holdout csak kapcsolóval és naplózva
- **Kutatóügynök** (`tests/test_research_agent.py`): az ügynök adata a holdout előtt véget ér, és a holdout-napló nem
  változik; a keresés csak a tanító időszakon fut, a validációt csak a döntősök látják, egyszer; rögzített seeddel a
  jelentés bitre azonos; paritás a `meanrev_research.run`-nal; az érvénytelen paramétert a validátor elutasítja, és a
  keresés folytatódik; a CPU-keret és a határidő leállítja, de a részjelentés elkészül; a Homokozó-átvétel nem köt
  paper fiókot, és `alerts=false`; a munkasor; a Kitörés-motor a laboron át, vágott adattal
- **Reggeli összefoglaló** (`tests/test_digest.py`): változásérzékelés szintetikus állapotpárokon, sorrend és plafon,
  a szöveg minden száma a forrásadatból jön, nincs ajánló szóhasználat, a 06:30-as budapesti ütemezés nyári és
  téli időben, teljes futás szintetikus adaton a jövőbeli gyertyák nélkül

---

## Tudni érdemes korlátok

- **Az adat ~15 perc késleltetésű** (yfinance). Napi swing időtávon ez nem számít, napon
  belüli kereskedéshez viszont nem elég — ahhoz Alpaca vagy Polygon kellene. Az adatréteg
  cserélhető adapterként készült (`providers/base.py`).
- **A napi gyertya csak záráskor végleges.** Nyitvatartás alatt a monitor 15 percenként
  frissít, de az akkori jelzés még változhat.
- **A backtest nem számol tranzakciós költséggel, csúszással és osztalékkal.** A találati
  arány geometriai, nem kereskedési eredmény.
- **A walk-forward vizsgálat megmutatja, ha egy statisztika nem stabil.** A bear zászló
  például 15% → 36%-ra változott a két időszak között — az ilyen számot óvatosan kezeld.
- **Valódi pénzzel nem kereskedik.** Csak Alpaca paper számla köthető be; éles számlán minden megbízás kézi.
- **A `time`-alapú piaci naptár** `pandas_market_calendars` nélkül fix 9:30–16:00-tal számol.

---

## Projektstruktúra

```
config/          watchlist.yaml, settings.yaml
src/stockwatch/
  config.py        pydantic beállítások + .env
  providers/       cserélhető adatforrás (yahoo)
  feed.py          inkrementális frissítés
  store.py         CSV cache retention-nel
  housekeeping.py  lemezhasználat-korlátozás
  indicators.py    saját EMA/RSI/ATR/MACD/ADX/Bollinger/OBV
  structure.py     swing pivotok, trendvonalak   ← minden erre épül
  levels.py        support/rezisztencia motor
  patterns/        formációdetektorok
  forecast.py      kompozit pontozás
  entry.py         swing long belépési motor (szűrők, pontszám, terv)
  market.py        papíronkénti rezsim (QQQ / S&P 500 / saját trend), relatív erő, earnings-naptár
  meanrev.py       Visszahúzás (RSI2) motor – a kutatás és az éles futás közös logikája
  meanrev_research.py  a Visszahúzás validálása (portfólió-szimuláció, elfogadási feltétel, homokozó-backtest)
  entry_research.py belépési szabályváltozatok + walk-forward
  trade_mgmt.py    pozíciókezelés (részleges kilépés, stop, követett stop)
  positions.py     a jelzett trade-ek követése és értesítés
  holdings.py      saját pozíciók felvétele és kiértékelése
  broker.py        Alpaca paper kereskedés (bracket, szinkron, kilépési szabályok)
  signal_review.py a korábbi értesítések utólagos minősítése
  indexes.py       Nasdaq-100 / S&P 500 összetétel (Wikipédia, heti cache)
  ledger.py        jelzésnapló, verziók, adat-pillanatképek
  replay.py        egy jelzés életútja és visszajátszása
  sessions.py      lezárt gyertya (a mai, alakuló gyertya kihagyása)
  revalidate.py    nyitás előtti újraellenőrzés a véglegesített adaton
  webapp.py        a webes felület szervere (JSON API)
  i18n.py          fordítás (angol / német / spanyol); katalógus: locale/catalog.jsonl
  webauth.py       bejelentkezés: scrypt jelszó, munkamenet, találgatás elleni zár
  lab.py           stratégia-labor: kísérletek, növekményes kitörés-cache, háttérfolyamat
  lab_ml.py        gépi tanulás walk-forward teszttel, végső modell mentése
  shadow.py        gépi tanulás árnyék-üzemben: becslés a jelzések mellé, napló, kimenetel
  short.py         kísérleti short belépési motor (a long tükörképe)
  best.py          „Legjobb belépő” gomb: legjobb long és short most
  locks.py         folyamatok közötti fájlzár, atomi írás
  backtest.py      historikus kalibráció
  monitor.py       figyelő ciklus + állapotgép
  charting.py      plotly chartok
  alerts/          konzol, JSONL, Discord, Telegram, webhook
  digest/          reggeli összefoglaló: pillanatkép, változásérzékelés, pontozás, sablonos szöveg, archívum
  events/          eseménytanulmányok: Form 4, S&P 500 változások, eseménykatalógus, CAR, bootstrap
  research_agent/  kutatóügynök: brief, keresési tér, determinisztikus keresés, döntősök, jelentés
  practice/        gyakorló mód: feladatkészlet, rejtett normalizált chart, szimuláció, összevetés, kör-statisztika
  labqueue.py      a labor fájlalapú munkasora (kutatóügynök, később eseménytanulmány, publikálás)
  sandbox_adopt.py paraméterek átvétele a Homokozóba (alerts=false, paper-kötés nélkül)
  workspace.py     a felhasználóhoz tartozó új adatok helye (data/users/<munkaterület>/)
  usage.py         felhasználónkénti CPU-idő és futásszám (a későbbi kvótákhoz)
app/web/           webes felület (index.html, login.html, style.css, app.js, i18n.js)
deploy/            szerveres telepítés (install.sh, systemd szolgáltatások, upload.ps1)
app/dashboard.py   Streamlit felület (régi)
tests/             521 teszt + szintetikus generátorok
cli.py             parancssori felület
```

### Miért saját indikátorok?

A `pandas-ta` eltörik numpy ≥ 2 alatt (`from numpy import NaN`), a TA-Lib pedig C fordítást
igényelne Windowson. A szükséges nyolc indikátor tiszta pandasban ~150 sor — cserébe nulla
függőségi kockázat és pontosan ismert szemantika.

### Chart színek

A paletta CVD-validált (színvakság-biztos). A zöld/piros pár a határsávban van, ezért két
másodlagos kódolás egészíti ki: **üreges emelkedő / tömör eső gyertyatest**, és minden
szinten **szöveges címke**. Ezeket ne távolítsd el.
