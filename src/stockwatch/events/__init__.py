"""Esemenytanulmanyok: mi tortent az arral egy adott esemenytipus utan?

    sources/form4.py          SEC Form 4 bennfentes-tranzakciok (negyedeves SEC-csomagok + elo XML)
    sources/index_changes.py  S&P 500 bekerulesek / kikerulesek (Wikipedia, tortenetei tablazat) + GICS-szektor
    catalog.py                esemenytipusok: mikor valt NYILVANOSSA (0. nap), es a kivalasztas adata
    study.py                  abnormalis hozam (piaci modell), CAR, bootstrap-sav, talalati arany, bontasok
    store.py                  mentett tanulmanyok (munkaterulet) es a kozos forrasadat (data/events/)
    service.py                futtatas: web (kis univerzum) / labor-munkasor (nagy univerzum) / CLI

POINT-IN-TIME: minden esemeny a nyilvanossa valas idopontjatol szamit; a kivalasztas es a becslesi ablak csak
az akkor ismert adatot hasznalja. A zarolt holdout alapbol kimarad (az adat elotte vagodik); kapcsoloval,
naplozva nyithato meg, mint a Backteszt oldalon. Tenyt es multbeli statisztikat kozol, nem ajanl.
"""
