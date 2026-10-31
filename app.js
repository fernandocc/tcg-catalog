/* Shared top bar for every page: navigation + live status + start/stop controls.
   Styles live in theme.css (#appbar / .ab-*). Talks to sort_viewer.py's /api/* endpoints
   (control actions only work from this computer). All class names are ab-prefixed on
   purpose: a generic name here once collided with a page's own buttons. */
(function () {
  const PAGES = [["/", "Home"], ["scan_sort.html", "Scan & Sort"], ["live_viewer.html", "Scanner"], ["sort_viewer.html", "Sorter"], ["view_collection.html", "Collection"]];
  const here = location.pathname.split("/").pop() || "";
  const isCur = h => h === "/" ? (here === "" || here === "index.html") : here === h;
  const bolt = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>';

  document.body.classList.add("has-appbar");
  const bar = document.createElement("nav");
  bar.id = "appbar";
  bar.innerHTML =
    `<a class="ab-brand" href="/"><span class="ab-logo">${bolt}</span><span>TCG Catalog</span></a>` +
    PAGES.slice(1).map(([h, t]) => `<a class="ab-nav${isCur(h) ? " cur" : ""}" href="${h}">${t}</a>`).join("") +
    `<span class="ab-sp"></span>
     <span class="ab-stat" title="Phone camera server"><i class="dot" id="abCamDot"></i><span class="ab-label">Camera</span><button id="abCam" class="ab-btn go">Start</button></span>
     <span class="ab-stat solo" title="Is the phone page sending pictures?"><i class="dot" id="abPhoneDot"></i><span id="abPhone">Phone</span></span>
     <span class="ab-stat" title="Card scanner"><i class="dot" id="abScanDot"></i><span class="ab-label">Scanner</span><button id="abScan" class="ab-btn go">Start</button></span>
     <button class="ab-fin" id="abFin" title="Stop the scanner, back up, merge this batch into the collection and clear it">Finish batch</button>
     <a class="ab-help${here === "help.html" ? " cur" : ""}" href="help.html" title="How to use the app">Help</a>`;
  document.body.prepend(bar);
  const toast = document.createElement("div");
  toast.id = "apptoast";
  document.body.appendChild(toast);

  const $ = (id) => document.getElementById(id);
  let st = null, toastTimer = null, armed = false, armTimer = null;

  function say(msg, ms) {
    toast.textContent = msg; toast.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), ms || 5000);
  }
  async function api(path, body) {
    const r = await fetch(path, { method: "POST", body: JSON.stringify(body || {}) });
    if (r.status === 403) throw new Error("Controls only work on the computer running the app.");
    return r.json();
  }
  window.AppCtl = { api, say, status: () => st, refresh };

  function paint() {
    if (!st) return;
    $("abCamDot").className = "dot " + (st.camera ? "on" : "off");
    $("abCam").textContent = st.camera ? "Stop" : "Start";
    $("abCam").className = "ab-btn " + (st.camera ? "stop" : "go");
    $("abScanDot").className = "dot " + (st.scanner ? "on" : "off");
    $("abScan").textContent = st.scanner ? "Stop" : "Start";
    $("abScan").className = "ab-btn " + (st.scanner ? "stop" : "go");
    const streaming = st.camera && st.phone_streaming;
    $("abPhoneDot").className = "dot " + (streaming ? "on" : st.camera ? "wait" : "off");
    $("abPhone").textContent = !st.camera ? "Phone" : streaming ? "Phone live" : "Waiting for phone";
    if (!armed) $("abFin").textContent = "Finish batch" + (st.live_scans ? " · " + st.live_scans : "");
    document.dispatchEvent(new CustomEvent("appstatus", { detail: st }));
  }
  async function refresh() {
    try { st = await (await fetch("/api/status?t=" + Date.now())).json(); paint(); } catch (e) {}
  }
  async function act(btn, path, body, doneMsg) {
    btn.disabled = true;
    try {
      const r = await api(path, body);
      if (r.ok === false) say(r.error || r.message || "That didn't work.");
      else if (doneMsg) say(doneMsg, 8000);
    } catch (e) { say(String(e.message || e)); }
    btn.disabled = false; refresh();
  }
  $("abCam").onclick = () => act($("abCam"), "/api/camera", { action: st && st.camera ? "stop" : "start" },
    st && !st.camera ? "Camera server started. On your phone, open " + st.phone_url + " (or scan the QR code on the Home page) and tap Start camera." : "");
  $("abScan").onclick = () => act($("abScan"), "/api/scanner", { action: st && st.scanner ? "stop" : "start" },
    st && !st.scanner ? "Scanner starting. It waits for the phone — open " + st.phone_url + " on the phone and tap Start camera." : "");
  $("abFin").onclick = async () => {
    const b = $("abFin");
    if (!armed) {
      armed = true; b.classList.add("armed");
      b.textContent = "Click again: merge " + ((st && st.live_scans) || 0) + " scans";
      armTimer = setTimeout(() => { armed = false; b.classList.remove("armed"); paint(); }, 4000);
      return;
    }
    clearTimeout(armTimer); armed = false; b.classList.remove("armed");
    b.disabled = true; b.textContent = "Finishing…";
    try {
      const r = await api("/api/finish-batch", {});
      say((r.ok ? "Batch saved to your collection.\n" : "Finish batch failed:\n") + r.message, 9000);
    } catch (e) { say(String(e.message || e)); }
    b.disabled = false; refresh();
  };

  refresh();
  setInterval(refresh, 2000);
})();
