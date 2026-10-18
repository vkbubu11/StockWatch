"""Kutatougynok (research_agent/): holdout-or, csak tanito idoszakos kivalasztas, determinizmus, paritas a
meanrev_research.run-nal, ervenytelen parameter, koltsegkeret, Homokozo-atvetel, munkasor, szovegezes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from stockwatch import config, i18n, labqueue, workspace
from stockwatch import entry_research as er
from stockwatch import meanrev_research as mr
from stockwatch.config import PullbackParams, Settings
from stockwatch.research_agent import explain, search, service, store, strategy
from stockwatch.research_agent.brief import Brief
from stockwatch.research_agent.space import Space
from stockwatch.research_agent.templates import TEMPLATES

from . import agent_synthetic as syn
from .guards import advice_hits, unexplained_numbers

N_SYMBOLS = 12


def small_brief(**search_kw) -> Brief:
    return Brief(name="Teszt cel", engine="pullback", universe="indexes",
                 objective={"trades_per_week": [0, 50], "min_trades": 5, "max_drawdown_pct": 50, "min_pf": 1.0,
                            "cost_bps": 10, "max_hold_days": 15},
                 search={"seed": 3, "max_variants": 16, "max_cpu_minutes": 30} | search_kw)


def _isolate(mp, tmp) -> None:
    mp.setattr(workspace, "USERS_DIR", tmp / "users")
    mp.setattr(labqueue, "QUEUE_DIR", tmp / "queue")
    mp.setattr(config, "STRATEGIES_PATH", tmp / "strategies.yaml")
    mp.setattr(mr, "SANDBOX_DIR", tmp / "sandbox")
    mp.setattr(mr, "SANDBOX_REQUEST", tmp / "sandbox" / "request.json")
    mp.setattr(mr, "SANDBOX_STATUS", tmp / "sandbox" / "status.json")
    log = tmp / "holdout_log.jsonl"
    log.write_text('{"elozo": "megnyitas"}\n', encoding="utf-8")
    mp.setattr(er, "HOLDOUT_LOG", log)
    config.load_settings.cache_clear()


@pytest.fixture(scope="module")
def frames():
    return syn.universe(N_SYMBOLS)


@pytest.fixture(scope="module")
def symbols(frames):
    return sorted(s for s in frames if s.startswith("S"))


@pytest.fixture(scope="module")
def shared(tmp_path_factory, frames, symbols):
    """Egy futas, kemlelve: mit kap a motor (datum), es mely idoszakot ertekeli."""
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("agent")
    _isolate(mp, tmp)
    syn.install(mp, frames, symbols)
    seen = {"last": [], "visible": [], "train_end": []}
    real_load = mr.load_universe

    def spy_load(*a, **k):
        data = real_load(*a, **k)
        seen["last"] += [d.feat.index[-1] for d in data.values()]
        return data

    mp.setattr(mr, "load_universe", spy_load)
    real_many = search.PullbackEngine.evaluate_many

    def spy_many(self, items, fraction, period="train", cost_mult=1.0):
        if period == "visible" and cost_mult == 1.0:
            seen["visible"] += [k for k, _ in items]
        return real_many(self, items, fraction, period, cost_mult)

    mp.setattr(search.PullbackEngine, "evaluate_many", spy_many)
    log_before = er.HOLDOUT_LOG.read_bytes()
    report = service.run(small_brief(), Settings())
    yield {"report": report, "seen": seen, "log_before": log_before, "tmp": tmp, "mp": mp}
    mp.undo()
    config.load_settings.cache_clear()


@pytest.fixture
def shared_ws(shared, monkeypatch):
    """A kozos futas munkaterulete (a conftest minden tesztnek uj, ures munkateruletet ad)."""
    monkeypatch.setattr(workspace, "USERS_DIR", shared["tmp"] / "users")
    monkeypatch.setattr(labqueue, "QUEUE_DIR", shared["tmp"] / "queue")
    return shared


# ---------------------------------------------------------------------------
# Brief es ter
# ---------------------------------------------------------------------------
def test_brief_schema_roundtrip_and_validation():
    b = Brief.from_yaml(open("briefs/pelda-swing-long.yaml", encoding="utf-8").read())
    assert b.engine == "pullback" and b.objective.trades_per_week == (0.5, 2.0) and b.search.seed == 42
    assert Brief.from_yaml(b.to_yaml()) == b
    for bad in ({"name": "x", "objective": {"trades_per_week": [2, 1]}}, {"name": "x", "unknown": 1},
                {"name": "x", "objective": {"min_win_lo": 1.5}}, {"name": "x", "search": {"max_variants": 1}},
                {"name": "x", "engine": "breakout", "universe": "etf"}, {"name": ""}):
        with pytest.raises(ValueError):
            Brief(**bad)


def test_space_contains_preset_respects_max_hold_and_fixed():
    s = Space.from_brief(Brief(name="x", objective={"max_hold_days": 7}, search={"fixed": {"regime_filter": True}}),
                         Settings())
    dims = {d.name: d.values for d in s.dims}
    assert "regime_filter" not in dims and s.preset["regime_filter"] is True
    assert max(dims["time_stop"]) <= 7 and s.preset["time_stop"] == 7
    preset = s.preset_assignment()
    assert all(preset[n] in v for n, v in dims.items())
    assert s.preset["cost_bps"] == 15.0
    with pytest.raises(ValueError):
        Space.from_brief(Brief(name="x", search={"fixed": {"nincs_ilyen": 1}}), Settings())


def test_plan_and_subset_are_deterministic():
    s = Space.from_brief(Brief(name="x"), Settings())
    a = strategy.plan(s, "halving", 40, seed=5)
    b = strategy.plan(s, "halving", 40, seed=5)
    assert a.initial == b.initial and a.initial[0] == s.preset_assignment()
    assert strategy.plan(s, "halving", 40, seed=6).initial != a.initial
    names = [f"S{i:02d}" for i in range(20)]
    assert strategy.subset(names, 0.25, 1) == strategy.subset(list(reversed(names)), 0.25, 1)
    with pytest.raises(ValueError):
        strategy.plan(s, "grid", 40, seed=5)                        # a racs nem fer bele a keretbe


# ---------------------------------------------------------------------------
# Becsuletesseg
# ---------------------------------------------------------------------------
def test_agent_never_sees_holdout_data_and_log_is_unchanged(shared):
    assert shared["seen"]["last"], "a motor nem kapott adatot"
    assert max(shared["seen"]["last"]) < er.HOLDOUT_START
    assert er.HOLDOUT_LOG.read_bytes() == shared["log_before"]


def test_guard_rejects_holdout_data():
    with pytest.raises(search.HoldoutLeak):
        search.guard([pd.Timestamp("2025-04-01")])
    assert search.guard([pd.Timestamp("2025-03-31")]) == pd.Timestamp("2025-03-31")


def test_validation_period_only_for_finalists_once(shared):
    r = shared["report"]
    keys = [f["key"] for f in r["finalists"] if f["checked"]]
    assert sorted(shared["seen"]["visible"]) == sorted(keys)            # minden dontos pontosan egyszer
    assert len(keys) == min(search.FINALISTS, r["tried"]["valid"])


def test_search_data_ends_before_split_date(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    ends = []
    real = mr.load_universe

    def spy(*a, **k):
        ends.append(k.get("end"))
        return real(*a, **k)

    monkeypatch.setattr(mr, "load_universe", spy)
    b = small_brief(max_variants=6)
    s = Space.from_brief(b, Settings())
    engine = search.make_engine(s, b, Settings(), symbols)
    srch = search.Search(b, Settings(), engine, s, search.Budget(6, 30))
    plan = strategy.plan(s, "halving", 6, 3)
    items = [srch._register(a, "initial") for a in plan.initial]
    srch.evaluate(items, 1.0)                                            # a keresesi ertekeles
    assert ends and all(e == search.TRAIN_END for e in ends)


# ---------------------------------------------------------------------------
# Determinizmus es paritas
# ---------------------------------------------------------------------------
def _without_runtime(report: dict) -> dict:
    return {k: v for k, v in report.items() if k != "runtime"}


def test_report_is_deterministic_with_fixed_seed(shared, frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    again = service.run(small_brief(), Settings())
    first = json.loads(json.dumps(_without_runtime(shared["report"]), default=str))
    second = json.loads(json.dumps(_without_runtime(again), default=str))
    assert first["validation_looks"] == 1 and second["validation_looks"] == 1
    assert first == second


def test_parity_with_meanrev_research_run(shared, frames, symbols, tmp_path, monkeypatch):
    syn.install(monkeypatch, frames, symbols)
    best = shared["report"]["finalists"][0]
    params = PullbackParams(**best["adopt"]["params"])
    out = mr.run(symbols, Settings(), params, progress=lambda *_: None, out_dir=tmp_path, end=search.VISIBLE_END)
    for period in ("train", "test"):
        ref, got = out["main"][period], best[period]
        assert got["n"] == ref["n"]
        assert got["win"] == pytest.approx(ref["win"], abs=1e-4)
        assert got["ev"] == pytest.approx(ref["ev"], abs=1e-4)
        assert got["max_dd"] == pytest.approx(ref["max_dd_pct"], abs=1e-4)


# ---------------------------------------------------------------------------
# Ervenytelen parameter, keret
# ---------------------------------------------------------------------------
def test_invalid_parameter_is_rejected_and_search_continues(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    real = Space.params

    def picky(self, assignment):
        if assignment.get("rsi2_max") == 7.0:
            raise ValueError("pullback: proba-elutasitas")
        return real(self, assignment)

    monkeypatch.setattr(Space, "params", picky)
    report = service.run(small_brief(max_variants=24), Settings())
    assert report["tried"]["rejected"] > 0 and report["finalists"]
    rejected = [r for r in store.read_log(report["runtime"]["run_id"]) if r.get("verdict") == "rejected"]
    assert rejected and "proba-elutasitas" in rejected[0]["reason"]


def test_all_invalid_still_writes_a_report(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    report = service.run(small_brief(fixed={"limit_pct": 40.0}), Settings())     # a PullbackParams elutasitja
    assert report["tried"]["rejected"] == report["tried"]["total"] > 0
    assert report["finalists"] == [] and any(ln["key"] == "no_result" for ln in report["summary"])


class StepClock:
    """Minden hivaskor `step` masodpercet leptet: a CPU-keret determinisztikusan elfogy."""

    def __init__(self, step: float) -> None:
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_cpu_budget_stops_and_partial_report_is_made(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    report = service.run(small_brief(max_variants=60, max_cpu_minutes=1), Settings(), clock=StepClock(15))
    assert report["stopped"] == "cpu"
    assert report["tried"]["total"] < 60
    assert any(ln["key"] == "stopped_cpu" for ln in report["summary"])
    assert store.status(report["runtime"]["run_id"])["state"] == "done"
    assert (store.run_dir(report["runtime"]["run_id"]) / "report.md").exists()


def test_deadline_stops_the_search(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    report = service.run(small_brief(), Settings(), deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert report["stopped"] == "deadline" and report["finalists"] == []


def test_variant_budget_is_respected(shared):
    r = shared["report"]
    assert r["tried"]["total"] <= r["brief"]["search"]["max_variants"]


# ---------------------------------------------------------------------------
# Homokozo-atvetel, munkasor
# ---------------------------------------------------------------------------
def test_adopt_to_sandbox_without_paper_and_without_alerts(shared_ws):
    shared = shared_ws
    before = config.load_settings()
    run_id = shared["report"]["runtime"]["run_id"]
    service.adopt(run_id, 1)
    config.load_settings.cache_clear()
    after = config.load_settings()
    sandbox = after.strategies.sandbox
    best = shared["report"]["finalists"][0]["adopt"]["params"]
    assert sandbox.engine == "pullback" and sandbox.enabled and sandbox.alerts is False
    assert sandbox.params.model_dump() | {"commission_bps": 0.0} == PullbackParams(**best).model_dump() | {"commission_bps": 0.0}
    assert after.strategies.paper == before.strategies.paper and after.strategies.paper != "sandbox"
    assert after.broker.auto_trade == before.broker.auto_trade
    assert mr.SANDBOX_REQUEST.exists()                                   # a homokozo backtestje sorba allt
    from stockwatch import sandbox_adopt

    assert sandbox_adopt.history()[0]["replaced_by"] == f"agent:{run_id}#1"


def test_queue_runs_the_job_in_the_lab(frames, symbols, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    syn.install(monkeypatch, frames, symbols)
    store.save_brief(small_brief(max_variants=8))
    brief_id = small_brief().id
    out = service.submit(brief_id, when="now")
    assert store.status(out["run_id"])["state"] == "queued"
    assert labqueue.run_next(Settings()) is True
    assert store.status(out["run_id"])["state"] == "done" and store.load_report(out["run_id"])
    assert labqueue.get("admin", out["job"]["id"])["status"] == "done"
    # ejszakai futas: napkozben meg nem esedekes
    noon = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)
    night = service.submit(brief_id, when="night", now=noon)
    assert night["job"]["not_before"] > noon.isoformat()
    assert labqueue.claim(now=noon) is None
    service.cancel(night["run_id"])
    assert store.status(night["run_id"])["state"] == "canceled"


def test_queue_limits_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(labqueue, "QUEUE_DIR", tmp_path / "queue")
    for _ in range(labqueue.MAX_PENDING):
        labqueue.submit("admin", "agent", {})
    with pytest.raises(ValueError):
        labqueue.submit("admin", "agent", {})
    job = labqueue.claim()
    assert job["status"] == "running"
    assert labqueue.recover(all_running=True) == 1
    assert labqueue.get("admin", job["id"])["status"] == "error"


def test_night_window_ends_before_the_morning_digest():
    start, deadline = service.night_window(datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc))
    assert pd.Timestamp(start).tz_convert("America/New_York").hour == 20
    assert pd.Timestamp(deadline).tz_convert("Europe/Budapest").strftime("%Y-%m-%d %H:%M") == "2026-10-20 06:00"
    start, deadline = service.night_window(datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc))   # mar ejszaka van
    assert start == datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
    assert pd.Timestamp(deadline).tz_convert("Europe/Budapest").strftime("%Y-%m-%d %H:%M") == "2026-10-20 06:00"


# ---------------------------------------------------------------------------
# Szovegezes
# ---------------------------------------------------------------------------
def _lines(report: dict) -> list[dict]:
    out = list(report["summary"]) + list(report["warnings"]) + [report["disclaimer"]]
    for f in report["finalists"]:
        out += f["explanation"]
    return out


def test_every_number_in_the_report_text_comes_from_the_data(shared):
    for ln in _lines(shared["report"]):
        line = explain.Line(ln["key"], ln["args"], ln["numeric"])
        assert line.text == ln["text"]
        assert unexplained_numbers(ln["text"], line.strings, set(line.numbers)) == [], ln["text"]
    best = shared["report"]["finalists"][0]
    for a in best["ablation"] or []:
        assert a["d_win_pp"] is not None                               # az indoklas szamai a mert ablaciobol


@pytest.mark.parametrize("lang", ["hu", "en", "de", "es"])
def test_report_text_has_no_advice_wording(shared_ws, lang):
    shared = shared_ws
    run_id = shared["report"]["runtime"]["run_id"]
    timeline = [explain.log_line(r) for r in store.read_log(run_id)]
    for ln in _lines(shared["report"]) + [t.to_dict() for t in timeline if t]:
        text = i18n.translate(ln["text"], lang)
        assert not advice_hits(text), (lang, text)
        if lang != "hu":
            assert text != ln["text"], f"nincs forditas ({lang}): {ln['text']}"


def test_templates_are_digit_free_and_in_the_catalog():
    rows = {" ".join(json.loads(line)["hu"].split()): json.loads(line)
            for line in i18n.CATALOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()}
    for key, template in TEMPLATES.items():
        assert not any(c.isdigit() for c in explain._SLOT.sub("", template)), key
        row = rows.get(" ".join(template.split()))
        assert row is not None, template
        assert not any(advice_hits(row[lang]) for lang in ("hu", "en", "de", "es")), row


def test_markdown_report_and_web_payloads(shared_ws, monkeypatch):
    shared = shared_ws
    from stockwatch import webapp

    run_id = shared["report"]["runtime"]["run_id"]
    md = (store.run_dir(run_id) / "report.md").read_text(encoding="utf-8")
    assert "# Kutatóügynök" in md and "1. jelölt" in md and "Nem befektetési tanács" in md
    payload = webapp.agent_run_payload(run_id)
    assert payload["status"]["state"] == "done" and payload["timeline"]
    assert payload["report"]["finalists"][0]["equity"]
    with pytest.raises(ValueError):
        webapp.agent_action("adopt", {"run_id": run_id, "rank": 1})        # megerosites nelkul nem
    listing = webapp.agent_payload()
    assert any(r["run_id"] == run_id for r in listing["runs"]) and listing["fields"]["pullback"]


def test_digest_mentions_the_finished_report(shared_ws):
    shared = shared_ws
    from stockwatch.digest import render
    from stockwatch.digest import service as digest

    ref = digest.agent_reference("admin", None)
    assert ref and ref["finalists"] >= 1 and ref["tried"] >= 1
    line = render.render({"for_day": "2026-10-20", "as_of": "2026-10-19", "symbols_count": 1, "first": False,
                          "agent": ref, "selected": {}})[1]
    assert line.key == "agent_report" and ref["brief_name"] in line.text


# ---------------------------------------------------------------------------
# Kitores-motor a laboron keresztul
# ---------------------------------------------------------------------------
def test_breakout_engine_goes_through_the_lab_with_cut_data(monkeypatch):
    from stockwatch import lab

    calls = []

    def fake_evaluate(exps, names, settings, progress=None, symbol_hook=None, until=None, guard=None):
        calls.append((until, guard, [e["entry"]["slippage_bps"] for e in exps]))
        guard([until])                                        # a labor a vagott gyertya utolso napjat adja at
        days = pd.bdate_range("2018-01-02", until, freq="20B")
        return {e["id"]: [er.Result("AAA", "Dupla alj", d, "breakout", 1.0 if i % 3 else -1.0, "target", 5)
                          for i, d in enumerate(days)] for e in exps}

    monkeypatch.setattr(lab, "evaluate", fake_evaluate)
    monkeypatch.setattr(lab, "load_cache", lambda s: {"events": []})
    idx = pd.bdate_range("2016-10-03", "2025-12-31")
    monkeypatch.setattr(lab, "_raw", lambda s, settings: pd.DataFrame({"close": 1.0}, index=idx))
    b = Brief(name="kitores", engine="breakout", objective={"trades_per_week": [0, 5], "min_trades": 5,
                                                             "max_drawdown_pct": 90, "cost_bps": 12})
    s = Space.from_brief(b, Settings())
    engine = search.make_engine(s, b, Settings(), ["AAA"])
    res = engine.evaluate_many([("k", s.preset_assignment())], 1.0, "train")
    assert calls[-1][0] == search.TRAIN_END and calls[-1][2] == [12.0]
    assert res["k"]["train"]["n"] > 0 and res["k"]["train"]["win"] == pytest.approx(2 / 3, abs=0.05)
    vis = engine.evaluate_many([("k", s.preset_assignment())], 1.0, "visible", cost_mult=2.0)
    assert calls[-1][0] == search.VISIBLE_END and calls[-1][2] == [24.0]
    assert vis["k"]["test"]["n"] > 0 and vis["k"]["equity"]


def test_lab_evaluate_cuts_frames_and_guard_stops_leaks(monkeypatch):
    from stockwatch import lab

    idx = pd.bdate_range("2016-10-03", "2025-12-31")
    frame = pd.DataFrame({"close": 1.0}, index=idx)
    monkeypatch.setattr(lab, "load_cache", lambda s: {"events": []})
    monkeypatch.setattr(lab, "load_frame", lambda s, settings: frame)
    monkeypatch.setattr(lab, "calibration_from_caches", lambda symbols: None)
    monkeypatch.setattr(lab.market, "load_context", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(lab.er, "run_variant", lambda v, ev, frames, *a, **k: seen.append(frames["AAA"].index[-1]) or [])
    exp = {"id": "x", "name": "x", "universe": "all", "entry": Settings().entry.model_dump() |
           {"earnings_blackout_days": 0}, "live": False}
    monkeypatch.setattr(lab, "universe_symbols", lambda name, settings: ["AAA"])
    lab.evaluate([exp], ["AAA"], Settings(), until=search.VISIBLE_END, guard=search.guard)
    assert seen == [search.VISIBLE_END]
    with pytest.raises(search.HoldoutLeak):
        lab.evaluate([exp], ["AAA"], Settings(), until=pd.Timestamp("2025-06-30"), guard=search.guard)
