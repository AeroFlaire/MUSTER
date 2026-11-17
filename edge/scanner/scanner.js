/*
 * MUSTER phone scanner (PBI-12). Reads the CAC front (PDF417) or back (Code 39) barcode
 * from the phone camera, entirely on the phone.
 *
 * Privacy rules this file follows:
 *   - The raw barcode string lives only inside onDecoded() and is dropped right after
 *     parsing. It is never shown, logged, stored, or sent anywhere.
 *   - The screen shows the name and only the last 4 digits of the DoD ID, and clears
 *     itself after 60 seconds.
 *   - No camera frame is saved. The camera stops when the page is hidden.
 * Hooking this to the Gatekeeper API (over HTTPS) is the next step, not part of this page.
 */
(function () {
  "use strict";

  const WASM_DIR = "vendor/zxing-wasm-3.1.5/";
  const MAX_DECODE_WIDTH = 1600;                         // keeps each attempt fast on phones
  const INTERVAL_MS = 120;
  const SCHEDULE = [
    { full: false, deg: 0 }, { full: false, deg: 0 }, { full: true, deg: 0 },
    { full: false, deg: 7 }, { full: false, deg: -7 },
    { full: false, deg: 14 }, { full: false, deg: -14 },
  ];
  const CLEAR_AFTER_MS = 60000;
  const READER_OPTIONS = {
    formats: ["PDF417", "Code39"],
    tryHarder: true,
    tryRotate: true,      // card held sideways still reads
    tryDownscale: true,
    tryInvert: false,
    maxNumberOfSymbols: 1,
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    video: $("video"), viewfinder: $("viewfinder"), start: $("start"), stop: $("stop"),
    torch: $("torch"), zoom: $("zoom"), status: $("status"), startPanel: $("start-panel"), result: $("result"),
    again: $("again"),
  };
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  let stream = null, timer = null, busy = false, attempts = 0, startedAt = 0;
  let clearTimer = null, torchOn = false, zoomed = false, zoomCap = null;

  function setStatus(text, bad) {
    el.status.textContent = text;
    el.status.classList.toggle("bad", !!bad);
  }

  // ---------- 1. Load the barcode reader (bundled in the repo, no outside site) ----------
  async function loadReader() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      el.start.textContent = "Camera not available";
      setStatus("The camera only works on an https:// page (or localhost). Open this page over HTTPS.", true);
      return;
    }
    try {
      await ZXingWASM.prepareZXingModule({
        overrides: {
          locateFile: (file, prefix) => (file.endsWith(".wasm") ? WASM_DIR + file : prefix + file),
        },
        fireImmediately: true,
      });
      el.start.disabled = false;
      el.start.textContent = "Start camera";
    } catch (e) {
      el.start.textContent = "Scanner failed to load";
      setStatus("Could not load the barcode reader. Reload the page.", true);
    }
  }

  // ---------- 2. Camera ----------
  async function startCamera() {
    clearResult();
    setStatus("Starting camera…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
    } catch (e) {
      setStatus(e && e.name === "NotAllowedError"
        ? "Camera permission was denied. Allow the camera for this site, then try again."
        : "Could not open the camera.", true);
      return;
    }
    const track = stream.getVideoTracks()[0];
    try { await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (_) {}
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    el.torch.hidden = !caps.torch;
    // Optical/digital zoom, where the browser allows it (Android Chrome; not iPhone Safari).
    zoomCap = caps.zoom && caps.zoom.max >= 1.5 ? caps.zoom : null;
    el.zoom.hidden = !zoomCap;
    zoomed = false;
    el.zoom.textContent = "Zoom 2x";

    el.video.srcObject = stream;
    await el.video.play().catch(() => {});
    el.viewfinder.hidden = false;
    el.startPanel.hidden = true;
    el.viewfinder.classList.remove("hit");
    attempts = 0;
    startedAt = performance.now();
    setStatus("Looking for a barcode…");
    timer = setInterval(tick, INTERVAL_MS);
  }

  function stopCamera() {
    clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    torchOn = false;
    el.video.srcObject = null;
    el.viewfinder.hidden = true;
    el.startPanel.hidden = false;
  }

  async function toggleZoom() {
    if (!stream || !zoomCap) return;
    zoomed = !zoomed;
    const level = zoomed ? Math.min(2, zoomCap.max) : Math.max(1, zoomCap.min || 1);
    try {
      await stream.getVideoTracks()[0].applyConstraints({ advanced: [{ zoom: level }] });
      el.zoom.textContent = zoomed ? "Zoom 1x" : "Zoom 2x";
    } catch (_) { zoomed = false; }
  }

  async function toggleTorch() {
    if (!stream) return;
    torchOn = !torchOn;
    try { await stream.getVideoTracks()[0].applyConstraints({ advanced: [{ torch: torchOn }] }); }
    catch (_) { torchOn = false; }
  }

  // ---------- 3. Read frames ----------
  // The video is shown with object-fit: cover (scaled up and trimmed to fill the box), so
  // the on-screen guide must be mapped back into the camera's own pixels.
  function guideInVideo(vw, vh) {
    const box = el.viewfinder.getBoundingClientRect();
    const g = $("guide").getBoundingClientRect();
    const scale = Math.max(box.width / vw, box.height / vh);
    const offX = (box.width - vw * scale) / 2, offY = (box.height - vh * scale) / 2;
    let sx = (g.left - box.left - offX) / scale, sy = (g.top - box.top - offY) / scale;
    let sw = g.width / scale, sh = g.height / scale;
    // Read a margin beyond the drawn box: a card a little off-center still has the ends
    // of its barcode (which the reader needs) inside the area we decode.
    const padX = sw * 0.15, padY = sh * 0.30;
    sx -= padX; sy -= padY; sw += 2 * padX; sh += 2 * padY;
    sx = Math.max(0, sx); sy = Math.max(0, sy);
    sw = Math.min(vw - sx, sw); sh = Math.min(vh - sy, sh);
    if (!(sw > 32 && sh > 32)) return { sx: 0, sy: 0, sw: vw, sh: vh };
    return { sx: Math.round(sx), sy: Math.round(sy), sw: Math.round(sw), sh: Math.round(sh) };
  }

  async function tick() {
    if (busy || !stream || el.video.readyState < 2) return;
    busy = true;
    try {
      const vw = el.video.videoWidth, vh = el.video.videoHeight;
      // The PDF417 reader copes with only ~5 degrees of tilt, and people rarely hold a card
      // level. So the tries rotate through: guide box straight, whole frame straight, then
      // the guide box turned by +/-7 and +/-14 degrees.
      const plan = SCHEDULE[attempts % SCHEDULE.length];
      const { sx, sy, sw, sh } = plan.full ? { sx: 0, sy: 0, sw: vw, sh: vh } : guideInVideo(vw, vh);
      const scale = Math.min(1, MAX_DECODE_WIDTH / sw);
      const w = Math.round(sw * scale), h = Math.round(sh * scale);
      const rad = (plan.deg * Math.PI) / 180, c = Math.abs(Math.cos(rad)), s = Math.abs(Math.sin(rad));
      canvas.width = Math.round(w * c + h * s);
      canvas.height = Math.round(w * s + h * c);
      ctx.save();
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.rotate(rad);
      ctx.drawImage(el.video, sx, sy, sw, sh, -w / 2, -h / 2, w, h);
      ctx.restore();
      const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
      attempts += 1;
      if (attempts % 8 === 0 && !el.status.classList.contains("bad"))
        setStatus(`Looking for a barcode… (camera ${vw}×${vh}, ${attempts} tries)`);
      const results = await ZXingWASM.readBarcodes(image, READER_OPTIONS);
      const hit = results.find((r) => r.isValid);
      if (hit && timer) onDecoded(hit.text, hit.format);
    } catch (_) {
      // A frame that fails to decode is normal; keep going.
    } finally {
      busy = false;
    }
  }

  // ---------- 4. Parse, show the minimum, drop the raw string ----------
  function onDecoded(rawText, format) {
    let parsed = null, reason = null;
    try { parsed = CacParse.parseCac(rawText); }
    catch (e) { reason = e instanceof CacParse.CacParseError ? e.message : "unreadable"; }
    rawText = null;   // the raw barcode is not kept past this point

    if (!parsed) {
      const why = reason === "not a CAC barcode" ? "" : ` (${reason})`;
      setStatus(`That ${format} barcode is not from a CAC${why}. Try the barcode on the front of the card.`, true);
      return;   // keep scanning
    }
    const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
    el.viewfinder.classList.add("hit");
    if (navigator.vibrate) navigator.vibrate(80);
    stopCamera();
    showResult(parsed, format, `${seconds} s (${attempts} tries)`);
  }

  function showResult(p, format, timing) {
    const name = p.lastName
      ? `${p.lastName}, ${p.firstName}${p.middleInitial ? " " + p.middleInitial + "." : ""}`
      : "(not on the back barcode)";
    $("r-name").textContent = name;
    $("r-id").textContent = CacParse.maskEdipi(p.edipi);
    $("r-cat").textContent = `${CacParse.CATEGORY[p.category] || "Unknown"} (${p.category})`;
    $("r-branch").textContent = `${CacParse.BRANCH[p.branch] || "Unknown"} (${p.branch})`;
    $("r-side").textContent = p.side === "front" ? "Front of card (PDF417)" : "Back of card (Code 39)";
    $("r-time").textContent = timing;
    p = null;
    el.startPanel.hidden = true;
    el.result.hidden = false;
    setStatus("");
    clearTimer = setTimeout(clearResult, CLEAR_AFTER_MS);
  }

  function clearResult() {
    clearTimeout(clearTimer);
    for (const id of ["r-name", "r-id", "r-cat", "r-branch", "r-side", "r-time"]) $(id).textContent = "";
    el.result.hidden = true;
    if (!stream) el.startPanel.hidden = false;
  }

  // ---------- wiring ----------
  el.start.addEventListener("click", startCamera);
  el.again.addEventListener("click", startCamera);
  el.stop.addEventListener("click", () => { stopCamera(); setStatus(""); });
  el.torch.addEventListener("click", toggleTorch);
  el.zoom.addEventListener("click", toggleZoom);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopCamera(); clearResult(); }
  });
  loadReader();
})();
