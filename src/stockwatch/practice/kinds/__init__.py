"""Feladattipusok (#49): egy kor feladatai kulonbozo tipusuak lehetnek.

  * decision - a regi: kihagyas / long / short dontes (practice/session.py, scoring.py, review.py)
  * stop (#50), pattern (#51), levels (#52), sizing (#53) - egy-egy modul ugyanazzal a felulettel:
      make(pick, rng, settings) -> a feladat (a kozos keszlet egy helyzete, vagy sajat szintetikus helyzet) es a
          parameterei: {"prompt": a felhasznalo latja, "secret": a jo valasz - a felfedes elott nem megy ki}
      validate(body, prompt) -> a valasz (ValueError: emberi szovegu hiba)
      evaluate(task, df, i, answer, settings) -> {"quality": 0..1, "lines": [sablonos sor], a chartra: "levels",
          "marks", "segments", "future"} - determinisztikus, minden szam a szamitasbol, LLM nelkul
  * pont: a minoseg aranyaban a "jo dontes" pontja (#34 skalaja); a tanulasi ut szamara a feladat "jo", ha a
    minoseg legalabb PASS
"""
from __future__ import annotations

from types import ModuleType

from ...config import Settings
from . import levels, pattern, sizing, stop

KINDS: dict[str, ModuleType] = {m.NAME: m for m in (stop, pattern, levels, sizing)}
ALL = ("decision", *KINDS)
PASS = 0.6


def get(kind: str) -> ModuleType:
    if kind not in KINDS:
        raise ValueError(f"Ismeretlen feladattipus: {kind}")
    return KINDS[kind]


def points(quality: float, settings: Settings) -> dict:
    value = int(round(settings.practice.points["good"] * quality))
    return {"points": value, "parts": [{"key": "quality", "label": f"Minőség {round(quality * 100)}%", "value": value}]}


def verdict(quality: float) -> dict:
    """A tanulasi ut (learning.record) es a statisztika szamara: jo, ha a minoseg legalabb PASS."""
    return {"category": "good" if quality >= PASS else "bad", "quality": quality}


def normalize_kinds(kinds: dict | None) -> dict[str, float]:
    """Feladattipus -> suly (None: csak dontes)."""
    kinds = kinds or {"decision": 1}
    bad = set(kinds) - set(ALL)
    if bad:
        raise ValueError(f"kinds: ismeretlen tipus: {', '.join(sorted(bad))}")
    weights = {k: float(v or 0) for k, v in kinds.items()}
    if any(v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("kinds: nemnegativ sulyok, legalabb egy pozitiv")
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items() if v > 0}
