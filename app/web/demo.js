/* Kiprobalhato helyzet a kezdooldalon, regisztracio nelkul (src/stockwatch/demo.py): egy rejtett chart, egy dontes,
 * utana az itelet, a kotes eredmenye es a levezetes. Semmi nem mentodik. A chart a kozos chart.js-bol. */
"use strict";

(() => {
  const box = document.getElementById("demo");
  if (!box) return;
  const svg = document.getElementById("pr-chart");
  const form = document.getElementById("demo-form");
  const out = document.getElementById("demo-result");
  const msg = document.getElementById("demo-msg");
  const VERDICT = { good: ["Jó döntés", "ok", "✓"], lucky: ["Szerencsés", "warn", "≈"], unlucky: ["Balszerencsés", "info", "↺"], bad: ["Rossz döntés", "bad", "✗"] };
  const REASON = { stop: "stop", gap_stop: "stop (nyitási rés)", target: "cél", time: "időkorlát", signal: "kilépési jel" };
  const signed = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, 2)} R`);
  const cls = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  let task = null, side = "skip", opt = {};

  async function call(path, body) {
    const init = { headers: { "X-StockWatch": "1", "Content-Type": "application/json" } };
    if (body) Object.assign(init, { method: "POST", body: JSON.stringify(body) });
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  const draw = () => practiceChart(svg, task.chart.bars, opt);
  const levels = () => {
    const f = form.elements, lv = [];
    if (side !== "skip" && f.stop.value) lv.push({ v: Number(f.stop.value), label: "Stop", color: "var(--down)" });
    if (side !== "skip" && f.target.value) lv.push({ v: Number(f.target.value), label: "Cél", color: "var(--up)", dash: true });
    return lv;
  };

  function pick(s) {
    side = s;
    form.querySelectorAll("[data-side]").forEach((b) => { b.classList.toggle("on", b.dataset.side === s); b.setAttribute("aria-pressed", String(b.dataset.side === s)); });
    form.querySelectorAll("[data-trade]").forEach((el) => { el.hidden = s === "skip"; });
    if (s !== "skip") {                              // javasolt stop: 1,5 ATR a belepotol (100)
      const atr = task.chart.atr || 2;
      form.elements.stop.value = (s === "long" ? 100 - 1.5 * atr : 100 + 1.5 * atr).toFixed(2);
      form.elements.target.value = "";
    }
    opt = { levels: levels() };
    draw();
  }

  function result(rev) {
    const v = rev.review && rev.review.verdict;
    const [label, vcls, icon] = (v && VERDICT[v.category]) || ["", "plain", ""];
    const u = rev.user, ref = rev.reference;
    const mine = rev.decision.action === "skip" ? ["Kihagytad", "", ""]
      : u.status === "closed" ? [signed(u.r), cls(u.r), `${REASON[u.reason] || u.reason || ""} · ${u.held} nap`] : ["Nem volt kötés", "", ""];
    const steps = (rev.review.steps || []).map((st) => `<li class="pr-step"><h3>${esc(st.title.text)}</h3>
      <ul class="digest-list">${st.lines.map((l) => `<li><span>${esc(l.text)}</span></li>`).join("")}</ul></li>`).join("");
    out.innerHTML = `<div class="pr-verdict ${vcls}" role="status"><span class="pr-verdict-icon" aria-hidden="true">${icon}</span>
        <div><div class="pr-verdict-label">${esc(label)}</div><div class="small">${esc(rev.review.verdict_line.text)}</div></div></div>
      <div class="kpis check-kpis">
        <div class="kpi"><div class="label">A te döntésed</div><div class="value ${mine[1]}">${mine[0]}</div><div class="sub">${esc(mine[2])}</div></div>
        <div class="kpi"><div class="label">Referencia-kötés</div><div class="value ${cls(ref.r)}">${signed(ref.r)}</div><div class="sub">következő nyitás, ${fmt(ref.stop_atr, 0)} ATR stop, ${ref.days} nap</div></div>
      </div>
      <details class="demo-steps"><summary>Levezetés lépésről lépésre</summary><ol class="pr-steps">${steps}</ol></details>
      <p class="demo-cta"><a class="btn primary" href="/register?src=demo">Ingyenes regisztráció – tíz szint és napi kihívás</a></p>
      <p class="muted small">Szimulált kötés generált árfolyamon. Nem befektetési tanács.</p>`;
  }

  form.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => pick(b.dataset.side)));
  ["stop", "target"].forEach((n) => form.elements[n].addEventListener("input", () => { opt = { levels: levels() }; draw(); }));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = form.elements;
    const body = side === "skip" ? { action: "skip" }
      : { action: side, stop: Number(f.stop.value), target: f.target.value ? Number(f.target.value) : null };
    msg.className = "small"; msg.textContent = "";
    try {
      const rev = await call("/api/demo/decide", body);
      form.querySelectorAll("input, button").forEach((el) => { el.disabled = true; });
      const u = rev.user, marks = [];
      if (u && u.entry_k != null) marks.push({ k: u.entry_k, v: u.entry, color: "var(--info)", label: "Belépés" });
      if (u && u.exit_k != null) marks.push({ k: u.exit_k, v: u.exit, color: u.r > 0 ? "var(--up)" : "var(--down)", label: "Kilépés" });
      opt = { levels: levels(), future: rev.future, marks };
      draw();
      result(rev);
    } catch (e) { msg.className = "msg err small"; msg.textContent = e.message; }
  });

  call("/api/demo").then((t) => {
    task = t;
    form.hidden = false;
    pick("skip");
    new ResizeObserver(draw).observe(svg.parentElement);
  }).catch(() => { box.hidden = true; });        // nincs feladatkeszlet: a blokk nem latszik
})();
