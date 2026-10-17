/* StockWatch helyi felulet - vanilla JS, keretrendszer nelkul.
 * Utvonalak (hash): #/  #/p/TICKER  #/holdings  #/positions  #/broker  #/ledger  #/signal/ID  #/alerts  #/settings
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const tooltip = $("#tooltip");
let overview = null;
let patternStats = {};      // backtest-eredmeny alakzatonkent (az info-panelhez)
const statsFor = (name, dir) => patternStats[`${name}|${dir || "bullish"}`] || null;
let jobTimer = null;
let chartRO = null;

// ---------------------------------------------------------------------------
// Segedek
// ---------------------------------------------------------------------------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("hu-HU", { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v) => (v === null || v === undefined ? "–" : `${fmt(v, 2)} $`);
const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}%`);
// pontos idopont (a bongeszo, azaz magyar ido szerint); a tooltipben a New York-i ido
const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  const opts = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  const ny = d.toLocaleString("hu-HU", { ...opts, timeZone: "America/New_York" });
  return `<span title="New York: ${esc(ny)}">${esc(d.toLocaleString("hu-HU", opts))}</span>`;
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

const KIND = {
  breakout: { text: "Megerősített kitörés", cls: "go" },
  retest: { text: "Visszateszt", cls: "go" },
  setup: { text: "Előkészület", cls: "watch" },
  pullback: { text: "Visszahúzás", cls: "go" },
  momentum: { text: "Momentum-vétel", cls: "go" },
};
// Strategiak: melyik kuldte a jelzest / kotest
const STRAT = {
  breakout: ["s-breakout", "Kitörés", "A kitöréses alakzat-motor: megerősített kitörés vagy visszateszt után long belépés."],
  pullback: ["s-pullback", "Visszahúzás (RSI2)", "Mély, rövid visszahúzódás emelkedő trendben: vételi limit a záró alatt, kilépés a ConnorsRSI 50 fölé emelkedésekor."],
  deep: ["s-deep", "Mély visszahúzás", "A Visszahúzás (RSI2) bemenete (RSI(2) < 2, vételi limit 5%-kal a záró alatt, kilépés ConnorsRSI > 50), de tág, 5 ATR-es stoppal: a validáción a szoros stop a mélyponton zárta ki a később visszapattanó kötéseket. Alapból csak virtuálisan követ, értesítést nem küld (a jelzései a Visszahúzáséval egyeznek)."],
  etf: ["s-etf", "ETF visszahúzás", "Likvid részvény-ETF-ek (SPY, QQQ, szektorok…) rövid túladottsága emelkedő trendben: vételi limit 1%-kal a záró alatt, kilépés az első visszapattanáskor (záró > SMA5 vagy ConnorsRSI > 50). Nincs gyorsjelentés, nincs egyedi csődkockázat; naponta max. 2, egyszerre max. 4 kötés."],
  momentum: ["s-momentum", "Momentum (12-1)", "Havi rotáció a legerősebb papírokba (151 Trading Strategies, 3.1): a hónap utolsó napján a Nasdaq-100 + S&P 500 tagjait a 12 havi hozamuk (a legutóbbi hónap nélkül) szerint rangsorolja, és a legjobb 10-et tartja egyenlő súllyal. Egy papír addig marad, amíg a legjobb 20-on belül van. Nagy hozam, de nagy visszaesések (a múltbeli tesztben ~38–48%)."],
  sandbox: ["s-sandbox", "Homokozó", "Szabad kutatói stratégia – soha nincs a paper fiókhoz kötve."],
};
const strategyBadge = (id, paper) => {
  if (!id) return `<span class="muted small">–</span>`;
  const [cls, label, help] = STRAT[id] || ["fail", id, ""];
  return `<span class="badge ${cls} has-help" title="${esc(help)}">${esc(label)}</span>${paper ? ` <span class="pill ok small" title="Ez a stratégia van a paper fiókhoz kötve">paper</span>` : ""}`;
};
// szuro-gombsor: Mind + a strategiak (+ opcionalis "egyeb")
const strategySeg = (current, attr, extra = "") => `<div class="segmented" role="group" aria-label="Stratégia">
  <button type="button" class="seg ${!current ? "on" : ""}" ${attr}="">Minden stratégia</button>
  ${Object.entries(STRAT).map(([id, [, label, help]]) => `<button type="button" class="seg ${current === id ? "on" : ""}" ${attr}="${id}" title="${esc(help)}">${esc(label)}</button>`).join("")}${extra}</div>`;

function mlNote(s) {
  if (!s.ml) return "";
  return `<div class="muted small" title="Gépi tanulás árnyék-üzemben – nem dönt">ML: ${signed(s.ml.pred_r, 2, "R")} ${s.ml.pick ? "✓ választaná" : "✗ nem választaná"}</div>`;
}

// Hir-jellemzok (sentiment, arnyek-uzem): csak tajekoztat, a jelzest nem befolyasolja
function newsBadge(n) {
  if (!n) return "";
  const latest = (n.latest || []).slice(0, 3).map((a) => `• ${a.title}`).join("\n");
  if (n.material_neg_3d) {
    const m = n.material[0];
    return ` <span class="badge bad" title="Lényeges negatív hír (tájékoztató, nem dönt): ${esc(m.title)}\n${esc(latest)}">▼ hír</span>`;
  }
  if (!n.count_3d) return n.coverage ? ` <span class="muted small" title="3 napban nincs releváns hír">nincs hír</span>` : "";
  const tone = n.sent_mean_3d === null || Math.abs(n.sent_mean_3d) < 0.1 ? "" : n.sent_mean_3d > 0 ? "up" : "down";
  return ` <span class="muted small ${tone}" title="${esc(`3 napban ${n.count_3d} hír (${n.neg_3d} negatív, ${n.pos_3d} pozitív)${n.coverage ? "" : " – részleges lefedettség"}\n${latest}`)}">${n.count_3d} hír</span>`;
}

function verdict(s) {
  return verdictBase(s) + newsBadge(s.news);
}

function verdictBase(s) {
  if (s.qualifies) return s.kind === "setup" ? `<span class="badge watch">Figyeld</span>` : `<span class="badge go">Belépési jelzés</span>`;
  if (s.failed && s.failed.length) return `<span class="badge fail" title="${esc(s.failed.join(", "))}">Bukott: ${esc(s.failed.join(", "))}</span>`;
  return `<span class="badge fail">Pont a küszöb alatt</span>`;
}

function scorebar(v) {
  const w = Math.max(0, Math.min(100, v || 0));
  return `<span class="scorebar"><span class="track"><span class="fill" style="width:${w}%"></span></span><span class="num">${fmt(v, 0)}</span></span>`;
}

// ---------------------------------------------------------------------------
// Felso sav: rezsim, utolso gyertya, frissites
// ---------------------------------------------------------------------------
// "S&P 500 (SPY)" - a rezsim az atnezett index ETF-jebol szamol (config: entry.benchmark auto)
const regimeName = (r) => (r.indexes ? r.indexes.map((i) => i.symbol).join(" / ") : r.name ? `${r.name} (${r.benchmark})` : r.benchmark);
const regimeWord = (ok) => (ok === true ? "bikás" : ok === false ? "gyenge" : "nincs adat");

function renderTopbar() {
  if (!overview) return;
  const r = overview.regime;
  const el = $("#regime");
  const parts = (r.indexes || []).map((i) => `${i.symbol} ${regimeWord(i.ok)}`).join(" · ");
  if (!r.enabled) { el.className = "pill"; el.textContent = `${regimeName(r)}: szűrő kikapcsolva`; }
  else el.className = `pill ${r.ok === true ? "ok" : r.indexes && r.indexes.some((i) => i.ok === false) ? (r.indexes.every((i) => i.ok === false) ? "bad" : "warn") : "warn"}`;
  if (r.enabled) el.textContent = parts || regimeName(r);
  el.title = `Piaci rezsim papíronként: Nasdaq-100 tag → QQQ, csak S&P 500 tag → S&P 500 index (SPX), indexen kívüli papír → a saját trendje (záró > SMA200).\n${r.text}`;
  $("#lastbar").textContent = overview.last_bar ? `Utolsó gyertya: ${overview.last_bar}` : "";
}

function renderJob(job) {
  const bar = $("#jobbar");
  const btn = $("#refresh");
  if (!job || job.state === "idle") { bar.hidden = true; btn.disabled = false; return; }
  bar.hidden = false;
  bar.className = `jobbar ${job.state}`;
  bar.textContent = job.state === "running" ? `Frissítés folyamatban – ${job.message}` : job.message;
  btn.disabled = job.state === "running";
  if (job.state === "done") setTimeout(() => { bar.hidden = true; }, 4000);
}

async function startRefresh() {
  try {
    const res = await api("/api/refresh", { method: "POST", body: "{}" });
    renderJob(res.job);
    pollJob();
  } catch (e) { renderJob({ state: "error", message: e.message }); }
}

function pollJob() {
  clearTimeout(jobTimer);
  jobTimer = setTimeout(async () => {
    const job = await api("/api/job");
    renderJob(job);
    if (job.state === "running") pollJob();
    else { overview = null; route(); }
  }, 1200);
}

// ---------------------------------------------------------------------------
// "Legjobb belepo most": a legjobb long es short belepo friss arakkal (best.py)
// ---------------------------------------------------------------------------
let bestTimer = null;

async function startBest() {
  try {
    const res = await api("/api/best", { method: "POST", body: "{}" });
    renderJob(res.job);
    if (!res.started) return;
    clearTimeout(bestTimer);
    const poll = async () => {
      const d = await api("/api/best");
      renderJob(d.job);
      if (d.job.state === "running") { bestTimer = setTimeout(poll, 1500); return; }
      if (d.result) showBest(d.result);
    };
    bestTimer = setTimeout(poll, 1500);
  } catch (e) { renderJob({ state: "error", message: e.message }); }
}

function showBest(r) {
  const dlg = $("#best-dialog");
  const when = new Date(r.computed_at).toLocaleString("hu-HU", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  dlg.innerHTML = `<div class="best-head"><h2>Legjobb belépő most</h2><button class="btn icon" type="button" id="best-close" aria-label="Bezárás">✕</button></div>
    <p class="muted small">${esc(r.label)} · ${r.evaluated} / ${r.symbols} papír elemezve · utolsó gyertya: ${esc(r.last_bar || "–")} · ${esc(when)}
      · a lista a <a href="#/settings">Beállításokban</a> választható</p>
    ${r.notes.map((n) => `<div class="callout">${esc(n)}</div>`).join("")}
    <div class="best-grid">${bestSide("long", r.long)}${bestSide("short", r.short)}</div>
    <p class="muted small">Nem befektetési tanács. A program semmit nem vásárol – csak megmutatja.</p>`;
  dlg.querySelector("#best-close").addEventListener("click", () => dlg.close());
  dlg.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => dlg.close()));
  if (!dlg.open) dlg.showModal();
}

function bestPlan(side, d) {
  const p = d.plan;
  const isShort = side === "short";
  const rows = [
    [isShort ? "Eladás (belépő)" : "Belépő", `${fmt(p.entry)} $ <span class="muted small">${isShort ? "ennél olcsóbban ne" : "limit"}: ${fmt(p.entry_limit)} $</span>`],
    ["Stop", `${fmt(p.stop)} $ <span class="muted small">(${isShort ? "+" : "−"}${fmt(p.stop_pct, 1)}%)</span>`],
    ["Cél", `${fmt(p.target)} $ <span class="muted small">(${isShort ? "−" : "+"}${fmt(p.target_pct, 1)}%)</span>`],
    ["Hozam/kockázat", `1 : ${fmt(p.risk_reward)}`],
    ["Méret", `${p.shares} db · kockázat ${fmt(p.risk_amount, 0)} $`],
  ];
  return `<table class="best-plan">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</table>`;
}

function bestSide(side, s) {
  const title = side === "long" ? "Long (vétel)" : `Short (eladás) <span class="badge warn" title="Nincs backtesttel igazolva, a paper trading nem küldi be">kísérleti</span>`;
  const b = s.best;
  const main = b ? `<div class="best-top"><a class="ticker" href="#/p/${esc(b.symbol)}">${esc(b.symbol)}</a>
        <span class="badge go">${esc(b.label)}</span> <span class="muted">${esc(b.pattern)} · pontszám ${fmt(b.score, 0)}</span></div>
      ${bestPlan(side, b)}
      ${b.ml ? `<div class="muted small">ML (árnyék-üzem, nem dönt): ${signed(b.ml.pred_r, 2, "R")} – ${b.ml.pick ? "a modell választaná" : "a modell nem választaná"}</div>` : ""}
      ${b.exit_lines && b.exit_lines.length ? `<details><summary>Kilépési terv</summary><ul class="checks">${b.exit_lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>` : ""}
      <details><summary>Szűrők</summary><ul class="checks">${b.checks.map((c) => `<li>${c.passed === false ? "✗" : c.passed ? "✓" : "?"} ${esc(c.name)}: ${esc(c.detail)}</li>`).join("")}</ul></details>`
    : `<div class="callout">Most nincs a szűrőkön átmenő ${side === "long" ? "long" : "short"} belépő ezen a listán.</div>`;
  const list = (items, label, extra) => (items.length ? `<h4>${label}</h4><ul class="best-list">${items.map((d) => `<li><a class="ticker" href="#/p/${esc(d.symbol)}">${esc(d.symbol)}</a>
      <span class="muted small">${esc(d.label)} · ${esc(d.pattern)} · ${fmt(d.score, 0)} pont${extra(d)}</span></li>`).join("")}</ul>` : "");
  return `<section class="best-side"><h3>${title}</h3>${main}
    ${list(s.others, "További belépők", (d) => ` · R/R 1:${fmt(d.plan.risk_reward)}`)}
    ${list(s.setups, "Figyelendő – még nem tört ki", (d) => ` · trigger ${fmt(d.trigger_level)} $`)}
    ${list(s.near, "Majdnem – szűrőn elbukott", (d) => ` · <span class="down">${esc((d.failed || []).join(", "))}</span>`)}
  </section>`;
}

// ---------------------------------------------------------------------------
// "Papir kiertekelese": egy beirt papir allapota MOST (quickcheck.py) - jelzesek, formalodo
// alakzatok "ha most neznenk" pontszammal, a Visszahuzas-strategiak szabalyai
// ---------------------------------------------------------------------------
let checkData = null;
let checkView = "live";
let checkSeq = 0;

const quickForm = (value = "", id = "quick") => `<form class="quick-form" data-quick autocomplete="off">
    <label for="${id}-symbol">Papír kiértékelése most</label>
    <input id="${id}-symbol" name="symbol" value="${esc(value)}" placeholder="pl. AAPL" maxlength="12" required
      spellcheck="false" autocapitalize="characters" list="quick-symbols">
    <button class="btn primary" type="submit">Kiértékelés</button>
  </form>`;

function bindQuickForms(root) {
  root.querySelectorAll("form[data-quick]").forEach((f) => f.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const symbol = f.elements.symbol.value.trim().toUpperCase();
    if (symbol) quickCheck(symbol);
  }));
}

async function quickCheck(symbol) {
  const dlg = $("#check-dialog");
  const seq = ++checkSeq;           // ha kozben ujat kernek, a regi valasz mar ne irja felul
  dlg.innerHTML = `<div class="best-head"><h2>${esc(symbol)} kiértékelése…</h2><button class="btn icon" type="button" data-close aria-label="Bezárás">✕</button></div>
    <div class="loading">Friss árfolyam letöltése és kiértékelés (néhány másodperc)…</div>`;
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  if (!dlg.open) dlg.showModal();
  try {
    const d = await api("/api/check", { method: "POST", body: JSON.stringify({ symbol }) });
    if (seq !== checkSeq) return;
    checkData = d;
    checkView = d.views.live ? "live" : "closed";
    renderCheck();
  } catch (e) {
    if (seq !== checkSeq) return;
    dlg.innerHTML = `<div class="best-head"><h2>${esc(symbol)}</h2><button class="btn icon" type="button" data-close aria-label="Bezárás">✕</button></div>
      <div class="callout">Nem sikerült kiértékelni: ${esc(e.message)}</div>${quickForm(symbol, "quick-dlg")}`;
    dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
    bindQuickForms(dlg);
  }
}

function checkStatus(v, live) {
  const sig = v.signals;
  if (sig.some((s) => s.qualifies && s.kind !== "setup")) return ["go", "Belépési jelzés", "minden szűrő ✔, a pont a küszöb felett"];
  const pb = v.pullback.find((p) => p.passed && p.enabled && p.in_universe);
  if (pb) return ["go", live ? "Visszahúzás – ha most zárna" : "Visszahúzás-jelzés", `${pb.label}: vételi limit ${fmt(pb.limit)} $`];
  if (sig.some((s) => s.qualifies)) return ["watch", "Figyeld – kitörés-közeli", "előkészület: vétel csak a trigger feletti záró után"];
  if (v.forming.some((s) => s.would_pass)) return ["watch", "Formálódik", "egy alakzat most átmenne a szűrőkön, de még nem jelez"];
  if (sig.length) return ["fail", "Helyzet van, de elbukik", "lásd a szűrőket lent"];
  if (v.forming.length) return ["fail", "Formálódik – gyenge", "az alakuló alakzat most elbukna a szűrőkön"];
  return ["fail", "Nincs belépési helyzet", "nincs kitörés, visszateszt vagy bikás alakuló alakzat"];
}

function renderCheck() {
  const d = checkData;
  const dlg = $("#check-dialog");
  const live = checkView === "live" && d.views.live;
  const v = live ? d.views.live : d.views.closed;
  const all = v.signals.concat(v.forming);
  const top = all.slice().sort((a, b) => b.score - a.score)[0];
  const [cls, status, statusSub] = checkStatus(v, live);
  const pb = v.pullback.find((p) => p.strategy === "pullback") || v.pullback[0];
  const f = v.forecast;
  const when = new Date(d.computed_at).toLocaleString("hu-HU", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const toggle = d.views.live ? `<div class="segmented" role="group" aria-label="Nézet">
      <button type="button" class="seg ${live ? "on" : ""}" data-view="live" title="A mai, még alakuló gyertyával – mintha az ár most zárna">Élő (most)</button>
      <button type="button" class="seg ${live ? "" : "on"}" data-view="closed" title="Az utolsó lezárt nap – ezt értékeli a monitor">Utolsó lezárt nap (${esc(d.views.closed.as_of)})</button></div>` : "";

  dlg.innerHTML = `<div class="best-head">
      <h2><a class="ticker" href="#/p/${esc(d.symbol)}">${esc(d.symbol)}</a>
        <span class="price">${fmt(v.price)} $</span> <span class="${v.change_pct > 0 ? "up" : v.change_pct < 0 ? "down" : ""}">${pct(v.change_pct, 2)}</span></h2>
      <button class="btn icon" type="button" data-close aria-label="Bezárás">✕</button></div>
    <p class="muted small">${live ? "élő ár (a mai gyertya még alakul)" : `lezárt nap: ${esc(v.as_of)}`} · kiértékelve ${esc(when)}
      ${d.watched ? " · figyelt papír" : " · nincs a figyelt listán"}${d.index ? ` · ${esc(d.index)}` : ""}
      · gyorsjelentés: ${esc(d.earnings_date || "nem ismert")}</p>
    <div class="check-bar">${toggle}${quickForm("", "quick-dlg")}</div>
    ${d.notes.map((n) => `<div class="callout">${esc(n)}</div>`).join("")}
    <div class="kpis check-kpis">
      <div class="kpi"><div class="label">Állapot</div><div class="value"><span class="badge ${cls}">${esc(status)}</span></div><div class="sub">${esc(statusSub)}</div></div>
      <div class="kpi"><div class="label">Kitörés-pontszám most</div><div class="value">${top ? `${fmt(top.score, 0)}<span class="muted small"> / 100</span>` : "–"}</div>
        <div class="sub">${top ? `${esc(top.pattern)} · ${top.hypothetical ? "formálódó" : esc((KIND[top.kind] || {}).text || top.kind)} · ` : "nincs bikás alakzat · "}küszöb ${fmt(d.min_score, 0)}</div></div>
      <div class="kpi"><div class="label">Visszahúzás (RSI2)</div><div class="value">${pb && pb.rsi2 != null ? fmt(pb.rsi2, 1) : "–"}</div>
        <div class="sub">${pb && pb.rsi2 != null ? `jelzés ${fmt(pb.rsi2_max, 0)} alatt · ${pb.met}/${pb.total} szabály teljesül` : esc((pb && pb.error) || "–")}</div></div>
      <div class="kpi"><div class="label">Irányjelzés</div><div class="value ${f.score > .15 ? "up" : f.score < -.15 ? "down" : ""}" style="font-size:19px">${esc(f.label)}</div>
        <div class="sub">kompozit ${signed(f.score * 100, 0)} · RSI ${fmt(v.rsi, 0)} · ADX ${fmt(v.adx, 0)} · vol. ${fmt(v.volume_ratio, 2)}×</div></div>
    </div>

    <h3 class="check-h">Belépési jelzések most <span class="muted small">– amit a monitor is jelezne</span></h3>
    ${v.signals.length ? `<div class="check-grid">${v.signals.map(checkCard).join("")}</div>`
      : `<div class="callout">Nincs kitörés, visszateszt vagy kitörés-közeli (≤1 ATR) alakzat.</div>`}

    <h3 class="check-h">Formálódó jelzések <span class="muted small">– ha most néznénk: ugyanazok a szűrők és pontozás, de ez még NEM jelzés</span></h3>
    ${v.forming.length ? `<div class="check-grid">${v.forming.map(checkCard).join("")}</div>`
      : `<div class="callout">Nincs a triggertől távolabb alakuló vagy ma kitört, még meg nem erősített bikás alakzat.</div>`}

    <h3 class="check-h">Visszahúzás-stratégiák <span class="muted small">– a jelzés a záron dől el${live ? "; most: ha így zárna" : ""}</span></h3>
    ${pullbackTable(v.pullback, live)}

    <h3 class="check-h">Momentum (12-1) <span class="muted small">– havi rotáció: a legjobb ${v.momentum.top_n} papír, megtartás a legjobb ${v.momentum.buffer} között</span></h3>
    ${momentumRow(v.momentum)}

    ${v.patterns.length ? `<h3 class="check-h">Minden aktív alakzat</h3>${checkPatterns(v.patterns)}` : ""}
    <details class="check-more"><summary>Irányjelzés összetevői</summary><div class="parts">${f.components.map((c) => `
      <div class="part"><span>${esc(c.name)}</span><span class="num ${c.score > 0 ? "up" : c.score < 0 ? "down" : ""}">${signed(c.score, 2)}</span><span class="muted">${esc(c.reason)}</span></div>`).join("")}</div></details>
    <p class="muted small"><a href="#/p/${esc(d.symbol)}">Részletes oldal charttal és hírekkel →</a> · Nem befektetési tanács – a program semmit nem vásárol.</p>`;

  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  dlg.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => dlg.close()));
  dlg.querySelectorAll("button[data-view]").forEach((b) => b.addEventListener("click", () => { checkView = b.dataset.view; renderCheck(); }));
  bindQuickForms(dlg);
}

// Hol tart az alakzat a jelzeshez kepest
function checkStage(s) {
  const trig = `${fmt(s.trigger_level)} $`;
  const dist = s.distance_atr != null ? `${fmt(s.distance_atr, 1)} ATR (${fmt(s.distance_pct, 1)}%)` : `${fmt(s.distance_pct, 1)}%`;
  if (!s.hypothetical) {
    return { breakout: `Megerősített kitörés: két egymást követő napon a ${trig} trigger fölött zárt.`,
      retest: `Visszateszt: a kitörés után visszajött a ${trig} szinthez, megtartotta és felfelé fordult.`,
      setup: `Kitörés-közeli: ${dist} a ${trig} triggertől – vétel csak a trigger fölötti záró után.` }[s.kind] || "";
  }
  if (s.kind === "breakout") return `MA tört ki a ${trig} trigger fölé. Ha a következő záró is fölötte marad, megerősített kitörés lesz belőle – ez még nem jelzés.`;
  return `Alakul: ${dist} a ${trig} triggertől. ${fmt(checkData.setup_max_distance_atr, 0)} ATR-en belül előkészület, a trigger fölötti záró után jelzés lesz belőle.`;
}

function checkCard(s) {
  const p = s.plan;
  const k = KIND[s.kind] || { text: s.kind, cls: "fail" };
  const badge = s.hypothetical
    ? `<span class="badge fail" title="Ugyanazzal a szűrő- és pontrendszerrel értékelve, mintha most jelezne">formálódó · ${s.kind === "breakout" ? "kitörés ma" : "alakul"}</span>`
    : `<span class="badge ${k.cls}">${k.text}</span>`;
  const verdictHtml = !s.hypothetical ? verdictBase(s)
    : s.would_pass ? `<span class="badge watch">Most átmenne a szűrőkön</span>`
    : s.failed.length ? `<span class="badge fail" title="${esc(s.failed.join(", "))}">Most bukna: ${esc(s.failed.join(", "))}</span>`
    : `<span class="badge fail">Pont a küszöb alatt</span>`;
  const checks = s.checks.map((c) => {
    const [mc, mark] = c.passed === true ? ["ok", "✓"] : c.passed === false ? ["no", "✗"] : ["unk", "?"];
    return `<li><span class="mark ${mc}">${mark}</span><span><strong>${esc(c.name)}</strong> <span class="muted">${esc(c.detail)}</span></span></li>`;
  }).join("");
  const parts = s.score_parts.map((x) => `
    <div class="part"><span>${esc(x.name)}</span><span class="track"><span class="fill" style="width:${(x.points / x.max) * 100}%"></span></span>
    <span class="muted small">${fmt(x.points, 0)}/${x.max} · ${esc(x.reason)}</span></div>`).join("");
  const waiting = s.kind === "setup";
  return `<div class="signal ${s.qualifies && !waiting ? "go" : ""}">
    <div class="signal-head"><strong>${esc(s.pattern)}</strong>${badge}</div>
    <div>${verdictHtml} ${scorebar(s.score)}${mlNote(s)}</div>
    <div class="muted small" style="margin-top:6px">${esc(checkStage(s))}</div>
    <div class="plan">
      <span class="k">${waiting ? "Trigger (vétel felette)" : "Belépő / limit"}</span><span class="v">${fmt(p.entry)}${waiting ? "" : ` / ${fmt(p.entry_limit)}`}</span><span class="p">${s.distance_pct != null && waiting ? `+${fmt(s.distance_pct, 1)}%` : ""}</span>
      <span class="k">Stop</span><span class="v down">${fmt(p.stop)}</span><span class="p">−${fmt(p.stop_pct, 1)}%</span>
      <span class="k">Cél</span><span class="v up">${fmt(p.target)}</span><span class="p">+${fmt(p.target_pct, 1)}%</span>
      <span class="k">Hozam / kockázat</span><span class="v">1 : ${fmt(p.risk_reward)}</span><span class="p"></span>
      <span class="k">Méret</span><span class="v">${p.shares} db</span><span class="p">kockázat ${fmt(p.risk_amount, 0)} $</span>
    </div>
    <details ${s.passed ? "" : "open"}><summary>Szűrők</summary><ul class="checks">${checks}</ul></details>
    <details><summary>Pontszám összetevői</summary><div class="parts">${parts}</div></details>
  </div>`;
}

function momentumRow(m) {
  if (!m) return "";
  const why = !m.enabled ? " (a stratégia ki van kapcsolva)" : !m.in_universe ? " (ezt a papírt nem figyeli – nincs a Nasdaq-100 / S&P 500 / watchlist listán)" : "";
  if (m.error) return `<div class="callout">Nem rangsorolható: ${esc(m.error)}${esc(why)}</div>`;
  const state = m.rank == null ? `<span class="muted">${esc(m.note || "")}</span>`
    : m.would_buy ? `<span class="badge go">A legjobb ${m.top_n} között – a rebalanszon bekerülne</span>`
    : m.would_keep ? `<span class="badge watch">A legjobb ${m.buffer} között – ha tartaná, megtartaná</span>`
    : `<span class="badge fail">Nincs a legjobbak között</span>`;
  return `<div class="callout">12-1 havi hozam most: <strong class="${m.score_pct > 0 ? "up" : "down"}">${signed(m.score_pct, 1, "%")}</strong>
    ${m.rank != null ? ` · rang: <strong>${m.rank}.</strong> / ${m.universe} <span class="muted small">(a többi papír a ${esc(m.ranking_as_of)}-i rangsorból)</span>` : ""}
    ${m.held ? ` · <strong>a stratégia most tartja</strong>` : ""}<div style="margin-top:6px">${state}${esc(why)}</div></div>`;
}

function pullbackTable(list, live) {
  const state = (r) => {
    if (r.error) return `<span class="muted">${esc(r.error)}</span>`;
    const why = !r.enabled ? ` <span class="muted small">(a stratégia ki van kapcsolva)</span>`
      : !r.in_universe ? ` <span class="muted small">(ezt a papírt nem figyeli)</span>` : "";
    if (r.passed) return `<span class="badge go">${live ? "Ha most zárna: jelzés" : "Jelzés a záron"}</span>${why}`;
    return `<span class="badge fail" title="${esc(r.failed.join(", "))}">Nincs: ${esc(r.failed.join(", "))}</span>${why}`;
  };
  return `<div class="table-wrap"><table>
    <thead><tr><th>Stratégia</th><th class="num">RSI(2) most</th><th class="num">Szabályok</th><th>Állapot</th><th class="num">Limit / stop</th></tr></thead>
    <tbody>${list.map((r) => `<tr>
      <td>${strategyBadge(r.strategy)}</td>
      <td class="num">${r.rsi2 != null ? `${fmt(r.rsi2, 1)} <span class="muted small">/ &lt;${fmt(r.rsi2_max, 0)}</span>` : "–"}</td>
      <td class="num">${r.total ? `${r.met}/${r.total}` : "–"}</td>
      <td>${state(r)}${r.checks ? `<details><summary class="small">részletek</summary><ul class="checks">${r.checks.map((c) =>
        `<li><span class="mark ${c.passed === true ? "ok" : c.passed === false ? "no" : "unk"}">${c.passed === true ? "✓" : c.passed === false ? "✗" : "?"}</span><span><strong>${esc(c.name)}</strong> <span class="muted">${esc(c.detail)}</span></span></li>`).join("")}</ul></details>` : ""}</td>
      <td class="num">${r.limit != null ? `${fmt(r.limit)} <span class="muted small">(−${fmt(r.limit_pct, 0)}%)</span> / ${fmt(r.stop)}` : "–"}</td></tr>`).join("")}</tbody></table></div>`;
}

function checkPatterns(list) {
  const status = (m) => (m.status === "triggered" ? `kitört${m.trigger_date ? ` ${esc(String(m.trigger_date).slice(0, 10))}` : ""}` : "alakul");
  return `<div class="table-wrap"><table>
    <thead><tr><th>Alakzat</th><th>Irány</th><th>Állapot</th><th class="num">Trigger</th><th class="num">Távolság</th><th class="num">Cél</th><th class="num">Minőség</th></tr></thead>
    <tbody>${list.map((m) => `<tr>
      <td>${esc(m.name)}</td>
      <td>${m.direction === "bullish" ? `<span class="up">▲ bikás</span>` : m.direction === "bearish" ? `<span class="down">▼ medvés</span>` : `<span class="muted">● semleges</span>`}</td>
      <td>${status(m)}</td>
      <td class="num">${fmt(m.trigger)}</td>
      <td class="num">${m.distance_atr != null ? `${signed(m.distance_atr, 1)} ATR <span class="muted small">${signed(m.distance_pct, 1, "%")}</span>` : "–"}</td>
      <td class="num">${fmt(m.target)}</td>
      <td class="num">${fmt(m.quality * 100, 0)}%</td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Attekintes
// ---------------------------------------------------------------------------
async function loadOverview(force = false) {
  if (!overview || force) overview = await api("/api/overview");
  patternStats = overview.pattern_stats || patternStats;
  renderTopbar();
  if (overview.job && overview.job.state === "running") { renderJob(overview.job); pollJob(); }
  return overview;
}

async function pageOverview() {
  const o = await loadOverview(true);
  const signals = o.candidates.filter((c) => c.qualifies && c.kind !== "setup");
  const watch = o.candidates.filter((c) => c.qualifies && c.kind === "setup");
  const [pos, own] = await Promise.all([api("/api/positions"), api("/api/holdings")]);
  const open = pos.positions.filter((p) => p.status === "open" || p.status === "pending");
  const ownBad = own.holdings.filter((h) => h.verdict === "bad").length;
  const ownWarn = own.holdings.filter((h) => h.verdict === "warn").length;
  const ix = o.index || { members: [], result: null, enabled: false };
  const ixAll = ((ix.result && ix.result.signals) || []).map((s) => (s.muted ? { ...s, qualifies: false, failed: ["előkészület – elnémítva az indexpapírokon"] } : s));
  const ixSignals = ixAll.filter((c) => c.qualifies && c.kind !== "setup");

  const regimeText = !o.regime.enabled ? "kikapcsolva" : (o.regime.indexes || []).map((i) => `${i.symbol} ${regimeWord(i.ok)}`).join(" · ") || "Nincs adat";
  view.innerHTML = `
    <section class="card quick-card">
      ${quickForm()}
      <span class="muted small">Friss árral bármely papírra (a figyelt listán kívül is): belépési jelzések, formálódó alakzatok
        és a pontszám, amit most kapna.</span>
      <datalist id="quick-symbols">${o.rows.map((r) => `<option value="${esc(r.symbol)}">`).join("")}</datalist>
    </section>
    <div class="kpis">
      <div class="kpi"><div class="label">Piaci rezsim (${esc(regimeName(o.regime))})</div>
        <div class="value ${o.regime.ok === true ? "up" : o.regime.ok === false ? "down" : ""}" style="font-size:17px">${regimeText}</div>
        <div class="sub">${esc(o.regime.text)}</div></div>
      <div class="kpi"><div class="label">Belépési jelzés ma</div><div class="value">${signals.length + ixSignals.length}</div>
        <div class="sub">minden szűrő ✔, pont ≥ ${fmt(o.settings.min_score, 0)}${ix.enabled ? ` · ebből indexből: ${ixSignals.length}` : ""}</div></div>
      <div class="kpi"><div class="label">Figyelendő</div><div class="value">${watch.length}</div>
        <div class="sub">≤1 ATR-re a kitöréstől</div></div>
      <div class="kpi"><div class="label">Követett trade-ek</div><div class="value">${open.length}</div>
        <div class="sub">${pos.stats.n ? `lezárt: ${pos.stats.n} · nyerő ${fmt(pos.stats.win * 100, 0)}% · ${signed(pos.stats.ev, 2, "R")}` : "még nincs lezárt trade"}</div></div>
      <a class="kpi" href="#/holdings" style="color:inherit;text-decoration:none"><div class="label">Saját pozíciók</div>
        <div class="value ${own.totals.pnl > 0 ? "up" : own.totals.pnl < 0 ? "down" : ""}">${own.holdings.length ? signed(own.totals.pnl, 0, " $") : "0"}</div>
        <div class="sub">${own.holdings.length ? `${own.holdings.length} nyitott${ownBad ? ` · <span class="down">${ownBad} kockázatos</span>` : ""}${ownWarn ? ` · ${ownWarn} figyelendő` : ""}` : "vedd fel a meglévő pozícióidat"}</div></a>
    </div>

    <section class="card">
      <h2>Belépési jelöltek <span class="muted">– ma kiértékelt helyzetek, a kiesettek okaival</span></h2>
      ${o.candidates.length ? candidatesTable(o.candidates) : `<div class="card-body empty">Ma nincs kitörés, visszateszt vagy kitörés-közeli minta a figyelt papírokon.</div>`}
    </section>

    ${indexCard(ix, ixAll)}

    <section class="card">
      <h2>Figyelt papírok <span class="muted">– ${o.rows.length} db</span></h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Ticker</th><th class="num">Záró</th><th class="num">Napi</th><th>Irány</th><th>Legerősebb alakzat</th><th>Belépés</th></tr></thead>
        <tbody>${o.rows.map(watchRow).join("")}</tbody>
      </table></div>
    </section>`;
  bindRowLinks();
  bindQuickForms(view);
  const ixBtn = $("#index-scan");
  if (ixBtn) ixBtn.addEventListener("click", async () => {
    ixBtn.disabled = true;
    try {
      const res = await api("/api/scan/index", { method: "POST", body: "{}" });
      renderJob(res.job);
      pollJob();
    } catch (e) { renderJob({ state: "error", message: e.message }); ixBtn.disabled = false; }
  });
  const ixToggle = $("#index-all");
  if (ixToggle) ixToggle.addEventListener("change", () => {
    try { localStorage.setItem("sw-index-all", ixToggle.checked ? "1" : ""); } catch (_) { /* nem baj */ }
    pageOverview();
  });
}

function indexCard(ix, all) {
  const active = ix.members.filter((m) => m.enabled);
  if (!ix.enabled) {
    return `<section class="card"><h2>Index-jelöltek</h2><div class="card-body muted">
      A teljes <strong>Nasdaq-100 (QQQ)</strong> és <strong>S&amp;P 500</strong> átnézése a <a href="#/settings">Beállítások</a> → „Átnézett papírok” résznél kapcsolható be.
      Zárás után a monitor ezeket is átnézi, és ezekre is küld belépési jelzést.</div></section>`;
  }
  let showAll = false;
  try { showAll = localStorage.getItem("sw-index-all") === "1"; } catch (_) { /* privat mod */ }
  const r = ix.result;
  const list = showAll ? all : all.filter((s) => s.qualifies);
  const when = r ? new Date(r.computed_at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }) : null;
  return `<section class="card">
    <h2>Index-jelöltek <span class="muted">– ${active.map((m) => `${esc(m.label)} (${m.count || "?"})`).join(" + ")}${r ? ` · ${r.analysed} papír elemezve, ${esc(when)} · gyertya: ${esc(r.last_bar || "–")}` : " · még nem futott"}</span>
      <span style="margin-left:auto;display:inline-flex;gap:12px;align-items:center;font-weight:400">
        <label class="small muted"><input type="checkbox" id="index-all" ${showAll ? "checked" : ""}> elbukottak is</label>
        <button class="btn" type="button" id="index-scan" title="Most átnézi a bekapcsolt indexeket (értesítést nem küld – azt a monitor teszi zárás után)">Szkennelés most</button></span></h2>
    ${active.some((m) => m.error) ? `<div class="card-body" style="color:var(--warn)">${active.filter((m) => m.error).map((m) => `${esc(m.label)}: a lista frissítése nem sikerült (${esc(m.error)}) – a legutóbb mentettel dolgozik.`).join("<br>")}</div>` : ""}
    ${!r ? `<div class="card-body empty">Az első szkenneléshez nyomd meg a „Szkennelés most” gombot (az első letöltés ~1–2 perc), vagy várd meg a zárás utáni futást.</div>`
      : list.length ? candidatesTable(list)
      : `<div class="card-body empty">Az indexpapírokon ma nincs minden szűrőn átjutó belépési jelzés.${all.length ? ` (${all.length} jelölt bukott el – „elbukottak is”)` : ""}</div>`}
  </section>`;
}

function candidatesTable(list) {
  return `<div class="table-wrap"><table>
    <thead><tr><th>Ticker</th><th>Időpont</th><th>Helyzet</th><th>Alakzat</th><th>Pont</th><th class="num">Záró</th>
      <th class="num">Belépő / limit</th><th class="num">Stop</th><th class="num">½ eladás</th><th class="num">Cél</th>
      <th class="num">R/R</th><th class="num">Db</th><th>Eredmény</th></tr></thead>
    <tbody>${list.map((s) => {
      const k = KIND[s.kind] || { text: s.kind, cls: "fail" };
      const p = s.plan;
      return `<tr class="clickable" data-href="#/p/${esc(s.symbol)}">
        <td class="ticker">${esc(s.symbol)}</td>
        <td class="nowrap">${candidateTime(s)}</td>
        <td><span class="badge ${k.cls}">${k.text}</span></td>
        <td><span class="with-info">${esc(s.pattern)}${infoButton(s.pattern, "bullish", s.kind, statsFor(s.pattern, "bullish"))}</span></td>
        <td>${scorebar(s.score)}</td>
        <td class="num">${fmt(s.price)}</td>
        <td class="num">${fmt(p.entry)}${s.kind === "setup" ? "" : ` / ${fmt(p.entry_limit)}`}</td>
        <td class="num">${fmt(p.stop)} <span class="muted small">−${fmt(p.stop_pct, 1)}%</span></td>
        <td class="num">${p.partial_price ? fmt(p.partial_price) : "–"}</td>
        <td class="num">${fmt(p.target)} <span class="muted small">+${fmt(p.target_pct, 1)}%</span></td>
        <td class="num">${fmt(p.risk_reward)}</td>
        <td class="num">${p.shares}</td>
        <td>${verdict(s)}${mlNote(s)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

// A jelolt pontos idopontja: a gyertya (zaro) napja es a kiertekeles datuma, ideje (helyi ido, masodpercre).
function candidateTime(s) {
  const bar = s.bar_time ? new Date(s.bar_time).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }) : s.as_of;
  if (!s.evaluated_at) return `<span class="small">gyertya: ${esc(bar || "–")}</span>`;
  const ev = new Date(s.evaluated_at);
  const full = ev.toLocaleString("hu-HU", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `<span title="Kiértékelve: ${esc(full)} (UTC: ${esc(s.evaluated_at)})">${esc(full)}</span>
    <div class="muted small">gyertya: ${esc(bar || "–")}</div>`;
}

function watchRow(r) {
  if (r.missing) return `<tr><td class="ticker">${esc(r.symbol)}</td><td colspan="5" class="muted">nincs adat – futtass frissítést</td></tr>`;
  const dir = r.direction === "bullish" ? `<span class="up">▲ ${esc(r.label)}</span>` : r.direction === "bearish" ? `<span class="down">▼ ${esc(r.label)}</span>` : `<span class="muted">● ${esc(r.label)}</span>`;
  const pat = r.pattern ? `${esc(r.pattern)} <span class="muted small">${r.pattern_status === "triggered" ? "kitört" : "alakul"}</span>` : `<span class="muted">–</span>`;
  let ent = `<span class="muted">–</span>`;
  if (r.own_position) ent = `<span class="badge watch">Saját pozíció</span>`;
  else if (r.active_position) ent = `<span class="badge warn">Nyitott trade</span>`;
  else if (r.entry) ent = verdict(r.entry);
  return `<tr class="clickable" data-href="#/p/${esc(r.symbol)}">
    <td class="ticker">${esc(r.symbol)}</td>
    <td class="num">${fmt(r.price)}</td>
    <td class="num ${r.change_pct > 0 ? "up" : r.change_pct < 0 ? "down" : ""}">${pct(r.change_pct, 2)}</td>
    <td>${dir}</td><td>${pat}</td><td>${ent}</td></tr>`;
}

function bindRowLinks() {
  view.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}

// ---------------------------------------------------------------------------
// Papir reszletei
// ---------------------------------------------------------------------------
async function pageSymbol(symbol) {
  view.innerHTML = `<div class="loading">${esc(symbol)} betöltése…</div>`;
  await loadOverview();
  const d = await api(`/api/symbol/${encodeURIComponent(symbol)}`);
  patternStats = d.pattern_stats || patternStats;
  const last = d.candles[d.candles.length - 1];
  const prev = d.candles[d.candles.length - 2];
  const change = prev ? (last.c / prev.c - 1) * 100 : null;
  const ind = d.indicators;
  const signals = d.signals.slice().sort((a, b) => (b.qualifies - a.qualifies) || (b.score - a.score));

  view.innerHTML = `
    <div class="page-head">
      <a href="#/" class="muted">← Áttekintés</a>
      <h1>${esc(d.symbol)}</h1>
      <span class="price">${fmt(last.c)} $</span>
      <span class="${change > 0 ? "up" : "down"}">${pct(change, 2)}</span>
      <span class="muted small">${esc(last.t)}</span>
      ${d.forecast ? `<span class="pill plain ${d.forecast.score > .15 ? "ok" : d.forecast.score < -.15 ? "bad" : ""}">${esc(d.forecast.label)}</span>` : ""}
    </div>
    <div class="grid-2">
      <div>
        <section class="card chart-card">
          <h2>Napi chart <span class="muted">– utolsó ${d.candles.length} gyertya</span></h2>
          <div class="chart-legend" id="legend"></div>
          <div class="card-body"><svg id="chart" role="img" aria-label="${esc(d.symbol)} árfolyam chart"></svg></div>
        </section>
        ${d.positions.length ? `<section class="card"><h2>Követett trade-ek ezen a papíron</h2>${positionsTable(d.positions, false)}</section>` : ""}
        <section class="card"><h2>Felismert alakzatok</h2>${patternsTable(d.patterns)}</section>
      </div>
      <aside>
        <section class="card" id="news-card"><h2>Hírek <span class="muted">– betöltés…</span></h2></section>
        ${d.holdings.length ? `<section class="card"><h2>Saját pozíció</h2><div class="card-body">${d.holdings.map((h) => holdingCard(h, false)).join("")}</div></section>` : ""}
        <section class="card"><h2>Belépési helyzet</h2><div class="card-body">
          ${signals.length ? signals.map(signalCard).join("") : `<div class="muted">Ma nincs belépési helyzet ezen a papíron (nincs friss kitörés és nincs kitörés-közeli alakzat).</div>`}
        </div></section>
        <section class="card"><h2>Mutatók</h2><div class="card-body">
          <dl class="stat-list">
            <dt>RSI (14)</dt><dd>${fmt(ind.rsi, 0)}</dd>
            <dt>ADX</dt><dd>${fmt(ind.adx, 0)}</dd>
            <dt>ATR</dt><dd>${fmt(ind.atr)} $ <span class="muted small">(${fmt(ind.atr_pct, 1)}%)</span></dd>
            <dt>Volumen / 20 napos átlag</dt><dd>${fmt(ind.vol_ratio, 2)}×</dd>
            <dt>EMA50 meredekség (20 nap)</dt><dd>${signed(ind.ema50_slope, 1, "%")}</dd>
            <dt>EMA20 / 50 / 200</dt><dd>${fmt(last.ema20, 0)} / ${fmt(last.ema50, 0)} / ${fmt(last.ema200, 0)}</dd>
          </dl>
          ${d.forecast ? `<details><summary>Irányjelzés összetevői</summary><div class="parts">${d.forecast.components.map((c) => `
            <div class="part"><span>${esc(c.name)}</span><span class="num ${c.score > 0 ? "up" : c.score < 0 ? "down" : ""}">${signed(c.score, 2)}</span><span class="muted">${esc(c.reason)}</span></div>`).join("")}</div></details>` : ""}
        </div></section>
      </aside>
    </div>`;

  setupChart(d, signals);
  bindHoldingActions(d.holdings, () => pageSymbol(symbol));
  bindBrokerButtons();
}

function signalCard(s) {
  const p = s.plan;
  const k = KIND[s.kind] || { text: s.kind, cls: "fail" };
  const checks = s.checks.map((c) => {
    const [cls, mark] = c.passed === true ? ["ok", "✓"] : c.passed === false ? ["no", "✗"] : ["unk", "?"];
    return `<li><span class="mark ${cls}">${mark}</span><span><strong>${esc(c.name)}</strong> <span class="muted">${esc(c.detail)}</span></span></li>`;
  }).join("");
  const parts = s.score_parts.map((x) => `
    <div class="part"><span>${esc(x.name)}</span><span class="track"><span class="fill" style="width:${(x.points / x.max) * 100}%"></span></span>
    <span class="muted small">${fmt(x.points, 0)}/${x.max} · ${esc(x.reason)}</span></div>`).join("");
  const half = Math.floor(p.shares / 2);
  return `<div class="signal ${s.qualifies && s.kind !== "setup" ? "go" : ""}">
    <div class="signal-head"><span class="with-info"><strong>${esc(s.pattern)}</strong>${infoButton(s.pattern, "bullish", s.kind, statsFor(s.pattern, "bullish"))}</span><span class="badge ${k.cls}">${k.text}</span></div>
    <div>${verdict(s)} ${scorebar(s.score)}${mlNote(s)}</div>
    <div class="plan">
      <span class="k">${s.kind === "setup" ? "Vétel csak kitörés felett" : "Belépő (limit)"}</span><span class="v">${fmt(p.entry)}${s.kind === "setup" ? "" : ` / ${fmt(p.entry_limit)}`}</span><span class="p"></span>
      <span class="k">Stop</span><span class="v down">${fmt(p.stop)}</span><span class="p">−${fmt(p.stop_pct, 1)}%</span>
      ${p.partial_price ? `<span class="k">½ eladás (+1R)</span><span class="v">${fmt(p.partial_price)}</span><span class="p">+${fmt((p.partial_price / p.entry - 1) * 100, 1)}%</span>` : ""}
      <span class="k">Cél</span><span class="v up">${fmt(p.target)}</span><span class="p">+${fmt(p.target_pct, 1)}%</span>
      <span class="k">Hozam / kockázat</span><span class="v">1 : ${fmt(p.risk_reward)}</span><span class="p"></span>
      <span class="k">Méret</span><span class="v">${p.shares} db</span><span class="p">${fmt(p.position_value, 0)} $</span>
      <span class="k">Kockázat</span><span class="v">${fmt(p.risk_amount, 0)} $</span><span class="p"></span>
    </div>
    ${p.partial_price ? `<ol class="steps"><li>Fele (${half} db) eladás ${fmt(p.partial_price)} $-nál</li><li>Utána stop a belépőre (${fmt(p.entry)} $)</li><li>Maradék a célig: ${fmt(p.target)} $</li></ol>` : ""}
    <div class="muted small" style="margin-top:6px">Történeti: ${esc(s.history)}${s.earnings_date ? ` · Jelentés: ${esc(s.earnings_date)}` : ""}</div>
    ${s.qualifies && s.kind !== "setup" && overview && overview.settings.broker_configured ? `<div class="actions" style="margin-top:8px">${sendButton(s)}</div>` : ""}
    <details ${s.qualifies ? "" : "open"}><summary>Szűrők</summary><ul class="checks">${checks}</ul></details>
    <details><summary>Pontszám összetevői</summary><div class="parts">${parts}</div></details>
  </div>`;
}

function patternsTable(list) {
  if (!list.length) return `<div class="card-body empty">Nincs aktív alakzat.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Alakzat</th><th>Irány</th><th>Állapot</th><th class="num">Minőség</th>
    <th class="num">Trigger</th><th class="num">Cél</th><th class="num">Érvénytelenítés</th><th>Időszak</th></tr></thead><tbody>
    ${list.map((m) => `<tr><td><span class="with-info">${esc(m.name)}${infoButton(m.name, m.direction, "", statsFor(m.name, m.direction))}</span></td>
      <td>${m.direction === "bullish" ? `<span class="up">▲ bikás</span>` : m.direction === "bearish" ? `<span class="down">▼ medvés</span>` : "semleges"}</td>
      <td>${m.status === "triggered" ? `<span class="badge go">kitört ${esc(m.trigger_date || "")}</span>` : `<span class="badge watch">alakul</span>`}</td>
      <td class="num">${fmt(m.quality * 100, 0)}%</td><td class="num">${fmt(m.trigger)}</td><td class="num">${fmt(m.target)}</td>
      <td class="num">${fmt(m.invalidation)}</td><td class="muted small">${esc(m.start)} → ${esc(m.end)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// SVG chart
// ---------------------------------------------------------------------------
const chartState = { show: { ema20: true, ema50: true, ema200: true, levels: false, pattern: true, plan: true, holding: true }, sel: 0 };

function setupChart(d, signals) {
  const legend = $("#legend");
  const items = [
    ["ema20", "EMA20", "var(--ema20)"], ["ema50", "EMA50", "var(--ema50)"], ["ema200", "EMA200", "var(--ema200)"],
    ["pattern", "Alakzat", "var(--accent)"], ["plan", "Kereskedési terv", "var(--info)"], ["levels", "Support / rezisztencia", "var(--muted)"],
  ];
  if (d.holdings.length) items.splice(3, 0, ["holding", "Saját pozíció", "var(--warn)"]);
  legend.innerHTML = items.map(([key, label, color]) =>
    `<label><input type="checkbox" data-key="${key}" ${chartState.show[key] ? "checked" : ""}><span class="swatch" style="background:${color}"></span>${label}</label>`).join("")
    + (signals.length > 1 ? ` <label>Terv: <select id="plansel">${signals.map((s, i) => `<option value="${i}">${esc(s.pattern)} – ${esc((KIND[s.kind] || {}).text || s.kind)}</option>`).join("")}</select></label>` : "");
  legend.querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => { chartState.show[cb.dataset.key] = cb.checked; draw(); }));
  const sel = $("#plansel");
  chartState.sel = 0;
  if (sel) sel.addEventListener("change", () => { chartState.sel = Number(sel.value); draw(); });

  const svg = $("#chart");
  const draw = () => drawChart(svg, d, signals[chartState.sel]);
  draw();
  if (chartRO) chartRO.disconnect();
  chartRO = new ResizeObserver(() => draw());
  chartRO.observe(svg.parentElement);
  loadNews(d.symbol);
}

function drawChart(svg, d, signal) {
  const W = Math.max(320, svg.parentElement.clientWidth);
  const H = W < 600 ? 360 : 460;
  const pad = { l: 8, r: 64, t: 12, b: 24 };
  const volH = Math.round(H * 0.16);
  const priceH = H - pad.t - pad.b - volH - 8;
  const c = d.candles;
  const n = c.length;
  const step = (W - pad.l - pad.r) / n;
  const x = (i) => pad.l + step * i + step / 2;

  // az ar-tartomany: gyertyak + a lathato vizszintes vonalak
  let lo = Infinity, hi = -Infinity;
  c.forEach((k) => { lo = Math.min(lo, k.l); hi = Math.max(hi, k.h); });
  const hlines = [];
  if (chartState.show.plan && signal) {
    const p = signal.plan;
    hlines.push({ v: p.entry, label: "Belépő", color: "var(--info)" });
    hlines.push({ v: p.stop, label: "Stop", color: "var(--down)" });
    hlines.push({ v: p.target, label: "Cél", color: "var(--up)" });
    if (p.partial_price) hlines.push({ v: p.partial_price, label: "½ el", color: "var(--warn)", dash: true });
  }
  if (chartState.show.holding) {
    d.holdings.filter((h) => !h.missing).forEach((h) => {
      hlines.push({ v: h.entry_price, label: "Saját", color: "var(--warn)" });
      hlines.push({ v: h.stop != null ? h.stop : h.suggested_stop, label: h.stop != null ? "S.stop" : "Jav.stop", color: "var(--down)", dash: true });
    });
  }
  if (chartState.show.pattern && !signal) {
    const m = d.patterns[0];
    if (m) hlines.push({ v: m.trigger, label: "Trigger", color: "var(--accent)", dash: true });
  }
  hlines.forEach((h) => { lo = Math.min(lo, h.v); hi = Math.max(hi, h.v); });
  const span = hi - lo || 1;
  lo -= span * 0.04; hi += span * 0.04;
  const y = (v) => pad.t + (hi - v) / (hi - lo) * priceH;
  const volTop = pad.t + priceH + 8;
  const vmax = Math.max(...c.map((k) => k.v || 0)) || 1;
  const vy = (v) => volTop + volH - (v / vmax) * volH;

  const parts = [];
  // racs + ar-tengely
  const ticks = niceTicks(lo, hi, 6);
  parts.push(`<g class="grid">${ticks.map((t) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`);
  parts.push(`<g class="axis">${ticks.map((t) => `<text x="${W - pad.r + 6}" y="${y(t) + 4}">${fmt(t, t >= 100 ? 0 : 2)}</text>`).join("")}</g>`);
  // datum-tengely: honapok
  const months = [];
  c.forEach((k, i) => { if (i === 0 || k.t.slice(0, 7) !== c[i - 1].t.slice(0, 7)) months.push(i); });
  const every = Math.ceil(months.length / Math.max(2, Math.floor(W / 90)));
  parts.push(`<g class="axis">${months.filter((_, j) => j % every === 0 && _ > 0).map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${c[i].t.slice(0, 7)}</text>`).join("")}</g>`);

  // S/R szintek
  if (chartState.show.levels) {
    d.levels.filter((l) => l.price > lo && l.price < hi).forEach((l) => {
      parts.push(`<line class="level" x1="${pad.l}" x2="${W - pad.r}" y1="${y(l.price)}" y2="${y(l.price)}"><title>${esc(l.kind)} ${fmt(l.price)} (${fmt(l.score, 0)} pont)</title></line>`);
    });
  }

  // volumen
  parts.push(`<g>${c.map((k, i) => `<rect class="vol ${k.c >= k.o ? "up" : "down"}" x="${x(i) - step * 0.35}" y="${vy(k.v || 0)}" width="${Math.max(1, step * 0.7)}" height="${volTop + volH - vy(k.v || 0)}"/>`).join("")}</g>`);

  // gyertyak
  const bw = Math.max(1, step * 0.62);
  parts.push(`<g>${c.map((k, i) => {
    const cls = k.c >= k.o ? "up" : "down";
    const top = y(Math.max(k.o, k.c)), bot = y(Math.min(k.o, k.c));
    return `<g class="candle ${cls}"><line x1="${x(i)}" x2="${x(i)}" y1="${y(k.h)}" y2="${y(k.l)}"/><rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}"/></g>`;
  }).join("")}</g>`);

  // EMA-k
  ["ema20", "ema50", "ema200"].forEach((key) => {
    if (!chartState.show[key]) return;
    let dpath = "";
    c.forEach((k, i) => { if (k[key] != null) dpath += `${dpath ? "L" : "M"}${x(i).toFixed(1)},${y(k[key]).toFixed(1)}`; });
    parts.push(`<path class="ema ${key}" d="${dpath}"/>`);
  });

  // alakzat geometria
  if (chartState.show.pattern) {
    const index = new Map(c.map((k, i) => [k.t, i]));
    const shapes = signal ? d.patterns.filter((m) => m.name === signal.pattern && Math.abs(m.trigger - signal.trigger_level) < 0.011) : d.patterns.slice(0, 1);
    shapes.forEach((m) => {
      const pts = m.points.filter(([t]) => index.has(t)).map(([t, p]) => [x(index.get(t)), y(p)]);
      if (pts.length > 1) {
        parts.push(`<path class="pattern" d="${pts.map(([a, b], j) => `${j ? "L" : "M"}${a.toFixed(1)},${b.toFixed(1)}`).join("")}"/>`);
        parts.push(pts.map(([a, b]) => `<circle class="pattern-pt" cx="${a}" cy="${b}" r="3"/>`).join(""));
      }
      if (m.trigger_date && index.has(m.trigger_date)) {
        const i = index.get(m.trigger_date);
        parts.push(`<text class="marker" x="${x(i)}" y="${y(c[i].l) + 14}" text-anchor="middle" fill="var(--accent)">▲</text>`);
      }
    });
  }

  // vizszintes vonalak cimkevel
  hlines.forEach((h) => {
    const yy = y(h.v);
    parts.push(`<line class="hline ${h.dash ? "dash" : ""}" x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}" stroke="${h.color}"/>`);
    parts.push(`<rect class="hlabel-bg" x="${W - pad.r + 1}" y="${yy - 9}" width="${pad.r - 2}" height="18" rx="4" fill="${h.color}"/>`);
    parts.push(`<text class="hlabel" x="${W - pad.r + 5}" y="${yy + 4}" fill="#fff">${esc(h.label)}</text>`);
  });

  parts.push(`<g id="cross"></g><rect id="hit" x="${pad.l}" y="${pad.t}" width="${W - pad.l - pad.r}" height="${H - pad.t - pad.b}" fill="transparent"/>`);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");

  // celkereszt + tooltip
  const hit = svg.querySelector("#hit");
  const cross = svg.querySelector("#cross");
  hit.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = (ev.clientX - rect.left) * (W / rect.width);
    const i = Math.max(0, Math.min(n - 1, Math.floor((px - pad.l) / step)));
    const k = c[i];
    cross.innerHTML = `<line class="cross" x1="${x(i)}" x2="${x(i)}" y1="${pad.t}" y2="${volTop + volH}"/>`;
    const ch = i > 0 ? (k.c / c[i - 1].c - 1) * 100 : null;
    tooltip.innerHTML = `<strong>${esc(k.t)}</strong>
      <div class="row"><span>Nyitó</span><span>${fmt(k.o)}</span></div>
      <div class="row"><span>Max</span><span>${fmt(k.h)}</span></div>
      <div class="row"><span>Min</span><span>${fmt(k.l)}</span></div>
      <div class="row"><span>Záró</span><span>${fmt(k.c)} <span class="${ch >= 0 ? "up" : "down"}">${pct(ch, 2)}</span></span></div>
      <div class="row"><span>Volumen</span><span>${fmt((k.v || 0) / 1e6, 1)} M</span></div>`;
    tooltip.hidden = false;
    const tx = ev.clientX + 16 + 170 > window.innerWidth ? ev.clientX - 186 : ev.clientX + 16;
    tooltip.style.left = `${tx}px`;
    tooltip.style.top = `${ev.clientY + 12}px`;
  });
  hit.addEventListener("mouseleave", () => { cross.innerHTML = ""; tooltip.hidden = true; });
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
// Sajat (kezzel felvett) poziciok
// ---------------------------------------------------------------------------
const VERDICT = { ok: ["go", "Rendben"], warn: ["warn", "Figyelj"], bad: ["bad", "Kockázatos"] };
const CHECK_MARK = { ok: ["ok", "✓"], warn: ["warn", "!"], bad: ["no", "✗"], info: ["info", "i"] };
const today = () => new Date().toISOString().slice(0, 10);
let pendingEdit = null;   // a papir oldalarol inditott szerkesztes

async function pageHoldings() {
  await loadOverview();
  const data = await api("/api/holdings");
  const t = data.totals;
  const open = data.holdings;
  const counts = { bad: 0, warn: 0, ok: 0 };
  open.forEach((h) => { if (h.verdict) counts[h.verdict] += 1; });

  view.innerHTML = `
    <div class="page-head"><h1>Saját pozíciók</h1>
      <span class="muted">A brókernél meglévő pozícióid. A rendszer ugyanazzal a motorral értékeli ki őket, mint a belépéseket, és a monitor szól, ha lépni kell.</span></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Nyitott pozíció</div><div class="value">${open.length}</div>
        <div class="sub">${counts.bad ? `<span class="down">${counts.bad} kockázatos</span> · ` : ""}${counts.warn ? `<span style="color:var(--warn)">${counts.warn} figyelendő</span> · ` : ""}${counts.ok} rendben</div></div>
      <div class="kpi"><div class="label">Piaci érték</div><div class="value">${fmt(t.value, 0)} $</div>
        <div class="sub">bekerülés: ${fmt(t.cost, 0)} $</div></div>
      <div class="kpi"><div class="label">Nem realizált eredmény</div>
        <div class="value ${t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : ""}">${signed(t.pnl, 0, " $")}</div>
        <div class="sub">${t.cost ? pct((t.pnl / t.cost) * 100, 1) : "–"}</div></div>
      <div class="kpi"><div class="label">Realizált (lezártak)</div>
        <div class="value ${t.realised > 0 ? "up" : t.realised < 0 ? "down" : ""}">${data.closed.length ? signed(t.realised, 0, " $") : "–"}</div>
        <div class="sub">${data.closed.length} lezárt pozíció</div></div>
    </div>

    <section class="card" id="holding-form-card"><h2 id="holding-form-title">Pozíció felvétele</h2><div class="card-body">
      ${holdingForm()}
    </div></section>

    ${open.length ? `<div class="holdings">${open.map((h) => holdingCard(h, true)).join("")}</div>`
      : `<section class="card"><div class="card-body empty">Még nincs felvett pozíció. Add meg fent a tickert, a belépő árat és a darabszámot.</div></section>`}

    ${data.closed.length ? `<section class="card" style="margin-top:20px"><h2>Lezárt saját pozíciók</h2><div class="table-wrap"><table>
      <thead><tr><th>Ticker</th><th>Belépés</th><th class="num">Belépő</th><th>Zárás</th><th class="num">Záró ár</th><th class="num">Db</th><th class="num">Eredmény</th><th></th></tr></thead>
      <tbody>${data.closed.map((h) => `<tr>
        <td class="ticker"><a href="#/p/${esc(h.symbol)}">${esc(h.symbol)}</a></td><td>${esc(h.entry_date)}</td><td class="num">${fmt(h.entry_price)}</td>
        <td>${esc(h.closed_at)}</td><td class="num">${fmt(h.close_price)}</td><td class="num">${fmt(h.shares, h.shares % 1 ? 2 : 0)}</td>
        <td class="num ${h.pnl > 0 ? "up" : h.pnl < 0 ? "down" : ""}">${signed(h.pnl, 0, " $")} <span class="muted small">${pct(h.pnl_pct, 1)}</span></td>
        <td><button class="btn danger" type="button" data-act="delete" data-id="${esc(h.id)}" data-sym="${esc(h.symbol)}">Törlés</button></td></tr>`).join("")}</tbody>
    </table></div></section>` : ""}`;

  bindHoldingForm(open);
  bindHoldingActions(open, () => pageHoldings());
  if (pendingEdit) {
    const btn = view.querySelector(`button[data-act="edit"][data-id="${CSS.escape(pendingEdit)}"]`);
    pendingEdit = null;
    if (btn) btn.click();
  }
}

function holdingForm(h = null) {
  const v = (k, d = "") => esc(h && h[k] != null ? h[k] : d);
  return `<form id="holding-form" data-id="${h ? esc(h.id) : ""}">
    <div class="form-grid tight">
      <div class="field"><label for="h-symbol">Ticker <span class="req">*</span></label>
        <input type="text" id="h-symbol" name="symbol" value="${v("symbol")}" placeholder="pl. AAPL" autocomplete="off" ${h ? "disabled" : "required"} style="text-transform:uppercase"></div>
      <div class="field"><label for="h-entry_price">Belépő ár ($) <span class="req">*</span></label>
        <input type="number" id="h-entry_price" name="entry_price" value="${v("entry_price")}" min="0" step="any" required></div>
      <div class="field"><label for="h-shares">Darab <span class="req">*</span></label>
        <input type="number" id="h-shares" name="shares" value="${v("shares")}" min="0" step="any" required></div>
      <div class="field"><label for="h-entry_date">Belépés napja</label>
        <input type="date" id="h-entry_date" name="entry_date" value="${v("entry_date", today())}" max="${today()}"></div>
      <div class="field"><label for="h-stop">Stop ($)</label>
        <input type="number" id="h-stop" name="stop" value="${v("stop")}" min="0" step="any" placeholder="opcionális"></div>
      <div class="field"><label for="h-target">Cél ($)</label>
        <input type="number" id="h-target" name="target" value="${v("target")}" min="0" step="any" placeholder="opcionális"></div>
      <div class="field wide"><label for="h-note">Megjegyzés</label>
        <input type="text" id="h-note" name="note" value="${v("note")}" maxlength="200" placeholder="opcionális – pl. miért vetted"></div>
    </div>
    <div class="form-actions">
      <button class="btn primary" type="submit">${h ? "Módosítás mentése" : "Felvétel és kiértékelés"}</button>
      ${h ? `<button class="btn" type="button" id="holding-cancel">Mégse</button>` : ""}
      <span id="holding-msg" class="msg"></span>
    </div>
    <div class="muted small" style="margin-top:8px">Stop nélkül is felveheted: a kiértékelés javasol egyet. Új tickernél az árfolyam-adatot most tölti le (néhány másodperc).</div>
  </form>`;
}

function bindHoldingForm(list) {
  const form = $("#holding-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#holding-msg");
    const val = (k) => form.elements[k].value.trim();
    const body = { entry_price: val("entry_price"), shares: val("shares"), entry_date: val("entry_date"),
      stop: val("stop"), target: val("target"), note: val("note") };
    const id = form.dataset.id;
    if (!id) body.symbol = val("symbol").toUpperCase();
    else body.id = id;
    msg.className = "msg"; msg.textContent = id ? "Mentés…" : "Felvétel és kiértékelés…";
    form.querySelector("button[type=submit]").disabled = true;
    try {
      await api(`/api/holdings/${id ? "update" : "add"}`, { method: "POST", body: JSON.stringify(body) });
      overview = null;
      await pageHoldings();
    } catch (e) {
      msg.className = "msg err"; msg.textContent = e.message;
      form.querySelector("button[type=submit]").disabled = false;
    }
  });
  const cancel = $("#holding-cancel");
  if (cancel) cancel.addEventListener("click", () => pageHoldings());
}

function holdingCard(h, withLink) {
  if (h.missing) {
    return `<div class="holding"><div class="holding-head"><span class="ticker">${esc(h.symbol)}</span>
      <span class="badge fail">nincs árfolyam-adat</span></div><div class="muted small">Nyomj Frissítést a letöltéshez.</div></div>`;
  }
  const [vcls, vtext] = VERDICT[h.verdict] || ["fail", h.verdict];
  const reasons = h.checks.filter((c) => c.level === "bad" || c.level === "warn")
    .sort((a, b) => (a.level === "bad" ? -1 : 1) - (b.level === "bad" ? -1 : 1));
  const checks = h.checks.map((c) => {
    const [cls, mark] = CHECK_MARK[c.level] || ["unk", "?"];
    return `<li><span class="mark ${cls}">${mark}</span><span><strong>${esc(c.name)}</strong> <span class="muted">${esc(c.detail)}</span></span></li>`;
  }).join("");
  const dec = h.shares % 1 ? 2 : 0;
  const stopCell = h.stop != null
    ? `<span class="v down">${fmt(h.stop)}</span><span class="p">${pct((h.stop / h.price - 1) * 100, 1)}</span>`
    : `<span class="v muted">nincs</span><span class="p">javaslat: ${fmt(h.suggested_stop)}</span>`;
  return `<div class="holding ${esc(h.verdict)}" data-id="${esc(h.id)}">
    <div class="holding-head">
      ${withLink ? `<a class="ticker" href="#/p/${esc(h.symbol)}">${esc(h.symbol)}</a>` : `<span class="ticker">Saját pozíció</span>`}
      <span class="badge ${vcls}">${vtext}</span>
      <span class="pnl ${h.pnl > 0 ? "up" : h.pnl < 0 ? "down" : ""}">${signed(h.pnl, 0, " $")} <span class="small">(${pct(h.pnl_pct, 1)})</span></span>
    </div>
    <div class="plan">
      <span class="k">Belépő</span><span class="v">${fmt(h.entry_price)}</span><span class="p">${esc(h.entry_date)}</span>
      <span class="k">Záró</span><span class="v">${fmt(h.price)}</span><span class="p">${esc(h.as_of)}</span>
      <span class="k">Méret</span><span class="v">${fmt(h.shares, dec)} db</span><span class="p">${fmt(h.value, 0)} $</span>
      <span class="k">Stop</span>${stopCell}
      ${h.r_multiple != null ? `<span class="k">Eredmény R-ben</span><span class="v">${signed(h.r_multiple, 2, "R")}</span><span class="p">P/L a stopnál: ${signed(h.pnl_at_stop, 0, " $")}</span>` : ""}
      <span class="k">Cél</span>${h.eval_target != null ? `<span class="v up">${fmt(h.eval_target)}</span><span class="p">${pct((h.eval_target / h.price - 1) * 100, 1)} · ${esc(h.target_source)}</span>` : `<span class="v muted">–</span><span class="p"></span>`}
    </div>
    ${reasons.length ? `<ul class="reasons">${reasons.map((c) => `<li class="${c.level}"><strong>${esc(c.name)}:</strong> ${esc(c.detail)}</li>`).join("")}</ul>`
      : `<div class="muted small" style="margin-top:8px">Nincs figyelmeztető jel a charton.</div>`}
    <div class="muted small">Stop-javaslat: <strong>${fmt(h.suggested_stop)} $</strong> – ${esc(h.suggested_stop_text)}</div>
    ${h.note ? `<div class="note">„${esc(h.note)}”</div>` : ""}
    <details><summary>Minden szempont (${h.checks.length})</summary><ul class="checks">${checks}</ul></details>
    <div class="actions">
      <button class="btn" type="button" data-act="edit" data-id="${esc(h.id)}">Szerkesztés</button>
      ${h.stop == null || h.suggested_stop > h.stop ? `<button class="btn" type="button" data-act="usestop" data-id="${esc(h.id)}" data-stop="${h.suggested_stop.toFixed(2)}">Javasolt stop: ${fmt(h.suggested_stop)}</button>` : ""}
      <button class="btn" type="button" data-act="close" data-id="${esc(h.id)}" data-price="${h.price.toFixed(2)}">Lezárás (eladtam)</button>
      <button class="btn danger" type="button" data-act="delete" data-id="${esc(h.id)}" data-sym="${esc(h.symbol)}">Törlés</button>
    </div>
  </div>`;
}

function bindHoldingActions(list, reload) {
  view.querySelectorAll("button[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
    const id = btn.dataset.id;
    const act = btn.dataset.act;
    try {
      if (act === "edit") {
        const h = list.find((x) => x.id === id);
        if (!location.hash.startsWith("#/holdings")) { pendingEdit = id; location.hash = "#/holdings"; return; }
        $("#holding-form-title").textContent = `${h.symbol} szerkesztése`;
        $("#holding-form-card .card-body").innerHTML = holdingForm(h);
        bindHoldingForm(list);
        $("#holding-form-card").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if (act === "usestop") {
        if (!confirm(`A stop legyen ${btn.dataset.stop} $? (A brókernél neked kell átállítanod.)`)) return;
        await api("/api/holdings/update", { method: "POST", body: JSON.stringify({ id, stop: btn.dataset.stop }) });
      } else if (act === "close") {
        const price = prompt("Milyen áron adtad el? ($)", btn.dataset.price);
        if (price === null) return;
        await api("/api/holdings/close", { method: "POST", body: JSON.stringify({ id, price: price.replace(",", "."), date: today() }) });
      } else if (act === "delete") {
        if (!confirm(`Biztosan törlöd a(z) ${btn.dataset.sym} pozíciót? Ez nem lezárás – nyom nélkül eltűnik.`)) return;
        await api("/api/holdings/delete", { method: "POST", body: JSON.stringify({ id }) });
      }
      overview = null;
      await reload();
    } catch (e) { alert(e.message); }
  }));
}

// ---------------------------------------------------------------------------
// Poziciok (a rendszer jelzeseinek virtualis kovetese)
// ---------------------------------------------------------------------------
const POS_STATUS = {
  pending: ["watch", "Belépésre vár"], open: ["go", "Nyitott"], closed: ["fail", "Lezárva"], skipped: ["fail", "Kimaradt"],
};
const REASON = { target: "cél", stop: "stop", breakeven: "nullszaldó (½ már eladva)", trail: "követett stop", timeout: "időkorlát", manual: "kézi zárás" };
// ha a jelzesbol paper trade lett, az Alpaca allapota az iranyado
const POS_BROKER_STATUS = {
  submitted: ["watch", "Beküldve – belépésre vár"], open: ["go", "Nyitott"], closed: ["fail", "Lezárva"],
  expired: ["fail", "Nem teljesült"], cancelled: ["fail", "Visszavonva"], error: ["bad", "Hiba"],
};
// a Jelzett trade-ek es a Jelzesnaplo ugyanazt az allapotot mutatja: [osztaly, felirat]
const posStatus = (p) => (p.source === "alpaca" ? POS_BROKER_STATUS[p.broker_status] : POS_STATUS[p.status]) || ["fail", p.status];
const posSource = (p) => (p.source === "alpaca"
  ? `<span class="muted small" title="A paper trade állapota az Alpacáról">Alpaca</span>`
  : `<span class="muted small" title="Nincs paper trade: a jelzés szerinti belépés virtuális követése">virtuális</span>`);

let positionsStrategy = "";
async function pagePositions() {
  await loadOverview();
  const data = await api("/api/positions");
  const s = positionsStrategy ? (data.by_strategy[positionsStrategy] || { n: 0 }) : data.stats;
  const list = data.positions.filter((p) => !positionsStrategy || p.strategy === positionsStrategy);
  const paper = data.strategies && data.strategies.paper;
  view.innerHTML = `
    <div class="page-head"><h1>Jelzett trade-ek</h1><span class="muted">Minden paper trade (a kézzel beküldött is) az Alpaca állapotával, percenként frissítve; ahol a jelzésből nem lett paper trade, ott a jelzés szerinti belépés virtuális követése.</span></div>
    ${data.broker_error ? `<div class="callout" style="color:var(--down)">Az Alpaca most nem érhető el – a legutóbbi szinkron állapota látszik. ${esc(data.broker_error)}</div>` : ""}
    <div class="kpis">
      <div class="kpi"><div class="label">Lezárt trade</div><div class="value">${s.n || 0}</div></div>
      <div class="kpi"><div class="label">Nyerő arány</div><div class="value">${s.n ? fmt(s.win * 100, 0) + "%" : "–"}</div></div>
      <div class="kpi"><div class="label">Átlag eredmény</div><div class="value ${s.ev > 0 ? "up" : s.ev < 0 ? "down" : ""}">${s.n ? signed(s.ev, 2, "R") : "–"}</div></div>
      <div class="kpi"><div class="label">Összesen</div><div class="value ${s.total > 0 ? "up" : s.total < 0 ? "down" : ""}">${s.n ? signed(s.total, 1, "R") : "–"}</div>
        <div class="sub">1R = a trade-enként vállalt kockázat</div></div>
    </div>
    <div class="filters">${strategySeg(positionsStrategy, "data-strategy")}
      <span class="muted small">A paper fiókhoz kötött stratégia: ${strategyBadge(paper)}</span></div>
    <section class="card"><h2>${positionsStrategy ? esc(STRAT[positionsStrategy][1]) + " – követett trade-ek" : "Összes követett trade"}</h2>
      ${list.length ? positionsTable(list, true) : `<div class="card-body empty">Még nincs követett trade${positionsStrategy ? " ebben a stratégiában" : ""}. Az első belépési jelzés vagy paper trade után jelenik meg itt.</div>`}
    </section>`;
  view.querySelectorAll("button[data-strategy]").forEach((b) => b.addEventListener("click", () => { positionsStrategy = b.dataset.strategy; pagePositions(); }));
  bindRowLinks();
}

function positionsTable(list, withTicker) {
  return `<div class="table-wrap"><table><thead><tr>${withTicker ? "<th>Ticker</th>" : ""}<th>Stratégia</th><th>Jelzés</th><th>Alakzat</th><th>Állapot</th>
    <th class="num">Belépő</th><th class="num">Aktuális stop</th><th class="num">½ eladás</th><th class="num">Cél</th><th class="num">Nap</th><th class="num">Eredmény</th><th>Megjegyzés</th></tr></thead><tbody>
    ${list.map((p) => {
      const [cls, label] = posStatus(p);
      const note = p.status === "skipped" ? p.reason : p.status === "closed" ? (REASON[p.reason] || p.reason) : p.partial_done ? "fele eladva, stop a belépőn" : "";
      return `<tr ${withTicker ? `class="clickable" data-href="#/p/${esc(p.symbol)}"` : ""}>
        ${withTicker ? `<td class="ticker">${esc(p.symbol)}</td>` : ""}
        <td>${strategyBadge(p.strategy)}</td><td>${esc(p.signal_date)}${p.signal_at ? `<div class="muted small">kiküldve ${when(p.signal_at)}</div>` : ""}${p.submitted_at && p.submitted_at !== p.signal_at ? `<div class="muted small">beküldve ${when(p.submitted_at)}</div>` : ""}</td><td>${esc(p.pattern)}</td><td><span class="badge ${cls}">${label}</span> ${posSource(p)}</td>
        <td class="num">${p.entry_price ? `${fmt(p.entry_price)}<div class="muted small">${when(p.entry_at) || esc(p.entry_date || "")}</div>` : "–"}</td><td class="num">${fmt(p.stop)}</td>
        <td class="num">${p.partial_price ? fmt(p.partial_price) : "–"}${p.partial_done ? " ✓" : ""}</td>
        <td class="num">${fmt(p.target)}</td><td class="num">${p.bars_held || "–"}${p.status === "open" ? ` / ${p.horizon}` : ""}</td>
        <td class="num ${p.result_r > 0 ? "up" : p.result_r < 0 ? "down" : ""}">${p.result_r != null ? signed(p.result_r, 2, "R") : "–"}</td>
        <td class="reason">${esc(note)}${p.signal_id ? ` ${signalLink(p.signal_id)}` : ""}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Strategiak: aktiv (paper fiokhoz kotott), kutatasi, homokozo
// ---------------------------------------------------------------------------
const PB_PARAMS = [
  ["rsi2_max", "RSI(2) e alatt", "Jelzés, ha a 2 napos RSI ez alá esik (mély túladottság)."],
  ["limit_pct", "Vételi limit a záró alatt (%)", "Másnapra szóló, egynapos vételi limit ennyivel a jelzés napi záró alatt."],
  ["stop_atr", "Stop (ATR)", "A stop a belépő alatt ennyi ATR-rel."],
  ["exit_crsi", "Kilépés: ConnorsRSI e felett", "Ha a záró ConnorsRSI e fölé megy, másnap nyitáskor kilép."],
  ["exit_sma", "Kilépés: záró > SMA (0 = ki)", "Akkor is kilép (másnap nyitáskor), ha a záró e mozgóátlag fölé ér – az első erős visszapattanás."],
  ["time_stop", "Időkorlát (nap)", "Legfeljebb ennyi kereskedési napig tart."],
  ["max_signals_per_day", "Napi jelzés-limit (0 = nincs)", "Naponta legfeljebb ennyi jelzés (a legalacsonyabb RSI(2) előrébb). A validálás szerint a limit rontja az eredményt."],
  ["max_open", "Max. nyitott kötés (0 = nincs)", "Ebben a stratégiában egyszerre legfeljebb ennyi nyitott / függő kötés."],
];
const MO_PARAMS = [
  ["top_n", "Papírok száma", "Ennyi papír van a portfólióban, egyenlő súllyal (tőke / ennyi)."],
  ["buffer", "Megtartás a legjobb … között", "A tartott papír addig marad, amíg a rangja ezen belül van (forgás-takarékos puffer)."],
  ["lookback", "Momentum-ablak (nap)", "A hozamot ennyi kereskedési napra visszamenőleg nézi (~12 hónap)."],
  ["skip", "Kihagyott utolsó napok", "A legutóbbi ~1 hónap kimarad: rövid távon a hozam inkább visszafordul."],
  ["stop_pct", "Stop (%; 0 = nincs)", "Katasztrófa-stop a belépő alatt. A validáció szerint a 15–25%-os stop rontott, ezért alapból nincs."],
  ["min_dollar_volume", "Min. dollárforgalom ($/nap)", "20 napos átlagos forgalom – az illikvid papírok kimaradnak."],
];
const BO_PARAMS = { min_score: "Min. pontszám", confirm_breakout: "Megerősített kitörés", exit_mode: "Kilépési mód",
  partial_r: "½ eladás (R)", min_stop_atr: "Stop min. távolság (ATR)", regime_filter: "Piaci rezsim-szűrő", earnings_blackout_days: "Earnings-tilalom (nap)" };

async function strategyAction(body) {
  return api("/api/strategies", { method: "POST", body: JSON.stringify(body) });
}

function paperStrategyCard(st) {
  if (!st) return "";
  const options = st.strategies.filter((x) => x.can_paper);
  return `<section class="card"><div class="card-body" style="padding-top:14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
    <strong>A paper fiókhoz kötött stratégia:</strong> ${strategyBadge(st.paper)}
    <select id="paper-strategy" style="font:inherit;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text)">
      ${options.map((x) => `<option value="${esc(x.id)}" ${x.paper ? "selected" : ""} ${x.enabled ? "" : "disabled"}>${esc(STRAT[x.id][1])}${x.enabled ? "" : " (kikapcsolva)"}</option>`).join("")}</select>
    <button class="btn" type="button" id="paper-switch">Átváltás</button>
    ${st.auto_trade ? "" : `<span class="pill warn">az automata beküldés ki van kapcsolva</span>`}
    <span class="muted small">A homokozó soha nem köthető ide. Váltáskor a régi stratégia nyitott kötései a saját szabályaikkal zárulnak.</span>
  </div></section>`;
}

function bindPaperSwitch(after) {
  const btn = $("#paper-switch");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const target = $("#paper-strategy").value;
    if (!confirm(`A paper fiókhoz mostantól a(z) „${STRAT[target][1]}” stratégia jelzései mennek. Folytatod?`)) return;
    btn.disabled = true;
    try { await strategyAction({ action: "paper", paper: target }); after(); } catch (e) { alert(e.message); btn.disabled = false; }
  });
}

// A legutobbi napi momentum-rangsor (a monitor zaras utan irja): ki lenne bent, ha ma lenne a rebalansz
function momentumTop(m) {
  const held = new Set(m.held || []);
  const n = (m.params && m.params.top_n) || 10;
  return `<details style="margin-top:8px"><summary class="small">Mostani rangsor (${esc(m.as_of)}, ${m.universe} papír)${m.rebalance_day ? " · ma rebalansz" : ""}</summary>
    <div class="muted small" style="margin-top:4px">A legjobb ${n}${held.size ? ` · félkövér: a stratégia most tartja (${held.size})` : ""}. Rebalansz: a hónap utolsó kereskedési napján.</div>
    <ul class="mo-top">${(m.top || []).slice(0, n).map((t) => `<li class="${held.has(t.symbol) ? "held" : ""}"><a href="#/p/${esc(t.symbol)}">${t.rank}. ${esc(t.symbol)}</a><span class="${t.score > 0 ? "up" : "down"}">${signed(t.score, 0, "%")}</span></li>`).join("")}</ul></details>`;
}

async function pageStrategies() {
  await loadOverview();
  const d = await api("/api/strategies");
  const card = (x) => {
    const [cls, label, help] = STRAT[x.id];
    const st = x.stats || { n: 0 };
    const params = x.id === "momentum" && x.params ? MO_PARAMS.map(([k, name, h]) => `<dt title="${esc(h)}">${esc(name)}</dt><dd>${esc(x.params[k])}</dd>`).join("")
      : x.params ? PB_PARAMS.map(([k, name, h]) => `<dt title="${esc(h)}">${esc(name)}</dt><dd>${esc(x.params[k])}</dd>`).join("")
      : x.entry ? Object.entries(x.entry).map(([k, v]) => `<dt>${esc(BO_PARAMS[k] || k)}</dt><dd>${esc(typeof v === "boolean" ? (v ? "igen" : "nem") : v)}</dd>`).join("") : "";
    return `<section class="card strat-card ${cls}"><div class="card-body" style="padding-top:14px">
      <div class="row"><h2 style="margin:0">${esc(label)}</h2>${x.paper ? `<span class="pill ok">paper fiókhoz kötve</span>` : x.can_paper ? `<span class="pill">kutatási</span>` : `<span class="pill warn">sosem paper</span>`}</div>
      <div class="muted small">${esc(help)}${x.id === "sandbox" ? ` Motor: <strong>${x.engine === "breakout" ? "kitörés" : "visszahúzás"}</strong>.` : ""}</div>
      <div class="row">
        <label class="small"><input type="checkbox" data-sw="enabled" data-id="${esc(x.id)}" ${x.enabled ? "checked" : ""} ${x.paper ? "disabled title=\"A paper fiókhoz kötött stratégia nem kapcsolható ki\"" : ""}> bekapcsolva</label>
        <label class="small"><input type="checkbox" data-sw="alerts" data-id="${esc(x.id)}" ${x.alerts ? "checked" : ""}> értesítés Discordra</label>
      </div>
      <dl><dt>Lezárt kötés</dt><dd>${st.n || 0}</dd>
        <dt>Nyerési arány</dt><dd>${st.n ? fmt(st.win * 100, 0) + "%" : "–"}</dd>
        <dt>Átlag eredmény</dt><dd class="${st.ev > 0 ? "up" : st.ev < 0 ? "down" : ""}">${st.n ? signed(st.ev, 2, "R") : "–"}</dd>
        <dt>Nyitott / függő</dt><dd>${x.open}${x.paper_open ? ` (paper: ${x.paper_open})` : ""}</dd>
        ${x.research && x.research.cagr != null ? `<dt title="Múltbeli validálás (2017–2025.03, költség után, csak a mostani indextagokon – felfelé torzít; a vegyes, kiesett papírokat is tartalmazó listán jóval kisebb a többlet)">Validálás (múlt)</dt>
          <dd>évi ${signed(x.research.cagr, 1, "%")} (univerzum: ${signed(x.research.baseline_cagr, 1, "%")}) · Sharpe ${fmt(x.research.sharpe, 2)} · max. visszaesés ${fmt(x.research.max_dd, 0)}%</dd>`
        : x.research ? `<dt title="Múltbeli validálás (2017–2025.03, költség után, csak a mostani indextagokon – felfelé torzít)">Validálás (múlt)</dt>
          <dd>${x.research.win != null ? fmt(x.research.win * 100, 0) + "%" : "–"} nyerő · ${x.research.n} kötés · heti ${x.research.per_week != null ? fmt(x.research.per_week, 2) : "–"}${x.research.ev != null ? ` · átlag ${signed(x.research.ev, 2, "%")}` : ""}</dd>` : ""}</dl>
      ${params ? `<details style="margin-top:8px"><summary class="small">Szabályok</summary><dl>${params}</dl></details>` : ""}
      ${x.momentum ? momentumTop(x.momentum) : ""}
      ${x.symbols ? `<details style="margin-top:8px"><summary class="small">ETF-lista (${x.symbols.length})</summary>
        <div class="row" style="margin-top:6px"><input type="text" data-etf-symbols value="${esc(x.symbols.join(", "))}" style="flex:1;min-width:0">
        <button class="btn" type="button" data-etf-save>Mentés</button></div></details>` : ""}
      ${x.can_paper && !x.paper ? `<div style="margin-top:10px"><button class="btn" type="button" data-paper="${esc(x.id)}" ${x.enabled ? "" : "disabled"}>Paper fiókhoz kötés</button></div>` : ""}
    </div></section>`;
  };
  view.innerHTML = `
    <div class="page-head"><h1>Stratégiák</h1><span class="muted">Mindegyik küld jelzést és virtuálisan követi a kötéseit; a paper fiókba csak a hozzá kötött stratégia jelzései mennek. Minden jelzésen, kötésen és értesítésen látszik, melyik stratégia küldte.</span></div>
    ${d.auto_trade ? "" : `<div class="callout">Az automata paper beküldés (Beállítások → auto_trade) ki van kapcsolva – most egyik stratégia sem küld megbízást.</div>`}
    <div class="strat-grid">${d.strategies.map(card).join("")}</div>`;
  view.querySelectorAll("input[data-sw]").forEach((cb) => cb.addEventListener("change", async () => {
    try { await strategyAction({ action: "update", strategy: cb.dataset.id, values: { [cb.dataset.sw]: cb.checked } }); }
    catch (e) { alert(e.message); }
    pageStrategies();
  }));
  const etfSave = view.querySelector("button[data-etf-save]");
  if (etfSave) etfSave.addEventListener("click", async () => {
    const symbols = view.querySelector("input[data-etf-symbols]").value.split(/[\s,;]+/).filter(Boolean);
    try { await strategyAction({ action: "update", strategy: "etf", values: { symbols } }); } catch (e) { alert(e.message); }
    pageStrategies();
  });
  view.querySelectorAll("button[data-paper]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`A paper fiókhoz mostantól a(z) „${STRAT[b.dataset.paper][1]}” stratégia jelzései mennek. A régi stratégia nyitott kötései a saját szabályaikkal zárulnak. Folytatod?`)) return;
    try { await strategyAction({ action: "paper", paper: b.dataset.paper }); } catch (e) { alert(e.message); }
    pageStrategies();
  }));
}

// ---------------------------------------------------------------------------
// Kutatas (homokozo): szabad kiserletezes, soha nincs a paper fiokhoz kotve
// ---------------------------------------------------------------------------
const PB_EXTRA = [
  ["trend_sma", "Trend: záró > SMA (nap)", "A papír ezen mozgóátlag felett legyen (emelkedő trend)."],
  ["atr_period", "ATR periódus", "A stop távolságához használt ATR hossza."],
  ["min_price", "Min. ár ($)", ""],
  ["min_dollar_volume", "Min. dollárforgalom ($/nap)", "20 napos átlag."],
  ["earnings_blackout_days", "Gyorsjelentés-tilalom (nap)", "Nincs jelzés, ha ennyi napon belül gyorsjelentés jön."],
  ["cost_bps", "Költség (bps / oldal)", "Jutalék + csúszás oldalanként – a nettó eredményhez."],
];
let sandboxTimer = null;

function periodRows(main) {
  if (!main) return "";
  const row = (label, v) => v ? `<tr><td>${label}</td><td class="num">${v.n}</td><td class="num">${v.fills_per_week != null ? fmt(v.fills_per_week, 2) : "–"}</td>
    <td class="num">${v.win != null ? fmt(v.win * 100, 1) + "%" : "–"}</td><td class="num ${tone(v.ev)}">${v.ev != null ? signed(v.ev, 2, "%") : "–"}</td>
    <td class="num">${v.pf != null ? fmt(v.pf, 2) : "–"}</td></tr>` : "";
  return `<div class="table-wrap"><table><thead><tr><th>Időszak</th><th class="num">Kötés</th><th class="num">Heti</th><th class="num">Nyerési arány</th><th class="num">Átlag nettó</th><th class="num">PF</th></tr></thead><tbody>
    ${row("Tanító (2022 előtt)", main.train)}${row("Validáció (2022–2025.03)", main.test)}${row("Látható összesen", main.visible)}</tbody></table></div>`;
}

async function pageSandbox() {
  await loadOverview();
  const d = await api("/api/sandbox");
  renderSandbox(d);
}

function renderSandbox(d, message = "") {
  const st = d.strategy, bt = d.backtest || {}, status = bt.status, res = bt.result;
  const pb = st.engine === "pullback";
  const input = (k, v, step = "any") => `<input type="number" step="${step}" name="${k}" value="${esc(v)}" style="width:120px">`;
  const pbForm = [...PB_PARAMS, ...PB_EXTRA].map(([k, label, help]) => `<label class="field" title="${esc(help)}"><span>${esc(label)}</span>${input(k, st.params[k])}
    ${d.pullback_params[k] !== st.params[k] ? `<span class="muted small">Visszahúzás: ${esc(d.pullback_params[k])}</span>` : ""}</label>`).join("")
    + `<label class="field check"><input type="checkbox" name="regime_filter" ${st.params.regime_filter ? "checked" : ""}><span>Rezsim-szűrő (QQQ / S&P 500 / saját trend)</span></label>`;
  const boForm = `<div class="table-wrap"><table><thead><tr><th>Szűrő</th><th>Élő (Kitörés)</th><th>Homokozó (üres = mint élőben)</th></tr></thead><tbody>
    ${d.entry_fields.map((f) => `<tr><td>${esc(f.label)}</td><td class="muted">${esc(f.live)}</td><td>${f.type === "bool"
      ? `<select name="${esc(f.field)}"><option value="">–</option><option value="true" ${f.value === true ? "selected" : ""}>igen</option><option value="false" ${f.value === false ? "selected" : ""}>nem</option></select>`
      : `<input name="${esc(f.field)}" value="${f.value == null ? "" : esc(f.value)}" placeholder="${esc(f.live)}" style="width:110px">`}</td></tr>`).join("")}</tbody></table></div>`;
  const statusPill = status ? `<span class="pill ${status.state === "done" ? "ok" : status.state === "error" ? "bad" : "warn"}">${esc({ queued: "sorban áll", running: "számol", done: "kész", error: "hiba" }[status.state] || status.state)}</span> <span class="muted small">${esc(status.message || "")}</span>` : "";
  view.innerHTML = `
    <div class="page-head"><h1>Kutatás (homokozó)</h1><span class="muted">Szabad kísérletezés: a homokozó stratégia jelez és virtuálisan követ, de <strong>soha nincs a paper fiókhoz kötve</strong>. Ami beválik, egy gombbal átvehető a Visszahúzás stratégiába.</span></div>
    ${message ? `<div class="callout">${esc(message)}</div>` : ""}
    <section class="card strat-card s-sandbox"><h2>Beállítás ${strategyBadge("sandbox")}</h2><div class="card-body">
      <form id="sb-form">
        <div class="row" style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
          <label>Motor: <select name="engine"><option value="pullback" ${pb ? "selected" : ""}>Visszahúzás (RSI2)</option><option value="breakout" ${pb ? "" : "selected"}>Kitörés (alakzatok)</option></select></label>
          <label><input type="checkbox" name="enabled" ${st.enabled ? "checked" : ""}> bekapcsolva (élő jelzések, virtuális követés)</label>
          <label><input type="checkbox" name="alerts" ${st.alerts ? "checked" : ""}> értesítés Discordra</label>
        </div>
        <div class="form-grid">${pb ? pbForm : boForm}</div>
        <div class="form-actions" style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" type="submit">Mentés</button>
          <button class="btn" type="button" id="sb-backtest">Backtest futtatása</button>
          ${pb ? `<button class="btn" type="button" id="sb-adopt">Átvétel a Visszahúzás stratégiába</button>` : ""}
          <button class="btn" type="button" id="sb-reset">Visszaállítás</button>
        </div>
      </form></div></section>
    <div class="grid-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <section class="card"><h2>Homokozó backtest ${statusPill}</h2><div class="card-body">
        ${pb ? (res ? `<div class="muted small">Számolva: ${esc(res.computed_at || "")} · ${res.symbols} papír · ${res.gate && res.gate.passed ? "az elfogadási feltételen ÁTMENT" : "az elfogadási feltételen nem ment át"}</div>${periodRows(res.main)}`
          : `<div class="empty">Még nincs eredmény – mentsd a beállítást, majd „Backtest futtatása”.</div>`)
          : `<div class="muted">Kitörés-motornál a backtest a <a href="#/lab">Labor</a> fülön fut kísérletként.</div>`}
      </div></section>
      <section class="card"><h2>Összehasonlítás: Visszahúzás (RSI2) validálása</h2><div class="card-body">
        ${d.reference ? `<div class="muted small">Számolva: ${esc(d.reference.computed_at || "")}</div>${periodRows(d.reference.main)}` : `<div class="empty">Még nincs validálás.</div>`}
      </div></section>
    </div>
    <section class="card"><h2>A homokozó kötései <span class="muted">– ${d.stats.n ? `${d.stats.n} lezárt, ${fmt(d.stats.win * 100, 0)}% nyerő` : "még nincs lezárt"}</span></h2>
      ${d.positions.length ? positionsTable(d.positions, true) : `<div class="card-body empty">Még nincs kötés – a homokozó a következő zárás után jelez.</div>`}</section>
    <section class="card"><h2>A homokozó döntései <span class="muted">– Jelzésnapló</span></h2>
      ${d.ledger.length ? `<div class="table-wrap"><table><thead><tr><th>Nap</th><th>Ticker</th><th>Döntés</th><th>Ok</th><th></th></tr></thead><tbody>
        ${d.ledger.map((r) => { const [cls, label] = DECISION[r.decision] || ["fail", r.decision]; return `<tr class="clickable" data-href="#/signal/${esc(r.signal_id)}"><td>${esc(r.as_of)}</td><td class="ticker">${esc(r.symbol)}</td><td><span class="badge ${cls}">${label}</span></td><td class="reason">${esc(r.reason)}</td><td>${signalLink(r.signal_id)}</td></tr>`; }).join("")}</tbody></table></div>`
        : `<div class="card-body empty">Még nincs döntés.</div>`}</section>`;

  const form = $("#sb-form");
  const collect = () => {
    const fd = new FormData(form);
    const values = { engine: fd.get("engine"), enabled: !!fd.get("enabled"), alerts: !!fd.get("alerts") };
    if (pb) {
      values.params = { regime_filter: !!fd.get("regime_filter") };
      [...PB_PARAMS, ...PB_EXTRA].forEach(([k]) => { values.params[k] = Number(fd.get(k)); });
    } else {
      values.entry = {};
      d.entry_fields.forEach((f) => { const v = fd.get(f.field); if (v !== null && v !== "") values.entry[f.field] = f.type === "bool" ? v === "true" : v; });
    }
    return values;
  };
  const save = async () => strategyAction({ action: "update", strategy: "sandbox", values: collect() });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await save(); renderSandbox(await api("/api/sandbox"), "Mentve."); } catch (e) { alert(e.message); }
  });
  form.querySelector("select[name=engine]").addEventListener("change", async () => {
    try { await strategyAction({ action: "update", strategy: "sandbox", values: { engine: form.querySelector("select[name=engine]").value } }); pageSandbox(); } catch (e) { alert(e.message); }
  });
  const act = (id, action, confirmText) => { const b = $(id); if (!b) return; b.addEventListener("click", async () => {
    if (confirmText && !confirm(confirmText)) return;
    b.disabled = true;
    try { if (action === "backtest") await save(); const r = await api("/api/sandbox", { method: "POST", body: JSON.stringify({ action }) }); renderSandbox(r, r.message); }
    catch (e) { alert(e.message); b.disabled = false; }
  }); };
  act("#sb-backtest", "backtest");
  act("#sb-adopt", "adopt", "A homokozó szabályai felülírják a Visszahúzás (RSI2) stratégia szabályait. Folytatod?");
  act("#sb-reset", "reset", "A homokozót visszaállítod a Visszahúzás stratégia szabályaira?");
  bindRowLinks();
  if (sandboxTimer) clearInterval(sandboxTimer);
  if (status && (status.state === "queued" || status.state === "running")) {
    sandboxTimer = setInterval(async () => {
      if (!location.hash.startsWith("#/sandbox")) { clearInterval(sandboxTimer); return; }
      const fresh = await api("/api/sandbox");
      const s2 = fresh.backtest && fresh.backtest.status;
      if (!s2 || (s2.state !== "queued" && s2.state !== "running")) { clearInterval(sandboxTimer); renderSandbox(fresh); }
      else { const pill = view.querySelector(".card h2 .pill"); if (pill && pill.nextElementSibling) pill.nextElementSibling.textContent = s2.message || ""; }
    }, 10000);
  }
}

// ---------------------------------------------------------------------------
// Alpaca paper trading
// ---------------------------------------------------------------------------
const BT_STATUS = {
  submitted: ["watch", "Belépésre vár"], open: ["go", "Nyitott"], closed: ["fail", "Lezárva"],
  expired: ["fail", "Nem teljesült"], cancelled: ["fail", "Visszavonva"], error: ["bad", "Hiba"],
};
const LEG_EXIT = { take_profit: "cél", stop: "stop", timeout: "időkorlát", manual: "kézi zárás", not_filled: "nem teljesült" };

async function pageBroker() {
  await loadOverview();
  view.innerHTML = `<div class="loading">Kapcsolódás az Alpacához…</div>`;
  const d = await api("/api/broker");
  renderBroker(d);
}

function renderBroker(d, message = "") {
  const c = d.credentials;
  if (!c.configured) {
    view.innerHTML = `<div class="page-head"><h1>Paper trading</h1></div>
      <section class="card"><div class="card-body empty">Még nincs Alpaca kapcsolat.
        <a href="#/settings" id="go-alpaca">Add meg az API kulcsokat a Beállítások fülön →</a></div></section>`;
    $("#go-alpaca").addEventListener("click", () => setTimeout(() => { const el = $("#alpaca"); if (el) el.scrollIntoView(); }, 300));
    return;
  }
  const a = d.account;
  const s = d.stats;
  const active = d.trades.filter((t) => t.is_active);
  view.innerHTML = `
    <div class="page-head"><h1>Paper trading</h1>
      <span class="pill ${d.error ? "bad" : "ok"}">${d.error ? "kapcsolati hiba" : "Alpaca paper · kapcsolódva"}</span>
      <span class="muted small">kulcs ${esc(c.key_hint)} · ${d.clock ? (d.clock.is_open ? "a piac nyitva" : "a piac zárva") : ""}</span>
      <span style="margin-left:auto"></span>
      <button class="btn" type="button" id="bk-sync">Szinkronizálás</button></div>
    ${d.error ? `<div class="callout" style="color:var(--down)">${esc(d.error)}</div>` : ""}
    ${message ? `<div class="callout">${esc(message)}</div>` : ""}
    ${paperStrategyCard(d.strategies)}
    ${pnlKpis(d, a, s, active)}
    ${equityCard(d.history)}
    ${capitalCard(d.capital)}

    <section class="card"><h2>Mai belépési jelzések <span class="muted">– minden szűrő ✔, pont ≥ küszöb</span></h2>
      ${d.candidates.length ? `<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Alakzat</th><th>Pont</th>
        <th class="num">Limit</th><th class="num">Stop</th><th class="num">½ eladás</th><th class="num">Cél</th><th class="num">Db (terv)</th><th></th></tr></thead><tbody>
        ${d.candidates.map((x) => `<tr><td class="ticker"><a href="#/p/${esc(x.symbol)}">${esc(x.symbol)}</a></td><td>${esc(x.pattern)}</td>
          <td>${scorebar(x.score)}</td><td class="num">${fmt(x.plan.entry_limit)}</td><td class="num">${fmt(x.plan.stop)}</td>
          <td class="num">${x.plan.partial_price ? fmt(x.plan.partial_price) : "–"}</td><td class="num">${fmt(x.plan.target)}</td><td class="num">${x.plan.shares}</td>
          <td>${x.sent ? `<span class="badge go">beküldve</span>` : sendButton(x)}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="card-body empty">Ma nincs beküldhető belépési jelzés (csak a megerősített kitörés / visszateszt küldhető, az előkészület nem).</div>`}
    </section>

    <section class="card"><h2>A rendszer paper trade-jei</h2>
      ${d.trades.length ? `<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Jelzés</th><th>Állapot</th><th class="num">Db</th>
        <th class="num">Limit</th><th class="num">Belépő</th><th class="num">Stop</th><th class="num">½ cél</th><th class="num">Cél</th><th>Lábak</th><th class="num">Eredmény</th><th></th></tr></thead><tbody>
        ${d.trades.map(tradeRow).join("")}</tbody></table></div>`
        : `<div class="card-body empty">Még nincs paper trade. Küldj be egy jelzést fent, vagy kapcsold be az automata beküldést a Beállításokban.</div>`}
    </section>

    <div class="grid-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <section class="card"><h2>Pozíciók az Alpaca számlán</h2>
        ${d.positions.length ? `<div class="table-wrap"><table><thead><tr><th>Ticker</th><th class="num">Db</th><th class="num">Átlagár</th><th class="num">Ár</th><th class="num">Eredmény</th></tr></thead><tbody>
          ${d.positions.map((p) => `<tr><td class="ticker">${esc(p.symbol)}</td><td class="num">${esc(p.qty)}</td><td class="num">${fmt(+p.avg_entry_price)}</td>
            <td class="num">${fmt(+p.current_price)}</td><td class="num ${+p.unrealized_pl >= 0 ? "up" : "down"}">${signed(+p.unrealized_pl, 0, " $")} <span class="muted small">${pct(+p.unrealized_plpc * 100, 1)}</span></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="card-body empty">Nincs nyitott pozíció.</div>`}
      </section>
      <section class="card"><h2>Nyitott megbízások</h2>
        ${d.orders.length ? `<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Irány</th><th>Típus</th><th class="num">Db</th><th class="num">Ár</th><th>Állapot</th></tr></thead><tbody>
          ${d.orders.map((o) => `<tr><td class="ticker">${esc(o.symbol)}</td><td>${o.side === "buy" ? "vétel" : "eladás"}</td><td>${esc(o.type)}${o.order_class === "bracket" ? " (bracket)" : ""}</td>
            <td class="num">${esc(o.qty)}</td><td class="num">${fmt(+(o.limit_price || o.stop_price))}</td><td class="muted small">${esc(o.status)}</td></tr>`).join("")}</tbody></table></div>`
          : `<div class="card-body empty">Nincs nyitott megbízás.</div>`}
      </section>
    </div>
    <div class="muted small">Alpaca PAPER számla – nem valódi pénz. A „Szinkronizálás” lekéri a teljesüléseket, nullszaldóra húzza a stopot a ½ eladás után, és visszavonja a le nem futott belépőket. A monitor ezt 15 percenként magától is megteszi.</div>`;

  $("#bk-sync").addEventListener("click", () => brokerAction("sync", {}));
  bindPaperSwitch(() => pageBroker());
  bindBrokerButtons();
  const eq = $("#equity-chart");
  if (eq) {
    const draw = () => drawEquity(eq, d.history);
    draw();
    if (chartRO) chartRO.disconnect();
    chartRO = new ResizeObserver(() => draw());
    chartRO.observe(eq.parentElement);
  }
}

const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

function pnlKpis(d, a, s, active) {
  const p = d.pnl;
  const c = d.capital;
  const dir = (v) => (v > 0 ? "▲ " : v < 0 ? "▼ " : "");   // az irany ne csak szinnel latszodjon
  const pending = active.filter((t) => t.status === "submitted").length;
  return `<div class="kpis">
    <div class="kpi pnl-hero" title="A paper számla eredménye a nyitás óta: mostani equity mínusz a kezdő érték. A számlán kézzel nyitott trade-ek is benne vannak.">
      <div class="label">Eddigi eredmény (számla)</div>
      <div class="value ${tone(p && p.account_total)}">${p && p.account_total != null ? dir(p.account_total) + signed(p.account_total, 0, " $") : "–"}</div>
      <div class="sub">${p && p.base ? `${fmt(p.base, 0)} $ → ${fmt(p.equity, 0)} $ (${signed(p.account_total_pct, 2, "%")}) · ma ${signed(p.today, 0, " $")}` : "a számlatörténet nem érhető el"}</div></div>
    <div class="kpi" title="Csak a StockWatch által beküldött trade-ek: a lezártak realizált eredménye + a nyitottak jelenlegi (nem realizált) eredménye.">
      <div class="label">A rendszer trade-jei</div>
      <div class="value ${tone(p && p.system_total)}">${p ? dir(p.system_total) + signed(p.system_total, 0, " $") : "–"}</div>
      <div class="sub">${p ? `realizált ${signed(p.system_realised, 0, " $")} (${p.closed_trades} lezárt) · nyitott ${signed(p.system_unrealised, 0, " $")} (${p.open_trades})` : ""}${s.n ? ` · nyerő ${fmt(s.win * 100, 0)}%` : ""}</div></div>
    <div class="kpi" title="Pozíciókban lévő tőke a számla értékéhez képest. A rendszer csak a szabad készpénzből vesz (készpénz − függő vételek), az Alpaca 4x-es margin vételi erejét nem használja.">
      <div class="label">Lekötött tőke</div><div class="value">${c ? `${fmt(c.invested, 0)} $ <span class="muted" style="font-size:.6em">${fmt(c.invested_pct, 1)}%</span>` : "–"}</div>
      <div class="sub">${c ? `szabad keret ${fmt(c.free, 0)} $${c.pending ? ` · függő vétel ${fmt(c.pending, 0)} $` : ""} · számla ${fmt(c.equity, 0)} $` : ""}</div></div>
    <div class="kpi"><div class="label">Aktív trade</div><div class="value">${active.length} / ${d.settings.max_open_trades}</div>
      <div class="sub">${pending ? `ebből ${pending} függő (belépésre vár) · ` : ""}automata beküldés: <strong>${d.settings.auto_trade ? "BE" : "KI"}</strong> · kilépés: ${esc(d.exit_mode)}</div></div>
  </div>`;
}

// A szamla osszetetele most: pozicionkent, fuggo vetel, szabad keret - egy savon
function capitalCard(c) {
  if (!c || !c.equity) return "";
  const parts = [
    ...c.positions.map((p) => ({ cls: "cap-pos", label: p.symbol, value: p.value, pct: p.pct })),
    ...(c.pending > 0 ? [{ cls: "cap-pending", label: "függő vétel", value: c.pending, pct: c.pending_pct }] : []),
    { cls: "cap-free", label: "szabad keret", value: c.free, pct: c.free_pct },
  ];
  return `<section class="card"><h2>A számla összetétele most <span class="muted">– ${fmt(c.equity, 0)} $ számlaérték, margin nélkül</span></h2>
    <div class="card-body">
      <div class="cap-bar" role="img" aria-label="${esc(parts.map((x) => `${x.label} ${fmt(x.pct, 1)}%`).join(", "))}">
        ${parts.map((x) => `<span class="${x.cls}" style="width:${Math.max(0, Math.min(100, x.pct || 0))}%" title="${esc(x.label)}: ${fmt(x.value, 0)} $ (${fmt(x.pct, 1)}%)"></span>`).join("")}</div>
      <div class="cap-legend">${parts.map((x) => `<span><i class="${x.cls}"></i>${esc(x.label)} <b>${fmt(x.value, 0)} $</b> <span class="muted">${fmt(x.pct, 1)}%</span></span>`).join("")}</div>
      <p class="muted small">A vétel a számla értékét nem változtatja (készpénzből részvény lesz) – a görbén csak az árfolyam-változás látszik, a lekötött tőke itt.
        Az Alpaca „buying power” (${fmt(c.buying_power, 0)} $) a 4x-es margint is tartalmazza; a rendszer csak a szabad keretből vesz.</p>
    </div></section>`;
}

function equityCard(h) {
  if (!h || h.t.length < 2) {
    return `<section class="card"><h2>Számla értéke a nyitás óta</h2><div class="card-body empty">
      A görbe az első lezárt kereskedési napok után rajzolódik ki${h ? ` (eddig ${h.t.length} nap)` : ""}.</div></section>`;
  }
  return `<section class="card chart-card"><h2>Számla értéke a nyitás óta <span class="muted">– napi záró equity${h.live ? ", az utolsó pont a mai élő érték" : ""}, szaggatott vonal: kezdő érték (${fmt(h.base, 0)} $)</span></h2>
    <div class="card-body"><svg id="equity-chart" role="img" aria-label="A paper számla equity-görbéje"></svg></div>
    <details class="card-body" style="margin-top:0"><summary>Táblázat</summary><div class="table-wrap"><table>
      <thead><tr><th>Nap</th><th class="num">Equity</th><th class="num">Eredmény a nyitás óta</th></tr></thead>
      <tbody>${h.t.map((t, i) => `<tr><td>${esc(t)}${h.live && i === h.t.length - 1 ? " (most)" : ""}</td><td class="num">${fmt(h.equity[i], 2)} $</td>
        <td class="num ${tone(h.equity[i] - h.base)}">${signed(h.equity[i] - h.base, 2, " $")}</td></tr>`).reverse().join("")}</tbody></table></div></details>
  </section>`;
}

function drawEquity(svg, h) {
  const W = Math.max(320, svg.parentElement.clientWidth);
  const H = 220;
  const pad = { l: 8, r: 70, t: 14, b: 24 };
  const n = h.equity.length;
  let lo = Math.min(h.base, ...h.equity), hi = Math.max(h.base, ...h.equity);
  const span = hi - lo || Math.max(1, hi * 0.01);
  lo -= span * 0.12; hi += span * 0.12;
  const x = (i) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
  const ticks = niceTicks(lo, hi, 4);
  const parts = [];
  parts.push(`<g class="grid">${ticks.map((t) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`);
  parts.push(`<g class="axis">${ticks.map((t) => `<text x="${W - pad.r + 8}" y="${y(t) + 4}">${fmt(t, 0)}</text>`).join("")}</g>`);
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(W / 110))));
  parts.push(`<g class="axis">${h.t.map((t, i) => (i % every === 0 ? `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : "middle"}">${t.slice(5)}</text>` : "")).join("")}</g>`);
  parts.push(`<line class="eq-base" x1="${pad.l}" x2="${W - pad.r}" y1="${y(h.base)}" y2="${y(h.base)}"/>`);
  const line = h.equity.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  parts.push(`<path class="eq-area" d="${line}L${x(n - 1).toFixed(1)},${y(lo)}L${x(0).toFixed(1)},${y(lo)}Z"/>`);
  parts.push(`<path class="eq-line" d="${line}"/>`);
  const last = h.equity[n - 1];
  parts.push(`<circle class="eq-dot" cx="${x(n - 1)}" cy="${y(last)}" r="4"/>`);
  parts.push(`<g id="eq-cross"></g><rect id="eq-hit" x="${pad.l}" y="${pad.t}" width="${W - pad.l - pad.r}" height="${H - pad.t - pad.b}" fill="transparent"/>`);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");

  const hit = svg.querySelector("#eq-hit"), cross = svg.querySelector("#eq-cross");
  const move = (clientX, clientY) => {
    const r = svg.getBoundingClientRect();
    const px = (clientX - r.left) * (W / r.width);
    const i = Math.max(0, Math.min(n - 1, Math.round((px - pad.l) / ((W - pad.l - pad.r) / Math.max(1, n - 1)))));
    const v = h.equity[i];
    cross.innerHTML = `<line class="cross" x1="${x(i)}" x2="${x(i)}" y1="${pad.t}" y2="${H - pad.b}"/><circle class="eq-dot" cx="${x(i)}" cy="${y(v)}" r="5"/>`;
    tooltip.innerHTML = `<strong>${esc(h.t[i])}${h.live && i === n - 1 ? " – most" : ""}</strong><div class="row"><span>Equity</span><span>${fmt(v, 2)} $</span></div>
      <div class="row"><span>A nyitás óta</span><span class="${tone(v - h.base)}">${signed(v - h.base, 2, " $")} (${signed((v / h.base - 1) * 100, 2, "%")})</span></div>`;
    tooltip.hidden = false;
    const tx = clientX + 16 + 200 > window.innerWidth ? clientX - 216 : clientX + 16;
    tooltip.style.left = `${tx}px`;
    tooltip.style.top = `${clientY + 12}px`;
  };
  hit.addEventListener("mousemove", (ev) => move(ev.clientX, ev.clientY));
  hit.addEventListener("touchstart", (ev) => { const t = ev.touches[0]; move(t.clientX, t.clientY); }, { passive: true });
  hit.addEventListener("mouseleave", () => { cross.innerHTML = ""; tooltip.hidden = true; });
}

function sendButton(s) {
  return `<button class="btn primary" type="button" data-bk="submit" data-symbol="${esc(s.symbol)}" data-kind="${esc(s.kind)}" data-pattern="${esc(s.pattern)}">Küldés a paper számlára</button>`;
}

function tradeRow(t) {
  const [cls, label] = BT_STATUS[t.status] || ["fail", t.status];
  const legs = t.legs.map((l) => `${l.name === "partial" ? "½" : "cél"} ${l.qty} db${l.filled_qty ? ` · ${l.filled_qty} teljesült` : ""}${l.exit_reason ? ` → ${LEG_EXIT[l.exit_reason] || l.exit_reason}${l.exit_price ? ` @ ${fmt(l.exit_price)}` : ""}${l.exit_ts ? ` · ${when(l.exit_ts)}` : ""}` : ""}`).join("<br>");
  return `<tr><td class="ticker"><a href="#/p/${esc(t.symbol)}">${esc(t.symbol)}</a></td><td>${esc(t.signal_date)}<div class="muted small">beküldve ${when(t.submitted_at) || "–"}</div><div class="muted small">${esc(t.pattern)}</div><div>${strategyBadge(t.strategy)}</div></td>
    <td><span class="badge ${cls}">${label}</span>${t.breakeven_done ? `<div class="muted small">stop a belépőn</div>` : ""}${t.waiting ? waitingNote(t) : ""}</td>
    <td class="num">${t.shares}</td><td class="num">${fmt(t.entry_limit)}</td><td class="num">${t.entry_price ? `${fmt(t.entry_price)}<div class="muted small">${when(t.filled_ts) || esc(t.filled_at || "")}</div>` : "–"}</td>
    <td class="num">${fmt(t.stop)}</td><td class="num">${t.partial_price ? fmt(t.partial_price) : "–"}</td><td class="num">${fmt(t.target)}</td>
    <td class="small">${t.signal_id ? `${signalLink(t.signal_id)}<br>` : ""}${legs}${t.notes.length ? `<details><summary>napló</summary><div class="small muted">${t.notes.map(esc).join("<br>")}</div></details>` : ""}</td>
    <td class="num ${tone(t.realised_pnl != null ? t.realised_pnl : t.unrealized_pl)}">${t.realised_pnl != null ? signed(t.realised_pnl, 2, " $")
      : t.unrealized_pl != null ? `${signed(t.unrealized_pl, 2, " $")}<div class="muted small">nyitott · ár ${fmt(t.current_price)}</div>` : "–"}</td>
    <td>${t.status === "submitted" ? `<button class="btn" type="button" data-bk="close" data-id="${esc(t.id)}" data-confirm="Visszavonod a(z) ${esc(t.symbol)} belépő megbízást?">Visszavonás</button>`
      : t.status === "open" ? `<button class="btn danger" type="button" data-bk="close" data-id="${esc(t.id)}" data-confirm="Lezárod a(z) ${esc(t.symbol)} paper pozíciót piaci áron?">Zárás most</button>` : ""}</td></tr>`;
}

// Miert var meg a belepo: hol a limit, meddig ervenyes, mennyire van tole az utolso ar
function waitingNote(t) {
  const w = t.waiting;
  const day = esc(w.entry_day);
  const rule = t.exit_mode === "indicator"
    ? `A vételi limit a jelzés napi záró alatt van${w.limit_pct != null ? ` (−${fmt(w.limit_pct, 0)}%)` : ""}: csak akkor vesz, ha ${day} folyamán az ár ${fmt(t.entry_limit)} $-ig esik. Ha nem, a nap végén visszavonja.`
    : `Csak ${day} nyitásakor teljesülhet (ha a nyitó ár ≤ ${fmt(t.entry_limit)} $). Ha nem, a nyitás után pár perccel visszavonja.`;
  const gap = w.last_price != null
    ? `Utolsó ismert ár: ${fmt(w.last_price)} $ (${esc(w.last_date)}) – ${w.gap_pct > 0 ? `${fmt(w.gap_pct, 1)}%-kal a limit felett` : "a limiten vagy alatta"}.`
    : "";
  return `<div class="muted small" style="max-width:260px;margin-top:4px">${rule} ${gap}</div>`;
}

function bindBrokerButtons() {
  view.querySelectorAll("button[data-bk]").forEach((btn) => btn.addEventListener("click", () => {
    if (btn.dataset.confirm && !confirm(btn.dataset.confirm)) return;
    const body = btn.dataset.bk === "submit"
      ? { symbol: btn.dataset.symbol, kind: btn.dataset.kind, pattern: btn.dataset.pattern }
      : { id: btn.dataset.id };
    brokerAction(btn.dataset.bk, body, btn);
  }));
}

async function brokerAction(action, body, btn = null) {
  if (btn) btn.disabled = true;
  try {
    const res = await api(`/api/broker/${action}`, { method: "POST", body: JSON.stringify(body) });
    if (location.hash.startsWith("#/broker")) renderBroker(res, res.message);
    else { alert(res.message); route(); }
  } catch (e) {
    alert(e.message);
    if (btn) btn.disabled = false;
  }
}

async function bindAlpacaForm() {
  const status = $("#alpaca-status");
  const msg = $("#alpaca-msg");
  const show = (c) => {
    status.className = `msg ${c.configured ? "ok" : ""}`;
    status.textContent = c.configured ? `Beállítva – kulcs ${c.key_hint}, ${c.endpoint}` : "Még nincs beállítva.";
    $("#a-key").placeholder = c.configured ? `beállítva (${c.key_hint}) – üresen hagyva nem változik` : "PK…";
    $("#a-secret").placeholder = c.secret_set ? "beállítva – üresen hagyva nem változik" : "";
    if (c.endpoint) $("#a-endpoint").value = c.endpoint;
  };
  try { show((await api("/api/broker")).credentials); } catch (e) { status.textContent = e.message; }

  $("#alpaca-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg.className = "msg"; msg.textContent = "Kapcsolódás…";
    try {
      const res = await api("/api/broker/credentials", { method: "POST", body: JSON.stringify({
        endpoint: $("#a-endpoint").value.trim(), key: $("#a-key").value.trim(), secret: $("#a-secret").value.trim() }) });
      $("#a-key").value = ""; $("#a-secret").value = "";
      show(res.credentials);
      msg.className = "msg ok"; msg.textContent = `Mentve – a paper számla állapota: ${res.account_status}`;
      overview = null;
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  });
  $("#alpaca-test").addEventListener("click", async () => {
    msg.className = "msg"; msg.textContent = "Tesztelés…";
    try {
      const res = await api("/api/broker/test", { method: "POST", body: "{}" });
      msg.className = "msg ok"; msg.textContent = `Rendben – ${res.account_status}, equity ${fmt(+res.equity, 0)} $`;
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  });
  $("#alpaca-forget").addEventListener("click", async () => {
    if (!confirm("Törlöd az Alpaca kulcsokat a .env-ből? A meglévő paper megbízások az Alpacán maradnak.")) return;
    const res = await api("/api/broker/forget", { method: "POST", body: "{}" });
    show(res.credentials);
    msg.className = "msg ok"; msg.textContent = "Kulcsok törölve.";
    overview = null;
  });
}

// ---------------------------------------------------------------------------
// Jelzesnaplo es egy jelzes eletutja
// ---------------------------------------------------------------------------
const DECISION = {
  alerted: ["go", "Kiküldve", "Minden szűrőn átment, a pontszám a küszöb felett – az értesítés kiment."],
  skipped: ["warn", "Kihagyva", "Megfelelt volna, de nem ment ki (irány kikapcsolva, ismétlésvédelem, már van nyitott trade, elnémított előkészület)."],
  rejected: ["fail", "Elutasítva", "Nem felelt meg: valamelyik kemény szűrő elbukott, vagy a pontszám a küszöb alatt volt."],
  confirmed: ["go", "Megerősítve", "Nyitás előtti ellenőrzés: a véglegesített napi adaton is megáll."],
  revoked: ["bad", "Visszavonva", "Nyitás előtti ellenőrzés: a véglegesített napi adaton már nem áll meg – a függő paper megbízás törölve."],
  manual: ["go", "Kézzel beküldve", "A Paper trading oldalról kézzel beküldött jelzés (a monitor értesítése nélkül) – a döntéskori adattal együtt naplózva."],
};
let ledgerFilter = { decision: "", symbol: "", strategy: "" };
const signalLink = (id, text = "életút →") => (id ? `<a href="#/signal/${esc(id)}" class="small" onclick="event.stopPropagation()">${text}</a>` : "");

async function pageLedger() {
  await loadOverview();
  view.innerHTML = `<div class="loading">Napló betöltése…</div>`;
  const q = new URLSearchParams({ limit: "500" });
  if (ledgerFilter.decision) q.set("decision", ledgerFilter.decision);
  if (ledgerFilter.symbol) q.set("symbol", ledgerFilter.symbol);
  if (ledgerFilter.strategy) q.set("strategy", ledgerFilter.strategy);
  const d = await api(`/api/ledger?${q}`);
  const c = d.counts;
  const seg = (value, label, title = "") => `<button type="button" class="seg ${ledgerFilter.decision === value ? "on" : ""}" data-decision="${value}" ${title ? `title="${esc(title)}"` : ""}>${label}</button>`;
  view.innerHTML = `
    <div class="page-head"><h1>Jelzésnapló</h1>
      <span class="muted">Minden lezárt gyertyán kiértékelt belépési jelölt – a kiküldöttek, a kihagyottak és az elutasítottak is, okkal, beállítás- és kódverzióval.</span></div>
    <div class="kpis">
      <div class="kpi" title="${esc(DECISION.alerted[2])}"><div class="label">Kiküldve</div><div class="value">${c.alerted || 0}</div><div class="sub">${c.manual ? `+ ${c.manual} kézzel beküldve · ` : ""}teljes adat-pillanatképpel</div></div>
      <div class="kpi" title="${esc(DECISION.skipped[2])}"><div class="label">Kihagyva</div><div class="value">${c.skipped || 0}</div><div class="sub">megfelelt volna</div></div>
      <div class="kpi" title="${esc(DECISION.rejected[2])}"><div class="label">Elutasítva</div><div class="value">${c.rejected || 0}</div><div class="sub">szűrő vagy pontszám miatt</div></div>
      <div class="kpi"><div class="label">Kódverzió most</div><div class="value" style="font-size:15px;font-family:var(--mono)">${esc(d.code)}</div>
        <div class="sub">minden bejegyzés a saját verzióját hordozza</div></div>
    </div>
    <div class="filters">
      <div class="segmented" role="group" aria-label="Döntés">${seg("", "Mind")}${seg("alerted", "Kiküldve", DECISION.alerted[2])}${seg("skipped", "Kihagyva", DECISION.skipped[2])}${seg("rejected", "Elutasítva", DECISION.rejected[2])}${seg("confirmed", "Megerősítve", DECISION.confirmed[2])}${seg("revoked", "Visszavonva", DECISION.revoked[2])}${seg("manual", "Kézi", DECISION.manual[2])}</div>
      <form id="ledger-sym" class="with-info"><input type="text" id="ledger-symbol" value="${esc(ledgerFilter.symbol)}" placeholder="Ticker…" style="width:110px;font:inherit;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);text-transform:uppercase"></form>
      ${strategySeg(ledgerFilter.strategy, "data-lstrategy")}
      <span class="muted small">${d.rows.length} bejegyzés</span>
    </div>
    <section class="card">
      ${d.rows.length ? `<div class="table-wrap"><table><thead><tr><th>Nap</th><th>Ticker</th><th>Stratégia</th><th>Helyzet</th><th>Alakzat</th><th>Pont</th><th>Döntés</th><th>Ok</th><th title="Mi lett a jelzésből – ugyanaz az állapot, mint a Jelzett trade-ek oldalon">Trade</th><th>Forrás</th><th></th></tr></thead><tbody>
        ${d.rows.map((r) => {
          const [cls, label, help] = DECISION[r.decision] || ["fail", r.decision, ""];
          const k = KIND[r.kind] || { text: r.kind, cls: "fail" };
          return `<tr class="clickable" data-href="#/signal/${esc(r.signal_id)}">
            <td>${esc(r.as_of)}${r.recorded_at ? `<div class="muted small">${when(r.recorded_at)}</div>` : ""}</td><td class="ticker">${esc(r.symbol)}</td><td>${strategyBadge(r.strategy)}</td>
            <td><span class="badge ${k.cls}">${k.text}</span></td>
            <td><span class="with-info">${esc(r.pattern)}${infoButton(r.pattern, "bullish", r.kind, statsFor(r.pattern, "bullish"))}</span></td>
            <td>${scorebar(r.score)}</td>
            <td><span class="badge ${cls} has-help" title="${esc(help)}">${label}</span></td>
            <td class="reason" style="max-width:360px">${esc(r.reason)}</td>
            <td>${ledgerTrade(r.trade)}</td>
            <td class="muted small">${r.mode === "index" ? "index" : "watchlist"}${r.snapshot ? " · 📷" : ""}</td>
            <td>${signalLink(r.signal_id)}</td></tr>`;
        }).join("")}</tbody></table></div>`
        : `<div class="card-body empty">Még nincs bejegyzés. A napló a monitor zárás utáni futásakor (és az indexszkenneléskor) töltődik – minden belépési jelöltről, nem csak a kiküldöttekről.</div>`}
    </section>
    <section class="card"><h2>Beállítás-változások <span class="muted">– ki, mikor, mit</span></h2>
      ${d.changes.length ? `<div class="table-wrap"><table><thead><tr><th>Időpont</th><th>Forrás</th><th>Változás</th><th>Beállítás-verzió</th></tr></thead><tbody>
        ${d.changes.map((ch) => `<tr><td class="muted small">${esc(new Date(ch.at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }))}</td>
          <td>${esc(ch.source)}</td>
          <td class="small">${Object.entries(ch.changes).map(([key, v]) => Array.isArray(v)
            ? `<code>${esc(key)}</code>: ${esc(JSON.stringify(v[0]))} → <strong>${esc(JSON.stringify(v[1]))}</strong>`
            : `<code>${esc(key)}</code>: ${v.hozzaadva && v.hozzaadva.length ? `+${esc(v.hozzaadva.join(", "))} ` : ""}${v.torolve && v.torolve.length ? `−${esc(v.torolve.join(", "))}` : ""}`).join("<br>")}</td>
          <td class="muted small" style="font-family:var(--mono)">${esc(ch.config || "")}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="card-body empty">Még nem változott beállítás a felületről.</div>`}
    </section>`;
  view.querySelectorAll("button[data-decision]").forEach((b) => b.addEventListener("click", () => { ledgerFilter.decision = b.dataset.decision; pageLedger(); }));
  view.querySelectorAll("button[data-lstrategy]").forEach((b) => b.addEventListener("click", () => { ledgerFilter.strategy = b.dataset.lstrategy; pageLedger(); }));
  $("#ledger-sym").addEventListener("submit", (ev) => { ev.preventDefault(); ledgerFilter.symbol = $("#ledger-symbol").value.trim().toUpperCase(); pageLedger(); });
  bindRowLinks();
}

function ledgerTrade(t) {
  if (!t) return `<span class="muted">–</span>`;
  const [cls, label] = posStatus(t);
  const result = t.result_r != null ? ` <span class="${t.result_r > 0 ? "up" : t.result_r < 0 ? "down" : ""}">${signed(t.result_r, 2, "R")}</span>` : "";
  return `<a href="#/positions" onclick="event.stopPropagation()"><span class="badge ${cls}">${label}</span></a>${result}<div>${posSource(t)}</div>`;
}

async function pageSignal(signalId) {
  await loadOverview();
  view.innerHTML = `<div class="loading">Jelzés betöltése…</div>`;
  const t = await api(`/api/signal/${encodeURIComponent(signalId)}`);
  const rec = t.records.find((r) => r.decision === "alerted" || r.decision === "manual") || t.records[0];
  if (!rec && !t.alerts.length) {
    view.innerHTML = `<div class="page-head"><a href="#/ledger" class="muted">← Jelzésnapló</a><h1>${esc(signalId)}</h1></div>
      <section class="card"><div class="card-body empty">Ez a jelzés nincs a naplóban (a napló bevezetése előtti jelzés, vagy már kitakarítva).</div></section>`;
    return;
  }
  const k = rec ? (KIND[rec.kind] || { text: rec.kind, cls: "fail" }) : null;
  const checks = (rec && rec.checks || []).map((c) => {
    const [cls, mark] = c.passed === true ? ["ok", "✓"] : c.passed === false ? ["no", "✗"] : ["unk", "?"];
    return `<li><span class="mark ${cls}">${mark}</span><span><strong>${esc(c.name)}</strong> <span class="muted">${esc(c.detail)}</span></span></li>`;
  }).join("");
  const parts = (rec && rec.score_parts || []).map((x) => `<div class="part"><span>${esc(x.name)}</span><span class="track"><span class="fill" style="width:${(x.points / x.max) * 100}%"></span></span>
    <span class="muted small">${fmt(x.points, 1)}/${x.max} · ${esc(x.reason)}</span></div>`).join("");
  const p = rec && rec.plan;
  const rv = t.review;
  const [gcls, glabel] = rv ? (GRADE[rv.grade] || GRADE.na) : GRADE.na;
  const timeline = [];
  t.records.slice().reverse().forEach((r) => {
    const [cls, label] = DECISION[r.decision] || ["fail", r.decision];
    timeline.push([r.recorded_at, `<span class="badge ${cls}">Napló: ${label}</span> ${esc(r.reason)} <span class="muted small">(${esc(r.source)})</span>`]);
  });
  t.alerts.forEach((a) => {
    const [cls, label] = EVENT[a.event] || ["fail", a.event];
    timeline.push([a.timestamp, `<span class="badge ${cls === "info" ? "watch" : cls}">${esc(label)}</span> ${esc(a.title)}`]);
  });
  if (t.position) timeline.push([t.position.entry_date || t.position.signal_date, `<span class="badge watch">Virtuális pozíció</span> ${esc(POS_STATUS[t.position.status] ? POS_STATUS[t.position.status][1] : t.position.status)}${t.position.result_r != null ? ` · ${signed(t.position.result_r, 2, "R")}` : ""} ${esc(t.position.reason || "")}`]);
  if (t.trade) timeline.push([t.trade.submitted_at, `<span class="badge watch">Paper trade</span> ${esc(BT_STATUS[t.trade.status] ? BT_STATUS[t.trade.status][1] : t.trade.status)} · ${t.trade.shares} db${t.trade.realised_pnl != null ? ` · ${signed(t.trade.realised_pnl, 2, " $")}` : ""}`]);
  timeline.sort((a, b) => String(a[0]).localeCompare(String(b[0])));

  view.innerHTML = `
    <div class="page-head"><a href="#/ledger" class="muted">← Jelzésnapló</a>
      <h1>${rec ? `<a href="#/p/${esc(rec.symbol)}">${esc(rec.symbol)}</a>` : ""} <span class="muted" style="font-size:15px;font-family:var(--mono)">${esc(signalId)}</span></h1>
      ${rec ? `<span class="badge ${k.cls}">${k.text}</span><span class="with-info"><strong>${esc(rec.pattern.name)}</strong>${infoButton(rec.pattern.name, rec.pattern.direction, rec.kind, statsFor(rec.pattern.name, rec.pattern.direction))}</span>
      <span class="muted">jelzés napja: ${esc(rec.as_of)}</span>` : ""}
      <span style="margin-left:auto"></span><button class="btn primary" type="button" id="do-replay" title="A naplózott beállítással, kalibrációval és adattal újraszámolja a döntést, és összeveti az eredetivel">Visszajátszás</button></div>
    <div id="replay-result"></div>
    <div class="grid-2">
      <div>
        <section class="card"><h2>Életút</h2><div class="card-body"><ol class="timeline">
          ${timeline.map(([at, html]) => `<li><span class="muted small">${esc(at ? new Date(at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }) : "–")}</span><div>${html}</div></li>`).join("")}
        </ol></div></section>
        ${rec ? `<section class="card"><h2>Szűrők és pontszám a döntéskor <span class="muted">– ${fmt(rec.score, 1)}/100</span></h2><div class="card-body">
          <ul class="checks">${checks}</ul><div class="parts">${parts}</div>
          <div class="muted small" style="margin-top:8px">Történeti: ${esc(rec.history)}${rec.earnings_date ? ` · Jelentés: ${esc(rec.earnings_date)}` : ""}</div></div></section>` : ""}
      </div>
      <aside>
        ${rv ? `<section class="card"><h2>Mi lett belőle?</h2><div class="card-body">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px"><span class="badge ${gcls}">${glabel}</span><span>${esc(rv.outcome)}</span></div>
          <dl class="stat-list"><dt>Jelzéskor</dt><dd>${fmt(rv.signal_price)} $</dd><dt>Most</dt><dd>${fmt(rv.now_price)} $</dd>
            <dt>Változás</dt><dd class="${tone(rv.change_pct)}">${rv.change_pct != null ? signed(rv.change_pct, 1, "%") : "–"}</dd>
            <dt>Eredmény</dt><dd class="${tone(rv.r)}">${rv.r != null ? signed(rv.r, 2, "R") : "–"}${rv.pnl_usd != null ? ` · ${signed(rv.pnl_usd, 0, " $")}` : ""}</dd></dl>
          ${rec && rec.decision !== "alerted" ? `<div class="muted small" style="margin-top:8px">Ez a jelölt nem ment ki – az értékelés azt mutatja, mi lett volna, ha kimegy.</div>` : ""}
        </div></section>` : ""}
        ${p ? `<section class="card"><h2>Terv a döntéskor</h2><div class="card-body"><div class="plan">
          <span class="k">Belépő / limit</span><span class="v">${fmt(p.entry)} / ${fmt(p.entry_limit)}</span><span class="p"></span>
          <span class="k">Stop</span><span class="v down">${fmt(p.stop)}</span><span class="p">−${fmt(p.stop_pct, 1)}%</span>
          ${p.partial_price ? `<span class="k">½ eladás</span><span class="v">${fmt(p.partial_price)}</span><span class="p"></span>` : ""}
          <span class="k">Cél</span><span class="v up">${fmt(p.target)}</span><span class="p">+${fmt(p.target_pct, 1)}%</span>
          <span class="k">Méret</span><span class="v">${p.shares} db</span><span class="p">R/R 1:${fmt(p.risk_reward)}</span></div></div></section>` : ""}
        ${rec ? `<section class="card"><h2>Verziók és adat</h2><div class="card-body"><dl class="stat-list">
          <dt>Beállítás-verzió</dt><dd style="font-family:var(--mono)">${esc(rec.versions.config)}</dd>
          <dt>Kódverzió</dt><dd style="font-family:var(--mono)">${esc(rec.versions.code)}</dd>
          <dt>Kalibráció</dt><dd style="font-family:var(--mono)">${esc(rec.versions.calibration)}</dd>
          <dt>Gyertyák</dt><dd>${rec.data.bars} (${esc(rec.data.first)} → ${esc(rec.data.last)})</dd>
          <dt>Adat-ujjlenyomat</dt><dd style="font-family:var(--mono)">${esc(rec.data.window_hash)}</dd>
          <dt>Pillanatkép</dt><dd>${rec.snapshot ? "van – bitre visszajátszható" : "nincs – a mostani adatból"}</dd>
          <dt>Forrás</dt><dd>${esc(rec.source)} · ${esc(rec.mode)}</dd></dl></div></section>` : ""}
      </aside>
    </div>`;
  $("#do-replay").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const box = $("#replay-result");
    btn.disabled = true;
    box.innerHTML = `<div class="callout">Visszajátszás…</div>`;
    try {
      const r = await api(`/api/replay/${encodeURIComponent(signalId)}`, { method: "POST", body: "{}" });
      const ok = r.found && !r.diffs.length;
      box.innerHTML = `<section class="card"><div class="card-body" style="padding-top:14px">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="badge ${ok ? "go" : "bad"}">${ok ? "Reprodukálható" : "Eltérés"}</span><strong>${esc(r.verdict)}</strong></div>
        <div class="muted small" style="margin-top:6px">Adatforrás: ${esc(r.source)} · azonos adatablak: ${r.same_data ? "igen" : "nem"} · kód akkor: <code>${esc(r.code_then)}</code>, most: <code>${esc(r.code_now)}</code></div>
        ${r.notes.map((n) => `<div class="muted small">Megjegyzés: ${esc(n)}</div>`).join("")}
        ${r.diffs.length ? `<table style="margin-top:8px"><thead><tr><th>Mező</th><th>Akkor</th><th>Most</th></tr></thead><tbody>${r.diffs.map((d) => `<tr><td>${esc(d.field)}</td><td>${esc(JSON.stringify(d.then))}</td><td>${esc(JSON.stringify(d.now))}</td></tr>`).join("")}</tbody></table>` : ""}
      </div></section>`;
    } catch (e) { box.innerHTML = `<div class="callout" style="color:var(--down)">${esc(e.message)}</div>`; }
    btn.disabled = false;
  });
}

// ---------------------------------------------------------------------------
// Ertesitesek - utolagos minositessel
// ---------------------------------------------------------------------------
// [jelveny-osztaly, rovid nev, magyarazat (egerrel ravive latszik)]
const EVENT = {
  new: ["fail", "Új alakzat", "A rendszer egy még formálódó chart-alakzatot talált (pl. zászló, dupla alj). Még nem tört ki – ez figyelmeztetés, nem vételi vagy eladási jelzés."],
  triggered: ["watch", "Kitörés", "Az alakzat kitört a trigger szinten: bikás mintánál felfelé (long), medvésnél lefelé (short). Innen számít aktívnak."],
  target_hit: ["go", "Célár", "Egy korábban kitört alakzat elérte a mért célárat – ez egy korábbi jelzés kimenete, nem új jelzés."],
  failed: ["bad", "Megbukott", "Az ár elérte az alakzat érvénytelenítési szintjét – a korábbi jelzés nem jött be."],
  entry_breakout: ["go", "Belépés", "Megerősített kitörés: a minta tegnap tört ki, ma is fölötte zárt, és minden szűrő átment. Kész long terv: belépő-limit, stop, ½ eladás +1R-nél, cél, darabszám."],
  entry_retest: ["go", "Belépés (visszateszt)", "Kitörés után az ár visszajött a szinthez, megtartotta, és felfelé fordult – long belépési jelzés kész tervvel."],
  entry_revoked: ["bad", "Visszavonva", "Egy előző esti belépési jelzés a nyitás előtti ellenőrzésen (véglegesített adaton) már nem állt meg – ne vegyél rá. A függő paper megbízás törölve."],
  entry_setup: ["watch", "Előkészület", "Az alakzat még nem tört ki, de legfeljebb 1 ATR-re van a triggertől. Figyelmeztetés: még NEM vétel, csak ha zárón kitör."],
  pos_entered: ["watch", "Belépett", "A jelzett trade virtuális követése: a másnapi nyitó a limit alatt volt, a belépés megtörtént."],
  pos_skipped: ["fail", "Kimaradt", "A másnapi nyitó a limit fölött vagy a stop alatt volt – a jelzés érvénytelen, nem volt belépés."],
  pos_partial: ["warn", "½ eladás", "+1R elérve: a terv szerint a pozíció fele eladva, a stop a belépőre húzva."],
  pos_breakeven: ["fail", "Nullszaldó", "A maradék pozíció a belépőn (nullszaldón) zárt, miután a fele már +1R-nél kiment."],
  pos_stop: ["bad", "Stop", "A követett trade stopja kiütött."],
  pos_trail: ["fail", "Követett stop", "A követett (húzott) stop zárta a trade-et."],
  pos_target: ["go", "Cél", "A követett trade elérte a célt."],
  pos_timeout: ["fail", "Időkorlát", "A trade kifutott az időkorlátból – zárás a záróáron."],
  hold_stop: ["bad", "Saját: stop", "Saját pozíciód: az ár elérte a megadott stopot."],
  hold_target: ["go", "Saját: cél", "Saját pozíciód: az ár elérte a megadott célt."],
  hold_risk: ["warn", "Saját: romlott", "Saját pozíciód minősítése romlott (pl. Rendben → Figyelj / Kockázatos)."],
  hold_earnings: ["warn", "Saját: jelentés", "Saját pozíciód papírjánál közeleg a gyorsjelentés – egy gap átugorhatja a stopot."],
  broker_submitted: ["watch", "Paper: beküldve", "A jelzés bracket megbízásként bement az Alpaca paper számlára."],
  broker_filled: ["watch", "Paper: belépett", "A paper számlán teljesült a belépő."],
  broker_partial: ["go", "Paper: ½ eladva", "A paper trade fele +1R-nél kiment."],
  broker_breakeven: ["fail", "Paper: stop a belépőn", "A paper trade maradékának stopja a belépőre került."],
  broker_stop: ["bad", "Paper: stop", "A paper trade stopja teljesült."],
  broker_target: ["go", "Paper: cél", "A paper trade elérte a célt."],
  broker_timeout: ["fail", "Paper: időkorlát", "A paper trade időkorlát miatt zárva."],
  broker_expired: ["fail", "Paper: nem teljesült", "A belépő-limit a jelzés utáni napon nem teljesült – visszavonva."],
  broker_closed: ["fail", "Paper: lezárva", "A paper trade lezárult."],
  broker_skipped: ["fail", "Paper: nincs hely", "A jelzés érvényes, de a portfólióban nem volt hely (max. trade-szám vagy nincs elég szabad készpénz) – nem vásárolt."],
  broker_error: ["bad", "Paper: hiba", "A jelzést nem sikerült beküldeni a paper számlára (az ok az üzenetben)."],
  entry_momentum: ["go", "Belépés (momentum)", "Havi rebalansz: a papír bekerült a legjobb 10 közé a 12-1 havi hozam szerint – vétel holnap nyitáskor, tartás amíg a legjobb 20-on belül marad."],
  entry_pullback: ["go", "Belépés (visszahúzás)", "Mély, rövid visszahúzódás emelkedő trendben: vételi limit holnapra a záró alatt, stoppal. Kilépés, ha a záró ConnorsRSI 50 fölé megy (másnap nyitáskor)."],
  pos_exit_signal: ["warn", "Kilépési jel", "A kilépési feltétel a zárón teljesült – holnap nyitáskor add el."],
  pos_exit: ["fail", "Kilépett", "A követett trade a kilépési jel után nyitáskor zárult."],
  broker_exit_signal: ["warn", "Paper: kilépési jel", "A paper trade kilépési feltétele a zárón teljesült – nyitáskor piaci áron zár."],
  broker_exit: ["fail", "Paper: kilépett", "A paper trade a kilépési jel után piaci áron zárult."],
  strategy_switch: ["watch", "Stratégia-váltás", "Átváltottad, melyik stratégia van a paper fiókhoz kötve."],
};
const GRADE = {
  good: ["go", "Jó", "A jelzés bejött: előbb érte el a célt, mint az érvénytelenítést (belépésnél: pozitív R-rel zárt)."],
  bad: ["bad", "Rossz", "A jelzés nem jött be: előbb érte el az érvénytelenítést / stopot."],
  flat: ["fail", "Nullszaldó", "A trade 0R körül zárt."],
  open: ["watch", "Folyamatban", "Még egyik sem: se a cél, se az érvénytelenítés. A változás a mostani árig számolva."],
  na: ["fail", "–", "Nem értékelhető (nem jelzés, nincs árfolyam, vagy nem lett belépés)."],
};
const COL_HELP = {
  side: "Long: emelkedésre szóló jelzés (bikás minta, belépés). Short: esésre szóló jelzés (medvés minta) – ott az esés a nyereség.",
  signal: "Az ár a jelzés pillanatában (a jelzés napjának záróára).",
  now: "Az utolsó lezárt napi gyertya záróára.",
  change: "Irányhelyes változás a jelzés óta: shortnál az esés pozitív. Belépésnél a szimulált trade eredménye (½ +1R-nél, stop a belépőre, cél).",
  profit: "Belépési jelzésnél: dollárban a jelzett darabszámmal és R-ben (1R = a vállalt kockázat). Alakzatnál: R az érvénytelenítési szinthez mért kockázatban.",
  grade: "Mi történt előbb a jelzés után: cél (Jó) vagy érvénytelenítés / stop (Rossz). Egy gyertyán mindkettő = Rossz (pesszimista, mint a backtestben).",
  mfe: "Legjobb / legrosszabb irányhelyes kilengés a jelzés óta (MFE / MAE).",
  strength: "Mennyire erős volt a jel a kiküldéskor. Belépési jelzésnél a 0–100-as belépési pontszám (a riasztási küszöb alatt gyenge, 70-től erős). Alakzatnál az alakzat geometriai minősége (70% alatt gyenge, 85%-tól erős – ugyanazok a sávok, mint a backtestben). A pontszám nem valószínűség.",
};
const STRENGTH = { strong: ["go", "Erős"], medium: ["warn", "Közepes"], weak: ["fail", "Gyenge"] };
let alertFilter = { side: "all", grade: "all", strength: "all", strategy: "" };
try { alertFilter = { ...alertFilter, ...JSON.parse(localStorage.getItem("sw-alert-filter") || "{}") }; } catch (_) { /* privat mod */ }

async function pageAlerts() {
  await loadOverview();
  view.innerHTML = `<div class="loading">Értesítések kiértékelése…</div>`;
  const data = await api("/api/signals?limit=500");
  renderAlerts(data);
}

function renderAlerts(data) {
  const s = data.summary;
  const rows = data.records.filter((r) => {
    if (alertFilter.side === "long" && r.side !== "long") return false;
    if (alertFilter.side === "short" && r.side !== "short") return false;
    if (alertFilter.side === "other" && r.category === "signal") return false;
    if (alertFilter.grade !== "all" && r.grade !== alertFilter.grade) return false;
    if (alertFilter.strength !== "all" && r.strength_label !== alertFilter.strength) return false;
    if (alertFilter.strategy === "none" ? r.strategy : alertFilter.strategy && r.strategy !== alertFilter.strategy) return false;
    return true;
  });
  const seg = (key, value, label, title = "") => `<button type="button" class="seg ${alertFilter[key] === value ? "on" : ""}" data-key="${key}" data-value="${value}" ${title ? `title="${esc(title)}"` : ""}>${label}</button>`;
  const th = (label, help, cls = "") => `<th class="${cls}" title="${esc(help)}">${label} <span class="help">?</span></th>`;

  view.innerHTML = `
    <div class="page-head"><h1>Értesítések</h1><span class="muted">Amit a monitor kiküldött, a legfrissebb elöl – utólag kiértékelve a jelzés óta lezárt gyertyákon.</span></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Jelzések</div><div class="value">${s.signals}</div><div class="sub">${s.open} folyamatban</div></div>
      <div class="kpi" title="${esc(GRADE.good[2])}"><div class="label">Találati arány</div>
        <div class="value">${s.hit_rate != null ? fmt(s.hit_rate * 100, 0) + "%" : "–"}</div><div class="sub">${s.good} jó / ${s.decided} eldőlt jelzés</div></div>
      <div class="kpi" title="Megmutatja, hogy az erősebb jelek tényleg gyakrabban jöttek-e be. Kevés eldőlt jelzésnél még nem mérvadó."><div class="label">Találati arány erősség szerint</div>
        <div class="strength-rates">${["strong", "medium", "weak"].map((k) => {
          const t = (s.by_strength || {})[k] || { decided: 0 };
          return `<div><span class="badge ${STRENGTH[k][0]}">${STRENGTH[k][1]}</span><strong>${t.hit_rate != null ? fmt(t.hit_rate * 100, 0) + "%" : "–"}</strong><span class="muted small">${t.good || 0}/${t.decided}</span></div>`;
        }).join("")}</div></div>
      <div class="kpi" title="${esc(COL_HELP.change)}"><div class="label">Átlagos változás</div>
        <div class="value ${s.avg_change_pct > 0 ? "up" : s.avg_change_pct < 0 ? "down" : ""}">${s.avg_change_pct != null ? signed(s.avg_change_pct, 1, "%") : "–"}</div>
        <div class="sub">irányhelyesen, a jelzés óta</div></div>
    </div>
    <div class="filters">
      <div class="segmented" role="group" aria-label="Irány">${seg("side", "all", "Mind")}${seg("side", "long", "▲ Long", COL_HELP.side)}${seg("side", "short", "▼ Short", COL_HELP.side)}${seg("side", "other", "Egyéb", "Kezelési értesítések: pozíciókövetés, saját pozíció, paper trading")}</div>
      <div class="segmented" role="group" aria-label="Minősítés">${seg("grade", "all", "Minden minősítés")}${seg("grade", "good", "Jó")}${seg("grade", "bad", "Rossz")}${seg("grade", "open", "Folyamatban")}</div>
      <div class="segmented" role="group" aria-label="Jelerősség">${seg("strength", "all", "Minden erősség")}${seg("strength", "strong", "Erős", COL_HELP.strength)}${seg("strength", "medium", "Közepes", COL_HELP.strength)}${seg("strength", "weak", "Gyenge", COL_HELP.strength)}</div>
      ${strategySeg(alertFilter.strategy === "none" ? "x" : alertFilter.strategy, "data-astrategy", `<button type="button" class="seg ${alertFilter.strategy === "none" ? "on" : ""}" data-astrategy="none" title="Nem stratégia-értesítések: alakzat (short), saját pozíció, rendszerüzenet">Egyéb</button>`)}
      <span class="muted small">${rows.length} / ${data.records.length} értesítés</span>
    </div>
    <section class="card">
      ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Időpont</th><th>Típus</th><th>Stratégia</th>${th("Irány", COL_HELP.side)}${th("Jelerősség", COL_HELP.strength)}<th>Ticker</th><th>Üzenet</th>
        ${th("Jelzéskor", COL_HELP.signal, "num")}${th("Most", COL_HELP.now, "num")}${th("Változás", COL_HELP.change, "num")}${th("Profit", COL_HELP.profit, "num")}${th("Minőség", COL_HELP.grade)}${th("Max ▲ / ▼", COL_HELP.mfe, "num")}</tr></thead><tbody>
      ${rows.map(alertRow).join("")}</tbody></table></div>`
        : `<div class="card-body empty">${data.records.length ? "Nincs a szűrőnek megfelelő értesítés." : "Még nem ment ki értesítés."}</div>`}
    </section>
    <div class="muted small">Alakzat-értesítésnél a jelzéskori árról indulva nézi, mit ért el előbb az ár: a célt vagy az érvénytelenítési szintet. Belépési jelzésnél ugyanazt a szimulációt futtatja, mint a backtest (másnapi nyitó a limit alatt, ½ +1R-nél, stop a belépőre). Tranzakciós költség nélkül. Nem befektetési tanács.</div>`;

  view.querySelectorAll("button.seg").forEach((b) => b.addEventListener("click", () => {
    alertFilter[b.dataset.key] = b.dataset.value;
    try { localStorage.setItem("sw-alert-filter", JSON.stringify(alertFilter)); } catch (_) { /* nem baj */ }
    renderAlerts(data);
  }));
  view.querySelectorAll("button[data-astrategy]").forEach((b) => b.addEventListener("click", () => {
    alertFilter.strategy = b.dataset.astrategy;
    try { localStorage.setItem("sw-alert-filter", JSON.stringify(alertFilter)); } catch (_) { /* nem baj */ }
    renderAlerts(data);
  }));
  bindRowLinks();
}

function strengthCell(r) {
  if (r.strength == null) return `<span class="muted">–</span>`;
  const [cls, label] = STRENGTH[r.strength_label] || ["fail", r.strength_label];
  const unit = r.strength_kind === "pattern_quality" ? "%" : "";
  return `<span class="strength has-help" title="${esc(r.strength_detail)}">${scorebar(r.strength)}<span class="badge ${cls}">${label}</span></span>
    <div class="muted small">${r.strength_kind === "entry_score" ? "belépési pont" : "alakzat-minőség"} ${fmt(r.strength, 0)}${unit}</div>`;
}

function alertRow(r) {
  const [cls, label, help] = EVENT[r.event] || ["fail", r.event, ""];
  const ts = r.timestamp ? new Date(r.timestamp).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }) : "–";
  const side = r.side === "long" ? `<span class="up">▲ Long</span>` : r.side === "short" ? `<span class="down">▼ Short</span>` : `<span class="muted">–</span>`;
  const [gcls, glabel, ghelp] = GRADE[r.grade] || GRADE.na;
  const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const profit = r.pnl_usd != null
    ? `${signed(r.pnl_usd, 0, " $")}<div class="muted small">${signed(r.r, 2, "R")}</div>`
    : r.r != null ? signed(r.r, 2, "R") : "–";
  return `<tr class="clickable" data-href="#/p/${esc(r.symbol)}">
    <td class="muted small">${esc(ts)}</td>
    <td><span class="badge ${cls === "info" ? "watch" : cls} has-help" title="${esc(help)}">${esc(label)}</span></td>
    <td>${strategyBadge(r.strategy)}</td>
    <td>${side}</td>
    <td>${strengthCell(r)}</td>
    <td class="ticker">${esc(r.symbol)}</td>
    <td class="reason" style="color:var(--text)">${esc(r.title)}${r.signal_id ? `<div>${signalLink(r.signal_id)}</div>` : ""}</td>
    <td class="num">${fmt(r.signal_price)}</td>
    <td class="num">${fmt(r.now_price)}${r.now_date ? `<div class="muted small">${esc(r.now_date)}</div>` : ""}</td>
    <td class="num ${tone(r.change_pct)}">${r.change_pct != null ? signed(r.change_pct, 1, "%") : "–"}</td>
    <td class="num ${tone(r.pnl_usd != null ? r.pnl_usd : r.r)}">${profit}</td>
    <td><span class="badge ${gcls} has-help" title="${esc(ghelp + (r.outcome ? "\n\n" + r.outcome : ""))}">${glabel}</span>${r.outcome && r.category === "signal" ? `<div class="muted small">${esc(r.outcome)}</div>` : ""}</td>
    <td class="num small">${r.mfe_pct != null ? `<span class="up">${signed(r.mfe_pct, 1, "%")}</span> / <span class="down">${signed(r.mae_pct, 1, "%")}</span>` : "–"}</td></tr>`;
}

// ---------------------------------------------------------------------------
// Beallitasok
// ---------------------------------------------------------------------------
const FIELDS = [
  ["Számla és kockázat", [
    ["account_size", "Számlaméret (USD)", "number", "Ebből számolja a javasolt darabszámot.", { min: 100, step: 100 }],
    ["risk_per_trade_pct", "Kockázat / trade (%)", "number", "Egy rossz trade legfeljebb ennyit visz el. Szokásos: 0,5–1%.", { min: 0.1, max: 5, step: 0.1 }],
    ["max_position_pct", "Max. pozícióméret (%)", "number", "Egy papír legfeljebb ekkora része a számlának.", { min: 1, max: 100, step: 1 }],
  ]],
  ["Belépés", [
    ["confirm_breakout", "Megerősített kitörés", "check", "A kitörés utáni nap záróját is megvárja (kutatás: több nyerő trade)."],
    ["min_score", "Min. pontszám", "number", "Ez alatt nincs riasztás (0–100).", { min: 0, max: 100, step: 5 }],
    ["min_risk_reward", "Min. hozam/kockázat", "number", "", { min: 0.5, max: 5, step: 0.1 }],
    ["min_stop_atr", "Stop min. távolság (ATR)", "number", "Kutatás: 2,0 jobb, mint 1,5.", { min: 0.5, max: 5, step: 0.25 }],
    ["alert_setups", "Előkészület-jelzés", "check", "Szóljon, ha egy alakzat ≤1 ATR-re van a kitöréstől."],
    ["alert_retests", "Visszateszt-jelzés", "check", "Kevés mintán instabil volt – alapból ki."],
  ]],
  ["Kilépés", [
    ["exit_mode", "Kilépési mód", "select", "", { options: [["partial", "Fele +1R-nél, stop a belépőre (ajánlott)"], ["target", "Teljes pozíció a célig"], ["trail", "Követett stop, fix cél nélkül"], ["partial_trail", "Fele +1R-nél, maradék követett stoppal"]] }],
    ["partial_r", "Részleges eladás szintje (R)", "number", "", { min: 0.5, max: 3, step: 0.25 }],
  ]],
  ["Átnézett papírok", [
    ["best_list", "„Legjobb belépő” gomb listája", "select", "A fejléc „Legjobb belépő” gombja ezen a listán keresi a legjobb long és short belépőt, friss árakkal. Nagy listán (indexek) az esti indexszkennelés legjobb jelöltjeit számolja újra.",
      { options: [["watchlist", "Watchlist"], ["nasdaq100", "Nasdaq-100"], ["sp500", "S&P 500"], ["indexes", "Nasdaq-100 + S&P 500"], ["all", "Watchlist + mindkét index"]] }],
    ["nasdaq100", "Teljes Nasdaq-100 (QQQ)", "check", "A QQQ-t alkotó ~100 papírt is átnézi zárás után, és azokra is küld belépési jelzést."],
    ["sp500", "Teljes S&P 500", "check", "Az S&P 500 ~500 papírját is átnézi zárás után (~1 perc; első letöltés ~30 MB)."],
    ["index_setups", "Előkészület-jelzés az indexpapírokra", "check", "Alapból csak a valódi belépési jelzések mennek ki az indexpapírokról – az „előkészület” 500 papíron sok zajt adna."],
  ]],
  ["Paper trading (Alpaca)", [
    ["auto_trade", "Automatikus beküldés", "check", "A monitor zárás után a belépési jelzéseket magától beküldi a paper számlára. Kikapcsolva csak a Paper trading fülön, kézzel."],
    ["size_from_account", "Méret a paper számla tőkéjéből", "check", "A darabszámot a paper számla equity-jéből és a kockázat/trade %-ból számolja (különben a fenti számlaméretből)."],
    ["max_open_trades", "Max. nyitott paper trade", "number", "Egyszerre legfeljebb ennyi nyitott vagy függő trade (a belépésre váró limit is helyet foglal).", { min: 1, max: 50, step: 1 }],
  ]],
  ["Piaci környezet és értesítések", [
    ["regime_filter", "Piaci rezsim-szűrő (index > SMA200)", "check", "Gyenge piacon nincs belépési jelzés. Az index a fenti „Átnézett papírok” szerint: ha az S&P 500 be van kapcsolva, az SPY, különben a QQQ (Nasdaq-100) 200 napos átlagát nézi."],
    ["earnings_blackout_days", "Earnings-tilalom (nap)", "number", "Gyorsjelentés előtt ennyi napig nincs belépés.", { min: 0, max: 30, step: 1 }],
    ["direction", "Értesítések iránya", "select", "Short: a medvés alakzatok (fej-váll, dupla tető, bear zászló…) friss, zárón megerősített lefelé törése – nincs mögötte belépési motor és paper trading, és a backtest szerint gyengébbek (pl. bear zászló: 28% találat). A pozíciókövetés, a saját pozíciók és a paper trading értesítései mindig mennek.",
      { options: [["long", "Csak long (vételi) jelzések"], ["short", "Csak short (eladási) jelzések"], ["both", "Long és short is"]] }],
    ["pattern_events", "Régi alakzat-értesítések is", "check", "Minden alakzat-állapotváltásról, a fent választott irány(ok)ban – alapból ki."],
  ]],
];

async function pageSettings() {
  await loadOverview();
  const data = await api("/api/settings");
  const v = data.values;
  const field = ([key, label, type, hint, opt = {}]) => {
    if (type === "check") return `<div class="field check"><input type="checkbox" id="f-${key}" name="${key}" ${v[key] ? "checked" : ""}>
      <div><label for="f-${key}">${label}</label>${hint ? `<div class="hint">${hint}</div>` : ""}</div></div>`;
    if (type === "select") return `<div class="field"><label for="f-${key}">${label}</label><select id="f-${key}" name="${key}">
      ${opt.options.map(([val, text]) => `<option value="${val}" ${v[key] === val ? "selected" : ""}>${text}</option>`).join("")}</select>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
    return `<div class="field"><label for="f-${key}">${label}</label><input type="number" id="f-${key}" name="${key}" value="${esc(v[key])}"
      ${opt.min != null ? `min="${opt.min}"` : ""} ${opt.max != null ? `max="${opt.max}"` : ""} ${opt.step != null ? `step="${opt.step}"` : ""}>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
  };
  view.innerHTML = `
    <div class="page-head"><h1>Beállítások</h1><span class="muted">A <code>config/settings.yaml</code>-ba ír, a kommentek megmaradnak. A futó monitor a következő körtől használja.</span></div>
    <form id="settings-form">
      ${FIELDS.map(([title, fields]) => `<section class="card"><h2>${title}</h2><div class="card-body"><div class="form-grid">${fields.map(field).join("")}</div></div></section>`).join("")}
      <div class="form-actions"><button class="btn primary" type="submit">Beállítások mentése</button><span id="settings-msg" class="msg"></span></div>
    </form>
    <section class="card" style="margin-top:24px"><h2>Figyelt papírok (watchlist)</h2><div class="card-body">
      <div class="callout">Soronként egy ticker. Új papír adatai a következő <strong>Frissítés</strong>kor töltődnek le.</div>
      <textarea id="watchlist" spellcheck="false">${esc(data.watchlist.join("\n"))}</textarea>
      <div class="form-actions"><button id="save-watchlist" class="btn primary" type="button">Watchlist mentése</button><span id="wl-msg" class="msg"></span></div>
    </div></section>
    <section class="card" id="alpaca"><h2>Alpaca paper trading – kapcsolódás</h2><div class="card-body">
      <div class="callout">Csak <strong>paper</strong> (nem valódi pénzes) számla engedélyezett. A kulcsok a <code>.env</code> fájlba kerülnek
        (verziókezelésen kívül); a felület a mentés után nem mutatja őket vissza, csak a kulcs utolsó 4 karakterét.
        Az Alpaca felületén: <em>Paper Trading → API Keys → Generate</em>.</div>
      <div id="alpaca-status" class="msg" style="margin-bottom:12px"></div>
      <form id="alpaca-form" autocomplete="off">
        <div class="form-grid">
          <div class="field"><label for="a-endpoint">Endpoint</label>
            <input type="text" id="a-endpoint" name="endpoint" value="https://paper-api.alpaca.markets" spellcheck="false">
            <div class="hint">Csak a paper cím fogadható el.</div></div>
          <div class="field"><label for="a-key">API Key ID</label>
            <input type="text" id="a-key" name="key" spellcheck="false" autocomplete="off"></div>
          <div class="field"><label for="a-secret">Secret Key</label>
            <input type="password" id="a-secret" name="secret" autocomplete="new-password"></div>
        </div>
        <div class="form-actions">
          <button class="btn primary" type="submit">Mentés és kapcsolat-teszt</button>
          <button class="btn" type="button" id="alpaca-test">Kapcsolat tesztelése</button>
          <button class="btn danger" type="button" id="alpaca-forget">Kulcsok törlése</button>
          <span id="alpaca-msg" class="msg"></span>
        </div>
      </form>
    </div></section>
    <section class="card"><h2>Nem itt állítható</h2><div class="card-body muted">
      Discord webhook, Telegram token és webhook URL: a titokfájlban (<code>python cli.py discord</code>) (biztonsági okból a felület nem olvassa). Minden további szűrő: <code>config/settings.yaml</code>.
    </div></section>`;
  bindAlpacaForm();

  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("#settings-msg");
    const values = {};
    FIELDS.flatMap(([, f]) => f).forEach(([key, , type]) => {
      const el = $(`#f-${key}`);
      values[key] = type === "check" ? el.checked : type === "select" ? el.value : Number(el.value);
    });
    try {
      const res = await api("/api/settings", { method: "POST", body: JSON.stringify({ values }) });
      msg.className = "msg ok";
      msg.textContent = res.index_scan_started
        ? "Mentve – az indexek átnézése elindult (~1–2 perc), utána minden zárás után automatikusan fut."
        : "Mentve.";
      overview = null;
      if (res.job && res.index_scan_started) { renderJob(res.job); pollJob(); }
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  });
  $("#save-watchlist").addEventListener("click", async () => {
    const msg = $("#wl-msg");
    const symbols = $("#watchlist").value.split(/\s+/).filter(Boolean);
    try {
      const res = await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbols }) });
      $("#watchlist").value = res.watchlist.join("\n");
      msg.className = "msg ok"; msg.textContent = `Mentve (${res.watchlist.length} papír). Az új papírokhoz nyomj Frissítést.`;
      overview = null;
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  });
}

// ---------------------------------------------------------------------------
// Utvonalvalasztas
// ---------------------------------------------------------------------------
async function route() {
  const hash = location.hash || "#/";
  const tab = hash.startsWith("#/ledger") || hash.startsWith("#/signal/") ? "ledger" : hash.startsWith("#/broker") ? "broker" : hash.startsWith("#/holdings") ? "holdings" : hash.startsWith("#/positions") ? "positions" : hash.startsWith("#/alerts") ? "alerts" : hash.startsWith("#/settings") ? "settings" : hash.startsWith("#/lab") ? "lab" : hash.startsWith("#/strategies") ? "strategies" : hash.startsWith("#/sandbox") ? "sandbox" : hash.startsWith("#/backtest") ? "backtest" : "overview";
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  tooltip.hidden = true;
  try {
    const m = hash.match(/^#\/p\/([A-Za-z0-9.\-^]+)$/);
    const sm = hash.match(/^#\/signal\/(sig-[0-9a-f]{12})$/);
    if (m) await pageSymbol(m[1].toUpperCase());
    else if (sm) await pageSignal(sm[1]);
    else if (tab === "ledger") await pageLedger();
    else if (tab === "broker") await pageBroker();
    else if (tab === "holdings") await pageHoldings();
    else if (tab === "positions") await pagePositions();
    else if (tab === "alerts") await pageAlerts();
    else if (tab === "settings") await pageSettings();
    else if (tab === "lab") await pageLab();
    else if (tab === "strategies") await pageStrategies();
    else if (tab === "sandbox") await pageSandbox();
    else if (tab === "backtest") {
      const rm = hash.match(/^#\/backtest\/run\/([0-9a-f]{12})$/);
      await pageBacktest(rm ? rm[1] : null);
    }
    else await pageOverview();
  } catch (e) {
    view.innerHTML = `<div class="empty">Hiba: ${esc(e.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Strategia-labor: kiserletek multbeli adatokon (a szamolast a hatterfolyamat vegzi)
// ---------------------------------------------------------------------------
const LAB_STATUS = { queued: ["watch", "Sorban áll"], running: ["watch", "Számol"], done: ["go", "Kész"], error: ["bad", "Hiba"] };
const LAB_PHASE = { indul: "Indul", adat: "Adatletöltés", kitoresek: "Kitörések gyűjtése", ertekeles: "Kísérletek értékelése",
  "gepi tanulas": "Gépi tanulás", var: "Pihen – nincs teendő", hiba: "Hiba" };
const LAB_EXIT = { target: "teljes pozíció a célig", partial: "fele +1R-nél, maradék a célig", trail: "követett stop", partial_trail: "fele +1R-nél, maradék követett stoppal" };
let labTimer = null;
let labOpen = new Set();
// rangsor: mi szerint "legjobb" egy kiserlet (a valasztas bongeszonkent megjegyezve)
const LAB_SORT = {
  ev_lo: ["Óvatos EV (95% alsó határ)", (s) => s.all.ev_lo, "Az átlagos R a legrosszabb még valószínű esetben – a kis mintás „szerencsés” kísérleteket hátrébb sorolja. Ajánlott."],
  ev: ["Átlagos R / trade (EV)", (s) => s.all.ev, "Egy trade átlagos eredménye – kis mintán félrevezető lehet."],
  win: ["Nyerési arány", (s) => s.all.win, "A nyerő trade-ek aránya – önmagában nem mond semmit a nyerések és vesztések méretéről."],
  total: ["Összes R", (s) => s.all.total, "Az összes trade eredménye együtt – a sok, kis előnyű trade itt erősebb, mint a kevés, nagy előnyű."],
  dd: ["Legkisebb visszaesés", (s) => -s.all.dd, "A legnagyobb tőkevisszaesés R-ben – kevesebb trade-nél természetesen kisebb."],
  created: ["Létrehozás sorrendje", null, ""],
};
let labSort = "ev_lo";
try { const saved = localStorage.getItem("sw-lab-sort"); if (LAB_SORT[saved]) labSort = saved; } catch (_) { /* privat mod */ }

const labEv = (s) => (s && s.n ? `${signed(s.ev, 2, "R")}<div class="muted small">${s.ev_lo != null ? `${signed(s.ev_lo, 2)} … ${signed(s.ev_hi, 2)}` : ""}</div>` : "–");
const labWin = (s) => (s && s.n ? `${fmt(s.win * 100, 0)}%${s.win_lo != null ? `<div class="muted small">${fmt(s.win_lo * 100, 0)}–${fmt(s.win_hi * 100, 0)}%</div>` : ""}` : "–");
const labPeriod = (s) => (s && s.n ? `${s.n} · ${fmt(s.win * 100, 0)}% · <span class="${s.ev > 0 ? "up" : s.ev < 0 ? "down" : ""}">${signed(s.ev, 2)}</span>` : "–");
const labVal = (v) => (v === true ? "be" : v === false ? "ki" : v === null || v === undefined ? "ki" : typeof v === "number" ? fmt(v, Number.isInteger(v) ? 0 : 2) : esc(v));

async function pageLab() {
  await loadOverview();
  const d = await api("/api/lab");
  view.innerHTML = `
    <div class="page-head"><h1>Stratégia-labor</h1>
      <span class="muted">Belépési stratégiák folyamatos tesztje múltbeli adatokon – a háttérben, a monitor zavarása nélkül.</span></div>
    <div id="lab-status"></div>
    <section class="card"><h2>Kísérletek <span class="muted" id="lab-count"></span></h2>
      <div class="card-body">
        <div class="callout" id="lab-caution"></div>
        <div id="lab-table"></div>
        <details id="lab-new"><summary>+ Új kísérlet</summary>${labForm(d)}</details>
      </div></section>
    <section class="card"><h2>Gépi tanulás <span class="muted">évenként előre haladó teszt – mindig csak a múltból tanul</span></h2>
      <div class="card-body" id="lab-ml"></div></section>`;
  renderLab(d);
  $("#lab-form").addEventListener("submit", submitLabForm);
  clearInterval(labTimer);
  labTimer = setInterval(async () => {
    if (!location.hash.startsWith("#/lab")) { clearInterval(labTimer); return; }
    try { renderLab(await api("/api/lab")); } catch (_) { /* a kovetkezo frissites ujraprobalja */ }
  }, 30000);
}

function renderLab(d) {
  const st = d.status || {};
  const pr = st.progress;
  const cov = st.coverage;
  const last = st.last_pass;
  const pill = !st.running ? `<span class="pill bad">nem fut</span>` : `<span class="pill ok">fut</span>`;
  $("#lab-status").innerHTML = `<section class="card"><h2>Állapot ${pill}
      <span class="muted">${esc(LAB_PHASE[st.phase] || st.phase || "")}${st.detail ? ` – ${esc(st.detail)}` : ""}</span></h2>
    <div class="card-body">
      ${pr ? `<div class="lab-progress" title="${pr[0]} / ${pr[1]}"><span style="width:${Math.min(100, (pr[0] / Math.max(pr[1], 1)) * 100)}%"></span></div>
        <div class="muted small">${pr[0]} / ${pr[1]}</div>` : ""}
      ${!st.running ? `<div class="callout">A labor-folyamat nem fut${st.age_s ? ` (utoljára ${fmt(st.age_s / 60, 0)} perce jelentkezett)` : ""}. Szerveren: <code>sudo systemctl start stockwatch-lab</code>, helyben: <code>python cli.py lab</code>.</div>` : ""}
      <div class="kpis" style="margin:12px 0 0">
        <div class="kpi"><div class="label">Papírok kitörés-adattal</div><div class="value">${cov ? `${cov.with_events} / ${cov.symbols}` : "–"}</div>
          <div class="sub">${cov ? `${cov.with_data} papírnak van friss árfolyama` : "az első kör után"}</div></div>
        <div class="kpi"><div class="label">Utolsó értékelő kör</div><div class="value">${last ? esc(String(last.finished).slice(0, 16).replace("T", " ")) : "–"}</div>
          <div class="sub">${last ? `${last.experiments} kísérlet, ${last.symbols} papír, ${fmt(last.seconds / 60, 0)} perc` : ""}</div></div>
        <div class="kpi"><div class="label">Valaha lefuttatott kísérlet</div><div class="value">${d.created_total}</div>
          <div class="sub">holdout-megnyitások: ${d.holdout_openings}</div></div>
      </div></div></section>`;
  $("#lab-count").textContent = `${d.experiments.length} / ${d.max_experiments}`;
  $("#lab-caution").innerHTML = `Időszakok: <b>tanító</b> 2022 előtt, <b>validáció</b> 2022 – 2025. március. A <b>holdout</b> (${esc(d.holdout_start)}-tól a kísérlet létrehozásáig) rejtve marad.
    Az <b>élő</b> oszlop a kísérlet létrehozása <i>utáni</i> új adat – ez valódi mintán kívüli teszt. Eddig ${d.created_total} kísérlet futott: minél több változatot próbálunk ki,
    annál valószínűbb, hogy a legjobb csak szerencsés – a <span class="badge go">robusztus</span> jelölés (mindkét időszakban és az EV alsó határában sem rosszabb az élőnél) és az élő oszlop számít igazán.`;
  $("#lab-table").innerHTML = labTable(d);
  $("#lab-ml").innerHTML = labMl(d.ml);
  view.querySelectorAll("[data-lab]").forEach((b) => b.addEventListener("click", labButton));
  $("#lab-sort")?.addEventListener("change", (ev) => {
    labSort = ev.target.value;
    try { localStorage.setItem("sw-lab-sort", labSort); } catch (_) { /* nem baj */ }
    renderLab(d);
  });
}

function labTable(d) {
  if (!d.experiments.length) return `<div class="empty">Még nincs kísérlet – a labor első indulásakor létrehozza az élő beállítást és az előre rögzített vizsgálatot.</div>`;
  // az elo beallitas mindig felul (az az osszehasonlitasi alap), alatta a kiserletek a valasztott rangsorban
  const key = LAB_SORT[labSort][1];
  const value = (e) => (key && e.summary && e.summary.all && e.summary.all.n ? key(e.summary) : null);
  const others = d.experiments.filter((e) => !e.live);
  if (key) others.sort((a, b) => (value(b) ?? -Infinity) - (value(a) ?? -Infinity));
  const ordered = [...d.experiments.filter((e) => e.live), ...others];
  let rank = 0;
  const rows = ordered.map((e) => {
    const s = e.summary || {};
    const [cls, label] = LAB_STATUS[e.status] || ["fail", e.status];
    const diff = e.live ? `<div class="muted small">a mostani beállítás – összehasonlítási alap, nem rangsoroljuk</div>`
      : e.diff.length ? `<div class="muted small">${e.diff.map((x) => `${esc(x.label)}: ${labVal(x.value)}`).join(" · ")}</div>` : "";
    const place = !e.live && value(e) != null ? ++rank : null;
    const open = labOpen.has(e.id);
    const perYear = s.all && s.all.n && s.years && s.years.years ? s.all.n / s.years.years : null;
    return `<tr${e.live ? ' class="lab-live"' : place === 1 ? ' class="lab-best"' : ""}>
      <td>${place ? `<span class="lab-rank" title="Helyezés a választott rangsor szerint">${place}.</span> ` : ""}<b>${esc(e.name)}</b>
        ${place === 1 ? `<span class="badge go has-help" title="Az első hely a választott rangsor szerint: ${esc(LAB_SORT[labSort][0])}">legjobb</span>` : ""}
        ${e.robust ? `<span class="badge go has-help" title="Mindkét időszakban (tanító és validáció) legalább olyan jó, mint az élő beállítás, és az EV 95%-os alsó határa sem rosszabb">robusztus</span>` : ""}${diff}</td>
      <td>${esc((d.universes[e.universe] || {}).label || e.universe)}</td>
      <td><span class="badge ${cls}">${label}</span>${e.error ? `<div class="muted small">${esc(e.error)}</div>` : ""}</td>
      <td class="num">${s.all ? s.all.n : "–"}</td><td class="num">${perYear == null ? "–" : fmt(perYear, 0)}</td>
      <td class="num">${labWin(s.all)}</td><td class="num">${labEv(s.all)}</td>
      <td class="num">${s.all && s.all.n ? `<span class="${s.all.total > 0 ? "up" : "down"}">${signed(s.all.total, 0, "R")}</span>` : "–"}</td>
      <td class="num">${labPeriod(s.train)}</td><td class="num">${labPeriod(s.test)}</td><td class="num">${labPeriod(s.forward)}</td>
      <td class="num">${s.all && s.all.n ? fmt(s.all.dd, 1) + "R" : "–"}</td>
      <td class="num">${s.years ? `${s.years.positive}/${s.years.years}` : "–"}</td>
      <td class="lab-actions"><button class="btn" type="button" data-lab="toggle" data-id="${e.id}">${open ? "Bezár" : "Részletek"}</button>
        ${e.live ? "" : `<button class="btn" type="button" data-lab="rerun" data-id="${e.id}" title="Újraszámolás a következő körben">↻</button>
        <button class="btn danger" type="button" data-lab="delete" data-id="${e.id}" title="Törlés">✕</button>`}</td></tr>
      ${open ? `<tr class="lab-detail"><td colspan="14">${labDetail(e, d)}</td></tr>` : ""}`;
  }).join("");
  const th = (text, help, num = true) => `<th${num ? ' class="num"' : ""}><span class="has-help" title="${esc(help)}">${text}</span></th>`;
  return `<div class="lab-sortbar"><label for="lab-sort">Rangsor:</label> <select id="lab-sort">
      ${Object.entries(LAB_SORT).map(([k, [t]]) => `<option value="${k}" ${k === labSort ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <span class="muted small">${esc(LAB_SORT[labSort][2])}</span></div>
    <div class="table-wrap"><table><thead><tr>
    ${th("Kísérlet", "A kísérlet neve, alatta amiben eltér a mostani (élő) beállítástól.", false)}${th("Univerzum", "Mely papírokon tesztelt.", false)}${th("Állapot", "Kész / számol / sorban áll.", false)}
    ${th("Trade", "Ennyi trade lett volna a látható időszakban (tanító + validáció, a holdout nélkül).")}
    ${th("Trade / év", "Átlagosan évente ennyi trade – ennyiszer kereskedne a rendszer ezzel a beállítással.")}
    ${th("Nyerés", "A nyereséggel zárt trade-ek aránya. Alatta a 95%-os tartomány: a mintaméret miatt ennyire bizonytalan a szám.")}
    ${th("EV (95%)", "Átlagos eredmény trade-enként, R-ben (várható érték). Alatta a 95%-os tartomány – az alsó határ az óvatos becslés.")}
    ${th("Össz. R", "Az összes trade eredménye együtt R-ben (trade × EV).")}
    ${th("Tanító &lt;2022", "A 2022 előtti trade-ek: darab · nyerési arány · EV (R).")}
    ${th("Validáció", "2022 – 2025. március: darab · nyerési arány · EV (R).")}
    ${th("Élő", "A kísérlet létrehozása UTÁNI új adat – valódi mintán kívüli teszt. Kezdetben üres, idővel telik.")}
    ${th("Max. DD", "A legnagyobb visszaesés R-ben: az egymás utáni trade-ek összesített eredményének legnagyobb esése csúcstól mélypontig.")}
    ${th("+ év", "Hány évben volt pozitív az átlagos eredmény / hány év volt összesen.")}<th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <details class="lab-help"><summary>Mit jelentenek az oszlopok és a jelölések?</summary>
      <ul>
        <li><b>R</b> – a trade-enként vállalt kockázat egysége: (belépő − stop) × darab. <b>+1R</b>: annyit nyert, amennyit kockáztatott;
          <b>−1R</b>: a stop teljesen kiütötte. 10 000 $-os számlán 1%-os kockázattal 1R = 100 $, így pl. +0,20R átlag = trade-enként átlag +20 $.</li>
        <li><b>Nyerés</b> – a nyereséggel zárt trade-ek aránya. Önmagában nem elég: a 67% kevés trade-ből bizonytalanabb, mint az 57% sokból,
          és nem mondja meg, mekkorák a nyerések és a vesztések.</li>
        <li><b>EV</b> – várható érték: egy trade átlagos eredménye R-ben. Pozitív = hosszú távon nyereséges. Alatta a <b>95%-os tartomány</b>:
          a valódi érték nagy valószínűséggel e kettő között van. Minél kevesebb a trade, annál szélesebb.</li>
        <li><b>Trade / év</b> és <b>Össz. R</b> – milyen gyakran kereskedne, és mennyit hozott volna összesen. Egy ritkán jelző, nagy előnyű
          beállítás összesen kevesebbet hozhat, mint egy gyakran jelző, kisebb előnyű.</li>
        <li><b>Tanító / Validáció / Élő</b> – ugyanaz három időszakra bontva (darab · nyerési arány · EV). Akkor megbízható egy eredmény,
          ha mindegyikben pozitív, nem csak az egyikben.</li>
        <li><b>Max. DD</b> – a legnagyobb visszaesés R-ben (pl. 20R ≈ 1%-os kockázattal 20% tőkecsökkenés a csúcshoz képest).</li>
        <li><b>+ év</b> – hány évben volt pozitív az eredmény (pl. 7/9).</li>
        <li><span class="badge go">robusztus</span> – mindkét időszakban legalább olyan jó, mint az élő beállítás, és az EV alsó határa sem rosszabb.
          <span class="badge go">legjobb</span> – az első hely a fent választott rangsor szerint.</li>
        <li><b>Holdout</b> – 2025 áprilistól a létrehozásig tartó, elrejtett időszak; a végső döntés előtt, egyszer érdemes megnyitni (Részletek).</li>
      </ul></details>`;
}

function labDetail(e, d) {
  const s = e.summary || {};
  const years = s.years && s.years.by_year ? Object.entries(s.years.by_year) : [];
  const pats = s.patterns ? Object.entries(s.patterns) : [];
  const params = d.fields.map((f) => `<li>${esc(f.label)}: <b>${labVal(e.entry[f.field])}</b></li>`).join("");
  const hold = s.holdout ? `<p>Holdout (${esc(d.holdout_start)} → létrehozás): <b>${labPeriod(s.holdout)}</b> – megnyitva ${esc(String(e.holdout_opened).slice(0, 10))}</p>`
    : e.live ? "" : `<p class="muted small">A holdout rejtett. Csak a végső döntés előtt, egyszer nyisd meg – utána ez a kísérlet már nem független teszt. Minden megnyitás naplózva.
      <button class="btn" type="button" data-lab="holdout" data-id="${e.id}">Holdout megnyitása</button></p>`;
  return `<div class="grid-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
    <div><h3>Évenként</h3>${years.length ? `<table><thead><tr><th>Év</th><th class="num">Trade</th><th class="num">EV</th></tr></thead><tbody>
      ${years.map(([y, v]) => `<tr><td>${esc(y)}</td><td class="num">${v.n}</td><td class="num ${v.ev > 0 ? "up" : "down"}">${signed(v.ev, 2, "R")}</td></tr>`).join("")}</tbody></table>` : "–"}
      ${hold}</div>
    <div><h3>Alakzatonként</h3>${pats.length ? `<table><thead><tr><th>Alakzat</th><th class="num">Trade</th><th class="num">Nyerés</th><th class="num">EV</th></tr></thead><tbody>
      ${pats.map(([p, v]) => `<tr><td>${esc(p)}</td><td class="num">${v.n}</td><td class="num">${fmt(v.win * 100, 0)}%</td><td class="num ${v.ev > 0 ? "up" : "down"}">${signed(v.ev, 2, "R")}</td></tr>`).join("")}</tbody></table>` : "–"}
      <details><summary>Minden beállítás</summary><ul class="checks">${params}</ul></details></div></div>`;
}

function labForm(d) {
  const field = (f) => {
    const v = d.defaults[f.field];
    const id = `lab-${f.field}`;
    if (f.type === "bool") return `<div class="field check"><input id="${id}" name="${f.field}" type="checkbox" ${v ? "checked" : ""}><label for="${id}">${esc(f.label)}</label></div>`;
    if (f.field === "exit_mode") return `<div class="field"><label for="${id}">${esc(f.label)}</label><select id="${id}" name="${f.field}">
      ${Object.entries(LAB_EXIT).map(([k, t]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>`;
    return `<div class="field"><label for="${id}">${esc(f.label)}</label><input id="${id}" name="${f.field}" type="number" step="any"
      ${f.min != null ? `min="${f.min}" max="${f.max}"` : ""} value="${v == null ? "" : v}"></div>`;
  };
  return `<form id="lab-form" class="lab-form">
    <p class="muted small">A mezők a mostani beállítással vannak kitöltve – csak azt változtasd meg, amit tesztelni szeretnél. Egyszerre egy-két eltérés: így látszik, mi hat.</p>
    <div class="form-grid tight">
      <div class="field"><label for="lab-name">Név <span class="req">*</span></label><input id="lab-name" name="name" type="text" maxlength="60" required placeholder="pl. stop 2,5 ATR"></div>
      <div class="field"><label for="lab-universe">Papírok</label><select id="lab-universe" name="universe">
        ${Object.entries(d.universes).map(([k, u]) => `<option value="${k}" ${k === "indexes" ? "selected" : ""}>${esc(u.label)} (${u.count})</option>`).join("")}</select></div>
      ${d.fields.map(field).join("")}
    </div>
    <div style="margin-top:12px"><button class="btn primary" type="submit">Kísérlet indítása</button> <span class="muted small" id="lab-msg"></span></div></form>`;
}

async function submitLabForm(ev) {
  ev.preventDefault();
  const form = ev.target;
  const values = {};
  form.querySelectorAll("[name]").forEach((el) => {
    if (el.name === "name" || el.name === "universe") return;
    values[el.name] = el.type === "checkbox" ? el.checked : el.value === "" ? null : el.type === "number" ? Number(el.value) : el.value;
  });
  try {
    const d = await api("/api/lab/experiment", { method: "POST", body: JSON.stringify({ name: form.name.value, universe: form.universe.value, values }) });
    form.name.value = "";
    $("#lab-msg").textContent = "Felvéve – a labor a következő körben számolja.";
    renderLab(d);
  } catch (e) {
    $("#lab-msg").textContent = e.message;
  }
}

async function labButton(ev) {
  const b = ev.currentTarget;
  const id = b.dataset.id;
  const act = b.dataset.lab;
  if (act === "toggle") {
    if (labOpen.has(id)) labOpen.delete(id); else labOpen.add(id);
    renderLab(await api("/api/lab"));
    return;
  }
  if (act === "delete" && !confirm("Törlöd a kísérletet és az eredményeit?")) return;
  if (act === "holdout" && !confirm("A holdout megnyitása után ez a kísérlet már nem független teszt, és a megnyitás naplózva lesz. Csak a végső döntés előtt nyisd meg. Megnyitod?")) return;
  try {
    const body = act === "holdout" ? { id, confirm: true } : act === "ml" ? {} : { id };
    renderLab(await api(`/api/lab/${act}`, { method: "POST", body: JSON.stringify(body) }));
  } catch (e) {
    alert(e.message);
  }
}

function labMl(ml) {
  const r = ml.report;
  const pending = ml.pending ? `<span class="badge watch">tanítás a következő körben</span>` : "";
  const btn = `<button class="btn" type="button" data-lab="ml">Újratanítás</button>`;
  if (!r) return `<p class="muted">Még nincs eredmény. ${pending}</p>${btn}`;
  const o = r.overall;
  const row = (label, s, help) => `<tr><td>${label}<div class="muted small">${help}</div></td><td class="num">${s.n}</td><td class="num">${labWin(s)}</td><td class="num">${labEv(s)}</td></tr>`;
  const maxImp = Math.max(...(r.features || []).map((f) => Math.abs(f.importance)), 1e-9);
  return `<div class="callout">${esc(r.verdict || "")}</div>
    ${o ? `<div class="table-wrap"><table><thead><tr><th>Mintán kívül, ${r.years.length} év (${r.years.length ? `${r.years[0].year}–${r.years[r.years.length - 1].year}` : ""})</th><th class="num">Trade</th><th class="num">Nyerés</th><th class="num">EV (95%)</th></tr></thead><tbody>
      ${row("Minden megerősített kitörés", o.all, "szűrés nélkül, a mostani kilépéssel")}
      ${row("A mostani szűrők", o.live, "amit az élő beállítás választott volna")}
      ${row(`A modell válogatása (felső ${fmt(r.select_fraction * 100, 0)}%)`, o.ml, "csak a múltból tanult modell becslése alapján")}
      ${row("Modell + mostani szűrők", o.ml_live, "a kettő együtt")}
    </tbody></table></div>
    <p class="muted small">Rangkorreláció a becslés és a tényleges eredmény között (IC): <b>${o.ic == null ? "–" : fmt(o.ic, 3)}</b> – 0 körül: nincs előrejelző erő; 0,05 fölött már használható jel.</p>` : ""}
    <div class="grid-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <div><h3>Évenként</h3><table><thead><tr><th>Év</th><th class="num">Tanító</th><th class="num">Minden</th><th class="num">Szűrők</th><th class="num">Modell</th><th class="num">IC</th></tr></thead><tbody>
        ${r.years.map((y) => `<tr><td>${y.year}</td><td class="num">${y.train}</td><td class="num">${labPeriod(y.all)}</td><td class="num">${labPeriod(y.live)}</td><td class="num">${labPeriod(y.ml)}</td><td class="num">${y.ic == null ? "–" : fmt(y.ic, 2)}</td></tr>`).join("")}</tbody></table></div>
      <div><h3>Mire figyel a modell</h3><p class="muted small">Permutációs fontosság az utolsó teszt-éven.</p>
        ${(r.features || []).map((f) => `<div class="lab-feat"><span>${esc(f.name)}</span><span class="lab-feat-bar"><span style="width:${Math.max(0, (f.importance / maxImp) * 100)}%"></span></span></div>`).join("") || "–"}</div>
    </div>
    <p class="muted small">Tanítva: ${esc(String(r.trained || "").slice(0, 16).replace("T", " "))} · ${r.rows} minta · ${pending}</p>${btn}
    ${labShadow(ml.shadow)}`;
}

const SHADOW_MODE = { auto: "automatikus – csak mintán kívül bizonyított modellel", on: "bekapcsolva", off: "kikapcsolva" };

function labShadow(sh) {
  if (!sh) return "";
  const g = sh.groups;
  const row = (label, s, help) => `<tr><td>${label}<div class="muted small">${help}</div></td><td class="num">${s.n}</td><td class="num">${labWin(s)}</td><td class="num">${labEv(s)}</td></tr>`;
  const state = sh.active ? `<span class="badge go">fut</span>` : `<span class="badge fail">készenlétben</span>`;
  const why = sh.active ? "A modell becslése megjelenik a belépési jelzések mellett és az értesítésekben, és naplózódik – de semmiről nem dönt."
    : !sh.model ? "Még nincs mentett modell – az első tanítás után lesz."
    : sh.mode === "off" ? "Kikapcsolva (settings.yaml: lab.shadow)."
    : "Vár: a legutóbbi teszt szerint a modell még nem bizonyítottan jobb a mostani szűrőknél. Ha az lesz, magától elindul.";
  return `<h3 style="margin-top:18px">Árnyék-üzem ${state}</h3>
    <p class="muted small">${why} Mód: <b>${esc(SHADOW_MODE[sh.mode] || sh.mode)}</b>${sh.model ? ` · modell: ${esc(String(sh.model.trained || "").slice(0, 10))}, ${sh.model.rows} mintából, szint: ${esc(sh.model.level)}` : ""}</p>
    ${sh.predictions ? `<p class="muted small">${sh.predictions} naplózott jelzés ${sh.since ? `(${esc(sh.since)} óta)` : ""} · ${sh.resolved} lefutott · ${sh.pending} még fut</p>
    <div class="table-wrap"><table><thead><tr><th>Valódi új adaton (árnyék-üzem)</th><th class="num">Trade</th><th class="num">Nyerés</th><th class="num">EV (95%)</th></tr></thead><tbody>
      ${row("Minden naplózott kitörés", g.all, "a szűrőn elbukottakkal együtt")}
      ${row("A modell választotta", g.pick, "becslés a küszöb fölött")}
      ${row("A modell elvetette", g.skip, "becslés a küszöb alatt")}
      ${row("Szűrőn átment + modell választotta", g.qualified_pick, "ezek mentek volna ki úgy is")}
      ${row("Szűrőn átment + modell elvetette", g.qualified_skip, "ezeket szűrné ki a modell")}
    </tbody></table></div>
    <p class="muted small">Akkor érdemes a modellt élesben szűrőként használni, ha itt a „modell választotta” tartósan jobb az „elvetette” sornál, és a „szűrőn átment + elvetette” sor gyengébb.</p>` : ""}`;
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
  if (location.hash.startsWith("#/p/")) route();
});
$("#refresh").addEventListener("click", startRefresh);
$("#best").addEventListener("click", startBest);
// kilepes gomb csak akkor, ha a felulet jelszavas (szerveren futtatva)
api("/api/session").then((s) => { $("#logout").hidden = !s.auth; }).catch(() => {});
$("#logout").addEventListener("click", () => {
  api("/logout", { method: "POST" }).catch(() => {}).finally(() => { location.href = "/login"; });
});
window.addEventListener("hashchange", route);
route();

// ---------------------------------------------------------------------------
// Backteszt: egy papir / ETF teljes tortenete a kivalasztott datumig, a rendszer strategiaival
// (docs/BACKTEST-PAGE-DESIGN.md). A szamolas ugyanaz, mint az eles futasban es a kutatasban.
// ---------------------------------------------------------------------------
var btTimer = null;         // var: a route() a fajl betoltesekor, e sor elott is lefut
var BT_FORM_KEY = "sw-backtest-form";
var BT_PARAM_LABELS = {
  rsi2_max: "RSI(2) küszöb (alatta jelez)", trend_sma: "Trend SMA (záró felette)", regime_filter: "Rezsimszűrő",
  limit_pct: "Vételi limit a záró alatt (%)", stop_atr: "Stop (ATR-szeres)", atr_period: "ATR periódus",
  exit_crsi: "Kilépés: ConnorsRSI felett", exit_sma: "Kilépés: záró az SMA felett (0 = ki)", time_stop: "Időkorlát (nap)",
  min_price: "Min. ár ($)", min_dollar_volume: "Min. dollárforgalom ($/nap)", earnings_blackout_days: "Gyorsjelentés-tilalom (nap)",
  max_signals_per_day: "Napi max. jelzés (0 = nincs)", max_open: "Max. nyitott (0 = nincs)", cost_bps: "Költség (bps / oldal)",
};
var BT_REASON = { signal: "kilépési jel", time: "időkorlát", stop: "stop", gap_stop: "rés a stop alatt" };

function btLoadForm() {
  try { return JSON.parse(localStorage.getItem(BT_FORM_KEY) || "{}"); } catch (_) { return {}; }
}
function btSaveForm(v) {
  try { localStorage.setItem(BT_FORM_KEY, JSON.stringify(v)); } catch (_) { /* privat mod */ }
}

async function pageBacktest(runId) {
  clearInterval(btTimer);
  const d = await api("/api/backtest");
  const result = runId ? await api(`/api/backtest/run/${runId}`).catch((e) => ({ error: e.message })) : null;
  // mentett futtatas megnyitasakor az urlap a futtatas beallitasait mutatja (igy konnyu modositani)
  const saved = result && !result.error
    ? { symbol: result.symbol, start: result.start, end: result.holdout ? result.end : "", strategy: result.strategy,
      params: result.overrides || {}, capital: result.capital, alloc_pct: result.alloc_pct }
    : btLoadForm();
  const strategies = d.strategies;
  const sym = (saved.symbol || (d.symbols[0] && d.symbols[0].symbol) || "SPY").toUpperCase();
  const strat = strategies.find((s) => s.id === saved.strategy && s.available) || strategies.find((s) => s.available);

  view.innerHTML = `
    <section class="card">
      <h2>Backteszt <span class="muted">– egy papír vagy ETF teljes története a kiválasztott dátumig, a rendszer stratégiáival</span></h2>
      <div class="card-body">
        <form id="bt-form" class="bt-form" autocomplete="off">
          <fieldset><legend>1. Adat</legend>
            <label>Ticker <input name="symbol" list="bt-symbols" value="${esc(sym)}" required maxlength="12" style="width:8em;text-transform:uppercase"></label>
            <datalist id="bt-symbols">${d.symbols.map((m) => `<option value="${esc(m.symbol)}">${esc(m.first)} → ${esc(m.last)}</option>`).join("")}</datalist>
            <label>Kezdődátum <input type="date" name="start" value="${esc(saved.start || "2000-01-01")}" min="1980-01-01" required></label>
            <button class="btn" type="button" id="bt-fetch">Adatok összegyűjtése</button>
            <div id="bt-data" class="small muted" style="margin-top:6px"></div>
          </fieldset>
          <fieldset><legend>2. Stratégia</legend>
            <label>Stratégia <select name="strategy">${strategies.map((s) => `<option value="${esc(s.id)}" ${s === strat ? "selected" : ""} ${s.available ? "" : "disabled"}>${esc(STRAT[s.id] ? STRAT[s.id][1] : s.label)}${s.available ? "" : " (hamarosan)"}</option>`).join("")}</select></label>
            <details id="bt-params-box"><summary>Paraméterek <span class="muted small">(a módosítás csak erre a futtatásra érvényes, az éles beállítást nem írja át)</span></summary>
              <div id="bt-params" class="bt-params"></div></details>
          </fieldset>
          <fieldset><legend>3. Futtatás</legend>
            <label>Vége <input type="date" name="end" value="${esc(saved.end || "")}"> <span class="muted small">üres: a zárolt holdout előtt (${esc(d.holdout_start)} előtti nap)</span></label>
            <label>Kezdőtőke ($) <input type="number" name="capital" value="${esc(saved.capital || 10000)}" min="100" step="100" style="width:8em"></label>
            <label>Pozíció a tőke %-ában <input type="number" name="alloc_pct" value="${esc(saved.alloc_pct || 100)}" min="1" max="100" step="1" style="width:5em"></label>
            <label><input type="checkbox" name="include_holdout"> a zárolt holdout is (${esc(d.holdout_start)}-tól – naplózva)</label>
            <button class="btn primary" type="submit" id="bt-run">Futtatás</button>
          </fieldset>
        </form>
        <div id="bt-job" class="small" style="margin-top:8px"></div>
      </div>
    </section>
    <div id="bt-result">${result ? (result.error ? `<section class="card"><div class="card-body empty">${esc(result.error)}</div></section>` : btResult(result)) : ""}</div>
    ${btRunsCard(d)}`;

  const form = $("#bt-form");
  const showData = () => {
    const s = form.symbol.value.trim().toUpperCase();
    const m = d.symbols.find((x) => x.symbol === s);
    $("#bt-data").innerHTML = m
      ? `Letöltve: <b>${esc(m.first)} → ${esc(m.last)}</b> (${fmt(m.bars, 0)} gyertya, ${esc(new Date(m.fetched_at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }))})`
        + (m.quality && m.quality.issues.length ? ` · <span style="color:var(--warn)">${esc(m.quality.issues.join("; "))}</span>` : " · adatminőség: rendben")
        + (m.stale ? ` · <span style="color:var(--warn)">elavult – gyűjtsd újra</span>` : "")
      : `Erről a papírról még nincs adat – nyomd meg az „Adatok összegyűjtése” gombot. <span class="muted">(Tár: ${fmt(d.usage_mb, 1)} / ${d.limit_mb} MB)</span>`;
  };
  const showParams = () => {
    const s = strategies.find((x) => x.id === form.strategy.value);
    const over = (saved.strategy === s.id && saved.params) || {};
    $("#bt-params").innerHTML = s.params ? Object.entries(s.params).map(([k, v]) => {
      const cur = k in over ? over[k] : v;
      const input = typeof v === "boolean"
        ? `<input type="checkbox" data-param="${esc(k)}" data-default="${v}" ${cur ? "checked" : ""}>`
        : `<input type="number" step="any" data-param="${esc(k)}" data-default="${esc(v)}" value="${esc(cur)}" style="width:9em">`;
      return `<label title="${esc(k)} – alapérték: ${esc(v)}">${esc(BT_PARAM_LABELS[k] || k)} ${input}</label>`;
    }).join("") : `<span class="muted">${esc(s.note || "")}</span>`;
    if (Object.keys(over).length) $("#bt-params-box").open = true;
  };
  const values = () => {
    const params = {};
    view.querySelectorAll("#bt-params [data-param]").forEach((el) => {
      const k = el.dataset.param;
      if (el.type === "checkbox") { if (String(el.checked) !== el.dataset.default) params[k] = el.checked; }
      else if (el.value !== "" && Number(el.value) !== Number(el.dataset.default)) params[k] = Number(el.value);
    });
    return { symbol: form.symbol.value.trim().toUpperCase(), start: form.start.value, end: form.end.value,
      strategy: form.strategy.value, params, capital: Number(form.capital.value), alloc_pct: Number(form.alloc_pct.value),
      include_holdout: form.include_holdout.checked };
  };
  showData(); showParams();
  form.symbol.addEventListener("input", showData);
  form.strategy.addEventListener("change", showParams);

  const poll = () => {
    clearInterval(btTimer);
    btTimer = setInterval(async () => {
      if (!location.hash.startsWith("#/backtest")) { clearInterval(btTimer); return; }
      const job = await api("/api/backtest/job").catch(() => null);
      if (!job) return;
      btJob(job);
      if (job.state !== "running") {
        clearInterval(btTimer);
        if (job.state === "done" && job.kind === "run" && job.run_id) location.hash = `#/backtest/run/${job.run_id}`;
        else if (job.state === "done") pageBacktest(runId);
      }
    }, 1500);
  };
  btJob(d.job);
  if (d.job.state === "running") poll();

  $("#bt-fetch").addEventListener("click", async () => {
    const v = values();
    btSaveForm({ ...btLoadForm(), symbol: v.symbol, start: v.start });
    try { const r = await api("/api/backtest/data", { method: "POST", body: JSON.stringify({ symbol: v.symbol, start: v.start }) }); btJob(r.job); poll(); }
    catch (e) { btJob({ state: "error", message: e.message }); }
  });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const v = values();
    if (v.include_holdout && !confirm("A zárolt holdout megnyitása naplózódik, és utána ez az időszak már nem független teszt. Biztosan?")) return;
    btSaveForm(v);
    try { const r = await api("/api/backtest/run", { method: "POST", body: JSON.stringify(v) }); btJob(r.job); poll(); }
    catch (e) { btJob({ state: "error", message: e.message }); }
  });
  bindRowLinks();
  view.querySelectorAll("button[data-bt-delete]").forEach((b) => b.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!confirm("Törlöd ezt a mentett futtatást?")) return;
    await api("/api/backtest/delete", { method: "POST", body: JSON.stringify({ id: b.dataset.btDelete }) });
    location.hash = "#/backtest"; pageBacktest(null);
  }));
  const unf = $("#bt-unfilled");
  if (unf) unf.addEventListener("change", () => $("#bt-trades").classList.toggle("show-unfilled", unf.checked));
  const csv = $("#bt-csv");
  if (csv && result && !result.error) csv.addEventListener("click", () => btDownloadCsv(result));
  if (result && !result.error) btDrawCharts(result);
}

function btJob(job) {
  const el = $("#bt-job");
  if (!el || !job || job.state === "idle") { if (el) el.innerHTML = ""; return; }
  const cls = job.state === "error" ? "down" : job.state === "done" ? "up" : "";
  const label = job.state === "running" ? "Fut" : job.state === "done" ? "Kész" : "Hiba";
  el.innerHTML = `<span class="badge ${job.state === "error" ? "bad" : job.state === "done" ? "go" : "watch"}">${label}</span>
    <span class="${cls}">${esc(job.kind === "data" ? "Adatgyűjtés" : "Futtatás")}${job.symbol ? ` (${esc(job.symbol)})` : ""}: ${esc(job.message)}</span>`;
}

function btResult(r) {
  const a = r.periods.all, sm = r.strategy_metrics, bm = r.buy_hold_metrics;
  const ci = r.win_ci && r.win_ci[0] !== null ? ` <span class="muted small">(${fmt(r.win_ci[0] * 100, 0)}–${fmt(r.win_ci[1] * 100, 0)}%)</span>` : "";
  const kpi = (label, value, sub, cls = "") => `<div class="kpi"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="sub">${sub}</div></div>`;
  const pl = (v, d = 1) => (v === null || v === undefined ? "–" : pct(v, d));
  const periodRow = (name, label) => {
    const s = r.periods[name];
    if (!s) return "";
    return `<tr><td>${label}</td><td class="num">${s.n || 0}</td><td class="num">${s.win === null || s.win === undefined ? "–" : fmt(s.win * 100, 1) + "%"}
      ${s.win_lo !== undefined && s.win_lo !== null ? `<span class="muted small">(${fmt(s.win_lo * 100, 0)}–${fmt(s.win_hi * 100, 0)})</span>` : ""}</td>
      <td class="num ${tone(s.ev)}">${pl(s.ev, 2)}</td><td class="num">${s.avg_win !== undefined ? `${pl(s.avg_win, 2)} / ${pl(s.avg_loss, 2)}` : "–"}</td>
      <td class="num">${s.pf === null || s.pf === undefined ? (s.n ? "∞" : "–") : fmt(s.pf, 2)}</td><td class="num">${s.held !== undefined ? fmt(s.held, 1) : "–"}</td>
      <td class="num">${s.stop_share !== undefined ? fmt(s.stop_share * 100, 0) + "%" : "–"}</td></tr>`;
  };
  const params = Object.entries(r.overrides || {});
  return `
  <section class="card">
    <h2>${esc(r.symbol)} · ${esc(STRAT[r.strategy] ? STRAT[r.strategy][1] : r.strategy_label)} <span class="muted">– ${esc(r.start)} → ${esc(r.end)} · költség ${fmt(r.params.cost_bps, 0)} bps / oldal · pozíció ${fmt(r.alloc_pct, 0)}%</span>
      <span style="margin-left:auto;font-weight:400"><button class="btn" type="button" id="bt-csv">Kötések CSV</button></span></h2>
    <div class="card-body">
      ${params.length ? `<div class="small" style="margin-bottom:6px">Módosított paraméterek: ${params.map(([k, v]) => `<code>${esc(k)} = ${esc(v)}</code>`).join(" ")}</div>` : ""}
      ${r.warnings.map((w) => `<div class="callout" style="margin:4px 0">${esc(w)}</div>`).join("")}
      ${r.hidden_after ? `<div class="muted small" style="margin-top:4px">A zárolt holdout (${esc(r.hidden_after)} utáni időszak) rejtve – a backtest adatai ennél a napnál véget érnek.</div>` : ""}
    </div>
    <div class="kpis">
      ${kpi("Lezárt kötés", fmt(a.n || 0, 0), `${r.open_at_end ? `+ ${r.open_at_end} nyitott a végén · ` : ""}heti ${fmt(a.fills_per_week, 2)}`)}
      ${kpi("Nyerési arány", a.win === null || a.win === undefined ? "–" : fmt(a.win * 100, 1) + "%" + ci, `PF ${a.pf === null || a.pf === undefined ? (a.n ? "∞" : "–") : fmt(a.pf, 2)}`)}
      ${kpi("Átlag nettó / kötés", pl(a.ev, 2), `nyerő ${pl(a.avg_win, 2)} · vesztes ${pl(a.avg_loss, 2)}`, tone(a.ev))}
      ${kpi("CAGR", pl(sm.cagr), `vettem és tartottam: ${pl(bm.cagr)}`, tone(sm.cagr))}
      ${kpi("Max. visszaesés", pl(sm.max_dd), `vettem és tartottam: ${pl(bm.max_dd)}`)}
      ${kpi("Sharpe", fmt(sm.sharpe, 2), `vettem és tartottam: ${fmt(bm.sharpe, 2)} · piacon: ${fmt(sm.exposure, 0)}%`)}
    </div>
  </section>
  <section class="card chart-card"><h2>Tőkegörbe <span class="muted">– <span class="bt-key s1"></span>stratégia, <span class="bt-key s2"></span>vettem és tartottam (${fmt(r.capital, 0)} $ kezdőtőke, logaritmikus skála)</span></h2>
    <div class="card-body"><svg id="bt-equity" class="bt-chart" role="img" aria-label="Tőkegörbe"></svg>
    <svg id="bt-dd" class="bt-chart" role="img" aria-label="Visszaesés"></svg></div></section>
  <section class="card chart-card"><h2>${esc(r.symbol)} árfolyam a kötésekkel <span class="muted">– <span class="up">▲</span> belépés, <span class="down">▼</span> kilépés (zöld: nyerő, piros: vesztes)</span></h2>
    <div class="card-body"><svg id="bt-price" class="bt-chart" role="img" aria-label="Árfolyam a kötésekkel"></svg></div></section>
  <section class="card"><h2>Időszakok</h2><div class="table-wrap"><table>
    <thead><tr><th>Időszak</th><th class="num">Kötés</th><th class="num">Nyerési arány (95%)</th><th class="num">Átlag nettó</th><th class="num">Átl. nyerő / vesztes</th><th class="num">PF</th><th class="num">Tartás (nap)</th><th class="num">Stop-arány</th></tr></thead>
    <tbody>${periodRow("all", "Teljes vizsgált időszak")}${periodRow("train", "Tanító (2022 előtt)")}${periodRow("test", "Validáció (2022 – 2025.03)")}${periodRow("holdout", "HOLDOUT (2025.04-től)")}</tbody></table></div></section>
  <section class="card"><h2>Évenként</h2><div class="table-wrap"><table>
    <thead><tr><th>Év</th><th class="num">Stratégia</th><th class="num">Vettem és tartottam</th><th class="num">Kötés</th><th class="num">Nyerési arány</th></tr></thead>
    <tbody>${r.years.map((y) => `<tr><td>${y.year}</td><td class="num ${tone(y.strategy)}">${pl(y.strategy)}</td><td class="num ${tone(y.buy_hold)}">${pl(y.buy_hold)}</td>
      <td class="num">${y.n}</td><td class="num">${y.win === null ? "–" : fmt(y.win * 100, 0) + "%"}</td></tr>`).join("")}</tbody></table></div></section>
  <section class="card"><h2>Kötések <span class="muted">– ${r.trades.filter((t) => t.entry_date).length} teljesült, ${r.trades.filter((t) => !t.entry_date).length} nem teljesült limit</span>
    <label class="small muted" style="margin-left:auto;font-weight:400"><input type="checkbox" id="bt-unfilled"> nem teljesültek is</label></h2>
    <div class="table-wrap bt-trades" id="bt-trades"><table>
    <thead><tr><th>Jelzés napja</th><th>Állapot</th><th class="num">Limit</th><th class="num">Stop</th><th>Belépés</th><th class="num">Ár</th><th>Kilépés</th><th class="num">Ár</th><th>Ok</th><th class="num">Nap</th><th class="num">Nettó</th><th class="num">R</th></tr></thead>
    <tbody>${r.trades.slice().reverse().map((t) => `<tr${t.entry_date ? "" : ` class="bt-unfilled"`}>
      <td>${esc(t.signal_date)}<div class="muted small">RSI(2) ${fmt(t.rsi2, 1)}</div></td>
      <td>${t.status === "closed" ? (t.net_pct > 0 ? `<span class="badge go">nyerő</span>` : `<span class="badge bad">vesztes</span>`) : t.status === "unfilled" ? `<span class="badge fail">nem teljesült</span>` : `<span class="badge watch">nyitott</span>`}</td>
      <td class="num">${fmt(t.limit)}</td><td class="num">${fmt(t.stop)}</td>
      <td>${esc(t.entry_date || "–")}</td><td class="num">${t.entry_price ? fmt(t.entry_price) : "–"}</td>
      <td>${esc(t.exit_date || "–")}</td><td class="num">${t.exit_price ? fmt(t.exit_price) : "–"}</td>
      <td>${esc(BT_REASON[t.reason] || t.reason || "–")}</td><td class="num">${t.held || "–"}</td>
      <td class="num ${tone(t.net_pct)}">${t.net_pct === null || t.net_pct === undefined ? "–" : pct(t.net_pct, 2)}</td>
      <td class="num">${t.r === null || t.r === undefined ? "–" : signed(t.r, 2)}</td></tr>`).join("")}</tbody></table></div></section>`;
}

function btRunsCard(d) {
  if (!d.runs.length) return "";
  return `<section class="card"><h2>Mentett futtatások <span class="muted">– legfeljebb 50, a legújabb elöl</span></h2><div class="table-wrap"><table>
    <thead><tr><th>Mikor</th><th>Papír</th><th>Stratégia</th><th>Időszak</th><th class="num">Kötés</th><th class="num">Nyerési arány</th><th class="num">Átlag</th><th class="num">CAGR</th><th class="num">Vettem és tartottam</th><th class="num">Max DD</th><th></th></tr></thead>
    <tbody>${d.runs.map((x) => `<tr class="clickable" data-href="#/backtest/run/${esc(x.id)}">
      <td>${esc(new Date(x.created_at).toLocaleString("hu-HU", { dateStyle: "short", timeStyle: "short" }))}</td>
      <td class="ticker">${esc(x.symbol)}</td>
      <td>${esc(STRAT[x.strategy] ? STRAT[x.strategy][1] : x.strategy_label)}${Object.keys(x.overrides || {}).length ? ` <span class="muted small">(${Object.entries(x.overrides).map(([k, v]) => `${esc(k)}=${esc(v)}`).join(", ")})</span>` : ""}</td>
      <td>${esc(x.start)} → ${esc(x.end)}${x.holdout ? ` <span class="badge warn">holdout</span>` : ""}</td>
      <td class="num">${x.n || 0}</td><td class="num">${x.win === null || x.win === undefined ? "–" : fmt(x.win * 100, 1) + "%"}</td>
      <td class="num ${tone(x.ev)}">${x.ev === null || x.ev === undefined ? "–" : pct(x.ev, 2)}</td>
      <td class="num ${tone(x.cagr)}">${pct(x.cagr, 1)}</td><td class="num">${pct(x.bh_cagr, 1)}</td><td class="num">${pct(x.max_dd, 1)}</td>
      <td><button class="btn" type="button" data-bt-delete="${esc(x.id)}" title="Törlés">✕</button></td></tr>`).join("")}</tbody></table></div></section>`;
}

function btDownloadCsv(r) {
  const cols = ["signal_date", "status", "limit", "stop", "rsi2", "natr", "entry_date", "entry_price", "exit_date", "exit_price", "reason", "held", "gross_pct", "net_pct", "r"];
  const lines = [cols.join(";")].concat(r.trades.map((t) => cols.map((c) => (t[c] === null || t[c] === undefined ? "" : String(t[c]).replace(".", ","))).join(";")));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `backteszt-${r.symbol}-${r.strategy}-${r.start}-${r.end}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// Egyszeru, tobbsoros vonaldiagram datum-tengellyel (log skalaval is), opcionalis jelolokkel.
function btLineChart(svg, t, series, opts = {}) {
  if (!svg || !t.length) return;
  const W = Math.max(320, svg.parentElement.clientWidth);
  const H = opts.height || 260;
  const pad = { l: 8, r: 72, t: 12, b: 24 };
  const tr = opts.log ? (v) => Math.log10(Math.max(v, 1e-9)) : (v) => v;
  const all = series.flatMap((s) => s.v).filter((v) => v !== null && Number.isFinite(v));
  let lo = Math.min(...all.map(tr)), hi = Math.max(...all.map(tr));
  if (opts.zeroTop) hi = Math.max(hi, 0);
  const span = hi - lo || 1;
  lo -= span * 0.06; hi += span * 0.06;
  const n = t.length;
  const x = (i) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (hi - tr(v)) / (hi - lo) * (H - pad.t - pad.b);
  const parts = [];
  const ticks = opts.log ? niceTicks(lo, hi, 4).map((v) => 10 ** v) : niceTicks(lo, hi, 4);
  parts.push(`<g class="grid">${ticks.map((v) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>`);
  parts.push(`<g class="axis">${ticks.map((v) => `<text x="${W - pad.r + 8}" y="${y(v) + 4}">${opts.fmtY ? opts.fmtY(v) : fmt(v, 0)}</text>`).join("")}</g>`);
  let lastYear = null;
  const yearTicks = [];
  t.forEach((d, i) => { const yr = d.slice(0, 4); if (yr !== lastYear) { yearTicks.push([i, yr]); lastYear = yr; } });
  const every = Math.max(1, Math.ceil(yearTicks.length / Math.max(2, Math.floor(W / 70))));
  parts.push(`<g class="axis">${yearTicks.filter((_, k) => k % every === 0).map(([i, yr]) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : "middle"}">${yr}</text>`).join("")}</g>`);
  if (opts.area) {
    const s = series[0];
    const path = s.v.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    parts.push(`<path class="bt-area" d="${path}L${x(n - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z"/>`);
  }
  series.forEach((s) => {
    let pen = "M";
    const path = s.v.map((v, i) => {
      if (v === null || !Number.isFinite(v)) { pen = "M"; return ""; }
      const seg = `${pen}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = "L";
      return seg;
    }).join("");
    parts.push(`<path class="bt-line ${s.cls}" d="${path}"/>`);
  });
  (opts.markers || []).forEach((m) => {
    const i = btIndex(t, m.date);
    if (i < 0) return;
    const cx = x(i), cy = y(m.price);
    const pts = m.up ? `${cx},${cy + 2} ${cx - 5},${cy + 10} ${cx + 5},${cy + 10}` : `${cx},${cy - 2} ${cx - 5},${cy - 10} ${cx + 5},${cy - 10}`;
    parts.push(`<polygon class="bt-mark ${m.cls}" points="${pts}"><title>${esc(m.title)}</title></polygon>`);
  });
  parts.push(`<g class="bt-cross"></g><rect class="bt-hit" x="${pad.l}" y="${pad.t}" width="${W - pad.l - pad.r}" height="${H - pad.t - pad.b}" fill="transparent"/>`);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");
  const hit = svg.querySelector(".bt-hit"), cross = svg.querySelector(".bt-cross");
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * (W / r.width);
    const i = Math.max(0, Math.min(n - 1, Math.round((px - pad.l) / ((W - pad.l - pad.r) / Math.max(1, n - 1)))));
    cross.innerHTML = `<line class="cross" x1="${x(i)}" x2="${x(i)}" y1="${pad.t}" y2="${H - pad.b}"/>`;
    tooltip.innerHTML = `<strong>${esc(t[i])}</strong>` + series.map((s) => `<div class="row"><span>${esc(s.name)}</span><span>${s.v[i] === null || s.v[i] === undefined ? "–" : opts.fmtTip ? opts.fmtTip(s.v[i]) : fmt(s.v[i], 2)}</span></div>`).join("");
    tooltip.hidden = false;
    tooltip.style.left = `${ev.clientX + 16 + 200 > window.innerWidth ? ev.clientX - 216 : ev.clientX + 16}px`;
    tooltip.style.top = `${ev.clientY + 12}px`;
  });
  hit.addEventListener("mouseleave", () => { cross.innerHTML = ""; tooltip.hidden = true; });
}

function btIndex(t, date) {        // az elso index, amelyik >= date (ISO datumok, rendezve)
  let lo = 0, hi = t.length - 1;
  if (!date || date < t[0] || date > t[hi]) return -1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (t[mid] < date) lo = mid + 1; else hi = mid; }
  return lo;
}

function btDrawCharts(r) {
  const eq = r.equity, bh = r.buy_hold;
  const bhOnEq = eq.t.map((d) => { const i = btIndex(bh.t, d); return i < 0 ? null : bh.v[i]; });
  btLineChart($("#bt-equity"), eq.t, [{ name: "Stratégia", v: eq.v, cls: "s1" }, { name: "Vettem és tartottam", v: bhOnEq, cls: "s2" }],
    { log: true, fmtY: (v) => fmt(v, 0), fmtTip: (v) => (v === null ? "–" : `${fmt(v, 0)} $`) });
  btLineChart($("#bt-dd"), r.drawdown.t, [{ name: "Visszaesés", v: r.drawdown.v, cls: "dd" }],
    { height: 120, area: true, zeroTop: true, fmtY: (v) => `${fmt(v, 0)}%`, fmtTip: (v) => `${fmt(v, 1)}%` });
  const markers = [];
  r.trades.filter((t) => t.entry_date).forEach((t) => {
    const win = t.status === "closed" ? (t.net_pct > 0 ? "win" : "loss") : "open";
    markers.push({ date: t.entry_date, price: t.entry_price, up: true, cls: win, title: `Belépés ${t.entry_date}: ${fmt(t.entry_price)} $` });
    if (t.exit_date) markers.push({ date: t.exit_date, price: t.exit_price, up: false, cls: win, title: `Kilépés ${t.exit_date}: ${fmt(t.exit_price)} $ (${BT_REASON[t.reason] || t.reason}, ${pct(t.net_pct, 2)})` });
  });
  btLineChart($("#bt-price"), r.price.t, [{ name: r.symbol, v: r.price.v, cls: "price" }],
    { height: 300, log: true, markers, fmtY: (v) => fmt(v, v < 10 ? 2 : 0), fmtTip: (v) => `${fmt(v, 2)} $` });
}


// ---------------------------------------------------------------------------
// Hirek egy papirra (sentiment, arnyek-uzem) - docs/SENTIMENT-DESIGN.md
// ---------------------------------------------------------------------------
const NEWS_LABEL = { negative: ["bad", "negatív"], positive: ["go", "pozitív"], neutral: ["fail", "semleges"] };
const NEWS_SOURCE = { edgar: "SEC", yahoo: "Yahoo", alpaca: "Alpaca" };

async function loadNews(symbol, refresh = false) {
  const card = $("#news-card");
  if (!card) return;
  try {
    const d = refresh ? await api("/api/news/refresh", { method: "POST", body: JSON.stringify({ symbol }) })
      : await api(`/api/news/${encodeURIComponent(symbol)}`);
    if (!$("#news-card")) return;
    card.innerHTML = newsCard(d);
    const btn = $("#news-refresh");
    if (btn) btn.addEventListener("click", async () => { btn.disabled = true; btn.textContent = "Frissítés…"; await loadNews(symbol, true); });
  } catch (e) {
    card.innerHTML = `<h2>Hírek</h2><div class="card-body muted">Nem tölthető be: ${esc(e.message)}</div>`;
  }
}

function newsCard(d) {
  const f = d.features || {};
  const when = (iso) => new Date(iso).toLocaleString("hu-HU", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const last = Object.values(d.fetched || {}).sort().pop();
  const rows = d.articles.slice(0, 25).map((a) => {
    const sc = a.score || {};
    const [cls, label] = NEWS_LABEL[sc.label] || ["fail", "?"];
    const ev = sc.event ? ` <span class="muted small">· ${esc(d.event_labels[sc.event] || sc.event)}${sc.materiality >= 2 ? " (lényeges)" : ""}</span>` : "";
    const title = a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>` : esc(a.title);
    return `<li><span class="badge ${cls}">${label}</span> ${title}${ev}
      <div class="muted small">${esc(when(a.published_at))} · ${esc(NEWS_SOURCE[a.source] || a.source)}${a.publisher && a.source !== "edgar" ? ` · ${esc(a.publisher)}` : ""}</div></li>`;
  }).join("");
  return `<h2>Hírek <span class="muted">– tájékoztató, a jelzést nem befolyásolja</span>
      <button class="btn" type="button" id="news-refresh" style="margin-left:auto" title="Letöltés: SEC EDGAR, Yahoo${d.fetched && d.fetched.alpaca ? ", Alpaca" : ""}">Frissítés</button></h2>
    <div class="card-body">
      <div class="${f.material_neg_3d ? "down" : ""}"><b>Ma:</b> ${esc(d.summary)}</div>
      <div class="muted small">A döntés csak a 16:00 (New York) előtt megjelent híreket látja. ${last ? `Utolsó letöltés: ${esc(when(last))}.` : "Még nem volt letöltés."}</div>
      ${rows ? `<ul class="news-list">${rows}</ul>` : `<div class="muted" style="margin-top:6px">Nincs tárolt hír – nyomd meg a „Frissítés” gombot.</div>`}
    </div>`;
}
