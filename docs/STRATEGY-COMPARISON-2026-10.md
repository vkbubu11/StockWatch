# Stratégia-összehasonlítás – 2026. október

Cél: legalább **70%-os nyerési arány** és nyereség, a tanító (2022 előtt) **és** a validációs (2022 – 2025.03)
időszakban külön-külön is. A **holdout (2025.04-től) zárolt maradt**, egyik futás sem nyitotta meg.

Adat: yfinance napi gyertyák, 10 év (2016.10-től; a bemelegítés miatt az első kötés 2017-ben van).
Költség: részvény 5 bps, ETF 3 bps oldalanként. A szimuláció pesszimista (azonos gyertyán stop és jel → stop;
a kilépés a jel utáni nyitáson).

## Futtatott parancsok

```bash
pip install -r requirements.txt
python cli.py meanrev-research --etf
python cli.py meanrev-research --etf --neighbourhood
python cli.py fetch-universe
python cli.py meanrev-research                     # 1. futás: csak 92 papírnak volt adata
# + a teljes Nasdaq-100 / S&P 500 tagság letöltése a kutatási cache-be, majd újra:
python cli.py meanrev-research                     # 2. futás: 514 papír
python cli.py meanrev-research --deep --neighbourhood --survivorship-check   # az új stratégia
```

Az első RSI2-futás az 518 indextagból csak 92 papíron számolt, mert a `fetch-universe` csak a
`config/universe.yaml` listáját (106 papír) tölti le, az indextagokét nem. Ezért a teljes tagságot is letöltöttem,
és újrafuttattam – a táblázatban mindkét eredmény szerepel.

## Összehasonlító táblázat

| Stratégia | Időszak | Kötés | Heti | Nyerési arány (95%) | Átlag nettó / kötés | Átl. bootstrap 95% | PF | Max DD* | 70%? |
|---|---|---|---|---|---|---|---|---|---|
| **ETF visszahúzás** (alap, 24 ETF) | Tanító | 187 | 0,83 | 62,0% (54,9–68,7) | +0,14% | −0,40 … +0,62 | 1,16 | 9,6% | ✗ |
| | Validáció | 132 | 0,78 | 65,2% (56,7–72,7) | +0,14% | −0,39 … +0,62 | 1,18 | 7,3% | ✗ |
| ETF – legjobb szomszéd (RSI2<10, limit 1%, CRSI>70) | Tanító | 174 | – | 69,5% | +0,39% | – | – | – | ✗ |
| | Validáció | 125 | – | 64,8% | +0,11% | – | – | – | ✗ |
| ETF – Connors kumulatív RSI (2 napos RSI2-összeg < 35, kilépés RSI2 > 65) ** | Tanító | 177 | 0,78 | 70,1% | +0,65% | +0,12 … | 1,86 | 5,9% | ✓ |
| | Validáció | 134 | 0,79 | 66,4% | +0,09% | −0,64 … | 1,08 | 7,4% | ✗ |
| **Visszahúzás (RSI2)** – 92 papír (1. futás) | Tanító | 44 | 0,19 | 79,5% (65,5–88,8) | +2,50% | – | 3,92 | 1,1% | ✓ |
| | Validáció | 11 | 0,07 | 72,7% (43,4–90,3) | +1,44% | – | 2,65 | 0,5% | ✓ (kis minta) |
| **Visszahúzás (RSI2)** – 514 papír (2. futás) | Tanító | 151 | 0,67 | 72,8% (65,3–79,3) | +1,69% | −0,44 … +3,13 | 2,47 | 8,5% | ✓ |
| | Validáció | 40 | 0,24 | 75,0% (59,8–85,8) | +2,50% | +0,18 … +4,32 | 4,57 | 1,3% | ✓ |
| **Mély visszahúzás (ÚJ)** – 514 papír | Tanító | 151 | 0,67 | **76,2%** (68,8–82,3) | **+2,09%** | **+0,98** … +2,86 | **3,58** | 3,6% | ✓ |
| | Validáció | 40 | 0,24 | **75,0%** (59,8–85,8) | **+2,48%** | **+0,12** … +4,31 | **4,41** | 1,4% | ✓ |

\* Fix súlyú pozícióval (Visszahúzás: 10%, ETF: 25% / kötés). ** Saját feltérképezés a tanító időszakon (eldobható szkripttel), nem a CLI része.

Kumulált hozam a teljes látható időszakra (2017 – 2025.03, fix súllyal): ETF +11,3%, Visszahúzás (RSI2) +42,3%,
Mély visszahúzás +51,1%.

### Elfogadási feltétel (a kód előre rögzített kapuja)

| Feltétel | ETF | RSI2 (514) | Mély visszahúzás |
|---|---|---|---|
| Nyerési arány ≥ 70% (tanító / validáció) | ✗ / ✗ | ✓ / ✓ | ✓ / ✓ |
| Heti 0,7–3 kötés (tanító / validáció) | ✓ / ✓ | ✗ / ✗ | ✗ / ✗ |
| Várható érték > 0 a bootstrap alsó határán is (tanító / validáció) | ✗ / ✗ | **✗** / ✓ | ✓ / ✓ |
| Profit-faktor ≥ 1,3 (tanító / validáció) | ✗ / ✗ | ✓ / ✓ | ✓ / ✓ |
| Legalább 7 évben ≥ 60% | ✗ | ✓ | ✓ (8/9 év; 2022: 43%) |
| 2× költséggel is ≥ 68% | ✗ (63,3%) | ✓ (72,3%) | ✓ (74,9%) |
| Survivorship-ellenőrzés (csődbe ment / kiesett papírokkal) | – | – | 70,4%, +1,62% / kötés |

## Értékelés

**ETF visszahúzás – nem éri el a 70%-ot.** Az alapbeállítás 62–65%-on áll, átlag +0,14% / kötés, PF 1,2 – a
költségek után alig nyereséges, a bootstrap alsó határa negatív. A 36 szomszédos beállításból egy sem éri el a
70%-ot mindkét időszakban (a legjobb: 69,5% / 64,8%). A Connors-féle kumulatív RSI tanítón 70,1%, de validáción
66,4%-ra esik. Kipróbálva (csak a tanítón): VIX-szűrő, SPY > SMA200 piaci rezsim, első felfelé záró / SMA5 / előző
csúcs feletti kilépés, tágabb stop, hosszabb időkorlát – egyik sem emeli tartósan 70% fölé. **A 24 likvid ETF-en ez a
stratégiacsalád nagyjából 66–70%-nál megáll**, ezért nem erre építettem.

**Visszahúzás (RSI2) – eléri a 70%-ot**, mindkét időszakban (72,8% / 75,0%), nyereséges (PF 2,5 / 4,6). Gyengeségei:
ritka (heti 0,24–0,67 kötés), és a tanító időszakban a várható érték bootstrap alsó határa negatív (−0,44%): a 3 ATR-es
stop kötéseinek ~10%-a a mélyponton zár, és ezek nagy veszteségek.

**Mély visszahúzás (új) – a legjobb.** Ugyanaz a bemenet, mint az RSI2-é, de **5 ATR-es (tág) stop**. Tanítón
76,2%, validáción 75,0%, PF 3,6 / 4,4, a várható érték bootstrap alsó határa mindkét időszakban pozitív, a max.
visszaesés 8,5% → 3,6%. 2× költséggel is 74,9%, a csődbe ment papírokat is tartalmazó listán 70,4%.
A stabilitásvizsgálatban a limit 5% / RSI2 < 2–3 környéke végig 70% körül vagy felett van, és a stop tágítása
a tanító időszakban minden szomszédban emeli a nyerési arányt (a validáción nem ront) – tehát nem egyetlen szerencsés pont.

### Mire figyelj (őszintén)

- **A heti kötésszám-kapu nem teljesül** (0,67 / 0,24 kötés / hét). A validációs időszak ritka, mert 2022-ben a
  rezsimszűrő a medvepiacon többnyire tiltott. 40 validációs kötés kis minta: a 95%-os intervallum 59,8–85,8%.
- **2022 rossz év** (7 kötés, 43%): a rezsimszűrő ellenére a medvepiaci rallik közben vett visszahúzások buktak.
- **Csak a MOSTANI indextagok** – a túlélési torzítás felfelé hat; a survivorship-ellenőrzés (70,4%) mutatja, hogy a
  70% e nélkül is nagyjából tartható, de a határon van.
- A választás **csak a tanító időszak** alapján történt (a validációt a kiválasztott jelölteken néztem meg); a végső
  szó a zárolt holdouté: `python cli.py meanrev-research --deep --holdout` – egyszer, ezzel a rögzített szabállyal.
- A tág stop nagyobb pozíciónkénti kockázatot jelent: azonos kockázat mellett ~40%-kal kisebb darabszám
  (`entry.position_size` ezt magától kezeli).

## Mi került a szoftverbe

- `config.py`: új `deep` stratégia (`DeepPullbackStrategy`) – „Mély visszahúzás”, `stop_atr = 5`, minden más a
  Visszahúzás (RSI2) alapértéke. Alapból **fut és virtuálisan követ, de értesítést nem küld** (a jelzései
  egyeznek a Visszahúzáséval); a paper fiókhoz a Stratégiák fülön köthető.
- `monitor.py`: a napi zárón a Visszahúzás-motorral fut, a Nasdaq-100 + S&P 500 listán.
- `cli.py`: `meanrev-research --deep` (kimenet: `data/research/meanrev/deep/`), külön holdout-naplózással.
- `meanrev_research.py`: a szomszédság-vizsgálat a stratégia saját stopját is tartalmazza.
- Web: stratégia-kártya, saját szín, kutatási összefoglaló. Tesztek: `tests/test_deep_strategy.py`.

Nem befektetési tanács.
