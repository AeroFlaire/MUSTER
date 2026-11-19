/*
 * MUSTER app (Edge). Runs in the browser and talks only to the Gatekeeper API on the same origin.
 * Privacy rules this file follows:
 *   - A scanned card is parsed on the device. Only the 10-digit ID is sent, over the request,
 *     to the Gatekeeper (which hashes it with a key this page never holds). The raw barcode
 *     string is dropped right after parsing and never shown, logged or stored.
 *   - The page shows the name and the last 4 digits only.
 *   - Tokens stay in memory. Nothing goes in localStorage, cookies or the URL.
 *   - The page shows what the API returns. It does not work out points, totals or grades.
 */
(function () {
  "use strict";
  const WASM_DIR = "/scanner/vendor/zxing-wasm-3.1.5/";
  const AFT_SESSION = 1, IOCT_SESSION = 2;                   // created by seed_app()
  const $ = (id) => document.getElementById(id);
  const DEMO = window.DEMO_TOKENS || {};

  const S = { test: null, position: null, token: null, name: "", role: "", sex: null, age: null,
              standards: null, cadet: null, eventId: null, ref: null };

  // ---------------- small helpers ----------------
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
    }
    for (const k of kids) if (k != null) e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  }
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  function banner(id, kind, title, lines) {
    const b = $(id); b.className = "banner " + (kind || ""); b.textContent = "";
    if (!kind) return;
    b.append(h("h3", { text: title }));
    for (const l of lines || []) b.append(h("p", { text: l }));
  }
  const norm = (s) => String(s || "").toUpperCase().replace(/[^A-Z]/g, "");
  const roleLabel = (r) => ({ oic: "OIC", grader: "Grader", cadet: "Cadet", admin: "Admin", instructor: "Instructor" }[r] || r);

  const logLines = [];
  function logReq(method, path, status) {
    logLines.push(`${new Date().toLocaleTimeString()}  ${method} ${path.split("?")[0]}  ->  ${status}`);
    $("log").textContent = logLines.slice(-40).join("\n");
  }

  // ---------------- API ----------------
  async function api(method, path, body, token) {
    const headers = { "X-Contract-Version": "v1" };
    const tok = token === undefined ? S.token : token;
    if (tok) headers.Authorization = "Bearer " + tok;
    let init = { method, headers };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let res, data = {};
    try { res = await fetch(path, init); }
    catch (_) { logReq(method, path, "no connection"); throw { code: "network", message: "Cannot reach the server. Is it running?" }; }
    try { data = await res.json(); } catch (_) { /* empty body */ }
    logReq(method, path, res.status);
    if (!res.ok) {
      const e = (data && data.error) || {};
      const err = { status: res.status, code: e.code || "error", message: e.message || "Request failed (" + res.status + ")" };
      if (res.status === 401 && S.token && token === undefined) { err.message = "Your session ended. Sign in again."; signOut(true); }
      throw err;
    }
    return data;
  }

  // ---------------- views ----------------
  const scanners = [];
  function show(name) {
    scanners.forEach((s) => s.stop());
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "v-" + name));
    window.scrollTo(0, 0);
  }
  function setWho() {
    $("whoami").hidden = !S.token;
    $("whoText").textContent = S.token ? `${S.name} · ${roleLabel(S.role)}` : "";
  }
  function signOut(silent) {
    S.token = null; S.name = ""; S.role = ""; S.cadet = null; S.eventId = null; S.ref = null;
    setWho(); resetRecord(); D.cadets = []; $("dirTable").textContent = ""; $("profBody").textContent = ""; banner("signinOut"); banner("ownOut"); $("ownTable").textContent = ""; $("resTable").textContent = "";
    if (!silent) show("home");
  }

  // ---------------- scanner component ----------------
  let readerReady = null;
  function loadReader() {
    if (!readerReady) {
      readerReady = ZXingWASM.prepareZXingModule({
        overrides: { locateFile: (f, p) => (f.endsWith(".wasm") ? WASM_DIR + f : p + f) }, fireImmediately: true,
      });
    }
    return readerReady;
  }
  const READER_OPTIONS = { formats: ["PDF417", "Code39"], tryHarder: true, tryRotate: true, tryDownscale: true, tryInvert: false, maxNumberOfSymbols: 1 };
  const SCHEDULE = [{ full: false, deg: 0 }, { full: false, deg: 0 }, { full: true, deg: 0 }, { full: false, deg: 7 }, { full: false, deg: -7 }, { full: false, deg: 14 }, { full: false, deg: -14 }];

  function makeScanner(host, label, onScan) {
    const video = h("video", { playsinline: "", muted: "", autoplay: "" });
    const guide = h("div", { class: "guide" });
    const vf = h("div", { class: "viewfinder", hidden: "" }, video, guide);
    const status = h("div", { class: "status", role: "status" });
    const startBtn = h("button", { class: "btn", type: "button", text: label || "Start camera" });
    const stopBtn = h("button", { class: "btn ghost", type: "button", text: "Stop camera", hidden: "" });
    const paste = h("input", { type: "password", autocomplete: "off", placeholder: "Paste a barcode string (test cards only)" });
    const pasteBtn = h("button", { class: "btn ghost", type: "button", text: "Use pasted barcode" });
    const det = h("details", {}, h("summary", { text: "No camera? Paste a test barcode" }), h("div", { class: "paste" }, paste, pasteBtn));
    host.textContent = "";
    host.classList.add("scanner");
    host.append(h("div", { class: "row" }, startBtn, stopBtn), vf, status, det);

    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let stream = null, timer = null, busy = false, attempts = 0;

    function say(t, bad) { status.textContent = t; status.classList.toggle("bad", !!bad); }
    function stop() {
      clearInterval(timer); timer = null;
      if (stream) stream.getTracks().forEach((t) => t.stop());
      stream = null; video.srcObject = null; vf.hidden = true; stopBtn.hidden = true; startBtn.hidden = false;
    }
    function decoded(raw) {
      let parsed = null, why = "";
      try { parsed = CacParse.parseCac(raw); }
      catch (e) { why = e instanceof CacParse.CacParseError ? e.message : "unreadable"; }
      raw = null;                                    // the raw string is not kept
      if (!parsed) { say("That is not a readable CAC barcode (" + why + "). Try the front of the card.", true); return false; }
      vf.classList.add("hit"); if (navigator.vibrate) navigator.vibrate(80);
      stop(); say("");
      onScan(parsed);
      parsed = null;
      return true;
    }
    function guideBox(vw, vh) {
      const box = vf.getBoundingClientRect(), g = guide.getBoundingClientRect();
      const sc = Math.max(box.width / vw, box.height / vh);
      const ox = (box.width - vw * sc) / 2, oy = (box.height - vh * sc) / 2;
      let sx = (g.left - box.left - ox) / sc, sy = (g.top - box.top - oy) / sc, sw = g.width / sc, sh = g.height / sc;
      const px = sw * 0.15, py = sh * 0.30;
      sx -= px; sy -= py; sw += 2 * px; sh += 2 * py;
      sx = Math.max(0, sx); sy = Math.max(0, sy); sw = Math.min(vw - sx, sw); sh = Math.min(vh - sy, sh);
      return sw > 32 && sh > 32 ? { sx: Math.round(sx), sy: Math.round(sy), sw: Math.round(sw), sh: Math.round(sh) } : { sx: 0, sy: 0, sw: vw, sh: vh };
    }
    async function tick() {
      if (busy || !stream || video.readyState < 2) return;
      busy = true;
      try {
        const vw = video.videoWidth, vh = video.videoHeight;
        const plan = SCHEDULE[attempts % SCHEDULE.length];
        const { sx, sy, sw, sh } = plan.full ? { sx: 0, sy: 0, sw: vw, sh: vh } : guideBox(vw, vh);
        const scale = Math.min(1, 1600 / sw), w = Math.round(sw * scale), hh = Math.round(sh * scale);
        const rad = (plan.deg * Math.PI) / 180, c = Math.abs(Math.cos(rad)), s = Math.abs(Math.sin(rad));
        canvas.width = Math.round(w * c + hh * s); canvas.height = Math.round(w * s + hh * c);
        ctx.save(); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.translate(canvas.width / 2, canvas.height / 2); ctx.rotate(rad);
        ctx.drawImage(video, sx, sy, sw, sh, -w / 2, -hh / 2, w, hh); ctx.restore();
        const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        attempts += 1;
        const res = await ZXingWASM.readBarcodes(img, READER_OPTIONS);
        const hit = res.find((r) => r.isValid);
        if (hit && timer) decoded(hit.text);
      } catch (_) { /* a frame that fails to decode is normal */ } finally { busy = false; }
    }
    async function start() {
      if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        say("The camera needs https:// or localhost. Use the paste box below, or start the server with --https.", true); return;
      }
      say("Starting camera…");
      try { await loadReader(); } catch (_) { say("Could not load the barcode reader.", true); return; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
      } catch (e) { say(e && e.name === "NotAllowedError" ? "Camera permission was denied. Allow it for this site and try again." : "Could not open the camera.", true); return; }
      const track = stream.getVideoTracks()[0];
      try { await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (_) {}
      video.srcObject = stream; await video.play().catch(() => {});
      vf.hidden = false; vf.classList.remove("hit"); stopBtn.hidden = false; startBtn.hidden = true; attempts = 0;
      say("Hold the front of the card in the box…");
      timer = setInterval(tick, 120);
    }
    startBtn.addEventListener("click", start);
    stopBtn.addEventListener("click", () => { stop(); say(""); });
    pasteBtn.addEventListener("click", () => { const v = paste.value; paste.value = ""; if (v.trim()) decoded(v.trim()); });
    const api_ = { stop, say };
    scanners.push(api_);
    return api_;
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) scanners.forEach((s) => s.stop()); });

  // ---------------- home, position, sign-in ----------------
  document.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", () => {
    S.test = b.dataset.test;
    $("posTitle").textContent = { AFT: "Army Fitness Test", IOCT: "Interior Obstacle Course Test (IOCT)", RECORDS: "Cadet records" }[S.test];
    const rec = S.test === "RECORDS";
    $("pg-t").textContent = rec ? "OIC" : "Grader or OIC"; $("pg-s").textContent = rec ? "I want to look up cadets" : "I am recording scores";
    $("pc-t").textContent = "Cadet"; $("pc-s").textContent = rec ? "I want to see my own record" : "I want to see my scores";
    show("position");
  }));
  $("tileAtt").addEventListener("click", () => show("attendance"));
  $("homeBtn").addEventListener("click", () => show("home"));
  $("signout").addEventListener("click", () => signOut(false));
  document.querySelectorAll("[data-back]").forEach((b) => b.addEventListener("click", () => show(b.dataset.back)));
  document.querySelectorAll("[data-position]").forEach((b) => b.addEventListener("click", () => {
    S.position = b.dataset.position;
    if (S.token && S.position === "grader" && (S.role === "grader" || S.role === "oic")) return enter();
    if (S.token && S.position === "cadet" && S.role === "cadet") return enter();
    $("signinTitle").textContent = S.position === "grader" ? "Grader or OIC: scan your CAC" : "Cadet: scan your CAC";
    banner("signinOut"); show("signin");
  }));

  makeScanner($("scan-signin"), "Scan my CAC", async (p) => {
    const edipi = p.edipi, cardName = p.lastName ? `${p.lastName}, ${p.firstName}` : "";
    banner("signinOut", "info", "Checking…", [`Card ending ${CacParse.maskEdipi(edipi).slice(-4)}`]);
    try {
      const r = await api("POST", "/v0/auth/cac-signin", { edipi, position: S.position }, null);
      S.token = r.token; S.name = r.name; S.role = r.role; setWho(); enter();
    } catch (e) {
      banner("signinOut", "bad", e.status === 403 ? "That card is not registered for this position" : "Sign-in failed",
        [e.message, cardName ? "Card read as " + cardName : ""].filter(Boolean));
    }
  });

  function enter() {
    if (S.test === "RECORDS") { return S.position === "cadet" ? openProfile(null) : openDirectory(); }
    if (S.position === "cadet") { $("cadetTitle").textContent = `Your ${S.test} scores`; show("cadet"); loadOwn(); return; }
    resetRecord(); resetBatch(); gTab("record"); $("recTitle").textContent = S.test === "AFT" ? "Record AFT (one or many cadets)" : "Record IOCT";
    $("rec-batch").hidden = S.test !== "AFT"; $("rec-single").hidden = S.test !== "IOCT";
    $("aftBox").hidden = true; $("ioctBox").hidden = S.test !== "IOCT";
    show("grader"); loadStandards();
  }

  // ---------------- grader: record ----------------
  async function loadStandards() {
    if (S.standards) return;
    try { S.standards = await api("GET", "/v0/scoring/standards"); } catch (_) { S.standards = { age_groups: [], column_for_sex: { male: "M|C", female: "F" } }; }
    const sel = $("age"); sel.textContent = ""; sel.append(h("option", { value: "", text: "Choose…" }));
    for (const a of S.standards.age_groups) sel.append(h("option", { value: a, text: a }));
    const bs = $("b-age"); bs.textContent = ""; bs.append(h("option", { value: "", text: "Choose…" }));
    for (const a of S.standards.age_groups) bs.append(h("option", { value: a, text: a }));
  }
  function gTab(t) {
    document.querySelectorAll("[data-gtab]").forEach((b) => b.classList.toggle("on", b.dataset.gtab === t));
    $("g-record").hidden = t !== "record"; $("g-results").hidden = t !== "results";
  }
  document.querySelectorAll("[data-gtab]").forEach((b) => b.addEventListener("click", () => gTab(b.dataset.gtab)));

  let cadetScanner = null;
  function resetRecord() {
    S.sex = null; S.age = null; S.cadet = null; S.eventId = null; S.ref = null;
    document.querySelectorAll("input[name=sex]").forEach((r) => (r.checked = false));
    $("st1").disabled = false; $("st3").disabled = true; $("st4").disabled = true; $("verify").hidden = true; $("verify").textContent = "";
    for (const id of ["MDL", "HRP", "SDC", "PLK", "2MR", "runtime"]) $(id).value = "";
    banner("gradeOut"); $("gradeTable").textContent = ""; $("nextCadet").hidden = true; $("score").disabled = false;
    if (cadetScanner) cadetScanner.stop();
  }
  document.querySelectorAll("input[name=sex]").forEach((r) => r.addEventListener("change", () => {
    S.sex = r.value; $("st3").disabled = false;         // IOCT is graded on sex only, no age group
    if (!cadetScanner) cadetScanner = makeScanner($("scan-cadet"), "Scan the cadet's CAC", onCadetScan);
  }));

  async function onCadetScan(p) {
    const cardName = p.lastName ? `${p.lastName}, ${p.firstName}${p.middleInitial ? " " + p.middleInitial + "." : ""}` : "";
    const last4 = CacParse.maskEdipi(p.edipi).slice(-4), edipi = p.edipi;
    const v = $("verify"); v.hidden = false; v.className = "verify"; v.textContent = "Checking the database…";
    try {
      const r = await api("POST", "/v0/identity/lookup", { edipi });
      const dl = h("dl", {}, h("dt", { text: "Name on the card" }), h("dd", { text: cardName || "(back of card has no name)" }),
        h("dt", { text: "Name in database" }), h("dd", { text: r.found ? r.name : "not found" }),
        h("dt", { text: "Card ID" }), h("dd", { text: "ending " + last4 }),
        h("dt", { text: "Registered as" }), h("dd", { text: r.found ? roleLabel(r.role) : "nobody" }));
      v.textContent = ""; v.append(dl);
      let verdict, good = false;
      if (!r.found) verdict = "This card is not registered. The cadet must be added first (Admin: register people).";
      else if (r.kind !== "cadet") verdict = `This card belongs to a ${roleLabel(r.role)}, not a cadet. Scan the cadet's card.`;
      else if (cardName && norm(cardName.replace(/ \w\.$/, "")) !== norm(r.name)) verdict = "The card name and the database name do not match. Do not record. Check the cadet's ID.";
      else { good = true; verdict = cardName ? "Verified: card and database agree." : "Registered cadet. The back of the card has no name, so only the database name is shown."; }
      v.classList.add(good ? "good" : "badv"); v.append(h("div", { class: "verdict", text: verdict }));
      if (good) { S.cadet = { edipi, name: r.name }; $("st1").disabled = true; $("st4").disabled = false; }
      else { S.cadet = null; $("st4").disabled = true; }
    } catch (e) { v.className = "verify badv"; v.textContent = e.message; }
  }

  $("score").addEventListener("click", async () => {
    if (!S.cadet) return;
    const btn = $("score"); btn.disabled = true; banner("gradeOut", "info", "Working…", []);
    try { await scoreIoct(); }
    catch (e) { banner("gradeOut", "bad", "Not saved", [e.message]); btn.disabled = false; }
  });

  async function scoreIoct() {
    const tag = $("tag").value, rt = $("runtime").value.trim().match(/^(\d{1,2}):([0-5]\d)$/);
    if (!rt) throw { message: "Enter the run time as m:ss, for example 2:35." };
    if (!DEMO.gate_station) throw { message: "The demo gate only works on the laptop running the server. The real gate hardware posts these reads itself." };
    const ms = (parseInt(rt[1], 10) * 60 + parseInt(rt[2], 10)) * 1000;
    if (!S.eventId) { S.ref = S.ref || uuid(); const ev = await api("POST", `/v0/sessions/${IOCT_SESSION}/ioct/pair`, { edipi: S.cadet.edipi, tag_uid: tag, client_ref: S.ref }); S.eventId = ev.event_id; }
    const base = Date.now() + 1000;           // the Vault only counts reads made after the pairing
    await api("POST", `/v0/sessions/${IOCT_SESSION}/gate-reads`, { tag_uid: tag, gate: "START", read_at_ms: base, gate_device: "START", client_ref: uuid() }, DEMO.gate_station);
    await api("POST", `/v0/sessions/${IOCT_SESSION}/gate-reads`, { tag_uid: tag, gate: "END", read_at_ms: base + ms, gate_device: "END", client_ref: uuid() }, DEMO.gate_station);
    const run = await api("POST", `/v0/events/${S.eventId}/ioct/compute`);
    const r = await api("POST", `/v0/events/${S.eventId}/submit`);
    const col = S.standards.column_for_sex[S.sex], g = r.grading.by_column[col];
    banner("gradeOut", g.result === "PASS" ? "ok" : "bad", `${S.cadet.name}: ${g.result}, grade ${g.grade}`,
      [`Time ${run.elapsed}`, `${g.points} points (${col} column)`, g.tab ? "TAB: A- or better, never has to be retaken." : "Not at the TAB level."]);
    $("gradeTable").textContent = "";
    finishCadet();
    const nxt = $("tag"); if (nxt.selectedIndex < nxt.options.length - 1) nxt.selectedIndex += 1;
  }
  function finishCadet() { $("st4").disabled = true; $("nextCadet").hidden = false; S.cadet = null; }
  $("nextCadet").addEventListener("click", () => { const tagIdx = $("tag").selectedIndex; resetRecord(); $("tag").selectedIndex = tagIdx; });


  // ---------------- AFT: grade many cadets at once, event by event ----------------
  const EVENTS = [["MDL", "Deadlift", "pounds"], ["HRP", "Hand-release push-ups", "reps"], ["SDC", "Sprint-drag-carry", "m:ss"], ["PLK", "Plank", "m:ss"], ["2MR", "2-mile run", "m:ss"]];
  const B = { n: 1, cadets: [], pending: null, event: "MDL", mode: "event", sel: 0, cells: {}, tabs: {} };
  let batchScanner = null;

  function resetBatch() {
    B.n = 1; B.cadets = []; B.pending = null; B.event = "MDL"; B.mode = "event"; B.sel = 0; B.cells = {}; B.tabs = {};
    $("b-setup").hidden = false; $("b-grade").hidden = true; $("b-scanzone").hidden = true; $("b-verify").hidden = true; $("b-addbox").hidden = true;
    $("b-begin").disabled = true; $("b-start").hidden = false; $("b-count").disabled = false;
    document.querySelectorAll("input[name=bmode]").forEach((r) => (r.checked = r.value === "event"));
    for (const id of ["b-setupOut", "b-out"]) banner(id);
    $("b-results").textContent = ""; $("b-table").textContent = ""; $("b-new").hidden = true; $("b-submit").hidden = false; $("b-submit").disabled = false;
    if (batchScanner) batchScanner.stop();
    drawSlots();
  }
  function drawSlots() {
    const host = $("b-slots"); host.textContent = "";
    for (let i = 0; i < B.n; i++) {
      const c = B.cadets[i];
      host.append(h("span", { class: "slot" + (c ? " on" : ""), text: c ? `${i + 1}. ${c.name}` : `${i + 1}. waiting` }));
    }
    const full = B.cadets.length >= B.n;
    $("b-prompt").textContent = full ? `All ${B.n} cadets are scanned.` : `Scan cadet ${B.cadets.length + 1} of ${B.n}.`;
    $("scan-batch").hidden = full || !!B.pending;
    $("b-begin").disabled = B.cadets.length === 0;
  }
  $("b-start").addEventListener("click", () => {
    const n = parseInt($("b-count").value, 10);
    if (!(n >= 1 && n <= 40)) { banner("b-setupOut", "bad", "Enter a number from 1 to 40", []); return; }
    B.n = n; $("b-count").disabled = true; $("b-start").hidden = true; $("b-scanzone").hidden = false;
    if (!batchScanner) batchScanner = makeScanner($("scan-batch"), "Scan a cadet's CAC", onBatchScan);
    drawSlots();
  });

  async function onBatchScan(p) {
    const cardName = p.lastName ? `${p.lastName}, ${p.firstName}${p.middleInitial ? " " + p.middleInitial + "." : ""}` : "";
    const last4 = CacParse.maskEdipi(p.edipi).slice(-4), edipi = p.edipi;
    const v = $("b-verify"); v.hidden = false; v.className = "verify"; v.textContent = "Checking the database…"; $("b-addbox").hidden = true;
    try {
      const r = await api("POST", "/v0/identity/lookup", { edipi });
      v.textContent = "";
      v.append(h("dl", {}, h("dt", { text: "Name on the card" }), h("dd", { text: cardName || "(back of card has no name)" }),
        h("dt", { text: "Name in database" }), h("dd", { text: r.found ? r.name : "not found" }), h("dt", { text: "Card ID" }), h("dd", { text: "ending " + last4 })));
      let verdict, good = false;
      if (!r.found) verdict = "This card is not registered. Register the cadet first.";
      else if (r.kind !== "cadet") verdict = `This card belongs to a ${roleLabel(r.role)}, not a cadet.`;
      else if (cardName && norm(cardName.replace(/ \w\.$/, "")) !== norm(r.name)) verdict = "Card name and database name do not match. Do not record.";
      else { good = true; verdict = cardName ? "Verified: card and database agree." : "Registered cadet (the back of the card has no name)."; }
      v.classList.add(good ? "good" : "badv"); v.append(h("div", { class: "verdict", text: verdict }));
      if (good) {
        B.pending = { edipi, name: r.name };
        document.querySelectorAll("input[name=bsex]").forEach((x) => (x.checked = false)); $("b-age").value = "";
        $("b-addbox").hidden = false; $("scan-batch").hidden = true;
      }
    } catch (e) { v.className = "verify badv"; v.textContent = e.message; }
  }
  $("b-add").addEventListener("click", async () => {
    const sexEl = document.querySelector("input[name=bsex]:checked"), age = $("b-age").value;
    if (!B.pending) return;
    if (!sexEl || !age) { banner("b-setupOut", "bad", "Pick sex and age group first", []); return; }
    banner("b-setupOut");
    const btn = $("b-add"); btn.disabled = true;
    try {
      const ref = uuid();
      const ev = await api("POST", `/v0/sessions/${AFT_SESSION}/events`, { edipi: B.pending.edipi, client_ref: ref });
      B.cadets.push({ name: B.pending.name, eventId: ev.event_id, sex: sexEl.value, age, col: S.standards.column_for_sex[sexEl.value], values: {}, points: {}, submitted: false, result: null });
      B.pending = null;                                        // the ID is not kept once the scorecard is open
      $("b-addbox").hidden = true; $("b-verify").hidden = true;
    } catch (e) {
      if (/already has a live score/.test(e.message)) offerEdit(sexEl.value, age);
      else banner("b-setupOut", "bad", "Could not add this cadet", [e.message]);
    }
    btn.disabled = false; drawSlots();
  });

  // A cadet who already has a score this session (a retake, or a correction): the OIC reopens it,
  // the old values stay in the audit log, the grader changes what is wrong and submits again.
  function offerEdit(sex, age) {
    const box = $("b-setupOut"); banner("b-setupOut", "bad", "This cadet already has a score in this session",
      [S.role === "oic" ? "You can edit it: it is reopened, the old values are kept in the audit log." : "Only the OIC can edit a saved score."]);
    if (S.role !== "oic") return;
    const b = h("button", { type: "button", class: "btn", text: "Edit this score" });
    b.addEventListener("click", () => editExisting(sex, age, b));
    box.append(h("p", {}, b));
  }
  async function editExisting(sex, age, btn) {
    btn.disabled = true; const who = B.pending.name;
    try {
      const all = await api("GET", "/v0/results?test_type=AFT");
      const hit = all.results.filter((r) => r.scorecard.cadet_name === who && r.scorecard.session_id === AFT_SESSION && r.status !== "SUPERSEDED").pop();
      if (!hit) throw new Error("Could not find that score.");
      let id = hit.event_id;
      if (hit.status === "SUBMITTED" || hit.status === "DISPUTED") await api("POST", `/v0/events/${id}/reopen`, { note: "Retake or correction from the grading screen" });
      else if (hit.status === "SIGNED" || hit.status === "EXPORTED") id = (await api("POST", `/v0/events/${id}/supersede`, { reason: "Retake or correction from the grading screen" })).new_event_id;
      const c = { name: who, eventId: id, sex, age, col: S.standards.column_for_sex[sex], values: {}, points: {}, submitted: false, result: null, edited: true };
      for (const k of hit.scorecard.components) { c.values[k.component] = k.raw_value; c.points[k.component] = k.points; }
      B.cadets.push(c); B.pending = null; $("b-addbox").hidden = true; $("b-verify").hidden = true;
      banner("b-setupOut", "ok", who + " reopened", ["Their saved results are loaded. Change what is wrong, then submit."]);
    } catch (e) { banner("b-setupOut", "bad", "Could not edit this score", [e.message]); }
    drawSlots();
  }
  $("b-begin").addEventListener("click", () => {
    if (!B.cadets.length) return;
    batchScanner.stop(); $("b-setup").hidden = true; $("b-grade").hidden = false; B.event = "MDL"; B.sel = 0; renderGrade();
  });

  function doneCount(ev) { return B.cadets.filter((c) => c.points[ev] != null).length; }
  function updateTabs() {
    for (const [code, label] of EVENTS) if (B.tabs[code]) B.tabs[code].textContent = `${label} ${doneCount(code)}/${B.cadets.length}`;
    document.querySelectorAll("#b-chips button").forEach((b, i) => { const c = B.cadets[i]; b.textContent = `${c.name} ${EVENTS.filter(([k]) => c.points[k] != null).length}/5`; });
  }
  function renderGrade() {
    const tabs = $("b-evtabs"); tabs.textContent = ""; tabs.className = "tabs evt"; B.tabs = {};
    for (const [code, label] of EVENTS) {
      const b = h("button", { type: "button", class: B.event === code && B.mode === "event" ? "on" : "" });
      b.addEventListener("click", () => { B.event = code; B.mode = "event"; document.querySelector("input[name=bmode][value=event]").checked = true; renderGrade(); });
      B.tabs[code] = b; tabs.append(b);
    }
    tabs.hidden = B.mode !== "event";
    const chips = $("b-chips"); chips.textContent = ""; chips.hidden = B.mode !== "cadet";
    B.cadets.forEach((c, i) => { const b = h("button", { type: "button", class: i === B.sel ? "on" : "" }); b.addEventListener("click", () => { B.sel = i; renderGrade(); }); chips.append(b); });
    B.cells = {};
    const rows = B.mode === "event" ? B.cadets.map((c) => [c, B.event]) : EVENTS.map(([code]) => [B.cadets[B.sel], code]);
    const unit = (code) => EVENTS.find((e) => e[0] === code)[2];
    const t = h("table", { class: "gt" }, h("thead", {}, h("tr", {}, h("th", { text: B.mode === "event" ? "Cadet" : "Event" }), h("th", { text: "Result" }), h("th", { text: "Points" }))));
    const body = h("tbody");
    const inputs = [];
    for (const [c, code] of rows) {
      const input = h("input", { autocomplete: "off", inputmode: code === "MDL" || code === "HRP" ? "numeric" : "text", placeholder: unit(code) });
      input.value = c.values[code] || ""; input.disabled = c.submitted;
      const pts = h("td", { class: "pts", text: c.points[code] != null ? c.points[code] : "" });
      B.cells[B.cadets.indexOf(c) + ":" + code] = pts;
      input.addEventListener("change", () => saveComp(c, code, input, pts));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); const k = inputs.indexOf(input); if (inputs[k + 1]) inputs[k + 1].focus(); else input.blur(); } });
      inputs.push(input);
      const label = B.mode === "event" ? c.name : EVENTS.find((e) => e[0] === code)[1];
      body.append(h("tr", {}, h("td", { class: "nm", text: label }), h("td", {}, input), pts));
    }
    t.append(body); $("b-table").textContent = ""; $("b-table").append(t); updateTabs();
    if (inputs[0] && !inputs[0].disabled) inputs[0].focus();
  }
  document.querySelectorAll("input[name=bmode]").forEach((r) => r.addEventListener("change", () => { B.mode = r.value; renderGrade(); }));

  async function saveComp(c, code, input, ptsCell) {
    const v = input.value.trim();
    if (!v || (v === c.values[code] && c.points[code] != null)) return;
    ptsCell.textContent = "…";
    try {
      const r = await api("POST", `/v0/events/${c.eventId}/components`, { component: code, raw_value: v, age_group: c.age, scoring_column: c.col });
      c.values[code] = v; c.points[code] = r.points; ptsCell.textContent = r.points; input.classList.remove("err"); banner("b-out");
    } catch (e) { ptsCell.textContent = ""; input.classList.add("err"); banner("b-out", "bad", "Not saved for " + c.name, [e.message]); }
    updateTabs();
  }

  $("b-submit").addEventListener("click", async () => {
    const btn = $("b-submit"); btn.disabled = true; banner("b-out", "info", "Submitting…", []);
    const rows = [];
    for (const c of B.cadets) {
      if (c.submitted) continue;
      const missing = EVENTS.filter(([k]) => c.points[k] == null).map(([, l]) => l);
      if (missing.length) { rows.push([c.name, "", "NOT SUBMITTED", "Missing: " + missing.join(", ")]); continue; }
      try {
        const r = await api("POST", `/v0/events/${c.eventId}/submit`);
        c.submitted = true; c.result = r.grading;
      } catch (e) { rows.push([c.name, "", "ERROR", e.message]); }
    }
    const t = h("table", {}, h("thead", {}, h("tr", {}, ...["Cadet", "Total", "Result", "Notes"].map((x) => h("th", { text: x })))));
    const body = h("tbody");
    for (const c of B.cadets) if (c.submitted) body.append(h("tr", {}, h("td", { text: c.name }), h("td", { text: c.result.total_points }), h("td", { class: c.result.result === "PASS" ? "pass" : "fail", text: c.result.result }), h("td", { text: c.result.failed_events.length ? "Below 60 on: " + c.result.failed_events.join(", ") : "" })));
    for (const r of rows) body.append(h("tr", {}, ...r.map((x, i) => h("td", { class: i === 2 ? "fail" : "", text: x }))));
    t.append(body); $("b-results").textContent = ""; $("b-results").append(t);
    const sub = B.cadets.filter((c) => c.submitted).length;
    banner("b-out", sub === B.cadets.length ? "ok" : "info", `${sub} of ${B.cadets.length} submitted`, sub === B.cadets.length ? ["Scores are locked and saved."] : ["Finish the missing events and submit again."]);
    renderGrade();
    btn.disabled = false;
    if (sub === B.cadets.length) { btn.hidden = true; $("b-new").hidden = false; }
  });
  $("b-new").addEventListener("click", () => { resetBatch(); });

  // ---------------- results tables ----------------
  function resultsTable(rows, col, showName) {
    if (!rows.length) return h("p", { class: "sub", text: "No scores yet." });
    const head = ["Cadet", "Test", "Status", "Detail", "Result"].filter((x) => showName || x !== "Cadet");
    const t = h("table", {}, h("thead", {}, h("tr", {}, ...head.map((x) => h("th", { text: x })))));
    const body = h("tbody");
    for (const r of rows) {
      const sc = r.scorecard, g = r.grading || {};
      let detail = "", res = "", cls = "";
      if (sc.test_type === "AFT") { detail = (sc.total_points != null ? sc.total_points + " pts" : "in progress"); res = g.result || ""; }
      else if (sc.ioct) {
        const bc = g.by_column || {};
        if (col && bc[col]) { detail = `${sc.ioct.elapsed || ""}  grade ${bc[col].grade}`; res = bc[col].result; }
        else { detail = `${sc.ioct.elapsed || ""}  M|C ${bc["M|C"] ? bc["M|C"].grade : "-"} · F ${bc.F ? bc.F.grade : "-"}`; res = (bc["M|C"] && bc.F) ? (bc["M|C"].result === bc.F.result ? bc["M|C"].result : "see grade") : ""; }
      }
      cls = res === "PASS" ? "pass" : res === "FAIL" ? "fail" : "";
      const note = r.status === "ENTERED" ? " (draft)" : r.status === "SUPERSEDED" ? " (replaced)" : "";
      const cells = [];
      if (showName) cells.push(h("td", { text: sc.cadet_name || "" }));
      cells.push(h("td", { text: sc.test_type }), h("td", {}, h("span", { class: "pill", text: r.status + note })), h("td", { text: detail }), h("td", { class: cls, text: res }));
      body.append(h("tr", {}, ...cells));
    }
    t.append(body); return t;
  }
  async function loadResults(outId, tableId, col, showName, testType) {
    banner(outId, "info", "Loading…", []);
    try {
      const r = await api("GET", "/v0/results" + (testType ? "?test_type=" + testType : ""));
      banner(outId, "ok", `${r.count} score${r.count === 1 ? "" : "s"}`, [r.scope === "all" ? "You can see everyone's scores." : "You can see only your own scores."]);
      const host = $(tableId); host.textContent = ""; host.append(resultsTable(r.results, col, showName));
    } catch (e) { banner(outId, "bad", "Could not load", [e.message]); }
  }
  $("refreshAll").addEventListener("click", () => loadResults("resOut", "resTable", S.standards ? S.standards.column_for_sex[$("rsex").value] : null, true, null));
  $("refreshOwn").addEventListener("click", loadOwn);
  function loadOwn() { loadResults("ownOut", "ownTable", null, false, S.test); }


  // ---------------- cadet records: OIC look-up and a cadet's own record ----------------
  const D = { cadets: [] };
  let dirScanner = null;
  const pillClass = (r) => (r === "PASS" ? "ok" : r === "FAIL" ? "warn" : "");

  async function openDirectory() {
    show("directory"); $("dirBody").hidden = false; $("dirTable").textContent = ""; $("dirCount").textContent = "";
    banner("dirOut", "info", "Loading…", []);
    try {
      const r = await api("GET", "/v0/cadets"); D.cadets = r.cadets; banner("dirOut");
      if (!dirScanner) dirScanner = makeScanner($("scan-dir"), "Scan a cadet's CAC", onDirScan);
      drawDirectory();
    } catch (e) { $("dirBody").hidden = true; banner("dirOut", "bad", e.status === 403 ? "OIC only" : "Could not load", [e.message]); }
  }
  function drawDirectory() {
    const q = $("dirQ").value.trim().toLowerCase();
    const rows = D.cadets.filter((c) => !q || [c.name, c.company || "", String(c.class_year || "")].join(" ").toLowerCase().includes(q));
    $("dirCount").textContent = `${rows.length} of ${D.cadets.length} cadets`;
    const t = h("table", {}, h("thead", {}, h("tr", {}, ...["Cadet", "Company", "Class", "AFT", "IOCT", "Attendance"].map((x) => h("th", { text: x })))));
    const body = h("tbody");
    for (const c of rows) {
      const nameBtn = h("button", { type: "button", class: "rowbtn", text: c.name }); nameBtn.addEventListener("click", () => openProfile(c.cadet_id));
      const aft = c.aft ? h("span", { class: "pill " + pillClass(c.aft.result), text: c.aft.total_points != null ? `${c.aft.total_points} ${c.aft.result}` : "in progress" }) : "none";
      const io = c.ioct ? `${c.ioct.elapsed || "in progress"}${c.ioct.grades && c.ioct.grades["M|C"] ? "  (M|C " + c.ioct.grades["M|C"] + " / F " + c.ioct.grades.F + ")" : ""}` : "none";
      const a = c.attendance, n = a.PRESENT + a.LATE + a.ABSENT + a.EXCUSED;
      body.append(h("tr", {}, h("td", {}, nameBtn), h("td", { text: c.company || "" }), h("td", { text: c.class_year || "" }), h("td", {}, aft), h("td", { text: io }),
        h("td", { text: n ? `${a.PRESENT} present, ${a.LATE} late, ${a.ABSENT} absent` : "none" })));
    }
    t.append(body); $("dirTable").textContent = ""; $("dirTable").append(rows.length ? t : h("p", { class: "sub", text: "No cadets match." }));
  }
  $("dirQ").addEventListener("input", drawDirectory);
  async function onDirScan(p) {
    banner("dirOut", "info", "Looking up…", []);
    try {
      const r = await api("POST", "/v0/identity/lookup", { edipi: p.edipi });
      const hit = r.found && r.kind === "cadet" ? D.cadets.find((c) => c.name === r.name) : null;
      if (hit) { banner("dirOut"); openProfile(hit.cadet_id); }
      else banner("dirOut", "bad", r.found ? "That card belongs to staff, not a cadet" : "That card is not registered", []);
    } catch (e) { banner("dirOut", "bad", "Lookup failed", [e.message]); }
  }

  async function openProfile(cadetId) {
    const own = cadetId == null;
    show("profile"); $("profBody").textContent = ""; $("profBack").hidden = own;
    banner("profOut", "info", "Loading…", []);
    try {
      const r = await api("GET", own ? "/v0/me" : `/v0/cadets/${cadetId}`);
      banner("profOut");
      if (r.kind === "staff") { $("profBody").append(h("div", { class: "profhead" }, h("h2", { text: r.name }), h("span", { class: "pill big", text: roleLabel(r.role) })),
        h("p", { class: "sub", text: "Staff accounts have no cadet record. Choose Cadet records, then OIC, to look up cadets." })); return; }
      renderProfile(r, own);
    } catch (e) { banner("profOut", "bad", "Could not load", [e.message]); }
  }
  function renderProfile(r, own) {
    const c = r.cadet, host = $("profBody"); host.textContent = "";
    host.append(h("div", { class: "profhead" }, h("h2", { text: c.name }),
      h("span", { class: "pill big", text: c.company || "No company set" }), h("span", { class: "pill big", text: c.class_year ? "Class of " + c.class_year : "No class year" }),
      h("span", { class: "pill big " + (c.active ? "ok" : "warn"), text: c.active ? "Active" : "Inactive" })));
    if (own) host.append(h("p", { class: "sub", text: "This is your own record. Nobody else's is shown to you." }));
    host.append(h("h3", { text: "Fitness results" }), resultsTable(r.results, null, false));
    const a = r.attendance.summary, n = a.PRESENT + a.LATE + a.ABSENT + a.EXCUSED;
    host.append(h("h3", { text: "Attendance" }));
    if (!n) host.append(h("p", { class: "sub", text: "No attendance recorded yet." }));
    else {
      host.append(h("div", { class: "pillrow" }, ...Object.entries(a).map(([k, v]) => h("span", { class: "pill big", text: `${k[0] + k.slice(1).toLowerCase()} ${v}` }))));
      const t = h("table", {}, h("thead", {}, h("tr", {}, ...["Date", "Class", "Status"].map((x) => h("th", { text: x })))));
      const b = h("tbody");
      for (const x of r.attendance.records) b.append(h("tr", {}, h("td", { text: x.class_date }), h("td", { text: `${x.course} ${x.section}` }), h("td", { text: x.status })));
      t.append(b); host.append(t);
    }
    if (r.sections.length) host.append(h("h3", { text: "Sections" }), h("p", { class: "sub", text: r.sections.map((s) => `${s.course} ${s.section} (${s.period})`).join(", ") }));
  }
  $("profBack").addEventListener("click", () => show("directory"));
  $("profHome").addEventListener("click", () => show("home"));

  // ---------------- admin: register people ----------------
  let regScanner = null, regCard = null;
  $("toRegister").addEventListener("click", () => {
    $("adminKeyBox").hidden = !!DEMO.admin; $("regForm").hidden = true; banner("regOut");
    show("register");
    if (!regScanner) regScanner = makeScanner($("scan-register"), "Scan a person's CAC", onRegScan);
    loadPeople();
  });
  const adminTok = () => DEMO.admin || $("adminKey").value.trim();
  function onRegScan(p) {
    regCard = { edipi: p.edipi };
    $("regFirst").value = p.firstName || ""; $("regLast").value = p.lastName || "";
    document.querySelectorAll("input[name=role]").forEach((r) => (r.checked = false));
    $("regCadetBox").hidden = true; $("regCompany").value = ""; $("regYear").value = "";
    $("regForm").hidden = false;
    banner("regOut", "info", "Card read", [`ID ending ${CacParse.maskEdipi(p.edipi).slice(-4)}. Check the name, pick a role, then Register.`]);
  }
  $("regGo").addEventListener("click", async () => {
    const role = (document.querySelector("input[name=role]:checked") || {}).value;
    if (!regCard) return;
    if (!role) { banner("regOut", "bad", "Pick a role", []); return; }
    const payload = { edipi: regCard.edipi, first_name: $("regFirst").value.trim(), last_name: $("regLast").value.trim(), role };
    if (role === "cadet") { const co = $("regCompany").value.trim(), yr = $("regYear").value; if (co) payload.company = co; if (yr) payload.class_year = parseInt(yr, 10); }
    try {
      const r = await api("POST", "/v0/admin/register", payload, adminTok());
      banner("regOut", "ok", "Registered", [`${r.name} as ${roleLabel(r.role)}`]);
      regCard = null; $("regForm").hidden = true; loadPeople();
    } catch (e) { banner("regOut", "bad", "Not registered", [e.message]); }
  });
  async function loadPeople() {
    const host = $("people"); host.textContent = "";
    try {
      const r = await api("GET", "/v0/admin/people", undefined, adminTok());
      if (!r.people.length) { host.append(h("p", { class: "sub", text: "Nobody yet." })); return; }
      const t = h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Name" }), h("th", { text: "Role" }))));
      const b = h("tbody");
      for (const p of r.people) b.append(h("tr", {}, h("td", { text: p.name }), h("td", { text: roleLabel(p.role) })));
      t.append(b); host.append(t);
    } catch (e) { host.append(h("p", { class: "sub", text: e.code === "network" ? e.message : "Enter the admin token to see the list." })); }
  }
  $("adminKey").addEventListener("change", loadPeople);
  document.querySelectorAll("input[name=role]").forEach((r) => r.addEventListener("change", () => { $("regCadetBox").hidden = r.value !== "cadet"; }));

  setWho(); show("home");
})();
