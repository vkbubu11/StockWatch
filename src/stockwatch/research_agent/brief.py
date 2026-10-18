"""A kutatasi cel (brief) semaja.

    name: swing-long-indexek
    engine: pullback            # pullback | deep | etf (Visszahuzas-csalad) | breakout (Kitores)
    universe: indexes           # indexes | nasdaq100 | sp500 | watchlist | etf | research
    objective:
      trades_per_week: [0.5, 2.0]
      max_drawdown_pct: 10
      max_hold_days: 15
      cost_bps: 15              # oldalankent, a futas teljes idejere rogzitve
      min_win_lo: null          # opcionalis FELTETEL: a nyeresi arany Wilson-also hatara (pl. 0.65)
      min_pf: 1.3
    search:
      seed: 42
      method: halving           # grid | random | halving
      max_variants: 300
      max_cpu_minutes: 120
      fixed: {regime_filter: true}

A celmutato mindig az atlagos netto hozam (Kitoresnel R) bootstrap also hatara (ev_lo) - a nyeresi arany
onmagaban tag stoppal es kis cellal felfele tornazhato, mikozben a varhato ertek romlik (REVIEW-2026-10).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ENGINES = ("pullback", "deep", "etf", "breakout")
UNIVERSES = ("indexes", "nasdaq100", "sp500", "watchlist", "etf", "research")
ENGINE_LABELS = {"pullback": "Visszahúzás (RSI2)", "deep": "Mély visszahúzás", "etf": "ETF visszahúzás",
                 "breakout": "Kitörés"}
UNIVERSE_LABELS = {"indexes": "Nasdaq-100 + S&P 500", "nasdaq100": "Nasdaq-100", "sp500": "S&P 500",
                   "watchlist": "Watchlist", "etf": "ETF-lista", "research": "Kutatási minta"}


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades_per_week: tuple[float, float] = (0.5, 2.0)
    max_drawdown_pct: float = 10.0
    max_hold_days: int = 15
    cost_bps: float = 15.0
    min_win_lo: float | None = None
    min_pf: float | None = 1.3
    min_trades: int = 30                 # a tanito idoszakban legalabb ennyi lezart kotes (kevesebb: foleg zaj)

    @model_validator(mode="after")
    def _check(self) -> "Objective":
        lo, hi = self.trades_per_week
        if not 0 <= lo < hi <= 50:
            raise ValueError("objective.trades_per_week: [also, felso], 0 <= also < felso <= 50")
        if not 0.5 <= self.max_drawdown_pct <= 100:
            raise ValueError("objective.max_drawdown_pct: 0,5 es 100 kozott")
        if not 1 <= self.max_hold_days <= 60:
            raise ValueError("objective.max_hold_days: 1 es 60 kozott")
        if not 0 <= self.cost_bps <= 200:
            raise ValueError("objective.cost_bps: 0 es 200 kozott")
        if self.min_win_lo is not None and not 0 < self.min_win_lo < 1:
            raise ValueError("objective.min_win_lo: 0 es 1 kozott (pl. 0.65)")
        if self.min_pf is not None and not 0 < self.min_pf <= 20:
            raise ValueError("objective.min_pf: 0 es 20 kozott")
        if not 5 <= self.min_trades <= 1000:
            raise ValueError("objective.min_trades: 5 es 1000 kozott")
        return self


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = 42
    method: Literal["grid", "random", "halving"] = "halving"
    max_variants: int = 300
    max_cpu_minutes: float = 120.0
    fixed: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Search":
        if not 0 <= self.seed <= 2 ** 31:
            raise ValueError("search.seed: 0 es 2^31 kozott")
        if not 5 <= self.max_variants <= 5000:
            raise ValueError("search.max_variants: 5 es 5000 kozott")
        if not 1 <= self.max_cpu_minutes <= 600:
            raise ValueError("search.max_cpu_minutes: 1 es 600 kozott")
        return self


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    engine: Literal["pullback", "deep", "etf", "breakout"] = "pullback"
    universe: Literal["indexes", "nasdaq100", "sp500", "watchlist", "etf", "research"] = "indexes"
    objective: Objective = Field(default_factory=Objective)
    search: Search = Field(default_factory=Search)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(str(value).split())
        if not 1 <= len(value) <= 60:
            raise ValueError("name: 1-60 karakter")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        if len(value) > 500:
            raise ValueError("description: legfeljebb 500 karakter")
        return value

    @model_validator(mode="after")
    def _check(self) -> "Brief":
        if self.engine == "breakout" and self.universe == "etf":
            raise ValueError("A Kitores-motor nem fut az ETF-listan (a labor univerzumain fut).")
        return self

    @property
    def id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower().encode("ascii", "ignore").decode()).strip("-")[:32]
        digest = hashlib.sha1(self.name.encode("utf-8")).hexdigest()[:6]
        return f"{slug or 'brief'}-{digest}"

    def fingerprint(self) -> str:
        """A brief tartalmanak hash-e (a jelentes determinizmusahoz)."""
        return hashlib.sha1(self.model_dump_json().encode("utf-8")).hexdigest()[:12]

    @classmethod
    def from_yaml(cls, text: str) -> "Brief":
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("A brief YAML-objektum legyen (kulcs: ertek).")
        return cls(**data)

    def to_yaml(self) -> str:
        data = self.model_dump()
        data["objective"]["trades_per_week"] = list(data["objective"]["trades_per_week"])
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
