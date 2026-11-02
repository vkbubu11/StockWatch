"""Fejezetzaro ("boss") feladatsor a tanulasi uton (#79 b).

A fejezet (2-3 szint) vegen egy rovid sor MAS piaci rezsimbol, mint a szint feladatai: kulon szintetikus papirokon
(SYNC / SYNV / SYND + sorszam), amelyek a feladatkeszletben soha nem szerepelnek - igy a helyzetek nem fedhetik a
szintek feladatait. Az 1. fejezet utan erme-dobas: tiszta veletlen bolyongas, alakzat es sodrodas nelkul (jelzes
nelkul nincs elorejelzes, a kihagyas jo dontes); utana nagy volatilitas, majd tartos eso trend, beultetett alakzatokkal.
"""
from __future__ import annotations

import numpy as np

from . import sampler, synthetic

REGIMES = {1: "C", 2: "V", 3: "D"}                # fejezet -> a szintetikus generator rezsimje
SOURCES = {"C": "coin", "V": "volatile", "D": "downtrend"}
ROUNDS = 6
SYMBOLS = 120                                     # rezsimenkent ennyi kulon papir


def picks(chapter: int, seed: int, rounds: int = ROUNDS) -> list[dict]:
    """A fejezetzaro helyzetei (a sampler.sample alakjaban): seedbol determinisztikus."""
    regime = REGIMES[chapter]
    rng = np.random.default_rng([int(seed), 79, chapter])
    out, used = [], set()
    while len(out) < rounds:
        symbol = f"{synthetic.PREFIX}{regime}{int(rng.integers(1, SYMBOLS + 1)):04d}"
        if symbol in used:
            continue
        used.add(symbol)
        df, embedded = synthetic.generate(symbol)
        df = df.loc[:sampler.VISIBLE_END]
        ok = sampler.eligible(df)
        # minden masodik helyzet egy beultetett alakzat utolso pivotja utan (ahol van), a tobbi veletlen nap
        near = [(name, p) for name, _, _, p, _ in embedded if p + 10 < len(ok) and ok[p:p + 10].any()]
        if near and len(out) % 2 == 0:
            name, p = near[int(rng.integers(len(near)))]
            i = p + int(rng.choice(np.flatnonzero(ok[p:p + 10])))
            category, source = "pattern", name
        else:
            i = int(rng.choice(np.flatnonzero(ok)))
            category, source = "nothing", SOURCES[regime]
        out.append({"symbol": symbol, "date": f"{df.index[i]:%Y-%m-%d}", "category": category, "source": source,
                    "breakout": None})
    return out
