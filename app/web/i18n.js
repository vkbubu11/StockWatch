/* StockWatch - tobbnyelvu felulet (magyar / angol / nemet / spanyol).
 *
 * A felulet magyarul keszul; a forditas a megjelenites UTAN tortenik: egy MutationObserver a kirajzolt
 * szovegcsomopontokat es a title / placeholder / aria-label attributumokat a katalogus szerint csereli
 * (src/stockwatch/locale/catalog.jsonl, a szerver nyelvenkent adja: /i18n/<nyelv>.json). A kulcs a magyar szoveg; a {0}, {1}... helyorzos kulcsok mintak
 * (a helyorzo barmit illeszt, a forditas sorrendje szabadon cserelheto). Ugyanezt a katalogust hasznalja a
 * szerver az ertesitesekhez (src/stockwatch/i18n.py). Ami nincs a katalogusban, magyarul marad.
 * A logikat nem erinti: data-* attributumot, erteket, URL-t nem fordit.
 */
"use strict";

// Az engedelyezett nyelvek; egyetlen nyelvnel nincs valaszto (#46)
const LANGS = { hu: "Magyar", en: "English", de: "Deutsch", es: "Español" };
const LOCALES = { hu: "hu-HU", en: "en-US", de: "de-DE", es: "es-ES" };
const LANG_KEY = "sw-lang";

const LANG = (() => {
  let saved = null;
  try { saved = localStorage.getItem(LANG_KEY); } catch (_) { /* privat mod */ }
  // nincs mentett valasztas: angol (az alapertelmezett, a bongeszo nyelvetol fuggetlenul)
  return saved && LANGS[saved] ? saved : "en";
})();
const LOCALE = LOCALES[LANG];

const I18N = (() => {
  const exact = new Map();
  let patterns = [];
  const done = new WeakMap();             // csomopont -> az altalunk beirt szoveg (ne forditsuk ujra)
  const missing = new Set();              // ami nem fordult (a tesztek olvassak: window.__i18nMissing)
  window.__i18nMissing = missing;
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE"]);
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const LETTER = /[A-Za-zÀ-ž]/;

  // Tagolas (mint az i18n.py-ban): [elvalaszto, legfeljebb ennyi vagas; 0 = mind], a legerosebbtol a
  // leggyengebbig. A "cimke: ertek" es a "nev - leiras" csak az elso elofordulasnal vag.
  const SEPARATORS = [[" | ", 0], [" · ", 0], [": ", 1], [" – ", 1], [" - ", 1], [", ", 0], [" → ", 1], [" @ ", 1]];
  const LIST_SEPARATORS = [" | ", " · ", ", "];
  const PREFIXES = ["- ", "→ ", "· ", "– "];
  const MAX_DEPTH = 8;

  // vagas az elvalasztonal, de csak a zarojeleken kivul ("support 1 (2 ATR, 3p), ..." egyben marad)
  function splitTop(text, sep, maxsplit = 0) {
    const parts = [];
    let depth = 0, start = 0, i = 0;
    while (i < text.length) {
      const c = text[i];
      if ("([{".includes(c)) depth += 1;
      else if (")]}".includes(c)) depth -= 1;
      else if (depth <= 0 && text.startsWith(sep, i)) {
        parts.push(text.slice(start, i));
        i += sep.length;
        start = i;
        if (maxsplit && parts.length >= maxsplit) break;
        continue;
      }
      i += 1;
    }
    parts.push(text.slice(start));
    return parts;
  }

  function load(dict) {
    const list = [];
    for (const [key, value] of Object.entries(dict)) {
      if (typeof value !== "string") continue;
      const k = norm(key);
      if (!/\{\d+(:n)?\}/.test(k)) { exact.set(k, value); continue; }
      // a kulcsban {1:n}: csak szamra illeszkedik
      const order = [], numeric = [], literals = [];
      let last = 0;
      for (const ph of k.matchAll(/\{(\d+)(:n)?\}/g)) {
        literals.push(k.slice(last, ph.index));
        order.push(Number(ph[1]));
        numeric.push(Boolean(ph[2]));
        last = ph.index + ph[0].length;
      }
      literals.push(k.slice(last));
      // szohatar: a helyorzo nem nyulhat bele egy szoba ("{0} pont" ne illeszkedjen a "pontszam"-ra)
      const parts = literals.map((l, i) => {
        let r = l.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        if (l && i > 0 && LETTER.test(l[0])) r = `(?<!${LETTER.source})${r}`;
        if (l && i < literals.length - 1 && LETTER.test(l[l.length - 1])) r = `${r}(?!${LETTER.source})`;
        return r;
      });
      const body = parts[0] + order.map((_, i) => (numeric[i] ? "([-+]?\\d[\\d.,]*%?)" : "(.*?)") + parts[i + 1]).join("");
      const re = new RegExp(`^${body}$`, "s");
      const probe = literals.reduce((a, b) => (b.length > a.length ? b : a), "");
      const pipe = literals.join("").includes(" | ");   // a " | " elvalaszton csak az ezt tartalmazo kulcs nyulhat at
      list.push({ re, order, value, probe, pipe, weight: literals.join("").length });
    }
    patterns = list.sort((a, b) => b.weight - a.weight);
  }

  // egy reszlet forditasa (a szelso szokozok maradnak); ha nem fordithato, valtozatlan
  function sub(raw, depth) {
    if (!/[A-Za-zÀ-ž]{2}/.test(raw)) return raw;
    const out = lookup(norm(raw), depth + 1);
    if (out === undefined) return raw;
    return raw.match(/^\s*/)[0] + out + raw.match(/\s*$/)[0];
  }

  function match(text, depth, strict) {
    for (const p of patterns) {
      if (p.probe && !text.includes(p.probe)) continue;
      const m = p.re.exec(text);
      if (!m) continue;
      const groups = m.slice(1);
      if (!p.pipe && groups.some((g) => g.includes(" | "))) continue;
      if (strict && groups.some((g) => LIST_SEPARATORS.some((sep) => splitTop(g, sep, 1).length > 1))) continue;
      const caps = {};
      p.order.forEach((idx, i) => { caps[idx] = sub(groups[i], depth); });
      const out = p.value.replace(/\{(\d+)\}/g, (_, i) => caps[i] ?? "");
      if (out !== text) return out;
    }
    return undefined;
  }

  function structure(text, depth) {
    for (const prefix of PREFIXES) {
      if (text.startsWith(prefix)) {
        const rest = text.slice(prefix.length), out = sub(rest, depth);
        return out !== rest ? prefix + out : undefined;
      }
    }
    if (text.startsWith("[") && text.includes("] ")) {
      const close = text.indexOf("] ");
      const head = text.slice(1, close), tail = text.slice(close + 2);
      const nh = sub(head, depth), nt = sub(tail, depth);
      if (nh !== head || nt !== tail) return `[${nh}] ${nt}`;
    }
    // az a tagolas nyer, amelyikben a darabok legnagyobb resze fordul (egyenlosegnel az erosebb elvalaszto)
    let best, bestScore = 0;
    for (const [sep, maxsplit] of SEPARATORS) {
      const parts = splitTop(text, sep, maxsplit);
      if (parts.length < 2) continue;
      const out = parts.map((p) => sub(p, depth));
      const score = out.filter((p, i) => p !== parts[i]).length / parts.length;
      if (score > bestScore) {
        best = out.join(sep);
        bestScore = score;
        if (score === 1) break;
      }
    }
    return best;
  }

  // 1. pontos egyezes, 2. minta szigoruan, 3. tagolas, 4. minta engedekenyen (lasd i18n.py)
  const memo = new Map();                      // ugyanaz a reszlet sokszor elofordul (tablazatok, listak)
  function lookup(text, depth = 0) {
    const hit = exact.get(text);
    if (hit !== undefined) return hit;
    if (depth >= MAX_DEPTH) return undefined;
    if (memo.has(text)) return memo.get(text);
    const out = match(text, depth, true) ?? structure(text, depth) ?? match(text, depth, false);
    if (memo.size > 20000) memo.clear();
    memo.set(text, out);
    return out;
  }

  // a szoveg forditasa; a vezeto / zaro szokozok maradnak
  function t(s) {
    if (LANG === "hu" || s === null || s === undefined) return s;
    const str = String(s);
    const n = norm(str);
    if (!n || !/[A-Za-zÀ-ž]/.test(n)) return str;
    const out = lookup(n);
    // forditatlan, vagy csak reszben fordult (az angol kimenetben magyar ekezet maradt)
    if (out === undefined || (LANG === "en" && /[áéíóöőúüű]/i.test(out))) missing.add(n);
    if (out === undefined) return str;
    return str.match(/^\s*/)[0] + out + str.match(/\s*$/)[0];
  }

  function textNode(node) {
    const value = node.nodeValue;
    if (done.get(node) === value) return;
    const out = t(value);
    if (out !== value) node.nodeValue = out;
    done.set(node, node.nodeValue);
  }

  function element(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || (el.__swI18n && el.__swI18n[a] === v)) continue;
      const out = t(v);
      if (out !== v) el.setAttribute(a, out);
      (el.__swI18n ||= {})[a] = out;
    }
  }

  function walk(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      if (!root.parentElement || (!SKIP.has(root.parentElement.tagName) && !root.parentElement.closest("[translate=no]"))) textNode(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE || SKIP.has(root.tagName) || root.closest("[translate=no]")) return;
    element(root);
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      // translate="no" (szabvanyos HTML): felhasznaloi adat (e-mail, becenev, csoportnev), uzemeltetoi adat
      acceptNode: (n) => (n.nodeType === Node.ELEMENT_NODE && (SKIP.has(n.tagName) || n.getAttribute("translate") === "no")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) textNode(n); else element(n);
    }
  }

  function observe() {
    walk(document.body);
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "childList") r.addedNodes.forEach(walk);
        else if (r.type === "characterData") walk(r.target);
        else if (r.type === "attributes") element(r.target);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  function picker() {
    const box = document.querySelector(".topbar-right");
    if (!box || document.getElementById("lang") || Object.keys(LANGS).length < 2) return;   // egy nyelvnel nincs valaszto
    const sel = document.createElement("select");
    sel.id = "lang";
    sel.className = "lang-select";
    sel.setAttribute("aria-label", "Language");
    sel.title = "Nyelv / Language / Sprache / Idioma";
    sel.innerHTML = Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${k === LANG ? "selected" : ""}>${k.toUpperCase()} · ${v}</option>`).join("");
    sel.addEventListener("change", () => {
      try { localStorage.setItem(LANG_KEY, sel.value); } catch (_) { /* privat mod */ }
      location.reload();
    });
    box.prepend(sel);
  }

  function start() {
    document.documentElement.lang = LANG;
    // nyelvenkenti kepek (a kezdooldal kepernyokepei): data-lang-src="/img/x-{lang}.png"
    for (const img of document.querySelectorAll("img[data-lang-src]")) img.src = img.dataset.langSrc.replace("{lang}", LANG);
    // a szerver is tudja (pl. a megerosito level nyelve); nem titok, nem azonosit
    document.cookie = `sw_lang=${LANG}; Path=/; Max-Age=31536000; SameSite=Lax`;
    picker();
    if (LANG === "hu") return;
    // a katalogus betolteseig a felulet rejtve (ne villanjon fel magyarul); hiba eseten magyarul jelenik meg
    document.documentElement.classList.add("i18n-loading");
    fetch(`i18n/${LANG}.json`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((dict) => { load(dict); document.title = t(document.title); observe(); })
      .catch(() => { /* forditas nelkul */ })
      .finally(() => document.documentElement.classList.remove("i18n-loading"));
  }

  // a nativ parbeszedablakok szovege is forduljon (az app.js alert / confirm / prompt hivasai)
  if (LANG !== "hu") {
    const [a, c, p] = [window.alert, window.confirm, window.prompt];
    window.alert = (m) => a.call(window, t(m));
    window.confirm = (m) => c.call(window, t(m));
    window.prompt = (m, d) => p.call(window, t(m), d);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  return { t, lang: LANG, locale: LOCALE };
})();
