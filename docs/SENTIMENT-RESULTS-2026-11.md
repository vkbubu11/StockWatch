# Hírhangulat-validálás (#20, 2. fázis) – eredmény, 2026-11-06

Előre rögzített hipotézisek és kapu: `src/stockwatch/sentiment/research.py`, `docs/SENTIMENT-DESIGN.md`.
Adat: a kutatási univerzum (106 papír), Alpaca (Benzinga) hírek + SEC-bejelentések 2016-10-01-től, point-in-time
(a `t` napi jelzés csak a 16:00 NY előtti híreket látja). Csak a hírforrással lefedett jelzésnapok számítanak.
Holdout zárva. Futtatás: `python cli.py news-research`.

| strategia / valtozat | idoszak | alap n | alap nyeres | megtartott nyeres | kiesik | alap EV | megtartott EV | kapu |
|---|---|---|---|---|---|---|---|---|
| Visszahuzas: H1a: kihagyas lenyeges negativ hirnel | tanito | 55 | 76.4% | 77.1% | 13% | +1.97% | +1.95% | nem |
| Visszahuzas: H1a: kihagyas lenyeges negativ hirnel | validacio | 25 | 60.0% | 60.0% | 0% | +0.53% | +0.53% | nem |
| Visszahuzas: H1b: kihagyas, ha negativ arany >= 50% es hirvolumen z >= 2 | tanito | 55 | 76.4% | 76.4% | 0% | +1.97% | +1.97% | nem |
| Visszahuzas: H1b: kihagyas, ha negativ arany >= 50% es hirvolumen z >= 2 | validacio | 25 | 60.0% | 60.0% | 0% | +0.53% | +0.53% | nem |
| Mely visszahuzas: H1a: kihagyas lenyeges negativ hirnel | tanito | 55 | 76.4% | 77.1% | 13% | +2.16% | +2.17% | nem |
| Mely visszahuzas: H1a: kihagyas lenyeges negativ hirnel | validacio | 25 | 60.0% | 60.0% | 0% | +0.53% | +0.53% | nem |
| Mely visszahuzas: H1b: kihagyas, ha negativ arany >= 50% es hirvolumen z >= 2 | tanito | 55 | 76.4% | 76.4% | 0% | +2.16% | +2.16% | nem |
| Mely visszahuzas: H1b: kihagyas, ha negativ arany >= 50% es hirvolumen z >= 2 | validacio | 25 | 60.0% | 60.0% | 0% | +0.53% | +0.53% | nem |
| Kitores: H2a: csak pozitiv hangulat es hirvolumen z >= 1 | tanito | 147 | 51.7% | 33.3% | 98% | +0.06 R | -0.51 R | nem |
| Kitores: H2a: csak pozitiv hangulat es hirvolumen z >= 1 | validacio | 77 | 62.3% | - | 100% | +0.29 R | - | nem |
| Kitores: H2b (ellenproba): kihagyas lenyeges negativ hirnel | tanito | 147 | 51.7% | 50.0% | 14% | +0.06 R | +0.03 R | nem |
| Kitores: H2b (ellenproba): kihagyas lenyeges negativ hirnel | validacio | 77 | 62.3% | 60.8% | 4% | +0.29 R | +0.25 R | nem |

## Döntés

- **Egyik változat sem ment át a kapun** (mindkét időszakban ≥ +2 pp nyerés, ≤ 25% kiesés, nem romló EV).
- H1 (Visszahúzás, Mély visszahúzás): a tanító időszakban +0,7 pp, a validációban nincs hatás (0% kiesés).
- H2 (Kitörés): a „pozitív hangulat + nagy hírvolumen” feltétel a kötések 98–100%-át kiejti; az ellenpróba
  (kihagyás lényeges negatív hírnél) kissé ront.
- Következmény: a hírhangulat **nem** kerül döntési szerepbe és a célár-valószínűség modelljébe sem (G csoport);
  a felületen tájékoztatásként, külön sorban marad (árnyék-üzem).
- Korlát: a Visszahúzás-jelzések száma a 106 papíros mintán kicsi (80 lefedett jelzés); a teljes indexen való mérés
  az indextagok hírarchívumát igényli (~5× adat, a 300 MB-os plafonon belül).
