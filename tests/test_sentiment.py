"""Hir- es hangulatelemzes: pontozas, point-in-time vagas, tar, forrasok, a jelzesek kiegeszitese (nem dont)."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from stockwatch import meanrev
from stockwatch.config import Settings, SentimentSettings
from stockwatch.sentiment import features, scoring, service, store
from stockwatch.sentiment.model import Article
from stockwatch.sentiment.sources import alpaca, edgar, yahoo
from tests.test_meanrev_live import uptrend_with_drop


def art(title, published, source="yahoo", kind="news", **kw) -> Article:
    a = Article(id=f"{source}:{title}:{published}", source=source, symbol="TEST", title=title, kind=kind,
                published_at=pd.Timestamp(published, tz="UTC").isoformat(), **kw)
    a.score = scoring.score(a)
    return a


# --- pontozas -----------------------------------------------------------------
@pytest.mark.parametrize("title,label,event", [
    ("Acme cuts full-year guidance as demand weakens", "negative", "elorejelzes_csokkentes"),
    ("Acme downgraded to Sell at Morgan Stanley", "negative", "leminosites"),
    ("Acme announces $500 million public offering of common stock", "negative", "kibocsatas"),
    ("Acme files for Chapter 11 bankruptcy protection", "negative", "csod"),
    ("Acme raises 2026 outlook after record quarter", "positive", "elorejelzes_emeles"),
    ("FDA approves Acme's new drug", "positive", "fda_pozitiv"),
    ("Acme to present at investor conference", "neutral", None),
    ("Why Acme Stock Is Nosediving Today", "negative", None),
])
def test_headline_scoring(title, label, event):
    s = scoring.score_text(title)
    assert s["label"] == label and s["event"] == event and s["version"] == scoring.MODEL_VERSION


def test_company_name_does_not_color_the_score():
    assert scoring.score_text("Why Super Micro (SMCI) Stock Is Nosediving", strip=["SMCI", "Super Micro Computer", "Super Micro"])["label"] == "negative"


def test_question_headline_is_less_material():
    assert scoring.score_text("Is Acme about to file for bankruptcy?")["materiality"] == 2
    assert scoring.score_text("Acme files for bankruptcy")["materiality"] == 3


def test_filing_scoring_from_8k_items():
    assert scoring.score_filing("8-K", ["4.02", "9.01"])["label"] == "negative"
    assert scoring.score_filing("8-K", ["2.02", "9.01"])["label"] == "neutral"       # eredmeny: semleges
    assert scoring.score_filing("424B4", [])["event"] == "kibocsatas"


# --- point-in-time ------------------------------------------------------------
def test_cutoff_is_4pm_new_york_and_later_news_is_invisible():
    day = pd.Timestamp("2025-03-14")                                     # pentek, EDT (UTC-4)
    assert features.decision_cutoff(day) == pd.Timestamp("2025-03-14 20:00", tz="UTC")
    before = art("Acme downgraded to Sell", "2025-03-14 19:59")
    after = art("Acme files for bankruptcy", "2025-03-14 20:01")
    f = features.daily([before, after], day, {"yahoo": [["2025-03-01T00:00:00+00:00", "2025-03-20T00:00:00+00:00"]]})
    assert f["count_3d"] == 1 and f["material"][0]["event"] == "leminosites"
    assert features.decision_day(after.published) == pd.Timestamp("2025-03-17")     # hetfo
    assert features.decision_day(before.published) == pd.Timestamp("2025-03-14")


def test_coverage_and_volume_z_need_full_window():
    day = pd.Timestamp("2025-06-02")
    arts = [art(f"Acme news item {i}", f"2025-05-{1 + i % 28:02d} 14:00") for i in range(30)]
    arts += [art(f"Acme spike {i}", "2025-06-02 15:00") for i in range(8)]
    full = {"yahoo": [["2025-01-01T00:00:00+00:00", "2025-06-03T00:00:00+00:00"]]}
    short = {"yahoo": [["2025-06-01T00:00:00+00:00", "2025-06-03T00:00:00+00:00"]]}
    f_full, f_short = features.daily(arts, day, full), features.daily(arts, day, short)
    assert f_full["coverage"] and f_full["news_volume_z"] > 2
    assert not f_short["coverage"] and f_short["news_volume_z"] is None              # nem ismert, nem 0
    assert "reszleges lefedettseg" in service.summary(f_short)
    assert service.summary(features.daily([], day, {})) == "nincs hirlefedettseg - nem ismert"


# --- tar ----------------------------------------------------------------------
def test_store_dedupes_across_sources_keeping_earliest():
    a1 = art("Acme downgraded to Sell", "2025-03-14 15:00", source="yahoo")
    a2 = art("ACME downgraded to sell!", "2025-03-14 14:00", source="alpaca")
    store.merge("TEST", "yahoo", [a1], ("2025-03-01T00:00:00+00:00", "2025-03-15T00:00:00+00:00"))
    res = store.merge("TEST", "alpaca", [a2], ("2025-03-10T00:00:00+00:00", "2025-03-16T00:00:00+00:00"))
    arts = store.articles("TEST")
    assert res["added"] == 0 and len(arts) == 1
    assert arts[0].published_at.startswith("2025-03-14T14:00") and arts[0].also == ["alpaca"]
    store.merge("TEST", "yahoo", [], ("2025-03-14T00:00:00+00:00", "2025-03-20T00:00:00+00:00"))
    assert store.load("TEST")["coverage"]["yahoo"] == [["2025-03-01T00:00:00+00:00", "2025-03-20T00:00:00+00:00"]]


def test_old_model_version_is_rescored():
    a = art("Acme downgraded", "2025-03-14 15:00")
    a.score = {"version": "regi", "label": "positive"}
    store.merge("TEST", "yahoo", [], None)
    data = store.load("TEST")
    data["articles"].append(a.to_dict())
    store._write("TEST", data)
    assert store.articles("TEST")[0].score["label"] == "negative"


# --- forrasok -----------------------------------------------------------------
def test_edgar_acceptance_time_is_conservative():
    t = edgar.accepted_utc("2024-10-30T16:51:00.000Z", "2024-10-30")
    assert t == pd.Timestamp("2024-10-30 20:51", tz="UTC")                 # a NY-falido ertelmezes a kesobbi
    assert features.decision_day(t) == pd.Timestamp("2024-10-31")


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(self.status_code)


def test_edgar_fetch_parses_filings_and_skips_routine_424b5(monkeypatch):
    monkeypatch.setattr(edgar, "ticker_map", lambda *a, **k: {"TEST": {"cik": 123, "name": "Test Corp"}})
    n = 15
    recent = {"accessionNumber": [f"0001-{i}" for i in range(n + 2)],
              "filingDate": ["2025-03-01"] * n + ["2025-02-01", "2025-01-15"],
              "acceptanceDateTime": ["2025-03-01T10:00:00.000Z"] * n + ["2025-02-01T21:00:00.000Z", ""],
              "form": ["424B5"] * n + ["8-K", "10-Q"], "items": [""] * n + ["4.02,9.01", ""],
              "primaryDocument": ["d.htm"] * (n + 2), "primaryDocDescription": [""] * (n + 2)}
    monkeypatch.setattr(edgar, "_get", lambda url, session=None: {"filings": {"recent": recent, "files": []}})
    arts, cov = edgar.fetch("TEST", pd.Timestamp("2025-01-01", tz="UTC"))
    assert [a.form for a in arts] == ["8-K"] and arts[0].items == ["4.02", "9.01"]       # rutinszeru 424B5 kimarad
    assert cov[0].startswith("2025-01-15")


def test_yahoo_relevance_uses_title_not_related_tickers():
    assert yahoo.relevant("NVDA", "Nvidia CEO speaks", ["NVDA"], "NVIDIA")
    assert yahoo.relevant("NVDA", "Why NVDA fell today", [], None)
    assert not yahoo.relevant("NVDA", "Own Colgate-Palmolive for the dividend", ["CL", "NVDA"], "NVIDIA")
    items = [{"uuid": "u1", "title": "Acme downgraded", "providerPublishTime": 1741975200, "relatedTickers": ["ACME"]},
             {"uuid": "u2", "title": "Market wrap", "providerPublishTime": 1741971600, "relatedTickers": ["ACME", "X"]}]
    arts, cov = yahoo.fetch("ACME", pd.Timestamp("2025-01-01", tz="UTC"), company="Acme", search=lambda q: items)
    assert [a.id for a in arts] == ["yahoo:u1"] and cov[0].startswith("2025-03-14T17:00")


def test_alpaca_paginates_and_handles_missing_access(monkeypatch):
    monkeypatch.setattr(alpaca, "credentials", lambda: ("k", "s"))
    pages = [{"news": [{"id": 1, "headline": "Acme downgraded", "created_at": "2025-03-14T14:00:00Z", "symbols": ["ACME"]}],
              "next_page_token": "p2"},
             {"news": [{"id": 2, "headline": "Acme raises outlook", "created_at": "2025-03-15T14:00:00Z"}],
              "next_page_token": None}]
    calls = []
    session = SimpleNamespace(get=lambda url, params, headers, timeout: calls.append(dict(params)) or FakeResp(pages[len(calls) - 1]))
    arts, cov = alpaca.fetch("ACME", pd.Timestamp("2025-01-01", tz="UTC"), session=session)
    assert [a.id for a in arts] == ["alpaca:1", "alpaca:2"] and calls[1]["page_token"] == "p2" and cov
    denied = SimpleNamespace(get=lambda *a, **k: FakeResp({}, 403))
    assert alpaca.fetch("ACME", pd.Timestamp("2025-01-01", tz="UTC"), session=denied) == ([], None)
    monkeypatch.setattr(alpaca, "credentials", lambda: (None, None))
    assert alpaca.fetch("ACME", pd.Timestamp("2025-01-01", tz="UTC")) == ([], None)


# --- a jelzesek kiegeszitese --------------------------------------------------
def test_annotate_adds_non_deciding_check_and_alert_line():
    settings = Settings()
    df = uptrend_with_drop()
    sig = meanrev.evaluate("TEST", df, settings, settings.strategies.pullback.params, None)
    passed, qualifies = sig.passed, sig.qualifies(settings.entry)
    day = sig.as_of
    store.merge("TEST", "yahoo", [art("TEST downgraded to Sell", f"{day:%Y-%m-%d} 15:00")],
                (f"{day - pd.Timedelta(days=10):%Y-%m-%d}T00:00:00+00:00", f"{day + pd.Timedelta(days=1):%Y-%m-%d}T00:00:00+00:00"))
    assert service.annotate([sig], settings) == 1
    check = [c for c in sig.checks if c.name == service.CHECK_NAME][0]
    assert check.passed is None and "LENYEGES NEGATIV: leminosites" in check.detail
    assert sig.passed == passed and sig.qualifies(settings.entry) == qualifies         # nem dont
    assert sig.to_dict()["news"]["material_neg_3d"] is True
    assert "Hirek (tajekoztato, nem dont): LENYEGES NEGATIV" in meanrev.format_body(sig, settings.strategies.pullback.params)
    assert service.annotate([sig], settings) == 0                                      # nem duplaz
    off = settings.model_copy(update={"sentiment": SentimentSettings(enabled=False)})
    sig2 = meanrev.evaluate("TEST", df, settings, settings.strategies.pullback.params, None)
    assert service.annotate([sig2], off) == 0 and sig2.news is None


def test_settings_reject_unknown_source():
    with pytest.raises(ValueError):
        SentimentSettings(sources=["twitter"])
