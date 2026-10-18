"""Reggeli osszefoglalo - mondatok a sablonokbol.

Minden sor: sablonkulcs + argumentumok + a kesz (magyar) szoveg. A szamok az esemeny ertekeibol
formazodnak (a sablonban szamjegy nincs), igy a teszt soronkent ellenorizheti, hogy a szoveg minden szama
a forrasadatbol jon. A forditas (en / de / es) a kiszallitaskor tortenik, a katalogusbol (i18n.translate).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .detect import Event
from .templates import DIRECTIONS, STRATEGY_NAMES, TEMPLATES

_SLOT = re.compile(r"\{(\d+)(?::n)?\}")


@dataclass
class Line:
    key: str
    args: list[str]
    numeric: list[int] = field(default_factory=list)      # mely argumentumok szamok
    type: str = ""
    symbol: str | None = None
    score: float = 0.0

    @property
    def text(self) -> str:
        return fill(TEMPLATES[self.key], self.args)

    @property
    def strings(self) -> list[str]:
        return [a for i, a in enumerate(self.args) if i not in self.numeric]

    @property
    def numbers(self) -> list[str]:
        return [a for i, a in enumerate(self.args) if i in self.numeric]

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.args, "numeric": self.numeric, "type": self.type,
                "symbol": self.symbol, "score": self.score, "text": self.text}


def fill(template: str, args: list[str]) -> str:
    return _SLOT.sub(lambda m: args[int(m.group(1))], template)


def price(v) -> str:
    return f"{float(v):.2f}"


def one(v) -> str:
    return f"{float(v):.1f}"


def whole(v) -> str:
    return f"{int(v)}"


def _symbol(ev: Event) -> str:
    return fill(TEMPLATES["holding"], [ev.symbol]) if ev.holding else ev.symbol


def event_line(ev: Event) -> Line:
    v = ev.values
    sym = _symbol(ev) if ev.symbol else v.get("label", "")

    def line(key: str, args: list, numeric: tuple = ()) -> Line:
        return Line(key, [str(a) for a in args], list(numeric), ev.type, ev.symbol, ev.score)

    if ev.type == "regime_change":
        return line("regime_up" if v["up"] else "regime_down",
                    [sym, price(v["close"]), whole(v["period"]), price(v["sma"])], (1, 2, 3))
    if ev.type == "own_trend":
        return line("own_trend_up" if v["up"] else "own_trend_down",
                    [sym, price(v["close"]), whole(v["period"]), price(v["sma"])], (1, 2, 3))
    if ev.type == "pattern_triggered":
        return line("pattern_triggered", [sym, v["name"], price(v["trigger"])], (2,))
    if ev.type == "pattern_new":
        return line("pattern_new", [sym, v["name"], DIRECTIONS.get(v["direction"], v["direction"])])
    if ev.type == "pattern_gone":
        return line("pattern_gone", [sym, v["name"]])
    if ev.type == "near_trigger":
        if v.get("earnings_days") is not None:
            return line("near_trigger_earnings", [sym, one(v["dist_atr"]), v["name"], price(v["trigger"]),
                                                  whole(v["earnings_days"])], (1, 3, 4))
        return line("near_trigger", [sym, one(v["dist_atr"]), v["name"], price(v["trigger"])], (1, 3))
    if ev.type == "level_break":
        return line("level_break", [sym, price(v["close"]), price(v["level"])], (1, 2))
    if ev.type == "level_lost":
        return line("level_lost", [sym, price(v["close"]), price(v["level"])], (1, 2))
    if ev.type == "earnings_soon":
        if int(v["days"]) == 0:
            return line("earnings_today", [sym, v["date"]])
        return line("earnings_soon", [sym, whole(v["days"]), v["date"]], (1,))
    if ev.type == "filing_8k":
        items, form = ", ".join(v.get("items") or []), v.get("form") or "8-K"
        if not items:
            return line("filing_8k_plain", [sym, form])
        return line("filing_8k_material" if v.get("material") else "filing_8k", [sym, form, items])
    if ev.type == "sentiment_flip":
        return line("sentiment_pos" if v["up"] else "sentiment_neg",
                    [sym, whole(v["days"]), f"{float(v['mean']):.2f}"], (1, 2))
    if ev.type == "material_news":
        from ..sentiment.service import EVENT_LABELS

        return line("material_news", [sym, EVENT_LABELS.get(v.get("event"), v.get("event") or "-")])
    if ev.type in ("candidate_in", "candidate_out"):
        return line(ev.type, [sym, STRATEGY_NAMES.get(v["strategy"], v["strategy"])])
    raise ValueError(f"Ismeretlen esemenytipus: {ev.type}")


def render(record: dict) -> list[Line]:
    """Az osszefoglalo sorai (fejlec, piac, papirok, osszevont sor, figyelmeztetes)."""
    sel = record.get("selected") or {}
    lines = [Line("header", [record["for_day"], record["as_of"] or "-"], type="header")]
    if not record.get("symbols_count"):
        lines.append(Line("no_symbols", [], type="info"))
    elif record.get("first"):
        lines.append(Line("first_run", [], type="info"))
    agent = record.get("agent")
    if agent:
        lines.append(Line("agent_report", [agent["brief_name"], whole(agent["finalists"]), whole(agent["tried"])],
                          [1, 2], type="agent"))
    events = [Event.from_dict(e) for e in (sel.get("market") or []) + (sel.get("items") or [])]
    lines += [event_line(ev) for ev in events]
    if sel.get("omitted"):
        lines.append(Line("more", [whole(sel["omitted"])], [0], type="info"))
    if sel.get("unchanged"):
        lines.append(Line("unchanged", [", ".join(sel["unchanged"])], type="unchanged"))
    if record.get("symbols_count") and not record.get("first") and not events:
        lines.append(Line("quiet", [], type="info"))
    lines.append(Line("disclaimer", [], type="disclaimer"))
    return lines


def to_text(lines: list[Line]) -> str:
    head, body = lines[0].text, [ln.text for ln in lines[1:]]
    return "\n".join([head, ""] + body)
