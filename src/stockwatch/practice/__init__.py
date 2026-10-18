"""Gyakorlo mod: rejtett, multbeli chart-helyzetek, dontes, szimulacio, felfedes.

    sampler.py   feladatkeszlet (labor / CLI, adat-ujjlenyomatonkent egyszer) es a seedelt 20 helyzet
    session.py   munkamenet: a rejtett, normalizalt chart, a dontes rogzitese, a felfedes
    scoring.py   szimulacio (meanrev.simulate_trade, napkozbeni cellal), osszevetes, alapvonal, kor-statisztika
    store.py     munkamenetek (munkaterulet) es a kozos feladatkeszlet (data/practice/), lemezplafonnal

REJTES: a felfedes elott az API csak veletlen, a papirtol fuggetlen feladat-azonositot es normalizalt adatot ad
(index-tengely, az utolso lathato zaro = 100, relativ volumen); a papir es a datum csak a szerveroldali
munkamenetben van. A jovobeli ablak soha nem eri el a zarolt holdoutot. Tenyt es szimulaciot kozol, nem ajanl.
"""
