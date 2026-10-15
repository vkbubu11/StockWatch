"""Hirtar: data/news/<PAPIR>.json.gz - cikkek (pontszammal), forrasonkenti lefedettsegi idoszakok.

Duplikatum: ugyanaz az azonosito, VAGY ugyanaz a normalizalt cim 48 oran belul (masik forrasbol).
Ilyenkor a KORABBI megjelenesu marad (point-in-time), a masik forras az `also` listaba kerul.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timezone

import pandas as pd

from ..config import DATA_DIR
from . import scoring
from .model import Article

NEWS_DIR = DATA_DIR / "news"
LIMIT_MB = 300
DUP_WINDOW = pd.Timedelta(hours=48)
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")


def _company(symbol: str) -> str | None:
    try:
        from .sources.edgar import company_name

        return company_name(symbol)
    except Exception:
        return None


def _path(symbol: str):
    if not SYMBOL_RE.match(symbol):
        raise ValueError(f"Ervenytelen ticker: {symbol}")
    return NEWS_DIR / f"{symbol.replace('^', '_')}.json.gz"


def load(symbol: str) -> dict:
    path = _path(symbol.upper())
    if not path.exists():
        return {"symbol": symbol.upper(), "articles": [], "coverage": {}, "fetched": {}}
    data = json.loads(gzip.decompress(path.read_bytes()))
    data.setdefault("coverage", {})
    data.setdefault("fetched", {})
    return data


def articles(symbol: str, rescore: bool = True) -> list[Article]:
    out = [Article.from_dict(d) for d in load(symbol)["articles"]]
    if rescore and any(a.score.get("version") != scoring.MODEL_VERSION for a in out):
        company = _company(symbol)
        for a in out:
            if a.score.get("version") != scoring.MODEL_VERSION:
                a.score = scoring.score(a, company)
    return out


def _write(symbol: str, data: dict) -> None:
    path = _path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8")))
    tmp.replace(path)


def _merge_intervals(intervals: list[list[str]]) -> list[list[str]]:
    spans = sorted((pd.Timestamp(a), pd.Timestamp(b)) for a, b in intervals)
    out = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[a.isoformat(), b.isoformat()] for a, b in out]


def merge(symbol: str, source: str, new: list[Article], coverage: tuple | None) -> dict:
    """Uj cikkek beolvasztasa (duplikatumszuressel, pontozassal) es a lefedettseg frissitese."""
    symbol = symbol.upper()
    data = load(symbol)
    have = {d["id"]: d for d in data["articles"]}
    by_title: dict[str, list[dict]] = {}
    for d in data["articles"]:
        by_title.setdefault(Article.from_dict(d).title_key, []).append(d)
    added, company = 0, _company(symbol)
    for a in new:
        if a.id in have:
            continue
        twins = [d for d in by_title.get(a.title_key, []) if a.kind == "news" and d.get("kind") == "news"
                 and abs(pd.Timestamp(d["published_at"]) - a.published) <= DUP_WINDOW]
        if twins:
            first = min(twins, key=lambda d: d["published_at"])
            if a.source not in first.setdefault("also", []) and a.source != first["source"]:
                first["also"].append(a.source)
            if a.published < pd.Timestamp(first["published_at"]):     # a korabbi megjelenes szamit
                first["published_at"] = a.published_at
            continue
        a.score = scoring.score(a, company)
        d = a.to_dict()
        data["articles"].append(d)
        have[a.id] = d
        by_title.setdefault(a.title_key, []).append(d)
        added += 1
    data["articles"].sort(key=lambda d: d["published_at"])
    if coverage:
        spans = data["coverage"].get(source, []) + [list(coverage)]
        data["coverage"][source] = _merge_intervals(spans)
    data["fetched"][source] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["updated_at"] = data["fetched"][source]
    _write(symbol, data)
    prune(keep={symbol})
    return {"added": added, "total": len(data["articles"])}


def usage_bytes() -> int:
    return sum(p.stat().st_size for p in NEWS_DIR.glob("*.json.gz")) if NEWS_DIR.exists() else 0


def prune(keep: set[str] | None = None) -> list[str]:
    keep = {k.replace("^", "_") for k in (keep or set())}
    files = sorted(NEWS_DIR.glob("*.json.gz"), key=lambda p: p.stat().st_mtime) if NEWS_DIR.exists() else []
    removed = []
    while files and usage_bytes() > LIMIT_MB * 1024 * 1024:
        victim = files.pop(0)
        if victim.name.split(".")[0] in keep:
            continue
        victim.unlink(missing_ok=True)
        removed.append(victim.name)
    return removed
