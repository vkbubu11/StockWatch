"""Alpaca News API (Benzinga hirfolyam, 2015-tol) - a paper szamla kulcsaival. Kulcs nelkul kimarad.
A `created_at` az elso megjelenes ideje; a kesobbi frissites (updated_at) nem mozgatja el."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from ..model import Article, utc_iso

log = logging.getLogger(__name__)

NAME = "alpaca"
URL = "https://data.alpaca.markets/v1beta1/news"
TIMEOUT = 20
MAX_PAGES = 200


def credentials() -> tuple[str | None, str | None]:
    from ...config import alpaca_credentials           # nem a broker modulbol: a hirforras ne erje el

    _, key, secret = alpaca_credentials()
    return key, secret


def fetch(symbol: str, since: pd.Timestamp, settings=None, session=None):
    key, secret = credentials()
    if not key or not secret:
        return [], None
    session = session or requests.Session()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    now = datetime.now(timezone.utc)
    params = {"symbols": symbol.upper(), "start": utc_iso(since), "end": utc_iso(now), "limit": 50,
              "sort": "asc", "include_content": "false"}
    out, token = [], None
    for _ in range(MAX_PAGES):
        if token:
            params["page_token"] = token
        try:
            r = session.get(URL, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Alpaca hirek (%s): %s", symbol, exc)
            return out, None
        if r.status_code in (401, 403):
            log.warning("Alpaca hirek: nincs jogosultsag (%s) - a fiok csomagja nem tartalmazza?", r.status_code)
            return [], None
        if r.status_code != 200:
            log.warning("Alpaca hirek (%s): HTTP %s", symbol, r.status_code)
            return out, None
        body = r.json()
        for n in body.get("news", []):
            out.append(Article(id=f"alpaca:{n['id']}", source=NAME, symbol=symbol.upper(),
                               title=str(n.get("headline") or "").strip(), summary=str(n.get("summary") or "")[:500],
                               url=n.get("url") or "", publisher=n.get("source") or n.get("author") or "",
                               related=list(n.get("symbols") or []), fetched_at=utc_iso(now),
                               published_at=utc_iso(n["created_at"])))
        token = body.get("next_page_token")
        if not token:
            break
    return [a for a in out if a.title], (utc_iso(since), utc_iso(now))
