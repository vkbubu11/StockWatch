/* StockWatch oktatasi felulet - vanilla JS, keretrendszer nelkul.
 * Utvonalak (hash): #/ (= #/learn)  #/learn/N  #/practice  #/practice/ID  #/welcome  #/profile  #/info
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const tooltip = $("#tooltip");
let chartRO = null;

// ---------------------------------------------------------------------------
// Segedek
// ---------------------------------------------------------------------------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}%`);
// pontos idopont (a bongeszo, azaz magyar ido szerint); a tooltipben a New York-i ido
const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  const opts = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  const ny = d.toLocaleString(LOCALE, { ...opts, timeZone: "America/New_York" });
  return `<span title="New York: ${esc(ny)}">${esc(d.toLocaleString(LOCALE, opts))}</span>`;
};
const signed = (v, d = 2, unit = "") => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}${unit}`);

async function api(path, options = {}) {
  const init = { ...options, headers: { "X-StockWatch": "1", "Content-Type": "application/json", ...(options.headers || {}) } };
  const res = await fetch(path, init);
  if (res.status === 401) {          // lejart / hianyzo munkamenet (szerveren futtatva)
    location.href = "/login";
    throw new Error("Bejelentkezés szükséges");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 409) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function bindRowLinks() {
  view.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}

function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const stepv = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / stepv) * stepv; v <= hi; v += stepv) out.push(Number(v.toFixed(6)));
  return out;
}

// ---------------------------------------------------------------------------
// Utvonalvalasztas
// ---------------------------------------------------------------------------
async function route() {
  const hash = location.hash || "#/";
  const tab = hash.startsWith("#/info") ? "info" : hash.startsWith("#/profile") ? "profile"
    : hash.startsWith("#/practice") ? "practice" : "learn";
  document.querySelectorAll(".tabs a, .bottom-nav a, .more-list a").forEach((a) => {
    a.classList.toggle("active", a.dataset.tab === tab);
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  closeMoreSheet();
  tooltip.hidden = true;
  try {
    const pm = hash.match(/^#\/practice\/([a-z]{12})$/);
    const me = await whoami();
    if (!me.onboarded && !pm) { pageWelcome(Number((hash.match(/^#\/welcome\/(\d)$/) || [0, 0])[1])); return; }
    const lm = hash.match(/^#\/learn\/(\d{1,2})$/);
    if (tab === "info") pageInfo();
    else if (tab === "learn") { if (lm) await pageLesson(Number(lm[1])); else await pageLearn(); }
    else if (tab === "profile") await pageProfile();
    else if (pm) await pagePracticeSession(pm[1]);
    else await pagePractice();
  } catch (e) {
    view.innerHTML = `<div class="empty">Hiba: ${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Bevezeto (#39): elso belepeskor 3 lepes, utana egy probafeladat
// ---------------------------------------------------------------------------
let me = null;
async function whoami() { if (!me) me = await api("/api/session"); return me; }

const WELCOME = [
  ["A chart olvasása", `<p>Egy rejtett helyzetet kapsz: a chart a döntés napjáig látszik, a jövő nem. A papír neve és a dátum rejtve marad,
     az ár <strong>normalizált</strong> – az utolsó látható záró mindig 100, így a számok nem árulják el, melyik papírról van szó.</p>
   <ul class="digest-list"><li><span>Gyertyák: nyitó, záró, napi csúcs és mélypont.</span></li>
     <li><span>EMA20 / EMA50 / EMA200: a rövid, közép és hosszú távú trend.</span></li>
     <li><span>Volumen: a 20 napos átlaghoz viszonyítva; az ATR a napi ingás szokásos mérete.</span></li></ul>`],
  ["A döntés", `<p>Három lehetőséged van: <strong>kihagyod</strong>, <strong>long</strong> (emelkedésre) vagy <strong>short</strong>
     (esésre) lépsz be. Belépésnél kötelező a stop; a cél és az időkorlát opcionális. A döntés után nem módosítható.</p>
   <p class="muted">A kihagyás is döntés – sokszor a legjobb.</p>`],
  ["Az ítélet és a levezetés", `<p>A döntés után lejátsszuk, mi történt. Az ítélet nem csak az eredményt nézi: egy rossz döntés is
     hozhat szerencsés nyereséget, egy jó döntés is végződhet balszerencsés stoppal.</p>
   <ul class="digest-list"><li><span>Jó döntés · Szerencsés · Balszerencsés · Rossz döntés</span></li>
     <li><span>Lépésenkénti levezetés: mit mutattak a szabályok, mi lett volna a stratégiák és egy véletlen alapvonal eredménye.</span></li></ul>
   <p>Most kipróbálhatod egy próbafeladaton; utána a Tanulás fülön az 1. szint vár.</p>`],
];

function pageWelcome(step) {
  const s = Math.min(Math.max(step, 0), WELCOME.length - 1);
  const last = s === WELCOME.length - 1;
  view.innerHTML = `<div class="onboard">
    <div class="page-head"><h1>Üdv a gyakorlásban!</h1></div>
    <section class="card"><div class="card-body">
      <div class="onboard-dots" aria-label="${s + 1}. lépés a ${WELCOME.length}-ből">${WELCOME.map((_, i) => `<span class="${i <= s ? "on" : ""}"></span>`).join("")}</div>
      <h2>${s + 1}. ${WELCOME[s][0]}</h2>${WELCOME[s][1]}
      <div class="digest-controls">
        ${s > 0 ? `<a class="btn" href="#/welcome/${s - 1}">Vissza</a>` : ""}
        ${last ? `<button class="btn primary" id="trial" type="button">Próbafeladat indítása</button>`
          : `<a class="btn primary" href="#/welcome/${s + 1}">Tovább</a>`}
      </div></div></section></div>`;
  if (last) $("#trial").addEventListener("click", async () => {
    await api("/api/onboarded", { method: "POST" });
    me = null;
    const r = await api("/api/practice/session", { method: "POST", body: JSON.stringify({ rounds: 1 }) });
    location.hash = `#/practice/${r.session.id}`;
  });
}

// ---------------------------------------------------------------------------
// Informacio: hibabejelentes a GitHub Issues oldalon
// ---------------------------------------------------------------------------
const ISSUES_URL = "https://github.com/vkbubu11/StockWatch/issues";

function pageInfo() {
  view.innerHTML = `
    <div class="page-head"><h1>Információ</h1></div>
    <section class="card"><h2>Hibabejelentés</h2><div class="card-body">
      <p>Hibát bejelenteni a rendszer GitHub oldalán, az <strong>Issues</strong> fülön lehet.</p>
      <p><a class="btn primary" href="${ISSUES_URL}" target="_blank" rel="noopener noreferrer">Hiba bejelentése a GitHubon (Issues) ↗</a></p>
      <p class="muted small">Írd le, mit csináltál, mire számítottál, és mi történt helyette – ha lehet, képernyőképpel.
        Jelszót soha ne másolj be.</p>
    </div></section>
    <section class="card"><h2>A szolgáltatásról</h2><div class="card-body muted">
      StockWatch Gyakorlás – oktatási célú chart-olvasási gyakorlás múltbeli adaton: döntés, ítélet és lépésenkénti levezetés.
      Nem befektetési tanács.
    </div></section>`;
}

// ---------------------------------------------------------------------------
// Tanulasi ut (#33): szintek, lecke abraval, celzott feladatsor
// ---------------------------------------------------------------------------
async function pageLearn() {
  const levels = await api("/api/lessons");
  view.innerHTML = `
    <div class="page-head"><h1>Tanulás</h1><span class="muted">Tíz szint: rövid lecke, egy ábra, utána célzott feladatsor.
      A szint akkor teljesül, ha a döntéseid nagy része jó vagy balszerencsés (a szabály szerint indokolt) – a következő addig zárolt.</span></div>
    <div class="learn-grid">${levels.map((l) => `
      <a class="card learn-card ${l.unlocked ? "" : "locked"}" ${l.unlocked ? `href="#/learn/${l.level}"` : `aria-disabled="true"`}>
        <div class="card-body"><div class="learn-level">${l.level}. szint</div><h2>${esc(l.title)}</h2>
          ${l.completed ? `<span class="badge go">Teljesítve</span>` : l.unlocked ? `<span class="badge watch">Elérhető</span>` : `<span class="badge fail">Zárolt</span>`}
          ${l.best_share != null ? `<span class="muted small">legjobb: ${fmt(l.best_share * 100, 0)}%</span>` : ""}</div></a>`).join("")}</div>`;
}

async function pageLesson(level) {
  const l = await api(`/api/lessons/${level}`);
  view.innerHTML = `
    <div class="page-head"><a href="#/learn" class="muted">← Tanulás</a><h1>${l.level}. ${esc(l.title)}</h1>
      ${l.status.completed ? `<span class="badge go">Teljesítve</span>` : ""}</div>
    <section class="card"><div class="card-body lesson">${l.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("")}
      <ul class="digest-list">${l.points.map((p) => `<li><span>${esc(p)}</span></li>`).join("")}</ul></div></section>
    <section class="card chart-card"><div class="chart-legend"><span><span class="swatch" style="background:var(--ema20)"></span> EMA20</span>
      <span><span class="swatch" style="background:var(--ema50)"></span> EMA50</span><span><span class="swatch" style="background:var(--ema200)"></span> EMA200</span>
      <span class="muted">${esc(l.caption)}</span></div>
      <div class="card-body"><svg id="lesson-chart" role="img" aria-label="${esc(l.caption)}"></svg></div></section>
    <section class="card"><div class="card-body"><div class="digest-controls">
      <button class="btn primary" id="lesson-start" type="button">Feladatsor indítása (${l.rounds} helyzet)</button>
      <span class="muted small">A szint teljesül, ha legalább ${fmt(l.pass_share * 100, 0)}% jó vagy balszerencsés döntés.</span></div></div></section>`;
  const svg = $("#lesson-chart");
  const draw = () => practiceChart(svg, l.figure.bars);
  if (chartRO) chartRO.disconnect();
  chartRO = new ResizeObserver(draw);
  chartRO.observe(svg.parentElement);
  draw();
  $("#lesson-start").addEventListener("click", async () => {
    const r = await api(`/api/lessons/${level}/start`, { method: "POST", body: "{}" });
    location.hash = `#/practice/${r.session.id}`;
  });
}

// ---------------------------------------------------------------------------
// Profil: a fiok, kilepes (mindenhonnan is)
// ---------------------------------------------------------------------------
async function pageProfile() {
  const s = await api("/api/session");
  view.innerHTML = `
    <div class="page-head"><h1>Profil</h1><span class="muted">${esc(s.email)}</span></div>
    <section class="card"><h2>Munkamenetek</h2><div class="card-body">
      <p>Ha más eszközön is beléptél (vagy egy eszközt elvesztettél), itt mindenhonnan kiléptethetsz.</p>
      <p><button class="btn" id="logout-all" type="button">Kilépés minden eszközön</button></p>
    </div></section>
    <section class="card"><h2>Adataid</h2><div class="card-body">
      <p>Minden tárolt adatodat (fiók, munkamenetek, gyakorló körök) letöltheted gépi feldolgozásra alkalmas formában.</p>
      <p><a class="btn" href="/api/account/export" download>Adataim letöltése (JSON)</a></p>
      <p class="muted small"><a href="/adatkezeles" target="_blank">Adatkezelési tájékoztató</a> · <a href="/aszf" target="_blank">ÁSZF</a> · <a href="/sutik" target="_blank">Sütik</a></p>
    </div></section>
    <section class="card"><h2>Fiók törlése</h2><div class="card-body">
      <p>A törlés végleges: a fiókod, a munkameneteid és minden gyakorló köröd azonnal törlődik (a biztonsági mentésekből
        legkésőbb 30 napon belül). Megerősítéshez írd be a jelszavadat.</p>
      <form id="delete-form" class="ag-form"><label>Jelszó<input name="password" type="password" autocomplete="current-password" required></label>
        <div class="wide"><button class="btn" type="submit">Fiók végleges törlése</button> <span id="delete-msg" class="small"></span></div></form>
    </div></section>`;
  $("#delete-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!confirm("Biztosan véglegesen törlöd a fiókodat és minden adatodat?")) return;
    try {
      await api("/api/account/delete", { method: "POST", body: JSON.stringify({ password: ev.target.elements.password.value }) });
      location.href = "/";
    } catch (e) { const m = $("#delete-msg"); m.className = "msg err small"; m.textContent = e.message; }
  });
  $("#logout-all").addEventListener("click", async () => {
    if (!confirm("Kilépsz minden eszközön (ezen is)?")) return;
    await api("/api/logout-all", { method: "POST" }).catch(() => {});
    location.href = "/login?e=loggedout";
  });
}

// tema: a rendszerbeallitast koveti, a gomb felulbiralja (bongeszonkent megjegyezve)
function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
try { applyTheme(localStorage.getItem("sw-theme")); } catch (_) { /* privat mod */ }
$("#theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("sw-theme", next); } catch (_) { /* nem baj */ }
});
$("#logout").addEventListener("click", () => {
  api("/logout", { method: "POST" }).catch(() => {}).finally(() => { location.href = "/login"; });
});


// ---------------------------------------------------------------------------
// Gyakorlas: rejtett multbeli helyzetek (practice/) - a papir es a datum csak a dontes utan derul ki
// ---------------------------------------------------------------------------
let practiceTimer = null;
const PR_REASON = { stop: "stop", gap_stop: "stop (nyitási rés)", target: "cél", time: "időkorlát", signal: "kilépési jel" };
const PR_STRAT = { pullback: "Visszahúzás (RSI2)", deep: "Mély visszahúzás", breakout: "Kitörés", short: "Short (kísérleti)" };
const PR_CAT = { strategy: "stratégia-jelzés", pattern: "formálódó alakzat", short: "short-gyanús helyzet", nothing: "semmi különös" };
const PR_SIDE = { long: "Long", short: "Short", enter: "Long", skip: "Kihagyás" };
// itelet: szin + ikon + szoveg (nem csak szin)
const PR_VERDICT = { good: ["Jó döntés", "ok", "✓"], lucky: ["Szerencsés", "warn", "≈"], unlucky: ["Balszerencsés", "info", "↺"], bad: ["Rossz döntés", "bad", "✗"] };
const PR_LEVEL = { stop: ["Stop", "var(--down)"], target: ["Cél", "var(--up)", true], limit: ["Limit", "var(--info)"],
  sma200: ["SMA200", "var(--ema200)", true], high20: ["20 napos csúcs", "var(--muted)", true], low20: ["20 napos mélypont", "var(--muted)", true] };
const PR_STATUS = { unfilled: "a limit nem teljesült", open: "nyitva maradt", filtered: "a szűrők kizárták", unknown: "a labor még nem értékelte" };
const rCls = (r) => (r == null ? "" : r > 0 ? "up" : r < 0 ? "down" : "");
const fmtIn = (v) => (Math.round(v * 100) / 100).toFixed(2);       // <input type="number"> erteke: pont a tizedesjel

async function pagePractice(msg = "") {
  clearInterval(practiceTimer);
  const d = await api("/api/practice");
  const p = d.pool;
  const total = p ? Object.values(p.counts).reduce((a, b) => a + b, 0) : 0;
  view.innerHTML = `
    <div class="page-head"><h1>Gyakorlás</h1>
      <span class="muted">Rejtett, múltbeli chart-helyzetek: eldöntöd, kihagyod-e, vagy long / short belépsz-e (stoppal, opcionális céllal és időkorláttal), utána megnézed, mi történt, ítéletet és lépésenkénti levezetést kapsz, és összeveted a stratégiákkal és egy véletlen alapvonallal.
        A papír és a dátum csak a döntés után derül ki. Szimuláció múltbeli adaton, nem ajánlás.</span></div>
    ${msg ? `<section class="card"><div class="card-body">${esc(msg)}</div></section>` : ""}
    <section class="card"><h2>Feladatkészlet</h2><div class="card-body">
      ${p ? `<p>${p.symbols} papír, ${total} helyzet: stratégia-jelzés ${p.counts.strategy}, formálódó alakzat ${p.counts.pattern}, short-gyanús helyzet ${p.counts.short ?? 0}, semmi különös ${p.counts.nothing}.</p>
        <p class="muted small">Készült: ${when(p.created)} · az adat a zárolt holdout előtt véget ér (${esc(p.visible_end)})${p.partial ? " · részleges: elfogyott az időkeret" : ""}</p>`
        : `<p>Még nincs feladatkészlet. Az üzemeltető a szerveren építi fel: <code>python cli.py practice-pool</code></p>`}
    </div></section>
    ${p ? `<section class="card"><h2>Új kör</h2><div class="card-body">
      <form id="pr-form" class="ag-form">
        <label>Helyzetek száma<input name="rounds" type="number" min="1" max="${d.defaults.max_rounds}" value="${d.defaults.rounds}"></label>
        <label>Seed (üres: véletlen)<input name="seed" type="number" min="0"></label>
        <label>Megosztási kód (ha van)<input name="code" placeholder="abcdefg-hjkmnpqr-st" autocomplete="off"></label>
        <label>Stratégia-jelzés (%)<input name="m_strategy" type="number" min="0" value="${d.defaults.mix.strategy}"></label>
        <label>Formálódó alakzat (%)<input name="m_pattern" type="number" min="0" value="${d.defaults.mix.pattern}"></label>
        <label>Short-gyanús (%)<input name="m_short" type="number" min="0" value="${d.defaults.mix.short ?? 0}"></label>
        <label>Semmi különös (%)<input name="m_nothing" type="number" min="0" value="${d.defaults.mix.nothing}"></label>
        <div class="wide"><button class="btn primary" type="submit">Kör indítása</button> <span id="pr-msg" class="small"></span>
          <span class="muted small">Megosztási kóddal a helyzetek száma és sorrendje a kódból jön.</span></div>
      </form></div></section>` : ""}
    <section class="card"><h2>Körök</h2><div class="card-body">${d.sessions.length ? `<table class="table"><thead><tr><th>Készült</th><th>Állás</th><th>Átlag R</th><th>Megosztási kód</th><th></th></tr></thead><tbody>
      ${d.sessions.map((s) => `<tr class="clickable" data-href="#/practice/${esc(s.id)}"><td class="small">${when(s.created)}</td>
        <td>${s.decided} / ${s.rounds}${s.done ? ` <span class="badge go">kész</span>` : ""}</td><td class="num ${rCls(s.mean_r)}">${signed(s.mean_r, 2, " R")}</td>
        <td class="small"><code>${esc(s.code)}</code></td><td><button class="btn" type="button" data-pr-delete="${esc(s.id)}">Törlés</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="muted">Még nincs kör.</div>`}</div></section>`;
  bindRowLinks();
  const form = $("#pr-form");
  if (form) form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const v = (n) => form.elements[n].value.trim();
    const body = { rounds: Number(v("rounds")), seed: v("seed") === "" ? null : Number(v("seed")), code: v("code") || null,
      mix: { strategy: Number(v("m_strategy")), pattern: Number(v("m_pattern")), short: Number(v("m_short")), nothing: Number(v("m_nothing")) } };
    try { const r = await api("/api/practice/session", { method: "POST", body: JSON.stringify(body) }); location.hash = `#/practice/${r.session.id}`; }
    catch (e) { const m = $("#pr-msg"); m.className = "msg err small"; m.textContent = e.message; }
  });
  view.querySelectorAll("[data-pr-delete]").forEach((b) => b.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!confirm("Törlöd ezt a kört?")) return;
    const r = await api("/api/practice/delete", { method: "POST", body: JSON.stringify({ id: b.dataset.prDelete }) });
    await pagePractice(r.message);
  }));
}

// a rejtett chart: index-tengely (0 = a dontes napja), normalizalt ar, relativ volumen; a felfedeskor a jovo
function practiceChart(svg, bars, opt = {}) {
  const future = (opt.future || []).slice(0, opt.shown ?? (opt.future || []).length);
  const all = bars.concat(future);
  const W = Math.max(320, svg.parentElement.clientWidth);
  const H = W < 600 ? 340 : 420;
  const pad = { l: 8, r: 64, t: 12, b: 24 };
  const volH = Math.round(H * 0.14);
  const priceH = H - pad.t - pad.b - volH - 8;
  const slots = bars.length + (opt.future ? opt.future.length : 0);
  const step = (W - pad.l - pad.r) / slots;
  const x = (j) => pad.l + step * j + step / 2;
  let lo = Infinity, hi = -Infinity;
  all.forEach((k) => { lo = Math.min(lo, k.l); hi = Math.max(hi, k.h); });
  const levels = (opt.levels || []).filter((h) => h.v != null && Number.isFinite(h.v));
  levels.forEach((h) => { lo = Math.min(lo, h.v); hi = Math.max(hi, h.v); });
  const span = hi - lo || 1;
  lo -= span * 0.04; hi += span * 0.04;
  const y = (v) => pad.t + (hi - v) / (hi - lo) * priceH;
  const volTop = pad.t + priceH + 8;
  const vmax = Math.max(...bars.map((k) => k.v || 0)) || 1;
  const vy = (v) => volTop + volH - (Math.min(v, vmax) / vmax) * volH;
  const parts = [];
  const ticks = niceTicks(lo, hi, 6);
  parts.push(`<g class="grid">${ticks.map((t) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`);
  parts.push(`<g class="axis">${ticks.map((t) => `<text x="${W - pad.r + 6}" y="${y(t) + 4}">${fmt(t, 0)}</text>`).join("")}</g>`);
  const zero = bars.length - 1;
  const labels = [];
  for (let j = 0; j < slots; j += 1) { const rel = j - zero; if (rel % 20 === 0 || (rel > 0 && rel % 10 === 0)) labels.push(j); }
  parts.push(`<g class="axis">${labels.map((j) => `<text x="${x(j)}" y="${H - 6}" text-anchor="middle">${j - zero > 0 ? "+" : ""}${j - zero}</text>`).join("")}</g>`);
  parts.push(`<line x1="${x(zero) + step / 2}" x2="${x(zero) + step / 2}" y1="${pad.t}" y2="${volTop + volH}" stroke="var(--muted)" stroke-dasharray="3 3"/>`);
  parts.push(`<g>${bars.map((k, j) => (k.v == null ? "" : `<rect class="vol ${k.c >= k.o ? "up" : "down"}" x="${x(j) - step * 0.35}" y="${vy(k.v)}" width="${Math.max(1, step * 0.7)}" height="${volTop + volH - vy(k.v)}"/>`)).join("")}</g>`);
  const bw = Math.max(1, step * 0.62);
  parts.push(`<g>${all.map((k, j) => {
    const top = y(Math.max(k.o, k.c)), bot = y(Math.min(k.o, k.c));
    return `<g class="candle ${k.c >= k.o ? "up" : "down"}${j > zero ? " future" : ""}"><line x1="${x(j)}" x2="${x(j)}" y1="${y(k.h)}" y2="${y(k.l)}"/><rect x="${x(j) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}"/></g>`;
  }).join("")}</g>`);
  ["ema20", "ema50", "ema200"].forEach((key) => {
    let dpath = "";
    bars.forEach((k, j) => { if (k[key] != null) dpath += `${dpath ? "L" : "M"}${x(j).toFixed(1)},${y(k[key]).toFixed(1)}`; });
    if (dpath) parts.push(`<path class="ema ${key}" d="${dpath}"/>`);
  });
  (opt.marks || []).forEach((m) => {
    const j = zero + m.k;
    if (m.k == null || j >= all.length || m.v == null) return;
    parts.push(`<circle cx="${x(j)}" cy="${y(m.v)}" r="4.5" fill="${m.color}" stroke="var(--surface)" stroke-width="1.5"><title>${esc(m.label)}</title></circle>`);
  });
  levels.forEach((h) => {
    const yy = y(h.v);
    parts.push(`<line class="hline ${h.dash ? "dash" : ""}" x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}" stroke="${h.color}"/>`);
    parts.push(`<rect class="hlabel-bg" x="${W - pad.r + 1}" y="${yy - 9}" width="${pad.r - 2}" height="18" rx="4" fill="${h.color}"/>`);
    parts.push(`<text class="hlabel" x="${W - pad.r + 5}" y="${yy + 4}" fill="#fff">${esc(h.label)}</text>`);
  });
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");
}

async function pagePracticeSession(id) {
  clearInterval(practiceTimer);
  const s = await api(`/api/practice/session/${id}/summary`);
  if (s.session.done) { renderPracticeSummary(s); return; }
  await pagePracticeTask(id, s.session.next, s.session);
}

function practiceLevels(f) {
  const out = [];
  if (!f || !["enter", "long", "short"].includes(f.action)) return out;
  if (f.entry === "limit" && f.limit) out.push({ v: f.limit, label: "Limit", color: "var(--info)" });
  if (f.stop) out.push({ v: f.stop, label: "Stop", color: "var(--down)" });
  if (f.target) out.push({ v: f.target, label: "Cél", color: "var(--up)", dash: true });
  return out;
}

async function pagePracticeTask(id, k, sess) {
  clearInterval(practiceTimer);
  const t = await api(`/api/practice/session/${id}/task/${k}`);
  const atr = t.chart.atr || 1;
  view.innerHTML = `
    <div class="page-head"><a href="#/practice" class="muted">← Gyakorlás</a><h1>Helyzet ${k + 1} / ${t.rounds}</h1>
      <span class="muted">Az utolsó látható záró = 100; a vízszintes tengely kereskedési napokban (0 = a döntés napja). ATR (14): ${fmt(atr, 2)}</span></div>
    ${sess.warning === "other_pool" ? `<section class="card"><div class="card-body">Ez a megosztási kód más feladatkészlethez készült: itt ugyanaz a kód más helyzeteket ad.</div></section>` : ""}
    <section class="card chart-card"><div class="chart-legend"><span><span class="swatch" style="background:var(--ema20)"></span> EMA20</span>
      <span><span class="swatch" style="background:var(--ema50)"></span> EMA50</span><span><span class="swatch" style="background:var(--ema200)"></span> EMA200</span>
      <span class="muted">Volumen: a 20 napos átlaghoz viszonyítva</span></div>
      <div class="card-body"><svg id="pr-chart" role="img" aria-label="Rejtett chart"></svg></div></section>
    <section class="card" id="pr-decision"><h2>Döntés</h2><div class="card-body">
      <form id="pr-dform" class="ag-form">
        <div class="wide"><span class="small muted">Döntés</span><br><div class="segmented pr-sides" role="group" aria-label="Döntés">
          <button type="button" class="seg" data-side="skip">Kihagyom</button><button type="button" class="seg on" data-side="long">Long</button><button type="button" class="seg" data-side="short">Short</button></div>
          <input type="hidden" name="action" value="long"></div>
        <label>Belépés módja<select name="entry"><option value="open">Következő nyitás</option><option value="limit">Limit (egynapos, a következő napra; shortnál a belépő fölött teljesül)</option></select></label>
        <label>Limit ár<input name="limit" type="number" step="0.01" value="${fmtIn(100 - 0.5 * atr)}"></label>
        <label>Stop (kötelező)<input name="stop" type="number" step="0.01" value="${fmtIn(100 - 2 * atr)}"></label>
        <label>Cél (üres: nincs)<input name="target" type="number" step="0.01"></label>
        <label>Időkorlát (nap)<input name="days" type="number" min="1" max="${t.max_days}" value="10"></label>
        <div class="wide"><span id="pr-risk" class="small muted"></span></div>
        <div class="wide"><button class="btn primary" type="submit">Döntés rögzítése</button> <span class="muted small">A döntés után nem módosítható.</span> <span id="pr-err" class="small"></span></div>
      </form></div></section>
    <div id="pr-reveal"></div>`;
  const svg = $("#pr-chart");
  const form = $("#pr-dform");
  const read = () => {
    const n = (name) => { const raw = form.elements[name].value.trim(); return raw === "" ? null : Number(raw); };
    return { action: form.elements.action.value, entry: form.elements.entry.value, limit: n("limit"), stop: n("stop"), target: n("target"), days: n("days") };
  };
  let chartOpt = {};
  const draw = () => practiceChart(svg, t.chart.bars, chartOpt);
  let edited = false;
  form.querySelectorAll("input[name=stop], input[name=target], input[name=limit]").forEach((el) => el.addEventListener("input", () => { edited = true; }));
  const setSide = (side) => {
    const prev = form.elements.action.value;
    form.elements.action.value = side;
    form.querySelectorAll(".pr-sides .seg").forEach((b) => { b.classList.toggle("on", b.dataset.side === side); b.setAttribute("aria-pressed", b.dataset.side === side); });
    if (side !== "skip" && side !== prev && (!edited || prev === "skip")) {
      const sign = side === "long" ? 1 : -1;
      form.elements.limit.value = fmtIn(100 - sign * 0.5 * atr);
      form.elements.stop.value = fmtIn(100 - sign * 2 * atr);
      form.elements.target.value = "";
      edited = false;
    }
    preview();
  };
  form.querySelectorAll(".pr-sides .seg").forEach((b) => b.addEventListener("click", () => { if (!b.disabled) setSide(b.dataset.side); }));
  const preview = () => {
    const f = read();
    const enter = f.action !== "skip";
    const sign = f.action === "short" ? -1 : 1;
    ["entry", "limit", "stop", "target", "days"].forEach((name) => { form.elements[name].closest("label").hidden = !enter || (name === "limit" && f.entry !== "limit"); });
    const ref = f.entry === "limit" ? f.limit : 100;
    $("#pr-risk").textContent = enter && f.stop != null && ref ? `Stop-távolság: ${fmt(sign * (ref - f.stop) / atr, 2)} ATR (${fmt(sign * (ref - f.stop) / ref * 100, 1)}%)`
      + (f.target ? ` · cél: ${fmt((f.target - ref) / (ref - f.stop), 2)} R` : "") : "";
    chartOpt = { levels: practiceLevels(f) };
    draw();
  };
  form.addEventListener("input", preview);
  form.addEventListener("change", preview);
  if (chartRO) chartRO.disconnect();
  chartRO = new ResizeObserver(() => draw());
  chartRO.observe(svg.parentElement);
  preview();
  if (t.decided) { showPracticeReveal(id, k, t, t.reveal, (o) => { chartOpt = o; draw(); }, true); return; }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = read();
    const body = f.action === "skip" ? { action: "skip" } : { ...f, limit: f.entry === "limit" ? f.limit : null };
    try {
      const r = await api(`/api/practice/session/${id}/task/${k}/decision`, { method: "POST", body: JSON.stringify(body) });
      showPracticeReveal(id, k, t, r.reveal, (o) => { chartOpt = o; draw(); }, false, r.session);
    } catch (e) { const m = $("#pr-err"); m.className = "msg err small"; m.textContent = e.message; }
  });
}

function prLevels(list) {
  return (list || []).map((h) => { const m = PR_LEVEL[h.kind] || [h.kind, "var(--muted)"]; return { v: h.v, label: m[0], color: m[1], dash: !!m[2] }; });
}

function prMarks(list) {
  return (list || []).map((m) => ({ k: m.k, v: m.v, label: m.kind === "entry" ? "Belépés" : "Kilépés",
    color: m.kind === "entry" ? "var(--info)" : m.r > 0 ? "var(--up)" : "var(--down)" }));
}

function showPracticeReveal(id, k, t, rev, redraw, instant, sess) {
  const d = rev.decision || {};
  const u = rev.user;
  const form = $("#pr-dform");
  form.querySelectorAll("input, select, button").forEach((el) => { el.disabled = true; });
  const side = d.action === "enter" ? "long" : d.action;
  form.querySelectorAll(".pr-sides .seg").forEach((b) => b.classList.toggle("on", b.dataset.side === side));
  const levels = practiceLevels(d.action !== "skip" ? d : null);
  const marks = u && u.entry_k != null ? [{ k: u.entry_k, v: u.entry, color: "var(--info)", label: "Belépés" }] : [];
  if (u && u.exit_k != null) marks.push({ k: u.exit_k, v: u.exit, color: u.r > 0 ? "var(--up)" : "var(--down)", label: "Kilépés" });
  const n = rev.future.length;
  let shown = instant ? n : 0;
  const box = $("#pr-reveal");
  const finish = () => {
    clearInterval(practiceTimer);
    shown = n;
    redraw({ levels, future: rev.future, shown, marks });
    box.innerHTML = practiceOutcome(k, t, rev, sess);
    $("#pr-next").addEventListener("click", () => pagePracticeSession(id));
    // a levezetes lepesei es az alternativak sorai: a chart a hozzajuk tartozo szinteket emeli ki
    box.querySelectorAll("[data-pr-focus]").forEach((el) => el.addEventListener("click", () => {
      box.querySelectorAll("[data-pr-focus].focused").forEach((x) => x.classList.remove("focused"));
      el.classList.add("focused");
      const f = JSON.parse(el.dataset.prFocus);
      redraw({ levels: f.mine ? levels : prLevels(f.levels), future: rev.future, shown: n, marks: f.mine ? marks : prMarks(f.marks) });
    }));
  };
  if (instant) { finish(); return; }
  box.innerHTML = `<section class="card"><div class="card-body"><button class="btn" id="pr-skipanim" type="button">Mutasd egyben</button> <span class="muted small">Ami a döntés után történt…</span></div></section>`;
  $("#pr-skipanim").addEventListener("click", finish);
  clearInterval(practiceTimer);
  practiceTimer = setInterval(() => {
    if (!location.hash.startsWith(`#/practice/${id}`)) { clearInterval(practiceTimer); return; }
    shown += 1;
    redraw({ levels, future: rev.future, shown, marks: marks.filter((m) => m.k <= shown) });
    if (shown >= n) finish();
  }, 70);
}

function practiceVerdict(rv) {
  if (!rv || !rv.verdict) return "";
  const v = rv.verdict;
  const [label, cls, icon] = PR_VERDICT[v.category] || [v.category, "plain", ""];
  const ok = (b) => `<span class="pill ${b ? "ok" : "bad"}">${b ? "helyes" : "nem helyes"}</span>`;
  return `<div class="pr-verdict ${cls}" role="status"><span class="pr-verdict-icon" aria-hidden="true">${icon}</span>
      <div><div class="pr-verdict-label">${esc(label)}</div><div class="small">${esc(rv.verdict_line.text)}</div></div></div>
    <div class="pr-verdict-parts small"><span><span>Kimenet szerint</span> ${ok(v.outcome_ok)}</span><span><span>Szabály szerint</span> ${ok(v.rule_ok)}</span></div>`;
}

function practiceSteps(rv) {
  if (!rv || !rv.steps) return "";
  const focus = (o) => esc(JSON.stringify(o));
  const table = (rows) => rows.filter((r) => r.checks.length || r.signaled).map((r) => `
    <h4>${esc(PR_STRAT[r.strategy] || r.strategy)} – ${r.signaled ? "jelzett" : "nem jelzett"}${r.status && PR_STATUS[r.status] ? ` (${esc(PR_STATUS[r.status])})` : ""}</h4>
    ${r.checks.length ? `<div class="table-scroll"><table class="table compact"><thead><tr><th>Szűrő</th><th>Érték</th><th>Feltétel</th><th>Teljesült</th></tr></thead><tbody>
      ${r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td class="num">${esc(c.value)}</td><td class="small">${esc(c.limit || "")}</td>
        <td>${c.passed == null ? `<span class="muted">nem ismert</span>` : c.passed ? `<span class="up"><span aria-hidden="true">✓</span> <span>igen</span></span>` : `<span class="down"><span aria-hidden="true">✗</span> <span>nem</span></span>`}</td></tr>`).join("")}
    </tbody></table></div>` : ""}`).join("");
  const rows = (rs) => `<div class="pr-alt-rows">${rs.map((r) => `<button type="button" class="pr-alt" data-pr-focus="${focus({ levels: r.levels || [], marks: r.marks || [] })}">
      <span>${esc(PR_SIDE[r.key] || PR_STRAT[r.key] || r.key)}</span><strong class="${rCls(r.r)}">${r.r == null ? "–" : signed(r.r, 2, " R")}</strong></button>`).join("")}</div>`;
  return `<section class="card"><h2>Levezetés</h2><div class="card-body">
    <p class="muted small">Kattints egy lépésre: a chart kiemeli a hozzá tartozó szinteket.</p>
    <ol class="pr-steps">${rv.steps.map((st) => `<li class="pr-step" data-pr-focus="${focus({ levels: st.levels, marks: st.marks, mine: st.id === "trade" })}" tabindex="0">
      <h3>${esc(st.title.text)}</h3>
      <ul class="digest-list">${st.lines.map((l) => `<li><span>${esc(l.text)}</span></li>`).join("")}</ul>
      ${st.table ? table(st.table) : ""}${st.rows ? rows(st.rows) : ""}</li>`).join("")}</ol>
  </div></section>`;
}

function practiceOutcome(k, t, rev, sess) {
  const d = rev.decision || {};
  const u = rev.user;
  const side = PR_SIDE[d.action] || "";
  const mine = d.action === "skip" ? ["Kihagytad", "", "–"]
    : u.status === "closed" ? [signed(u.r, 2, " R"), rCls(u.r), `${esc(side)} · ${esc(PR_REASON[u.reason] || u.reason || "")} · ${u.held} nap · nettó ${pct(u.net_pct, 2)}`]
      : [esc(PR_STATUS[u.status] || u.status), "", `${esc(side)} · nem volt kötés`];
  const ref = rev.reference, base = rev.baseline;
  const rows = rev.strategies.map((s) => `<tr><td>${esc(PR_STRAT[s.strategy] || s.strategy)}</td>
    <td>${s.signaled ? "jelzett" : s.blocked === "earnings" ? "nem (gyorsjelentés-tilalom)" : "nem jelzett"}</td>
    <td class="num ${rCls(s.r)}">${s.signaled ? (s.r != null ? signed(s.r, 2, " R") : esc(PR_STATUS[s.status] || "–")) : "–"}</td>
    <td class="small muted">${s.signaled && s.reason ? `${esc(PR_REASON[s.reason] || s.reason)}${s.held ? ` · ${s.held} nap` : ""}` : ""}</td></tr>`).join("");
  const last = sess ? sess.done : k + 1 >= t.rounds;
  return `<section class="card"><h2>Ítélet</h2><div class="card-body">${practiceVerdict(rev.review)}</div></section>
    <section class="card"><h2>Eredmény</h2><div class="card-body">
    <div class="kpis check-kpis">
      <div class="kpi"><div class="label">A te döntésed</div><div class="value ${mine[1]}">${mine[0]}</div><div class="sub">${mine[2]}</div></div>
      <div class="kpi"><div class="label">Referencia-kötés</div><div class="value ${rCls(ref.r)}">${signed(ref.r, 2, " R")}</div>
        <div class="sub">következő nyitás, ${fmt(ref.stop_atr, 0)} ATR stop, ${ref.days} nap</div></div>
      <div class="kpi"><div class="label">Véletlen alapvonal</div><div class="value ${rCls(base.mean_r)}">${signed(base.mean_r, 2, " R")}</div>
        <div class="sub">${base.n} véletlen nap ugyanezen a papíron, azonos stop-távolsággal</div></div>
    </div>
    <div class="table-scroll"><table class="table"><thead><tr><th>Stratégia ugyanezen a ponton</th><th>Jelzés</th><th>Eredmény</th><th>Kilépés</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="pr-name"><strong>${esc(rev.symbol)}</strong>${rev.date ? ` · ${esc(rev.date)}` : ""} · ${esc(PR_CAT[rev.category] || rev.category)}${rev.source && rev.category !== "strategy" ? ` (${esc(rev.source)})` : ""}</p>
  </div></section>
  ${practiceSteps(rev.review)}
  <section class="card"><div class="card-body"><div class="digest-controls"><button class="btn primary" id="pr-next" type="button">${last ? "Összegzés" : "Következő helyzet"}</button>
      <span class="muted small">Nem befektetési tanács. Szimulált kötés múltbeli adaton.</span></div></div></section>`;
}

function renderPracticeSummary(p) {
  const s = p.summary;
  const lines = `<ul class="digest-list">${p.lines.map((l) => `<li><span>${esc(l.text)}</span></li>`).join("")}</ul>`;
  const maxb = Math.max(1, ...s.stop_buckets.map((b) => b.n));
  const buckets = s.stop_buckets.map((b) => `<div class="part"><span>${b.hi == null ? `≥ ${fmt(b.lo, 0)} ATR` : `${fmt(b.lo, 0)}–${fmt(b.hi, 0)} ATR`}</span>
    <span class="track"><span class="fill" style="width:${(b.n / maxb) * 100}%"></span></span><span class="muted small">${b.n}</span></div>`).join("");
  const ls = p.lesson;
  const lessonBox = ls ? `<section class="card"><div class="card-body"><div class="pr-verdict ${ls.passed ? "ok" : "warn"}" role="status">
      <span class="pr-verdict-icon" aria-hidden="true">${ls.passed ? "✓" : "↺"}</span>
      <div><div class="pr-verdict-label">${ls.level}. szint: ${ls.passed ? "teljesítve" : "még nem teljesült"}</div>
        <div class="small">Jó vagy balszerencsés döntés: ${fmt(ls.share * 100, 0)}% (kell: ${fmt(ls.needed * 100, 0)}%).</div></div></div>
      <div class="digest-controls">${ls.next ? `<a class="btn primary" href="#/learn/${ls.next}">Tovább a ${ls.next}. szintre</a>` : ""}
        <a class="btn" href="#/learn/${ls.level}">${ls.passed ? "A lecke újra" : "Újrapróbálom"}</a></div></div></section>` : "";
  view.innerHTML = `
    <div class="page-head"><a href="${ls ? "#/learn" : "#/practice"}" class="muted">← ${ls ? "Tanulás" : "Gyakorlás"}</a><h1>A kör összegzése</h1>
      <span class="muted">Megosztási kód: <code>${esc(p.session.code)}</code> – ugyanazzal a feladatkészlettel ugyanazt a helyzetsort adja.</span></div>
    ${lessonBox}
    <div class="kpis">
      <div class="kpi"><div class="label">Teljesült kötés</div><div class="value">${s.filled}</div><div class="sub">${s.entered} belépési döntés, ${s.skipped} kihagyás</div></div>
      <div class="kpi"><div class="label">Nyerési arány</div><div class="value">${s.win == null ? "–" : `${fmt(s.win * 100, 1)}%`}</div><div class="sub">átlag ${signed(s.mean_r, 2, " R")}</div></div>
      <div class="kpi"><div class="label">Véletlen alapvonal</div><div class="value ${rCls(s.baseline_r)}">${signed(s.baseline_r, 2, " R")}</div><div class="sub">ugyanazokon a papírokon</div></div>
      <div class="kpi"><div class="label">Kihagyott jó helyzet</div><div class="value">${s.missed_good}</div><div class="sub">${s.good_total} helyzetben volt legalább +1 R</div></div>
      ${s.reviewed ? `<div class="kpi"><div class="label">Ítélet</div><div class="value">${s.verdicts.good} / ${s.reviewed}</div>
        <div class="sub">jó döntés · szerencsés ${s.verdicts.lucky} · balszerencsés ${s.verdicts.unlucky} · rossz ${s.verdicts.bad}</div></div>` : ""}
    </div>
    <section class="card"><h2>Összefoglaló</h2><div class="card-body">${lines}</div></section>
    <section class="card"><h2>Stop-távolság (ATR)</h2><div class="card-body"><div class="parts">${buckets}</div></div></section>
    <section class="card"><h2>Helyzetek</h2><div class="card-body"><div class="table-scroll"><table class="table"><thead><tr><th>#</th><th>Papír</th><th>Dátum</th><th>Helyzet</th><th>Döntés</th><th>Ítélet</th><th>Eredmény</th><th>Stratégiák</th><th>Referencia</th><th>Alapvonal</th></tr></thead><tbody>
      ${p.tasks.map((t) => `<tr><td>${t.k + 1}</td><td class="ticker">${esc(t.symbol || "")}</td><td>${esc(t.date || "")}</td><td>${esc(PR_CAT[t.category] || "")}</td>
        <td>${t.action ? esc(PR_SIDE[t.action] || t.action) : "–"}</td>
        <td>${t.verdict && PR_VERDICT[t.verdict] ? `<span class="pill plain ${PR_VERDICT[t.verdict][1]}"><span aria-hidden="true">${PR_VERDICT[t.verdict][2]}</span> <span>${esc(PR_VERDICT[t.verdict][0])}</span></span>` : "–"}</td>
        <td class="num ${rCls(t.r)}">${t.r != null ? signed(t.r, 2, " R") : t.status ? esc(PR_STATUS[t.status] || t.status) : "–"}</td>
        <td class="small">${(t.strategies || []).map((x) => `${esc(PR_STRAT[x.strategy] || x.strategy)}: ${x.r != null ? signed(x.r, 2, " R") : esc(PR_STATUS[x.status] || "–")}`).join(" · ") || "–"}</td>
        <td class="num ${rCls(t.reference_r)}">${signed(t.reference_r, 2, " R")}</td><td class="num ${rCls(t.baseline_r)}">${signed(t.baseline_r, 2, " R")}</td></tr>`).join("")}
    </tbody></table></div></div></section>`;
}

// ---------------------------------------------------------------------------
// Reszponziv felulet (#23): "Tovabbiak" menu telefonon, tablak gorgetheto / kartyas nezete
// ---------------------------------------------------------------------------
function closeMoreSheet() {
  const sheet = $("#more-sheet");
  if (!sheet || sheet.hidden) return;
  sheet.hidden = true;
  $("#more-btn").setAttribute("aria-expanded", "false");
}

(function setupMoreSheet() {
  const list = $(".more-list");
  const btn = $("#more-btn");
  if (!list || !btn) return;
  // a teljes menu a fo navigaciobol (egy forras)
  list.innerHTML = [...document.querySelectorAll(".tabs a")]
    .map((a) => `<a href="${esc(a.getAttribute("href"))}" data-tab="${esc(a.dataset.tab)}">${esc(a.textContent.trim())}</a>`).join("");
  btn.addEventListener("click", () => {
    const sheet = $("#more-sheet");
    sheet.hidden = !sheet.hidden;
    btn.setAttribute("aria-expanded", String(!sheet.hidden));
    if (!sheet.hidden) { const first = sheet.querySelector("a.active") || sheet.querySelector("a"); if (first) first.focus(); }
  });
  $("#more-sheet").addEventListener("click", (e) => { if (e.target.id === "more-sheet") closeMoreSheet(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMoreSheet(); });
})();

// minden tablazat gorgetheto tartoba kerul (az oldal soha nem gorog oldalra); a .cards-on-phone tablak
// telefonon kartyakent jelennek meg - a cimkek a fejlecbol
function adaptTables(root) {
  root.querySelectorAll("table").forEach((table) => {
    const parent = table.parentElement;
    if (table.classList.contains("best-plan") || parent.closest(".table-wrap, .table-scroll")) {
      // mar gorgetheto tartoban van
    } else {
      const wrap = document.createElement("div");
      wrap.className = "table-scroll";
      parent.insertBefore(wrap, table);
      wrap.appendChild(table);
    }
    if (table.classList.contains("cards-on-phone")) {
      const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
      table.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => {
        if (!td.dataset.label && heads[i]) td.dataset.label = heads[i];
      }));
    }
  });
}
new MutationObserver(() => adaptTables(view)).observe(view, { childList: true, subtree: true });

// Az elso utvonal-kezeles a fajl VEGEN: a kesobb deklaralt let/const-ok (pl. practiceTimer, NEWS_LABEL) csak itt
// inicializaltak - kozvetlen betolteskor (#/practice, #/events) kulonben "before initialization" hiba jonne.
window.addEventListener("hashchange", route);
route();
