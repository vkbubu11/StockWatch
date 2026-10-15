"""Egy hir vagy bejelentes - a MEGJELENES idopontjaval (UTC), nem a letoltesevel."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

import pandas as pd


@dataclass
class Article:
    id: str                              # forras:azonosito (stabil, ujratoltesnel ugyanaz)
    source: str                          # edgar | yahoo | alpaca
    symbol: str
    published_at: str                    # ISO, UTC
    title: str
    kind: str = "news"                   # news | filing
    url: str = ""
    publisher: str = ""
    summary: str = ""
    form: str = ""                       # bejelentesnel: 8-K, 424B5...
    items: list[str] = field(default_factory=list)      # 8-K tetelkodok (pl. 2.02, 4.02)
    related: list[str] = field(default_factory=list)    # a forras szerinti kapcsolodo tickerek
    fetched_at: str = ""
    also: list[str] = field(default_factory=list)       # duplikatumkent mas forrasbol is megjott
    score: dict = field(default_factory=dict)           # scoring.score() kimenete (modellverzioval)

    @property
    def published(self) -> pd.Timestamp:
        return pd.Timestamp(self.published_at).tz_convert("UTC")

    @property
    def title_key(self) -> str:
        """Duplikatum-kulcs: normalizalt cim (kisbetu, csak betu/szam)."""
        norm = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def utc_iso(ts) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.isoformat(timespec="seconds")
