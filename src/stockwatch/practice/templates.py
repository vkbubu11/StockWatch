"""A gyakorlo kor osszegzesenek mondatsablonjai (i18n katalogus kulcsai; szamjegy a sablonban nincs, minden szam
argumentum a szamitasbol). Tenyt es szimulacios eredmenyt kozol, nem ajanl."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TEMPLATES = {
    "rounds": "{0:n} helyzet, ebből {1:n} belépési döntés és {2:n} kihagyás.",
    "unfilled": "{0:n} limitáras belépés nem teljesült.",
    "result": "A teljesült belépések: {0:n} kötés, nyerési arány {1:n}%, átlag {2:n} R, összesen {3:n} R.",
    "no_entries": "Nem volt teljesült belépés.",
    "baseline": "Véletlen alapvonal ugyanazokon a papírokon (azonos stop-távolság és időkorlát, véletlen napokon): átlag {0:n} R.",
    "reference": "Referencia-kötés minden helyzetben (következő nyitás, {0:n} ATR stop, {1:n} nap): átlag {2:n} R.",
    "strategy": "{0}: {1:n} helyzetben jelzett; a szabály szerinti lezárt kötések ({2:n}) átlaga {3:n} R.",
    "strategy_none": "{0}: {1:n} helyzetben jelzett, de lezárt szabály szerinti kötés nem volt (nem teljesült limit, szűrő, vagy a labor még nem értékelte).",
    "missed": "{0:n} kihagyott helyzetben a szabály vagy a referencia-kötés legalább +{1:n} R lett volna.",
    "stop_median": "A stop-távolság mediánja {0:n} ATR; a stopok {1:n}%-a {2:n} ATR-nél szorosabb.",
    "few": "Kevés kötés ({0:n}): az eredmény főleg zaj.",
    "x_t_context": "Mit lehetett látni a döntéskor",
    "x_t_rules": "A szabályok a döntés pillanatában",
    "x_t_trade": "A te kötésed lépésről lépésre",
    "x_t_alts": "Az alternatívák ugyanígy kiszámolva",
    "x_t_best": "Mi lett volna a jó döntés",
    "x_value": "{0}: {1:n}.",
    "x_trend": "Trendkép: {0}.",
    "x_regime": "Piaci rezsim ({0}): {1}.",
    "x_earn": "Következő gyorsjelentés: {0:n} nap múlva.",
    "x_earn_none": "A következő gyorsjelentés dátuma nem ismert.",
    "x_rules_none": "Egyik szabály sem jelzett ezen a ponton.",
    "x_rules_some": "Jelzett szabály: {0}",   # pont nelkul: a felsorolas a forditasban tagolhato
    "x_rules_choice": "A szabályok szerinti döntés: {0}.",
    "x_breakout_note": "A Kitörés szűrőit a labor értékeli; itt csak az eredménye látszik.",
    "x_short_note": "A short motor kísérleti: backtesttel nincs igazolva.",
    "x_skip": "Kihagytad: nem volt kötés, az eredmény {0:n} R.",
    "x_entry_open": "Belépés ({0}) a következő nyitáson, a(z) {1:n}. napon: {2:n}.",
    "x_entry_limit": "Limit ({0}) {1:n}: teljesült a(z) {2:n}. napon, {3:n} áron.",
    "x_unfilled": "A limit ({0:n}) nem teljesült a következő napon, így nem volt kötés (eredmény: {1:n} R).",
    "x_risk": "Kockázat (egy R): |belépő − stop| = |{0:n} − {1:n}| = {2:n}, azaz {3:n} ATR.",
    "x_risk_gap": "A nyitó már a stopon túl volt, ezért a kockázat a tervezett belépőből számol: |{0:n} − {1:n}| = {2:n}.",
    "x_exit_stop_long": "A(z) {0:n}. napon a napi minimum ({1:n}) elérte a stopot ({2:n}): kilépés a stopon.",
    "x_exit_stop_short": "A(z) {0:n}. napon a napi maximum ({1:n}) elérte a stopot ({2:n}): kilépés a stopon.",
    "x_exit_gap": "A(z) {0:n}. napon a nyitó ({1:n}) már a stopon ({2:n}) túl volt: kilépés a nyitóáron.",
    "x_exit_target": "A(z) {0:n}. napon az ár elérte a célt ({1:n}): kilépés {2:n} áron.",
    "x_exit_time": "Az időkorlát ({0:n} nap) lejárt: kilépés a(z) {1:n}. napi nyitón, {2:n} áron.",
    "x_exit_signal": "A(z) {0:n}. napon kilépési jel: kilépés a következő nyitón, {1:n} áron.",
    "x_open": "A kötés a vizsgált ablak végéig nyitva maradt, ezért nincs lezárt eredmény.",
    "x_same_bar": "Ugyanazon a gyertyán a stop és a cél is elérhető volt; a szimuláció pesszimista, ezért a stop teljesült.",
    "x_gross_long": "Bruttó eredmény: kilépő − belépő = {0:n} − {1:n} = {2:n}.",
    "x_gross_short": "Bruttó eredmény: belépő − kilépő = {0:n} − {1:n} = {2:n}.",
    "x_cost": "Költség: oldalanként {0:n} bps, a belépő és a kilépő összegére: {1:n}.",
    "x_borrow": "Kölcsönzési díj: {0:n} nap × {1:n} bps a belépőre: {2:n}.",
    "x_net": "Nettó eredmény: {0:n} − {1:n} − {2:n} = {3:n}.",
    "x_r": "R = nettó eredmény / kockázat = {0:n} / {1:n} = {2:n} R.",
    "x_alt_params": "Azonos feltételek: következő nyitás, {0:n} ATR stop, {1}, {2:n} nap időkorlát ({3}).",
    "x_alt_row": "{0}: {1:n} R ({2}, {3:n} nap).",
    "x_alt_skip": "Kihagyás: {0:n} R.",
    "x_alt_none": "{0}: nincs lezárt kötés.",
    "x_strat_row": "{0} (szabály szerint): {1:n} R.",
    "x_strat_status": "{0} (szabály szerint): {1}.",
    "x_best": "Utólag a legjobb lehetőség: {0}, {1:n} R.",
    "x_why_trend_up": "A döntéskor a trend emelkedő volt, ez a long oldalnak kedvezett.",
    "x_why_trend_down": "A döntéskor a trend csökkenő volt, ez a short oldalnak kedvezett.",
    "x_why_trend_mixed": "A döntéskor a trend vegyes volt: egyik irányt sem támasztotta alá egyértelműen.",
    "x_why_rules": "A szabályok szerinti döntés: {0}; a te döntésed: {1}.",
    "x_v_good": "Jó döntés: a szabályok szerint is ez volt indokolt, és a kimenet is igazolta.",
    "x_v_lucky": "Szerencsés: a kimenet jó lett, de a döntés pillanatában ismert szabályok nem támasztották alá.",
    "x_v_unlucky": "Balszerencsés: a szabályok szerint indokolt volt, a kimenet mégis rossz lett – egy jó döntés is veszíthet.",
    "x_v_bad": "Rossz döntés: sem a szabályok, sem a kimenet nem igazolta.",
    "x_flag_tight": "A stop szoros volt: {0:n} ATR (a jelzési küszöb {1:n} ATR).",
    "x_flag_rr": "A cél és a kockázat aránya {0:n} volt (a jelzési küszöb {1:n}).",
    "x_flag_unfilled": "A limit nem teljesült, ezért nem volt kötés.",
    "x_better": "Utólagos összevetés (előre rögzített rács: {0}): {1:n} ATR-es stoppal az eredmény {2:n} R lett volna {3:n} R helyett. Ez utólagos, nem szabály.",
    "verdicts": "Ítéletek: {0:n} jó döntés, {1:n} szerencsés, {2:n} balszerencsés, {3:n} rossz döntés.",
    "sides": "Long: {0:n} teljesült kötés, átlag {1:n} R; short: {2:n} teljesült kötés, átlag {3:n} R.",
    "disclaimer": "Nem befektetési tanács. Gyakorlás múltbeli adaton, szimulált kötésekkel.",
}

STRATEGY_LABELS = {"pullback": "Visszahúzás (RSI2)", "deep": "Mély visszahúzás", "breakout": "Kitörés",
                   "short": "Short (kísérleti)"}


@dataclass
class Line:
    """Egy sablonos mondat: kulcs + argumentumok; a `numeric` argumentumok szamok (a szamitasbol)."""

    key: str
    args: list[str] = field(default_factory=list)
    numeric: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return re.sub(r"\{(\d+)(?::n)?\}", lambda m: self.args[int(m.group(1))], TEMPLATES[self.key])

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.args, "numeric": self.numeric, "text": self.text}
