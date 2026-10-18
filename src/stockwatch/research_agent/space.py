"""A kereshető ter motoronkent.

Visszahuzas-csalad (pullback / deep / etf): a config.PullbackParams mezoi, ertekkeszlettel. A kiindulo
beallitas (preset) a motor MOSTANI parametere a brief koltsegevel; az ertekkeszlet mindig tartalmazza a
preset erteket, igy a preset maga is egy kiprobalt valtozat (osszevetes).
Kitores (breakout): a lab.EDITABLE mezoi, az elo `entry` beallitas felett, a lab.validate_entry ellenorzesevel.

Minden valtozat a motor sajat validatoran megy at (PullbackParams / lab.validate_entry): az ervenytelent
elutasitja, a kereses folytatodik.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..config import EntrySettings, PullbackParams, Settings
from .brief import Brief


@dataclass(frozen=True)
class Dim:
    name: str
    values: tuple
    feature: bool = False          # a jellemzoket is ujraszamoltatja (meanrev.features) - csoportositashoz


PULLBACK_DIMS = (
    Dim("rsi2_max", (1.0, 2.0, 3.0, 5.0, 7.0, 10.0)),
    Dim("limit_pct", (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)),
    Dim("stop_atr", (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)),
    Dim("exit_crsi", (40.0, 50.0, 60.0, 70.0, 80.0)),
    Dim("exit_sma", (0, 5, 10), feature=True),
    Dim("time_stop", (3, 5, 7, 10, 15, 20)),
    Dim("trend_sma", (100, 150, 200), feature=True),
    Dim("regime_filter", (False, True)),
    Dim("earnings_blackout_days", (0, 3, 5)),
    Dim("max_open", (0, 5, 10)),
)
ETF_DIMS = (
    Dim("rsi2_max", (3.0, 5.0, 10.0, 15.0, 20.0)),
    Dim("limit_pct", (0.0, 0.5, 1.0, 1.5, 2.0)),
    Dim("stop_atr", (3.0, 4.0, 5.0, 6.0, 8.0)),
    Dim("exit_crsi", (40.0, 50.0, 60.0, 70.0)),
    Dim("exit_sma", (0, 5, 10), feature=True),
    Dim("time_stop", (3, 5, 7, 10, 15)),
    Dim("trend_sma", (100, 150, 200), feature=True),
    Dim("regime_filter", (False, True)),
    Dim("max_open", (2, 4, 6, 0)),
)
BREAKOUT_DIMS = (
    Dim("min_score", (40.0, 50.0, 60.0, 70.0)),
    Dim("min_stop_atr", (1.5, 2.0, 2.5, 3.0)),
    Dim("exit_mode", ("target", "partial", "trail", "partial_trail")),
    Dim("partial_r", (0.5, 1.0, 1.5)),
    Dim("trail_atr", (2.0, 3.0, 4.0)),
    Dim("min_risk_reward", (1.5, 2.0, 2.5, 3.0)),
    Dim("max_extension_atr", (0.5, 1.0, 1.5, 2.0)),
    Dim("min_breakout_volume", (0.0, 1.0, 1.5)),
    Dim("regime_filter", (False, True)),
    Dim("earnings_blackout_days", (0, 7)),
    Dim("max_contraction", (0.0, 0.8, 1.0)),
)

FIELD_LABELS = {
    "rsi2_max": "RSI(2) küszöb", "limit_pct": "limit a záró alatt (%)", "stop_atr": "stop (ATR)",
    "exit_crsi": "kilépési ConnorsRSI", "exit_sma": "kilépési SMA", "time_stop": "időkorlát (nap)",
    "trend_sma": "trend-SMA", "regime_filter": "rezsimszűrő", "earnings_blackout_days": "gyorsjelentés-tilalom (nap)",
    "max_open": "max. nyitott kötés", "max_signals_per_day": "napi max. jelzés", "min_price": "min. ár",
    "min_dollar_volume": "min. dollárforgalom", "atr_period": "ATR-periódus",
    "min_score": "min. pontszám", "min_stop_atr": "min. stop (ATR)", "exit_mode": "kilépési mód",
    "partial_r": "részleges zárás (R)", "trail_atr": "követett stop (ATR)", "min_risk_reward": "min. hozam/kockázat",
    "max_extension_atr": "max. távolság a triggertől (ATR)", "min_breakout_volume": "min. kitörési volumen",
    "max_contraction": "max. összehúzódás",
}


def _canonical(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    return float(value) if isinstance(value, float) else int(value)


@dataclass
class Space:
    engine: str
    dims: list[Dim]
    preset: dict                                        # a motor teljes kiindulo beallitasa (minden mezo)
    fixed: dict = field(default_factory=dict)

    # -- letrehozas ---------------------------------------------------------
    @classmethod
    def from_brief(cls, brief: Brief, settings: Settings) -> "Space":
        obj = brief.objective
        if brief.engine == "breakout":
            preset = settings.entry.model_dump()
            preset["slippage_bps"] = obj.cost_bps
            dims = list(BREAKOUT_DIMS)
            allowed = _breakout_fields()
        else:
            params = settings.strategies.get(brief.engine).params
            preset = params.model_dump() | {"cost_bps": obj.cost_bps,
                                            "time_stop": min(params.time_stop, obj.max_hold_days)}
            dims = list(ETF_DIMS if brief.engine == "etf" else PULLBACK_DIMS)
            dims = [Dim(d.name, tuple(v for v in d.values if v <= obj.max_hold_days), d.feature)
                    if d.name == "time_stop" else d for d in dims]
            allowed = set(PullbackParams.model_fields) - {"commission_bps", "cost_bps"}
        fixed = dict(brief.search.fixed)
        unknown = set(fixed) - allowed
        if unknown:
            raise ValueError(f"search.fixed: nem allithato mezo: {', '.join(sorted(unknown))}")
        preset.update(fixed)
        out = []
        for d in dims:
            if d.name in fixed:
                continue
            base = _canonical(preset.get(d.name))
            values = tuple(dict.fromkeys(_canonical(v) for v in d.values))
            if base not in values:                       # a preset erteke mindig a terben van
                values = tuple(sorted(values + (base,), key=_order)) if base is not None else (None,) + values
            out.append(Dim(d.name, values, d.feature))
        return cls(brief.engine, out, preset, fixed)

    # -- valtozatok ---------------------------------------------------------
    @property
    def size(self) -> int:
        n = 1
        for d in self.dims:
            n *= len(d.values)
        return n

    def preset_assignment(self) -> dict:
        return {d.name: _canonical(self.preset.get(d.name)) for d in self.dims}

    @staticmethod
    def key(assignment: dict) -> str:
        raw = json.dumps({k: _canonical(v) for k, v in sorted(assignment.items())}, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def full(self, assignment: dict) -> dict:
        return dict(self.preset) | {k: _canonical(v) for k, v in assignment.items()}

    def params(self, assignment: dict):
        """A valtozat validalt parameterei: PullbackParams, illetve (Kitoresnel) EntrySettings. ValueError, ha
        a motor validatora elutasitja."""
        values = self.full(assignment)
        if self.engine == "breakout":
            from .. import lab

            editable = {k: v for k, v in values.items() if k in lab.EDITABLE}
            base = EntrySettings(**{k: v for k, v in values.items() if k not in lab.EDITABLE})
            return lab.validate_entry(editable, base)
        return PullbackParams(**values)

    def feature_key(self, assignment: dict) -> tuple:
        if self.engine == "breakout":
            return ()
        values = self.full(assignment)
        return (values.get("trend_sma"), values.get("atr_period"), values.get("exit_sma"))

    def changes(self, assignment: dict) -> dict:
        """Mezo -> [preset, valtozat] ott, ahol elter."""
        preset = self.preset_assignment()
        return {k: [preset.get(k), v] for k, v in sorted(assignment.items()) if preset.get(k) != v}

    def neighbours(self, assignment: dict) -> list[tuple[str, object, dict]]:
        """Egylepeses szomszedok: mezonkent az ertekkeszlet szomszedos elemei (mezo, uj ertek, valtozat)."""
        out = []
        for d in self.dims:
            values = list(d.values)
            i = values.index(assignment[d.name]) if assignment[d.name] in values else None
            if i is None:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(values):
                    out.append((d.name, values[j], dict(assignment) | {d.name: values[j]}))
        return out


def _order(value):
    return (0, 0) if value is None else ((1, value) if not isinstance(value, str) else (2, value))


def _breakout_fields() -> set[str]:
    from .. import lab

    return set(lab.EDITABLE)
