# Hír- és hangulatelemzés (sentiment) – terv a StockWatch-hoz

Állapot: **terv**, még nincs implementálva. Kiindulópont: a [shirosaidev/stocksight](https://github.com/shirosaidev/stocksight)
átnézése (2026-10-15).

---

## 1. Mit csinál a stocksight, és mit veszünk át belőle

**Felépítés.**

- `sentiment.py`: Twitter-stream (tweepy) vagy hírcímek (a Yahoo Finance oldal `<h3>` elemeinek leszedése), opcionálisan a linkelt cikk első 10 bekezdése.
- Szűrés NLTK tokenekkel: kötelező és tiltott kulcsszavak, legalább 5 token.
- Pontozás: **TextBlob polaritás + VADER compound** átlaga. Címke: pozitív vagy negatív, ha mindkettő egyirányú (±0,05 küszöb), különben semleges. Opcionálisan egy külső web-API is szavaz.
- Tárolás: Elasticsearch 5; `stockprice.py` percenként hozzáteszi az árat; megjelenítés Kibana-dashboardon.

**Értékelés kereskedői szemmel.**

| | Megfigyelés | Következmény nálunk |
|---|---|---|
| ✓ | Több pontozó egyetértése kell a nem semleges címkéhez (konzervatív címkézés) | Átvesszük: ensemble + „bizonytalan = semleges” |
| ✓ | Kulcsszavas relevancia-szűrés és zajszűrés (rövid szöveg, RT, linkek) | Átvesszük, de ticker- és cégnév-egyeztetéssel |
| ✗ | **Nincs validáció.** A README kérdése („mennyire hat a hangulat az árra? derítsük ki”) megválaszolatlan maradt, nincs backtest | Nálunk kötelező: tanító / validáció / zárolt holdout, mint minden szabálynál |
| ✗ | **Nincs point-in-time adat.** A hír időbélyege a *letöltés* ideje (`datetime.utcnow()`), nem a megjelenésé, és múltbeli adat nincs | A megjelenés időpontja kell, történeti hírarchívummal |
| ✗ | Általános lexikon (TextBlob, VADER): pénzügyi szövegen gyenge („beats”, „liability”, „short squeeze”, „downgrade” rosszul pontozódik) | Pénzügyi lexikon (Loughran–McDonald) és / vagy FinBERT, eseménytípus-felismerés |
| ✗ | HTML-scraping (törékeny, ÁSZF-kockázat); a Twitter v1.1 stream API 2023 óta nem elérhető ingyen; a projekt 2020 óta nem frissül; Elasticsearch 5 | Hivatalos API-k, fájlalapú tárolás a meglévő lemezplafonnal; nincs új szerverkomponens |
| ✗ | Hiba: a tiltott token esetén a belső `continue` nem ugorja át a hírt (`NewsHeadlineListener`) | Nem vesszük át a kódot, csak az ötletet (Apache-2.0 licenc, de nincs mit átemelni) |

**Összefoglalva:** a stocksight jó demó (adatgyűjtés + dashboard), de **nem kereskedési jel**. Az ötleteit
(több pontozó, relevancia-szűrés) átvesszük. Az architektúrát nem: Elasticsearch, Twitter és scraping
helyett hivatalos, időbélyeges, visszatesztelhető forrásokat használunk.

---

## 2. Cél és hipotézisek (előre rögzítve)

A sentiment **nem önálló stratégia**, hanem **szűrő és kockázati jelzés** a meglévőkhöz. Hipotézisek,
amelyeket a tesztelés *előtt* rögzítünk:

- **H1 – a visszahúzás „hírrel vagy hír nélkül”** (a legfontosabb). Ha egy papír hír nélkül esik túladottá
  (likviditás, piaci zaj), az esés jobban visszafordul, mint ha **lényeges negatív hír** okozta
  (bevételi előrejelzés csökkentése, leminősítés, per, részvénykibocsátás).
  - Szakirodalom: Chan (2003), *Stock price reaction to news and no-news*: a hírrel járó mozgás után sodródás jön, a hír nélküli után visszafordulás. Tetlock (2007, 2008).
  - Várt hatás a Visszahúzás és a Mély visszahúzás stratégiára: **magasabb nyerési arány, kevés kiesett kötéssel.**
- **H2 – a Kitörés megerősítése.** A pozitív hír és a megnövekedett hírvolumen mellett bekövetkező kitörés gyakrabban folytatódik (hír-momentum).
- **H3 – kockázati riasztás a saját pozíciókra.** A tartott papírnál megjelenő lényeges negatív hír korai figyelmeztetés, még a stop előtt. Ez nem kereskedési szabály, hanem értesítés.

Elfogadás (a `meanrev_research.GATE` mintájára): a szűrő csak akkor kerülhet döntési szerepbe, ha a **tanító és a validációs** időszakban külön-külön is
- emeli a nyerési arányt (≥ +2 százalékpont),
- nem rontja a várható értéket és a profit-faktort,
- a kötések legfeljebb 25%-át ejti ki,

és a zárolt holdout egyszeri megnyitásán sem fordul meg az irány.

---

## 3. Adatforrások

| Forrás | Mire jó | Történeti adat | Költség | Prioritás |
|---|---|---|---|---|
| **Alpaca News API** (`data.alpaca.markets/v1beta1/news`, Benzinga-hírfolyam) | Hírcím, összefoglaló, tickerek, **megjelenési időbélyeg** | **igen, 2015-től** → visszatesztelhető | a meglévő paper kulcsokkal elérhető | **P0** |
| SEC EDGAR (8-K, 424B, S-3, Form 4) | Lényeges események, kibocsátás, bennfentes kereskedés, pontos beadási idővel | igen | ingyenes (User-Agent kell, ≤ 10 kérés/s) | P1 |
| yfinance `Ticker.news` | Gyors élő kiegészítés | csak a legutóbbi néhány nap | ingyenes, nem hivatalos | P2 (csak élő, tartalék) |
| Gyorsjelentési átiratok (pl. FMP) | Vezetőségi hangnem, előrejelzés | igen | fizetős | később |
| StockTwits / Reddit / X | Retail-hangulat, „crowding” | korlátozott / drága | | **nem** az első körben: zajos, nehéz point-in-time |

A forrás a meglévő `providers/` mintájára cserélhető adapter (`sentiment/sources/base.py`).

---

## 4. Architektúra

```
src/stockwatch/sentiment/
  __init__.py
  sources/
    base.py          NewsSource interfesz: fetch(symbols, start, end) -> list[Article]
    alpaca.py        Alpaca News (élő + történeti letöltés, lapozás, rate limit, gyorsítótár)
    edgar.py         SEC 8-K / 424B / Form 4
  model.py           Article, ScoredArticle, DailyFeatures dataclassok
  relevance.py       ticker- és cégnév-egyeztetés, duplikátum-szűrés (normalizált cím-hash, 48 órás ablak)
  scoring.py         pontozók: lexicon (Loughran–McDonald + VADER), finbert (opcionális), llm (opcionális)
  events.py          eseménytípus: guidance, downgrade/upgrade, earnings, M&A, offering, lawsuit, FDA, management...
  features.py        papíronkénti, napi, point-in-time jellemzők
  store.py           data/news/<SYMBOL>/<YYYY-MM>.jsonl.gz + pontszámok modellverzióval
  research.py        hipotézisvizsgálat a meglévő kutatási motorral
```

### 4.1 Point-in-time szabály (a legfontosabb)

- Minden hír a **megjelenési** időpontjával (`created_at`, UTC) tárolódik, a letöltés ideje külön mezőben.
- A *t* napi záróra (16:00 New York) születő döntés **csak a 16:00 előtt megjelent** híreket láthatja. A 16:00 utáni hír a *t+1* napi döntéshez tartozik. Ugyanez az elv érvényes, mint az árfolyamnál (`docs`: „look-ahead védelem, három rétegben”).
- Utólag frissített hír (`updated_at`): a pontszám a *legelső* változatból számol, a frissítés új bejegyzés lesz.
- A pontszám mellé a **modellverzió** is bekerül. Ha a modell változik, a történeti pontszámokat újra kell számolni, nem keverhetők.

### 4.2 Pontozás (rétegesen, a stocksight-féle egyetértési elvvel)

1. **Lexikon-alap** (mindig fut, offline, determinisztikus, ingyenes):
   - VADER a **Loughran–McDonald pénzügyi szólistával** kiegészítve: negatív, pozitív, bizonytalanság, peres szavak;
   - kimenet: `polarity` (−1…+1) és `uncertainty`;
   - csak a címen és az összefoglalón fut.
2. **FinBERT** (`ProsusAI/finbert`, opcionális extra): pénzügyi szövegre tanított osztályozó.
   - Nehéz függőség (torch, ~450 MB modell).
   - Ezért `pip install .[sentiment-ml]` extra, és a **labor-szolgáltatás** futtatja kötegben, nem a monitor.
3. **LLM-alapú eseményosztályozás** (opcionális, csak a jelöltekre):
   - csak azoknál a papíroknál fut, amelyek aznap belépési jelöltek vagy saját pozíciók (naponta ~5–30 hír, ezért a költség kicsi);
   - strukturált kimenet: `event_type`, `direction`, `materiality` (0–3), `confidence`, egysoros indoklás;
   - modell: a legolcsóbb megfelelő Claude-modell (pl. `claude-haiku-5-5`), `temperature=0`, cikkazonosító szerinti gyorsítótárral;
   - a történeti backtesthez **ugyanaz a prompt és modellverzió** fut a teljes archívumon egyszer (költségbecslés: kb. 500 papír × 10 év × ~2 hír/nap → kötegelve, cache-elve).
   - Kulcs: `ANTHROPIC_API_KEY` a `.env`-ben, nincs kulcs → a réteg kimarad.

**Összesítés:** egy hír `negative`, ha legalább két réteg negatív (vagy az LLM `materiality ≥ 2` negatív eseményt ad), különben `neutral` / `positive`. A bizonytalanság semleges felé tol.

### 4.3 Napi jellemzők papíronként (`features.py`)

Minden kereskedési napra és papírra, a 4.1 szerinti időhatárral:

| Jellemző | Leírás |
|---|---|
| `news_count_1d`, `news_count_3d` | releváns, duplikátummentes hírek száma |
| `news_volume_z` | hírvolumen z-értéke a papír saját 60 napos átlagához képest (a nagy cégekről mindig több hír van) |
| `sent_mean_3d` | átlagos polaritás, a frissebb hír nagyobb súllyal (felezési idő: 1 nap) |
| `neg_share_3d` | a negatív hírek aránya |
| `material_neg_3d` | volt-e lényeges negatív esemény (guidance-csökkentés, leminősítés, kibocsátás, per, FDA-elutasítás) az utolsó 3 napban |
| `sent_shift` | a 3 napos átlag eltérése a 30 napostól |
| `coverage` | van-e egyáltalán hírlefedettség (a „nincs adat” nem azonos a „nincs hír” esettel) |

---

## 5. Beépítés a meglévő rendszerbe

### 5.1 Árnyék-üzem először (nem dönt)

A `shadow.py` (ML árnyék-becslés) mintájára:

- A monitor zárás utáni körében (`monitor.pullback_cycle`, `monitor.scan`) minden jelöltre kiszámolja a napi jellemzőket, és **beírja a jelzésnaplóba** (`ledger`) és a jelzés `extras` mezőjébe.
- Új `entry.Check("Hírek", None, "3 napban 2 negatív hír: leminősítés (Benzinga, 14:32)")`. A `passed=None` „nem dönt” állapot, ugyanaz, amit a rendszer a nem ellenőrizhető szűrőkre már használ. A jelzés így nem bukik el miatta, de látszik.
- A Discord / Telegram jelzés kap egy sort: `Hírek: ▼ lényeges negatív (leminősítés, tegnap 14:32)`. Ha nincs hír: `Hírek: nincs releváns hír 3 napban`.

### 5.2 Visszatesztelés (`sentiment.research`, CLI: `meanrev-research --news-filter`)

1. `python cli.py fetch-news --since 2016-10-01`: az Alpaca-archívum letöltése a kutatási univerzumra (lapozva, gyorsítótárazva, lemezplafonnal).
2. Pontozás kötegben (a lexikon mindig, a FinBERT és az LLM ha be van kapcsolva).
3. A meglévő `meanrev_research.candidates()` kötései mellé kerülnek a jelzésnapi hírjellemzők. Ezután mérjük:
   - a nyerési arányt, az átlagot és a profit-faktort `material_neg_3d = igaz` és `hamis` esetén, a tanító időszakon;
   - előre rögzített szűrőváltozatokat (legfeljebb 3–4): pl. *kihagyás, ha lényeges negatív hír van*; *kihagyás, ha `neg_share_3d ≥ 0,5` és `news_volume_z ≥ 2`*.
4. Azonos statisztika és kapu, mint eddig: Wilson- és bootstrap-intervallum, évenkénti bontás, 2× költség, holdout egyszer, naplózva.
5. **Lefedettségi torzítás:** a hírarchívum a nagy cégekre sűrűbb. Ezért a hatást lefedettség szerint bontva is mérni kell.

### 5.3 Döntési szerep (csak ha a kapu átment)

- Stratégiánként kapcsolható szűrő: `strategies.<id>.params.news_filter: off | shadow | block | half_size`.
- A Visszahúzás és a Mély visszahúzás: `block` lényeges negatív hírnél (H1). A Kitörés: pontszám-komponens vagy megerősítés (H2), csak ha a validáció indokolja.
- A paper fiókra ugyanaz a szabály érvényes, mint a virtuális követésre (egyetlen igazságforrás).

### 5.4 Saját pozíciók és figyelt papírok (H3)

- A `holdings` kiértékelése új okot kap: „Lényeges negatív hír: guidance-csökkentés (ma 13:05)”. Az ítélet ilyenkor *Figyelj*, súlyos eseménynél *Kockázatos*.
- Azonnali értesítés a saját pozíciónál megjelenő lényeges negatív hírről, napközben is: a monitor 15 perces frissítésekor ellenőrzi, értesítésismétlés-védelemmel.

### 5.5 Felület

- **Belépési jelöltek táblázat:** hír-ikon (▲ / ▼ / –) a hírek számával; rámutatva a legfrissebb címek az időpontjukkal.
- **Papír oldal:** hír-idővonal a chart alatt (pontok a megjelenés idején, színük a hangulat), hírlista forrással, linkkel és pontszámmal.
- **Kutatás fül:** a H1/H2 eredménytáblája (tanító / validáció / holdout).
- **Beállítások:** források, pontozók, LLM be/ki, napi költségkeret.

---

## 6. Üzemeltetés, költség, kockázatok

| Téma | Megoldás |
|---|---|
| Lemez | `data/news/` tömörített JSONL; a `housekeeping` plafonja vonatkozik rá (pl. 300 MB). A történeti archívumból csak a cím, az összefoglaló és a pontszám marad meg, a teljes cikktartalom nem |
| API-korlát | Alpaca: lapozás `page_token`-nel, visszalépés (backoff) 429-nél; EDGAR: ≤ 10 kérés/s, User-Agent |
| LLM-költség | Csak a jelöltekre és a saját pozíciókra; cikkenkénti cache; napi keret (`sentiment.llm_daily_budget_usd`), túllépéskor a réteg kimarad, és ez naplózva van |
| Reprodukálhatóság | Modell- és promptverzió minden pontszám mellett; a `replay` ugyanazt a tárolt pontszámot használja, nem pontoz újra |
| Hiányzó adat | A `coverage = hamis` állapot „nem ismert”-ként jelenik meg, nem „nincs negatív hír”-ként |
| Jogi | Csak hivatalos API-k, nincs scraping; a hírszöveget nem tesszük közzé, csak a címet és a linket |
| Biztonság | A hírszöveg nem megbízható bemenet: az LLM-promptban adatként szerepel, a kimenet szigorú sémára validálva (nincs utasítás-végrehajtás) |

---

## 7. Ütemezés

| Fázis | Tartalom | Kész, ha |
|---|---|---|
| **0 – adat** (≈1 hét) | `sentiment/sources/alpaca.py`, `store.py`, `relevance.py`, `fetch-news` CLI, tesztek (point-in-time vágás, duplikátum) | 10 év hírarchívum az univerzumra, lemezplafonon belül |
| **1 – pontozás és árnyék** (≈1 hét) | lexikon-pontozó, napi jellemzők, `Check("Hírek", None, …)`, napló- és értesítéssor, felületi ikon | a monitor minden jelöltnél kiírja a hírjellemzőket |
| **2 – kutatás** (≈1 hét) | `meanrev-research --news-filter`, H1/H2 mérése a tanító / validációs időszakon, riport a `docs/` alatt | döntés: van-e mérhető hatás |
| **3 – döntés** (csak ha a 2. átment) | `news_filter` kapcsoló, holdout egyszer, paper-kötés | a kapu és a holdout pozitív |
| **4 – bővítés** | FinBERT / LLM eseményosztályozás, EDGAR, saját pozíciós riasztás | mérhetően jobb a lexikonnál a validációs időszakon |

A 4. fázisig nincs új nehéz függőség. A `requirements.txt` csak a `vaderSentiment` csomaggal bővül (a Loughran–McDonald szólista adatfájlként jön).

---

## 8. Nyitott kérdések (döntés kell)

1. **Alpaca News hozzáférés:** a jelenlegi paper kulcsokkal elérhető-e a történeti hírarchívum ezen a fiókon (a fiók csomagjától függ)? Ha nem, a 2. fázis csak az EDGAR-ral és rövidebb élő gyűjtéssel indulhat, és lassabb lesz.
2. **LLM-réteg:** használjunk-e Claude API-t a lényeges események osztályozására (pontosabb, de API-kulcs és kis költség kell)?
3. **Retail közösségi hangulat** (StockTwits / Reddit): kell-e egyáltalán? A javaslat szerint csak később, külön hipotézissel.

*Nem befektetési tanács.*
