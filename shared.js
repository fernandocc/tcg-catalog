/* Shared helpers used by several pages: icons, HTML escaping, accent folding, duplicate runs,
   the batch report, rarity symbols and the player's-guide finish boxes.
   Pages that show a batch report define their own renderReport(); the report's click/input
   handlers below call it. */

/* ---------- icons (inline SVG, stroke = currentColor) ---------- */
const ICON_PATHS = {
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
  camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3.5"/>',
  layers: '<path d="m12 2 9 5-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  skip: '<path d="m5 4 10 8-10 8V4z"/><path d="M19 5v14"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>',
  play: '<path d="M6 4v16l14-8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  help: '<circle cx="12" cy="12" r="9.5"/><path d="M9.2 9a3 3 0 0 1 5.6 1.2c0 2-3 2.3-3 4.3"/><path d="M12 17.5h.01"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>',
  sound: '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/>',
  mute: '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
  sparkle: '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
};
// "$1,234.56" -- market prices are TCGplayer USD
const usd = v => "$" + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function icon(name, extra) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' +
    (extra ? " " + extra : "") + ">" + (ICON_PATHS[name] || "") + "</svg>";
}
/* fill every <i data-icon="name"></i> placeholder in static HTML */
function hydrateIcons(root) {
  (root || document).querySelectorAll("i[data-icon]").forEach(el => { el.outerHTML = icon(el.dataset.icon); });
}
document.addEventListener("DOMContentLoaded", () => hydrateIcons());

/* ---------- text helpers ---------- */
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
/* accent-insensitive text: "pokemon" matches "Pokémon", typing e matches é */
const fold = t => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/* ---------- duplicate runs: 5+ duplicates in a row can go into the dupes bucket together ---------- */
const DUP_RUN_MIN = 5;
function dupRun(list, idx) {
  if (!list[idx] || !list[idx].duplicate) return null;
  let s = idx, e = idx;
  while (s > 0 && list[s - 1].duplicate) s--;
  while (e < list.length - 1 && list[e + 1].duplicate) e++;
  return { start: s, end: e, len: e - s + 1 };
}
function dupRuns(list) {
  const runs = []; let k = 0;
  while (k < list.length) {
    const r = dupRun(list, k);
    if (r) { if (r.len >= DUP_RUN_MIN) runs.push(r); k = r.end + 1; } else k++;
  }
  return runs;
}

/* ---------- batch report: what to do with this stack ---------- */
function batchReport(cards) {
  const n = cards.length;
  if (!n) return { html: '<div class="empty-state"><b>No scans yet</b>The report fills in as cards are scanned.</div>' };
  const distinct = new Set(cards.map(c => c.card_id || c.name + c.number + c.set)).size;
  const all = cards.map((c, k) => ({ c, pos: k + 1 }));
  const review = all.filter(x => x.c.needs_review);
  const fresh = all.filter(x => !x.c.duplicate);
  const dups = n - fresh.length;
  const prior = cards.filter(c => c.duplicate && c.in_prior_batch).length;
  const within = dups - prior;
  const so = window.__rsort || (window.__rsort = { k: "pos", d: 1 });
  const cmp = {
    pos: (a, b) => a.pos - b.pos,
    card: (a, b) => a.c.name.localeCompare(b.c.name) || a.pos - b.pos,
    set: (a, b) => (a.c.set || "~").localeCompare(b.c.set || "~") || a.pos - b.pos,
    end: (a, b) => (n - a.pos) - (n - b.pos),
  }[so.k];
  const arrow = k => so.k === k ? (so.d === 1 ? " ▲" : " ▼") : "";
  const th = (k, label) => '<th class="rth" data-k="' + k + '" title="Click to sort">' + label + arrow(k) + "</th>";
  const flt = fold(window.__rfilter || "");
  const table = (items) => {
    const rows = items.filter(x => !flt || fold(x.c.name + " " + (x.c.set || "")).includes(flt)).sort((a, b) => so.d * cmp(a, b));
    return '<div class="rbox"><table class="rt"><thead><tr>' + th("pos", "Scan #") + th("card", "Card") + th("set", "Set") + th("end", "From end") + "</tr></thead><tbody>" +
      rows.map(x => '<tr data-pos="' + x.pos + '"><td><a href="#" class="rj" data-pos="' + x.pos + '">#' + x.pos + "</a></td><td>" + esc(x.c.name) +
        (x.c.number ? ' <span class="rpos">#' + esc(x.c.number) + "</span>" : "") + "</td><td>" + esc(x.c.set || "—") + "</td><td>" + (n - x.pos + 1) + "</td></tr>").join("") +
      "</tbody></table></div>";
  };
  const open = window.__rdOpen === false ? "" : " open";
  const runs = dupRuns(cards);
  const runsHtml = runs.length ? '<div class="rruns"><b>' + runs.length + " run" + (runs.length === 1 ? "" : "s") + " of " + DUP_RUN_MIN + "+ duplicates in a row</b> — dump each group together: " +
    runs.map(r => '<a href="#" class="rj" data-pos="' + (r.start + 1) + '">#' + (r.start + 1) + "–" + (r.end + 1) + " (" + r.len + ")</a>").join(", ") + "</div>" : "";
  const filterBox = '<input id="rfilter" class="field" type="search" placeholder="Filter by card or set (é = e)…" autocomplete="off" value="' + esc(window.__rfilter || "") + '">';
  let h = '<div class="r-stats">' +
    '<div class="r-stat"><b>' + n + "</b><span>scans</span></div>" +
    '<div class="r-stat new"><b>' + fresh.length + "</b><span>new</span></div>" +
    '<div class="r-stat dup"><b>' + dups + "</b><span>duplicates</span></div></div>" +
    '<div class="muted">' + distinct + " different cards. " + dups + " duplicate" + (dups === 1 ? "" : "s") + ": " + prior + " already in earlier batches, " + within + " repeated in this batch. " +
    (review.length ? '<span class="rrevtxt">' + review.length + " need review</span>" : "None need review.") + "</div>";
  if (fresh.length === 0) {
    h += runsHtml + '<div class="rall">All ' + n + " are duplicates" + (prior === n ? " of cards in earlier batches" : "") +
      " — the whole stack can go straight into the dupes pile.</div>";
  } else {
    h += runsHtml + '<div class="rnew"><b>' + fresh.length + " NOT duplicate" + (fresh.length === 1 ? "" : "s") + "</b> — pull these out and file them by set; everything else goes in the dupes pile.</div>" +
      '<details class="rd"' + open + "><summary>The " + fresh.length + " cards to pull</summary>" + filterBox + table(fresh) + "</details>";
  }
  if (review.length) h += '<details class="rd"' + open + "><summary>Needs review (" + review.length + ")</summary>" + table(review) + "</details>";
  return { html: h };
}
document.addEventListener("toggle", (e) => { if (e.target.classList && e.target.classList.contains("rd")) window.__rdOpen = e.target.open; }, true);
// click a column header to sort by it (click again to reverse)
document.addEventListener("click", (e) => {
  const t = e.target.closest && e.target.closest("th.rth");
  if (!t) return;
  const so = window.__rsort || (window.__rsort = { k: "pos", d: 1 });
  if (so.k === t.dataset.k) so.d = -so.d; else { so.k = t.dataset.k; so.d = 1; }
  if (typeof renderReport === "function") renderReport();
});
document.addEventListener("input", (e) => {
  if (!e.target || e.target.id !== "rfilter") return;
  const pos = e.target.selectionStart;
  window.__rfilter = e.target.value;
  if (typeof renderReport === "function") renderReport();
  const f = document.getElementById("rfilter");
  if (f) { f.focus(); f.setSelectionRange(pos, pos); }
});
// keep the list where it was when the report re-renders (picking a card must not jump the list to the top)
function keepScroll(el, rebuild) {
  const boxes = [...el.querySelectorAll(".rbox")].map(b => b.scrollTop), top = el.scrollTop;
  rebuild();
  el.querySelectorAll(".rbox").forEach((b, k) => { b.scrollTop = boxes[k] || 0; });
  el.scrollTop = top;
}
function markCurrentRow(pos) {
  document.querySelectorAll(".report tr[data-pos]").forEach(tr => tr.classList.toggle("cur", +tr.dataset.pos === pos));
}

/* ---------- rarity symbols (as printed in the Player's Guide) ---------- */
const DIAMOND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 1.5 22.5 12 12 22.5 1.5 12Z"/><path d="M12 6l1.6 4.4L18 12l-4.4 1.6L12 18l-1.6-4.4L6 12l4.4-1.6Z" fill="currentColor" stroke="none"/></svg>';
function rarityIcon(r) {
  const k = (r || "").trim().toLowerCase();
  if (!k) return "";
  const t = (cls, html, title) => '<span class="ri ' + cls + '" title="' + esc(title) + '">' + html + "</span>";
  if (k === "c" || k === "common") return t("c", "&#9679;", "common");
  if (k === "u" || k === "uncommon") return t("u", "&#9670;", "uncommon");
  if (k === "r" || k === "rare" || k.startsWith("rare holo") || k === "trainer gallery rare holo") return t("r", "&#9733;", "rare");
  if (k === "double rare") return t("rr", "&#9733;&#9733;", "double rare");
  if (k === "ultra rare" || k === "rare ultra" || k === "ace spec rare") return t("ur", "&#9733;&#9733;", k);
  if (k === "illustration rare") return t("ir", "&#9733;", "illustration rare");
  if (k === "special illustration rare") return t("ir", "&#9733;&#9733;", "special illustration rare");
  if (k === "mega hyper rare") return t("ir", DIAMOND, "Mega hyper rare");
  if (k.includes("hyper") || k.includes("secret") || k.includes("rainbow")) return t("ir", "&#9733;&#9733;&#9733;", r);
  if (k.includes("shiny")) return t("ir", "&#10022;", r);
  // One Piece Card Game
  if (k === "uc") return t("u", "&#9670;", "uncommon");
  if (k === "sr") return t("rr", "&#9733;&#9733;", "super rare");
  if (k === "sec") return t("ir", "&#9733;&#9733;&#9733;", "secret rare");
  if (k === "l") return t("ur", "L", "leader");
  if (k === "sp") return t("ir", "SP", "special card");
  if (k === "tr") return t("ir", "&#10022;", "treasure rare");
  if (k === "don!!") return t("c", "D", "DON!! card");
  return t("r", "&#9830;", r);  // promo / anything else
}

/* ---------- finish boxes: standard / foil / parallel (filled = owned) ---------- */
const FIN_LONG = { standard: "standard", foil: "foil", parallel: "parallel" };
const FIN_SHORT = { standard: "S", foil: "F", parallel: "P" };
function finishBoxes(possible, finishes, opts) {
  const o = opts || {}, labels = o.short ? FIN_SHORT : FIN_LONG;
  return (o.rarity !== undefined ? rarityIcon(o.rarity) : "") + (possible || []).map(f => {
    const n = (finishes || {})[f] || 0;
    return '<span class="fb ' + f + (n ? " have" : "") + '" title="' + f + (n ? " ×" + n : " (not owned)") + (o.guessed ? " — rarity unknown, guessed" : "") + '"><i>' +
      (n ? "&#10003;" : "") + "</i>" + labels[f] + (n > 1 ? "×" + n : "") + "</span>";
  }).join("");
}
const finCache = {};
async function showFinishes(el, cardId, force) {
  if (!el) return;
  if (!cardId) { el.innerHTML = ""; return; }
  const c = finCache[cardId];
  if (force || !c || Date.now() - c.t > 4000) {
    try { finCache[cardId] = { t: Date.now(), d: await (await fetch("/card-finishes?id=" + encodeURIComponent(cardId))).json() }; }
    catch (e) { return; }
  }
  const d = finCache[cardId].d;
  el.innerHTML = finishBoxes(d.possible, d.finishes, { rarity: d.rarity, guessed: d.finish_known === false });
}

/* ---------- printing picker: games whose catalog has one card per printing (One Piece) ----------
   The camera can't tell same-artwork printings apart (base / reprint / Jolly Roger foil), so the
   scanner commits the base printing and this picker moves the scan to the right one. */
function usesFinishButtons(c) { return !c || !c.game_id || c.game_id === "pokemon"; }
async function showPrintings(wrap, c, onChanged, errEl) {
  if (!wrap) return;
  if (usesFinishButtons(c) || !c.card_id) { wrap.style.display = "none"; wrap.innerHTML = ""; return; }
  const token = c.id + "|" + c.card_id;
  wrap.dataset.for = token;
  let list = [];
  try { list = await (await fetch("/printings?id=" + encodeURIComponent(c.card_id))).json(); } catch (e) { return; }
  if (wrap.dataset.for !== token) return;   // moved on to another card while this was loading
  if (list.length < 2) { wrap.style.display = "none"; wrap.innerHTML = ""; return; }
  const money = p => p ? " · " + usd(p) : "";
  wrap.style.display = "";
  wrap.innerHTML = '<div class="label">Printing</div><select class="field printing">' + list.map(x =>
    '<option value="' + esc(x.id) + '"' + (x.id === c.card_id ? " selected" : "") + ">" + esc(x.name) + " — " + esc(x.set || "") + money(x.price) + "</option>").join("") + "</select>";
  wrap.querySelector("select").onchange = async e => {
    try {
      const d = await (await fetch("/set-printing", { method: "POST", body: JSON.stringify({ id: c.id, card_id: e.target.value }) })).json();
      if (!d.ok) { if (errEl) errEl.textContent = d.error; return; }
      await onChanged();
    } catch (err) { if (errEl) errEl.textContent = String(err); }
  };
}
