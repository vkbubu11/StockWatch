"""Discord csatorna: kartya, kep, sebessegkorlat, titokvedelem, duplikacio."""
from __future__ import annotations

import json
import logging

import pytest
import requests

from stockwatch import config
from stockwatch.alerts import Dispatcher
from stockwatch.alerts import discord as discord_mod
from stockwatch.alerts.base import Alert
from stockwatch.alerts.discord import DiscordChannel, color_of, is_discord_url
from stockwatch.config import AlertSettings, Settings

URL = "https://discord.com/api/webhooks/123456789/titkos-token_ABC"


@pytest.fixture()
def settings():
    return Settings(alerts=AlertSettings(min_seconds_between=0, telegram=False, console=False, jsonl=False))


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def _alert(event="entry_breakout", chart=None, body="Belepo: 101.20\nStop: 96.00"):
    return Alert("NVDA", "NVDA: kitores", body, None, event, chart_png=chart)


def test_url_validation():
    assert is_discord_url(URL)
    assert is_discord_url("https://discordapp.com/api/webhooks/1/x-y_z")
    assert not is_discord_url("https://discord.com.gonosz.example/api/webhooks/1/x")
    assert not is_discord_url("http://discord.com/api/webhooks/1/x")
    assert not is_discord_url("")


def test_payload_embed_without_mentions(settings):
    p = DiscordChannel(settings, url=URL).payload(_alert(body="@everyone ```"), with_image=True)
    embed = p["embeds"][0]
    assert p["allowed_mentions"] == {"parse": []}               # a szoveg senkit nem pingelhet
    assert embed["image"]["url"] == "attachment://chart.png"
    assert embed["color"] == discord_mod.GREEN
    assert embed["description"].count("```") == 2                 # a torzsbeli ``` nem torheti a blokkot
    long = DiscordChannel(settings, url=URL).payload(_alert(body="x" * 10_000), with_image=False)
    assert len(long["embeds"][0]["description"]) <= discord_mod.DESCRIPTION_LIMIT
    assert "image" not in long["embeds"][0]


def test_colors():
    assert color_of(_alert("pos_stop")) == discord_mod.RED
    assert color_of(_alert("entry_setup")) == discord_mod.AMBER
    assert color_of(_alert("broker_filled")) == discord_mod.BLUE
    assert color_of(_alert("pos_target")) == discord_mod.GREEN


def test_send_with_image_is_multipart(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw)) or FakeResponse(200))
    assert DiscordChannel(settings, url=URL).send(_alert(chart=b"\x89PNG"))
    url, kw = calls[0]
    assert url.endswith("?wait=true")
    assert kw["files"]["files[0]"][0] == "chart.png"
    assert json.loads(kw["data"]["payload_json"])["embeds"][0]["image"]


def test_rate_limit_retries_once(settings, monkeypatch):
    replies = [FakeResponse(429, {"retry_after": 0.01}), FakeResponse(204)]
    monkeypatch.setattr(requests, "post", lambda url, **kw: replies.pop(0))
    assert DiscordChannel(settings, url=URL).send(_alert())
    assert not replies


def test_network_error_never_logs_url(settings, monkeypatch, caplog):
    def boom(url, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(requests, "post", boom)
    with caplog.at_level(logging.ERROR):
        assert not DiscordChannel(settings, url=URL).send(_alert())
    assert "titkos-token" not in caplog.text and "ConnectionError" in caplog.text


def test_dispatcher_no_duplicate_with_legacy_webhook(settings, monkeypatch):
    monkeypatch.setattr(config, "env_value", lambda key: None)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", URL)                  # regi beallitas: Discord a webhookban
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    names = [c.name for c in Dispatcher(settings).channels]
    assert names == ["discord"]


# ---------------------------------------------------------------------------
# Webhook csere a feluletrol (Beallitasok) - ujrainditas nelkul el
# ---------------------------------------------------------------------------
@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", path)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    return path


def test_running_channel_follows_the_secrets_file(settings, env_file, monkeypatch):
    posted = []
    monkeypatch.setattr(requests, "post", lambda url, **k: posted.append(url.split("?")[0]) or FakeResponse(204))
    d = Dispatcher(settings)
    channel = next(c for c in d.channels if c.name == "discord")      # webhook nelkul is bent van
    assert not channel.send(_alert()) and posted == []
    config.save_env({"DISCORD_WEBHOOK_URL": URL})
    assert channel.send(_alert()) and posted == [URL]                   # ujrainditas nelkul az uj cimre megy
    new = "https://discord.com/api/webhooks/987654321/uj-token"
    config.save_env({"DISCORD_WEBHOOK_URL": new})
    assert channel.send(_alert()) and posted[-1] == new
    # a monitor inditaskor a titokfajlt a kornyezetbe is betolti: a torles utan se kuldjon a regi cimre
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", new)
    config.save_env({"DISCORD_WEBHOOK_URL": ""})
    assert not channel.send(_alert()) and len(posted) == 2


def test_webapp_saves_verified_webhook_and_never_returns_it(env_file, monkeypatch):
    from stockwatch import webapp

    with pytest.raises(ValueError, match="nem Discord webhook"):
        webapp.discord_action("save", {"url": "https://example.com/hook"})
    monkeypatch.setattr(DiscordChannel, "test", lambda self: None)
    with pytest.raises(ValueError, match="nem fogadta el"):
        webapp.discord_action("save", {"url": URL})
    assert config.discord_webhook_url() is None                        # elutasitott cim nem kerul a fajlba

    monkeypatch.setattr(DiscordChannel, "test", lambda self: "StockWatch-bot")
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, **k: sent.append(k) or FakeResponse(204))
    res = webapp.discord_action("save", {"url": URL, "test": True})
    assert res["name"] == "StockWatch-bot" and res["sent"] is True and len(sent) == 1
    assert config.discord_webhook_url() == URL
    status = webapp.discord_status()
    assert status["configured"] and status["valid"] and "titkos-token" not in json.dumps(status)
    assert status["hint"].endswith("_ABC") and "123456789" in status["hint"]
    assert webapp.discord_action("test", {})["ok"] and len(sent) == 2
    webapp.discord_action("forget", {})
    assert config.discord_webhook_url() is None and not webapp.discord_status()["configured"]
    with pytest.raises(ValueError, match="Nincs beallitva"):
        webapp.discord_action("test", {})
