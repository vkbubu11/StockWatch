"""Indexosszetetelek (Nasdaq-100, S&P 500) - hogy a watchlisten tul is legyen jelzes.

A tagokat a Wikipedia osszetetel-tablazataibol olvassuk (ezek naprakeszek, es
nem kell hozzajuk kulcs). Az eredmeny a `data/index_members.json`-be kerul, es
csak `scan.refresh_members_days` naponta toltjuk ujra. Ha a letoltes nem sikerul
(nincs halozat, megvaltozott az oldal), a legutobb mentett listaval dolgozunk.

Vedelem a hibas oldal ellen: ha a tagok szama nem eshet egybe a valosaggal
(pl. 30 papir az S&P 500-ban), nem irjuk felul a jo listat.

A Nasdaq-100-at a QQQ ETF koveti, igy a "teljes QQQ lista" = a Nasdaq-100 tagjai.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

import requests

from .config import Settings

log = logging.getLogger(__name__)

SOURCES = {
    "nasdaq100": {
        "label": "Nasdaq-100 (QQQ)",
        "url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        "column": ("Ticker", "Symbol"),
        "expected": (90, 110),
    },
    "sp500": {
        "label": "S&P 500",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "column": ("Symbol", "Ticker"),
        "expected": (480, 520),
    },
}
HEADERS = {"User-Agent": "StockWatch/1.0 (personal technical-analysis tool; python-requests)"}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def yahoo_symbol(ticker: str) -> str:
    """A Yahoo a share-osztalyt kotojellel irja: BRK.B -> BRK-B."""
    return ticker.strip().upper().replace(".", "-")


def parse_constituents(html: str, columns: tuple[str, ...]) -> list[str]:
    """A `constituents` azonositoju Wikipedia-tablazat ticker-oszlopa."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="constituents")
    if table is None:
        raise ValueError("nincs 'constituents' tablazat az oldalon")
    rows = table.find_all("tr")
    heads = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    col = next((heads.index(name) for name in columns if name in heads), None)
    if col is None:
        raise ValueError(f"nincs ticker-oszlop (fejlec: {heads[:6]})")
    out: list[str] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= col:
            continue
        ticker = cells[col].get_text(strip=True).upper()
        if TICKER_RE.match(ticker):
            out.append(yahoo_symbol(ticker))
    return list(dict.fromkeys(out))


def fetch(name: str) -> list[str]:
    source = SOURCES[name]
    response = requests.get(source["url"], headers=HEADERS, timeout=20)
    response.raise_for_status()
    symbols = parse_constituents(response.text, source["column"])
    low, high = source["expected"]
    if not low <= len(symbols) <= high:
        raise ValueError(f"{len(symbols)} papir - a(z) {source['label']} {low}-{high} kozott lenne")
    return symbols


class IndexMembers:
    """Az indexek tagjai, napi cache-sel (data/index_members.json, < 20 KB)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.index_members_path
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Serult index_members.json - ujratoltjuk.")

    def enabled(self) -> list[str]:
        cfg = self.settings.scan
        return [name for name in SOURCES if getattr(cfg, name)]

    def members(self, name: str, allow_network: bool = True) -> list[str]:
        entry = self.data.get(name) or {}
        fetched = entry.get("fetched")
        stale = (not fetched or date.fromisoformat(fetched)
                 < date.today() - timedelta(days=self.settings.scan.refresh_members_days))
        if stale and allow_network:
            try:
                symbols = fetch(name)
                entry = {"fetched": date.today().isoformat(), "symbols": symbols, "error": None}
                self.data[name] = entry
                self._save()
                log.info("%s osszetetel frissitve: %d papir", SOURCES[name]["label"], len(symbols))
            except (requests.RequestException, ValueError) as exc:
                log.warning("A(z) %s osszetetele nem toltheto le (%s) - a mentett listat hasznaljuk.",
                            SOURCES[name]["label"], exc)
                if entry:
                    entry["error"] = str(exc)[:200]
                    self._save()
        return list(entry.get("symbols") or [])

    def symbols(self, allow_network: bool = True) -> list[str]:
        """A bekapcsolt indexek tagjai egyesitve (atfedes nelkul)."""
        out: list[str] = []
        for name in self.enabled():
            out += self.members(name, allow_network)
        return list(dict.fromkeys(out))

    def status(self) -> list[dict]:
        return [
            {"name": name, "label": src["label"], "enabled": getattr(self.settings.scan, name),
             "count": len((self.data.get(name) or {}).get("symbols") or []),
             "fetched": (self.data.get(name) or {}).get("fetched"),
             "error": (self.data.get(name) or {}).get("error")}
            for name, src in SOURCES.items()
        ]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
