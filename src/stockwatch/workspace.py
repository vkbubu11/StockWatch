"""Munkaterulet: a felhasznalohoz tartozo UJ adatok helye (SaaS-elokeszites).

Csak az uj funkciok felhasznaloi adata kerul ide (reggeli osszefoglalok, ugynok-briefek es -futasok,
gyakorlo korok, publikalt oldalak): data/users/<munkaterulet>/<funkcio>/. A kozos piaci adat (arfolyam,
indexosszetetel, Form 4, indexvaltozasok) a kozos data/ alatt marad.

Az eleresi utat KIZAROLAG a `user_dir` / `user_root` adja, igy a SaaS-atallaskor egy helyen csereljuk.
A meglevo modulok (watchlist, Sajat poziciok, beallitasok) meg globalisak: az admin munkaterulet ezeket
latja, mas munkaterulet a SaaS 1. fazisaig NotImplementedError-t kap.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import DATA_DIR

DEFAULT = "admin"
USERS_DIR = DATA_DIR / "users"
WORKSPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FEATURE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def check(workspace: str) -> str:
    if not isinstance(workspace, str) or not WORKSPACE_RE.match(workspace):
        raise ValueError(f"Ervenytelen munkaterulet: {workspace!r}")
    return workspace


def user_root(workspace: str = DEFAULT) -> Path:
    return USERS_DIR / check(workspace)


def user_dir(workspace: str, feature: str) -> Path:
    """data/users/<munkaterulet>/<funkcio>/ - nem hozza letre (az iro fuggveny teszi)."""
    if not FEATURE_RE.match(feature):
        raise ValueError(f"Ervenytelen funkcionev: {feature!r}")
    return user_root(workspace) / feature


def _require_admin(workspace: str) -> None:
    if check(workspace) != DEFAULT:
        raise NotImplementedError("Tobb munkaterulet a SaaS 1. fazisaban jon (docs/SAAS-DESIGN.md).")


def watchlist(workspace: str = DEFAULT) -> list[str]:
    from .config import load_watchlist

    _require_admin(workspace)
    return list(load_watchlist().symbols)


def holding_symbols(settings, workspace: str = DEFAULT) -> list[str]:
    """A Sajat poziciok nyitott papirjai (csak olvasva)."""
    from .holdings import HoldingStore

    _require_admin(workspace)
    return HoldingStore(settings, read_only=True).symbols()
