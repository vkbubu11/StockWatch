"""Keresesi strategia - TISZTAN DETERMINISZTIKUS, rogzitett seeddel.

  * grid     a teljes racs (csak ha belefer a valtozat-keretbe)
  * random   veletlen valtozatok (seed), utana koordinata-finomitas a legjobb korul
  * halving  successive halving: sok valtozat a papirok kis reszhalmazan, a legjobb harmad tovabb egyre
             nagyobb reszhalmazra, vegul az osszes papirra; utana koordinata-finomitas

A papir-reszhalmaz a seedbol es a tickerbol szamolt hash szerinti sorrend eleje: ugyanaz a seed ugyanazt
a reszhalmazt adja, a sorrend nem fugg a betoltes sorrendjetol.
"""
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass

import numpy as np

from .space import Space

HALVING_FRACTIONS = (0.25, 0.5, 1.0)
KEEP_SHARE = 1 / 3
INITIAL_SHARE = {"halving": 0.6, "random": 0.7}      # a valtozat-keret ennyi resze megy a kezdo mintara
MIN_KEEP = 3


@dataclass
class Plan:
    initial: list[dict]                 # a kezdo valtozatok (a preset az elso)
    fractions: tuple[float, ...]        # fokonkent a papirok aranya
    refine_budget: int                  # a finomitasra marado uj valtozatok szama


def subset(symbols: list[str], fraction: float, seed: int) -> list[str]:
    if fraction >= 1:
        return sorted(symbols)
    order = sorted(symbols, key=lambda s: hashlib.sha1(f"{seed}:{s}".encode()).hexdigest())
    n = max(1, int(round(len(order) * fraction)))
    return sorted(order[:n])


def sample(space: Space, n: int, rng: np.random.Generator, exclude: set[str]) -> list[dict]:
    out, seen, tries = [], set(exclude), 0
    while len(out) < n and tries < n * 50:
        tries += 1
        a = {d.name: d.values[int(rng.integers(len(d.values)))] for d in space.dims}
        k = space.key(a)
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def grid(space: Space) -> list[dict]:
    names = [d.name for d in space.dims]
    return [dict(zip(names, combo)) for combo in itertools.product(*(d.values for d in space.dims))]


def plan(space: Space, method: str, max_variants: int, seed: int) -> Plan:
    preset = space.preset_assignment()
    if method == "grid":
        if space.size > max_variants:
            raise ValueError(f"A racs {space.size} valtozat, a keret {max_variants}: szukitsd a terel "
                             "(search.fixed), vagy valaszd a halving / random modszert.")
        items = grid(space)
        items.sort(key=lambda a: a != preset)                     # a preset legyen elol
        return Plan(items, (1.0,), 0)
    rng = np.random.default_rng(seed)
    n0 = max(MIN_KEEP * 3, int(max_variants * INITIAL_SHARE[method]))
    n0 = min(n0, max_variants, space.size)
    initial = [preset] + sample(space, n0 - 1, rng, {space.key(preset)})
    fractions = HALVING_FRACTIONS if method == "halving" else (1.0,)
    return Plan(initial, fractions, max(max_variants - len(initial), 0))


def keep_count(n: int) -> int:
    return max(MIN_KEEP, int(np.ceil(n * KEEP_SHARE)))
