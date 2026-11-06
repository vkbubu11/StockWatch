"""A hirhangulat validalasa (#20, 2. fazis): javitja-e a meglevo strategiakat - ELORE rogzitett hipotezisekkel es kapuval.

Hipotezisek (docs/SENTIMENT-DESIGN.md 2. fejezet, utolag nem valtoztatjuk):
  H1  Visszahuzas / Mely visszahuzas: a lenyeges negativ hir (3 napon belul) okozta tuladottsag rosszabbul fordul
      vissza -> szuro: kihagyas, ha `material_neg_3d`; masodik valtozat: kihagyas, ha `neg_share_3d >= 0,5` es
      `news_volume_z >= 2`.
  H2  Kitores: a pozitiv hirrel es nagy hirvolumennel jaro kitores gyakrabban folytatodik -> szuro: csak ha
      `sent_mean_3d > 0` es `news_volume_z >= 1`; ellenproba: kihagyas, ha `material_neg_3d`.

Point-in-time: a `t` napi jelzes csak a `t` nap 16:00 (New York) elott megjelent hireket latja (features.daily).
Csak a hirforrassal LEFEDETT jelzesnapok szamitanak (a lefedetlen "nem ismert", nem "nincs negativ hir").

Kapu (a #20 elfogadasi feltetele): a szuro a tanito ES a validacios idoszakban is >= +2 szazalekponttal emeli a
nyeresi aranyt, a kotesek legfeljebb 25%-at ejti ki, es nem rontja a varhato erteket. A holdout zarva marad.
A hatas a hirsuruseg szerint is bontva latszik (a nagy cegekrol tobb a hir - lefedettsegi torzitas).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..entry_research import HOLDOUT_START, SPLIT_DATE, wilson
from . import features, store

GATE = {"win_pp": 2.0, "max_drop": 0.25}
H1 = (("H1a: kihagyas lenyeges negativ hirnel", lambda f: f["material_neg_3d"]),
      ("H1b: kihagyas, ha negativ arany >= 50% es hirvolumen z >= 2",
       lambda f: (f["neg_share_3d"] or 0) >= 0.5 and (f["news_volume_z"] or 0) >= 2))
H2 = (("H2a: csak pozitiv hangulat es hirvolumen z >= 1",
       lambda f: not ((f["sent_mean_3d"] or 0) > 0 and (f["news_volume_z"] or 0) >= 1)),
      ("H2b (ellenproba): kihagyas lenyeges negativ hirnel", lambda f: f["material_neg_3d"]))


def annotate(trades: list[tuple[str, pd.Timestamp, float]]) -> list[dict]:
    """(papir, jelzesnap, eredmeny) -> a jelzesnapi hirjellemzokkel; a lefedetlen nap kimarad (coverage=False)."""
    by_symbol: dict[str, list] = {}
    for symbol, day, value in trades:
        by_symbol.setdefault(symbol, []).append((day, value))
    out = []
    for symbol, rows in by_symbol.items():
        arts, coverage = store.articles(symbol), store.load(symbol).get("coverage") or {}
        density = len([a for a in arts if a.kind == "news"])
        for day, value in rows:
            f = features.daily(arts, day, coverage)
            out.append({"symbol": symbol, "day": pd.Timestamp(day), "value": float(value), "density": density} | f)
    return out


def _stats(rows: list[dict]) -> dict:
    v = np.array([r["value"] for r in rows], dtype=float)
    if not len(v):
        return {"n": 0, "win": None, "ev": None}
    lo, hi = wilson(int((v > 0).sum()), len(v))
    return {"n": len(v), "win": float((v > 0).mean()), "win_lo": lo, "win_hi": hi, "ev": float(v.mean())}


def _period(rows: list[dict], name: str) -> list[dict]:
    if name == "train":
        return [r for r in rows if r["day"] < SPLIT_DATE]
    return [r for r in rows if SPLIT_DATE <= r["day"] < HOLDOUT_START]


def evaluate(rows: list[dict], drop) -> dict:
    """Egy elore rogzitett szuro (`drop(f)` igaz: kihagyjuk) a lefedett jelzeseken, idoszakonkent + kapu."""
    covered = [r for r in rows if r["coverage"] and r["day"] < HOLDOUT_START]
    out = {"periods": {}, "checks": []}
    for name in ("train", "test"):
        base = _period(covered, name)
        kept = [r for r in base if not drop(r)]
        dropped = [r for r in base if drop(r)]
        b, k, d = _stats(base), _stats(kept), _stats(dropped)
        share = len(dropped) / len(base) if base else None
        out["periods"][name] = {"base": b, "kept": k, "dropped": d, "drop_share": share}
        win_pp = (k["win"] - b["win"]) * 100 if k["win"] is not None and b["win"] is not None else None
        out["checks"] += [
            (f"{name}: nyeresi arany >= +{GATE['win_pp']:g} pp", win_pp is not None and win_pp >= GATE["win_pp"]),
            (f"{name}: legfeljebb {GATE['max_drop']:.0%} kiesik", share is not None and share <= GATE["max_drop"]),
            (f"{name}: a varhato ertek nem romlik", k["ev"] is not None and b["ev"] is not None and k["ev"] >= b["ev"]),
        ]
    # lefedettsegi torzitas: a hirsuruseg felso es also fele kulon (a teljes lathato idoszakon)
    if covered:
        median = float(np.median([r["density"] for r in covered]))
        out["density"] = {part: {"base": _stats(sel), "kept": _stats([r for r in sel if not drop(r)])}
                          for part, sel in (("sok hir", [r for r in covered if r["density"] >= median]),
                                            ("keves hir", [r for r in covered if r["density"] < median]))}
    out["passed"] = all(ok for _, ok in out["checks"])
    return out


def run(groups: dict[str, list[tuple[str, pd.Timestamp, float]]]) -> dict:
    """`groups`: strategia -> a jelzesei (papir, nap, eredmeny). A Kitores H2-t, a tobbi H1-et kapja."""
    report = {}
    for name, trades in groups.items():
        rows = annotate(trades)
        covered = [r for r in rows if r["coverage"]]
        tests = H2 if name == "Kitores" else H1
        report[name] = {"signals": len(rows), "covered": len(covered),
                        "first_covered": min((r["day"] for r in covered), default=None),
                        "variants": {label: evaluate(rows, drop) for label, drop in tests}}
    return report


def _pct(v, d=1) -> str:
    return "-" if v is None else f"{v * 100:.{d}f}%"


def report_markdown(report: dict, unit: dict) -> str:
    lines = ["| strategia / valtozat | idoszak | alap n | alap nyeres | megtartott nyeres | kiesik | alap EV | megtartott EV | kapu |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, r in report.items():
        u = unit.get(name, "")
        for label, v in r["variants"].items():
            for period in ("train", "test"):
                p = v["periods"][period]
                ev = lambda s: "-" if s["ev"] is None else f"{s['ev']:+.2f}{u}"   # noqa: E731
                lines.append(f"| {name}: {label} | {'tanito' if period == 'train' else 'validacio'} | {p['base']['n']} | "
                             f"{_pct(p['base']['win'])} | {_pct(p['kept']['win'])} | {_pct(p['drop_share'], 0)} | "
                             f"{ev(p['base'])} | {ev(p['kept'])} | {'ATMENT' if v['passed'] else 'nem'} |")
    return "\n".join(lines)
