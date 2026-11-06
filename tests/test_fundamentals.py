"""Point-in-time fundamentalis adat (#72): egy negyedev adata a beadas napjan meg nem, utana mar latszik; a javitott
ertek csak a javitas beadasa utan; a 4. negyedev az eves ertekbol; ev/ev novekedes, gyorsulas; az EPS-szuro alapbol ki,
adat nelkul "nem ismert"; a study-sorok a meglevo formaban."""
from __future__ import annotations

import gzip
import json
from datetime import date

import pandas as pd

from stockwatch import entry, entry_research, fundamentals
from stockwatch.config import EntrySettings


def _fact(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _facts() -> dict:
    eps = [
        # FY2023 (naptari ev): Q1-Q3 10-Q, eves 10-K
        _fact("2023-01-01", "2023-03-31", 1.00, "2023-05-02"),
        _fact("2023-04-01", "2023-06-30", 1.10, "2023-08-01"),
        _fact("2023-07-01", "2023-09-30", 1.20, "2023-11-01"),
        _fact("2023-01-01", "2023-12-31", 4.60, "2024-02-15", "10-K"),
        # FY2024: novekvo, gyorsulo
        _fact("2024-01-01", "2024-03-31", 1.15, "2024-05-02"),             # +15%
        _fact("2024-04-01", "2024-06-30", 1.43, "2024-08-01"),             # +30%
        _fact("2024-07-01", "2024-09-30", 1.80, "2024-11-01"),             # +50%
        _fact("2024-07-01", "2024-09-30", 1.70, "2025-01-20", "10-Q/A"),   # javitas kesobb
        _fact("2024-01-01", "2024-06-30", 2.58, "2024-08-01"),             # 6 honapos: nem negyedev
    ]
    rev = [_fact("2023-01-01", "2023-03-31", 100.0, "2023-05-02"), _fact("2024-01-01", "2024-03-31", 125.0, "2024-05-02")]
    old_concept = [_fact("2022-01-01", "2022-03-31", 90.0, "2022-05-02")]        # korabbi fogalom (cegek valtanak)
    return {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
        "SalesRevenueNet": {"units": {"USD": old_concept + [_fact("2023-01-01", "2023-03-31", 999.0, "2023-05-02")]}},
    }}}


def test_quarter_is_invisible_on_its_filing_day_and_visible_the_day_after():
    data = fundamentals.extract(_facts())
    on_day = fundamentals.quarters(data["eps"], date(2024, 11, 1))
    after = fundamentals.quarters(data["eps"], date(2024, 11, 2))
    assert "2024-09-30" not in on_day and after["2024-09-30"] == (1.80, False)
    assert fundamentals.quarters(data["eps"], date(2025, 1, 21))["2024-09-30"] == (1.70, False)   # a javitas utan


def test_q4_is_derived_from_the_annual_report_and_concepts_merge():
    data = fundamentals.extract(_facts())
    q = fundamentals.quarters(data["eps"], date(2024, 2, 15))
    assert "2023-12-31" not in q                                                       # a 10-K beadasa napjan meg nem
    q = fundamentals.quarters(data["eps"], date(2024, 2, 16))
    assert q["2023-12-31"][1] is True and abs(q["2023-12-31"][0] - (4.60 - 3.30)) < 1e-9
    rev = fundamentals.quarters(data["revenue"], date(2025, 1, 1))
    assert rev["2023-03-31"] == (100.0, False) and rev["2022-03-31"] == (90.0, False)  # elso fogalom elsobbsege
    assert len(data["eps"]) == 8                                                       # a 6 honapos kimarad


def test_growth_acceleration_and_streak():
    f = fundamentals.as_of("TEST", date(2024, 11, 2), fundamentals.extract(_facts()))
    assert f["last_quarter"] == "2024-09-30" and f["eps_yoy"] == 50.0
    assert [q["eps_yoy"] for q in f["quarters"] if q["end"] >= "2024-01-01"] == [15.0, 30.0, 50.0]
    assert f["eps_accelerating"] and f["eps_positive_streak"] == 3 and f["revenue_yoy"] == 25.0
    later = fundamentals.as_of("TEST", date(2025, 1, 21), fundamentals.extract(_facts()))
    assert later["eps_yoy"] == round((1.70 - 1.20) / 1.20 * 100, 1)                  # a javitott ertekkel
    assert fundamentals.as_of("TEST", date(2025, 9, 1), fundamentals.extract(_facts())) is None   # elavult
    assert fundamentals.as_of("TEST", date(2023, 1, 1), fundamentals.extract(_facts())) is None   # meg semmi sem ismert


def _store(symbol: str) -> None:
    fundamentals.DIR.mkdir(parents=True, exist_ok=True)
    data = {"symbol": symbol, "fetched": "2026-01-01T00:00:00+00:00"} | fundamentals.extract(_facts())
    (fundamentals.DIR / f"{symbol}.json.gz").write_bytes(gzip.compress(json.dumps(data).encode()))


def test_eps_filter_is_off_by_default_and_unknown_without_data():
    cfg = EntrySettings()
    assert cfg.min_eps_growth_pct is None and not cfg.require_eps_acceleration
    _store("TEST")
    on = cfg.model_copy(update={"min_eps_growth_pct": 20.0, "require_eps_acceleration": True})
    checks = entry.fundamental_checks("TEST", pd.Timestamp("2024-11-04"), on)
    assert [(c.name, c.passed) for c in checks] == [("EPS-novekedes", True), ("EPS gyorsul", True)]
    early = entry.fundamental_checks("TEST", pd.Timestamp("2024-08-05"), on)        # +30%, meg csak 2 negyedev no
    assert early[0].passed is True and early[1].passed is False
    assert all(c.passed is None for c in entry.fundamental_checks("NONE", pd.Timestamp("2024-11-04"), on))


def test_study_rows_in_the_existing_form():
    names = [v.name for v in entry_research.study_grid(EntrySettings())]
    assert {"+EPS-novekedes >= 20%", "+EPS gyorsul", "+EPS >= 20% es gyorsul"} <= set(names)


def test_eps_filter_runs_in_the_live_check_chain():
    """A jelzes teljes ellenorzese (entry.evaluate -> run_checks) is latja az EPS-szurot."""
    from .test_entry import _signal, enrich, lenient, uptrend

    df = enrich(uptrend())
    off = _signal(df, lenient())
    assert not any(c.name.startswith("EPS") for c in off.checks)                     # alapbol ki
    on = _signal(df, lenient().model_copy(update={"min_eps_growth_pct": 20.0}))
    eps = [c for c in on.checks if c.name == "EPS-novekedes"]
    assert len(eps) == 1 and eps[0].passed is None                                      # TEST-re nincs SEC-adat
