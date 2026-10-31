"""Gyakorlo mod (practice/): szivargas (a felfedes elotti API-valasz nem arulja el a papirt, a datumot, a nyers
arat es volument), a szimulacio egyezik a kozvetlen meanrev.simulate_trade hivassal, a napkozbeni cel
szabalyai, az alapviselkedes valtozatlan, holdout, seed / megosztasi kod, osszegzes szovegezese, a Kitores-osszevetes
a keszletben (labor nelkul)."""
from __future__ import annotations

import gzip
import json
import math
import pickle
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry_research as er
from stockwatch import accounts, i18n, market, meanrev, webapp, webauth
from stockwatch.config import PullbackParams, Settings
from stockwatch.practice import breakouts, sampler, scoring, session, store, synthetic
from stockwatch.practice.templates import TEMPLATES

from . import agent_synthetic as syn
from .conftest import make_user
from .guards import advice_hits, unexplained_numbers

TICKERS = ["ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR"]
PW = "helyes-lo-elem-tuzfal"                       # a conftest.make_user alapjelszava
_FRAMES: dict = {}


def frames() -> dict[str, pd.DataFrame]:
    """Magas arszint (~2000+), hogy a normalizalt (~100) es a nyers arak ne eshessenek egybe veletlenul."""
    if not _FRAMES:
        for n, symbol in enumerate(TICKERS):
            _FRAMES[symbol] = syn.frame(300 + n, price=2000.0 + 150 * n)
        _FRAMES["QQQ"] = syn.frame(7, drift=0.0009, vol=0.005, price=150)
        _FRAMES["^GSPC"] = syn.frame(8, drift=0.0008, vol=0.005, price=2200)
    return _FRAMES


@pytest.fixture
def env(monkeypatch, user):
    data = frames()
    # a teszt-tickerek a sajat sorukbol; a szintetikus papirok (pl. az alakzat-feladat, #51) a generatorbol
    monkeypatch.setattr(sampler, "load_prices", lambda symbol, settings: data[symbol].loc[:sampler.VISIBLE_END].copy()
                        if symbol in data else synthetic.frame(symbol) if synthetic.is_synthetic(symbol) else None)
    monkeypatch.setattr(sampler, "universe", lambda settings: list(TICKERS))
    ctx = market.MarketContext({"QQQ": data["QQQ"], "^GSPC": data["^GSPC"]}, frozenset(TICKERS), frozenset())
    monkeypatch.setattr(market, "load_context", lambda settings, refresh=True, allow_network=None: ctx)
    monkeypatch.setattr(market, "HistoricalEarnings", syn.NoEarnings)
    settings = Settings()
    monkeypatch.setattr(session, "load_settings", lambda: settings)
    monkeypatch.setattr(webapp, "load_settings", lambda: settings)
    pool = sampler.build(settings)
    return {"frames": data, "settings": settings, "pool": pool, "user": user}


_COOKIE: dict[str, str] = {}                     # a `server` fixture bejelentkezett felhasznalojanak sutije


def login_cookie(email: str = "anna@example.com") -> str:
    return f"{webauth.COOKIE}={accounts.login(email, PW, '127.0.0.1')}"


@pytest.fixture
def server(env):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webapp.make_handler())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _COOKIE["default"] = login_cookie()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    _COOKIE.clear()
    httpd.shutdown()
    httpd.server_close()


def _call(url: str, body: dict | None = None, cookie: str | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"X-StockWatch": "1", "Content-Type": "application/json",
                                          "Cookie": cookie or _COOKIE.get("default", "")})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


# ---------------------------------------------------------------------------
# meanrev.simulate_trade: alapbol valtozatlan, a napkozbeni cel szabalyai
# ---------------------------------------------------------------------------
def test_simulate_trade_default_is_unchanged():
    df = syn.frame(5)
    params = PullbackParams()
    feat = meanrev.features(df, params)
    exits = meanrev.exit_flags(feat, params).to_numpy()
    mask = meanrev.setup_mask(feat, params, None).to_numpy().copy()
    mask[: meanrev.WARMUP] = False
    signals = np.flatnonzero(mask)
    assert len(signals) > 10
    for i in signals:
        base = meanrev.simulate_trade("X", feat, exits, int(i), params).to_dict()
        explicit = meanrev.simulate_trade("X", feat, exits, int(i), params, target=None, target_intraday=False).to_dict()
        target_off = meanrev.simulate_trade("X", feat, exits, int(i), params,
                                            target=float(feat["close"].iloc[i]) * 1.01).to_dict()
        assert base == explicit == target_off


def _bars(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range("2020-01-01", periods=len(rows)))
    df["atr"], df["rsi2"], df["natr"] = 1.0, 1.0, 1.0
    return df


@pytest.mark.parametrize("third, price, reason", [
    ([100, 111, 94, 100], 95.0, "stop"),           # a stop es a cel ugyanazon a gyertyan -> stop
    ([112, 113, 111, 112], 112.0, "target"),       # a nyito a cel felett -> a nyitoaron
    ([100, 111, 99, 105], 110.0, "target"),        # napkozben eleri -> a celaron
])
def test_intraday_target_rules(third, price, reason):
    feat = _bars([[100, 101, 99, 100], [100, 101, 99.5, 100], third, [100, 101, 99, 100]])
    params = PullbackParams(time_stop=10, cost_bps=0)
    trade = meanrev.simulate_trade("X", feat, np.zeros(4, bool), 0, params, levels=(math.inf, 95.0), target=110.0,
                                   target_intraday=True)
    assert (trade.exit_price, trade.reason, trade.exit_date) == (price, reason, feat.index[2])


def test_entry_day_target_only_for_open_fills():
    rows = [[100, 101, 99, 100], [100, 112, 98, 100], [100, 101, 99.5, 100], [100, 101, 99.5, 100]]
    params = PullbackParams(time_stop=1, cost_bps=0)
    intraday = meanrev.simulate_trade("X", _bars(rows), np.zeros(4, bool), 0, params, levels=(99.0, 90.0),
                                      target=110.0, target_intraday=True)
    assert intraday.entry_price == 99.0 and intraday.reason == "time"          # a csucs a teljesules elott lehetett
    at_open = meanrev.simulate_trade("X", _bars(rows), np.zeros(4, bool), 0, params, levels=(101.0, 90.0),
                                     target=110.0, target_intraday=True)
    assert (at_open.entry_price, at_open.exit_price, at_open.reason) == (100.0, 110.0, "target")


# ---------------------------------------------------------------------------
# Feladatkeszlet, seed, megosztasi kod
# ---------------------------------------------------------------------------
def test_pool_points_have_warmup_and_end_before_the_holdout(env):
    pool = env["pool"]
    assert pool["counts"]["strategy"] > 0 and pool["counts"]["nothing"] > 0
    for cat, rows in pool["entries"].items():
        for symbol, day, source in rows:
            df = env["frames"][symbol].loc[:sampler.VISIBLE_END]
            i = df.index.get_loc(pd.Timestamp(day))
            assert i >= sampler.WARMUP_BARS
            assert i + sampler.FUTURE_BARS < len(df) and df.index[i + sampler.FUTURE_BARS] < er.HOLDOUT_START
            if cat == "strategy":
                assert source in ("pullback", "breakout")
    again = sampler.build(env["settings"])
    assert again.get("unchanged") and again["fingerprint"] == pool["fingerprint"]


def test_seed_and_share_code_are_reproducible(env):
    pool = env["pool"]
    assert sampler.sample(pool, 7, 10) == sampler.sample(pool, 7, 10)
    assert sampler.sample(pool, 7, 10) != sampler.sample(pool, 8, 10)
    assert len({p["symbol"] for p in sampler.sample(pool, 7, 6)}) == 6           # egy papir egyszer
    first = session.create(env["user"], env["settings"], seed=7, rounds=6)
    assert not re.search(r"\d", first["code"])
    second = session.create(env["user"], env["settings"], code=first["code"])
    a, b = (store.load_session(x["id"], env["user"]) for x in (first, second))
    assert [(t["symbol"], t["date"]) for t in a["tasks"]] == [(t["symbol"], t["date"]) for t in b["tasks"]]
    assert second["warning"] is None and second["rounds"] == 6
    store.save_pool(pool | {"fingerprint": "f" * 16})
    assert session.create(env["user"], env["settings"], code=first["code"])["warning"] == "other_pool"
    with pytest.raises(ValueError):
        sampler.parse_code("abc-def")


# ---------------------------------------------------------------------------
# Szivargas: a teljes API-valasz a felfedes elott
# ---------------------------------------------------------------------------
def _numbers(text: str) -> set[float]:
    return {float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text)}


def test_api_hides_symbol_date_and_raw_prices_before_reveal(env, server):
    status, created_text = _call(server + "/api/practice/session", {"rounds": 6, "seed": 11})
    assert status == 200, created_text
    created = json.loads(created_text)["session"]
    texts = [created_text.replace(created["created"], "")]
    status, listing = _call(server + "/api/practice")
    charts = []
    for k in range(created["rounds"]):
        status, text = _call(server + f"/api/practice/session/{created['id']}/task/{k}")
        assert status == 200, text
        texts.append(text)
        charts.append(json.loads(text)["chart"])
    stored = store.load_session(created["id"], env["user"])
    raw_prices, raw_volumes = set(), set()
    for symbol in {t["symbol"] for t in stored["tasks"]}:
        df = env["frames"][symbol]
        raw_prices |= set(np.round(df[["open", "high", "low", "close"]].to_numpy().ravel(), 2).tolist())
        raw_volumes |= set(np.round(df["volume"].to_numpy()).tolist())
    for text in texts:
        for ticker in [*TICKERS, "QQQ", "SPY", "GSPC"]:
            assert ticker not in text
        assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
        assert not re.search(r"(?<![\d.])(19[89]\d|20[0-2]\d|2030)(?![\d])", text)
        nums = _numbers(text)
        assert not nums & raw_prices and not nums & raw_volumes
        assert "strategy" not in text and "pullback" not in text and "category" not in text
    # a lista (a keszlet ideje, ujjlenyomata, a munkasor is benne van): papir es feladat-datum nincs benne
    for ticker in [*TICKERS, "QQQ", "SPY", "GSPC"]:
        assert ticker not in listing
    assert created["id"] in listing and not any(t["date"] in listing for t in stored["tasks"])
    for chart in charts:
        bars = chart["bars"]
        assert len(bars) == sampler.VISIBLE_BARS and bars[-1]["i"] == 0 and bars[0]["i"] == -119
        assert bars[-1]["c"] == 100.0 and 30 < max(b["h"] for b in bars) < 300
    # a felfedes utan (es csak utana) jon a papir es a datum
    status, text = _call(server + f"/api/practice/session/{created['id']}/task/0/decision", {"action": "skip"})
    assert status == 200 and stored["tasks"][0]["symbol"] in text and stored["tasks"][0]["date"] in text


# ---------------------------------------------------------------------------
# Szimulacio es dontes
# ---------------------------------------------------------------------------
def test_scoring_matches_direct_simulate_trade(env):
    created = session.create(env["user"], env["settings"], seed=21, rounds=4)
    task = session.task_payload(env["user"], created["id"], 0)
    atr = task["chart"]["atr"]
    decision = {"action": "enter", "entry": "limit", "limit": 99.0, "stop": round(99 - 2 * atr, 2),
                "target": round(99 + 3 * atr, 2), "days": 12}
    rev = session.decide(env["user"], created["id"], 0, decision)["reveal"]
    t = store.load_session(created["id"], env["user"])["tasks"][0]
    df = env["frames"][t["symbol"]].loc[:sampler.VISIBLE_END]
    i = df.index.get_loc(pd.Timestamp(t["date"]))
    scale = 100 / float(df["close"].iloc[i])
    cost = scoring.cost_bps(env["settings"])
    feat = meanrev.features(df, env["settings"].strategies.pullback.params)
    direct = meanrev.simulate_trade("X", feat, np.zeros(len(feat), bool), i,
                                    PullbackParams(time_stop=12, cost_bps=cost, commission_bps=0),
                                    levels=(99.0 / scale, decision["stop"] / scale), target=decision["target"] / scale,
                                    target_intraday=True)
    user = rev["user"]
    assert user["status"] == direct.status and user["reason"] == (direct.reason or None)
    if direct.status == "closed":
        assert user["exit"] == round(direct.exit_price * scale, 2) and user["entry"] == round(direct.entry_price * scale, 2)
        expected = scoring.r_multiple(direct.entry_price, direct.exit_price, direct.stop, 99.0 / scale, cost)
        assert user["r"] == pytest.approx(round(expected, 3))
    assert len(rev["future"]) == sampler.FUTURE_BARS
    assert df.index[i + len(rev["future"])] < er.HOLDOUT_START
    assert rev["baseline"]["n"] > 0 and {s["strategy"] for s in rev["strategies"]} == {"pullback", "deep", "breakout", "short"}


def test_decision_is_validated_and_final(env, server):
    created = session.create(env["user"], env["settings"], seed=5, rounds=3)
    with pytest.raises(ValueError):
        session.decide(env["user"], created["id"], 0, {"action": "enter", "entry": "open", "stop": 101, "days": 5})
    with pytest.raises(ValueError):
        session.decide(env["user"], created["id"], 0, {"action": "enter", "entry": "open", "stop": 95, "days": 31})
    session.decide(env["user"], created["id"], 0, {"action": "enter", "entry": "open", "stop": 95, "days": 5})
    with pytest.raises(ValueError):
        session.decide(env["user"], created["id"], 0, {"action": "skip"})
    url = server + f"/api/practice/session/{created['id']}/task/0/decision"
    assert _call(url, {"action": "skip"})[0] == 400
    assert _call(server + f"/api/practice/session/{created['id']}/task/9")[0] == 404
    assert _call(server + "/api/practice/session/nincsilyenxx/task/0")[0] == 404


def _lab_files(days: list, closed: dict) -> None:
    """A labor kimeneti fajljai (kitores-cache + az ELO kiserlet kotesei), ahogy a labor irja oket."""
    (breakouts.LAB_DIR / "events").mkdir(parents=True)
    (breakouts.LAB_DIR / "events" / "ALFA.pkl").write_bytes(pickle.dumps({"events": [(d, 20, None) for d in days]}))
    (breakouts.LAB_DIR / "experiments.json").write_text(json.dumps({"experiments": [{"id": "live1", "live": True}]}))
    (breakouts.LAB_DIR / "results").mkdir()
    rows = [[f"{d:%Y-%m-%d}", "ALFA", "Dupla alj", "breakout", r, "target", 7] for d, r in closed.items()]
    (breakouts.LAB_DIR / "results" / "live1.json.gz").write_bytes(gzip.compress(json.dumps(rows).encode()))


def test_breakout_compare_reads_the_lab_output():
    df = syn.frame(9)
    day = df.index[400]
    _lab_files([day], {day: 1.5})
    days, results = breakouts.days("ALFA"), breakouts.live_results()
    assert breakouts.compare("ALFA", df, 400, days, results) == {"strategy": "breakout", "signaled": True,
                                                                  "status": "closed", "r": 1.5, "reason": "target",
                                                                  "held": 7, "kind": "breakout"}
    assert breakouts.compare("BRVO", df, 400, days, results)["status"] == "filtered"
    assert not breakouts.compare("ALFA", df, 401, days, results)["signaled"]
    assert breakouts.compare("ALFA", df, 400, days, {})["status"] == "unknown"


def test_breakout_comparison_is_stored_in_the_pool_and_works_without_the_lab(env, monkeypatch):
    df = env["frames"]["ALFA"].loc[:sampler.VISIBLE_END]
    days = [df.index[i] for i in np.flatnonzero(sampler.eligible(df))[::60][:4]]
    _lab_files(days, {days[0]: 1.5})
    monkeypatch.setitem(sampler.PER_SYMBOL, "strategy", 10_000)                # minden jelzes a keszletbe
    pool = sampler.build(env["settings"], force=True)
    stored = {k.split("|")[1]: v for k, v in pool["breakouts"].items() if k.startswith("ALFA|")}
    assert all(len(e) == 3 for c in pool["entries"].values() for e in c)       # a bejegyzesek formaja valtozatlan
    first = f"{days[0]:%Y-%m-%d}"
    assert set(stored) == {f"{d:%Y-%m-%d}" for d in days}
    assert stored[first]["status"] == "closed" and stored[first]["r"] == 1.5
    assert all(v["status"] == "filtered" for k, v in stored.items() if k != first)
    # a labor fajljai nelkul (pl. az oktatasi szerveren) a kor a keszletbol kapja az osszevetest
    for path in sorted(breakouts.LAB_DIR.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    store.save_pool({"fingerprint": "0" * 16, "breakouts": {f"ALFA|{first}": stored[first]},
                     "entries": {"strategy": [["ALFA", first, "breakout"]], "pattern": [], "short": [], "nothing": []}})
    created = session.create(env["user"], env["settings"], seed=1, rounds=1, mix={"strategy": 1})
    out = session.decide(env["user"], created["id"], 0, {"action": "skip"})
    assert next(s for s in out["reveal"]["strategies"] if s["strategy"] == "breakout") == stored[first]


# ---------------------------------------------------------------------------
# Osszegzes, szovegezes
# ---------------------------------------------------------------------------
def _play(env, rounds: int = 8) -> dict:
    created = session.create(env["user"], env["settings"], seed=33, rounds=rounds)
    for k in range(rounds):
        atr = session.task_payload(env["user"], created["id"], k)["chart"]["atr"]
        if k % 3 == 0:
            body = {"action": "skip"}
        elif k % 3 == 1:
            body = {"action": "enter", "entry": "open", "stop": round(100 - 0.7 * atr, 2), "days": 10}
        else:
            body = {"action": "enter", "entry": "limit", "limit": 99.5, "stop": round(99.5 - 2.5 * atr, 2),
                    "target": round(99.5 + 4 * atr, 2), "days": 20}
        session.decide(env["user"], created["id"], k, body)
    return session.summary_payload(env["user"], created["id"])


@pytest.mark.parametrize("lang", ["hu", "en", "de", "es"])
def test_summary_numbers_come_from_the_data_and_no_advice(env, lang):
    out = _play(env)
    s = out["summary"]
    assert out["session"]["done"] and s["decided"] == 8 and s["skipped"] == 3 and s["entered"] == 5
    assert s["tight_share"] is not None and sum(b["n"] for b in s["stop_buckets"]) == 5
    for ln in out["lines"]:
        line = session.Line(ln["key"], ln["args"], ln["numeric"])
        strings = [a for i, a in enumerate(line.args) if i not in line.numeric]
        numbers = {a for i, a in enumerate(line.args) if i in line.numeric}
        assert unexplained_numbers(ln["text"], strings, numbers) == [], ln["text"]
        text = i18n.translate(ln["text"], lang)
        assert not advice_hits(text), text
        if lang != "hu":
            assert text != ln["text"], f"nincs forditas ({lang}): {ln['text']}"


def test_templates_digit_free_and_in_catalog():
    rows = {" ".join(json.loads(x)["hu"].split()) for x in i18n.CATALOG_PATH.read_text(encoding="utf-8").splitlines() if x}
    formulas = {"l_ema_formula", "l_rsi", "l_r"}                    # keplet-allandok (a lecke levezetese)
    for key, template in TEMPLATES.items():
        text = re.sub(r"\{\d+(:n)?\}|EMA\d+|RSI\(\d\)", "", template)       # indikatornevek
        assert key in formulas or not re.search(r"\d", text), key
        assert " ".join(template.split()) in rows, template


# ---------------------------------------------------------------------------
# Tarolas, munkasor, webes payload
# ---------------------------------------------------------------------------
def test_sessions_are_capped_per_user(env, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSIONS", 3)
    other = make_user("bela@example.com")
    theirs = session.create(other, env["settings"], seed=1, rounds=1)
    ids = [session.create(env["user"], env["settings"], seed=n, rounds=1)["id"] for n in range(5)]
    assert [s["id"] for s in session.listing(env["user"])] == ids[:-4:-1]      # a 3 legujabb, a legujabb elol
    assert store.load_session(theirs["id"], other) is not None                 # masik felhasznalot nem erinti
    with pytest.raises(ValueError):
        store.load_session("../../etc", env["user"])


def test_user_cannot_see_or_change_another_users_session(env, server):
    created = session.create(env["user"], env["settings"], seed=4, rounds=2)
    make_user("bela@example.com")
    bela = login_cookie("bela@example.com")
    sid = created["id"]
    assert _call(server + f"/api/practice/session/{sid}/task/0", cookie=bela)[0] == 404
    assert _call(server + f"/api/practice/session/{sid}/summary", cookie=bela)[0] == 404
    assert _call(server + f"/api/practice/session/{sid}/task/0/decision", {"action": "skip"}, cookie=bela)[0] == 404
    assert _call(server + "/api/practice/delete", {"id": sid}, cookie=bela)[0] == 404
    status, listing = _call(server + "/api/practice", cookie=bela)
    assert status == 200 and sid not in listing
    assert session.task_payload(env["user"], sid, 0)["decided"] is False          # a sajat kore erintetlen
    assert _call(server + f"/api/practice/session/{sid}/task/0", cookie="sw_session=hamis")[0] == 401


def test_symbol_without_eligible_points_does_not_stop_the_pool(env):
    df = env["frames"]["ALFA"].loc[:sampler.VISIBLE_END].copy()
    df["volume"] = 0.0                                                      # egyetlen tiszta ablak sincs
    out = sampler.symbol_entries("ALFA", df, env["settings"], None, [])
    assert out["breakouts"] == {} and not any(out[c] for c in sampler.CATEGORIES)


def test_web_payload_and_no_pool_build_from_the_web(env):
    payload = webapp.practice_payload(env["user"])
    assert payload["pool"]["counts"] == env["pool"]["counts"] and payload["defaults"]["max_days"] == 30
    with pytest.raises(ValueError):
        webapp.practice_action("pool", {}, env["user"])     # a keszletet csak a CLI epiti
