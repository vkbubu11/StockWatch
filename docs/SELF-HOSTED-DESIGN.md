# StockWatch értékesítése ingyenes szerverrel és ingyenes adattal – saját példányos modell

Állapot: **terv**, még nincs megvalósítva. Ez a javasolt út, amíg a szerver és az adat ingyenes kell legyen. A hosztolt,
több-felhasználós változat (`SAAS-DESIGN.md`) későbbi lehetőség, ha a bevétel fedezi a fizetős adatlicencet.

## 0. Összefoglaló

| Kérdés | Döntés |
|---|---|
| Mit adsz el? | **A szoftver használati jogát és a frissítéseket** (előfizetés), nem egy általad üzemeltetett szolgáltatást. |
| Hol fut? | **A vásárló saját, ingyenes Oracle Cloud gépén** (Always Free Ampere A1: 2 mag, 12 GB – egy felhasználónak bőven elég). Egy telepítő szinte mindent elvégez. |
| Honnan jön az adat? | **A vásárló saját hozzáférésével**, a saját gépére: alapból yfinance, választhatóan a saját ingyenes Alpaca-kulcsával. A te gépeden nem megy át adat. |
| „Mindenki mindent állíthat” | Magától teljesül: mindenkinek saját, teljes példánya van. |
| Discord | Mindenkinek a saját webhookja, mint ma. |
| A te költséged | **0 Ft fix költség**: GitHub (privát tároló), a fizetési szolgáltató csak tranzakciós díjat von le, értékesítési oldal GitHub Pages-en. |
| Mi kell a kódban? | Sokkal kevesebb, mint a hosztolt változatnál: licencellenőrzés, frissítő parancs, egyszerűbb telepítés, minden beállítás a felületről, Alpaca adatszolgáltató. A mostani egyfelhasználós felépítés marad. |

## 1. Miért nem működik ingyen a hosztolt szolgáltatás

| Akadály | Részletek |
|---|---|
| **Adatlicenc** | Ha a szolgáltatás a te szervereden fut és más fizet érte, az árfolyamadatot te mutatod meg a felhasználóidnak – ehhez kereskedelmi, végfelhasználói megjelenítést engedő licenc kell. A yfinance a saját leírása szerint személyes használatra való. Az Alpaca munkatársa szerint harmadik fél alkalmazásának saját adatforrás kell, a megjelenítéshez „professional” licenc, amit az Alpaca nem kínál. Ingyenes, szabályos, teljes amerikai részvény-árfolyamforrást nem találtam. |
| **Szerverkapacitás** | Az Oracle ingyenes kerete 2 mag / 12 GB. Felhasználónkénti szabályokkal és laborral ez néhány felhasználónál betelik. |

A saját példányos modellben mindkettő megszűnik: a vásárló a **saját** gépén, a **saját** személyes adathozzáférésével
kutat – pontosan azt teszi, amire ezek az ingyenes források valók.

## 2. Hogyan néz ki a vásárlónak

1. **Vásárlás** az értékesítési oldalon → e-mailben licenckulcs.
2. **Ingyenes Oracle Cloud fiók** (bankkártyás azonosítás kell, díjat nem von le) → új Ampere A1 gép (2 mag, 12 GB,
   Ubuntu) → a gép létrehozásakor egy előre megírt **indítószkriptet** illeszt be (cloud-init), benne a licenckulcs.
3. A gép magától telepíti a StockWatch-ot (a mostani `install.sh` alapján), HTTPS-sel ingyenes DuckDNS-címen vagy
   SSH-alagúton át.
4. **Első indítási varázsló** a felületen: jelszó, Discord webhook, Alpaca paper kulcs, adatforrás, watchlist.
5. Utána minden a felületről állítható, a frissítés egy gomb (vagy `stockwatch-cli update`).

Tartalék, ha az Oracle-nél nincs szabad Ampere-kapacitás vagy nem fogadják el a regisztrációt: bármilyen Ubuntu VPS,
vagy a vásárló saját Windows gépe (a mostani `start.bat` – ilyenkor csak akkor fut, amikor a gép be van kapcsolva).

## 3. Mit kell a kódban megcsinálni

| # | Feladat | Miért |
|---|---|---|
| 1 | **Licenc**: kulcs megadása (varázsló / telepítő), napi ellenőrzés a fizetési szolgáltató licenc-API-ján; 7 napos türelmi idő hálózati hibánál; lejárt licencnél a meglévő adatok és a felület olvashatók maradnak, de a frissítés és az ütemezett futások (jelzés, értesítés, paper szinkron, labor) leállnak | előfizetés érvényesítése saját szerver nélkül |
| 2 | **Frissítés**: `stockwatch-cli update` + gomb a felületen – letölti az új kiadást, lefuttatja a telepítőt, megtartja az adatot és a beállítást; verziószám és változásnapló a felületen | a vásárló ne kézzel frissítsen |
| 3 | **Egyszerűbb telepítés**: cloud-init indítószkript az Oracle-gép létrehozásához; a telepítő magától kér HTTPS-t (DuckDNS) vagy alagutat; ellenőrzi a 2 mag / 12 GB keretet | ne kelljen parancssor |
| 4 | **Első indítási varázsló**: jelszó, Discord, Alpaca, adatforrás, watchlist, nyelv | ma ezek részben parancssorból mennek |
| 5 | **Minden beállítás a felületről**: a `settings.yaml` ma csak részben állítható a felületen – egy „Haladó beállítások” oldal, amely a teljes beállítást validálva szerkeszti | „mindenki mindent állíthat” YAML-szerkesztés nélkül |
| 6 | **Alpaca adatszolgáltató**: új `PriceProvider` (napi gyertyák a vásárló saját ingyenes Alpaca-kulcsával, 2016-tól); választható a yfinance helyett | stabilabb, hivatalos API a vásárló saját hozzáférésével |
| 7 | **Kiadási folyamat**: verziócímkék, változásnapló, beállítás-migráció kiadásonként, tesztek a kiadás előtt | sok példány frissül ugyanarra a verzióra |
| 8 | **Hibabejelentés**: az Információ oldal a privát tároló Issues fülére (vagy egy ügyfél-Discordra) mutat | támogatás |

A hosztolt tervből ami **nem kell**: felhasználói fiókrendszer, munkaterület-réteg, munkasor, kvóták, előfizetés-webhookok,
titkosított titoktár, CDN. Ez a munka nagyjából harmada-negyede.

## 4. Értékesítés és terjesztés – saját szerver nélkül

- **Fizetés + licenckulcs:** olyan szolgáltató, amely maga állít ki licenckulcsot és ellenőrző API-t ad (pl. **Lemon
  Squeezy** – licenc-API: aktiválás, ellenőrzés, visszavonás). Ő számláz és kezeli az ÁFÁ-t (merchant of record),
  havidíja nincs, csak tranzakciós díja. **Előtte írásban rákérdezni**, hogy kutatási / paper trading szoftvert
  elfogad-e (a Paddle például a kereskedést végrehajtó termékeket kizárja).
- **A kód terjesztése:** a GitHub-tároló ma **nyilvános és licenc nélküli** – eladás előtt **privátra kell állítani**.
  A vásárló gépe **csak olvasási jogú deploy kulccsal** éri el a privát tárolót (ingyenes, tárolónként sok kulcs
  adható, egyenként visszavonható). Az első verzióban a kulcsot kézzel adod hozzá a vásárlás után; később automatizálható.
- **Értékesítési oldal:** GitHub Pages (ingyenes), leírás, képernyőképek, árak, ÁSZF, adatkezelés, telepítési útmutató.
- **Támogatás:** a privát tároló Issues füle vagy egy ingyenes Discord-szerver.
- **Másolás ellen:** a Python-kód olvasható, a licencellenőrzés megkerülhető. Ezt el kell fogadni; a fizetés értéke a
  frissítés, az új stratégiák és a támogatás. A szoftver licencszerződése (EULA) tiltja a továbbadást.

## 5. Jogi oldal – egyszerűbb, de nem nulla

- **Licencszerződés (EULA) és ÁSZF:** használati jog, tilos továbbadni, nincs garancia a hozamra, nem befektetési tanács,
  az adatforrások feltételeinek betartása a vásárló felelőssége (a saját hozzáférésével tölt).
- **Adatvédelem:** a te oldaladon csak a vásárlási adat (a fizetési szolgáltatónál) és a licenckulcs-ellenőrzés van; a
  kereskedési adat a vásárló gépén marad. Adatkezelési tájékoztató így is kell.
- **Befektetési tanácsadás határa:** egy telepíthető kutatási szoftver eladása tisztább helyzet, mint jelzések
  szolgáltatása, de a mellékelt alapstratégiák és a marketing miatt **egy pénzügyi jogásszal egyszer érdemes átnézetni**.
  A README hozamszámai (pl. „évi +30%”) eladásra nem használhatók.
- **Vállalkozási forma, adózás:** könyvelővel (a merchant of record a vevő felé számláz, neked tőle van bevételed).

## 6. A vásárló gépének kapacitása

Egy felhasználó, teljes funkcióval, 2 mag / 12 GB-on: a mai rendszer 1 mag / 6 GB-on fut (monitor, webes felület,
labor, kb. 240 MB folyamatonként, 48 MB adat). A dupla keret a labornak és a Backteszt oldalnak ad tartalékot.
Figyelni kell: az Oracle tétlennek minősítheti és visszaveheti az ingyenes gépet, ha 7 napig a processzor-, hálózat- és
memóriahasználat egyaránt 20% alatt marad. A futó labor és a betöltött adat általában fölötte tartja, de a telepítő
figyelmeztessen erre.

## 7. Ütemezés

| Fázis | Tartalom | Eredmény |
|---|---|---|
| 0 | Tároló privátra; EULA / ÁSZF vázlat; fizetési szolgáltató előzetes jóváhagyása; jogász egyszer | eladható jogi alap |
| 1 | Licenc (3. fejezet, 1.), frissítés (2.), kiadási folyamat (7.) | fizető ügyfél kezelhető |
| 2 | Cloud-init telepítés (3. fejezet, 3.), első indítási varázsló (4.) | parancssor nélküli telepítés |
| 3 | Haladó beállítások oldal (3. fejezet, 5.), Alpaca adatszolgáltató (6.) | minden a felületről, hivatalos adat-API |
| 4 | Értékesítési oldal, telepítési útmutató, zárt béta 3–5 ismerőssel ingyen | valós telepítési tapasztalat |
| 5 | Fizetős indulás | |

## 8. Nyitott döntések

1. **Fizetési szolgáltató** licenckulccsal: Lemon Squeezy (javaslat, ha elfogadja a terméket) vagy más?
2. **Ár és csomagok:** egy csomag havidíjjal, vagy éves előfizetés kedvezménnyel?
3. **Tároló privátra állítása** most, vagy a 0. fázis végén? (Amíg nyilvános, bárki letöltheti; licenc nélkül
   jogilag nem használhatja, de technikailag igen.)
4. **Alap adatforrás** az új telepítéseknél: yfinance (nem kell hozzá kulcs) vagy a vásárló Alpaca-kulcsa (hivatalos API,
   de csak 2016-tól van adat)?

## Források

- Oracle Always Free erőforrások és tétlenségi szabály: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm>
- Oracle Free Tier GYIK: <https://www.oracle.com/sa/cloud/free/faq/>
- yfinance (személyes használat): <https://pypi.org/project/yfinance/0.2.39>
- Alpaca piaci adat (ingyenes csomag, 2016-tól, 200 hívás / perc): <https://docs.alpaca.markets/docs/about-market-data-api>
- Alpaca fórum – harmadik fél alkalmazásának saját adat kell: <https://forum.alpaca.markets/t/apierror-token-is-not-authorized-for-this-scope-code-40110000/4315>
- Lemon Squeezy licenc-API: <https://docs.lemonsqueezy.com/api/license-api>
- Paddle elfogadási szabályok: <https://developer.paddle.com/partners/embed-billing/seller-go-live/minimum-seller-terms>
- GitHub ingyenes privát tárolók: <https://github.blog/news-insights/product-news/github-is-now-free-for-teams/>
