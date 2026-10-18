"""Reggeli osszefoglalo: a watchlist es a Sajat poziciok valtozasai az elozo osszefoglalo ota.

    detect.py    allapot-pillanatkep (csak tarolt, LEZART adatbol) es valtozaserzekeles
    score.py     szabalyalapu fontossag (settings.yaml: digest.weights), plafonok
    render.py    mondatok a sablonokbol (templates.py, i18n katalogus) - minden szam a szamitasbol
    store.py     archivum: data/users/<munkaterulet>/digest/<nap>.json
    service.py   egy futas: pillanatkep -> valtozasok -> pontozas -> mentes -> (Discord)

Tenyeket kozol, nem ajanl: a rendszer nem kereskedik, es nem ad befektetesi tanacsot.
"""
