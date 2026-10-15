"""Yahoo Finance hirkereso (yfinance Search): a legfrissebb ~20 hir, kulcs nelkul. Tortenti adat NINCS -
ez az elo arnyek-uzemhez valo. A lefedettseg a legregebbi visszaadott hirtol a letoltesig tart."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import pandas as pd

from ..model import Article, utc_iso

log = logging.getLogger(__name__)

NAME = "yahoo"
COUNT = 25


def relevant(symbol: str, title: str, related: list[str], company: str | None) -> bool:
    """A hir a papirrol szol-e: a ticker vagy a cegnev szerepel a cimben."""
    if re.search(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])", title):
        return True
    if company and re.search(rf"\b{re.escape(company)}\b", title, re.I):
        return True
    return False            # a "relatedTickers" mezo megbizhatatlan (pl. NVDA-hoz Colgate-hir) - csak a cim szamit


def fetch(symbol: str, since: pd.Timestamp, settings=None, company: str | None = None, search=None):
    if search is None:
        import yfinance as yf

        search = lambda q: yf.Search(q, news_count=COUNT).news  # noqa: E731
    try:
        items = search(symbol) or []
    except Exception as exc:
        log.warning("Yahoo hirek (%s): %s", symbol, exc)
        return [], None
    now = utc_iso(datetime.now(timezone.utc))
    out = []
    for n in items:
        title = str(n.get("title") or "").strip()
        if not title or not n.get("providerPublishTime"):
            continue
        if not relevant(symbol, title, n.get("relatedTickers") or [], company):
            continue
        published = pd.Timestamp(int(n["providerPublishTime"]), unit="s", tz="UTC")
        out.append(Article(id=f"yahoo:{n.get('uuid') or title}", source=NAME, symbol=symbol.upper(), title=title,
                           url=n.get("link") or "", publisher=n.get("publisher") or "", fetched_at=now,
                           related=list(n.get("relatedTickers") or []), published_at=utc_iso(published)))
    stamps = [pd.Timestamp(int(n["providerPublishTime"]), unit="s", tz="UTC") for n in items if n.get("providerPublishTime")]
    return out, ((utc_iso(min(stamps)), now) if stamps else None)
