/* A rejtett chart rajzolasa (SVG) - kozos az appban (app.js) es a kezdooldali kiprobalhato helyzetben (demo.js).
 * Az i18n.js utan toltodik (LOCALE). */
"use strict";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }));

function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const stepv = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / stepv) * stepv; v <= hi; v += stepv) out.push(Number(v.toFixed(6)));
  return out;
}

// a rejtett chart: index-tengely (0 = a dontes napja), normalizalt ar, relativ volumen; a felfedeskor a jovo
function practiceChart(svg, bars, opt = {}) {
  const future = (opt.future || []).slice(0, opt.shown ?? (opt.future || []).length);
  const all = bars.concat(future);
  const W = Math.max(320, svg.parentElement.clientWidth);
  const H = W < 600 ? 340 : 420;
  // a jobb margo a leghosszabb szint-cimkehez igazodik (legfeljebb 112 px)
  const longest = Math.max(0, ...(opt.levels || []).map((h) => String(h.label || "").length));
  const pad = { l: 8, r: Math.min(112, Math.max(64, 14 + 6.4 * longest)), t: 12, b: 24 };
  const volH = Math.round(H * 0.14);
  const priceH = H - pad.t - pad.b - volH - 8;
  const slots = bars.length + (opt.future ? opt.future.length : 0);
  const step = (W - pad.l - pad.r) / slots;
  const x = (j) => pad.l + step * j + step / 2;
  let lo = Infinity, hi = -Infinity;
  all.forEach((k) => { lo = Math.min(lo, k.l); hi = Math.max(hi, k.h); });
  (opt.segments || []).forEach((s) => { lo = Math.min(lo, s.v1, s.v2); hi = Math.max(hi, s.v1, s.v2); });
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
  parts.push(`<line class="tip-hit" data-tip="zero" x1="${x(zero) + step / 2}" x2="${x(zero) + step / 2}" y1="${pad.t}" y2="${volTop + volH}"/>`);
  parts.push(`<g>${bars.map((k, j) => (k.v == null ? "" : `<rect data-tip="v:${j}" class="vol ${k.c >= k.o ? "up" : "down"}" x="${x(j) - step * 0.35}" y="${vy(k.v)}" width="${Math.max(1, step * 0.7)}" height="${volTop + volH - vy(k.v)}"/>`)).join("")}</g>`);
  const bw = Math.max(1, step * 0.62);
  parts.push(`<g>${all.map((k, j) => {
    const top = y(Math.max(k.o, k.c)), bot = y(Math.min(k.o, k.c));
    return `<g data-tip="c:${j}" class="candle ${k.c >= k.o ? "up" : "down"}${j > zero ? " future" : ""}"><line x1="${x(j)}" x2="${x(j)}" y1="${y(k.h)}" y2="${y(k.l)}"/><rect x="${x(j) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}"/></g>`;
  }).join("")}</g>`);
  ["ema20", "ema50", "ema200"].forEach((key) => {
    let dpath = "";
    bars.forEach((k, j) => { if (k[key] != null) dpath += `${dpath ? "L" : "M"}${x(j).toFixed(1)},${y(k[key]).toFixed(1)}`; });
    if (dpath) parts.push(`<path class="ema ${key}" d="${dpath}"/><path class="tip-hit" data-tip="${key}" d="${dpath}"/>`);
  });
  (opt.marks || []).forEach((m, n) => {
    const j = zero + m.k;
    if (m.k == null || j >= all.length || m.v == null) return;
    parts.push(`<circle data-tip="m:${n}" cx="${x(j)}" cy="${y(m.v)}" r="4.5" fill="${m.color}" stroke="var(--surface)" stroke-width="1.5"><title>${esc(m.label)}</title></circle>`);
  });
  (opt.segments || []).forEach((s) => {
    parts.push(`<line class="hline ${s.dash ? "dash" : ""}" x1="${x(zero + s.k1)}" y1="${y(s.v1)}" x2="${x(zero + s.k2)}" y2="${y(s.v2)}" stroke="${s.color}"/>`);
  });
  let prevLabel = -Infinity;                       // az egymashoz kozeli cimkek nem fedik egymast
  levels.slice().sort((a, b) => b.v - a.v).forEach((h) => {
    const yy = y(h.v);
    const ly = Math.max(yy, prevLabel + 18);
    prevLabel = ly;
    const tip = `data-tip="l:${levels.indexOf(h)}"`;
    parts.push(`<line class="hline ${h.dash ? "dash" : ""}" x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}" stroke="${h.color}"/>`);
    parts.push(`<line class="tip-hit" ${tip} x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}"/>`);
    parts.push(`<rect ${tip} class="hlabel-bg" x="${W - pad.r + 1}" y="${ly - 9}" width="${pad.r - 2}" height="18" rx="4" fill="${h.color}"/>`);
    parts.push(`<text ${tip} class="hlabel" x="${W - pad.r + 5}" y="${ly + 4}" fill="#fff">${esc(h.label)}</text>`);
  });
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");
  svg.__map = { lo, hi, padT: pad.t, priceH, step, padL: pad.l, zero, W, H };     // erintes -> (nap, ar): chartPoint
  svg.__tip = { all, zero, levels, marks: opt.marks || [] };
  chartTips(svg);
}

// a chart elemeinek neve (es erteke) egerrel / erintessel: a data-tip jelolesbol, a kozos #tooltip dobozban
const EMA_NAMES = { ema20: "EMA20 – 20 napos exponenciális mozgóátlag", ema50: "EMA50 – 50 napos exponenciális mozgóátlag",
  ema200: "EMA200 – 200 napos exponenciális mozgóátlag" };
function chartTips(svg) {
  const tooltip = document.getElementById("tooltip");
  if (svg.__tipBound || !tooltip) return;
  svg.__tipBound = true;
  const day = (j) => { const rel = j - svg.__tip.zero; return `${rel > 0 ? "+" : ""}${rel}`; };
  const row = (k, v) => `<div class="row"><span>${esc(k)}</span><span>${v}</span></div>`;
  const content = (tip, ev) => {
    const { all, levels, marks } = svg.__tip;
    const [kind, n] = tip.split(":");
    if (kind === "c" || kind === "v") {
      const k = all[Number(n)];
      if (kind === "v") return `<strong>Relatív volumen</strong>${row("a 20 napos átlaghoz képest", `${fmt(k.v, 2)}×`)}${row("Nap", day(Number(n)))}`;
      return `<strong>${k.c >= k.o ? "Emelkedő gyertya" : "Eső gyertya"}</strong>${row("Nap", day(Number(n)))}${row("Nyitó", fmt(k.o, 2))}`
        + `${row("Csúcs", fmt(k.h, 2))}${row("Mélypont", fmt(k.l, 2))}${row("Záró", fmt(k.c, 2))}`;
    }
    if (EMA_NAMES[kind]) {                       // a vonal erteke a mutato alatti napon
      const m = svg.__map, box = svg.getBoundingClientRect();
      const j = Math.max(0, Math.min(all.length - 1, Math.floor(((ev.clientX - box.left) * (m.W / box.width) - m.padL) / m.step)));
      return `<strong>${esc(EMA_NAMES[kind])}</strong>${all[j][kind] != null ? row("Nap", day(j)) + row(kind.toUpperCase(), fmt(all[j][kind], 2)) : ""}`;
    }
    if (kind === "l") return `<strong>${esc(levels[Number(n)].label)}</strong>${row("Szint", fmt(levels[Number(n)].v, 2))}`;
    if (kind === "m") return `<strong>${esc(marks[Number(n)].label)}</strong>${row("Szint", fmt(marks[Number(n)].v, 2))}`;
    return `<strong>A döntés napja</strong>`;
  };
  const show = (ev) => {
    const el = ev.target.closest && ev.target.closest("[data-tip]");
    if (!el || !svg.contains(el)) { tooltip.hidden = true; return; }
    tooltip.innerHTML = content(el.dataset.tip, ev);
    tooltip.hidden = false;
    const w = tooltip.offsetWidth, h = tooltip.offsetHeight;
    tooltip.style.left = `${Math.min(ev.clientX + 14, innerWidth - w - 8)}px`;
    tooltip.style.top = `${ev.clientY + 14 + h > innerHeight ? ev.clientY - h - 10 : ev.clientY + 14}px`;
  };
  svg.addEventListener("pointermove", show);
  svg.addEventListener("pointerdown", show);
  svg.addEventListener("pointerleave", () => { tooltip.hidden = true; });
}
