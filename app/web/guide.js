/* StockWatch - tankonyvi alakzat-utmutato (info-panel a belepesi helyzetnel).
 *
 * Minden alakzat egy sematikus rajz 0-100 koordinatakon (y felfele = magasabb ar)
 * es egy rovid leiras. A medves alakzatok a bikas parjuk tukrozesei, igy a ket
 * irany garantaltan egyseges. A rajz inline SVG, a szinek a tema CSS-valtozoi.
 */
"use strict";

// path: az arfolyam utja | lines: trend- / nyakvonalak | trigger: kitoresi szint (y) es kezdete (x)
// breakout: a path pontjanak indexe, ahol kitor | stop: a stop szintje | height: az alakzat magassaga
// (x-nel, y1-tol y2-ig) - ezt vetitjuk ki a triggertol: ez a "mert elmozdulas" cel
const GUIDE_SHAPES = {
  "Dupla alj": {
    path: [[0, 88], [14, 50], [30, 16], [46, 54], [62, 17], [78, 54], [90, 72], [99, 80]],
    lines: [], trigger: { y: 54, x: 46 }, breakout: 5, stop: 11, height: { x: 30, y1: 16, y2: 54 },
  },
  "Tripla alj": {
    path: [[0, 88], [10, 48], [20, 16], [32, 50], [44, 17], [56, 50], [68, 16], [80, 51], [90, 70], [99, 80]],
    lines: [], trigger: { y: 51, x: 32 }, breakout: 7, stop: 11, height: { x: 44, y1: 17, y2: 50 },
  },
  "Forditott fej-vall": {
    path: [[0, 90], [10, 52], [20, 32], [32, 56], [46, 8], [60, 57], [72, 32], [84, 58], [93, 74], [99, 82]],
    lines: [[32, 56, 90, 58.4]], trigger: { y: 58, x: 32 }, breakout: 7, stop: 27, height: { x: 46, y1: 8, y2: 56.5 },
  },
  "Emelkedo haromszog": {
    path: [[0, 12], [14, 66], [26, 36], [38, 66], [50, 46], [62, 66], [72, 55], [82, 66], [91, 80], [99, 88]],
    lines: [[14, 66, 86, 66], [26, 36, 80, 60]], trigger: { y: 66, x: 14 }, breakout: 7, stop: 50, height: { x: 26, y1: 36, y2: 66 },
  },
  "Szimmetrikus haromszog": {
    path: [[0, 12], [12, 80], [26, 34], [40, 72], [54, 44], [66, 64], [76, 52], [85, 60], [93, 76], [99, 84]],
    lines: [[12, 80, 88, 56], [26, 34, 86, 56]], trigger: { y: 59, x: 66 }, breakout: 7, stop: 45, height: { x: 26, y1: 34, y2: 76 },
  },
  "Eso ek": {
    path: [[0, 94], [10, 76], [20, 54], [30, 66], [42, 40], [52, 52], [62, 32], [72, 40], [80, 27], [88, 42], [95, 56], [99, 62]],
    lines: [[10, 76, 88, 36], [20, 54, 82, 26]], trigger: { y: 38, x: 62 }, breakout: 9, stop: 22, height: { x: 20, y1: 54, y2: 72 },
  },
  "Bull zaszlo": {
    path: [[0, 8], [10, 20], [24, 72], [32, 62], [40, 68], [48, 58], [56, 63], [64, 55], [74, 68], [86, 84], [99, 94]],
    lines: [[24, 72, 70, 60], [32, 62, 64, 55]], trigger: { y: 62, x: 24 }, breakout: 8, stop: 50, height: { x: 10, y1: 20, y2: 72 },
  },
  "Teglalap (range)": {
    path: [[0, 10], [12, 66], [24, 38], [36, 66], [48, 38], [60, 66], [72, 40], [82, 66], [91, 80], [99, 88]],
    lines: [[12, 66, 86, 66], [24, 38, 86, 38]], trigger: { y: 66, x: 12 }, breakout: 7, stop: 33, height: { x: 48, y1: 38, y2: 66 },
  },
  "Csesze-ful": {
    path: [[0, 82], [8, 66], [18, 38], [32, 20], [46, 22], [58, 40], [68, 66], [76, 80], [82, 70], [88, 73], [93, 82], [99, 92]],
    lines: [[0, 82, 92, 80]], trigger: { y: 81, x: 0 }, breakout: 10, stop: 66, height: { x: 32, y1: 20, y2: 81 },
  },
};
// a medves parok: a bikas rajz tukrozese
const GUIDE_MIRROR = {
  "Dupla teto": "Dupla alj", "Tripla teto": "Tripla alj", "Fej-vall": "Forditott fej-vall",
  "Csokkeno haromszog": "Emelkedo haromszog", "Emelkedo ek": "Eso ek", "Bear zaszlo": "Bull zaszlo",
};

const GUIDE_TEXT = {
  "Dupla alj": {
    title: "Dupla alj (W)", type: "Fordulós, bikás",
    text: "Esés után az ár kétszer, közel azonos szinten talál támaszt, a két mélypont között visszapattan. Az eladók kétszer sem tudták lejjebb vinni – a kereslet átveszi az irányítást.",
    rules: ["Kell előtte esés (amit megfordít)", "Trigger: a két alj közötti csúcs (nyakvonal) feletti záró", "Stop: a mélypontok alatt", "Cél: a mélypont–nyakvonal távolság a nyakvonaltól felfelé"],
  },
  "Dupla teto": {
    title: "Dupla tető (M)", type: "Fordulós, medvés",
    text: "Emelkedés után az ár kétszer akad el közel azonos csúcson. A vevők kétszer sem tudták feljebb vinni – a nyakvonal letörése a forduló jele.",
    rules: ["Kell előtte emelkedés", "Trigger: a két csúcs közötti mélypont alatti záró", "Érvénytelen: új csúcs a tetők felett", "Cél: a tető–nyakvonal távolság lefelé"],
  },
  "Tripla alj": {
    title: "Tripla alj", type: "Fordulós, bikás",
    text: "Mint a dupla alj, de háromszor tesztelt támasz. Ritkább, viszont az erősebb, többször bizonyított támasz miatt megbízhatóbb fordulót jelez.",
    rules: ["Kell előtte esés", "Trigger: a közbenső csúcsok (ellenállás) feletti záró", "Stop: a három alj alatt", "Cél: az alakzat magassága felfelé"],
  },
  "Tripla teto": {
    title: "Tripla tető", type: "Fordulós, medvés",
    text: "Háromszor elutasított ellenállás egy emelkedés végén. A támasz letörése után a felhalmozott eladási nyomás gyors esést hozhat.",
    rules: ["Kell előtte emelkedés", "Trigger: a közbenső mélypontok alatti záró", "Érvénytelen: záró a tetők felett", "Cél: az alakzat magassága lefelé"],
  },
  "Forditott fej-vall": {
    title: "Fordított fej-váll", type: "Fordulós, bikás",
    text: "Három mélypont, a középső (fej) a legmélyebb, a két váll közel azonos. Az egyik legismertebb aljképző alakzat: az utolsó váll már magasabban fordul, az eladók kifogytak.",
    rules: ["Kell előtte esés", "Trigger: a nyakvonal (a vállak közötti csúcsokat összekötő vonal) feletti záró", "Stop: a jobb váll alatt", "Cél: fej–nyakvonal távolság a nyakvonaltól felfelé"],
  },
  "Fej-vall": {
    title: "Fej-váll", type: "Fordulós, medvés",
    text: "Három csúcs, a középső (fej) a legmagasabb. A jobb váll már alacsonyabb – a vevők gyengülnek. A nyakvonal letörése a klasszikus csúcsképző jel.",
    rules: ["Kell előtte emelkedés", "Trigger: a nyakvonal alatti záró", "Érvénytelen: záró a jobb váll felett", "Cél: fej–nyakvonal távolság lefelé"],
  },
  "Emelkedo haromszog": {
    title: "Emelkedő háromszög", type: "Folytatólagos, bikás",
    text: "Vízszintes ellenállás, egyre magasabb mélypontok: a vevők minden visszaesésnél korábban lépnek be, a kínálat a plafonon fogy. A kitörés jellemzően felfelé történik.",
    rules: ["Kell előtte emelkedés", "Trigger: a vízszintes plafon feletti záró", "Stop: az utolsó emelkedő mélypont alatt", "Cél: a háromszög legszélesebb része a kitöréstől felfelé"],
  },
  "Csokkeno haromszog": {
    title: "Csökkenő háromszög", type: "Folytatólagos, medvés",
    text: "Vízszintes támasz, egyre alacsonyabb csúcsok: az eladók egyre korábban adnak el. A támasz letörése a trend folytatását jelzi.",
    rules: ["Kell előtte esés", "Trigger: a vízszintes támasz alatti záró", "Érvénytelen: záró az utolsó csúcs felett", "Cél: a háromszög magassága lefelé"],
  },
  "Szimmetrikus haromszog": {
    title: "Szimmetrikus háromszög", type: "Folytatólagos (a megelőző trend irányában)",
    text: "Csökkenő csúcsok és emelkedő aljak, összetartó vonalak: a piac kivár, az ingadozás szűkül. Általában a korábbi trend irányába tör ki – ezért kell előtte trend.",
    rules: ["Kell előtte trend (azt folytatja)", "Trigger: a háromszög oldalának áttörése zárón", "Stop: az ellentétes oldal mögött", "Cél: a háromszög legszélesebb része a kitöréstől"],
  },
  "Eso ek": {
    title: "Eső ék", type: "Bikás (forduló vagy folytatás)",
    text: "Mindkét vonal lefelé tart, de összetartanak: az esés lassul, az új mélypontok egyre kisebbek. Az eladói nyomás kifullad – a felső vonal áttörése felfelé fordulást jelez.",
    rules: ["Trigger: a felső (eső) vonal feletti záró", "Stop: az ék utolsó mélypontja alatt", "Cél: az ék kezdeti szélessége felfelé", "Volumen az ékben jellemzően csökken"],
  },
  "Emelkedo ek": {
    title: "Emelkedő ék", type: "Medvés (forduló vagy folytatás)",
    text: "Mindkét vonal felfelé tart, de összetartanak: az emelkedés lendülete fogy. Az alsó vonal letörése gyakran gyors esést hoz.",
    rules: ["Trigger: az alsó (emelkedő) vonal alatti záró", "Érvénytelen: záró az ék csúcsa felett", "Cél: az ék kezdeti szélessége lefelé"],
  },
  "Bull zaszlo": {
    title: "Bull zászló", type: "Folytatólagos, bikás",
    text: "Erős, meredek emelkedés (zászlórúd), majd rövid, enyhén lefelé tartó csatorna (a zászló) – a piac kifújja magát, mielőtt folytatja. A rendszerben ez a leggyakoribb és legjobban mért alakzat.",
    rules: ["Kell előtte erős emelkedés (a rúd)", "A zászló rövid és sekély (a rúd felénél kevesebbet ad vissza)", "Trigger: a csatorna felső vonala feletti záró", "Cél: a rúd hossza a kitöréstől felfelé"],
  },
  "Bear zaszlo": {
    title: "Bear zászló", type: "Folytatólagos, medvés",
    text: "Meredek esés (rúd), majd rövid, enyhén felfelé tartó csatorna. A csatorna alsó vonalának letörése az esés folytatását jelzi.",
    rules: ["Kell előtte erős esés", "Trigger: a csatorna alsó vonala alatti záró", "Érvénytelen: záró a zászló felett", "Cél: a rúd hossza lefelé"],
  },
  "Teglalap (range)": {
    title: "Téglalap (oldalazás)", type: "Folytatólagos (a kitörés irányában)",
    text: "Az ár két vízszintes szint között oldalaz: vevők és eladók egyensúlyban. A kitörés iránya dönt – trend után általában arra, amerre korábban ment.",
    rules: ["Trigger: záró a sávon kívül", "Stop: a sáv belsejében / a túloldalon", "Cél: a sáv magassága a kitöréstől", "A hamis kitörés gyakori – ezért vár a rendszer megerősítő zárót"],
  },
  "Csesze-ful": {
    title: "Csésze-fül", type: "Folytatólagos, bikás",
    text: "Lekerekített, U alakú bázis (csésze), majd a jobb peremnél kis, sekély visszahúzódás (fül). A lassú, kerek aljzat a bizalom visszatérését mutatja, a fül kirázza a gyenge kezeket.",
    rules: ["Kell előtte emelkedés", "A fül rövid, és a csésze felső felében marad", "Trigger: a perem feletti záró", "Cél: a csésze mélysége a peremtől felfelé"],
  },
};

const GUIDE_KIND = {
  breakout: ["Megerősített kitörés", "Az ár tegnap zárt először a trigger fölött, és a mai záró is fölötte maradt. Belépés másnap nyitáskor, limittel (+1%); a hamis kitörések nagy részét ez a megerősítő nap szűri ki."],
  retest: ["Visszateszt", "Kitörés után az ár visszajött a trigger-szinthez, megtartotta (nem zárt alatta), és felfelé fordult. Szorosabb stop, de ritkább helyzet."],
  setup: ["Előkészület – még nem vétel", "Az alakzat még nem tört ki, de az ár legfeljebb 1 ATR-re van a triggertől. Csak akkor vétel, ha egy záró a trigger fölé kerül."],
};

function guideShape(name, direction) {
  const base = GUIDE_SHAPES[name] || GUIDE_SHAPES[GUIDE_MIRROR[name]];
  if (!base) return null;
  const mirror = GUIDE_MIRROR[name] || ((name === "Szimmetrikus haromszog" || name === "Teglalap (range)") && direction === "bearish");
  if (!mirror) return { ...base, bear: false };
  const f = (y) => 100 - y;
  return {
    path: base.path.map(([x, y]) => [x, f(y)]),
    lines: base.lines.map(([x1, y1, x2, y2]) => [x1, f(y1), x2, f(y2)]),
    trigger: { y: f(base.trigger.y), x: base.trigger.x },
    breakout: base.breakout,
    stop: f(base.stop),
    height: { x: base.height.x, y1: f(base.height.y2), y2: f(base.height.y1) },
    bear: true,
  };
}

function guideSvg(shape) {
  const W = 320, H = 168, pad = { l: 8, r: 46, t: 12, b: 12 };
  const h = Math.abs(shape.height.y2 - shape.height.y1);
  // a cel a teljes kivetitett magassag: a rajz aranyat igazitjuk hozza, nem a celt vagjuk le
  const target = shape.trigger.y + (shape.bear ? -h : h);
  const ys = shape.path.map((p) => p[1]).concat([shape.stop, target, shape.trigger.y]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const sx = (x) => pad.l + (x / 100) * (W - pad.l - pad.r);
  const sy = (y) => pad.t + (1 - (y - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
  const bp = shape.path[shape.breakout];
  const tx = 97;
  const parts = [];
  // stop es trigger
  parts.push(`<line x1="${sx(shape.trigger.x)}" x2="${sx(100)}" y1="${sy(shape.trigger.y)}" y2="${sy(shape.trigger.y)}" class="g-trigger"/>`);
  parts.push(`<line x1="${sx(shape.trigger.x)}" x2="${sx(100)}" y1="${sy(shape.stop)}" y2="${sy(shape.stop)}" class="g-stop"/>`);
  parts.push(`<line x1="${sx(bp[0])}" x2="${sx(100)}" y1="${sy(target)}" y2="${sy(target)}" class="g-target"/>`);
  // alakzat-vonalak
  shape.lines.forEach(([x1, y1, x2, y2]) => parts.push(`<line x1="${sx(x1)}" y1="${sy(y1)}" x2="${sx(x2)}" y2="${sy(y2)}" class="g-line"/>`));
  // magassag es a kivetitett cel
  const hy1 = Math.min(shape.height.y1, shape.height.y2), hy2 = Math.max(shape.height.y1, shape.height.y2);
  parts.push(`<line x1="${sx(shape.height.x)}" x2="${sx(shape.height.x)}" y1="${sy(hy1)}" y2="${sy(hy2)}" class="g-measure" marker-start="url(#g-arr)" marker-end="url(#g-arr)"/>`);
  parts.push(`<line x1="${sx(tx)}" x2="${sx(tx)}" y1="${sy(shape.trigger.y)}" y2="${sy(target)}" class="g-measure" marker-end="url(#g-arr)"/>`);
  // arfolyam
  parts.push(`<polyline points="${shape.path.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ")}" class="g-price"/>`);
  parts.push(`<circle cx="${sx(bp[0])}" cy="${sy(bp[1])}" r="5" class="g-break"/>`);
  // cimkek
  // cimkek - egymastol legalabb 12 px-re, hogy ne csusszanak egymasra
  const labels = [[target, "Cél", "up"], [shape.trigger.y, "Trigger", "info"], [shape.stop, "Stop", "down"]]
    .map(([y, text, cls]) => ({ py: sy(y) + 4, text, cls }))
    .sort((a, b) => a.py - b.py);
  for (let i = 1; i < labels.length; i++) labels[i].py = Math.max(labels[i].py, labels[i - 1].py + 12);
  const overflow = labels[labels.length - 1].py - (H - 2);
  if (overflow > 0) labels.forEach((l) => { l.py -= overflow; });
  labels.forEach((l) => parts.push(`<text x="${W - pad.r + 4}" y="${l.py.toFixed(1)}" class="g-label ${l.cls}">${l.text}</text>`));
  return `<svg viewBox="0 0 ${W} ${H}" class="guide-svg" role="img" aria-label="Sematikus tankönyvi példa">
    <defs><marker id="g-arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" class="g-arrowhead"/></marker></defs>${parts.join("")}</svg>`;
}

function guideHtml(name, direction, kind, stats) {
  const text = GUIDE_TEXT[name];
  const shape = guideShape(name, direction);
  if (!text || !shape) return `<div class="muted">Ehhez az alakzathoz még nincs tankönyvi leírás.</div>`;
  const k = GUIDE_KIND[kind];
  const st = stats && stats.samples ? stats : null;
  return `<div class="guide-head"><strong>${esc(text.title)}</strong><span class="badge ${shape.bear ? "bad" : "go"}">${esc(text.type)}</span></div>
    ${guideSvg(shape)}
    <div class="guide-legend"><span><i class="sw price"></i>ár</span><span><i class="sw line"></i>alakzat</span><span><i class="sw meas"></i>magasság → cél</span><span><i class="dot"></i>kitörés</span></div>
    <p>${esc(text.text)}</p>
    <ul>${text.rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${k ? `<div class="guide-kind"><strong>${esc(k[0])}:</strong> ${esc(k[1])}</div>` : ""}
    <div class="guide-stats">${st
      ? `Ebben a rendszerben (backtest): <strong>${fmt(st.hit_rate * 100, 0)}%</strong> találat, várható érték <strong>${signed(st.expectancy_r, 2, "R")}</strong> · ${st.samples} historikus minta${st.samples < 30 ? " – <em>kevés minta, óvatosan</em>" : ""}`
      : "Ebben a rendszerben még nincs elég historikus minta erre az alakzatra."}</div>
    <div class="muted small">Sematikus tankönyvi példa – a valódi alakzat ritkán ilyen szabályos.</div>`;
}

// --- a lebego panel ----------------------------------------------------------
const guidePanel = document.getElementById("guide");
let guideTimer = null;
let guidePinned = null;

function showGuide(btn) {
  clearTimeout(guideTimer);
  let stats = null;
  try { stats = JSON.parse(btn.dataset.stats || "null"); } catch (_) { stats = null; }
  guidePanel.innerHTML = guideHtml(btn.dataset.guide, btn.dataset.dir, btn.dataset.kind, stats);
  guidePanel.hidden = false;
  const r = btn.getBoundingClientRect();
  const pw = guidePanel.offsetWidth, ph = guidePanel.offsetHeight;
  let left = r.right + 10;
  if (left + pw > window.innerWidth - 8) left = r.left - pw - 10;
  left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
  let top = r.top - 20;
  top = Math.max(8, Math.min(top, window.innerHeight - ph - 8));
  guidePanel.style.left = `${left}px`;
  guidePanel.style.top = `${top}px`;
  btn.setAttribute("aria-expanded", "true");
}

function hideGuide(delay = 120) {
  clearTimeout(guideTimer);
  guideTimer = setTimeout(() => {
    guidePanel.hidden = true;
    guidePinned = null;
    document.querySelectorAll(".info-btn[aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  }, delay);
}

function infoButton(name, direction, kind, stats) {
  if (!GUIDE_TEXT[name]) return "";
  return `<button type="button" class="info-btn" aria-label="${esc(name)}: tankönyvi példa és leírás" aria-expanded="false"
    data-guide="${esc(name)}" data-dir="${esc(direction || "bullish")}" data-kind="${esc(kind || "")}" data-stats="${esc(JSON.stringify(stats || null))}">i</button>`;
}

// esemenydelegalas: minden (kesobb renderelt) info-gombra mukodik
document.addEventListener("mouseover", (ev) => {
  const btn = ev.target.closest && ev.target.closest(".info-btn");
  if (btn && !guidePinned) showGuide(btn);
});
document.addEventListener("mouseout", (ev) => {
  const btn = ev.target.closest && ev.target.closest(".info-btn");
  if (btn && !guidePinned && !btn.contains(ev.relatedTarget)) hideGuide();
});
document.addEventListener("focusin", (ev) => { if (ev.target.classList && ev.target.classList.contains("info-btn")) showGuide(ev.target); });
document.addEventListener("focusout", (ev) => { if (ev.target.classList && ev.target.classList.contains("info-btn") && !guidePinned) hideGuide(); });
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest && ev.target.closest(".info-btn");
  if (btn) {                       // erinto kepernyon: koppintasra nyit / zar
    ev.preventDefault();
    ev.stopPropagation();
    if (guidePinned === btn) { hideGuide(0); return; }
    guidePinned = btn;
    showGuide(btn);
    return;
  }
  if (guidePinned) hideGuide(0);
}, true);   // capture: a kattinthato tablazatsor ne navigaljon el az info-gombra kattintva
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hideGuide(0); });
window.addEventListener("scroll", () => { if (!guidePanel.hidden) hideGuide(0); }, { passive: true });
