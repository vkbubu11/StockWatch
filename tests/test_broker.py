"""Az Alpaca paper kereskedes tesztjei - halozat nelkul, egy memoriabeli hamis
Alpaca-val, ami ugyanazt a REST-szemantikat koveti (bracket, legs, replace, cancel)."""
from __future__ import annotations

import itertools

import pandas as pd
import pytest

from stockwatch import broker, config
from stockwatch.config import Settings
from tests.test_entry import _signal, enrich, lenient, uptrend


# ---------------------------------------------------------------------------
# Hamis Alpaca
# ---------------------------------------------------------------------------
class FakeAlpaca:
    def __init__(self, equity: float = 100_000, buying_power: float = 200_000) -> None:
        self.account_data = {"status": "ACTIVE", "trading_blocked": False,
                             "equity": str(equity), "cash": str(equity), "buying_power": str(buying_power)}
        self.is_open = False
        self.minutes_left: float | None = None
        self.orders_by_id: dict[str, dict] = {}
        self.submitted: list[dict] = []
        self.client_ids: set[str] = set()
        self.extra_positions: list[dict] = []
        self._ids = itertools.count(1)

    def _new(self, **fields) -> dict:
        order = {"id": f"o{next(self._ids)}", "status": "new", "filled_qty": "0",
                 "filled_avg_price": None, "filled_at": None, "legs": None, **fields}
        self.orders_by_id[order["id"]] = order
        return order

    # --- REST ---
    def account(self):
        return self.account_data

    def clock(self):
        clock = {"is_open": self.is_open}
        if self.minutes_left is not None:
            clock.update(timestamp="2023-03-01T15:00:00-05:00",
                         next_close=str(pd.Timestamp("2023-03-01T15:00:00-05:00")
                                        + pd.Timedelta(minutes=self.minutes_left)))
        return clock

    def positions(self):
        return list(self.extra_positions)

    def orders(self, status="open", limit=100):
        return [o for o in self.orders_by_id.values() if o["status"] in broker.OPEN_ORDER and o.get("parent") is None]

    def get_order(self, order_id):
        order = self.orders_by_id[order_id]
        while order["status"] == "replaced":
            order = self.orders_by_id[order["replaced_by"]]
        return dict(order)

    def submit_order(self, body):
        cid = body.get("client_order_id")
        if cid and cid in self.client_ids:
            raise broker.BrokerError("Alpaca 422: client_order_id must be unique")
        if cid:
            self.client_ids.add(cid)
        self.submitted.append(body)
        parent = self._new(symbol=body["symbol"], side=body["side"], type=body["type"], qty=body["qty"],
                           limit_price=body.get("limit_price"), order_class=body.get("order_class"))
        if body.get("order_class") == "bracket":
            tp = self._new(symbol=body["symbol"], side="sell", type="limit", qty=body["qty"],
                           limit_price=body["take_profit"]["limit_price"], parent=parent["id"], status="held")
            sl = self._new(symbol=body["symbol"], side="sell", type="stop", qty=body["qty"],
                           stop_price=body["stop_loss"]["stop_price"], parent=parent["id"], status="held")
            parent["legs"] = [tp, sl]
        return dict(parent)

    def replace_order(self, order_id, **fields):
        old = self.orders_by_id[order_id]
        new = self._new(**{k: v for k, v in old.items() if k not in ("id",)})
        new.update({k: str(v) for k, v in fields.items()})
        old["status"], old["replaced_by"] = "replaced", new["id"]
        return dict(new)

    def cancel_order(self, order_id):
        order = self.orders_by_id[order_id]
        if order["status"] == "filled":
            raise broker.BrokerError("Alpaca 422: order is not cancelable")
        order["status"] = "canceled"

    # --- teszt-segedek ---
    def fill(self, order_id, price, day="2023-03-01"):
        order = self.orders_by_id[order_id]
        order.update(status="filled", filled_qty=order["qty"], filled_avg_price=str(price),
                     filled_at=f"{day}T15:00:00Z")
        for leg in order.get("legs") or []:
            if self.orders_by_id[leg["id"]]["status"] == "held":
                self.orders_by_id[leg["id"]]["status"] = "new"

    def child(self, trade, leg_name, kind):
        leg = next(l for l in trade.leg_objs() if l.name == leg_name)
        return getattr(leg, "tp_id" if kind == "tp" else "sl_id")


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(entry=lenient().model_copy(update={"min_score": 0}))
    monkeypatch.setattr(type(settings), "broker_path", property(lambda self: tmp_path / "broker.json"))
    df = enrich(uptrend())
    monkeypatch.setattr(broker.store, "load", lambda symbol, s=None, universe=False: df)
    monkeypatch.setattr(broker, "_today", lambda: pd.Timestamp(df.index[-1]))
    return settings, df, FakeAlpaca(), broker.BrokerBook(settings)


def signal_for(df, settings):
    return _signal(df, settings.entry)


def submit(env):
    settings, df, fake, book = env
    trade = broker.submit(signal_for(df, settings), settings, fake, book)
    return trade


# ---------------------------------------------------------------------------
# Endpoint es kulcsok
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://paper-api.alpaca.markets", "https://paper-api.alpaca.markets/v2",
    "paper-api.alpaca.markets/", None,
])
def test_paper_endpoint_accepted(url):
    assert broker.normalise_endpoint(url) == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize("url", [
    "https://api.alpaca.markets", "http://paper-api.alpaca.markets",
    "https://paper-api.alpaca.markets.evil.com", "https://paper-api.alpaca.markets/other",
])
def test_live_or_foreign_endpoint_rejected(url):
    with pytest.raises(broker.BrokerError, match="PAPER"):
        broker.normalise_endpoint(url)


def test_save_env_keeps_other_lines_and_hides_secret(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# komment\nTELEGRAM_BOT_TOKEN=abc\nALPACA_KEY_ID=old\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", path)
    config.save_env({"ALPACA_KEY_ID": "PKTESTKEY1234", "ALPACA_SECRET_KEY": "s3cr3tvalue",
                     "ALPACA_ENDPOINT": broker.PAPER_ENDPOINT})
    text = path.read_text(encoding="utf-8")
    assert "# komment" in text and "TELEGRAM_BOT_TOKEN=abc" in text and "old" not in text
    described = broker.describe_credentials()
    assert described["configured"] and described["key_hint"] == "...1234"
    assert "s3cr3tvalue" not in str(described)
    with pytest.raises(ValueError):
        config.save_env({"ALPACA_KEY_ID": "a\nINJECTED=1"})


# ---------------------------------------------------------------------------
# Bekuldes
# ---------------------------------------------------------------------------
def test_submit_creates_two_brackets_for_partial_mode(env):
    settings, df, fake, book = env
    trade = submit(env)
    assert [b["order_class"] for b in fake.submitted] == ["bracket", "bracket"]
    partial, rest = fake.submitted
    assert int(partial["qty"]) + int(rest["qty"]) == trade.shares
    assert partial["time_in_force"] == "gtc" and partial["type"] == "limit"
    assert float(partial["take_profit"]["limit_price"]) == pytest.approx(trade.partial_price)
    assert float(rest["take_profit"]["limit_price"]) == pytest.approx(trade.target)
    assert float(partial["stop_loss"]["stop_price"]) == float(rest["stop_loss"]["stop_price"]) == trade.stop
    assert trade.stop < trade.entry_limit < trade.partial_price < trade.target
    legs = trade.leg_objs()
    assert all(leg.order_id and leg.tp_id and leg.sl_id for leg in legs)


def test_size_from_account_uses_equity(env):
    settings, df, fake, book = env
    trade = submit(env)
    risk = trade.entry_limit / 1.01 - trade.stop             # terv: belepo = zaro
    expected = int(min(100_000 * 0.01 / risk, 100_000 * 0.25 / (trade.entry_limit / 1.01)))
    assert trade.shares == pytest.approx(expected, abs=1)


def test_duplicate_and_same_symbol_rejected(env):
    settings, df, fake, book = env
    submit(env)
    with pytest.raises(broker.BrokerError, match="mar bekuldtuk"):
        broker.submit(signal_for(df, settings), settings, fake, book)


def test_max_open_trades_and_buying_power(env):
    settings, df, fake, book = env
    settings.broker.max_open_trades = 0
    with pytest.raises(broker.NoRoom, match="max"):
        submit(env)
    settings.broker.max_open_trades = 5
    fake.account_data["cash"] = "10"                    # margin (buying_power 200k) mellett sem vesz
    with pytest.raises(broker.NoRoom, match="szabad keret"):
        submit(env)
    assert fake.submitted == []


def test_existing_alpaca_position_blocks(env):
    settings, df, fake, book = env
    fake.extra_positions = [{"symbol": "TEST"}]
    with pytest.raises(broker.BrokerError, match="mar van pozicio"):
        submit(env)


def test_setup_and_trail_mode_rejected(env):
    settings, df, fake, book = env
    signal = signal_for(df, settings)
    signal.kind = "setup"
    with pytest.raises(broker.BrokerError, match="elokeszulet"):
        broker.submit(signal, settings, fake, book)
    settings.entry.exit_mode = "trail"
    with pytest.raises(broker.BrokerError, match="kilepesi mod"):
        broker.submit(signal_for(df, settings), settings, fake, book)


def test_failed_second_bracket_cancels_first(env, monkeypatch):
    settings, df, fake, book = env
    original = fake.submit_order
    calls = {"n": 0}

    def flaky(body):
        calls["n"] += 1
        if calls["n"] == 2:
            raise broker.BrokerError("Alpaca 500: hiba")
        return original(body)

    monkeypatch.setattr(fake, "submit_order", flaky)
    with pytest.raises(broker.BrokerError):
        submit(env)
    first = next(o for o in fake.orders_by_id.values() if o.get("order_class") == "bracket")
    assert first["status"] == "canceled"
    assert not book.trades


# ---------------------------------------------------------------------------
# Szinkronizalas
# ---------------------------------------------------------------------------
def test_fill_then_partial_moves_stop_to_breakeven_then_stop_out(env):
    settings, df, fake, book = env
    trade = submit(env)
    legs = trade.leg_objs()
    for leg in legs:
        fake.fill(leg.order_id, trade.entry_limit - 0.10)

    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_filled"] and trade.status == "open"

    fake.fill(fake.child(trade, "partial", "tp"), trade.partial_price)
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_partial", "broker_breakeven"]
    new_sl = fake.get_order(fake.child(trade, "target", "sl"))
    assert float(new_sl["stop_price"]) == pytest.approx(trade.entry_price)

    fake.fill(new_sl["id"], trade.entry_price)
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_stop", "broker_closed"]
    assert trade.status == "closed"
    half = legs[0].qty
    assert trade.realised_pnl == pytest.approx((trade.partial_price - trade.entry_price) * half, abs=0.05)
    assert broker.sync(settings, fake, book) == []           # nincs ismetles


def test_unfilled_entry_expires_after_next_session(env):
    settings, df, fake, book = env
    trade = submit(env)
    trade.signal_date = f"{df.index[-2]:%Y-%m-%d}"            # a jelzes utani nap mar lezajlott
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert events == ["broker_expired"] and trade.status == "expired"
    assert all(fake.orders_by_id[l.order_id]["status"] == "canceled" for l in trade.leg_objs())


def test_entry_waits_while_signal_day_is_last_bar(env):
    settings, df, fake, book = env
    trade = submit(env)                                     # jelzes = utolso gyertya
    assert broker.sync(settings, fake, book) == []
    assert trade.status == "submitted"


def test_timeout_sells_remaining_without_double_selling(env):
    settings, df, fake, book = env
    trade = submit(env)
    for leg in trade.leg_objs():
        fake.fill(leg.order_id, trade.entry_limit, day=f"{df.index[-60]:%Y-%m-%d}")
    broker.sync(settings, fake, book)
    trade.horizon = 10
    # a ½ lab celja kozben teljesult -> arra NEM kuldunk eladast
    fake.fill(fake.child(trade, "partial", "tp"), trade.partial_price)
    events = [e for _, e in broker.sync(settings, fake, book, final_bar=True)]
    assert "broker_timeout" in events
    sells = [b for b in fake.submitted if b.get("side") == "sell"]
    rest = next(l for l in trade.leg_objs() if l.name == "target")
    assert len(sells) == 1 and int(sells[0]["qty"]) == rest.filled_qty
    fake.fill(rest.exit_order_id, trade.entry_limit + 1)
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert "broker_closed" in events
    assert next(l for l in trade.leg_objs() if l.name == "target").exit_reason == "timeout"


def test_timeout_exits_before_the_close_not_next_morning(env):
    settings, df, fake, book = env
    trade = submit(env)
    for leg in trade.leg_objs():
        fake.fill(leg.order_id, trade.entry_limit, day=f"{df.index[-60]:%Y-%m-%d}")
    broker.sync(settings, fake, book)
    trade.horizon = 60                      # a mai (utolso) nap a 60. - ma jar le
    fake.is_open, fake.minutes_left = True, 30
    assert "broker_timeout" not in [e for _, e in broker.sync(settings, fake, book)]    # meg nem a zaras elott
    fake.minutes_left = 5
    events = [e for _, e in broker.sync(settings, fake, book)]
    assert "broker_timeout" in events
    sells = [b for b in fake.submitted if b.get("side") == "sell"]
    assert len(sells) == 2 and all(b["type"] == "market" for b in sells)


def test_timeout_counts_today_even_if_cache_has_no_bar_yet(env):
    settings, df, fake, book = env
    trade = submit(env)
    for leg in trade.leg_objs():
        fake.fill(leg.order_id, trade.entry_limit, day=f"{df.index[-60]:%Y-%m-%d}")
    broker.sync(settings, fake, book)
    held_closed = broker.bars_held(trade, df.iloc[:-1], include_today=True)     # a cache-ben nincs mai gyertya
    assert held_closed == broker.bars_held(trade, df, include_today=True) == broker.bars_held(trade, df) == 60
    trade.horizon = 61                      # holnap jar le - ma meg nem zarunk
    fake.is_open, fake.minutes_left = True, 5
    assert "broker_timeout" not in [e for _, e in broker.sync(settings, fake, book)]


def test_auto_submit_without_room_is_a_notice_not_an_error(env, monkeypatch):
    from stockwatch import monitor

    settings, df, fake, book = env
    settings.broker.auto_trade = True
    settings.broker.max_open_trades = 0
    monkeypatch.setattr(monitor, "_fresh_settings", lambda: settings)
    monkeypatch.setattr(broker.AlpacaClient, "from_env", classmethod(lambda cls: fake))

    class Sink:
        def __init__(self):
            self.alerts = []

        def dispatch(self, alert):
            self.alerts.append(alert)

    sink = Sink()
    monitor.submit_to_broker([(signal_for(df, settings), None)], sink)
    assert [a.event for a in sink.alerts] == ["broker_skipped"]
    assert "nincs hely" in sink.alerts[0].body and fake.submitted == []


def test_manual_close_and_cancel(env):
    settings, df, fake, book = env
    trade = submit(env)
    broker.close_trade(trade, fake)
    assert trade.status == "cancelled"
    assert all(fake.orders_by_id[l.order_id]["status"] == "canceled" for l in trade.leg_objs())


def test_book_roundtrip(env):
    settings, df, fake, book = env
    trade = submit(env)
    book.save()
    again = broker.BrokerBook(settings)
    assert again.get(trade.id).leg_objs()[0].order_id == trade.leg_objs()[0].order_id


def test_strong_signal_gets_bigger_size(env):
    settings, df, fake, book = env
    settings.entry.max_position_pct = 100                                   # a kockazat korlatozzon, ne a plafon
    base = broker.submit(signal_for(df, settings), settings, fake, broker.BrokerBook(settings))
    signal = signal_for(df, settings)
    settings.entry.boost_score, settings.entry.boost_risk_mult = signal.score, 1.5
    fake2 = FakeAlpaca()
    boosted = broker.submit(signal, settings, fake2, broker.BrokerBook(settings))
    assert boosted.shares == pytest.approx(base.shares * 1.5, abs=1)


def test_pending_buys_reduce_free_cash(env):
    settings, df, fake, book = env
    trade = submit(env)                                     # fuggo belepo: limit x db lekotve
    pending = broker.pending_buys(fake.orders("open"))
    assert pending == pytest.approx(trade.entry_limit * trade.shares, rel=1e-6)
    assert broker.free_cash(fake.account(), fake.orders("open")) == pytest.approx(100_000 - pending)
    fake.account_data["buying_power"] = "1000"               # a vasarloero a felso korlat
    assert broker.free_cash(fake.account(), []) == 1000


def test_capital_payload_shares():
    from stockwatch import webapp

    account = {"equity": "10000", "cash": "8860", "buying_power": "38637"}
    positions = [{"symbol": "DDOG", "market_value": "1141.5"}]
    cap = webapp.capital_payload(account, positions, [])
    assert cap["invested"] == 1141.5 and cap["invested_pct"] == pytest.approx(11.415)
    assert cap["free"] == 8860 and cap["positions"][0]["symbol"] == "DDOG"
