/* StockWatch oktatasi felulet - vanilla JS, keretrendszer nelkul.
 * Utvonalak (hash): #/ (= #/learn)  #/learn/N  #/practice  #/practice/ID  #/daily  #/leaderboard  #/welcome  #/profile  #/info
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const tooltip = $("#tooltip");
let chartRO = null;

// ---------------------------------------------------------------------------
// Segedek
// ---------------------------------------------------------------------------
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

// bongeszos hibaszamlalo (#48): csak a darabszam (uzenet es adat nelkul), oldalbetoltesenkent legfeljebb harom
let reportedErrors = 0;
const reportError = () => {
  if (reportedErrors++ < 3) fetch("/api/client-error", { method: "POST", headers: { "X-StockWatch": "1" }, body: "{}", keepalive: true }).catch(() => {});
};
window.addEventListener("error", reportError);
window.addEventListener("unhandledrejection", reportError);

function bindRowLinks() {
  view.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}


// ---------------------------------------------------------------------------
// Utvonalvalasztas
// ---------------------------------------------------------------------------
async function route() {
  const hash = location.hash || "#/";
  const tab = hash.startsWith("#/info") ? "info" : hash.startsWith("#/profile") ? "profile"
    : hash.startsWith("#/practice") ? "practice" : hash.startsWith("#/daily") || hash.startsWith("#/duel") ? "daily"
    : hash.startsWith("#/leaderboard") ? "leaderboard" : "learn";
  document.querySelectorAll(".tabs a, .bottom-nav a, .more-list a").forEach((a) => {
    a.classList.toggle("active", a.dataset.tab === tab);
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  closeMoreSheet();
  tooltip.hidden = true;
  view.innerHTML = `<div class="loading">Betöltés…</div>`;      // a regi nezet ne latszodjon az uj adat helyett
  try {
    const pm = hash.match(/^#\/practice\/([a-z]{12})$/);
    const dm = hash.match(/^#\/duel\/([a-z]{12})$/);
    const me = await whoami();
    if (!me.onboarded && !pm && !dm) { pageWelcome(Number((hash.match(/^#\/welcome\/(\d)$/) || [0, 0])[1])); return; }
    const lm = hash.match(/^#\/learn\/(\d{1,2})$/);
    if (tab === "info") pageInfo(me.contact);
    else if (tab === "learn") { if (lm) await pageLesson(Number(lm[1])); else await pageLearn(); }
    else if (tab === "profile") await pageProfile();
    else if (tab === "daily") { if (dm) await pageDuel(dm[1]); else await pageDaily(); }
    else if (tab === "leaderboard") await pageLeaderboard();
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
  ["A chart olvasása", `<p>Rejtett helyzetet kapsz: a chart a döntés napjáig látszik, a papír neve és a dátum nem.
     Az ár <strong>normalizált</strong>: az utolsó záró mindig 100.</p>
   <ul class="digest-list"><li><span>Gyertyák, EMA20 / 50 / 200 (rövid, közép, hosszú trend), volumen.</span></li>
     <li><span>ATR: a napi ingás szokásos mérete – a stopot ehhez érdemes mérni.</span></li></ul>`],
  ["A döntés", `<p><strong>Kihagyod</strong>, vagy <strong>long</strong> (emelkedésre) / <strong>short</strong> (esésre) lépsz be,
     kötelező stoppal. A döntés után nem módosítható – és a kihagyás is döntés.</p>`],
  ["Az ítélet", `<p>Lejátsszuk, mi történt. Az ítélet a döntés minőségét nézi, nem csak az eredményt:
     <strong>jó · szerencsés · balszerencsés · rossz</strong>, lépésenkénti levezetéssel.</p>
   <p>Jöjjön egy próbafeladat; utána a Tanulás fülön az 1. szint vár.</p>`],
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
// Informacio: visszajelzes e-mailben (#45) - a cim a CONTACT_EMAIL beallitasbol
// ---------------------------------------------------------------------------
function pageInfo(contact) {
  const mail = contact ? `mailto:${encodeURIComponent(contact)}?subject=${encodeURIComponent("StockWatch Gyakorlás – visszajelzés")}` : "";
  view.innerHTML = `
    <div class="page-head"><h1>Információ</h1></div>
    ${contact ? `<section class="card"><h2>Visszajelzés, hibabejelentés</h2><div class="card-body">
      <p>Hibát találtál, vagy van ötleted? Írj nekünk – minden visszajelzést elolvasunk.</p>
      <p><a class="btn primary" href="${esc(mail)}">Visszajelzés küldése</a> <span class="muted small" translate="no">${esc(contact)}</span></p>
      <p class="muted small">Írd le, mit csináltál, mire számítottál, és mi történt helyette – ha lehet, képernyőképpel.
        Jelszót soha ne küldj el.</p>
    </div></section>` : ""}
    <section class="card"><h2>A szolgáltatásról</h2><div class="card-body muted">
      StockWatch Gyakorlás – oktatási célú chart-olvasási gyakorlás múltbeli adaton: döntés, ítélet és lépésenkénti levezetés.
      Nem befektetési tanács.
    </div></section>`;
}

// ---------------------------------------------------------------------------
// Tanulasi ut (#33): szintek, lecke abraval, celzott feladatsor
// ---------------------------------------------------------------------------
// vizsga (#37): rogzitett helyzetsor, idokorlat, felfedes csak a vegen; a sikeres vizsga tanusitvanya a koddal ellenorizheto
function examCard(x) {
  const status = (e) => (e.passed ? `<span class="badge go">Sikeres</span>` : e.finished ? `<span class="badge fail">Sikertelen</span>` : `<span class="badge watch">Folyamatban</span>`);
  return `<section class="card"><h2>Záróvizsga</h2><div class="card-body">
    <p><span>${x.rounds} helyzet, ${x.minutes} perc; az eredmény csak a beadás után látszik.</span>
      <span>Sikeres, ha legalább ${fmt(x.pass_share * 100, 0)}% jó vagy balszerencsés döntés (a ki nem töltött helyzet nem jó).</span></p>
    <div class="digest-controls">${x.running ? `<a class="btn primary" href="#/practice/${esc(x.running)}">Vizsga folytatása</a>`
      : x.next_start ? `<span class="muted"><span>Újabb vizsga:</span> ${when(x.next_start)}</span>`
      : `<button class="btn primary" id="exam-start" type="button">Vizsga indítása</button>`} <span id="exam-msg" class="small" role="status"></span></div>
    ${x.exams.length ? `<table class="table cards-on-phone"><thead><tr><th>Indítva</th><th>Eredmény</th><th>Állapot</th><th>Tanúsítvány</th></tr></thead><tbody>
      ${x.exams.map((e) => `<tr><td class="small">${when(e.created)}</td><td class="num">${e.score == null ? "–" : `${fmt(e.score * 100, 0)}%`}</td><td>${status(e)}</td>
        <td>${e.code ? `<a href="/c/${esc(e.code)}" target="_blank" rel="noopener">Megnyitás</a> <code>${esc(e.code)}</code>` : ""}</td></tr>`).join("")}</tbody></table>` : ""}
    <p class="muted small">A sikeres vizsga tanúsítványa nyilvános linken, az ellenőrző kóddal visszaellenőrizhető (becenév, dátum, eredmény). A becenevet a Profil oldalon állíthatod be. Oktatási célú gyakorlás múltbeli adaton, nem befektetési képesítés.</p></div></section>`;
}

async function pageLearn() {
  const [levels, exams] = await Promise.all([api("/api/lessons"), api("/api/exams")]);
  view.innerHTML = `
    <div class="page-head"><h1>Tanulás</h1><span class="muted">Tíz szint: rövid lecke, egy ábra, utána célzott feladatsor.
      A szint akkor teljesül, ha a döntéseid nagy része jó vagy balszerencsés (a szabály szerint indokolt) – a következő addig zárolt.</span></div>
    <div class="learn-grid">${levels.map((l) => `
      <a class="card learn-card ${l.unlocked ? "" : "locked"}" ${l.unlocked ? `href="#/learn/${l.level}"` : `aria-disabled="true"`}>
        <div class="card-body"><div class="learn-level">${l.level}. szint</div><h2>${esc(l.title)}</h2>
          ${l.completed ? `<span class="badge go">Teljesítve</span>` : l.unlocked ? `<span class="badge watch">Elérhető</span>` : `<span class="badge fail">Zárolt</span>`}
          ${l.best_share != null ? `<span class="muted small">legjobb: ${fmt(l.best_share * 100, 0)}%</span>` : ""}</div></a>`).join("")}</div>
    ${examCard(exams)}`;
  const start = $("#exam-start");
  if (start) start.addEventListener("click", async () => {
    try { const r = await api("/api/exams/start", { method: "POST", body: "{}" }); location.hash = `#/practice/${r.session.id}`; }
    catch (e) { const m = $("#exam-msg"); m.className = "msg err small"; m.textContent = e.message; }
  });
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
    ${l.walkthrough ? `<section class="card"><h2>Levezetés a példán</h2><div class="card-body">
      <p class="muted small">Hogyan számolj és mit figyelj – a fenti ábra számaival. Vidd az egeret a chart elemeire: kiírja a nevüket.</p>
      <ol class="walk-steps">${l.walkthrough.lines.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ol></div></section>` : ""}
    <section class="card"><div class="card-body"><div class="digest-controls">
      <button class="btn primary" id="lesson-start" type="button">Feladatsor indítása (${l.rounds} helyzet)</button>
      <span class="muted small">A szint teljesül, ha legalább ${fmt(l.pass_share * 100, 0)}% jó vagy balszerencsés döntés.</span></div></div></section>`;
  const svg = $("#lesson-chart");
  const w = l.walkthrough || {};
  const draw = () => practiceChart(svg, l.figure.bars, { levels: w.levels, marks: w.marks });
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
// Napi kihivas es ranglista (#35)
// ---------------------------------------------------------------------------
async function pageDaily() {
  const [d, duelList] = await Promise.all([api("/api/daily"), api("/api/duels")]);
  const s = d.session;
  const state = !s ? "new" : d.hidden ? "running" : "done";
  view.innerHTML = `
    <div class="page-head"><h1>Napi kihívás</h1><span class="muted">${esc(d.day)} – ma mindenki ugyanazt az ${d.rounds} helyzetet kapja.
      Egyszer játszható, az indítástól ${d.minutes} percig; az eredmény a beadás után derül ki, és a ranglistába számít.</span></div>
    <section class="card"><div class="card-body"><div class="digest-controls">
      ${state === "new" ? `<button class="btn primary" id="daily-start" type="button">A mai kihívás indítása</button>`
        : state === "running" ? `<a class="btn primary" href="#/practice/${esc(s.id)}">Folytatás (${s.decided} / ${s.rounds})</a>
            <span class="badge watch">Beadás: ${esc(new Date(s.deadline).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }))}-ig</span>`
        : `<a class="btn primary" href="#/practice/${esc(s.id)}">A mai eredményem</a> <span class="badge go">Kész</span>`}
      <a class="btn" href="#/leaderboard">Ranglista</a></div></div></section>
    ${duelSection(duelList)}`;
  bindRowLinks();
  wireDuelForm();
  const btn = $("#daily-start");
  if (btn) btn.addEventListener("click", async () => {
    const r = await api("/api/daily/start", { method: "POST", body: "{}" });
    location.hash = `#/practice/${r.session.id}`;
  });
}

// ---------------------------------------------------------------------------
// Parbaj (#55): ugyanaz a helyzetsor ket jatekosnak; a masik eredmenye a sajat kor befejezese utan
// ---------------------------------------------------------------------------
const DUEL_RESULT = { win: ["Nyertél", "go"], loss: ["Vesztettél", "fail"], draw: ["Döntetlen", "watch"] };

function duelSection(list) {
  return `<section class="card"><h2>Párbaj</h2><div class="card-body">
    <p class="muted small">Hívj ki valakit: ugyanazokat a helyzeteket kapjátok ugyanabban a sorrendben, 72 órátok van rá. A másik eredményét a saját köröd befejezése után látod; aki a határidőig nem fejezi be, veszít.</p>
    <form id="duel-form" class="ag-form">
      <label>Helyzetek<select name="rounds"><option value="5">5</option><option value="10">10</option></select></label>
      <label>Feladattípus<select name="kind">${Object.entries(PR_KIND).map(([key, label]) => `<option value="${key}">${esc(label)}</option>`).join("")}<option value="mixed">Vegyes</option></select></label>
      <div class="wide"><button class="btn primary" type="submit">Párbaj indítása</button> <span id="duel-msg" class="small" role="status"></span></div></form>
    ${list.length ? `<table class="table cards-on-phone"><thead><tr><th>Indult</th><th>Ellenfél</th><th>Helyzetek</th><th>Eredmény</th></tr></thead><tbody>
      ${list.map((x) => `<tr class="clickable" data-href="#/duel/${esc(x.id)}"><td class="small">${when(x.created)}</td>
        <td>${x.opponent ? `<span translate="no">${esc(x.opponent)}</span>` : `<span class="muted">még nincs</span>`}</td><td class="num">${x.rounds}</td>
        <td>${x.revoked ? `<span class="muted">visszavonva</span>` : x.result ? `<span class="badge ${DUEL_RESULT[x.result][1]}">${DUEL_RESULT[x.result][0]}</span>` : `<span class="muted">folyamatban</span>`}</td></tr>`).join("")}
    </tbody></table>` : ""}</div></section>`;
}

function wireDuelForm() {
  const form = $("#duel-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const kind = form.elements.kind.value;
    try {
      const r = await api("/api/duels/create", { method: "POST", body: JSON.stringify({ rounds: Number(form.elements.rounds.value),
        kinds: kind === "mixed" ? PR_MIXED : { [kind]: 1 } }) });
      location.hash = `#/duel/${r.id}`;
    } catch (e) { const m = $("#duel-msg"); m.className = "msg err small"; m.textContent = e.message; }
  });
}

const duelBrief = (b) => (!b ? "–" : (b.kind && b.kind !== "decision"
  ? `${esc(PR_KIND[b.kind] || b.kind)}: ${b.quality != null ? `${fmt(b.quality * 100, 0)}%` : "–"}`
  : `${esc(PR_SIDE[b.action] || b.action || "–")}${b.verdict && PR_VERDICT[b.verdict] ? ` <span class="pill plain ${PR_VERDICT[b.verdict][1]}"><span aria-hidden="true">${PR_VERDICT[b.verdict][2]}</span> <span>${esc(PR_VERDICT[b.verdict][0])}</span></span>` : ""}`)
  + (b.points != null ? ` <span class="muted small">+${b.points}</span>` : ""));

async function pageDuel(id) {
  const d = await api(`/api/duels/${id}`);
  const accept = `<button class="btn primary" id="duel-accept" type="button">Elfogadom, játszom</button>`;
  if (d.invite) {
    view.innerHTML = `<div class="page-head"><a href="#/daily" class="muted">← Napi kihívás</a><h1>Párbaj-meghívó</h1></div>
      <section class="card"><div class="card-body"><p><strong translate="no">${esc(d.challenger)}</strong> <span>kihívott egy párbajra.</span></p>
        <p>${d.rounds} helyzet, ugyanazok ugyanabban a sorrendben. Határidő: ${when(d.expires)}.</p>
        <div class="digest-controls">${accept}</div></div></section>`;
  } else {
    const m = d.me;
    const link = `${location.origin}/d/${d.id}`;
    const mine = !m ? accept : m.decided < m.total && !d.over ? `<a class="btn primary" href="#/practice/${esc(m.session)}">Folytatás (${m.decided} / ${m.total})</a>`
      : `<a class="btn" href="#/practice/${esc(m.session)}">A köröm</a> <span class="muted">${m.finished ? `kész: ${m.points} pont` : "nem fejeződött be időben"}</span>`;
    view.innerHTML = `<div class="page-head"><a href="#/daily" class="muted">← Napi kihívás</a><h1>Párbaj</h1>
        <span class="muted">${d.opponent ? `<span>Ellenfél:</span> <span translate="no">${esc(d.opponent)}</span>` : "Még nincs ellenfél."} · ${d.rounds} helyzet · határidő: ${when(d.expires)}</span></div>
      ${d.revoked ? `<section class="card"><div class="card-body"><p>A meghívót visszavontad.</p></div></section>` : ""}
      ${d.side === "challenger" && !d.other.accepted && !d.revoked && !d.over ? `<section class="card"><h2>Meghívó link</h2><div class="card-body">
        <p class="muted small">Küldd el annak, akit kihívsz. Az első elfogadó lesz az ellenfeled; addig visszavonhatod.</p>
        <p><code translate="no">${esc(link)}</code></p>
        <div class="digest-controls"><button class="btn primary" id="duel-copy" type="button">Link másolása</button>
          <button class="btn" id="duel-revoke" type="button">Visszavonás</button> <span id="duel-msg" class="small" role="status"></span></div></div></section>` : ""}
      <section class="card"><div class="card-body"><div class="digest-controls">${mine}</div>
        <p>${!d.other.accepted ? "Az ellenfél még nem fogadta el." : d.other.finished ? "Az ellenfél befejezte." : "Az ellenfél még játszik."}
          ${d.other.points != null ? `<span>Pontja:</span> ${d.other.points}` : ""}</p>
        ${d.result ? `<div class="digest-controls"><span class="badge ${DUEL_RESULT[d.result][1]}">${DUEL_RESULT[d.result][0]}</span>
          <button class="btn primary" id="duel-rematch" type="button">Visszavágó</button></div>` : ""}</div></section>
      ${d.compare ? `<section class="card"><h2>Helyzetenként</h2><div class="card-body"><div class="table-scroll"><table class="table"><thead><tr><th>#</th><th>Papír</th><th>Dátum</th><th>Te</th><th>Ellenfél</th></tr></thead><tbody>
        ${d.compare.map((c) => `<tr><td>${c.k + 1}</td><td class="ticker">${esc((c.me || c.other || {}).symbol || "")}</td><td>${esc((c.me || c.other || {}).date || "")}</td>
          <td>${duelBrief(c.me)}</td><td>${duelBrief(c.other)}</td></tr>`).join("")}</tbody></table></div></div></section>` : ""}`;
  }
  const on = (sel, fn) => { const el = $(sel); if (el) el.addEventListener("click", fn); };
  const fail = (e) => { const msg = $("#duel-msg"); if (msg) { msg.className = "msg err small"; msg.textContent = e.message; } else alert(e.message); };
  on("#duel-accept", async () => {
    try { const r = await api(`/api/duels/${id}/accept`, { method: "POST", body: "{}" }); location.hash = `#/practice/${r.session.id}`; } catch (e) { fail(e); }
  });
  on("#duel-copy", async () => {
    try { await navigator.clipboard.writeText(`${location.origin}/d/${id}`); const msg = $("#duel-msg"); msg.className = "msg ok small"; msg.textContent = "A link a vágólapon."; }
    catch (e) { fail(e); }
  });
  on("#duel-revoke", async () => {
    if (!confirm("Visszavonod a meghívót?")) return;
    try { await api(`/api/duels/${id}/revoke`, { method: "POST", body: "{}" }); await pageDuel(id); } catch (e) { fail(e); }
  });
  on("#duel-rematch", async () => {
    try { const r = await api(`/api/duels/${id}/rematch`, { method: "POST", body: "{}" }); location.hash = `#/duel/${r.id}`; } catch (e) { fail(e); }
  });
}

let lbPeriod = "week";
let lbGroup = "";                                    // "" = mindenki, kulonben a privat csoport azonositoja (#47)
const LB_PERIODS = { day: "Napi", week: "Heti", month: "Havi", all: "Összesített" };

// heti liga (#56): kb. 30 fos csoport, hetfotol vasarnapig a dontesek pontja; a het vegen feljutas / kieses
const LEAGUE_TIERS = ["Bronz", "Ezüst", "Arany", "Platina", "Gyémánt"];
const LEAGUE_OUTCOME = { up: "Feljutottál", stay: "A ligádban maradtál", down: "Kiestél" };
const LEAGUE_ZONE = { up: ["feljutó hely", "go"], down: ["kieső hely", "fail"] };

function leagueLast(l) {
  if (!l.last) return "";
  return `<p class="${l.last.new ? "" : "muted small"}"><span>Múlt heti liga:</span> <strong>${LEAGUE_OUTCOME[l.last.outcome]}</strong>
    · <span>${l.last.rank}. hely, ${l.last.xp} pont</span> · <span>Most:</span> <span>${LEAGUE_TIERS[l.tier]}</span></p>`;
}

function leagueNotice(l) {
  if (!l.last || !l.last.new) return "";
  return `<section class="card"><div class="card-body">${leagueLast(l)}
    <div class="digest-controls"><a class="btn" href="#/leaderboard">Heti liga</a> <button class="btn" type="button" id="league-seen">Rendben</button></div></div></section>`;
}

function leagueCard(l) {
  const name = (r) => (r.name ? `<span translate="no">${esc(r.name)}</span>` : `Játékos #${esc(r.tag)}`);
  const body = l.opted_out ? `<p class="muted">Kiléptél a heti ligából – a Profil oldalon visszaléphetsz.</p>`
    : !l.group ? `<p>Ezen a héten még nem döntöttél – az első döntéseddel bekerülsz egy kb. 30 fős csoportba.</p>`
    : `<p class="muted small"><span>A hét zárása:</span> ${when(l.ends)} · <span>Feljut: ${l.quotas.up} · kiesik: ${l.quotas.down}</span></p>
      <table class="table cards-on-phone"><thead><tr><th>Hely</th><th>Becenév</th><th>Heti pont</th><th></th></tr></thead><tbody>
      ${l.group.map((r) => `<tr class="${r.me ? "lb-me" : ""}"><td class="num">${r.rank}.</td><td>${name(r)}${r.me ? " (te)" : ""}</td>
        <td class="num">${r.xp}</td><td>${LEAGUE_ZONE[r.zone] ? `<span class="badge ${LEAGUE_ZONE[r.zone][1]}">${LEAGUE_ZONE[r.zone][0]}</span>` : ""}</td></tr>`).join("")}
      </tbody></table>`;
  return `<section class="card"><h2><span>Heti liga:</span> <span>${LEAGUE_TIERS[l.tier]}</span></h2><div class="card-body">
    ${leagueLast(l)}${body}
    <p class="muted small">A heti pont a döntések minősége szerint jár (a napi kihívás és a leckék is számítanak, ugyanannak a helyzetnek az újrajátszása nem). A hét vasárnap éjfélkor zárul.</p></div></section>`;
}

async function pageLeaderboard() {
  const [groups, league] = await Promise.all([api("/api/groups"), api("/api/league")]);
  if (lbGroup && !groups.some((g) => String(g.id) === lbGroup)) lbGroup = "";
  const d = await api(`/api/leaderboard?period=${lbPeriod}${lbGroup ? `&group=${lbGroup}` : ""}`);
  const row = (r) => `<tr class="${r.me ? "lb-me" : ""}"><td class="num">${r.rank}.</td>
    <td>${r.name ? `<span translate="no">${esc(r.name)}</span>` : `Játékos #${esc(r.tag)}`}${r.me ? " (te)" : ""}</td>
    <td class="num">${r.points}</td><td class="num">${r.days}</td></tr>`;
  view.innerHTML = `
    <div class="page-head"><h1>Ranglista</h1><span class="muted">A napi kihívások pontjai szerint (a döntés minősége, nem a profit).
      Becenév látszik, e-mail soha – a becenevet a Profil oldalon állíthatod be.</span></div>
    ${leagueCard(league)}
    <section class="card"><div class="card-body">
      ${groups.length ? `<label class="cal-field">Kik között<select id="lb-group"><option value="">Mindenki</option>
        ${groups.map((g) => `<option value="${g.id}" translate="no" ${String(g.id) === lbGroup ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select></label>` : ""}
      <div class="segmented" role="group" aria-label="Időszak">${Object.entries(LB_PERIODS).map(([k, v]) =>
        `<button type="button" class="seg ${k === lbPeriod ? "on" : ""}" data-lb="${k}" aria-pressed="${k === lbPeriod}">${v}</button>`).join("")}</div>
      ${d.rows.length ? `<table class="table cards-on-phone"><thead><tr><th>Hely</th><th>Becenév</th><th>Pont</th><th>Napok</th></tr></thead>
        <tbody>${d.rows.map(row).join("")}</tbody></table>` : `<p class="muted">Ebben az időszakban még nincs eredmény.</p>`}
      ${d.me && !d.rows.some((r) => r.me) ? `<p>A te helyed: <strong>${d.me.rank}.</strong> (${d.me.points} pont)</p>` : ""}
    </div></section>
    <section class="card"><h2>Csoportjaim</h2><div class="card-body">
      <p class="muted small">Privát ranglista egy kurzusnak vagy baráti körnek: hozz létre egy csoportot, és add tovább a meghívókódot.</p>
      ${groups.length ? `<table class="table cards-on-phone"><thead><tr><th>Csoport</th><th>Meghívókód</th><th>Tagok</th><th></th></tr></thead><tbody>
        ${groups.map((g) => `<tr><td><span translate="no">${esc(g.name)}</span>${g.owner ? ` <span class="badge go">saját</span>` : ""}</td><td><code>${esc(g.code)}</code></td>
          <td class="num">${g.members}</td><td><button class="btn" type="button" data-group-${g.owner ? "delete" : "leave"}="${g.id}">${g.owner ? "Törlés" : "Kilépés a csoportból"}</button></td></tr>`).join("")}
      </tbody></table>` : ""}
      <form id="group-create" class="ag-form"><label>Új csoport neve<input name="name" maxlength="40" required></label>
        <div class="wide"><button class="btn" type="submit">Csoport létrehozása</button></div></form>
      <form id="group-join" class="ag-form"><label>Meghívókód<input name="code" maxlength="8" autocomplete="off" required></label>
        <div class="wide"><button class="btn" type="submit">Csatlakozás</button> <span id="group-msg" class="small"></span></div></form>
    </div></section>`;
  if (league.last && league.last.new) api("/api/league/seen", { method: "POST", body: "{}" }).catch(() => {});
  view.querySelectorAll("[data-lb]").forEach((b) => b.addEventListener("click", () => { lbPeriod = b.dataset.lb; pageLeaderboard(); }));
  const sel = $("#lb-group");
  if (sel) sel.addEventListener("change", () => { lbGroup = sel.value; pageLeaderboard(); });
  const groupAction = async (action, body) => {
    try { await api(`/api/groups/${action}`, { method: "POST", body: JSON.stringify(body) }); await pageLeaderboard(); }
    catch (e) { const m = $("#group-msg"); m.className = "msg err small"; m.textContent = e.message; }
  };
  $("#group-create").addEventListener("submit", (ev) => { ev.preventDefault(); groupAction("create", { name: ev.target.elements.name.value }); });
  $("#group-join").addEventListener("submit", (ev) => { ev.preventDefault(); groupAction("join", { code: ev.target.elements.code.value }); });
  view.querySelectorAll("[data-group-leave]").forEach((b) => b.addEventListener("click", () => {
    if (confirm("Kilépsz a csoportból?")) groupAction("leave", { id: Number(b.dataset.groupLeave) });
  }));
  view.querySelectorAll("[data-group-delete]").forEach((b) => b.addEventListener("click", () => {
    if (confirm("Törlöd a csoportot? Minden tag kikerül belőle.")) groupAction("delete", { id: Number(b.dataset.groupDelete) });
  }));
}

// ---------------------------------------------------------------------------
// Profil: a fiok, kilepes (mindenhonnan is)
// ---------------------------------------------------------------------------
async function pageProfile() {
  const [s, g, sh, rv, lg] = await Promise.all([api("/api/session"), api("/api/progress"), api("/api/shares"), api("/api/review"), api("/api/league")]);
  view.innerHTML = `
    <div class="page-head"><h1>Profil</h1><span class="muted" translate="no">${esc(s.email)}</span>
      ${s.admin ? `<a class="btn" href="/admin">Admin felület</a>` : ""}</div>
    <div class="kpis">
      <div class="kpi"><div class="label">Szint</div><div class="value">${g.level}</div><div class="sub">${g.level_xp} / ${g.next} XP a következőig</div></div>
      <div class="kpi"><div class="label">XP</div><div class="value">${g.xp}</div><div class="sub">a döntések minősége szerint – nincs pénzbeli értéke</div></div>
      <div class="kpi"><div class="label">Napi sorozat</div><div class="value">${g.streak} nap</div><div class="sub">legjobb: ${g.best_streak} · fagyasztó: ${g.freeze ? "van" : "felhasználva"}</div></div>
    </div>
    ${s.google_available || s.google ? `<section class="card"><h2>Google-fiók</h2><div class="card-body">
      ${s.google ? `<p><span class="badge go">Kapcsolva</span> A Google-fiókoddal is beléphetsz.</p>
        ${s.has_password ? `<p><button class="btn" id="google-unlink" type="button">Kapcsolat bontása</button></p>`
          : `<p class="muted small">Bontás előtt állíts be jelszót (Elfelejtett jelszó), különben nem tudnál belépni.</p>`}`
        : `<p>Kapcsold össze a fiókodat a Google-fiókoddal, és utána azzal is beléphetsz.</p>
          <p><a class="btn" href="/auth/google/start?link=1">Google-fiók összekapcsolása</a></p>`}
    </div></section>` : ""}
    <section class="card"><h2>Becenév a ranglistán</h2><div class="card-body">
      <form id="nick-form" class="ag-form"><label>Becenév<input name="nickname" maxlength="20" value="${esc(s.nickname || "")}" autocomplete="nickname"></label>
        <div class="wide"><button class="btn" type="submit">Mentés</button> <span id="nick-msg" class="small"></span></div></form></div></section>
    <section class="card"><h2>Megosztásaid</h2><div class="card-body">
      ${sh.length ? `<ul class="digest-list">${sh.map((x) => `<li><span><a href="/s/${esc(x.id)}" target="_blank" rel="noopener">${x.kind === "daily"
        ? `<span>Napi kihívás</span> · ${esc(x.ref)}` : `${esc(x.ref)}. szint`}</a></span>
        <button class="btn share-revoke" type="button" data-id="${esc(x.id)}">Visszavonás</button></li>`).join("")}</ul>`
        : `<p class="muted">Még nem osztottál meg eredményt.</p>`}
      <p class="muted small">Visszavonás után a link és a kártya nem érhető el.</p></div></section>
    <section class="card"><h2>Magabiztosság és kalibráció</h2><div class="card-body">
      <label class="check"><input type="checkbox" id="conf-toggle" ${prefConfidence() ? "checked" : ""}> <span>Magabiztosság megadása minden döntésnél (a leckékben a 6. szinttől mindig)</span></label>
      ${g.calibration.n ? calibrationView(g.calibration) : `<p class="muted">Még nincs magabiztossággal megadott döntés.</p>`}</div></section>
    ${weaknessCard(rv)}
    <section class="card"><h2>Heti liga</h2><div class="card-body">
      <label class="check"><input type="checkbox" id="league-toggle" ${lg.opted_out ? "" : "checked"}> <span>Részt veszek a heti ligában</span></label>
      <p class="muted small">Kilépéskor azonnal kikerülsz a heti csoportodból; visszalépés után a következő döntéseddel kerülsz be újra.</p></div></section>
    <section class="card"><h2>Jelvények</h2><div class="card-body"><div class="badges">${g.badges.map((b) =>
      `<span class="badge ${b.earned_at ? "go" : "fail"}">${b.earned_at ? "✓" : "○"} ${esc(b.label)}</span>`).join(" ")}</div></div></section>
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
      <form id="delete-form" class="ag-form"><label>${s.has_password ? "Jelszó" : "Az e-mail-címed (megerősítésként)"}
        <input name="password" type="${s.has_password ? "password" : "email"}" autocomplete="${s.has_password ? "current-password" : "off"}" required></label>
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
  $("#league-toggle").addEventListener("change", (ev) => api("/api/league/opt", { method: "POST", body: JSON.stringify({ in: ev.target.checked }) }));
  $("#conf-toggle").addEventListener("change", (ev) => { try { localStorage.setItem("sw-confidence", ev.target.checked ? "1" : "0"); } catch (e) { /* privat mod */ } });
  document.querySelectorAll(".share-revoke").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/share/revoke", { method: "POST", body: JSON.stringify({ id: b.dataset.id }) });
    await pageProfile();
  }));
  const unlink = $("#google-unlink");
  if (unlink) unlink.addEventListener("click", async () => {
    if (!confirm("Bontod a Google-kapcsolatot? Utána jelszóval léphetsz be.")) return;
    await api("/api/account/google/unlink", { method: "POST", body: "{}" });
    await pageProfile();
  });
  $("#nick-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const m = $("#nick-msg");
    try {
      await api("/api/profile/nickname", { method: "POST", body: JSON.stringify({ nickname: ev.target.elements.nickname.value }) });
      m.className = "small up"; m.textContent = "Mentve.";
    } catch (e) { m.className = "msg err small"; m.textContent = e.message; }
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
  ref_stop: ["Referencia-stop", "var(--info)", true], struct: ["Szerkezeti szint", "var(--muted)", true], trigger: ["Trigger", "var(--warn)"],
  p_target: ["Mért cél", "var(--up)", true], support: ["Support", "var(--up)", true], resistance: ["Rezisztencia", "var(--down)", true],
  mine: ["A te vonalad", "var(--info)"], entry_level: ["Belépő", "var(--info)"],
  sma200: ["SMA200", "var(--ema200)", true], high20: ["20 napos csúcs", "var(--muted)", true], low20: ["20 napos mélypont", "var(--muted)", true] };
const PR_STATUS = { unfilled: "a limit nem teljesült", open: "nyitva maradt", filtered: "a szűrők kizárták", unknown: "a labor még nem értékelte" };
const rCls = (r) => (r == null ? "" : r > 0 ? "up" : r < 0 ? "down" : "");
const fmtIn = (v) => (Math.round(v * 100) / 100).toFixed(2);       // <input type="number"> erteke: pont a tizedesjel

// hibak ismetlese (#36): az esedekes helyzettipusokbol hasonlo, de uj helyzetek
function reviewCard(r) {
  const due = r.queue.filter((q) => q.is_due);
  return `<section class="card"><h2>Ismétlés</h2><div class="card-body">
    <p class="muted small">A rossz vagy szerencsés döntések (és a túl szoros stopok) helyzettípusa másnap visszajön, hasonló, de másik helyzettel; jó döntés után egyre ritkábban.</p>
    ${due.length ? `<div class="digest-controls"><button class="btn primary" id="review-start" type="button">Ismétlő kör (${due.length} típus esedékes)</button> <span id="review-msg" class="small" role="status"></span></div>`
      : `<p>Nincs esedékes ismétlés.${r.queue.length ? ` <span>A következő:</span> ${esc(r.queue[0].due)}` : ""}</p>`}</div></section>`;
}

function weaknessCard(r) {
  if (!r.weaknesses.length) return "";
  return `<section class="card"><h2>Gyengeségeim</h2><div class="card-body"><table class="table cards-on-phone"><thead><tr><th>Helyzettípus</th><th>Döntés</th><th>Rossz vagy szerencsés</th><th>Szoros stop</th></tr></thead><tbody>
    ${r.weaknesses.map((w) => `<tr><td>${esc(PR_CAT[w.category] || w.category)}</td><td class="num">${w.n}</td><td class="num">${w.misses} (${fmt(w.miss_rate * 100, 0)}%)</td><td class="num">${w.tight}</td></tr>`).join("")}
    </tbody></table>
    ${r.queue.length ? `<p class="muted small"><span>Ismétlésre vár:</span> ${r.queue.map((q) => `<span>${esc(PR_CAT[q.category] || q.category)}</span> (${esc(q.due)})`).join(" · ")}</p>` : ""}</div></section>`;
}

async function pagePractice(msg = "") {
  clearInterval(practiceTimer);
  const [d, rv, lg] = await Promise.all([api("/api/practice"), api("/api/review"), api("/api/league")]);
  const p = d.pool;
  const total = p ? Object.values(p.counts).reduce((a, b) => a + b, 0) : 0;
  view.innerHTML = `
    <div class="page-head"><h1>Gyakorlás</h1>
      <span class="muted">Rejtett, múltbeli chart-helyzetek: eldöntöd, kihagyod-e, vagy long / short belépsz-e (stoppal, opcionális céllal és időkorláttal), utána megnézed, mi történt, ítéletet és lépésenkénti levezetést kapsz, és összeveted a stratégiákkal és egy véletlen alapvonallal.
        A papír és a dátum csak a döntés után derül ki. Szimuláció múltbeli adaton, nem ajánlás.</span></div>
    ${msg ? `<section class="card"><div class="card-body">${esc(msg)}</div></section>` : ""}
    ${leagueNotice(lg)}
    ${p ? reviewCard(rv) : ""}
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
        <label>Feladattípus<select name="kind">${Object.entries(PR_KIND).map(([key, label]) => `<option value="${key}">${esc(label)}</option>`).join("")}<option value="mixed">Vegyes</option></select></label>
        <div class="wide"><button class="btn primary" type="submit">Kör indítása</button> <span id="pr-msg" class="small"></span>
          <span class="muted small">Megosztási kóddal a helyzetek száma és sorrendje a kódból jön.</span></div>
      </form></div></section>` : ""}
    <section class="card"><h2>Körök</h2><div class="card-body">${d.sessions.length ? `<table class="table"><thead><tr><th>Készült</th><th>Állás</th><th>Átlag R</th><th>Megosztási kód</th><th></th></tr></thead><tbody>
      ${d.sessions.map((s) => `<tr class="clickable" data-href="#/practice/${esc(s.id)}"><td class="small">${when(s.created)}</td>
        <td>${s.decided} / ${s.rounds}${s.done ? ` <span class="badge go">kész</span>` : ""}</td><td class="num ${rCls(s.mean_r)}">${signed(s.mean_r, 2, " R")}</td>
        <td class="small">${s.duel ? "Párbaj" : s.exam ? "Vizsga" : `<code>${esc(s.code)}</code>`}</td><td>${s.duel || s.exam ? "" : `<button class="btn" type="button" data-pr-delete="${esc(s.id)}">Törlés</button>`}</td></tr>`).join("")}</tbody></table>`
      : `<div class="muted">Még nincs kör.</div>`}</div></section>`;
  bindRowLinks();
  const seen = $("#league-seen");
  if (seen) seen.addEventListener("click", async () => { await api("/api/league/seen", { method: "POST", body: "{}" }); seen.closest("section").remove(); });
  const rs = $("#review-start");
  if (rs) rs.addEventListener("click", async () => {
    try { const r = await api("/api/review/start", { method: "POST", body: "{}" }); location.hash = `#/practice/${r.session.id}`; }
    catch (e) { const m = $("#review-msg"); m.className = "msg err small"; m.textContent = e.message; }
  });
  const form = $("#pr-form");
  if (form) form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const v = (n) => form.elements[n].value.trim();
    const body = { rounds: Number(v("rounds")), seed: v("seed") === "" ? null : Number(v("seed")), code: v("code") || null,
      mix: { strategy: Number(v("m_strategy")), pattern: Number(v("m_pattern")), short: Number(v("m_short")), nothing: Number(v("m_nothing")) },
      kinds: v("kind") === "mixed" ? PR_MIXED : { [v("kind")]: 1 } };
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


async function pagePracticeSession(id) {
  clearInterval(practiceTimer);
  const s = await api(`/api/practice/session/${id}/summary`);
  const expired = s.session.deadline && new Date(s.session.deadline) < new Date();
  if (!s.hidden && (s.session.done || expired)) { renderPracticeSummary(s); return; }
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
  if (t.kind && t.kind !== "decision") { await pageKindTask(id, k, sess, t); return; }
  const atr = t.chart.atr || 1;
  view.innerHTML = `
    <div class="page-head"><a href="${sess.daily ? "#/daily" : sess.exam ? "#/learn" : "#/practice"}" class="muted">← ${sess.daily ? "Napi kihívás" : sess.exam ? "Tanulás" : "Gyakorlás"}</a><h1>Helyzet ${k + 1} / ${t.rounds}</h1>
      ${sess.deadline ? `<span class="badge watch">Beadás: ${esc(new Date(sess.deadline).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }))}-ig</span>` : ""}
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
        ${t.confidence || prefConfidence() ? `<label class="wide">Magabiztosság: <output id="pr-conf-out">70%</output> – ennyire vagy biztos benne, hogy ez a jó döntés
          <input name="confidence" type="range" min="50" max="95" step="5" value="70"></label>` : ""}
        <div class="wide"><span id="pr-risk" class="small muted"></span></div>
        <div class="wide"><button class="btn primary" type="submit">Döntés rögzítése</button> <span class="muted small">A döntés után nem módosítható.</span> <span id="pr-err" class="small"></span></div>
      </form></div></section>
    ${practiceCriteriaBefore(t.hints)}
    <div id="pr-reveal"></div>`;
  const svg = $("#pr-chart");
  const form = $("#pr-dform");
  const read = () => {
    const n = (name) => { const raw = form.elements[name].value.trim(); return raw === "" ? null : Number(raw); };
    return { action: form.elements.action.value, entry: form.elements.entry.value, limit: n("limit"), stop: n("stop"), target: n("target"), days: n("days"),
      confidence: form.elements.confidence ? n("confidence") : null };
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
    if (form.elements.confidence) $("#pr-conf-out").textContent = `${f.confidence}%`;
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
    const body = f.action === "skip" ? { action: "skip", confidence: f.confidence } : { ...f, limit: f.entry === "limit" ? f.limit : null };
    try {
      const r = await api(`/api/practice/session/${id}/task/${k}/decision`, { method: "POST", body: JSON.stringify(body) });
      if (r.hidden) {                          // napi kihivas: az eredmeny csak a vegen
        form.querySelectorAll("input, select, button").forEach((el) => { el.disabled = true; });
        $("#pr-reveal").innerHTML = `<section class="card"><div class="card-body"><p><strong>Rögzítve.</strong> A napi kihívás eredménye a beadás után derül ki.</p>
          <button class="btn primary" id="pr-next" type="button">Következő helyzet</button></div></section>`;
        $("#pr-next").addEventListener("click", () => pagePracticeSession(id));
        return;
      }
      showPracticeReveal(id, k, t, { ...r.reveal, progress: r.progress }, (o) => { chartOpt = o; draw(); }, false, r.session);
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

// itelet-szempontok (#59): a dontes elott altalanos lista (nem az adott helyzetrol), a felfedeskor ugyanez pipakkal
const PR_CRITERIA = {
  trend: "Trend: a záró és az EMA50 / EMA200 helyzete melyik irányt támogatja? A döntés iránya egyezzen vele.",
  signal: "Szabály: jelez-e valamelyik stratégia (Visszahúzás, Kitörés, short-gyanús helyzet)? Ha egyik sem, a kihagyás is jó döntés lehet.",
  stop: "Stop: legalább 1 ATR-re a belépőtől, a szerkezeti szint (swing pont) mögött.",
  rr: "Cél / kockázat: ha adsz meg célt, legalább 1 legyen.",
};

function practiceCriteriaBefore(mode) {
  if (mode !== "show" && mode !== "toggle") return "";
  const list = `<ul class="digest-list">${Object.values(PR_CRITERIA).map((c) => `<li><span>${esc(c)}</span></li>`).join("")}</ul>`;
  return mode === "show"
    ? `<section class="card pr-criteria"><h2>Mitől jó egy döntés?</h2><div class="card-body">${list}</div></section>`
    : `<section class="card pr-criteria"><div class="card-body"><details><summary>Szempontok mutatása: mitől jó egy döntés?</summary>${list}</details></div></section>`;
}

function practiceCriteriaAfter(rv) {
  if (!rv || !rv.criteria) return "";
  const mark = (ok) => (ok == null ? `<span class="muted" aria-label="nem értelmezhető">–</span>`
    : ok ? `<span class="up" aria-label="teljesült">✓</span>` : `<span class="down" aria-label="nem teljesült">✗</span>`);
  return `<section class="card"><h2>A szempontok szerint</h2><div class="card-body"><ul class="digest-list">
    ${rv.criteria.map((c) => `<li><span>${mark(c.ok)} ${esc(PR_CRITERIA[c.key] || c.key)}</span></li>`).join("")}</ul></div></section>`;
}

// magabiztossag es kalibracio (#54): a 6. szinttol a leckekben mindig, kulonben a Profil oldalon kapcsolhato
// (eszkozonkent, localStorage); az iteletet nem befolyasolja, kulon kalibracios pontot ad
const prefConfidence = () => { try { return localStorage.getItem("sw-confidence") === "1"; } catch (e) { return false; } };

function calibrationView(c) {
  const pctv = (v) => `${fmt(v * 100, 0)}%`;
  const say = { over: "Túl magabiztos", under: "Túl óvatos", ok: "Jól kalibrált" };
  return `${c.noisy ? `<p class="small"><strong>Még csak ${c.n} döntés (legalább ${c.min} kell): a görbe még főleg zaj.</strong></p>` : ""}
    ${c.summary ? `<p><strong>${say[c.summary]}:</strong> <span>átlagosan ${pctv(c.stated)}-ra becsülted, a döntéseid ${pctv(c.actual)}-a volt jó vagy balszerencsés.</span></p>` : ""}
    <div class="parts">${c.buckets.map((b) => `<div class="part"><span>${b.lo}–${b.hi}%</span>
      <span class="track"><span class="fill" style="width:${b.actual * 100}%"></span></span>
      <span class="muted small">${pctv(b.actual)} / ${pctv(b.stated)} · ${b.n}</span></div>`).join("")}</div>
    <p class="muted small">Sávonként: tényleges találati arány / a megadott átlag · a döntések száma. Jól kalibráltnál a kettő közel azonos.
      Brier: ${fmt(c.brier, 3)} · kalibrációs pont összesen: ${c.points > 0 ? "+" : ""}${c.points}</p>`;
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

// a pont atlathato: melyik reszbol mennyi (#34); a pontnak nincs penzbeli erteke
function practicePoints(rev) {
  const p = rev.points;
  if (!p) return "";
  const g = rev.progress;
  return `<div class="pr-points"><strong>+${p.points} pont</strong>
    <span class="small muted">${p.parts.map((x) => `${esc(x.label)} ${x.value >= 0 ? "+" : "−"}${Math.abs(x.value)}`).join(" · ")}</span>
    ${g ? `<span class="small">XP ${g.xp} · ${g.level}. szint · sorozat: ${g.streak} nap</span>` : ""}
    ${rev.calibration ? `<span class="small">Magabiztosság: ${rev.calibration.confidence}% · kalibrációs pont: ${rev.calibration.points > 0 ? "+" : ""}${rev.calibration.points}</span>` : ""}
    ${g && g.new_badges.length ? g.new_badges.map((b) => `<span class="badge go">Új jelvény: ${esc(b.label)}</span>`).join(" ") : ""}</div>`;
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
  return `<section class="card"><h2>Ítélet</h2><div class="card-body">${practiceVerdict(rev.review)}${practicePoints(rev)}</div></section>
    ${practiceCriteriaAfter(rev.review)}
    <section class="card"><h2>Eredmény</h2><div class="card-body">
    <div class="kpis check-kpis">
      <div class="kpi"><div class="label">A te döntésed</div><div class="value ${mine[1]}">${mine[0]}</div><div class="sub">${mine[2]}</div></div>
      <div class="kpi"><div class="label">Referencia-kötés</div><div class="value ${rCls(ref.r)}">${signed(ref.r, 2, " R")}</div>
        <div class="sub">következő nyitás, ${fmt(ref.stop_atr, 0)} ATR stop, ${ref.days} nap</div></div>
      <div class="kpi"><div class="label">Véletlen alapvonal</div><div class="value ${rCls(base.mean_r)}">${signed(base.mean_r, 2, " R")}</div>
        <div class="sub">${base.n} véletlen nap ugyanezen a papíron, azonos stop-távolsággal</div></div>
    </div>
    <div class="table-scroll"><table class="table"><thead><tr><th>Stratégia ugyanezen a ponton</th><th>Jelzés</th><th>Eredmény</th><th>Kilépés</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="pr-name"><strong>${esc(rev.symbol)}</strong>${rev.date ? ` · ${esc(rev.date)}` : ""} · ${esc(PR_CAT[rev.category] || rev.category)}${rev.source && rev.category !== "strategy" ? ` (${esc(rev.source === "downtrend" ? "eső trend" : rev.source)})` : ""}</p>
  </div></section>
  ${practiceSteps(rev.review)}
  <section class="card"><div class="card-body"><div class="digest-controls"><button class="btn primary" id="pr-next" type="button">${last ? "Összegzés" : "Következő helyzet"}</button>
      <span class="muted small">Nem befektetési tanács. Szimulált kötés múltbeli adaton.</span></div></div></section>`;
}

// ---------------------------------------------------------------------------
// Feladattipusok (#49-#53): stop, alakzat, szintek / trendvonal, pozicioméret
// ---------------------------------------------------------------------------
const PR_KIND = { decision: "Döntés", stop: "Tedd le a stopot", pattern: "Melyik alakzat ez?", levels: "Szintek és trendvonal", sizing: "Pozícióméret" };
const PR_MIXED = { decision: 1, stop: 1, pattern: 1, levels: 1, sizing: 1 };
const PR_KIND_MARK = { entry: ["Belépés", "var(--info)"], exit: ["Kilépés", "var(--down)"], p_start: ["Az alakzat kezdete", "var(--warn)"],
  p_end: ["Utolsó pivot", "var(--warn)"] };

// a chart egy pontja (k: nap a dontes napjahoz kepest, v: normalizalt ar) egy egeres / erintes esemenybol
function chartPoint(svg, ev) {
  const m = svg.__map;
  if (!m) return null;
  const r = svg.getBoundingClientRect();
  const px = (ev.clientX - r.left) * (m.W / r.width), py = (ev.clientY - r.top) * (m.H / r.height);
  const j = Math.round((px - m.padL - m.step / 2) / m.step);
  const v = m.hi - ((py - m.padT) / m.priceH) * (m.hi - m.lo);
  return { k: Math.max(-m.zero, Math.min(0, j - m.zero)), v: Math.round(v * 100) / 100, pxPerPrice: m.priceH / (m.hi - m.lo) };
}

// erintes / kattintas / huzas a charton (pointer esemenyek: eger es erintokepernyo egyutt)
function chartInput(svg, onDown, onMove) {
  svg.style.touchAction = "none";
  svg.classList.add("pr-input");
  let pressed = false;
  svg.addEventListener("pointerdown", (ev) => { pressed = true; svg.setPointerCapture(ev.pointerId); const p = chartPoint(svg, ev); if (p) onDown(p); });
  svg.addEventListener("pointermove", (ev) => { if (!pressed || !onMove) return; const p = chartPoint(svg, ev); if (p) onMove(p); });
  const up = () => { pressed = false; };
  svg.addEventListener("pointerup", up);
  svg.addEventListener("pointercancel", up);
}

function kindLevels(list) {
  return (list || []).map((h) => {
    const m = PR_LEVEL[h.kind] || [h.kind, "var(--muted)"];
    return { v: h.v, label: m[0], color: m[1], dash: !!m[2] };
  });
}

function kindSegments(list) {
  return (list || []).map((s) => ({ ...s, color: s.kind === "ref" ? "var(--up)" : "var(--info)", dash: s.kind === "ref" }));
}

function kindMarks(list) {
  return (list || []).filter((m) => m.k != null).map((m) => {
    const d = PR_KIND_MARK[m.kind] || [m.kind, "var(--muted)"];
    return { k: m.k, v: m.v, label: d[0], color: m.kind === "exit" ? (m.r > 0 ? "var(--up)" : "var(--down)") : d[1] };
  });
}

// tipusonkent: a feladat urlapja (HTML) es a kezeloje ({ read: valasz, reset? })
const KIND_UI = {
  stop: {
    html: (t) => `<p>${t.prompt.side === "long" ? "Long belépés a következő nyitáson." : "Short belépés a következő nyitáson."}
      Érintsd meg vagy kattintsd a chartot a stop helyén; húzással finomíthatod.</p>
      <label>Stop<input name="stop" type="number" step="0.01" inputmode="decimal"></label>
      <div class="wide"><span id="pr-risk" class="small muted"></span></div>`,
    bind(t, form, svg, show) {
      const atr = t.chart.atr || 1, sign = t.prompt.side === "long" ? 1 : -1;
      const set = (v) => { form.elements.stop.value = fmtIn(v); update(); };
      const update = () => {
        const v = Number(form.elements.stop.value);
        const ok = form.elements.stop.value !== "" && Number.isFinite(v);
        $("#pr-risk").textContent = ok ? `Stop-távolság: ${fmt((sign * (100 - v)) / atr, 2)} ATR` : "";
        show({ levels: ok ? [{ v, label: "Stop", color: "var(--down)" }] : [] });
      };
      form.elements.stop.addEventListener("input", update);
      chartInput(svg, (p) => set(p.v), (p) => set(p.v));
      update();
      return () => ({ stop: form.elements.stop.value === "" ? null : Number(form.elements.stop.value) });
    },
  },
  pattern: {
    html: (t) => `<p>Melyik alakzat formálódott a chart végén?</p>
      <div class="wide pr-options" role="radiogroup" aria-label="Alakzat">${t.prompt.options.map((o) =>
        `<button type="button" class="seg" role="radio" aria-checked="false" data-choice="${esc(o.key)}">${esc(o.label)}</button>`).join("")}</div>`,
    bind(t, form) {
      let choice = null;
      form.querySelectorAll("[data-choice]").forEach((b) => b.addEventListener("click", () => {
        if (b.disabled) return;
        choice = b.dataset.choice;
        form.querySelectorAll("[data-choice]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); });
      }));
      return () => ({ choice });
    },
  },
  levels: {
    html: (t) => (t.prompt.mode === "levels"
      ? `<p>Érintsd meg a chartot a legfontosabb vízszintes szinteknél (legfeljebb ${t.prompt.max}). Egy meglévő vonal megérintése törli.</p>`
      : `<p>${t.prompt.side === "lows" ? "Emelkedő trend: kösd össze a mélypontokat." : "Csökkenő trend: kösd össze a csúcsokat."}
          Két érintés: a trendvonal két pontja.</p>`)
      + `<div class="wide"><button class="btn" type="button" id="pr-undo">Visszavonás</button> <span id="pr-drawn" class="small muted"></span></div>`,
    bind(t, form, svg, show) {
      const mode = t.prompt.mode;
      let lines = [], pts = [];
      const update = () => {
        if (mode === "levels") {
          show({ levels: lines.map((v) => ({ v, label: "Vonal", color: "var(--info)" })) });
          $("#pr-drawn").textContent = lines.length ? `Vonalak: ${lines.map((v) => fmt(v, 2)).join(", ")}` : "";
        } else {
          show({ levels: [], segments: pts.length === 2 ? [{ k1: pts[0].k, v1: pts[0].v, k2: pts[1].k, v2: pts[1].v, color: "var(--info)" }] : [],
            marks: pts.map((p) => ({ k: p.k, v: p.v, label: "Pont", color: "var(--info)" })) });
          $("#pr-drawn").textContent = `${pts.length} / 2 pont`;
        }
      };
      chartInput(svg, (p) => {
        if (mode === "levels") {
          const near = lines.findIndex((v) => Math.abs(v - p.v) * p.pxPerPrice < 10);
          if (near >= 0) lines.splice(near, 1);
          else if (lines.length < t.prompt.max) lines.push(p.v);
        } else {
          pts = pts.length >= 2 ? [p] : pts.concat([p]);
        }
        update();
      });
      $("#pr-undo").addEventListener("click", () => { if (mode === "levels") lines.pop(); else pts.pop(); update(); });
      update();
      return () => (mode === "levels" ? { lines } : { points: pts.map((p) => [p.k, p.v]) });
    },
  },
  sizing: {
    html: (t) => {
      const p = t.prompt;
      return `<p>${p.side === "long" ? "Long" : "Short"} kötés. Számla: <strong>${fmt(p.account, 0)} $</strong>, kockázat kötésenként: <strong>${fmt(p.risk_pct, 1)}%</strong>.<br>
        Belépő: <strong>${fmt(p.entry, 2)} $</strong>, stop: <strong>${fmt(p.stop, 2)} $</strong>${p.target ? `, cél: <strong>${fmt(p.target, 2)} $</strong>` : ""}.</p>
        <label>Darabszám<input name="shares" type="text" inputmode="numeric" autocomplete="off"></label>
        <label>Kockázat ($)<input name="risk" type="text" inputmode="decimal" autocomplete="off"></label>
        ${p.target ? `<label>Cél / kockázat (R)<input name="rr" type="text" inputmode="decimal" autocomplete="off"></label>` : ""}`;
    },
    bind(t, form, svg, show) {
      const p = t.prompt;
      show({ levels: [{ v: p.entry, label: "Belépő", color: "var(--info)" }, { v: p.stop, label: "Stop", color: "var(--down)" }]
        .concat(p.target ? [{ v: p.target, label: "Cél", color: "var(--up)", dash: true }] : []) });
      return () => ({ shares: form.elements.shares.value.trim(), risk: form.elements.risk.value.trim(),
        ...(p.target ? { rr: form.elements.rr.value.trim() } : {}) });
    },
  },
};

async function pageKindTask(id, k, sess, t) {
  view.innerHTML = `
    <div class="page-head"><a href="${sess.lesson ? "#/learn" : "#/practice"}" class="muted">← ${sess.lesson ? "Tanulás" : "Gyakorlás"}</a><h1>Helyzet ${k + 1} / ${t.rounds}</h1>
      <span class="muted">Az utolsó látható záró = 100; a vízszintes tengely kereskedési napokban (0 = a döntés napja). ATR (14): ${fmt(t.chart.atr, 2)}</span></div>
    <section class="card chart-card"><div class="chart-legend"><span><span class="swatch" style="background:var(--ema20)"></span> EMA20</span>
      <span><span class="swatch" style="background:var(--ema50)"></span> EMA50</span><span><span class="swatch" style="background:var(--ema200)"></span> EMA200</span></div>
      <div class="card-body"><svg id="pr-chart" role="img" aria-label="Rejtett chart"></svg></div></section>
    <section class="card" id="pr-decision"><h2>${esc(PR_KIND[t.kind] || t.kind)}</h2><div class="card-body">
      <form id="pr-dform" class="ag-form">${KIND_UI[t.kind].html(t)}
        <div class="wide"><button class="btn primary" type="submit">Válasz rögzítése</button> <span class="muted small">A válasz után nem módosítható.</span> <span id="pr-err" class="small"></span></div>
      </form></div></section>
    <div id="pr-reveal"></div>`;
  const svg = $("#pr-chart");
  const form = $("#pr-dform");
  let chartOpt = {};
  const draw = () => practiceChart(svg, t.chart.bars, chartOpt);
  const show = (o) => { chartOpt = o; draw(); };
  if (chartRO) chartRO.disconnect();
  chartRO = new ResizeObserver(() => draw());
  chartRO.observe(svg.parentElement);
  draw();
  const read = KIND_UI[t.kind].bind(t, form, svg, show);
  if (t.decided) { showKindReveal(id, k, t, t.reveal, show, sess); return; }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const r = await api(`/api/practice/session/${id}/task/${k}/decision`, { method: "POST", body: JSON.stringify(read()) });
      showKindReveal(id, k, t, { ...r.reveal, progress: r.progress }, show, r.session);
    } catch (e) { const m = $("#pr-err"); m.className = "msg err small"; m.textContent = e.message; }
  });
}

function showKindReveal(id, k, t, rev, show, sess) {
  const form = $("#pr-dform");
  form.querySelectorAll("input, select, button").forEach((el) => { el.disabled = true; });
  const svg = $("#pr-chart");
  svg.style.touchAction = "";
  svg.classList.remove("pr-input");
  show({ levels: kindLevels(rev.levels), segments: kindSegments(rev.segments), marks: kindMarks(rev.marks),
    future: rev.future && rev.future.length ? rev.future : undefined, shown: (rev.future || []).length });
  const good = rev.quality >= 0.6;
  const last = sess ? sess.done : k + 1 >= t.rounds;
  $("#pr-reveal").innerHTML = `<section class="card"><h2>Értékelés</h2><div class="card-body">
      <div class="pr-verdict ${good ? "ok" : "bad"}" role="status"><span class="pr-verdict-icon" aria-hidden="true">${good ? "✓" : "✗"}</span>
        <div><div class="pr-verdict-label">Értékelés: ${fmt(rev.quality * 100, 0)}%</div></div></div>
      ${practicePoints(rev)}
      <ul class="digest-list">${rev.lines.map((l) => `<li><span>${esc(l.text)}</span></li>`).join("")}</ul></div></section>
    <section class="card"><div class="card-body"><div class="digest-controls"><button class="btn primary" id="pr-next" type="button">${last ? "Összegzés" : "Következő helyzet"}</button>
      <span class="muted small">Nem befektetési tanács. Gyakorlás szimulált adaton.</span></div></div></section>`;
  $("#pr-next").addEventListener("click", () => pagePracticeSession(id));
}

// megosztas (#57): kartya + link, csak gombnyomasra jon letre; a Profil oldalon visszavonhato
function shareBox(kind, ref) {
  return `<section class="card"><h2>Megosztás</h2><div class="card-body">
    <p class="small muted">Kártya és link a beceneveddel és az eredménnyel – a helyzetek és a válaszaid nélkül, hogy más is kitölthesse. A Profil oldalon visszavonhatod.</p>
    <div class="digest-controls"><select id="share-group" hidden aria-label="Csoport meghívókódja a megosztásban"></select>
      <button class="btn primary" id="share-btn" type="button" data-kind="${kind}" data-ref="${esc(String(ref))}">Eredmény megosztása</button>
      <span id="share-msg" class="small" role="status"></span></div></div></section>`;
}

async function wireShare() {
  const btn = $("#share-btn");
  if (!btn) return;
  const sel = $("#share-group");
  btn.addEventListener("click", async () => {
    const msg = $("#share-msg");
    try {
      const r = await api("/api/share", { method: "POST", body: JSON.stringify({ kind: btn.dataset.kind, ref: btn.dataset.ref, group: sel.value || null }) });
      let copied = false;
      try { await navigator.clipboard.writeText(r.url); copied = true; } catch (e) { /* nincs vagolap-engedely: a link latszik */ }
      msg.className = "msg ok small";
      msg.innerHTML = `<a href="${esc(r.url)}" target="_blank" rel="noopener" translate="no">${esc(r.url)}</a> ${copied ? "<span>(a vágólapon)</span>" : ""}`;
      if (navigator.share) navigator.share({ url: r.url }).catch(() => {});
    } catch (e) { msg.className = "msg err small"; msg.textContent = e.message; }
  });
  const groups = await api("/api/groups").catch(() => []);
  if (groups.length) {
    sel.innerHTML = `<option value="">Csoport nélkül</option>${groups.map((g) => `<option value="${g.id}">Meghívó: ${esc(g.name)}</option>`).join("")}`;
    sel.hidden = false;
  }
}

function renderPracticeSummary(p) {
  const s = p.summary;
  const lines = `<ul class="digest-list">${p.lines.map((l) => `<li><span>${esc(l.text)}</span></li>`).join("")}</ul>`;
  const maxb = Math.max(1, ...s.stop_buckets.map((b) => b.n));
  const buckets = s.stop_buckets.map((b) => `<div class="part"><span>${b.hi == null ? `≥ ${fmt(b.lo, 0)} ATR` : `${fmt(b.lo, 0)}–${fmt(b.hi, 0)} ATR`}</span>
    <span class="track"><span class="fill" style="width:${(b.n / maxb) * 100}%"></span></span><span class="muted small">${b.n}</span></div>`).join("");
  const ls = p.lesson;
  const dailyBox = p.session.daily ? `<section class="card"><div class="card-body"><div class="digest-controls">
      <strong>Napi kihívás (${esc(p.session.daily)}): ${p.tasks.reduce((a, t) => a + (t.points || 0), 0)} pont</strong>
      <a class="btn primary" href="#/leaderboard">Ranglista</a></div></div></section>${shareBox("daily", p.session.daily)}` : "";
  const lessonBox = ls ? `<section class="card"><div class="card-body"><div class="pr-verdict ${ls.passed ? "ok" : "warn"}" role="status">
      <span class="pr-verdict-icon" aria-hidden="true">${ls.passed ? "✓" : "↺"}</span>
      <div><div class="pr-verdict-label">${ls.level}. szint: ${ls.passed ? "teljesítve" : "még nem teljesült"}</div>
        <div class="small">Jó vagy balszerencsés döntés: ${fmt(ls.share * 100, 0)}% (kell: ${fmt(ls.needed * 100, 0)}%).</div></div></div>
      <div class="digest-controls">${ls.next ? `<a class="btn primary" href="#/learn/${ls.next}">Tovább a ${ls.next}. szintre</a>` : ""}
        <a class="btn" href="#/learn/${ls.level}">${ls.passed ? "A lecke újra" : "Újrapróbálom"}</a></div></div></section>${ls.passed ? shareBox("level", ls.level) : ""}` : "";
  const kindsBox = Object.keys(p.kinds || {}).length ? `<section class="card"><h2>Feladattípusok</h2><div class="card-body"><ul class="digest-list">
      ${Object.entries(p.kinds).map(([key, v]) => `<li><span>${esc(PR_KIND[key] || key)}: ${v.n} feladat, átlagos értékelés ${fmt(v.mean_quality * 100, 0)}%</span></li>`).join("")}
    </ul></div></section>` : "";
  view.innerHTML = `
    <div class="page-head"><a href="${ls ? "#/learn" : "#/practice"}" class="muted">← ${ls ? "Tanulás" : "Gyakorlás"}</a><h1>A kör összegzése</h1>
      ${p.session.code ? `<span class="muted">Megosztási kód: <code>${esc(p.session.code)}</code> – ugyanazzal a feladatkészlettel ugyanazt a helyzetsort adja.</span>` : ""}</div>
    ${lessonBox}${dailyBox}${p.session.duel ? `<section class="card"><div class="card-body"><a class="btn primary" href="#/duel/${esc(p.session.duel)}">Vissza a párbajhoz</a></div></section>` : ""}${p.session.exam
      ? `<section class="card"><div class="card-body"><a class="btn primary" href="#/learn">Vizsgaeredmény és tanúsítvány</a></div></section>` : ""}${kindsBox}
    ${s.decided ? `<div class="kpis">
      <div class="kpi"><div class="label">Teljesült kötés</div><div class="value">${s.filled}</div><div class="sub">${s.entered} belépési döntés, ${s.skipped} kihagyás</div></div>
      <div class="kpi"><div class="label">Nyerési arány</div><div class="value">${s.win == null ? "–" : `${fmt(s.win * 100, 1)}%`}</div><div class="sub">átlag ${signed(s.mean_r, 2, " R")}</div></div>
      <div class="kpi"><div class="label">Véletlen alapvonal</div><div class="value ${rCls(s.baseline_r)}">${signed(s.baseline_r, 2, " R")}</div><div class="sub">ugyanazokon a papírokon</div></div>
      <div class="kpi"><div class="label">Kihagyott jó helyzet</div><div class="value">${s.missed_good}</div><div class="sub">${s.good_total} helyzetben volt legalább +1 R</div></div>
      ${s.reviewed ? `<div class="kpi"><div class="label">Ítélet</div><div class="value">${s.verdicts.good} / ${s.reviewed}</div>
        <div class="sub">jó döntés · szerencsés ${s.verdicts.lucky} · balszerencsés ${s.verdicts.unlucky} · rossz ${s.verdicts.bad}</div></div>` : ""}
    </div>
    <section class="card"><h2>Összefoglaló</h2><div class="card-body">${lines}</div></section>
    <section class="card"><h2>Stop-távolság (ATR)</h2><div class="card-body"><div class="parts">${buckets}</div></div></section>` : ""}
    <section class="card"><h2>Helyzetek</h2><div class="card-body"><div class="table-scroll"><table class="table"><thead><tr><th>#</th><th>Papír</th><th>Dátum</th><th>Helyzet</th><th>Döntés</th><th>Ítélet</th><th>Eredmény</th><th>Stratégiák</th><th>Referencia</th><th>Alapvonal</th></tr></thead><tbody>
      ${p.tasks.map((t) => (t.kind && t.kind !== "decision" ? `<tr><td>${t.k + 1}</td><td class="ticker">${esc(t.symbol || "")}</td><td>${esc(t.date || "")}</td>
        <td>${esc(PR_KIND[t.kind] || t.kind)}</td><td>–</td><td>${t.quality != null ? `${fmt(t.quality * 100, 0)}%` : "–"}</td><td colspan="4" class="muted small">${t.points != null ? `+${t.points} pont` : ""}</td></tr>` : `<tr><td>${t.k + 1}</td><td class="ticker">${esc(t.symbol || "")}</td><td>${esc(t.date || "")}</td><td>${esc(PR_CAT[t.category] || "")}</td>
        <td>${t.action ? esc(PR_SIDE[t.action] || t.action) : "–"}</td>
        <td>${t.verdict && PR_VERDICT[t.verdict] ? `<span class="pill plain ${PR_VERDICT[t.verdict][1]}"><span aria-hidden="true">${PR_VERDICT[t.verdict][2]}</span> <span>${esc(PR_VERDICT[t.verdict][0])}</span></span>` : "–"}</td>
        <td class="num ${rCls(t.r)}">${t.r != null ? signed(t.r, 2, " R") : t.status ? esc(PR_STATUS[t.status] || t.status) : "–"}</td>
        <td class="small">${(t.strategies || []).map((x) => `${esc(PR_STRAT[x.strategy] || x.strategy)}: ${x.r != null ? signed(x.r, 2, " R") : esc(PR_STATUS[x.status] || "–")}`).join(" · ") || "–"}</td>
        <td class="num ${rCls(t.reference_r)}">${signed(t.reference_r, 2, " R")}</td><td class="num ${rCls(t.baseline_r)}">${signed(t.baseline_r, 2, " R")}</td></tr>`)).join("")}
    </tbody></table></div></div></section>`;
  wireShare();
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
        if (heads[i] && td.dataset.label !== heads[i]) td.dataset.label = heads[i];   // a forditas utan is a fejlec szovege
      }));
    }
  });
}
// characterData: a forditas (i18n.js) a fejlec szoveget helyben csereli - a kartya-cimkek kovessek
new MutationObserver(() => adaptTables(view)).observe(view, { childList: true, subtree: true, characterData: true });

// Az elso utvonal-kezeles a fajl VEGEN: a kesobb deklaralt let/const-ok (pl. practiceTimer, NEWS_LABEL) csak itt
// inicializaltak - kozvetlen betolteskor (#/practice, #/events) kulonben "before initialization" hiba jonne.
window.addEventListener("hashchange", route);
route();
