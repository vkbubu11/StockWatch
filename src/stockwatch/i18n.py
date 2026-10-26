"""Forditas (angol / nemet / spanyol): a felulet es az ertesitesek kozos katalogusa (locale/catalog.jsonl).

A rendszer magyarul fogalmaz; a kikuldes elott (alerts.Dispatcher) a cim es a szoveg SORONKENT fordul.
A katalogus soronkent egy JSON objektum: {"hu": ..., "en": ..., "de": ..., "es": ...}. A kulcs a magyar
szoveg; a {0}, {1}... helyorzos kulcsok mintak (a {1:n} helyorzo csak szamra illeszkedik), a befogott resz
maga is fordul. Egy sor forditasa:
  1. pontos egyezes;
  2. minta, amelynek befogott reszei nem nyulnak at lista-elvalaszton (" | ", " · ", ", ");
  3. tagolas: sor eleji jelolo ("- ", "[...] ") vagy a zarojeleken kivuli elvalasztok menten, darabonkent;
  4. minta engedekenyebben (a befogott resz atnyulhat az elvalasztokon).
Ami nincs a katalogusban, magyarul marad. Ugyanez az algoritmus fut a feluleten (app/web/i18n.js).
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from .config import LANGUAGES

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent / "locale" / "catalog.jsonl"
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_KEY_PLACEHOLDER = re.compile(r"\{(\d+)(:n)?\}")      # a kulcsban {1:n}: csak szamra illeszkedik
_NUMBER = r"([-+]?\d[\d.,]*%?)"
_WORDISH = re.compile(r"[A-Za-zÀ-ž]{2}")
_LETTER = re.compile(r"[A-Za-zÀ-ž]")
_MAX_DEPTH = 8

# Tagolas: (elvalaszto, legfeljebb ennyi vagas; 0 = mind) - a legerosebbtol a leggyengebbig.
# A "cimke: ertek" es a "nev - leiras" csak az elso elofordulasnal vag (az ertek maga is tartalmazhatja).
_SEPARATORS = ((" | ", 0), (" · ", 0), (": ", 1), (" – ", 1), (" - ", 1), (", ", 0), (" → ", 1), (" @ ", 1))
_LIST_SEPARATORS = (" | ", " · ", ", ")
_PREFIXES = ("- ", "→ ", "· ", "– ")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _split_top(text: str, sep: str, maxsplit: int = 0) -> list[str]:
    """Vagas az elvalasztonal, de csak a zarojeleken kivul ("support 1 (2 ATR, 3p), ..." egyben marad)."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth <= 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            if maxsplit and len(parts) >= maxsplit:
                break
            continue
        i += 1
    parts.append(text[start:])
    return parts


class Catalog:
    def __init__(self, entries: dict[str, str]) -> None:
        self.exact: dict[str, str] = {}
        self._memo: dict[str, str | None] = {}       # ugyanaz a reszlet sokszor elofordul
        patterns = []
        for key, value in entries.items():
            if not isinstance(value, str):
                continue
            key = _norm(key)
            parts = _KEY_PLACEHOLDER.split(key)
            if len(parts) == 1:
                self.exact[key] = value
                continue
            literals, order, numeric = parts[0::3], [int(p) for p in parts[1::3]], [bool(p) for p in parts[2::3]]
            pieces = []
            for i, lit in enumerate(literals):        # szohatar: a helyorzo nem nyulhat bele egy szoba
                piece = re.escape(lit)
                if i > 0 and _LETTER.match(lit[:1]):
                    piece = f"(?<!{_LETTER.pattern}){piece}"
                if i < len(literals) - 1 and _LETTER.match(lit[-1:]):
                    piece = f"{piece}(?!{_LETTER.pattern})"
                pieces.append(piece)
            body = pieces[0] + "".join((_NUMBER if numeric[i] else "(.*?)") + pieces[i + 1] for i in range(len(order)))
            probe = max(literals, key=len)
            pipe = " | " in "".join(literals)     # a " | " elvalaszton csak az ezt tartalmazo kulcs nyulhat at
            patterns.append((-sum(map(len, literals)), re.compile("^" + body + "$", re.S), order, value, probe, pipe))
        self.patterns = [p[1:] for p in sorted(patterns, key=lambda p: p[0])]

    def lookup(self, text: str, depth: int = 0) -> str | None:
        hit = self.exact.get(text)
        if hit is not None:
            return hit
        if depth >= _MAX_DEPTH:
            return None
        if text in self._memo:
            return self._memo[text]
        out = None
        for step in (lambda: self._match(text, depth, strict=True), lambda: self._structure(text, depth),
                     lambda: self._match(text, depth, strict=False)):
            out = step()
            if out is not None:
                break
        if len(self._memo) > 20000:
            self._memo.clear()
        self._memo[text] = out
        return out

    def _sub(self, raw: str, depth: int) -> str:
        """Egy reszlet forditasa (a szelso szokozok maradnak); ha nem fordithato, valtozatlan."""
        if not _WORDISH.search(raw):
            return raw
        out = self.lookup(_norm(raw), depth + 1)
        if out is None:
            return raw
        return raw[: len(raw) - len(raw.lstrip())] + out + raw[len(raw.rstrip()):]

    def _match(self, text: str, depth: int, strict: bool) -> str | None:
        for regex, order, value, probe, pipe in self.patterns:
            if probe and probe not in text:
                continue
            m = regex.match(text)
            if not m:
                continue
            groups = m.groups()
            if not pipe and any(" | " in g for g in groups):
                continue
            if strict and any(len(_split_top(g, sep, 1)) > 1 for g in groups for sep in _LIST_SEPARATORS):
                continue
            caps = {idx: self._sub(groups[i], depth) for i, idx in enumerate(order)}
            out = _PLACEHOLDER.sub(lambda g: caps.get(int(g.group(1)), ""), value)
            if out != text:
                return out
        return None

    def _structure(self, text: str, depth: int) -> str | None:
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                rest = self._sub(text[len(prefix):], depth)
                return prefix + rest if rest != text[len(prefix):] else None
        if text.startswith("[") and "] " in text:
            close = text.index("] ")
            head, tail = text[1:close], text[close + 2:]
            new_head, new_tail = self._sub(head, depth), self._sub(tail, depth)
            if (new_head, new_tail) != (head, tail):
                return f"[{new_head}] {new_tail}"
        # az a tagolas nyer, amelyikben a darabok legnagyobb resze fordul (egyenlosegnel az erosebb elvalaszto)
        best, best_score = None, 0.0
        for sep, maxsplit in _SEPARATORS:
            parts = _split_top(text, sep, maxsplit)
            if len(parts) > 1:
                new = [self._sub(p, depth) for p in parts]
                score = sum(a != b for a, b in zip(new, parts)) / len(parts)
                if score > best_score:
                    best, best_score = sep.join(new), score
                    if score == 1.0:
                        break
        return best

    def line(self, text: str) -> str:
        core = _norm(text)
        if not core or not _WORDISH.search(core):
            return text
        out = self.lookup(core)
        if out is None:
            return text
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return lead + out + trail


@lru_cache(maxsize=len(LANGUAGES))
def entries(lang: str) -> dict[str, str]:
    """{magyar kulcs: forditas} egy nyelvre - a felulet is ezt kapja (/i18n/<nyelv>.json)."""
    if lang == "hu" or lang not in LANGUAGES:
        return {}
    out = {}
    try:
        for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row.get(lang), str):
                    out[row["hu"]] = row[lang]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        log.warning("A forditasi katalogus nem toltheto be (%s) - magyarul marad.", exc)
        return {}
    return out


@lru_cache(maxsize=len(LANGUAGES))
def catalog(lang: str) -> Catalog | None:
    data = entries(lang)
    return Catalog(data) if data else None


_TEXT_NODE = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r'\b(alt|title|placeholder|aria-label)="([^"]*)"')


def translate_html(page: str, lang: str) -> str:
    """Egy (inline szkript nelkuli) HTML-oldal forditasa a szerveren: a szovegcsomopontok es az alt / title /
    placeholder / aria-label attributumok a katalogusbol. A JavaScriptet nem futtato robotok (kereso, AI) is a
    latogato nyelven latjak az oldalt; a bongeszo (i18n.js) a data-lang-done jelolest latva nem forditja ujra."""
    if lang == "hu" or catalog(lang) is None:
        return page

    def tr(text: str) -> str:
        core = " ".join(text.split())
        if not re.search(r"[A-Za-zÀ-ž]{2}", core):
            return text
        out = translate(core, lang)
        return text if out == core else text[: len(text) - len(text.lstrip())] + out + text[len(text.rstrip()):]

    page = _TEXT_NODE.sub(lambda m: ">" + tr(m.group(1)) + "<", page)
    page = _ATTR.sub(lambda m: f'{m.group(1)}="{tr(m.group(2))}"', page)
    return re.sub(r'<html lang="[a-z]+"', f'<html lang="{lang}" data-lang-done="{lang}"', page, count=1)


def translate(text: str | None, lang: str) -> str | None:
    """Soronkenti forditas; magyar nyelvnel vagy hianyzo katalogusnal valtozatlan."""
    cat = catalog(lang)
    if cat is None or not text:
        return text
    return "\n".join(cat.line(line) for line in text.split("\n"))
