"""Hir-pontozas ket fuggetlen reteggel (a stocksight "egyetertes" elve alapjan, penzugyi szoveghez igazitva).

  A reteg  VADER (altalanos hangulat-lexikon, `vaderSentiment` csomag). Ha nincs telepitve: None.
  B reteg  sajat penzugyi szolista + esemenyfelismeres (leminosites, elorejelzes-csokkentes,
           kibocsatas, per / vizsgalat, FDA, csod, konyvelesi helyesbites...). Az esemeny
           "lenyegessege" 0-3.
  Bejelentes (SEC 8-K): a tetelkodokbol (pl. 4.02 = a korabbi beszamolok nem megbizhatok).

Cimke: negativ, ha a ket reteg egyetert (A <= -0.05 es B < 0), vagy A semleges es B erosen negativ
(<= -0.5), VAGY lenyeges (>= 2) negativ esemeny van; pozitiv szimmetrikusan; ellentmondasnal semleges. Bizonytalan -> semleges. Kerdo cim ("Csodbe megy az X?")
eseten a lenyegesseg eggyel kisebb (kattintasvadasz cimek).

A pontszam mindig a MODEL_VERSION-nel egyutt tarolodik; ha a verzio valtozik, ujraszamoljuk.
"""
from __future__ import annotations

import re
from functools import lru_cache

MODEL_VERSION = "lex-5"              # a szolista / tetelsulyok valtozasakor noveld (ujrapontozas)

# (nev, regex, irany, lenyegesseg) - az elso talalat szamit a legfontosabbnak (rendezve)
EVENTS: list[tuple[str, re.Pattern, int, int]] = [(n, re.compile(p, re.I), d, m) for n, p, d, m in [
    ("csod", r"\b(chapter 11|bankrupt\w*|insolven\w*)", -1, 3),
    ("going_concern", r"going concern", -1, 3),
    ("helyesbites", r"\b(restat(e|es|ed|ement)\w*|accounting (irregularit\w*|error\w*))", -1, 3),
    ("kivezetes", r"\bdelist\w*", -1, 3),
    ("fda_negativ", r"\b(complete response letter|fda (rejects|declines|refuses)|clinical hold|trial (fails|failed))", -1, 3),
    ("short_jelentes", r"\b(short[- ]seller|short report|fraud)\b", -1, 2),
    ("elorejelzes_csokkentes", r"\b(cut|cuts|lower|lowers|lowered|slash\w*|trim\w*|reduc\w*|withdraw\w*)\b[^.]{0,40}"
                               r"\b(guidance|forecast|outlook)", -1, 2),
    ("elorejelzes_emeles", r"\b(raise[sd]?|lift\w*|boost\w*|hike[sd]?)\b[^.]{0,40}\b(guidance|forecast|outlook)", 1, 2),
    ("leminosites", r"\bdowngrad\w*", -1, 2),
    ("felminosites", r"\bupgrad\w*\b[^.]{0,30}\b(to (buy|outperform|overweight))", 1, 1),
    ("eredmeny_elmarad", r"\b(miss(es|ed)?|falls? short|below (estimates|expectations))\b", -1, 2),
    ("eredmeny_felulmul", r"\b(beats?|tops?|surpass\w*|exceed\w*)\b[^.]{0,30}\b(estimates|expectations|views|forecasts?)", 1, 1),
    ("kibocsatas", r"\b(public offering|secondary offering|stock offering|share offering|equity offering|"
                   r"prices? (an? )?offering|at-the-market offering|share sale)", -1, 2),
    ("osztalek_csokkentes", r"\b(cut\w*|suspend\w*|slash\w*|eliminat\w*)\b[^.]{0,20}\bdividend", -1, 2),
    ("per_vizsgalat", r"\b(lawsuit|sued|sues|class action|probe|investigation|subpoena|antitrust|indicted|charged)\b", -1, 2),
    ("visszahivas", r"\brecall(s|ed)?\b", -1, 2),
    ("vezeto_tavozik", r"\b(ceo|cfo|chief executive|chief financial)\b[^.]{0,40}\b(resign\w*|steps? down|depart\w*|ousted|fired|exits?)", -1, 2),
    ("fda_pozitiv", r"\bfda (approv\w*|clears|grants)", 1, 2),
    ("leepites", r"\b(layoffs?|lay off|job cuts|restructuring)\b", -1, 1),
    ("felvasarlas", r"\b(to acquire|acquisition of|agrees? to buy|merger|buyout|takeover|to be acquired)\b", 0, 1),
]]

# B reteg: penzugyi szolista (szo -> ertek). Szandekosan rovid es atlathato.
FIN_WORDS = {
    **dict.fromkeys(["plunge", "plunges", "plunged", "crash", "crashes", "tumble", "tumbles", "tumbled", "bankruptcy",
                     "fraud", "default", "defaults", "collapse", "collapses", "plummet", "plummets", "plummeted",
                     "nosedive", "nosedives", "nosediving", "tank", "tanks", "tanking", "tanked"], -2.0),
    **dict.fromkeys(["loss", "losses", "decline", "declines", "declined", "slump", "slumps", "sink", "sinks", "drop",
                     "drops", "dropped", "fall", "falls", "fell", "weak", "weaker", "warning", "warns", "warned",
                     "concern", "concerns", "lawsuit", "probe", "downgrade", "downgraded", "miss", "misses", "missed",
                     "layoffs", "recall", "halt", "halts", "delay", "delays", "shortfall", "impairment", "writedown",
                     "write-down", "dilution", "slide", "slides", "sliding", "selloff", "sell-off", "bearish", "slowdown", "headwinds", "cut",
                     "cuts", "slashes", "lowers", "disappoint", "disappoints", "disappointing", "underperform"], -1.0),
    **dict.fromkeys(["gain", "gains", "rise", "rises", "rose", "beat", "beats", "record", "strong", "stronger",
                     "growth", "upgrade", "upgraded", "buyback", "outperform", "bullish", "approval", "approved",
                     "wins", "profit", "profitable", "expands", "tailwinds", "raises", "boosts", "tops"], 1.0),
    **dict.fromkeys(["surge", "surges", "surged", "soar", "soars", "soared", "jump", "jumps", "jumped", "rally",
                     "rallies", "skyrocket", "skyrockets"], 2.0),
}

# 8-K tetelkodok: (esemeny, irany, lenyegesseg). A tobbi tetel (pl. 2.02 eredmeny, 9.01 mellekletek) semleges.
EDGAR_ITEMS = {
    "1.03": ("csod", -1, 3), "2.04": ("hitel_gyorsitas", -1, 3), "2.06": ("ertekvesztes", -1, 2),
    "3.01": ("kivezetesi_ertesites", -1, 3), "4.01": ("konyvvizsgalo_csere", -1, 2),
    "4.02": ("helyesbites", -1, 3), "2.05": ("leepites_koltseg", -1, 1), "3.02": ("hig_kibocsatas", -1, 1),
    "5.02": ("vezeto_valtas", -1, 1), "1.01": ("szerzodes", 0, 1), "2.01": ("felvasarlas", 0, 1),
    "2.02": ("eredmeny", 0, 0), "1.05": ("kiberbiztonsagi_incidens", -1, 2),
}
EDGAR_FORMS = {"424B4": ("kibocsatas", -1, 2), "424B5": ("kibocsatas", -1, 1)}


@lru_cache(maxsize=1)
def _vader():
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        return SentimentIntensityAnalyzer()
    except Exception:                                     # nincs telepitve -> csak a B reteg
        return None


def _fin_score(text: str) -> float:
    words = re.findall(r"[a-z][a-z\-]+", text.lower())
    vals = [FIN_WORDS[w] for w in words if w in FIN_WORDS]
    return max(-1.0, min(1.0, sum(vals) / 3.0)) if vals else 0.0


def _event(text: str) -> tuple[str | None, int, int]:
    best = (None, 0, 0)
    for name, rx, direction, materiality in EVENTS:
        if rx.search(text) and materiality > best[2]:
            best = (name, direction, materiality)
    return best


def _strip(text: str, terms: list[str]) -> str:
    """A cegnev / ticker kivetele: a "Super Micro" ne legyen pozitiv, az "Ally" ne legyen "baratsagos"."""
    for term in sorted((t for t in terms if t), key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", " ", text, flags=re.I)
    return text


def score_text(title: str, summary: str = "", strip: list[str] | None = None) -> dict:
    text = _strip(f"{title}. {summary}".strip(". "), strip or [])
    analyzer = _vader()
    a = analyzer.polarity_scores(text)["compound"] if analyzer is not None else None
    b = _fin_score(text)
    event, direction, materiality = _event(text)
    if title.strip().endswith("?"):
        materiality = max(0, materiality - 1)
    if event and direction:
        b = max(-1.0, min(1.0, b + 0.5 * direction))
    material = materiality >= 2 and direction != 0
    # a VADER nem ismeri a penzugyi szavakat ("nosediving"): ha o semleges, az eros B reteg dont;
    # ha a ket reteg ellentmond egymasnak, a cim semleges marad
    a_neutral = a is None or abs(a) < 0.05
    if (material and direction < 0) or (b < 0 and ((a is not None and a <= -0.05) or (a_neutral and b <= -0.5))):
        label = "negative"
    elif (material and direction > 0) or (b > 0 and ((a is not None and a >= 0.05) or (a_neutral and b >= 0.5))):
        label = "positive"
    else:
        label = "neutral"
    polarity = b if a is None else (a + b) / 2
    return {"version": MODEL_VERSION, "vader": None if a is None else round(a, 3), "fin": round(b, 3),
            "polarity": round(polarity, 3), "label": label, "event": event, "direction": direction,
            "materiality": materiality}


def score_filing(form: str, items: list[str]) -> dict:
    best = (None, 0, 0)
    for item in items:
        hit = EDGAR_ITEMS.get(item)
        if hit and hit[2] > best[2]:
            best = hit
    hit = EDGAR_FORMS.get(form)
    if hit and hit[2] > best[2]:
        best = hit
    event, direction, materiality = best
    label = "negative" if direction < 0 and materiality >= 2 else "neutral"
    return {"version": MODEL_VERSION, "vader": None, "fin": float(direction) if materiality >= 2 else 0.0,
            "polarity": float(direction) * min(1.0, materiality / 3) if direction else 0.0, "label": label,
            "event": event, "direction": direction, "materiality": materiality}


def score(article, company: str | None = None) -> dict:
    if article.kind == "filing":
        return score_filing(article.form, article.items)
    words = (company or "").split()
    variants = [company or "", " ".join(words[:2]), words[0] if words and len(words[0]) >= 4 else ""]
    return score_text(article.title, article.summary, strip=[article.symbol, *variants])
