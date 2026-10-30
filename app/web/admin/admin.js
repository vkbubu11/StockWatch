// Admin felulet (#58): csak admin szerepkorrel es ketlepcsos azonositassal (TOTP). Magyar nyelvu uzemeltetoi eszkoz.
"use strict";

const main = document.getElementById("adm");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = (v) => (v ? new Date(v).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }) : "–");
const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const TABS = { overview: "Áttekintés", users: "Felhasználók", content: "Tartalom", community: "Közösség", system: "Rendszer", audit: "Napló" };
let tab = "overview";

async function api(path, body) {
  const init = body === undefined ? {} : { method: "POST", body: JSON.stringify(body) };
  init.headers = { "X-StockWatch": "1", "Content-Type": "application/json" };
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
  return data;
}

function table(head, rows) {
  return `<div class="table-scroll"><table class="table cards-on-phone"><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("") || `<tr><td colspan="${head.length}" class="muted">Nincs adat.</td></tr>`}</tbody></table></div>`;
}

function labelCells(root) {             // telefonon a tablazat kartyakka valik: a cimkek a fejlecbol
  root.querySelectorAll("table.cards-on-phone").forEach((t) => {
    const heads = [...t.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    t.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => { if (heads[i]) td.dataset.label = heads[i]; }));
  });
}

function msg(text, ok = true) {
  const box = document.getElementById("adm-msg");
  if (box) { box.className = `msg ${ok ? "" : "err"} small`; box.textContent = text; }
}

async function act(path, body, confirmText) {
  if (confirmText && !confirm(confirmText)) return null;
  try { const r = await api(path, body || {}); msg(r.message || "Kész."); return r; } catch (e) { msg(e.message, false); return null; }
}

// --- belepes: TOTP beallitas / kod -------------------------------------------------------------
async function login() {
  const s = await api("/api/admin/state");
  if (s.verified) return start();
  main.innerHTML = `<section class="card"><h2>Kétlépcsős azonosítás</h2><div class="card-body">
    ${s.enrolled ? `<p>Írd be a hitelesítő alkalmazás (pl. Google Authenticator, Aegis) 6 jegyű kódját.</p>`
      : `<p>Első belépés: add hozzá a fiókot egy hitelesítő alkalmazáshoz.</p>
        <ol>
          <li>Telepíts egy hitelesítő alkalmazást: Google Authenticator, Microsoft Authenticator vagy Aegis.</li>
          <li>Az alkalmazásban: <strong>+</strong> → <strong>Beállítási kulcs megadása</strong> (Enter a setup key).
            Fiók neve: <em>StockWatch admin</em> · kulcs: az alábbi · típus: <strong>időalapú</strong>.</li>
          <li>Írd be lent az alkalmazás 6 jegyű kódját.</li>
        </ol>
        <p>Kulcs: <code id="totp-key" style="word-break:break-all;font-size:1.1em">${esc(s.secret.replace(/(.{4})(?=.)/g, "$1 "))}</code>
          <button class="btn" type="button" id="totp-copy">Másolás</button></p>
        <p class="muted small">Fiók: ${esc(s.email)} · 30 másodperces, 6 jegyű kód. A kulcs az első helyes kód után többé nem látszik.<br>
          Alternatíva: <a href="${esc(s.uri)}">megnyitás a telefon alapértelmezett kódkezelőjében</a> – iPhone-on ez gyakran a Jelszavak app;
          az is jó, ha ott megjelenik a 6 jegyű kód.</p>`}
    <form id="totp" class="ag-form"><label>Kód<input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required></label>
      <div class="wide"><button class="btn primary" type="submit">Belépés</button> <span id="adm-msg" class="small"></span></div></form>
  </div></section>`;
  const copy = document.getElementById("totp-copy");
  if (copy) copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(s.secret); copy.textContent = "Másolva"; }
    catch (_) { getSelection().selectAllChildren(document.getElementById("totp-key")); }   // kezzel masolhato
  });
  document.getElementById("totp").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api("/api/admin/verify", { code: ev.target.elements.code.value }); start(); } catch (e) { msg(e.message, false); }
  });
}

function start() {
  const out = document.getElementById("adm-logout");
  out.hidden = false;
  out.onclick = async () => { await api("/api/admin/logout", {}); location.reload(); };
  render();
}

async function render() {
  main.innerHTML = `<nav class="segmented adm-tabs" aria-label="Admin">${Object.entries(TABS).map(([k, v]) =>
    `<button type="button" class="seg ${k === tab ? "on" : ""}" data-tab="${k}" aria-pressed="${k === tab}">${v}</button>`).join("")}</nav>
    <p id="adm-msg" class="small"></p><div id="adm-body"><div class="loading">Betöltés…</div></div>`;
  main.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; render(); }));
  const body = document.getElementById("adm-body");
  try {
    body.innerHTML = await VIEWS[tab]();
    labelCells(body);
    if (BIND[tab]) BIND[tab](body);
  } catch (e) {
    if (e.status === 403) return login();
    body.innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
  }
}

// --- nezetek -------------------------------------------------------------------------------------
const FUNNEL = { landing: "Kezdőoldal-megtekintés", register: "Regisztráció", verify: "E-mail megerősítve", first_decision: "Első döntés",
  level1: "1. szint teljesítve" };
let days = 30;
let overviewData = null;                 // a kizart felhasznalok listajahoz (BIND.overview)
let exQuery = "", exOpen = false;        // a kizart-lista keresese es nyitott allapota ujrarajzolas utan is megmarad

const VIEWS = {
  async overview() {
    const o = overviewData = await api(`/api/admin/overview?days=${days}`);
    const top = o.funnel[1].n || 0;
    const funnel = o.funnel.map((s, i) => `<tr><td>${FUNNEL[s.step]}</td><td class="num">${s.n}</td>
      <td class="num">${i > 1 && top ? pct(s.n / top) : "–"}</td></tr>`);
    const cohorts = o.cohorts.map((c) => `<tr><td>${esc(c.week)}</td><td class="num">${c.size}</td>${c.retention.map((r) => `<td class="num">${pct(r)}</td>`).join("")}</tr>`);
    const views = (key) => o.pageviews[key].map((r) => `<tr><td>${esc(r.key || "–")}</td><td class="num">${r.n}</td></tr>`);
    return `<div class="digest-controls"><label>Időszak <select id="adm-days">${[7, 30, 90, 365].map((d) => `<option ${d === days ? "selected" : ""}>${d}</option>`).join("")}</select> nap</label></div>
      <details class="card" id="adm-ex" ${exOpen ? "open" : ""}><summary class="card-body"><strong>Kizárva az összegzésből: ${o.excluded.length} felhasználó</strong></summary>
        <div class="card-body"><p class="muted small">A bepipált felhasználók (pl. saját és tesztfiókok) eseményei nem számítanak bele a mutatókba.
          A név nélküli látogatások (kezdőoldal-megtekintés, források, eszközök) nem köthetők felhasználóhoz, azok maradnak.</p>
          <label>Keresés (e-mail vagy becenév)<input id="adm-ex-q" type="search" value="${esc(exQuery)}" autocomplete="off"></label>
          <div id="adm-ex-list" class="adm-ex-list"></div></div></details>
      <div class="kpis"><div class="kpi"><div class="label">Aktív ma</div><div class="value">${o.active.day}</div></div>
        <div class="kpi"><div class="label">Aktív 7 nap</div><div class="value">${o.active.week}</div></div>
        <div class="kpi"><div class="label">Aktív 30 nap</div><div class="value">${o.active.month}</div></div>
        <div class="kpi"><div class="label">Döntés / felhasználó</div><div class="value">${o.decisions.per_user ?? "–"}</div><div class="sub">${o.decisions.total} döntés</div></div>
        <div class="kpi"><div class="label">Hibák (7 nap)</div><div class="value">${o.errors.js + o.errors.server}</div><div class="sub">böngésző ${o.errors.js} · szerver ${o.errors.server}</div></div>
        <div class="kpi"><div class="label">Megosztás</div><div class="value">${o.shares.created}</div><div class="sub">${o.shares.views} megnyitás · ${o.shares.registers} regisztráció</div></div></div>
      <section class="card"><h2>Tölcsér (${o.days} nap)</h2><div class="card-body">${table(["Lépés", "Fő", "A regisztráltak"], funnel)}</div></section>
      <section class="card"><h2>Heti kohorsz-visszatérés</h2><div class="card-body">
        ${table(["Regisztráció hete", "Fő", ...Array.from({ length: 8 }, (_, i) => `${i + 1}. hét`)], cohorts)}</div></section>
      <section class="card"><h2>Tanulási út</h2><div class="card-body">${table(["Szint", "Elkezdte", "Teljesítette", "Arány"],
        o.levels.map((l) => `<tr><td>${l.level}.</td><td class="num">${l.started}</td><td class="num">${l.passed}</td><td class="num">${l.started ? pct(l.passed / l.started) : "–"}</td></tr>`))}</div></section>
      <section class="card"><h2>Napi kihívás (14 nap)</h2><div class="card-body">${table(["Nap", "Játékos"], o.daily.map((d) => `<tr><td>${esc(d.day)}</td><td class="num">${d.players}</td></tr>`))}</div></section>
      <section class="card"><h2>Látogatások (${o.days} nap)</h2><div class="card-body">
        <h3>Honnan</h3>${table(["Forrás", "Megtekintés"], views("source"))}<h3>Eszköz</h3>${table(["Eszköz", "Megtekintés"], views("device"))}
        <h3>Oldal</h3>${table(["Oldal", "Megtekintés"], views("path"))}
        <p class="muted small">Süti és azonosító nélkül, a szerveren számolva; robotok nélkül.</p></div></section>`;
  },
  async users() {
    return `<form id="adm-search" class="ag-form"><label>Keresés (e-mail vagy becenév)<input name="q" autocomplete="off"></label>
      <div class="wide"><button class="btn primary" type="submit">Keresés</button></div></form><div id="adm-users"></div><div id="adm-user"></div>`;
  },
  async content() {
    const c = await api("/api/admin/content");
    const p = c.pool;
    const daily = c.daily.map((d) => `<tr><td>${esc(d.day)}${d.replaced ? ` <span class="badge watch">cserélve</span>` : ""}</td>
      <td class="small">${d.tasks.map((t) => `${esc(t.category)}${t.source && t.category !== "strategy" ? ` (${esc(t.source)})` : ""}`).join(" · ")}</td>
      <td><button class="btn" type="button" data-daily="${esc(d.day)}">Csere</button></td></tr>`);
    const versions = c.lessons.versions.map((v) => `<tr><td>#${v.id}</td><td>${when(v.created_at)}</td><td>${esc(v.admin || "")}</td><td>${esc(v.note || "")}</td>
      <td><button class="btn" type="button" data-restore="${v.id}">Visszaállítás</button></td></tr>`);
    return `<section class="card"><h2>Feladatkészlet</h2><div class="card-body">
        ${p ? `<p>${p.symbols} papír · ${Object.entries(p.counts).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}<br><span class="muted small">Készült: ${when(p.created)}</span></p>` : "<p>Nincs feladatkészlet.</p>"}
        <button class="btn" type="button" id="adm-rebuild" ${c.rebuilding ? "disabled" : ""}>${c.rebuilding ? "Újraépítés folyamatban…" : "Újraépítés"}</button>
        <p class="muted small">Az újraépítés néhány perc; a napi kihívás helyzetsora a készlettel együtt változik.</p></div></section>
      <section class="card"><h2>Napi kihívás – következő napok</h2><div class="card-body">${table(["Nap", "Helyzetek", ""], daily)}
        <p class="muted small">Csere: új helyzetsor arra a napra (pl. hibás feladat). Aki már elkezdte, a sajátját játssza.</p></div></section>
      <section class="card"><h2>Leckék</h2><div class="card-body">
        <p class="muted small">Forrás: ${c.lessons.source === "db" ? "szerkesztett verzió" : "a csomag fájlja (locale/lessons.hu.json)"}. A mentés előtt a szerver ellenőrzi
          a szerkezetet (szintek, szöveg, ábra, feladatsor); érvénytelent nem ment. A szöveg fordítása a katalógusból jön – új mondatnál a többi nyelven magyarul jelenik meg.</p>
        <label class="wide">JSON<textarea id="adm-lessons" spellcheck="false" rows="18">${esc(JSON.stringify(c.lessons.data, null, 2))}</textarea></label>
        <div class="digest-controls"><input id="adm-note" placeholder="Megjegyzés (mit változtattál)" maxlength="200">
          <button class="btn" type="button" id="adm-preview">Előnézet</button><button class="btn primary" type="button" id="adm-save">Mentés új verzióként</button></div>
        <div id="adm-lesson-preview"></div>
        <h3>Verziók</h3>${table(["Verzió", "Mentve", "Admin", "Megjegyzés", ""], versions)}</div></section>`;
  },
  async community() {
    const g = await api("/api/admin/groups");
    return `<section class="card"><h2>Csoportok</h2><div class="card-body">${table(["Csoport", "Meghívókód", "Létrehozó", "Tagok", "Készült", ""],
      g.map((x) => `<tr><td>${esc(x.name)}</td><td><code>${esc(x.code)}</code></td><td>${esc(x.owner_nickname || x.owner_email || "–")}</td>
        <td class="num">${x.members}</td><td>${when(x.created_at)}</td><td><button class="btn" type="button" data-group="${x.id}" data-gact="revoke_code">Kód visszavonása</button>
        <button class="btn" type="button" data-group="${x.id}" data-gact="delete">Törlés</button></td></tr>`))}</div></section>
      <section class="card"><h2>Ranglista-moderáció</h2><div class="card-body"><p>Nem megfelelő becenév: a Felhasználók fülön a felhasználó adatlapján „Becenév törlése”.</p></div></section>
      <section class="card"><h2>Visszajelzések</h2><div class="card-body"><p>A visszajelzések a kapcsolati e-mail-címre érkeznek (az Információ oldal linkje); a felület nem tárolja őket.</p></div></section>`;
  },
  async system() {
    const s = await api("/api/admin/system");
    const row = (k, v) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`;
    return `<section class="card"><h2>Rendszerállapot</h2><div class="card-body">${table(["Elem", "Állapot"], [
      row("Adatbázis", `elérhető · ${s.db_ms} ms · migráció: ${esc(s.migration)}`),
      row("Felhasználók", `${s.users} (megerősítve: ${s.verified})`),
      row("E-mail-küldés", s.mail ? "beállítva" : `<span class="down">nincs beállítva</span>`),
      row("Utolsó mentés", s.backup ? `${esc(s.backup.name)} · ${(s.backup.bytes / 1024).toFixed(1)} KB · ${when(s.backup.at)} (${s.backups} db)` : `<span class="down">nincs</span>`),
      row("Lemezhasználat (data, logs)", `${s.disk_mb} MB`),
      row("Telepítve", when(s.deployed)),
      row("Feladatkészlet", s.pool ? `${s.pool.symbols} papír${s.pool_rebuilding ? " · újraépítés folyamatban" : ""}` : "nincs"),
      row("Hibák (7 nap)", `böngésző ${s.errors.js} · szerver ${s.errors.server}`)])}</div></section>`;
  },
  async audit() {
    const a = await api("/api/admin/audit");
    return `<section class="card"><h2>Audit-napló</h2><div class="card-body">
      <p class="muted small">Minden admin művelet; csak hozzáfűzhető, a felületről nem törölhető.</p>
      ${table(["Idő", "Admin", "Művelet", "Felhasználó", "Részletek"], a.map((r) => `<tr><td>${when(r.at)}</td><td>${esc(r.admin || r.admin_id)}</td>
        <td>${esc(r.action)}</td><td>${r.target_user ?? "–"}</td><td class="small">${esc(JSON.stringify(r.details))}</td></tr>`))}</div></section>`;
  },
};

const USER_ACTIONS = { resend: ["Megerősítő levél újraküldése"], suspend: ["Felfüggesztés", "Felfüggeszted? A munkamenetei megszűnnek."],
  unsuspend: ["Visszaállítás"], reset_nickname: ["Becenév törlése", "Törlöd a becenevét (moderáció)?"], export: ["Adatexport (JSON)"],
  delete: ["Fiók törlése", "Véglegesen törlöd a fiókot és minden adatát? Ez nem vonható vissza."] };

async function showUser(id) {
  const u = await api(`/api/admin/users/${id}`);
  const box = document.getElementById("adm-user");
  if (!box) return;
  box.innerHTML = `<section class="card"><h2>${esc(u.email)}</h2><div class="card-body">
    <p>Becenév: ${esc(u.nickname || "–")} · regisztrált: ${when(u.created_at)} · megerősítve: ${when(u.verified_at)}<br>
      Belépés: ${[u.password ? "jelszó" : "", u.google ? "Google" : ""].filter(Boolean).join(" + ") || "–"} · utolsó aktivitás: ${when(u.last_active)} · XP ${u.xp}
      · körök: ${u.rounds} (napi: ${u.daily_rounds}) · döntések: ${u.decisions} · aktív munkamenet: ${u.active_sessions}
      ${u.suspended_at ? `<br><span class="badge warn">felfüggesztve: ${when(u.suspended_at)}</span>` : ""}${u.role ? ` <span class="badge go">${esc(u.role)}</span>` : ""}</p>
    <p class="small">Leckék: ${u.lessons.map((l) => `${l.level}. ${l.completed_at ? "✓" : pct(l.best_share)}`).join(" · ") || "–"}<br>
      Csoportok: ${u.groups.map((g) => esc(g.name) + (g.owner ? " (saját)" : "")).join(", ") || "–"}</p>
    <div class="digest-controls">${Object.entries(USER_ACTIONS).filter(([k]) => (k !== "resend" || !u.verified_at) && (k !== "suspend" || !u.suspended_at)
      && (k !== "unsuspend" || u.suspended_at)).map(([k, v]) => `<button class="btn ${k === "delete" ? "danger" : ""}" type="button" data-uact="${k}">${v[0]}</button>`).join("")}</div>
  </div></section>`;
  box.querySelectorAll("[data-uact]").forEach((b) => b.addEventListener("click", async () => {
    const [, confirmText] = USER_ACTIONS[b.dataset.uact];
    const r = await act(`/api/admin/users/${id}/${b.dataset.uact}`, {}, confirmText);
    if (!r) return;
    if (b.dataset.uact === "export") {
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([JSON.stringify(r.export, null, 2)], { type: "application/json" })),
        download: `felhasznalo-${id}.json` });
      a.click();
      return;
    }
    if (b.dataset.uact === "delete") { box.innerHTML = ""; return; }
    showUser(id);
  }));
}

const BIND = {
  overview(body) {
    body.querySelector("#adm-days").addEventListener("change", (e) => { days = Number(e.target.value); render(); });
    body.querySelector("#adm-ex").addEventListener("toggle", (e) => { exOpen = e.target.open; });
    const list = async () => {
      const q = exQuery;
      const found = await api(`/api/admin/users?q=${encodeURIComponent(q)}`);
      const box = document.getElementById("adm-ex-list");
      if (!box || q !== exQuery) return;                 // kozben tovabb gepelt vagy fulet valtott
      // a kizartak mindig elol (a talalati lista 50 fore korlatozott)
      const match = (u) => `${u.email} ${u.nickname || ""}`.toLowerCase().includes(q.trim().toLowerCase());
      const rows = [...overviewData.excluded.filter(match).map((u) => ({ ...u, stats_excluded: true })),
        ...found.filter((u) => !u.stats_excluded)];
      box.innerHTML = rows.length ? rows.map((u) => `<label class="check"><input type="checkbox" data-ex="${u.id}" ${u.stats_excluded ? "checked" : ""}>
        <span>${esc(u.email)}${u.nickname ? ` · ${esc(u.nickname)}` : ""}</span></label>`).join("") : `<p class="muted">Nincs találat.</p>`;
      box.querySelectorAll("[data-ex]").forEach((c) => c.addEventListener("change", async () => {
        if (await act(`/api/admin/users/${c.dataset.ex}/${c.checked ? "exclude" : "include"}`, {})) render();
        else c.checked = !c.checked;
      }));
    };
    let timer = null;
    body.querySelector("#adm-ex-q").addEventListener("input", (e) => { exQuery = e.target.value; clearTimeout(timer); timer = setTimeout(list, 250); });
    list();
  },
  users(body) {
    const search = async (q) => {
      const list = await api(`/api/admin/users?q=${encodeURIComponent(q)}`);
      const box = document.getElementById("adm-users");
      if (!box) return;                                  // kozben masik fulre valtott
      box.innerHTML = table(["E-mail", "Becenév", "Regisztrált", "Állapot", "Utolsó aktivitás", "XP"], list.map((u) =>
        `<tr class="clickable" data-user="${u.id}"><td>${esc(u.email)}</td><td>${esc(u.nickname || "–")}</td><td>${when(u.created_at)}</td>
        <td>${u.suspended_at ? "felfüggesztve" : u.verified_at ? "aktív" : "nincs megerősítve"}${u.role ? ` · ${esc(u.role)}` : ""}</td><td>${when(u.last_active)}</td><td class="num">${u.xp}</td></tr>`));
      labelCells(box);
      box.querySelectorAll("[data-user]").forEach((tr) => tr.addEventListener("click", () => showUser(tr.dataset.user)));
    };
    body.querySelector("#adm-search").addEventListener("submit", (ev) => { ev.preventDefault(); search(ev.target.elements.q.value); });
    search("");
  },
  content(body) {
    body.querySelector("#adm-rebuild").addEventListener("click", async () => { if (await act("/api/admin/pool/rebuild", {}, "Újraépíted a feladatkészletet?")) render(); });
    body.querySelectorAll("[data-daily]").forEach((b) => b.addEventListener("click", async () => {
      if (await act("/api/admin/daily/replace", { day: b.dataset.daily }, `Lecseréled a(z) ${b.dataset.daily} napi helyzetsorát?`)) render();
    }));
    body.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
      if (await act("/api/admin/lessons/restore", { id: Number(b.dataset.restore) }, `Visszaállítod a #${b.dataset.restore} verziót?`)) render();
    }));
    const parse = () => { try { return JSON.parse(body.querySelector("#adm-lessons").value); } catch (e) { msg(`Hibás JSON: ${e.message}`, false); return null; } };
    body.querySelector("#adm-preview").addEventListener("click", () => {
      const data = parse();
      if (!data) return;
      body.querySelector("#adm-lesson-preview").innerHTML = Array.isArray(data) ? table(["Szint", "Cím", "Bekezdés", "Ábra", "Feladatsor"],
        data.map((l) => `<tr><td>${esc(l.level)}</td><td>${esc(l.title)}</td><td class="num">${(l.paragraphs || []).length}</td>
          <td>${esc(l.figure && (l.figure.pattern || l.figure.kind))}</td><td class="small">${esc(JSON.stringify(l.tasks))}</td></tr>`)) : "";
      labelCells(body);
    });
    body.querySelector("#adm-save").addEventListener("click", async () => {
      const data = parse();
      if (data && await act("/api/admin/lessons", { data, note: body.querySelector("#adm-note").value })) render();
    });
  },
  community(body) {
    body.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", async () => {
      const text = b.dataset.gact === "delete" ? "Törlöd a csoportot? A tagság megszűnik." : "Visszavonod a meghívókódot? Új kód készül.";
      if (await act(`/api/admin/groups/${b.dataset.group}/${b.dataset.gact}`, {}, text)) render();
    }));
  },
};

login().catch((e) => { main.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; });
