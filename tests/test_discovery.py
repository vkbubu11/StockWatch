"""AI-felfedezes (GEO): llms.txt, ai.json, ecp.json (gyoker es /.well-known), robots.txt, sitemap.xml nyilvanosak, a
szerver cimevel es a kapcsolati e-maillel; a Rolunk oldal es a kezdooldal a szerveren fordul (valasztas nelkul
angolul - a JavaScriptet nem futtato robotok is igy latjak)."""
from __future__ import annotations

import json
import re

import pytest

from stockwatch import webapp

from .test_webapp import _req, port  # noqa: F401  (pytest fixture)

BOT = {"User-Agent": "GPTBot/1.1"}


@pytest.fixture
def site_env(monkeypatch):
    real = webapp.config.env_value
    values = {"CONTACT_EMAIL": "hello@gyakorlas.example", "PUBLIC_URL": "https://gyakorlas.example"}
    monkeypatch.setattr(webapp.config, "env_value", lambda k: values[k] if k in values else real(k))


def test_ai_discovery_files_are_public(site_env, port):  # noqa: F811
    for path in ("/llms.txt", "/ai.json", "/.well-known/ai.json", "/ecp.json", "/.well-known/ecp.json"):
        res = _req(port, "GET", path, headers=BOT)
        assert res.status == 200, path
        assert "{URL}" not in res.text and "{CONTACT}" not in res.text
        assert "https://gyakorlas.example/about" in res.text and "hello@gyakorlas.example" in res.text, path
        if path.endswith(".json"):
            assert res.getheader("Content-Type").startswith("application/json")
            data = json.loads(res.text)
            assert data["name"] == "StockWatch Practice"
    llms = _req(port, "GET", "/llms.txt", headers=BOT).text
    assert llms.startswith("# StockWatch Practice\n\n> ") and "not investment advice" in llms.lower()
    robots = _req(port, "GET", "/robots.txt", headers=BOT).text
    assert "Allow: /" in robots and "Disallow: /api/" in robots and "Sitemap: https://gyakorlas.example/sitemap.xml" in robots
    sitemap = _req(port, "GET", "/sitemap.xml", headers=BOT).text
    assert "<loc>https://gyakorlas.example/about</loc>" in sitemap and "/api" not in sitemap


def test_about_page_is_public_and_english_for_crawlers(site_env, port):  # noqa: F811
    res = _req(port, "GET", "/about", headers=BOT)
    assert res.status == 200 and '<html lang="en" data-lang-done="en"' in res.text
    assert "<h1>About and contact</h1>" in res.text and "hello@gyakorlas.example" in res.text
    assert not re.search(r"[őűŐŰ]", res.text) and "Not investment advice." in res.text
    hu = _req(port, "GET", "/about", headers=BOT | {"Cookie": "sw_lang=hu"}).text
    assert "<h1>Rólunk és kapcsolat</h1>" in hu and '<html lang="hu">' in hu
    de = _req(port, "GET", "/about", headers=BOT | {"Cookie": "sw_lang=de"}).text
    assert "<h1>Über uns und Kontakt</h1>" in de and 'lang="de"' in de


def test_landing_is_english_for_crawlers_and_links_the_about_page(site_env, port):  # noqa: F811
    res = _req(port, "GET", "/", headers=BOT)
    assert res.status == 200 and 'href="/about"' in res.text
    assert "<h1>Learn to read charts – with blind decisions and instant feedback</h1>" in res.text
    assert "<title>StockWatch Practice – chart reading with blind decisions</title>" in res.text
    assert not re.search(r"[őűŐŰ]", res.text)
    assert 'alt="A hidden practice situation on a phone' in res.text                # az alt szoveg is
