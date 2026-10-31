"""Esemenytanulmanyok (events/): ismert CAR szintetikus adaton, point-in-time (a 0. nap utani adat nem hat a
kivalasztasra es a becslesre), 0. nap-szabalyok, Form 4 parser fixturakon, SEC-kereskorlat, indexvaltozasok,
atfedes, bootstrap, teljes futas a holdouttal es nelkule, szovegezes."""
from __future__ import annotations

import gzip
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stockwatch import i18n, labqueue
from stockwatch import entry_research as er
from stockwatch.config import Settings
from stockwatch.events import catalog, service, store, study
from stockwatch.events.sources import form4, index_changes
from stockwatch.events.templates import TEMPLATES
from stockwatch.sentiment.sources import edgar

from . import agent_synthetic as syn
from .guards import advice_hits, unexplained_numbers

FIX = Path(__file__).parent / "fixtures" / "events"


def market_and_stock(n: int = 600, beta: float = 1.2, jump_at: int | None = 400, jump: float = 0.05, seed: int = 1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    rm = rng.normal(0.0004, 0.01, n)
    r = beta * rm + rng.normal(0, 0.002, n)
    if jump_at is not None:
        r[jump_at + 1] += jump                        # abnormalis ugras az 1. napon
    m = pd.Series(100 * np.cumprod(1 + rm), index=idx)
    s = pd.Series(50 * np.cumprod(1 + r), index=idx)
    return s, m


# ---------------------------------------------------------------------------
# Modszertan
# ---------------------------------------------------------------------------
def test_known_abnormal_return_is_recovered():
    s, m = market_and_stock()
    ar, model, est_n, alpha, beta = study.abnormal(s, m, s.index[400], (-5, 10), (-260, -21))
    assert model == "market" and est_n >= 200
    assert beta == pytest.approx(1.2, abs=0.05)
    assert ar.sum() == pytest.approx(0.05, abs=0.01)
    assert ar[6] == pytest.approx(0.05, abs=0.01)                   # az ugras az 1. nap (index: 1 - (-5))


def test_aggregate_mean_band_and_hit_rate():
    outs = []
    for k in range(40):
        s, m = market_and_stock(seed=10 + k)
        got = study.abnormal(s, m, s.index[400], (-5, 10), (-260, -21))
        outs.append(study.Outcome(f"S{k}", s.index[400 - k], got[0], got[1], got[2], got[3], got[4],
                                  groups={"year": 2019 + k % 2}))
    res = study.aggregate(outs, (-5, 10))
    assert res["summary"]["mean"] == pytest.approx(5.0, abs=0.6)
    assert res["path"]["lo"][-1] < 5.0 < res["path"]["hi"][-1]
    assert res["summary"]["hit"] == 1.0 and res["summary"]["hit_lo"] > 0.85
    assert res["post"]["mean"] == pytest.approx(5.0, abs=0.6)          # az ugras az 1. napon: "utana" is benne
    again = study.aggregate(outs, (-5, 10))
    assert again["path"] == res["path"]                              # rogzitett seed
    assert set(res["groups"]["year"]) == {"2019", "2020"}


def test_estimation_is_point_in_time():
    s, m = market_and_stock()
    day = s.index[400]
    base = study.abnormal(s, m, day, (-5, 10), (-260, -21))
    s2 = s.copy()
    s2.iloc[396:] = s2.iloc[396:] * np.linspace(0.5, 2.0, len(s2) - 396)    # az esemenyablaktol kezdve atirva
    changed = study.abnormal(s2, m, day, (-5, 10), (-260, -21))
    assert (changed[3], changed[4]) == (base[3], base[4])                   # alfa, beta valtozatlan
    with pytest.raises(ValueError):
        study.abnormal(s, m, day, (-5, 10), (-260, -5))                     # a becsles belelog az ablakba


def test_short_history_falls_back_to_market_adjusted():
    s, m = market_and_stock(n=200, jump_at=100)
    _, model, est_n, alpha, beta = study.abnormal(s, m, s.index[100], (-5, 10), (-260, -21))
    assert model == "adjusted" and (alpha, beta) == (0.0, 1.0) and est_n < study.MIN_ESTIMATION


def test_incomplete_window_is_skipped():
    s, m = market_and_stock(n=420, jump_at=None)
    assert study.abnormal(s, m, s.index[-5], (-5, 10), (-260, -21)) is None


def test_overlapping_events_of_the_same_symbol_are_dropped():
    idx = pd.bdate_range("2020-01-01", periods=300)
    mk = lambda sym, i: study.Outcome(sym, idx[i], np.zeros(16), "market", 200, 0, 1)     # noqa: E731
    kept, dropped = study.drop_overlaps([mk("A", 100), mk("A", 105), mk("A", 130), mk("B", 105)],
                                        {"A": idx, "B": idx}, (-5, 10))
    assert dropped == 1 and [(o.symbol, o.day) for o in kept] == [("A", idx[100]), ("A", idx[130]), ("B", idx[105])]


# ---------------------------------------------------------------------------
# 0. nap es detektorok
# ---------------------------------------------------------------------------
def test_day_zero_rules():
    idx = pd.bdate_range("2024-03-01", periods=10)                          # pentek, hetfo...
    ny = lambda text: pd.Timestamp(text).tz_localize("America/New_York")    # noqa: E731
    assert catalog.day0_from_time(idx, ny("2024-03-04 08:00")) == pd.Timestamp("2024-03-04")   # nyitas elott
    assert catalog.day0_from_time(idx, ny("2024-03-04 09:30")) == pd.Timestamp("2024-03-05")   # nyitaskor mar keso
    assert catalog.day0_from_time(idx, ny("2024-03-01 17:00")) == pd.Timestamp("2024-03-04")   # pentek este -> hetfo
    assert catalog.day0_from_date(idx, "2024-03-04") == pd.Timestamp("2024-03-05")             # csak datum


def _ohlc(n: int = 600, seed: int = 3) -> pd.DataFrame:
    return syn.frame(seed).iloc[:n]


def _walk(n: int = 900, seed: int = 5) -> pd.DataFrame:
    """Drift nelkuli veletlen bolyongas, nagy nyito gapekkel (uj csucs, uj melypont es gap is van benne)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2017-01-02", periods=n)
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    opens = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.015, n))
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, close) * 1.01, "low": np.minimum(opens, close) * 0.99,
                         "close": close, "volume": 3e6}, index=idx)


def test_earnings_timing_and_gap_direction():
    df = _ohlc()
    d1, d2 = df.index[300].date(), df.index[320].date()
    ctx = {"earnings_dates": {"X": [d1, d2]}, "earnings_times": {"X": {f"{d1:%Y-%m-%d}": "07:00"}}}
    evs = catalog.earnings("X", df, ctx)
    assert [(e.day, e.exact) for e in evs] == [(df.index[300], True), (df.index[321], False)]
    i = df.index.get_loc(evs[0].day)
    gap = (df["open"].iloc[i] / df["close"].iloc[i - 1] - 1) * 100
    assert evs[0].attrs["gap_pct"] == pytest.approx(gap, abs=1e-3)


@pytest.mark.parametrize("kind, params", [("high52", {}), ("low52", {}), ("gap", {"gap_pct": 2.0}),
                                          ("strategy", {"strategy": "pullback"})])
def test_detectors_do_not_look_ahead(kind, params):
    df = _ohlc(900) if kind == "strategy" else _walk()
    ctx = {"settings": Settings(), "market": None, "earnings_dates": {}}
    cut = df.index[700]
    full = [e for e in catalog.detect(kind, "X", df, ctx, params) if e.day <= cut]
    short = catalog.detect(kind, "X", df.loc[:cut], ctx, params)
    assert [(e.day, e.attrs) for e in full] == [(e.day, e.attrs) for e in short]
    assert full, f"a(z) {kind} detektor semmit nem talalt a szintetikus adaton"


def test_unknown_params_are_rejected():
    with pytest.raises(ValueError):
        catalog.detect("gap", "X", _ohlc(), {}, {"nincs": 1})
    with pytest.raises(ValueError):
        service.StudySpec(type="gap", params={"nincs": 1})
    with pytest.raises(ValueError):
        service.StudySpec(type="gap", window=(-5, 10), estimation=(-260, -3))


# ---------------------------------------------------------------------------
# Form 4
# ---------------------------------------------------------------------------
def test_form4_xml_parser():
    rows = form4.parse_xml((FIX / "form4_buy.xml").read_bytes(), "0001-24-000001", "2024-03-04",
                           "2024-03-04T21:05:00+00:00")
    assert len(rows) == 2 and rows[0]["symbol"] == "EXMP" and rows[0]["code"] == "P"
    assert rows[1]["shares"] == 2000 and rows[1]["value"] == pytest.approx(101_000)
    assert rows[0]["director"] and not rows[0]["officer"] and rows[0]["plan_10b5_1"] is False
    sale = form4.parse_xml((FIX / "form4_sale_plan.xml").read_text(encoding="utf-8"), "0001-24-000002", "2024-03-05")
    assert sale[0]["form"] == "4/A" and sale[0]["code"] == "S" and sale[0]["plan_10b5_1"] is True
    assert sale[0]["officer"] and sale[0]["title"] == "CFO" and sale[0]["accepted"] == ""
    with pytest.raises(ValueError):
        form4.parse_xml("<other/>", "x", "2024-01-01")


def _bulk_zip() -> bytes:
    files = {
        "SUBMISSION.tsv": "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tISSUERTRADINGSYMBOL\tAFF10B5ONE\n"
                          "A-1\t04-MAR-2024\t4\t1\tEXMP\t0\nA-2\t05-MAR-2024\t5\t1\tEXMP\t\nA-3\t06-MAR-2024\t4\t2\tOTHR\t1\n",
        "REPORTINGOWNER.tsv": "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
                              "A-1\t9\tDoe Jane\tDirector,Officer\tCEO\nA-3\t8\tBig Fund\tTenPercentOwner\t\n",
        "NONDERIV_TRANS.tsv": "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD\n"
                              "A-1\tP\t100\t10\tA\nA-2\tP\t5\t10\tA\nA-3\tS\t1000\t20\tD\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"2024q1_form345/{name}", text)
    return buf.getvalue()


def test_form4_bulk_parser_and_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(form4, "FORM4_DIR", tmp_path / "form4")
    rows = form4.parse_bulk(_bulk_zip())
    assert [(r["accession"], r["symbol"], r["filed"]) for r in rows] == [("A-1", "EXMP", "2024-03-04"),
                                                                         ("A-3", "OTHR", "2024-03-06")]
    assert rows[0]["director"] and rows[0]["officer"] and rows[0]["value"] == 1000 and rows[0]["plan_10b5_1"] is False
    assert rows[1]["ten_pct"] and rows[1]["plan_10b5_1"] is True
    form4.save(rows)
    form4.save(rows)                                                        # ismetelt mentes: nincs duplikalas
    assert len(form4.load()) == 2
    xml = form4.parse_xml((FIX / "form4_buy.xml").read_bytes(), "A-1", "2024-03-04", "2024-03-04T21:05:00+00:00")
    form4.save(xml)                                                         # az elo XML felulirja a csomagbelit
    stored = form4.load(symbols=["EXMP"])
    assert set(stored["source"]) == {"xml"} and len(stored) == 2
    assert form4.coverage()["years"] == ["2024"]


def _form4_frame(rows: list[tuple]) -> pd.DataFrame:
    out = []
    for acc, filed, accepted, owner, code, value, plan in rows:
        out.append({c: None for c in form4.COLUMNS} | {"accession": acc, "symbol": "X", "filed": filed,
                    "accepted": accepted, "owner": owner, "code": code, "value": value, "plan_10b5_1": plan,
                    "director": True, "officer": False, "ten_pct": False})
    return pd.DataFrame(out)


def test_insider_events_timing_thresholds_and_clusters():
    df = _ohlc()
    d = [f"{x:%Y-%m-%d}" for x in df.index[300:320]]
    rows = _form4_frame([
        ("a1", d[0], "", "Ann", "P", 150_000, None),            # csak datum -> kovetkezo nap
        ("a2", d[2], f"{d[2]}T13:00:00+00:00", "Bob", "P", 50_000, None),      # 09:00 NY: aznap
        ("a3", d[5], "", "Cid", "P", 120_000, False),
        ("a4", d[6], "", "Ann", "P", 120_000, None),            # ugyanaz a bennfentes: nem novel
        ("s1", d[8], "", "Dan", "S", 5_000_000, True),          # 10b5-1 terv: kimarad
        ("s2", d[9], "", "Eve", "S", 5_000_000, False),
    ])
    ctx = {"form4": rows}
    buys = catalog.insider_buy("X", df, ctx, min_value=100_000)
    assert [(e.day, e.exact) for e in buys] == [(df.index[301], False), (df.index[306], False), (df.index[307], False)]
    sells = catalog.insider_sell("X", df, ctx, min_value=1_000_000, exclude_plans=True)
    assert [e.day for e in sells] == [df.index[310]]
    clusters = catalog.insider_cluster_buy("X", df, ctx, min_insiders=3, days=10)
    assert len(clusters) == 1 and clusters[0].day == df.index[306] and clusters[0].attrs["insiders"] == 3


def test_sec_requests_are_spaced_across_processes_and_identified(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "RATE_FILE", tmp_path / "_sec_rate")
    waits, now = [], [1000.0]
    monkeypatch.setattr(edgar.time, "time", lambda: now[0])
    monkeypatch.setattr(edgar.time, "sleep", lambda s: (waits.append(s), now.__setitem__(0, now[0] + s)))
    (tmp_path / "_sec_rate").write_text(repr(999.95))           # "egy masik folyamat" epp kert
    edgar.throttle()
    edgar.throttle()
    assert waits[0] == pytest.approx(0.075, abs=1e-6) and waits[1] == pytest.approx(edgar.MIN_INTERVAL)
    assert 1 / edgar.MIN_INTERVAL <= 10

    class Session:
        def get(self, url, headers, timeout):
            self.headers = headers

            class R:
                content = b"{}"

                def raise_for_status(self):
                    pass
            return R()

    s = Session()
    monkeypatch.setenv("SEC_USER_AGENT", "Teszt Elek teszt@pelda.hu")
    edgar.get_bytes("https://www.sec.gov/x", s)
    assert s.headers["User-Agent"] == "Teszt Elek teszt@pelda.hu"


# ---------------------------------------------------------------------------
# Indexvaltozasok
# ---------------------------------------------------------------------------
def test_index_changes_parser_and_events():
    rows = index_changes.parse_changes((FIX / "sp500_changes.html").read_text(encoding="utf-8"))
    assert [(r["date"], r["added"], r["removed"]) for r in rows] == [
        ("1976-07-01", "BRK-B", "HNG"), ("2024-03-18", "DECK", "ZION"), ("2024-03-18", "SMCI", "WHR"),
        ("2026-10-06", None, "CTVA")]
    from stockwatch import indexes

    assert indexes.parse_sectors((FIX / "sp500_changes.html").read_text(encoding="utf-8")) == \
        {"SMCI": "Information Technology", "BRK-B": "Financials"}
    idx = pd.bdate_range("2024-03-01", periods=30)
    evs = catalog.sp500_add("SMCI", pd.DataFrame(index=idx), {"index_changes": rows})
    assert [e.day for e in evs] == [pd.Timestamp("2024-03-18")] and evs[0].exact


# ---------------------------------------------------------------------------
# Teljes futas
# ---------------------------------------------------------------------------
@pytest.fixture
def universe_env(tmp_path, monkeypatch):
    frames = syn.universe(8)
    symbols = sorted(s for s in frames if s.startswith("S"))
    monkeypatch.setattr(service, "universe", lambda spec, settings, ws="admin": symbols if spec.universe != "symbol"
                        else [spec.symbol])
    monkeypatch.setattr(service, "prices", lambda symbol, settings, end: frames[symbol].loc[:end] if symbol in frames else None)
    monkeypatch.setattr(service, "_bench_frames", lambda settings, end: {"QQQ": frames["QQQ"]["close"].loc[:end],
                                                                         "^GSPC": frames["^GSPC"]["close"].loc[:end]})
    from stockwatch import indexes, market

    monkeypatch.setattr(indexes.IndexMembers, "members", lambda self, name, allow_network=True: symbols)
    monkeypatch.setattr(market, "HistoricalEarnings", syn.NoEarnings)
    # a rezsim-indexek a szintetikus univerzumbol (kulonben a valodi data/ cache-tol fuggne: CI-ban ures)
    ctx = market.MarketContext({"QQQ": frames["QQQ"], "^GSPC": frames["^GSPC"]}, frozenset(symbols), frozenset())
    monkeypatch.setattr(market, "load_context", lambda settings, refresh=True, allow_network=None: ctx)
    log = tmp_path / "holdout_log.jsonl"
    log.write_text("", encoding="utf-8")
    monkeypatch.setattr(er, "HOLDOUT_LOG", log)
    return {"frames": frames, "symbols": symbols, "log": log}


def test_full_run_keeps_holdout_closed_and_saves(universe_env):
    rec = service.run(service.StudySpec(type="gap", params={"gap_pct": 1.0}, universe="indexes",
                                        window=(-5, 20)), Settings())
    assert rec["result"]["n"] > 0
    assert max(e["day"] for e in rec["events"]) < "2025-04-01"
    assert rec["end"] == "2025-03-31" and universe_env["log"].read_text() == ""
    assert store.load(rec["id"])["result"]["n"] == rec["result"]["n"]
    csv_text = service.to_csv(rec)
    assert csv_text.splitlines()[0].startswith("symbol,day,car_pct") and len(csv_text.splitlines()) == len(rec["events"]) + 1
    detail = service.event_detail(rec, 0, Settings())
    assert detail["rel"][0] == -6 and 0 in detail["rel"] and len(detail["close"]) == len(detail["rel"])


def test_holdout_only_with_explicit_switch_and_logged(universe_env):
    rec = service.run(service.StudySpec(type="gap", params={"gap_pct": 1.0}, universe="indexes", window=(-5, 20),
                                        include_holdout=True, end="2025-12-31"), Settings())
    assert max(e["day"] for e in rec["events"]) >= "2025-04-01"
    log = universe_env["log"].read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1 and "event-study" in log[0]


def test_strategy_events_use_the_live_engine(universe_env):
    rec = service.run(service.StudySpec(type="strategy", params={"strategy": "pullback"}, universe="indexes",
                                        window=(-5, 20)), Settings())
    assert rec["result"]["n"] > 0 and all("rsi2" in e["attrs"] for e in rec["events"])


@pytest.mark.parametrize("lang", ["hu", "en", "de", "es"])
def test_text_numbers_and_no_advice(universe_env, lang):
    rec = service.run(service.StudySpec(type="gap", params={"gap_pct": 1.0}, universe="indexes", window=(-5, 20)),
                      Settings())
    for ln in rec["lines"]:
        line = service.Line(ln["key"], ln["args"], ln["numeric"])
        strings = [a for i, a in enumerate(line.args) if i not in line.numeric]
        numbers = {a for i, a in enumerate(line.args) if i in line.numeric}
        assert unexplained_numbers(ln["text"], strings, numbers) == [], ln["text"]
        text = i18n.translate(ln["text"], lang)
        assert not advice_hits(text), text
        if lang != "hu":
            assert text != ln["text"], f"nincs forditas ({lang}): {ln['text']}"


def test_templates_digit_free_and_in_catalog():
    import re

    rows = {" ".join(json.loads(x)["hu"].split()) for x in i18n.CATALOG_PATH.read_text(encoding="utf-8").splitlines() if x}
    for key, template in TEMPLATES.items():
        assert not re.search(r"\d", re.sub(r"\{\d+(:n)?\}", "", template)), key
        assert " ".join(template.split()) in rows, template


def test_web_payload_and_queue_handler(universe_env, monkeypatch):
    from stockwatch import webapp

    rec = service.run(service.StudySpec(type="high52", universe="indexes", window=(-5, 20)), Settings())
    payload = webapp.events_payload()
    assert rec["id"] in [s["id"] for s in payload["studies"]] and "earnings" in payload["types"]
    out = webapp.events_action("study", {"spec": {"type": "gap", "universe": "sp500"}})
    assert out["queued"] and labqueue.get("admin", out["queued"])["kind"] == "event_study"
    assert "event_study" in labqueue._handlers()
    webapp.events_action("delete", {"id": rec["id"]})
    assert store.load(rec["id"]) is None


def test_studies_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_STUDIES", 3)
    ids = [store.save({"result": {}, "spec": {}}) for _ in range(5)]
    assert len(store.listing()) == 3 and store.load(ids[-1]) is not None
    assert gzip is not None
