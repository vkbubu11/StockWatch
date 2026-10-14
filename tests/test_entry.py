"""A swing long belepesi motor tesztjei.

Szandekosan kezzel epitett, ismert geometriaju adatokon: itt nem a
formaciofelismerest teszteljuk (azt a test_patterns teszi), hanem hogy a
belepesi szabalyok PONTOSAN azt csinaljak-e, amit a jelzes allit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from stockwatch import entry, indicators, market, positions
from stockwatch.alerts.base import Alert
from stockwatch.alerts.webhook import WebhookChannel
from stockwatch.config import EntrySettings, Settings
from stockwatch.forecast import Forecast
from stockwatch.patterns.base import PatternMatch


# ---------------------------------------------------------------------------
# Segedek
# ---------------------------------------------------------------------------
def uptrend(bars: int = 320, start: float = 50.0, drift: float = 0.0025, seed: int = 1) -> pd.DataFrame:
    """Egyenletes emelkedo trend enyhe zajjal (SMA200/EMA-khoz eleg hosszu)."""
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + drift + rng.normal(0, 0.006, bars))
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * 1.004
    lows = np.minimum(opens, closes) * 0.996
    volume = np.full(bars, 1e6)
    index = pd.bdate_range("2022-01-03", periods=bars, name="date")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=index
    )


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    return indicators.enrich(df, 14)


def make_match(df: pd.DataFrame, trigger: float, status="triggered", trigger_pos=-1,
               target_mult=0.12, invalidation_mult=0.06, direction="bullish") -> PatternMatch:
    return PatternMatch(
        symbol="TEST",
        name="Bull zaszlo",
        direction=direction,
        status=status,
        quality=0.8,
        trigger_level=trigger,
        target=trigger * (1 + target_mult),
        invalidation=trigger * (1 - invalidation_mult),
        start_date=df.index[-40],
        end_date=df.index[-5] if status == "forming" else df.index[trigger_pos - 1],
        trigger_date=df.index[trigger_pos] if status == "triggered" else None,
    )


def lenient() -> EntrySettings:
    """Minden kemeny szuro laza - az egyes teszteket egyenkent szigoritjuk."""
    return EntrySettings(
        rsi_min=0, rsi_max=100, min_breakout_volume=0, max_extension_atr=100,
        min_risk_reward=0, max_stop_pct=100, regime_filter=False, earnings_blackout_days=0,
        require_uptrend=False, confirm_breakout=False, alert_retests=True, min_stop_atr=1.5,
    )


def settings_with(cfg: EntrySettings) -> Settings:
    return Settings(entry=cfg)


# ---------------------------------------------------------------------------
# Helyzetfelismeres
# ---------------------------------------------------------------------------
def test_breakout_today_is_breakout():
    df = enrich(uptrend())
    match = make_match(df, trigger=float(df["close"].iloc[-1]) * 0.99)
    assert entry.classify(match, df, lenient()) == "breakout"


def test_confirmed_breakout_needs_second_close_above():
    df = enrich(uptrend())
    cfg = lenient()
    cfg.confirm_breakout = True
    level = float(df["close"].iloc[-1]) * 0.99
    # tegnap tort ki, ma is felette zart -> megerositett kitores
    yesterday = make_match(df, trigger=level, trigger_pos=-2)
    assert entry.classify(yesterday, df, cfg) == "breakout"
    # a kitoresi napon meg NINCS jelzes
    today = make_match(df, trigger=level, trigger_pos=-1)
    assert entry.classify(today, df, cfg) is None
    # ha a mai zaro visszaesett a szint ala: nincs megerosites
    above = make_match(df, trigger=float(df["close"].iloc[-1]) * 1.01, trigger_pos=-2)
    assert entry.classify(above, df, cfg) is None


def test_retests_off_by_default():
    assert EntrySettings().alert_retests is False
    assert EntrySettings().confirm_breakout is True
    assert EntrySettings().exit_mode == "partial"


def test_bearish_pattern_is_ignored():
    df = enrich(uptrend())
    match = make_match(df, trigger=float(df["close"].iloc[-1]) * 0.99, direction="bearish")
    assert entry.classify(match, df, lenient()) is None


def test_old_breakout_without_retest_is_ignored():
    """Egy 5 napja kitort, azota elfutott minta NEM mai belepo."""
    df = enrich(uptrend(drift=0.01))
    trigger = float(df["close"].iloc[-6]) * 0.99
    match = make_match(df, trigger=trigger, trigger_pos=-6)
    assert entry.classify(match, df, lenient()) is None


def test_retest_detected():
    raw = uptrend()
    level = float(raw["close"].iloc[-6])
    # kitores 5 napja, visszahuzodas a szintre, ma felfele zaro gyertya
    raw.iloc[-5:, raw.columns.get_loc("close")] = [level * 1.03, level * 1.02, level * 1.01, level * 1.005, level * 1.02]
    raw.iloc[-5:, raw.columns.get_loc("open")] = [level * 1.01, level * 1.03, level * 1.02, level * 1.01, level * 1.003]
    raw["high"] = raw[["open", "close"]].max(axis=1) * 1.003
    raw["low"] = raw[["open", "close"]].min(axis=1) * 0.997
    df = enrich(raw)
    match = make_match(df, trigger=level * 0.999, trigger_pos=-5)
    assert entry.classify(match, df, lenient()) == "retest"


def test_retest_rejected_after_close_below_level():
    """Ha kozben zaro a szint ala ment, az hamis kitores, nem visszateszt."""
    raw = uptrend()
    level = float(raw["close"].iloc[-6])
    raw.iloc[-5:, raw.columns.get_loc("close")] = [level * 1.03, level * 0.97, level * 1.00, level * 1.005, level * 1.02]
    raw.iloc[-5:, raw.columns.get_loc("open")] = [level * 1.01, level * 1.02, level * 0.98, level * 1.00, level * 1.003]
    raw["high"] = raw[["open", "close"]].max(axis=1) * 1.003
    raw["low"] = raw[["open", "close"]].min(axis=1) * 0.997
    df = enrich(raw)
    match = make_match(df, trigger=level * 0.999, trigger_pos=-5)
    assert entry.classify(match, df, lenient()) is None


def test_setup_only_within_atr_distance():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    near = make_match(df, trigger=close + 0.5 * atr, status="forming")
    far = make_match(df, trigger=close + 3 * atr, status="forming")
    assert entry.classify(near, df, lenient()) == "setup"
    assert entry.classify(far, df, lenient()) is None


# ---------------------------------------------------------------------------
# Terv es meretezes
# ---------------------------------------------------------------------------
def test_position_size_math():
    cfg = EntrySettings(account_size=10_000, risk_per_trade_pct=1.0, max_position_pct=100)
    # 100 $ kockazat / 2 $ reszvenyenkent = 50 db
    assert entry.position_size(100.0, 98.0, cfg) == 50


def test_position_size_capped_by_max_position():
    cfg = EntrySettings(account_size=10_000, risk_per_trade_pct=1.0, max_position_pct=25)
    # kockazat alapjan 1000 db lenne, de 2500 $ / 100 $ = 25 db a plafon
    assert entry.position_size(100.0, 99.9, cfg) == 25


def test_position_size_zero_for_invalid_stop():
    assert entry.position_size(100.0, 101.0, EntrySettings()) == 0


def test_stop_at_least_min_atr_below_entry():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    # az invalidacio nagyon kozel van - a stopnak legalabb 1.5 ATR-re kell kerulnie
    match = make_match(df, trigger=close * 0.995, invalidation_mult=0.0)
    match.invalidation = close - 0.2 * atr
    plan = entry.build_plan(match, "breakout", df, EntrySettings(min_stop_atr=1.5))
    assert plan.stop == pytest.approx(close - 1.5 * atr)
    assert plan.entry == pytest.approx(close)
    assert plan.entry_limit == pytest.approx(close * 1.01)


def test_setup_plan_enters_at_trigger():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    match = make_match(df, trigger=close + 0.5 * atr, status="forming")
    plan = entry.build_plan(match, "setup", df, EntrySettings())
    assert plan.entry == pytest.approx(match.trigger_level)


# ---------------------------------------------------------------------------
# Kemeny szurok
# ---------------------------------------------------------------------------
def _signal(df, cfg, match=None, **kwargs):
    match = match or make_match(df, trigger=float(df["close"].iloc[-1]) * 0.99)
    signals = entry.evaluate("TEST", df, [match], settings_with(cfg), **kwargs)
    assert signals, "a mintanak ertekelhetonek kell lennie"
    return signals[0]


def test_lenient_breakout_passes():
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    assert signal.passed
    assert 0 <= signal.score <= 100


def test_downtrend_fails_trend_filter():
    df = enrich(uptrend(drift=-0.002))
    cfg = lenient()
    cfg.require_uptrend = True
    signal = _signal(df, cfg)
    assert "Trend" in [c.name for c in signal.failed_checks]


def test_low_volume_breakout_fails():
    df = enrich(uptrend())          # vol_ratio = 1.0
    cfg = lenient()
    cfg.min_breakout_volume = 1.3
    signal = _signal(df, cfg)
    assert "Volumen" in [c.name for c in signal.failed_checks]


def test_high_volume_breakout_passes_volume():
    raw = uptrend()
    raw.iloc[-1, raw.columns.get_loc("volume")] = 2.5e6
    df = enrich(raw)
    cfg = lenient()
    cfg.min_breakout_volume = 1.3
    assert not _signal(df, cfg).failed_checks


def test_extended_breakout_fails():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    cfg = lenient()
    cfg.max_extension_atr = 1.5
    match = make_match(df, trigger=close - 3 * atr)
    assert "Tavolsag a triggertol" in [c.name for c in _signal(df, cfg, match).failed_checks]


def test_poor_risk_reward_fails():
    df = enrich(uptrend())
    cfg = lenient()
    cfg.min_risk_reward = 2.0
    match = make_match(df, trigger=float(df["close"].iloc[-1]) * 0.99, target_mult=0.01)
    assert "Hozam/kockazat" in [c.name for c in _signal(df, cfg, match).failed_checks]


def test_bearish_market_blocks():
    df = enrich(uptrend())
    bench = uptrend(drift=-0.003, seed=7)
    bench.index = df.index
    cfg = lenient()
    cfg.regime_filter = True
    signal = _signal(df, cfg, benchmark=bench)
    assert any(c.name.startswith("Piac") for c in signal.failed_checks)


def test_missing_benchmark_warns_but_does_not_block():
    df = enrich(uptrend())
    cfg = lenient()
    cfg.regime_filter = True
    signal = _signal(df, cfg, benchmark=None)
    assert signal.passed
    assert any("Piac" in w for w in signal.warnings)


def test_earnings_blackout():
    df = enrich(uptrend())
    cfg = lenient()
    cfg.earnings_blackout_days = 7
    as_of = df.index[-1].date()
    blocked = _signal(df, cfg, earnings_date=as_of + timedelta(days=3), today=as_of)
    clear = _signal(df, cfg, earnings_date=as_of + timedelta(days=30), today=as_of)
    assert "Earnings" in [c.name for c in blocked.failed_checks]
    assert clear.passed


def test_min_score_and_setup_switch():
    df = enrich(uptrend())
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    cfg = lenient()
    cfg.min_score = 0
    setup = _signal(df, cfg, make_match(df, trigger=close + 0.5 * atr, status="forming"))
    assert setup.kind == "setup" and setup.qualifies(cfg)
    cfg.alert_setups = False
    assert not setup.qualifies(cfg)
    cfg.alert_setups = True
    cfg.min_score = 101
    assert not setup.qualifies(cfg)


# ---------------------------------------------------------------------------
# Look-ahead: a jovo nem valtoztathatja a mai dontest
# ---------------------------------------------------------------------------
def test_no_look_ahead_in_evaluation():
    full = enrich(uptrend(bars=360))
    cut = 320
    today = full.iloc[: cut + 1]
    match = make_match(today, trigger=float(today["close"].iloc[-1]) * 0.99)

    before = entry.evaluate("TEST", today, [match], settings_with(lenient()))[0]

    # a "jovo" gyertyait szetverjuk - a mai eredmeny nem valtozhat
    future = full.copy()
    future.iloc[cut + 1 :, :] = future.iloc[cut + 1 :, :] * 0.5
    after = entry.evaluate("TEST", future.iloc[: cut + 1], [match], settings_with(lenient()))[0]

    assert before.score == after.score
    assert before.plan.to_dict() == after.plan.to_dict()


# ---------------------------------------------------------------------------
# Ismetlesvedelem es csatornak
# ---------------------------------------------------------------------------
def test_entry_state_cooldown(tmp_path, monkeypatch):
    settings = settings_with(EntrySettings(cooldown_days=10))
    monkeypatch.setattr(type(settings), "entry_state_path", property(lambda self: tmp_path / "es.json"))
    df = enrich(uptrend())
    signal = _signal(df, lenient())

    state = entry.EntryState(settings)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert state.should_alert(signal, now)
    state.mark(signal, now)
    assert not state.should_alert(signal, now + timedelta(days=3))
    assert state.should_alert(signal, now + timedelta(days=11))

    state.sent[signal.key] = datetime.now(timezone.utc).isoformat()
    state.save()
    assert not entry.EntryState(settings).should_alert(signal)


def test_alert_text_contains_plan_and_disclaimer():
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    body = entry.format_body(signal)
    assert "Stop:" in body and "Cel:" in body and "Meret:" in body
    assert "Nem befektetesi tanacs" in body
    assert "meg nem zart le" in entry.format_body(signal, final_bar=False)
    assert entry.format_title(signal).startswith("TEST: Bull zaszlo")


def test_webhook_payload_formats():
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    forecast = Forecast(symbol="TEST", as_of=df.index[-1], price=signal.price, score=0.5, label="x")
    alert = Alert("TEST", entry.format_title(signal), entry.format_body(signal), forecast,
                  "entry_breakout", extra=signal.to_dict())
    s = Settings()
    assert "text" in WebhookChannel(s, url="https://chat.googleapis.com/v1/x").payload(alert)
    discord = WebhookChannel(s, url="https://discord.com/api/webhooks/1/abc").payload(alert)
    assert "content" in discord and len(discord["content"]) <= 2000
    assert not WebhookChannel(s, url="").enabled
    assert alert.is_entry and alert.bullish


def test_market_regime_and_relative_strength():
    up = uptrend(bars=260)
    down = uptrend(bars=260, drift=-0.003, seed=3)
    down.index = up.index
    assert market.market_regime(up, up.index[-1]).ok is True
    assert market.market_regime(down, down.index[-1]).ok is False
    assert market.market_regime(pd.DataFrame(), up.index[-1]).ok is None
    assert market.relative_strength(up, down, up.index[-1]) > 0
    assert market.days_until(date(2026, 1, 10), date(2026, 1, 3)) == 7


# ---------------------------------------------------------------------------
# Kilepesi terv es poziciokovetes
# ---------------------------------------------------------------------------
def test_plan_contains_partial_exit():
    df = enrich(uptrend())
    cfg = lenient()
    cfg.exit_mode = "partial"
    signal = _signal(df, cfg)
    plan = signal.plan
    assert plan.partial_price == pytest.approx(plan.entry + plan.risk_per_share)
    assert plan.horizon > 0
    body = entry.format_body(signal)
    assert "Kilepesi terv" in body and "stopot huzd a belepore" in body


def _tracker(tmp_path, monkeypatch):
    settings = settings_with(lenient())
    monkeypatch.setattr(type(settings), "positions_path", property(lambda self: tmp_path / "pos.json"))
    return settings, positions.PositionTracker(settings)


def _future(df, rows):
    """Uj gyertyak a jelzes utan (open, high, low, close)."""
    index = pd.bdate_range(df.index[-1] + pd.offsets.BDay(1), periods=len(rows))
    extra = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    extra["volume"] = 1e6
    return pd.concat([df[["open", "high", "low", "close", "volume"]], extra])


def test_tracker_partial_then_breakeven(tmp_path, monkeypatch):
    settings, tracker = _tracker(tmp_path, monkeypatch)
    df = enrich(uptrend())
    cfg = lenient()
    cfg.exit_mode = "partial"
    signal = _signal(df, cfg)
    pos = tracker.register(signal)
    close, risk = signal.price, signal.plan.risk_per_share

    e = close * 1.002                                        # masnapi nyito a limit alatt
    later = _future(df, [
        (e, e * 1.003, e * 0.998, e * 1.001),                # belepes
        (e, e + risk * 1.2, e * 0.999, e + risk),            # +1R -> fele el
        (e + risk, e + risk, e - 0.1, e - 0.05),             # vissza a belepo ala -> nullszaldo
    ])
    events = [ev for _, ev in tracker.update("TEST", later)]
    assert events == ["pos_entered", "pos_partial", "pos_breakeven"]
    assert pos.status == "closed" and pos.result_r == pytest.approx(0.5, abs=0.01)

    tracker.save()
    reloaded = positions.PositionTracker(settings)
    assert reloaded.positions[pos.id].status == "closed"
    assert reloaded.closed_stats()["win"] == 1.0
    # ujrafuttatas nem general ujabb esemenyt
    assert tracker.update("TEST", later) == []


def test_tracker_skips_gap_above_limit(tmp_path, monkeypatch):
    _, tracker = _tracker(tmp_path, monkeypatch)
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    pos = tracker.register(signal)
    g = signal.plan.entry_limit * 1.02
    events = tracker.update("TEST", _future(df, [(g, g * 1.01, g * 0.99, g)]))
    assert [ev for _, ev in events] == ["pos_skipped"] and pos.status == "skipped"
    title, body = positions.format_event(pos, "pos_skipped")
    assert "ervenytelen" in body


def test_tracker_stop_and_setup_not_tracked(tmp_path, monkeypatch):
    _, tracker = _tracker(tmp_path, monkeypatch)
    df = enrich(uptrend())
    signal = _signal(df, lenient())
    pos = tracker.register(signal)
    e, stop = signal.price, signal.plan.stop
    later = _future(df, [(e, e * 1.002, e * 0.998, e), (e * 0.99, e * 0.99, stop * 0.99, stop * 0.995)])
    events = [ev for _, ev in tracker.update("TEST", later)]
    assert events == ["pos_entered", "pos_stop"]
    assert pos.result_r == pytest.approx(-1.0, abs=0.05)

    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    setup = _signal(df, lenient(), make_match(df, trigger=close + 0.5 * atr, status="forming"))
    assert tracker.register(setup) is None


def test_benchmark_follows_scanned_index():
    from stockwatch.config import ScanSettings, Settings

    assert Settings().entry.benchmark == "QQQ"                                     # csak watchlist
    assert Settings(scan=ScanSettings(nasdaq100=True)).entry.benchmark == "QQQ"
    assert Settings(scan=ScanSettings(sp500=True)).entry.benchmark == "SPY"
    assert Settings(scan=ScanSettings(nasdaq100=True, sp500=True)).entry.benchmark == "SPY"
    fixed = Settings(scan=ScanSettings(sp500=True), entry={"benchmark": "iwm"})       # kezzel megadott ticker
    assert fixed.entry.benchmark == "IWM"
