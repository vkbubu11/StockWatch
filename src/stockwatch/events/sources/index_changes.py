"""S&P 500 bekerulesek es kikerulesek - a Wikipedia "Historical components of the S&P 500" oldalanak
`changes` tablazatabol (2026 augusztusa ota itt van; korabban a "List of S&P 500 companies" oldalon volt).

A tablazat a HATALYOS datumot adja (Effective Date). A bejelentes jellemzoen ~5 nappal korabbi, ezert a
tanulmany a hatalyos naptol mert hatast mutatja - a bejelentesi hatas nincs benne (a felulet kiirja).
A hatalyos datum elore ismert, igy jovobe latas nincs.

Tarolas: data/events/index_changes.json (< 1 MB) es data/events/sectors.json (GICS-szektor tickerenkent).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone

import requests

from ...config import DATA_DIR
from ...indexes import HEADERS, TICKER_RE, yahoo_symbol
from ...locks import write_atomic

log = logging.getLogger(__name__)

URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
EVENTS_DIR = DATA_DIR / "events"
MIN_ROWS = 100                         # ennel kevesebb sor: hibas / atalakitott oldal - nem irjuk felul a jot


def changes_path():
    return EVENTS_DIR / "index_changes.json"


def sectors_path():
    return EVENTS_DIR / "sectors.json"


def _date(text: str) -> date | None:
    text = re.sub(r"\[.*?\]", "", text).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _ticker(text: str) -> str | None:
    text = re.sub(r"\[.*?\]", "", text).strip().upper()
    return yahoo_symbol(text) if TICKER_RE.match(text) else None


def parse_changes(html: str) -> list[dict]:
    """[{date, added, added_name, removed, removed_name, reason}] - a datum nelkuli (osszevont) sor az elozo
    datumot orokli."""
    from bs4 import BeautifulSoup

    table = BeautifulSoup(html, "lxml").find("table", id="changes")
    if table is None:
        raise ValueError("nincs 'changes' tablazat az oldalon")
    out, last = [], None
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if not cells or row.find("th") is not None and not row.find("td"):
            continue                                          # fejlec
        day = _date(cells[0])
        if day is None and last is not None and len(cells) in (5, 6):
            cells = [""] + cells                              # rowspan-nal osszevont datum
            day = last
        if day is None or len(cells) < 6:
            continue
        last = day
        added, removed = _ticker(cells[1]), _ticker(cells[3])
        if not added and not removed:
            continue
        out.append({"date": day.isoformat(), "added": added, "added_name": cells[2] or None,
                    "removed": removed, "removed_name": cells[4] or None, "reason": cells[5][:300]})
    return sorted(out, key=lambda r: (r["date"], r["added"] or "", r["removed"] or ""))


def refresh(session=None) -> dict:
    """A valtozas-tablazat es a szektorok letoltese. Hiba vagy gyanus eredmeny eseten a regi fajl marad."""
    from ... import indexes

    http = session or requests
    out = {}
    response = http.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    rows = parse_changes(response.text)
    if len(rows) < MIN_ROWS:
        raise ValueError(f"csak {len(rows)} indexvaltozas - az oldal valoszinuleg megvaltozott")
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(changes_path(), json.dumps({"fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             "source": URL, "changes": rows}, ensure_ascii=False))
    out["changes"] = len(rows)
    try:
        sectors = indexes.fetch_sectors()
        if len(sectors) >= 400:
            write_atomic(sectors_path(), json.dumps({"fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                     "sectors": sectors}, ensure_ascii=False))
            out["sectors"] = len(sectors)
    except (requests.RequestException, ValueError) as exc:
        log.warning("A GICS-szektorok nem tolthetok le: %s", exc)
    return out


def load_changes() -> list[dict]:
    try:
        return json.loads(changes_path().read_text(encoding="utf-8"))["changes"] if changes_path().exists() else []
    except (OSError, ValueError, KeyError):
        return []


def load_sectors() -> dict[str, str]:
    try:
        return json.loads(sectors_path().read_text(encoding="utf-8"))["sectors"] if sectors_path().exists() else {}
    except (OSError, ValueError, KeyError):
        return {}
