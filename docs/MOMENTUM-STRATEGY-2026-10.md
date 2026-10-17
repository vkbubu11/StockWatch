# Momentum (12-1) – új stratégia a „151 Trading Strategies” alapján (2026. október)

Forrás: Zura Kakushadze & Juan Andrés Serur: *151 Trading Strategies* (Palgrave Macmillan, 2018;
a szerzők ingyenes változata: [SSRN 3247865](https://papers.ssrn.com/abstract=3247865)). A könyv a stratégiákat
képletekkel és irodalmi hivatkozásokkal írja le, saját backteszt nélkül – ezért minden jelöltet a mi adatunkon,
a projekt szokásos rendjével mértem (tanító 2017–2021, validáció 2022 – 2025.03, a 2025.04-től tartó holdout
**zárolt maradt**, be sem töltöttük).

## Mit néztem meg a könyvből

A rendszer long-only, napi gyertyákon dolgozik, a Nasdaq-100 + S&P 500 tagjain és likvid ETF-eken. Ezért a
részvény- (3. fejezet) és ETF- (4. fejezet) stratégiák közül azokat mértem, amelyek ehhez illenek:

| Könyv | Stratégia (saját összefoglalás) | Eredmény a mi adatunkon |
|---|---|---|
| 3.1 | Ár-momentum: 12 havi hozam a legutóbbi hónap nélkül, havi rebalansz, a legjobbak long | **erős, stabil többlet** – ez lett az új stratégia |
| 3.4 | Alacsony volatilitás: a legkisebb ingadozású papírok | a hozam elmarad az univerzumétól |
| 3.6 | Multifaktor (momentum + alacsony volatilitás rangjainak átlaga) | gyengébb a tiszta momentumnál |
| 4.1 / 4.1.1 | Szektor-ETF rotáció (6 / 12-1 hónapos hozam), mozgóátlag-szűrővel | elmarad a SPY tartásától |
| 4.1.2 | „Dual momentum”: csak ha a piac trendje pozitív, különben készpénz / kötvény | mindenhol **rontott** (ki-be lépegetés) |
| 4.6 | Több eszközös trendkövetés (részvény, kötvény, arany, nyersanyag ETF-ek) | elmarad a SPY tartásától |

A többi részvény-stratégia (érték, gyorsjelentés-momentum, implikált volatilitás, párkereskedés, statisztikai
arbitrázs, market making) vagy fundamentális / opciós adatot, vagy short oldalt igényel, ami most nincs a rendszerben.
A könyv saját megjegyzése is az, hogy az egyedi papírokon futó technikai szabályok kevésbé megalapozottak, mint a
keresztmetszeti (sok papírt egymáshoz mérő) módszerek – a mostani mérés ezt alátámasztotta.

## Az új stratégia szabályai (`config.MomentumParams`)

- **Rebalansz:** a hónap utolsó kereskedési napjának záróján.
- **Pontszám:** záró(t−21) / záró(t−252) − 1 – 12 havi hozam, a legutóbbi ~1 hónap nélkül (az rövid távon inkább visszafordul).
- **Jogosult:** záró ≥ 5 $, 20 napos átlagos forgalom ≥ 20 M$, elég hosszú historia.
- **Portfólió:** a legjobb **10** papír, egyenlő súllyal (tőke / 10).
- **Puffer:** a tartott papír addig marad, amíg a legjobb **20** között van – a forgást és a költséget csökkenti.
- **Végrehajtás:** a rebalanszt követő **nyitáson** (piaci megbízás); a kilépés a nyitóaukción.
- **Stop:** nincs (lásd lent – a validáció szerint rontott). `stop_pct`-vel bekapcsolható.

## Hogyan választottam (csak a tanító időszakon)

1. Felderítés: a fenti jelöltek, a könyv paramétereivel, előre rögzítve.
2. A momentum többletét **ugyanannak az univerzumnak az egyenlő súlyú tartásához** mértem. A túlélési torzítás
   (csak a mostani indextagokról van adat) mindkettőt felfelé húzza, a különbség a válogatás értéke.
3. Kockázatkezelési változatok a tanító időszakon: 10 / 20 / 30 papír, puffer, 15 / 20 / 25%-os stop, inverz
   volatilitás szerinti súly, piaci rezsimszűrő. A legjobb Sharpe-mutatót a **10 papír + 20-as puffer, stop nélkül**
   adta, mindkét univerzumon. A stopok mindhárom szinten rontottak, a rezsimszűrő is.
4. Utána néztem meg a validációt, és futott le az előre rögzített elfogadási kapu (`momentum_research.GATE`).

## Eredmény (`python cli.py momentum-research --survivorship-check`)

Költség: 5 bps / oldal. 518 mostani indextag.

| Időszak | Évi hozam | Univerzum (egyenlő súly) | Többlet / év | Sharpe | Max. visszaesés |
|---|---|---|---|---|---|
| Tanító (2017–2021) | +30,4% | +17,1% | +14,4% | 0,96 | −37,9% |
| Validáció (2022 – 2025.03) | +51,7% | +9,0% | +41,4% | 1,16 | −37,8% |

- **Vegyes lista** (`config/universe.yaml`, kiesett és gyengén teljesítő papírokkal is): a többlet tanítón
  **+7,6%**, validáción **+9,5%** / év – kisebb, de pozitív. **Ez a reálisabb várakozás.**
- **Dupla költséggel** is szinte változatlan (havi rotáció, kevés kötés: ~16 / év).
- Kötésenként: 55% nyerő, átlag +26% (a nyerők sokáig maradnak, a vesztesek gyorsan kiesnek).
- Évenként: 2018 +9%, 2019 +31%, 2020 +115%, 2021 +22%, 2022 +6%, 2023 +53%, 2024 +162%, 2025 (márciusig) −9,5%.
  (2017 szinte üres: a 12 havi ablakhoz 2016 októberétől van adat.)
- Elfogadási kapu: **minden pontja teljesül** (többlet, Sharpe ≥ 0,8, 2× költség, vegyes lista – mindkét időszakban).

## Őszintén a kockázatokról

- **A számok felfelé torzítanak.** A 2024-es +162% nagyrészt olyan papírokból jön (pl. a mesterséges intelligencia
  nyertesei), amelyek *azért* vannak ma az indexben, mert felmentek. A vegyes listán mért évi +8–10% többlet a
  becsületesebb kép.
- **Nagy visszaesések.** A tanítón és a validáción is ~38% (a felderítő, durvább szimulációban 40–48%), és a mintában nincs igazi momentum-összeomlás (mint 2009 tavaszán,
  amikor a korábbi vesztesek pattantak vissza a legerősebben). Ilyenkor a stratégia egy hónap alatt nagyot eshet.
- **Koncentrált:** 10 papír, gyakran ugyanabból a szektorból.
- **Más jellegű, mint a többi stratégia.** Nem 70%-os nyerési arányú, rövid kötésekből áll, hanem kevés, hosszan
  tartott pozícióból – a nyereséget néhány nagy nyerő adja. Az élesítési kapu (`broker.live_gate_win` = 70%)
  erre a stratégiára nem illik; ezt élesítés előtt külön kell eldönteni.
- A holdout (2025.04-től) **még zárolt**. A végső szó: `python cli.py momentum-research --holdout` – egyszer.

## Mi került a szoftverbe

- `momentum.py`: pontszám, rangsor, rebalansz-döntés, naptár (NYSE), kötés-lejátszás, jelzés – egyetlen igazságforrás.
- `momentum_research.py` + `cli.py momentum-research`: a validálás (kimenet: `data/research/momentum/`, lemezplafonnal).
- `monitor.momentum_cycle`: minden zárás után rangsor (`data/momentum_state.json`) és a kötések követése; a hónap
  utolsó kereskedési napján rebalansz → értesítés, virtuális követés, és ha ez a paper fiókhoz kötött stratégia,
  paper megbízás (`broker.submit_momentum`; kilépés a nyitóaukción).
- `config.py`: `momentum` stratégia (alapból **be van kapcsolva**: értesít és virtuálisan követ; a paper fiókhoz a
  Stratégiák fülön köthető). Ha a paper fiókhoz kötöd, egyszerre 10 pozíciót tart.
- Felület: stratégia-kártya (szabályok, validálás, a mostani rangsor), a „Papír kiértékelése” ablakban a papír
  momentum-rangja, a jelzésnaplóban és az értesítésekben saját címke.
- Tesztek: `tests/test_momentum.py`.

Nem befektetési tanács.
