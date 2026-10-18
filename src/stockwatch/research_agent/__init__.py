"""Ejszakai kutatougynok: a felhasznalo kutatasi celja (brief) alapjan strategiavaltozatokat general es tesztel,
reggelre jelentest ad a legjobb 3 jeloltrol. Determinisztikus keresessel, LLM nelkul.

    brief.py      a brief semaja (pydantic)
    space.py      a keresheto ter motoronkent (PullbackParams mezoi; Kitores: lab.EDITABLE)
    strategy.py   keresesi strategia: racs, veletlen, successive halving - rogzitett seeddel
    search.py     ertekeles a MEGLEVO motorokkal (meanrev_research.evaluate_variant, lab.evaluate),
                  dontosok, robusztussagi lepesek, koltsegkeret
    explain.py    sablonos indoklas es lepesnaplo-szovegek (templates.py, i18n katalogus)
    report.py     jelentes JSON + Markdown, tokegorbe
    store.py      briefek, futasok, naplo, plafonok (data/users/<munkaterulet>/agent/)
    service.py    futtatas: CLI (azonnal) es labor-munkasor (ejszaka / kerésre)

BECSULETESSEG: a kivalasztas CSAK a tanito idoszakon tortenik (a tanito adat a SPLIT_DATE elott vagodik);
a validacios idoszakot csak a dontosokre nezi meg, egyszer; a zarolt holdoutot (HOLDOUT_START-tol) soha nem
latja - az adat elotte vagodik, es a holdout-naplo nem valtozik. A kiprobalt valtozatok szama a jelentesben.
Nem kereskedik, nem ad befektetesi tanacsot.
"""
