# StockWatch fizetős, nyilvános kutatási szolgáltatásként

Állapot: **későbbi lehetőség.** Amíg a szerver és az adat ingyenes kell legyen, a javasolt út a saját példányos modell:
[`SELF-HOSTED-DESIGN.md`](SELF-HOSTED-DESIGN.md). Ez a terv akkor válik időszerűvé, ha a bevétel fedezi a fizetős
adatlicencet és a nagyobb szervert.

## 0. Összefoglaló

| Kérdés | Döntés |
|---|---|
| Mi a termék? | Kutatási szoftver stratégiák kidolgozására, backtesztelésére és követésére. Paper trading, valódi pénz nélkül. |
| Ki használja? | Bárki, aki előfizet. Saját fiók, e-mailes regisztráció. |
| Mit állíthat? | **Mindent a saját munkaterületén:** szabályok, stratégia-paraméterek, univerzum, labor, homokozó, méretezés, értesítés. |
| Értesítés | Felhasználónként saját Discord webhook. |
| Közös ranglista | Egyelőre nincs. |
| Szerver | **Marad az Oracle**, de a fiók **Pay As You Go**-ra vált, és nagyobb Ampere A1 gépre bővül (lásd 8.). |
| Indulás előtt kötelező | **Licencelt adatforrás** (a Yahoo/yfinance nem használható fizetős szolgáltatáshoz), jogi átvilágítás, a fizetési szolgáltató írásos jóváhagyása (3. fejezet). |

A legnagyobb változás a korábbi tervhez képest: mivel mindenki saját szabályokkal dolgozik, **a jelzésszámítás
felhasználónként fut**. Ezt csak úgy lehet gazdaságosan, ha az adat és a drága előfeldolgozás közös, a felhasználói
számítás munkasorból, kvóták szerint fut, és a gép a felhasználók számával együtt bővül.

## 1. A termék

- **Munkaterület felhasználónként:** saját beállítások és stratégiák (a mostani Kitörés / Visszahúzás / Mély visszahúzás /
  ETF / Momentum motorokra építve, bármely paraméterrel), saját watchlist és univerzum, saját homokozó, labor-kísérletek,
  backteszt-futtatások, Saját pozíciók, paper fiók, Discord webhook, nyelv, kötési díj.
- **Közös:** csak az, ami a piacról szól: árfolyamadat, indexösszetétel, gyorsjelentés-naptár, hírek, és a szabályoktól
  független előfeldolgozás (indikátorok). A közös jelzésnapló és a közös Jelzett trade-ek megszűnnek: mindenkinek a
  saját jelzései és a saját virtuális követése van.
- **Nem része:** valódi pénzes kereskedés, befektetési tanács, másoló kereskedés, közös ranglista.

## 2. Mit kell megoldani: a mostani rendszer korlátai

| Terület | Ma | Nyilvános szolgáltatásban |
|---|---|---|
| Adatforrás | yfinance (Yahoo) | a yfinance saját leírása szerint személyes használatra való – **licencelt szolgáltató kell** |
| Felhasználó | egy jelszó, memóriában tartott munkamenet | regisztráció, e-mail-ellenőrzés, jelszó-visszaállítás, 2FA, tartós munkamenet |
| Számítás | egy monitor egy beállítással, egy labor | felhasználónkénti futtatás munkasorból, kvótákkal, határidővel |
| Tárolás | JSON / CSV fájlok, egy könyvtár | felhasználónkénti munkaterület, fiók- és előfizetés-nyilvántartás tranzakcióval |
| Webszerver | Python beépített `http.server` | nyilvános internetre éles keretrendszer, rate limit, CDN / WAF |
| Titkok | egy titokfájl | felhasználónként, titkosítva tárolva |
| Szerver | 1 mag, 6 GB, ingyenes keret | bővíthető, fizetős fiók, mentés, felügyelet |
| Kód | 13 modul a közös `DATA_DIR` / `CONFIG_DIR` / `ENV_PATH` konstansokra hivatkozik | minden a munkaterületen keresztül |

## 3. Indulás előtti blokkolók

### 3.1 Adatlicenc – a legfontosabb
A rendszer ma a yfinance-szel tölt, amely a saját dokumentációja szerint személyes használatra szánt, nem hivatalos
eszköz; a Yahoo feltételei szabályozzák az adat felhasználását. Fizetős szolgáltatáshoz **olyan szolgáltató kell, amely
írásban engedi, hogy az adatot (és a belőle számolt chartokat, jelzéseket) fizető végfelhasználóknak megmutasd**.
Napi gyertyákhoz több szolgáltató is kínál kereskedelmi csomagot (pl. EODHD, Tiingo, Polygon, EODData – utóbbinál
viszonteladói, felhasználónkénti díjas modell is van). A váltás a kódban kicsi: a rendszer egy `PriceProvider`
felületen át tölt, új szolgáltató egy új osztály. A hírekre és a gyorsjelentés-naptárra ugyanez vonatkozik (a SEC EDGAR
nyilvános, az rendben van). **Ez a havi költség legnagyobb tétele lehet** – az árajánlatot a szerződés előtt kérd be.

Alternatíva: „hozd a saját adatkulcsodat” – minden felhasználó a saját szolgáltatói kulcsát adja meg, a rendszer azzal tölt.
Jogilag egyszerűbb, de a felhasználónak bonyolultabb, és a közös adatcache előnye elvész. Csak végső megoldásnak.

### 3.2 Jogi keret
- **Befektetési tanács / ajánlás határa.** A termék kutatási eszköz: a szabályokat a felhasználó állítja, a rendszer nem
  ad személyre szabott ajánlást. Hogy ez a magyar és uniós szabályok (MNB, MiFID II, piaci visszaélési rendelet) szerint
  elég-e, azt **pénzügyi jogásszal kell tisztázni** a fizetős indulás előtt – különösen az alapból mellékelt stratégiák és a
  Discordra küldött „vételi limit” üzenetek miatt.
- **Marketing:** a README mostani hozamszámai (pl. „évi +30% / +52%”) eladásra nem használhatók; a felületen és az
  üzenetekben maradjon a „Nem befektetési tanács” jelzés.
- **Kötelező dokumentumok:** ÁSZF, adatkezelési tájékoztató (GDPR), elállás / visszatérítés, cookie-tájékoztató (csak
  működéshez szükséges sütik – a mostani rendszer csak ilyet használ).
- **Vállalkozási forma, számlázás, ÁFA:** könyvelővel (lásd a fizetési szolgáltató választását lent).

### 3.3 Fizetési szolgáltató
- **Paddle** (ún. merchant of record: ő számláz és fizeti be az ÁFÁ-t minden országban) – egyszemélyes indulásnál ez a
  legkevesebb adminisztráció. Az elfogadási szabályai szerint a piaci elemző, riasztó szoftver elfogadható, de a
  **kereskedést végrehajtó, brókerszámlához kapcsolódó termék nem**. A StockWatch automatikus paper-beküldése ezen a
  határon van: **előre, írásban kérj jóváhagyást** a pontos leírással (csak paper, a felhasználó saját Alpaca paper
  fiókja). Ha nem fogadják el, a paper-beküldés a felhasználó saját gépén / saját kulcsával futó opcionális kiegészítő
  lehet, vagy Stripe-ra kell váltani.
- **Stripe** – alacsonyabb díj, de az ÁFA-bevallás és a számlázás a tiéd (magyar számlázóprogram-integráció kell).
  Nagyobb forgalomnál éri meg.

### 3.4 Alpaca
- Kezdetben a felhasználó a **saját Alpaca paper kulcsát** adja meg (mint ma). A kulcsot a rendszer titkosítva tárolja.
- Később **Alpaca OAuth**: a felhasználó az Alpacánál jóváhagyja a hozzáférést, kulcsot nem kell bemásolnia. Ehhez az
  alkalmazást az Alpacának jóvá kell hagynia.
- A valódi pénzes endpoint tiltása marad.

## 4. Architektúra

```
 Böngésző ──► Cloudflare (DDoS, WAF, cache) ──► Caddy (HTTPS) ──► Webes alkalmazás
                                                                   │  fiók, belépés, API, fizetési webhookok
                                                                   ▼
             Ütemező ──────────────────────────────────────────► Munkasor (felhasználónkénti feladatok)
   (zárás után, nyitás előtt, 15 percenként)                        │
                                                                   ▼
   Közös adatréteg  ◄────────────────────────────────────────  Dolgozók (N folyamat)
   árfolyam, index, naptár, hírek,                               szkennelés, értesítés, paper szinkron,
   napi előfeldolgozás (indikátorok)                             backteszt, labor, homokozó
                                                                   │
                                                                   ▼
                                                     Munkaterületek: data/users/<id>/…  +  titkosított titkok
```

### 4.1 Hogyan fér el, hogy „mindenki mindent állíthat”
1. **Közös adat, egyszer:** zárás után az adatréteg egyszer tölti le a felhasználók univerzumainak unióját, és papíronként
   kiszámolja a szabályoktól független előfeldolgozást (gyertyák, mozgóátlagok, ATR, RSI, volumen). Ezt minden dolgozó
   megosztva olvassa.
2. **Olcsó és drága paraméterek:** a legtöbb beállítás (szűrők, pontszámküszöb, limitek, stopok, kilépés, méretezés) a
   közös előfeldolgozáson fut, papíronként milliszekundumok alatt. A drága paraméterek (pl. alakzatfelismerés
   geometriája, nem szabványos indikátorperiódus) a felhasználónál teljes újraszámolást indítanak – ezek a kvótából fogynak.
3. **Munkasor, határidővel:** a zárás utáni felhasználói szkennelések a munkasorba kerülnek; a dolgozók méltányos sorrendben
   (felhasználónként felváltva, csomag szerinti prioritással) dolgozzák fel őket. Határidő: a jelzésnek a következő nyitás
   előtt meg kell lennie; ha nem fér bele, a rendszer riaszt (bővíteni kell).
4. **Labor és homokozó:** felhasználónkénti kísérletek, szintén munkasorból, **CPU-perc kvótával**. Ez a legnagyobb
   számításigény – a csomagok ezen különböznek leginkább.
5. **Felhasználói kód nincs:** a felhasználó paramétereket és később egy biztonságos szabálynyelven összerakott feltételeket
   ad meg; tetszőleges Python-kódot soha nem futtat a rendszer.

### 4.2 Ütemezett munkák
| Mikor | Mi | Felhasználónként? |
|---|---|---|
| Zárás után | adatfrissítés, előfeldolgozás | nem (közös) |
| Zárás után | szkennelés, jelzés, virtuális követés, Discord, paper-beküldés | igen (munkasor) |
| Nyitás előtt | nyitás előtti újraellenőrzés, paper kilépések | igen |
| 15 percenként | paper szinkron | igen, egymástól elszigetelve, időkorláttal |
| Folyamatosan | labor, backteszt, homokozó | igen, kvótából |

Egy felhasználó hibája (rossz Alpaca kulcs, törölt webhook) csak őt érinti: naplózza, értesíti a felületen, a többiek
futása megy tovább.

## 5. Fiókok és biztonság a nyilvános interneten

- **Regisztráció:** e-mail + jelszó, e-mail-ellenőrzés kötelező. Jelszó-visszaállítás e-mailben, egyszer használható,
  rövid ideig érvényes linkkel. Ehhez tranzakciós e-mail-szolgáltató kell.
- **Jelszó:** scrypt (a mostani paraméterekkel), legalább 12 karakter, kiszivárgott jelszavak tiltólistája.
- **Kétlépcsős azonosítás (TOTP):** felhasználónak választható, adminnak kötelező.
- **Munkamenet:** HttpOnly + Secure + SameSite süti, felhasználóhoz kötve, tartósan tárolva (csak a token hash-e),
  lejárattal; jelszócsere és „kijelentkezés mindenhonnan” minden munkamenetet zár.
- **Találgatás és túlterhelés ellen:** IP- és fiókszintű korlát, regisztráció- és jelszó-visszaállítás-korlát;
  Cloudflare előtte (DDoS, alap WAF, bot-védelem a regisztráción).
- **Elszigetelés:** minden kérés és minden munkasor-feladat egy munkaterület nevében fut, amelyet a függvények explicit
  paraméterként kapnak; minden azonosító csak a saját tárban keresődik („nincs ilyen”, nem „nincs jogosultság”).
  Izolációs tesztek minden végpontra.
- **Titkok:** az Alpaca kulcsok és a Discord webhookok **titkosítva** tárolódnak (alkalmazás-kulccsal, amely nem a
  mentésekben van); a felület sosem adja vissza őket.
- **Webes keretrendszer:** a beépített `http.server` helyett éles ASGI-keretrendszer (pl. Starlette / FastAPI + uvicorn),
  a felület marad sima HTML/JS. A mostani biztonsági fejlécek (CSP stb.) átkerülnek.
- **Napló és felügyelet:** belépések, beállítás-változások, fizetési események naplója (plafonnal); hibariasztás és
  elérhetőség-figyelés; függőségfrissítések rendszeresen.
- **Mentés:** napi titkosított mentés gépen kívülre (OCI Object Storage), **havonta kipróbált visszaállítással**.

## 6. Előfizetés

- **Csomagok:** a csomagok a kvótákban különböznek (lásd 9.); ingyenes próbaidőszak a regisztráció után.
- **Állapot:** a fizetési szolgáltató webhookjai állítják (aktív, próba, lejárt, fizetési hiba). Fizetési hibánál türelmi
  idő; utána a fiók **csak olvasható**: nincs szkennelés, értesítés, paper szinkron, labor.
- **Megszűnés:** az adatok 30 napig megmaradnak (visszaaktiválható), utána törlődnek. A felhasználó bármikor kérheti az
  adatai exportját és törlését (GDPR).

## 7. Tárolás

| Mit | Hol | Megjegyzés |
|---|---|---|
| Fiókok, munkamenetek, előfizetések, munkasor, audit | **egy SQLite-fájl** (javaslat) vagy JSON | lásd a döntést lent |
| Közös piaci adat | `data/` (mint ma) | plafonokkal, mint ma |
| Munkaterületek | `data/users/<id>/` | beállítások, stratégiák, watchlist, pozíciók, paper, értesítési napló, backteszt, labor |
| Titkok | titkosítva, felhasználónként | |

**Döntés kell – adatbázis.** A projekt eddigi szabálya: nincs adatbázis-telepítés, minden fájlalapú, kemény lemezplafonnal.
A fiókokat, előfizetéseket és a munkasort viszont sok folyamat írja egyszerre, és a fizetésnél nem veszhet el vagy
duplázódhat esemény. **Javaslat: SQLite** – egyetlen fájl, nincs szerverfolyamat, nem kell telepíteni (a Python része),
tranzakciós, és a mérete plafonozható és figyelhető. A munkaterületek maradnak fájlok, plafonnal. Ha az SQLite nem jöhet
szóba: fájlalapú tárolás zárakkal is megoldható, de a fizetési és munkasor-részt sokkal gondosabban kell megírni, és
nagyobb a hibalehetőség.

Plafonok felhasználónként (csomagfüggő): munkaterület-méret, mentett backteszt-futtatások, labor-kísérletek, értesítési
napló (rotálva), watchlist / univerzum mérete; globálisan: a lemezfelügyelet a munkaterületeket is összesíti.

## 8. Szerver és költség

**Javaslat: marad az Oracle Cloud (Frankfurt, EU – GDPR szempontból jó), de a fiók Pay As You Go-ra vált.**

- Az ingyenes Ampere A1 keret Oracle mostani dokumentációja szerint **2 mag és 12 GB** (korábban 4 / 24 volt). A mostani
  gép (1 mag, 6 GB) ennek a fele. Nyilvános, fizetős szolgáltatásra az ingyenes keret nem elég, és nincs mögötte támogatás.
- Fizetős fiókban az ingyenes keret megmarad, felette az Ampere A1 ára Oracle szerint **0,01 USD / mag / óra** és
  **0,0015 USD / GB / óra**:

| Gép | Havi költség (az ingyenes keret levonásával) | Mire elég (becslés, a 0. fázisban mérni kell) |
|---|---|---|
| 4 mag / 24 GB | kb. **28 USD** | zárt béta, néhány tucat felhasználó mérsékelt laborhasználattal |
| 8 mag / 48 GB | kb. **83 USD** | nyilvános indulás; a labor-kvóták határozzák meg a felhasználószámot |
| + külön dolgozó-gép(ek) | gépenként a fenti arányban | növekedéskor: a webes rész és a dolgozók szétválnak |

- Mellé: OCI Object Storage a mentéseknek (kis tétel), Cloudflare ingyenes csomag, saját domain (a duckdns fizetős
  szolgáltatáshoz nem elég), tranzakciós e-mail, külön kis tesztkörnyezet (staging).
- **Kockázat:** Frankfurtban az Ampere A1 kapacitás néha elfogy – a bővítést érdemes időben megcsinálni. Tartalék: x86-os
  (AMD) gép az Oracle-nél, drágábban; vagy másik szolgáltató. A Hetzner 2026-ban jelentősen emelt árat, ma nem olcsóbb.
- A havi költség legnagyobb tétele valószínűleg **nem a szerver, hanem az adatlicenc**.

## 9. Csomagok – a kvóták vázlata

| Kvóta | Alap | Pro |
|---|---|---|
| Aktív stratégiák | pl. 3 | pl. 10 |
| Univerzum | watchlist (≤ 60) | watchlist + Nasdaq-100 + S&P 500 |
| Labor | pl. havi 60 CPU-perc | pl. havi 600 CPU-perc |
| Backteszt-futtatás | pl. napi 20 | pl. napi 200 |
| Paper fiók | 1 | 1 (később több) |
| Munkaterület-méret | pl. 50 MB | pl. 500 MB |

Az árakat a mért költségből (szerver + adatlicenc + fizetési díj felhasználónként) érdemes kiszámolni a 0. fázis után.

## 10. Ütemezés

Minden fázis után a rendszer működik. **Fizető felhasználó csak az 5. fázis után jöhet; idegen felhasználó (zárt béta)
a 3. után.**

| Fázis | Tartalom |
|---|---|
| 0 | Mérés: szkennelés / labor / backteszt CPU- és memóriaigénye felhasználónként; licencelt adatszolgáltató kiválasztása és bekötése; jogi és fizetési előegyeztetés elindítása; Oracle Pay As You Go, gép bővítése |
| 1 | Munkaterület-réteg (a 13 modul átírása), izolációs tesztkeret; a mostani telepítés átköltöztetése az első (admin) munkaterületre |
| 2 | Fiókok: regisztráció, e-mail-ellenőrzés, jelszó-visszaállítás, TOTP, tartós munkamenet; ASGI-keretrendszer; Cloudflare; titkosított titkok |
| 3 | Felhasználónkénti számítás: közös előfeldolgozás, munkasor és dolgozók, szkennelés / értesítés / paper szinkron / backteszt felhasználónként, kvóták → **zárt béta, ingyen** |
| 4 | Felhasználónkénti labor és homokozó CPU-kvótával |
| 5 | Előfizetés (webhookok, csomagok, türelmi idő, csak-olvasható állapot), ÁSZF / adatkezelés / visszatérítés, export és törlés → **fizetős indulás** |
| 6 | Üzemeltetés: felügyelet, riasztás, mentés-visszaállítás próbája, hibaelhárítási leírás; később Alpaca OAuth |

## 11. Tesztek

- Izoláció: két felhasználóval minden végpontra és minden munkasor-feladatra.
- Titkok: semmilyen válaszban, naplóban, hibaüzenetben nem jelenik meg kulcs vagy webhook.
- Fiókok: regisztráció, ellenőrzés, visszaállítás, 2FA, zárak, munkamenetek.
- Számítás: két felhasználó eltérő szabályaival ugyanazon a közös adaton más jelzés jön, és a közös előfeldolgozással
  ugyanaz az eredmény, mint a mostani motorral (egyezés-teszt, mint a Backteszt oldalnál).
- Kvóták és határidő: kvóta-túllépésnél a feladat nem indul; túlterhelésnél riasztás.
- Előfizetés: webhook-események sorrendje, ismétlése, hibája; lejárt fiók csak olvasható.
- Átköltöztetés: a mostani adatok az admin munkaterületén változatlanul látszanak.

## 12. Nyitott döntések

1. **Adatbázis:** SQLite a fiókokhoz, előfizetésekhez és a munkasorhoz (javaslat), vagy maradjon minden fájlalapú?
2. **Fizetési szolgáltató:** Paddle (kevesebb adminisztráció, de az automatikus paper-beküldést jóvá kell hagyniuk) vagy Stripe?
3. **Adatszolgáltató:** melyiktől kérjünk ajánlatot kereskedelmi, végfelhasználói megjelenítést engedő licencre?
4. **Mellékelt stratégiák:** a mostani stratégiák alapértelmezett sablonként járjanak-e a fiókhoz (jogi kérdés, lásd 3.2)?

## Források

- Oracle Always Free erőforrások: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm>
- Oracle Arm-alapú gépek ára: <https://www.oracle.com/cloud/compute/arm/pricing>, <https://www.oracle.com/cloud/compute/arm>
- yfinance (személyes használat): <https://pypi.org/project/yfinance/0.2.39>
- Adatszolgáltatók: <https://eodhd.com/pricing>, <https://eoddata.com/products/DataFeed.aspx>
- Paddle elfogadási szabályok: <https://paddle.com/support/aup>, <https://developer.paddle.com/partners/embed-billing/seller-go-live/minimum-seller-terms>
- Stripe és Paddle összevetés: <https://cut-the-saas.com/guides/stripe-vs-paddle>
- Alpaca OAuth: <https://docs.alpaca.markets/docs/using-oauth2-and-trading-api>
- Hetzner 2026-os áremelés: <https://wz-it.com/en/blog/hetzner-price-increase-june-2026-cpx-ccx-alternatives/>
