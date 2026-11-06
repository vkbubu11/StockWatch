"""A hirhangulat validalasa (#20, 2. fazis): a lefedetlen jelzesnap kimarad ("nem ismert"), a kapu a tanito ES a
validacios idoszakban is kell (>= +2 pp nyeres, <= 25% kieses, nem romlo EV), a holdout soha nem szamit."""
from __future__ import annotations

import pandas as pd

from stockwatch.sentiment import research


def _row(day: str, value: float, neg: bool, covered: bool = True) -> dict:
    return {"symbol": "X", "day": pd.Timestamp(day), "value": value, "density": 10, "coverage": covered,
            "material_neg_3d": neg, "neg_share_3d": None, "news_volume_z": None, "sent_mean_3d": None}


def _period(year: str, wins: int, losses: int, neg_losses: int) -> list[dict]:
    rows = [_row(f"{year}-03-01", 1.0, False) for _ in range(wins)]
    rows += [_row(f"{year}-03-01", -1.0, False) for _ in range(losses)]
    return rows + [_row(f"{year}-03-01", -1.0, True) for _ in range(neg_losses)]


def test_filter_that_drops_negative_news_losers_passes_the_gate():
    rows = _period("2019", 60, 30, 10) + _period("2023", 60, 30, 10)
    rows += [_row("2025-06-01", -5.0, False)] * 50                                  # holdout: nem szamit
    rows += [_row("2019-03-01", -9.0, False, covered=False)] * 50                   # lefedetlen: nem szamit
    v = research.evaluate(rows, lambda f: f["material_neg_3d"])
    tr = v["periods"]["train"]
    assert tr["base"]["n"] == 100 and tr["kept"]["n"] == 90 and tr["drop_share"] == 0.1
    assert round(tr["kept"]["win"] - tr["base"]["win"], 4) == round(60 / 90 - 0.6, 4)
    assert v["passed"]


def test_gate_fails_if_only_one_period_improves_or_too_much_is_dropped():
    rows = _period("2019", 60, 30, 10) + _period("2023", 60, 40, 0)
    assert not research.evaluate(rows, lambda f: f["material_neg_3d"])["passed"]    # a validacioban nincs hatas
    many = _period("2019", 50, 0, 50) + _period("2023", 50, 0, 50)
    v = research.evaluate(many, lambda f: f["material_neg_3d"])
    assert v["periods"]["train"]["drop_share"] == 0.5 and not v["passed"]           # tul sok kiesik


def test_report_lists_every_variant_and_period():
    rows = _period("2019", 6, 3, 1) + _period("2023", 6, 3, 1)
    report = {"Visszahuzas": {"signals": 20, "covered": 20, "first_covered": None,
                              "variants": {label: research.evaluate(rows, drop) for label, drop in research.H1}}}
    md = research.report_markdown(report, {"Visszahuzas": "%"})
    assert md.count("| Visszahuzas:") == 4 and "tanito" in md and "validacio" in md
