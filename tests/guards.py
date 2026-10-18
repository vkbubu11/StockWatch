"""Kozos ellenorzesek az uj funkciokhoz (docs/FEATURES-2026-10-DESIGN.md, 1.3).

  * import_closure: a stockwatch csomagon beluli TRANZITIV importok AST-bol - a fuggvenyen beluli
    (lusta) importokat is koveti, mert azok is futhatnak
  * advice_hits: ajanlas / felszolitas jellegu megfogalmazas (hu / en / de / es tiltolista)
  * numbers / unexplained_numbers: minden szam a szovegben a forrasadatbol jon-e
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = "stockwatch"

# Ezek a meglevo modulok elerik a brokert (kozvetlenul vagy lustan) - az uj kod nem importalhatja oket.
BROKER_REACHING = ("stockwatch.broker", "stockwatch.monitor", "stockwatch.heartbeat", "stockwatch.positions",
                   "stockwatch.replay", "stockwatch.revalidate")


def _module_file(name: str) -> Path | None:
    base = SRC.joinpath(*name.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return None


def _is_package(name: str) -> bool:
    return (SRC.joinpath(*name.split(".")) / "__init__.py").exists()


def direct_imports(name: str) -> set[str]:
    path = _module_file(name)
    if path is None:
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name if _is_package(name) else name.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(PACKAGE + "."):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base_parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(base_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not (base == PACKAGE or base.startswith(PACKAGE + ".")):
                continue
            out.add(base)
            for alias in node.names:                        # "from . import broker" -> almodul
                sub = f"{base}.{alias.name}"
                if _module_file(sub) is not None:
                    out.add(sub)
    # egy almodul importja a szulocsomag __init__-jet is lefuttatja
    for mod in list(out):
        parts = mod.split(".")
        for i in range(2, len(parts)):
            out.add(".".join(parts[:i]))
    return {m for m in out if _module_file(m) is not None and m != name}


def import_closure(*roots: str) -> set[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(direct_imports(name) - seen)
    return seen


def package_modules(package: str) -> list[str]:
    """Egy csomag (vagy modul) osszes modulja."""
    base = SRC.joinpath(*package.split("."))
    if base.with_suffix(".py").exists():
        return [package]
    out = []
    for path in base.rglob("*.py"):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        out.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return sorted(out)


# ---------------------------------------------------------------------------
# Ajanlas jellegu megfogalmazas - tiltolista (kisbetusen, szohatarral)
# ---------------------------------------------------------------------------
ADVICE_PATTERNS = [
    # magyar (ekezettel es nelkule)
    r"\bvedd meg", r"\bvegy[eé]l\b", r"\bv[aá]s[aá]rolj", r"\badj el\b", r"\badd el\b", r"\beladni [eé]rdemes",
    r"\bv[eé]teli limit", r"\bv[eé]teli jelz[eé]s", r"\baj[aá]nl", r"\bjavasolt\b", r"\b[eé]rdemes (meg)?venni",
    r"\bh[uú]zd\b", r"\bz[aá]rd\b", r"\bl[eé]pj be\b", r"\bl[eé]pj ki\b",
    # angol
    r"\bbuy\b", r"\bsell\b", r"\bshould (buy|sell)\b", r"\brecommend", r"\bstrong buy\b",
    # nemet
    r"\bkaufen\b", r"\bverkaufen\b", r"\bkauflimit", r"\bempfehl",
    # spanyol
    r"\bcomprar\b", r"\bvender\b", r"\brecomend",
]
_ADVICE = [re.compile(p, re.IGNORECASE) for p in ADVICE_PATTERNS]


def advice_hits(text: str) -> list[str]:
    return [m.group(0) for rx in _ADVICE for m in rx.finditer(text or "")]


# ---------------------------------------------------------------------------
# Szamok
# ---------------------------------------------------------------------------
NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?![\w])")


def numbers(text: str) -> list[str]:
    return [n.lstrip("+") for n in NUMBER.findall(text or "")]


def unexplained_numbers(text: str, strings: list[str], allowed: set[str]) -> list[str]:
    """A szoveg szamai, amelyek nem a megengedett (forrasbol formazott) ertekek kozul valok. A szoveges
    argumentumok (ticker, datum, tetelkod, cimke, parameter-ertek) szamai is forrasadatok: azok
    darabszamra levonodnak (szam-tokenenkent, nem reszszovegkent)."""
    from collections import Counter

    budget = Counter(n for s in strings if s for n in numbers(s))
    norm = {a.lstrip("+") for a in allowed}
    out = []
    for n in numbers(text):
        if budget[n] > 0:
            budget[n] -= 1
        elif n not in norm:
            out.append(n)
    return out
