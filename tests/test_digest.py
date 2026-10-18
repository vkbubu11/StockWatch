"""Reggeli osszefoglalo (digest/): valtozaserzekeles szintetikus allapotparokon, pontozas es plafon,
szam-ellenorzes, tiltolista, sablonok / katalogus, utemezes, es egy teljes futas szintetikus adaton."""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pandas as pd
import pytest

from stockwatch import i18n, sessions, usage, workspace
from stockwatch.config import DigestSettings, Settings
from stockwatch.digest import detect, render, score, service, store
from stockwatch.digest.templates import TEMPLATES

from . import synthetic
from .guards import advice_hits, unexplained_numbers

CFG = Settings().digest


def sym(**kw) -> dict:
    base = {"bar": "2026-10-15", "holding": False, "close": 100.0, "atr": 2.0, "sma": 95.0, "trend_ok": True,
            "patterns": [], "support": 90.0, "resistance": 105.0, "earnings": None, "earnings_days": None,
            "filings": [], "sentiment": {"state": "neutral", "mean": 0.02, "material_neg": False,
                                         "material_event": None},
            "candidates": []}
    base.update(kw)
    return base


def snap(symbols: dict, market: dict | None = None, day: str = "2026-10-16",
         created: str = "2026-10-16T04:30:00+00:00", known=("breakout", "pullback")) -> dict:
    return {"version": 1, "workspace": "admin", "for_day": day, "as_of": "2026-10-15", "created_at": created,
            "symbols_count": len(symbols), "market": market or {}, "symbols": symbols,
            "strategies_known": list(known), "sentiment_days": 3}


def pattern(name="Dupla alj", status="forming", trigger=104.0, start="2026-08-03", dist=2.0, direction="bullish"):
    return {"name": name, "direction": direction, "status": status, "trigger": trigger, "start": start,
            "dist_atr": dist}


def types(events) -> list[str]:
    return sorted(e.type for e in events)


def pair(prev_sym: dict, cur_sym: dict, **kw):
    prev = snap({"AAA": sym(**prev_sym)}, day="2026-10-15", created="2026-10-15T04:30:00+00:00")
    cur = snap({"AAA": sym(**({"bar": "2026-10-16"} | cur_sym))}, **kw)
    return detect.diff(prev, cur, CFG)


# ---------------------------------------------------------------------------
# Valtozaserzekeles
# ---------------------------------------------------------------------------
def test_first_run_reports_state_only():
    record = service.assemble(snap({"AAA": sym()}), None, CFG)
    assert record["first"] and record["events"] == []
    assert [ln["key"] for ln in record["lines"]] == ["header", "first_run", "unchanged", "disclaimer"]


def test_symbol_added_since_last_time_has_no_events():
    prev = snap({}, day="2026-10-15")
    cur = snap({"AAA": sym(bar="2026-10-16", close=200.0, trend_ok=False)})
    assert detect.diff(prev, cur, CFG) == []


def test_market_regime_change():
    m0 = {"QQQ": {"label": "Nasdaq-100 (QQQ)", "bar": "2026-10-15", "ok": True, "close": 500.0, "sma": 490.0}}
    m1 = {"QQQ": {"label": "Nasdaq-100 (QQQ)", "bar": "2026-10-16", "ok": False, "close": 480.0, "sma": 489.0}}
    events = detect.diff(snap({}, m0), snap({}, m1), CFG)
    assert [(e.type, e.symbol, e.values["up"], e.values["close"], e.values["sma"]) for e in events] == \
        [("regime_change", None, False, 480.0, 489.0)]
    m1_same_bar = {"QQQ": dict(m1["QQQ"], bar="2026-10-15")}
    assert detect.diff(snap({}, m0), snap({}, m1_same_bar), CFG) == []        # nincs uj gyertya -> nincs valtozas


def test_own_trend_flip():
    events = pair({"trend_ok": True}, {"trend_ok": False, "close": 94.0, "sma": 95.5})
    assert types(events) == ["own_trend"] and events[0].values == {"up": False, "close": 94.0, "period": 200,
                                                                     "sma": 95.5}


def test_patterns_new_gone_triggered_and_same_pattern_with_shifted_start():
    assert types(pair({"patterns": []}, {"patterns": [pattern()]})) == ["pattern_new"]
    assert types(pair({"patterns": [pattern()]}, {"patterns": []})) == ["pattern_gone"]
    assert types(pair({"patterns": [pattern()]}, {"patterns": [pattern(status="triggered", dist=-0.2)]})) == \
        ["pattern_triggered"]
    # a kezdopont par napot csuszott: ugyanaz a minta, nincs uj / megszunt
    assert pair({"patterns": [pattern(start="2026-08-03")]}, {"patterns": [pattern(start="2026-08-05")]}) == []
    # mas iranyu minta ugyanazzal a nevvel: masik minta
    assert types(pair({"patterns": [pattern()]}, {"patterns": [pattern(direction="bearish")]})) == \
        ["pattern_gone", "pattern_new"]


def test_near_trigger_only_when_it_becomes_near_or_approaches_further():
    assert types(pair({"patterns": [pattern(dist=1.5)]}, {"patterns": [pattern(dist=0.9)]})) == ["near_trigger"]
    assert pair({"patterns": [pattern(dist=0.9)]}, {"patterns": [pattern(dist=0.8)]}) == []
    assert types(pair({"patterns": [pattern(dist=0.9)]}, {"patterns": [pattern(dist=0.3)]})) == ["near_trigger"]
    # uj, mar kozeli minta: kozelseg (nem "uj formacio" is)
    assert types(pair({"patterns": []}, {"patterns": [pattern(dist=0.5)]})) == ["near_trigger"]


def test_near_trigger_mentions_earnings_only_within_the_window():
    ev = pair({"patterns": [pattern(dist=1.5)]}, {"patterns": [pattern(dist=0.8)], "earnings": "2026-10-21",
                                                   "earnings_days": 5})
    near = next(e for e in ev if e.type == "near_trigger")
    assert near.values["earnings_days"] == 5
    assert types(ev) == ["near_trigger"]                     # a jelentes nem kerul be kulon sorban is
    assert render.event_line(near).key == "near_trigger_earnings"
    ev = pair({"patterns": [pattern(dist=1.5)]}, {"patterns": [pattern(dist=0.8)], "earnings": "2026-11-30",
                                                   "earnings_days": 45})
    assert ev[0].values["earnings_days"] is None and render.event_line(ev[0]).key == "near_trigger"


def test_level_break_and_loss():
    assert types(pair({"close": 104.0, "resistance": 105.0}, {"close": 106.2})) == ["level_break"]
    assert types(pair({"close": 91.0, "support": 90.0}, {"close": 89.5})) == ["level_lost"]
    assert pair({"close": 106.0, "resistance": 105.0}, {"close": 107.0}) == []          # mar folotte volt


def test_earnings_thresholds_fire_once_each():
    assert types(pair({"earnings": "2026-10-23", "earnings_days": 8}, {"earnings": "2026-10-23", "earnings_days": 7})) \
        == ["earnings_soon"]
    assert pair({"earnings": "2026-10-23", "earnings_days": 7}, {"earnings": "2026-10-23", "earnings_days": 6}) == []
    assert types(pair({"earnings": "2026-10-23", "earnings_days": 4}, {"earnings": "2026-10-23", "earnings_days": 3})) \
        == ["earnings_soon"]
    # uj datum (eddig nem ismert): mar a kuszobon belul -> szol
    assert types(pair({}, {"earnings": "2026-10-20", "earnings_days": 2})) == ["earnings_soon"]


def test_new_8k_filings_only():
    old = {"id": "edgar:1", "published_at": "2026-10-14T21:00:00+00:00", "items": ["8.01"], "material": False}
    new = {"id": "edgar:2", "form": "8-K", "published_at": "2026-10-15T22:10:00+00:00", "items": ["4.02", "9.01"], "material": True}
    stale = {"id": "edgar:3", "published_at": "2026-10-03T22:10:00+00:00", "items": ["8.01"], "material": False}
    events = pair({"filings": [old]}, {"filings": [old, new, stale]})
    assert [(e.type, e.values["items"], e.values["material"]) for e in events] == \
        [("filing_8k", ["4.02", "9.01"], True)]
    assert render.event_line(events[0]).text == "AAA: új 8-K bejelentés lényeges tétellel: 4.02, 9.01."


def test_sentiment_flip_and_material_news():
    pos = {"state": "pos", "mean": 0.31, "material_neg": False, "material_event": None}
    neg = {"state": "neg", "mean": -0.22, "material_neg": True, "material_event": "leminosites"}
    neutral = {"state": "neutral", "mean": -0.05, "material_neg": False, "material_event": None}
    assert types(pair({"sentiment": pos}, {"sentiment": neg})) == ["material_news", "sentiment_flip"]
    assert types(pair({"sentiment": neutral}, {"sentiment": dict(neg, material_neg=False)})) == []


def test_candidates_only_for_strategies_known_on_both_days():
    events = pair({"candidates": ["breakout", "deep"]}, {"candidates": ["pullback", "deep"]})
    assert [(e.type, e.values["strategy"]) for e in events] == [("candidate_in", "pullback"),
                                                                ("candidate_out", "breakout")]


def test_no_new_bar_no_price_events_but_calendar_and_filings_still_count():
    f = {"id": "edgar:9", "published_at": "2026-10-15T22:00:00+00:00", "items": [], "material": False}
    events = pair({"close": 104.0, "trend_ok": True, "patterns": [pattern()]},
                  {"bar": "2026-10-15", "close": 106.0, "trend_ok": False, "patterns": [], "filings": [f],
                   "earnings": "2026-10-19", "earnings_days": 3})
    assert types(events) == ["earnings_soon", "filing_8k"]


# ---------------------------------------------------------------------------
# Pontozas es plafon
# ---------------------------------------------------------------------------
def _many_events() -> list[detect.Event]:
    out = [detect.Event("regime_change", None, {"label": "S&P 500", "up": False, "close": 1.0, "period": 200,
                                                "sma": 2.0})]
    for n in range(10):
        s = f"S{n:02d}"
        out += [detect.Event("pattern_new", s, {"name": "Dupla alj", "direction": "bullish", "trigger": 10.0}),
                detect.Event("candidate_out", s, {"strategy": "deep"}),
                detect.Event("level_break", s, {"close": 11.0, "level": 10.5}),
                detect.Event("near_trigger", s, {"name": "Ek", "direction": "bullish", "trigger": 12.0,
                                                 "dist_atr": 0.1 * (n + 1), "earnings_days": None})]
    out.append(detect.Event("candidate_out", "OWN", {"strategy": "deep"}, holding=True))
    return out


def test_scoring_rules():
    ev = {e.symbol or "-": e for e in score.score(_many_events(), CFG) if e.type in ("near_trigger", "candidate_out")}
    w = CFG.weights
    assert ev["S00"].score == pytest.approx(w.near_trigger / 0.25)        # 0,1 ATR -> legfeljebb x4
    assert ev["S09"].score == pytest.approx(w.near_trigger / 1.0)
    assert ev["OWN"].score == pytest.approx(w.candidate_out * CFG.holding_mult)


def test_select_caps_order_and_determinism():
    events = score.score(_many_events(), CFG)
    symbols = [f"S{n:02d}" for n in range(10)] + ["OWN", "QUIET"]
    sel = score.select(events, CFG, symbols)
    assert sel["market"][0].type == "regime_change"
    assert len(sel["market"]) + len(sel["items"]) == CFG.max_items
    per = {}
    for e in sel["items"]:
        per[e.symbol] = per.get(e.symbol, 0) + 1
    assert max(per.values()) <= CFG.per_symbol
    assert sel["omitted"] == len(events) - CFG.max_items
    assert sel["unchanged"] == ["QUIET"]
    scores = [e.score for e in sel["items"]]
    assert scores == sorted(scores, reverse=True)
    shuffled = list(events)
    random.Random(3).shuffle(shuffled)
    again = score.select(shuffled, CFG, symbols)
    assert [(e.type, e.symbol) for e in again["items"]] == [(e.type, e.symbol) for e in sel["items"]]


def test_unchanged_symbols_collapse_into_one_line():
    record = service.assemble(snap({"AAA": sym(bar="2026-10-16"), "BBB": sym(bar="2026-10-16")}),
                              snap({"AAA": sym(), "BBB": sym()}, day="2026-10-15"), CFG)
    keys = [ln["key"] for ln in record["lines"]]
    assert keys == ["header", "unchanged", "quiet", "disclaimer"]
    assert record["lines"][1]["text"] == "Változás nélkül: AAA, BBB."


# ---------------------------------------------------------------------------
# Szamok, szovegezes, sablonok
# ---------------------------------------------------------------------------
def _rich_pair() -> tuple[dict, dict]:
    f = {"id": "edgar:2", "published_at": "2026-10-15T22:10:00+00:00", "items": ["4.02"], "material": True}
    f2 = {"id": "edgar:5", "published_at": "2026-10-15T23:10:00+00:00", "items": ["8.01"], "material": False}
    f3 = {"id": "edgar:6", "published_at": "2026-10-15T23:30:00+00:00", "items": [], "material": False}
    pos = {"state": "pos", "mean": 0.31, "material_neg": False, "material_event": None}
    neg = {"state": "neg", "mean": -0.27, "material_neg": True, "material_event": "leminosites"}
    prev = snap({
        "AAA": sym(close=104.37, resistance=105.12, patterns=[pattern(dist=1.7), pattern(name="Ek", start="2026-07-01")]),
        "BBB": sym(close=91.25, support=90.4, trend_ok=True, sentiment=pos, candidates=["breakout"]),
        "CCC": sym(holding=True, earnings="2026-10-21", earnings_days=8),
        "DDD": sym(patterns=[pattern(name="Bull zaszlo", trigger=51.3)]),
        "EEE": sym(earnings="2026-10-16", earnings_days=1),
    }, {"^GSPC": {"label": "S&P 500", "bar": "2026-10-15", "ok": True, "close": 6612.4, "sma": 6420.77}},
        day="2026-10-15", created="2026-10-15T04:30:00+00:00")
    cur = snap({
        "AAA": sym(bar="2026-10-16", close=106.21, atr=1.83, resistance=108.0,
                   patterns=[pattern(dist=0.64, trigger=107.42)], earnings="2026-10-20", earnings_days=4),
        "BBB": sym(bar="2026-10-16", close=89.94, support=88.1, trend_ok=False, sma=90.88, sentiment=neg,
                   candidates=["pullback"], filings=[f, f2]),
        "CCC": sym(bar="2026-10-16", holding=True, earnings="2026-10-21", earnings_days=5, filings=[f3],
                   patterns=[pattern(name="Csesze-ful", dist=6.0)]),
        "DDD": sym(bar="2026-10-16", patterns=[pattern(name="Bull zaszlo", trigger=51.3, status="triggered")]),
        "EEE": sym(bar="2026-10-16", earnings="2026-10-16", earnings_days=0),
    }, {"^GSPC": {"label": "S&P 500", "bar": "2026-10-16", "ok": False, "close": 6401.05, "sma": 6418.9}})
    return prev, cur


def _source_numbers(*snapshots: dict) -> set[str]:
    out = {str(detect.REGIME_PERIOD)}
    for s in snapshots:
        stack = [s]
        while stack:
            v = stack.pop()
            if isinstance(v, dict):
                stack.extend(v.values())
            elif isinstance(v, list):
                stack.extend(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out |= {f"{v:.2f}", f"{v:.1f}", f"{int(v)}" if float(v).is_integer() else f"{v:.2f}"}
    return out


def test_every_number_in_the_text_comes_from_the_source_data():
    prev, cur = _rich_pair()
    record = service.assemble(cur, prev, CFG.model_copy(update={"max_items": 50, "per_symbol": 10}))
    types_seen = {ln["type"] for ln in record["lines"]}
    assert {"regime_change", "own_trend", "near_trigger", "pattern_gone", "pattern_triggered", "pattern_new",
            "level_break", "level_lost", "earnings_soon", "filing_8k", "sentiment_flip", "material_news",
            "candidate_in", "candidate_out"} <= types_seen
    source = _source_numbers(prev, cur)
    for ln in record["lines"]:
        line = render.Line(ln["key"], ln["args"], ln["numeric"])
        assert line.text == ln["text"]
        assert unexplained_numbers(ln["text"], line.strings, set(line.numbers)) == [], ln["text"]
        for value in line.numbers:
            assert value.lstrip("+") in source, (value, ln["text"])


@pytest.mark.parametrize("lang", ["hu", "en", "de", "es"])
def test_rendered_digest_has_no_advice_wording(lang):
    prev, cur = _rich_pair()
    record = service.assemble(cur, prev, CFG.model_copy(update={"max_items": 50, "per_symbol": 10}))
    for ln in record["lines"]:
        text = i18n.translate(ln["text"], lang)
        assert not advice_hits(text), (lang, text)
        if lang != "hu":
            assert text != ln["text"], f"nincs forditas ({lang}): {ln['text']}"


def test_templates_have_no_literal_digits_and_are_in_the_catalog():
    rows = {" ".join(json.loads(line)["hu"].split()): json.loads(line)
            for line in i18n.CATALOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()}
    for key, template in TEMPLATES.items():
        assert not any(c.isdigit() for c in render._SLOT.sub("", template)), key
        row = rows.get(" ".join(template.split()))
        assert row is not None, f"hianyzik a katalogusbol: {template}"
        for lang in ("hu", "en", "de", "es"):
            assert not advice_hits(row[lang]), (lang, row[lang])


# ---------------------------------------------------------------------------
# Utemezes es beallitasok
# ---------------------------------------------------------------------------
def _utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


@pytest.fixture
def calendar(monkeypatch):
    holidays = {pd.Timestamp("2026-11-26")}
    monkeypatch.setattr(sessions, "trading_day", lambda day: day.weekday() < 5 and day.normalize() not in holidays)


def test_session_for_the_budapest_morning(calendar):
    assert service.todays_session(_utc("2026-10-19 04:30")) == "2026-10-19"        # hetfo 06:30 nyari ido
    assert service.todays_session(_utc("2026-10-26 05:30")) == "2026-10-26"        # EU mar teli, US meg nyari
    assert service.todays_session(_utc("2026-10-17 04:30")) is None                # szombat
    assert service.todays_session(_utc("2026-11-26 05:30")) is None                # amerikai unnepnap
    assert f"{service.for_day(_utc('2026-10-16 21:00')):%Y-%m-%d}" == "2026-10-19"  # pentek zaras utan -> hetfo


def test_due_now_catches_up_only_before_the_open(calendar, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "USERS_DIR", tmp_path / "users")
    settings = Settings()
    assert not service.due_now(settings, now=_utc("2026-10-19 04:00"))            # 06:00, meg nincs ideje
    assert service.due_now(settings, now=_utc("2026-10-19 05:00"))                 # 07:00, nincs kesz
    assert not service.due_now(settings, now=_utc("2026-10-19 14:00"))            # NY 10:00, mar nyitva
    store.save({"for_day": "2026-10-19"})
    assert not service.due_now(settings, now=_utc("2026-10-19 05:00"))


def test_monitor_trigger_runs_at_budapest_time():
    from stockwatch import monitor

    trig = monitor.schedule_triggers(Settings())["digest"]
    nxt = trig.get_next_fire_time(None, datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    assert nxt.isoformat() == "2026-10-19T06:30:00+02:00"
    nxt = trig.get_next_fire_time(None, datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc))
    assert nxt.isoformat() == "2026-10-26T06:30:00+01:00"


def test_digest_settings_validation():
    assert DigestSettings(time="6:05").time == "06:05"
    assert DigestSettings(earnings_days=[1, 7, 3, 7]).earnings_days == [7, 3, 1]
    for bad in ({"time": "25:00"}, {"max_items": 0}, {"per_symbol": 9}, {"earnings_days": []},
                {"weights": {"near_trigger": -1}}):
        with pytest.raises(ValueError):
            DigestSettings(**bad)


# ---------------------------------------------------------------------------
# Teljes futas szintetikus adaton (halozat nelkul, ideiglenes munkateruleten)
# ---------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path, monkeypatch, calendar):
    monkeypatch.setattr(workspace, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(workspace, "watchlist", lambda ws="admin": ["AAA", "BBB"])
    monkeypatch.setattr(workspace, "holding_symbols", lambda settings, ws="admin": ["BBB"])
    frames = {
        "AAA": synthetic.build([(0, 50), (300, 80), (420, 120)], seed=1),
        "BBB": synthetic.build([(0, 120), (300, 90), (420, 70)], seed=2),
        "QQQ": synthetic.build([(0, 300), (420, 480)], seed=3),
        "^GSPC": synthetic.build([(0, 4000), (420, 6500)], seed=4),
    }
    for df in frames.values():               # friss datumok: a 90 napos archivum-takaritas ne torolje
        df.index = pd.bdate_range(end="2026-10-16", periods=len(df), name="date")
    monkeypatch.setattr(service.price_store, "load",
                        lambda symbol, settings=None, universe=False: frames.get(symbol, pd.DataFrame()).copy())
    monkeypatch.setattr(service.market.EarningsCalendar, "next_date", lambda self, symbol, as_of=None: None)
    monkeypatch.setattr(detect, "momentum_candidates", lambda settings, session: None)
    sent = []

    class FakeDispatcher:
        def __init__(self, settings):
            pass

        def _in_quiet_hours(self):
            return False

        def broadcast(self, alert):
            sent.append(alert)
            return {"discord": True}

    import stockwatch.alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "Dispatcher", FakeDispatcher)
    last = frames["AAA"].index[-1]
    return {"frames": frames, "sent": sent, "last": last}


def test_full_run_is_point_in_time_saved_and_measured(env):
    days = env["frames"]["AAA"].index
    # a "most" a 400. gyertya napjanak estejen: a kesobbi gyertyak NEM latszhatnak
    day = days[400]
    now = pd.Timestamp(f"{day:%Y-%m-%d} 22:00", tz="UTC")
    record = service.build(Settings(), now=now, refresh=False, send=False)
    assert record["as_of"] == f"{day:%Y-%m-%d}"
    assert all(s["bar"] <= f"{day:%Y-%m-%d}" for s in record["symbols"].values())
    assert record["symbols"]["BBB"]["holding"] and not record["symbols"]["AAA"]["holding"]
    assert record["first"] and store.load(record["for_day"]) is not None
    assert usage.month_summary("admin")["digest"]["runs"] == 1       # a futas valodi honapja

    later = pd.Timestamp(f"{days[401]:%Y-%m-%d} 22:00", tz="UTC")
    second = service.build(Settings(), now=later, refresh=False, send=False)
    assert not second["first"] and second["previous"] == record["for_day"]
    for ln in second["lines"]:
        line = render.Line(ln["key"], ln["args"], ln["numeric"])
        assert unexplained_numbers(ln["text"], line.strings, set(line.numbers)) == []
        assert not advice_hits(ln["text"])


def test_discord_only_when_enabled_and_only_once_per_day(env):
    days = env["frames"]["AAA"].index
    now = pd.Timestamp(f"{days[402]:%Y-%m-%d} 22:00", tz="UTC")
    service.build(Settings(), now=now, refresh=False, send=True)
    assert env["sent"] == []                                       # alapbol kikapcsolva
    on = Settings(digest={"send_discord": True})
    service.build(on, now=now, refresh=False, send=True)
    service.build(on, now=now, refresh=False, send=True)           # ugyanarra a napra ujra: nem kuldi ujra
    assert len(env["sent"]) == 1
    alert = env["sent"][0]
    assert alert.event == "digest" and alert.title.startswith("Reggeli összefoglaló")
    assert "Nem befektetési tanács" in alert.body


def test_web_payload_and_housekeeping(env, monkeypatch):
    from stockwatch import housekeeping, webapp

    days = env["frames"]["AAA"].index
    record = service.build(Settings(), now=pd.Timestamp(f"{days[403]:%Y-%m-%d} 22:00", tz="UTC"),
                           refresh=False, send=False)
    monkeypatch.setattr(webapp, "load_settings", lambda: Settings())
    monkeypatch.setattr(webapp, "discord_status", lambda: {"configured": False})
    payload = webapp.digest_payload()
    assert payload["days"] == [record["for_day"]] and payload["record"]["lines"] == record["lines"]
    assert "symbols" not in payload["record"]                      # a reszletes pillanatkep nem megy ki
    assert webapp.EDITABLE["send_discord"] == ("digest", bool)
    assert any("osszefoglalo" in e.label for e in housekeeping.disk_usage(Settings()))
    assert store.prune(1, today=pd.Timestamp(record["for_day"]).date() + pd.Timedelta(days=10)) == 1
    assert store.days() == []
